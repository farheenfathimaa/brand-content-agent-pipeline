"""Dependency container used by the API, demo, and eval runner."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC

import httpx

from app.config import Settings
from app.db import Database
from app.facts import FactSheetRegistry, load_fact_sheets
from app.guardrails.engine import GuardrailEngine
from app.integrations.base import DesignProvider, TaskTracker
from app.integrations.cms import LocalCMS
from app.integrations.design import build_design_provider
from app.integrations.task_tracker import build_task_tracker
from app.llm import build_llm
from app.llm.base import LLMClient
from app.pipeline import PipelineService
from app.prompts import PromptLibrary
from app.translation import TranslationService
from app.webhooks import WebhookDispatcher

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    facts: FactSheetRegistry
    prompts: PromptLibrary
    llm: LLMClient
    guardrails: GuardrailEngine
    translator: TranslationService
    cms: LocalCMS
    task_tracker: TaskTracker
    design_provider: DesignProvider
    webhooks: WebhookDispatcher
    service: PipelineService
    prompt_versions: dict[str, str]

    def health(self) -> dict:
        from datetime import datetime

        return {
            "status": "ok",
            "mode": self.settings.app_mode,
            "database": self.settings.database_path,
            "llm_provider": getattr(self.llm, "name", "unknown"),
            "task_tracker": getattr(self.task_tracker, "provider", "unknown"),
            "design_provider": getattr(self.design_provider, "provider", "unknown"),
            "webhook_configured": bool(self.settings.webhook_url),
            "prompt_versions": self.prompt_versions,
            "time": datetime.now(UTC),
        }


def build_container(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    task_tracker: TaskTracker | None = None,
    design_provider: DesignProvider | None = None,
    webhook_client: httpx.Client | None = None,
) -> Container:
    settings = settings or Settings()
    settings.validate_live()

    if settings.webhook_url and not settings.webhook_secret:
        settings = settings.model_copy(update={"webhook_secret": secrets.token_hex(32)})
        logger.warning(
            "WEBHOOK_URL is set without WEBHOOK_SECRET; using an ephemeral signing "
            "secret. Set WEBHOOK_SECRET for a stable secret across restarts."
        )

    db = Database(settings.database_path)
    db.init_schema()
    facts = load_fact_sheets()
    prompts = PromptLibrary()
    llm_client = llm or build_llm(settings, prompts)
    guardrails = GuardrailEngine(product_catalog=facts.catalog_products())
    translator = TranslationService(
        llm_client, threshold=settings.translation_similarity_threshold
    )
    cms = LocalCMS(db)
    tracker = task_tracker or build_task_tracker(settings)
    design = design_provider or build_design_provider(settings)
    webhooks = WebhookDispatcher(db, settings, client=webhook_client)

    overrides = {
        "draft": settings.prompt_draft_version,
        "critic": settings.prompt_critic_version,
        "translation": settings.prompt_translation_version,
        "back_translation": settings.prompt_translation_version,
        "judge": settings.prompt_judge_version,
    }
    prompt_versions = prompts.active_versions(overrides)

    service = PipelineService(
        db=db,
        settings=settings,
        facts=facts,
        guardrails=guardrails,
        llm=llm_client,
        translator=translator,
        cms=cms,
        task_tracker=tracker,
        design_provider=design,
        webhooks=webhooks,
        prompt_versions=prompt_versions,
    )
    return Container(
        settings=settings,
        db=db,
        facts=facts,
        prompts=prompts,
        llm=llm_client,
        guardrails=guardrails,
        translator=translator,
        cms=cms,
        task_tracker=tracker,
        design_provider=design,
        webhooks=webhooks,
        service=service,
        prompt_versions=prompt_versions,
    )
