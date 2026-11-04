"""Pipeline orchestration: draft, guardrails, revisions, approval, translation."""

from __future__ import annotations

import logging
import uuid

from app.config import Settings
from app.db import Database
from app.enums import BriefState, Language, Vertical
from app.errors import (
    AppError,
    GuardrailBlockedError,
    InvalidTransitionError,
    NotFoundError,
    PipelineFailure,
    ValidationError,
)
from app.facts import FactSheetRegistry
from app.guardrails.engine import GuardrailEngine
from app.integrations.base import DesignProvider, TaskTracker
from app.integrations.cms import LocalCMS
from app.llm.base import LLMClient
from app.schemas import (
    ApprovalRequest,
    BriefCreate,
    BriefOut,
    BriefSummary,
    CMSDocument,
    CriticNote,
    DesignAsset,
    EventOut,
    FactSheet,
    GuardrailReport,
    PublishRequest,
    RejectRequest,
    TaskRef,
    TranslationResult,
)
from app.state_machine import StateMachine
from app.translation import TranslationService
from app.webhooks import WebhookDispatcher

logger = logging.getLogger(__name__)


class PipelineService:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        facts: FactSheetRegistry,
        guardrails: GuardrailEngine,
        llm: LLMClient,
        translator: TranslationService,
        cms: LocalCMS,
        task_tracker: TaskTracker,
        design_provider: DesignProvider,
        webhooks: WebhookDispatcher,
        prompt_versions: dict[str, str] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.facts = facts
        self.guardrails = guardrails
        self.llm = llm
        self.translator = translator
        self.cms = cms
        self.task_tracker = task_tracker
        self.design_provider = design_provider
        self.webhooks = webhooks
        self.prompt_versions = prompt_versions or {}

    # ------------------------------------------------------------- creation
    def create_brief(self, payload: BriefCreate) -> BriefOut:
        try:
            fact_sheet = self.facts.get(payload.fact_sheet_id)
        except KeyError as exc:
            raise NotFoundError(str(exc)) from exc
        if fact_sheet.vertical != payload.vertical:
            raise ValidationError(
                f"Fact sheet '{fact_sheet.id}' is for vertical "
                f"'{fact_sheet.vertical.value}', not '{payload.vertical.value}'."
            )

        brief_id = f"brf_{uuid.uuid4().hex[:12]}"
        record = {
            "id": brief_id,
            "title": payload.title,
            "vertical": payload.vertical.value,
            "brief": payload.brief,
            "fact_sheet_id": fact_sheet.id,
            "fact_sheet": fact_sheet.model_dump(mode="json"),
            "languages": [language.value for language in payload.languages],
            "metadata": payload.metadata,
            "state": BriefState.BRIEF.value,
        }
        event = self.db.create_brief(record, actor="api")
        self._emit(event)
        self._create_task(brief_id, payload, fact_sheet)

        self.run_to_approval(brief_id, actor="system")
        return self.get_brief(brief_id)

    def _create_task(
        self, brief_id: str, payload: BriefCreate, fact_sheet: FactSheet
    ) -> None:
        try:
            ref = self.task_tracker.create_task(
                title=payload.title,
                description=(
                    f"Vertical: {payload.vertical.value}\n"
                    f"Fact sheet: {fact_sheet.id}\n\n{payload.brief}"
                ),
                brief_id=brief_id,
                metadata=payload.metadata,
            )
            self.db.update_brief_fields(brief_id, {"task_ref_json": ref.model_dump()})
        except Exception as exc:  # noqa: BLE001 - tracker outage must not kill the pipeline
            logger.warning("Task tracker create failed for %s: %s", brief_id, exc)

    # -------------------------------------------------------- draft + revise
    def run_to_approval(self, brief_id: str, actor: str = "system") -> BriefOut:
        brief = self._require_brief(brief_id)
        if brief["state"] != BriefState.BRIEF.value:
            raise InvalidTransitionError(
                f"Brief {brief_id} is in state '{brief['state']}', expected 'brief'.",
                details={"current_state": brief["state"]},
            )
        fact_sheet = FactSheet.model_validate(brief["fact_sheet"])
        pack = self.guardrails.pack_for(fact_sheet.vertical)
        disclaimers = [rule.text for rule in pack.required_disclaimers]
        max_sentence_words = pack.brand_voice.max_sentence_words

        try:
            self._transition(
                brief_id,
                BriefState.DRAFTING,
                actor=actor,
                reason="Drafting from the approved brief and fact sheet.",
                updates={"revision_count": 0},
                payload={"revision_count": 0},
            )
            draft = self.llm.draft(
                brief=brief["brief"],
                fact_sheet=fact_sheet,
                vertical=fact_sheet.vertical,
                required_disclaimers=disclaimers,
                max_sentence_words=max_sentence_words,
                metadata=brief.get("metadata", {}),
            )
            report = self.guardrails.check(
                headline=draft.headline, body=draft.body, fact_sheet=fact_sheet
            )
            self._transition(
                brief_id,
                BriefState.GUARDRAIL_CHECK,
                actor="guardrail-engine",
                reason="Initial guardrail check.",
                updates={
                    "headline": draft.headline,
                    "body": draft.body,
                    "guardrail_json": report.model_dump(mode="json"),
                    "initial_guardrail_json": report.model_dump(mode="json"),
                    "revision_count": 0,
                },
                payload={
                    "guardrails_passed": report.passed,
                    "violations": len(report.violations),
                    "revision_count": 0,
                },
            )

            loops = 0
            while not report.passed and loops < self.settings.max_revision_loops:
                loops += 1
                self._transition(
                    brief_id,
                    BriefState.REVISION,
                    actor="critic",
                    reason=f"Revising to resolve guardrail violations (loop {loops}).",
                    updates={"revision_count": loops},
                    payload={
                        "revision_count": loops,
                        "violations": len(report.violations),
                    },
                )
                revision = self.llm.revise(
                    brief=brief["brief"],
                    draft=draft,
                    report=report,
                    fact_sheet=fact_sheet,
                    vertical=fact_sheet.vertical,
                    required_disclaimers=disclaimers,
                    max_sentence_words=max_sentence_words,
                )
                draft = type(draft)(headline=revision.headline, body=revision.body)
                report = self.guardrails.check(
                    headline=draft.headline, body=draft.body, fact_sheet=fact_sheet
                )
                notes = [
                    CriticNote(
                        summary=note, addressed_rules=revision.addressed_rules
                    ).model_dump()
                    for note in (revision.notes or ["Critic revision applied."])
                ]
                self._transition(
                    brief_id,
                    BriefState.GUARDRAIL_CHECK,
                    actor="guardrail-engine",
                    reason=f"Guardrail re-check after revision {loops}.",
                    updates={
                        "headline": draft.headline,
                        "body": draft.body,
                        "guardrail_json": report.model_dump(mode="json"),
                        "critique_json": notes,
                        "revision_count": loops,
                    },
                    payload={
                        "guardrails_passed": report.passed,
                        "violations": len(report.violations),
                        "revision_count": loops,
                    },
                )

            self._transition(
                brief_id,
                BriefState.PENDING_APPROVAL,
                actor="system",
                reason=(
                    "Ready for human approval."
                    if report.passed
                    else "Revision limit reached; human override is required."
                ),
                payload={
                    "guardrails_passed": report.passed,
                    "violations": len(report.violations),
                    "revision_count": loops,
                },
            )
            return self.get_brief(brief_id)
        except Exception as exc:  # noqa: BLE001 - persist a failed state, then surface
            self._mark_failed(brief_id, exc)
            if isinstance(exc, AppError):
                raise
            raise PipelineFailure(
                f"Pipeline failed while preparing brief {brief_id}: "
                f"{type(exc).__name__}: {exc}",
                details={"brief_id": brief_id},
            ) from exc

    # -------------------------------------------------------------- approval
    def approve(self, brief_id: str, request: ApprovalRequest) -> BriefOut:
        brief = self._require_brief(brief_id)
        self._expect_state(brief, BriefState.PENDING_APPROVAL)
        report_raw = brief.get("guardrail")
        report = GuardrailReport.model_validate(report_raw) if report_raw else None
        has_errors = bool(report and not report.passed)

        if has_errors and not request.force:
            raise GuardrailBlockedError(
                "Guardrails still report blocking violations. Resolve them or resubmit "
                "with force=true and an override_reason.",
                details={
                    "brief_id": brief_id,
                    "violations": [
                        violation.model_dump(mode="json")
                        for violation in (report.violations if report else [])
                    ],
                },
            )
        if has_errors and not request.override_reason.strip():
            raise ValidationError(
                "override_reason is required when forcing approval past guardrail errors."
            )

        reason = f"Approved by {request.approver}."
        if request.notes:
            reason += f" Notes: {request.notes}"
        if has_errors:
            reason += f" Guardrail override: {request.override_reason}"
        self._transition(
            brief_id,
            BriefState.TRANSLATING,
            actor=request.approver,
            reason=reason,
            updates={
                "approver": request.approver,
                "override_reason": request.override_reason if has_errors else "",
            },
            payload={"guardrails_passed": not has_errors, "override": has_errors},
        )

        try:
            languages = [Language(value) for value in brief["languages"]]
            translations = self.translator.translate_all(brief["body"], languages)
            for result in translations:
                self.db.add_translation(
                    brief_id=brief_id,
                    language=result.language.value,
                    text=result.text,
                    back_translation=result.back_translation,
                    similarity=result.similarity,
                    attempt=result.attempt,
                )
            self._create_cms_drafts(brief_id, brief, translations)
            design_asset = self._generate_design(brief_id, brief)
        except AppError as exc:
            self.db.delete_cms_documents(brief_id)
            self._mark_failed(brief_id, exc)
            raise
        except Exception as exc:  # noqa: BLE001
            self.db.delete_cms_documents(brief_id)
            self._mark_failed(brief_id, exc)
            raise PipelineFailure(
                f"Staging failed for brief {brief_id}: {type(exc).__name__}: {exc}",
                details={"brief_id": brief_id},
            ) from exc

        updates = {}
        if design_asset is not None:
            updates["design_asset_json"] = design_asset.model_dump()
        try:
            self._transition(
                brief_id,
                BriefState.STAGED,
                actor="system",
                reason=(
                    "Translations passed back-translation check at "
                    f"{self.settings.translation_similarity_threshold:.2f} threshold. "
                    "Content staged in the local mini-CMS."
                ),
                updates=updates,
                payload={
                    "translations": len(translations),
                    "languages": [item.language.value for item in translations],
                    "min_similarity": min(item.similarity for item in translations),
                },
            )
            self.cms.set_status(brief_id, "staged")
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(brief_id, exc)
            raise
        return self.get_brief(brief_id)

    def reject(self, brief_id: str, request: RejectRequest) -> BriefOut:
        brief = self._require_brief(brief_id)
        self._expect_state(brief, BriefState.PENDING_APPROVAL)
        self._transition(
            brief_id,
            BriefState.REJECTED,
            actor=request.actor,
            reason=request.reason,
            payload={"reason": request.reason},
        )
        return self.get_brief(brief_id)

    def publish(self, brief_id: str, request: PublishRequest) -> BriefOut:
        brief = self._require_brief(brief_id)
        self._expect_state(brief, BriefState.STAGED)
        self._transition(
            brief_id,
            BriefState.PUBLISHED,
            actor=request.actor,
            reason="Published to the local mini-CMS.",
            payload={"cms_status": "published"},
        )
        self.cms.set_status(brief_id, "published")
        return self.get_brief(brief_id)

    # ---------------------------------------------------------------- reads
    def get_brief(self, brief_id: str) -> BriefOut:
        brief = self._require_brief(brief_id)
        translations = [
            TranslationResult(
                language=Language(row["language"]),
                text=row["text"],
                back_translation=row["back_translation"],
                similarity=row["similarity"],
                attempt=row["attempt"],
            )
            for row in self.db.list_translations(brief_id)
        ]
        documents = [
            CMSDocument.model_validate(row)
            for row in self.cms.list_documents(brief_id=brief_id)
        ]
        critique = [CriticNote.model_validate(item) for item in brief.get("critique", [])]
        guardrail = (
            GuardrailReport.model_validate(brief["guardrail"])
            if brief.get("guardrail")
            else None
        )
        initial_guardrail = (
            GuardrailReport.model_validate(brief["initial_guardrail"])
            if brief.get("initial_guardrail")
            else None
        )
        return BriefOut(
            id=brief["id"],
            title=brief["title"],
            vertical=Vertical(brief["vertical"]),
            brief=brief["brief"],
            fact_sheet_id=brief["fact_sheet_id"],
            fact_sheet=FactSheet.model_validate(brief["fact_sheet"]),
            languages=[Language(value) for value in brief["languages"]],
            state=BriefState(brief["state"]),
            headline=brief["headline"],
            body=brief["body"],
            guardrail=guardrail,
            initial_guardrail=initial_guardrail,
            critique=critique,
            revision_count=brief["revision_count"],
            approver=brief["approver"],
            override_reason=brief["override_reason"],
            task_ref=TaskRef.model_validate(brief["task_ref"]) if brief.get("task_ref") else None,
            design_asset=(
                DesignAsset.model_validate(brief["design_asset"])
                if brief.get("design_asset")
                else None
            ),
            translations=translations,
            cms_documents=documents,
            created_at=brief["created_at"],
            updated_at=brief["updated_at"],
        )

    def list_briefs(self, limit: int = 50, offset: int = 0) -> list[BriefSummary]:
        summaries: list[BriefSummary] = []
        for brief in self.db.list_briefs(limit=limit, offset=offset):
            report = brief.get("guardrail") or {}
            summaries.append(
                BriefSummary(
                    id=brief["id"],
                    title=brief["title"],
                    vertical=Vertical(brief["vertical"]),
                    state=BriefState(brief["state"]),
                    revision_count=brief["revision_count"],
                    passed_guardrails=bool(report.get("passed")),
                    created_at=brief["created_at"],
                    updated_at=brief["updated_at"],
                )
            )
        return summaries

    def get_events(self, brief_id: str) -> list[EventOut]:
        self._require_brief(brief_id)
        return [EventOut.model_validate(event) for event in self.db.list_events(brief_id)]

    def get_translations(self, brief_id: str) -> list[TranslationResult]:
        self._require_brief(brief_id)
        return [
            TranslationResult(
                language=Language(row["language"]),
                text=row["text"],
                back_translation=row["back_translation"],
                similarity=row["similarity"],
                attempt=row["attempt"],
            )
            for row in self.db.list_translations(brief_id)
        ]

    def get_cms_documents(
        self, brief_id: str | None = None, status: str | None = None
    ) -> list[CMSDocument]:
        return [
            CMSDocument.model_validate(row)
            for row in self.cms.list_documents(brief_id=brief_id, status=status)
        ]

    def get_cms_document(self, doc_id: str) -> CMSDocument:
        row = self.cms.get_document(doc_id)
        if row is None:
            raise NotFoundError(f"CMS document {doc_id} not found.")
        return CMSDocument.model_validate(row)

    # -------------------------------------------------------------- internals
    def _create_cms_drafts(
        self,
        brief_id: str,
        brief: dict,
        translations: list[TranslationResult],
    ) -> None:
        self.cms.create_draft(
            brief_id=brief_id,
            language="en",
            title=brief["headline"],
            body=brief["body"],
        )
        for result in translations:
            self.cms.create_draft(
                brief_id=brief_id,
                language=result.language.value,
                title=f"{brief['headline']} [{result.language.value}]",
                body=result.text,
            )

    def _generate_design(self, brief_id: str, brief: dict) -> DesignAsset | None:
        if not self.settings.generate_design_assets:
            return None
        try:
            return self.design_provider.create_card(
                title=brief["headline"],
                body=brief["body"],
                language="en",
                brief_id=brief_id,
            )
        except Exception as exc:  # noqa: BLE001 - design is best-effort
            logger.warning("Design generation failed for %s: %s", brief_id, exc)
            return DesignAsset(
                provider=self.design_provider.provider,
                kind="png",
                status="failed",
                note=(
                    f"{type(exc).__name__}: design generation failed; "
                    "content was still staged."
                ),
            )

    def _transition(
        self,
        brief_id: str,
        to_state: BriefState,
        *,
        actor: str,
        reason: str,
        updates: dict | None = None,
        payload: dict | None = None,
    ) -> dict:
        brief = self._require_brief(brief_id)
        from_state = BriefState(brief["state"])
        StateMachine.assert_transition(from_state, to_state)
        event = self.db.record_transition(
            brief_id=brief_id,
            from_state=from_state.value,
            to_state=to_state.value,
            actor=actor,
            reason=reason,
            updates=updates or {},
            payload=payload or {},
        )
        self._emit(event)
        self._sync_task(brief_id, to_state)
        return event

    def _emit(self, event: dict) -> None:
        try:
            self.webhooks.publish(event)
        except Exception as exc:  # noqa: BLE001 - audit failure must not roll back state
            logger.warning("Webhook emission failed for event %s: %s", event.get("id"), exc)

    def _sync_task(self, brief_id: str, state: BriefState) -> None:
        if state.value not in {
            BriefState.PENDING_APPROVAL.value,
            BriefState.STAGED.value,
            BriefState.PUBLISHED.value,
            BriefState.REJECTED.value,
            BriefState.FAILED.value,
        }:
            return
        brief = self._require_brief(brief_id)
        ref_raw = brief.get("task_ref")
        if not ref_raw:
            return
        try:
            self.task_tracker.update_status(TaskRef.model_validate(ref_raw), state.value)
        except Exception as exc:  # noqa: BLE001 - tracker outage must not kill the pipeline
            logger.warning("Task tracker update failed for %s: %s", brief_id, exc)

    def _mark_failed(self, brief_id: str, exc: Exception) -> None:
        brief = self.db.get_brief(brief_id)
        if not brief:
            return
        state = BriefState(brief["state"])
        if state in {BriefState.FAILED, BriefState.PUBLISHED, BriefState.REJECTED}:
            return
        if not StateMachine.can_transition(state, BriefState.FAILED):
            return
        try:
            self._transition(
                brief_id,
                BriefState.FAILED,
                actor="system",
                reason=f"{type(exc).__name__}: {exc}"[:500],
                payload={"error": type(exc).__name__},
            )
        except Exception as nested:  # noqa: BLE001
            logger.error("Could not mark brief %s failed: %s", brief_id, nested)

    def _expect_state(self, brief: dict, expected: BriefState) -> None:
        if brief["state"] != expected.value:
            raise InvalidTransitionError(
                f"Brief {brief['id']} is in state '{brief['state']}', "
                f"expected '{expected.value}'.",
                details={"current_state": brief["state"], "expected": expected.value},
            )

    def _require_brief(self, brief_id: str) -> dict:
        brief = self.db.get_brief(brief_id)
        if brief is None:
            raise NotFoundError(f"Brief {brief_id} not found.")
        return brief
