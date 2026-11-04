"""LiteLLM-backed client: Groq primary, Gemini fallback, optional Claude."""

from __future__ import annotations

import json
import logging
import re

from app.config import Settings
from app.enums import Language, Vertical
from app.errors import LLMError
from app.llm.base import DraftResult, RevisionResult
from app.prompts import PromptLibrary
from app.schemas import FactSheet, GuardrailReport, JudgeResult

logger = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    Language.HI: "Hindi",
    Language.ES: "Spanish",
    Language.DE: "German",
    Language.FR: "French",
}


class LiteLLMClient:
    name = "litellm"

    def __init__(
        self,
        settings: Settings,
        prompts: PromptLibrary,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.prompts = prompts
        self.overrides = overrides or {}
        self.provider_models = self._build_model_chain()

    def _build_model_chain(self) -> list[tuple[str, str]]:
        candidates = [
            (self.settings.llm_primary_model, self.settings.groq_api_key),
            (self.settings.llm_fallback_model, self.settings.gemini_api_key),
        ]
        if self.settings.llm_anthropic_model:
            candidates.append((self.settings.llm_anthropic_model, self.settings.anthropic_api_key))
        chain: list[tuple[str, str]] = []
        for model, key in candidates:
            if not model or not key:
                continue
            if self._key_for_model(model, key):
                chain.append((model, key))
        if not chain:
            raise LLMError(
                "No live LLM credentials are available. Set GROQ_API_KEY, GEMINI_API_KEY, "
                "or ANTHROPIC_API_KEY + LLM_ANTHROPIC_MODEL, or use APP_MODE=mock."
            )
        return chain

    @staticmethod
    def _key_for_model(model: str, key: str) -> str:
        lowered = model.casefold()
        if lowered.startswith(("groq/", "gemini/", "anthropic/", "claude")):
            return key
        # LiteLLM also supports provider-prefixed custom routes; use the supplied key.
        return key

    def _complete(self, system: str, user: str) -> str:
        try:
            import litellm

            litellm.suppress_debug_info = True
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise LLMError("litellm is not installed. Run `pip install -r requirements.txt`.") from exc

        errors: list[str] = []
        for model, api_key in self.provider_models:
            try:
                response = litellm.completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    api_key=api_key,
                    temperature=self.settings.llm_temperature,
                    max_tokens=self.settings.llm_max_tokens,
                    timeout=90,
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise ValueError("empty completion")
                return content.strip()
            except Exception as exc:  # noqa: BLE001 - fallback chain is intentional
                logger.warning("LLM model %s failed: %s", model, type(exc).__name__)
                errors.append(f"{model}: {type(exc).__name__}")
        raise LLMError(
            "All configured LLM providers failed. " + "; ".join(errors),
            details={"providers": [model for model, _ in self.provider_models]},
        )

    def _version(self, name: str) -> str:
        return self.prompts.resolve_version(name, self.overrides.get(name, ""))

    # ------------------------------------------------------------------ tasks
    def draft(
        self,
        *,
        brief: str,
        fact_sheet: FactSheet,
        vertical: Vertical,
        required_disclaimers: list[str],
        max_sentence_words: int,
        metadata: dict,
    ) -> DraftResult:
        del metadata
        system, user = self.prompts.render(
            "draft",
            self._version("draft"),
            brand=fact_sheet.brand,
            vertical=vertical.value,
            brief=brief,
            fact_sheet=_fact_sheet_text(fact_sheet),
            approved_claims=fact_sheet.approved_claims,
            required_disclaimers=required_disclaimers,
            max_sentence_words=max_sentence_words,
        )
        headline, body = _parse_sections(self._complete(system, user))
        return DraftResult(headline=headline, body=body)

    def revise(
        self,
        *,
        brief: str,
        draft: DraftResult,
        report: GuardrailReport,
        fact_sheet: FactSheet,
        vertical: Vertical,
        required_disclaimers: list[str],
        max_sentence_words: int,
    ) -> RevisionResult:
        violations = "\n".join(
            f"- [{item.category.value}/{item.severity.value}] {item.rule_id}: "
            f"{item.message} Evidence: {item.evidence}"
            for item in report.violations
        ) or "- none"
        system, user = self.prompts.render(
            "critic",
            self._version("critic"),
            brand=fact_sheet.brand,
            vertical=vertical.value,
            brief=brief,
            draft=f"HEADLINE: {draft.headline}\nBODY: {draft.body}",
            violations=violations,
            fact_sheet=_fact_sheet_text(fact_sheet),
            approved_claims=fact_sheet.approved_claims,
            required_disclaimers=required_disclaimers,
            max_sentence_words=max_sentence_words,
        )
        headline, body = _parse_sections(self._complete(system, user))
        return RevisionResult(
            headline=headline,
            body=body,
            notes=["Live critic revision."],
            addressed_rules=sorted({item.rule_id for item in report.violations}),
        )

    def translate(self, *, text: str, language: Language) -> str:
        system, user = self.prompts.render(
            "translation",
            self._version("translation"),
            language_name=LANGUAGE_NAMES[language],
            text=text,
        )
        return self._complete(system, user).strip()

    def back_translate(self, *, text: str, language: Language) -> str:
        system, user = self.prompts.render(
            "back_translation",
            self._version("back_translation"),
            language_name=LANGUAGE_NAMES[language],
            text=text,
        )
        return self._complete(system, user).strip()

    def judge(
        self,
        *,
        text: str,
        fact_sheet: FactSheet,
        rubric: list[dict[str, str]],
        report: GuardrailReport | None = None,
    ) -> JudgeResult:
        del report
        system, user = self.prompts.render(
            "judge",
            self._version("judge"),
            brand=fact_sheet.brand,
            vertical=fact_sheet.vertical.value,
            text=text,
            rubric=rubric,
        )
        raw = self._complete(system, user)
        data = _extract_json(raw)
        if not data:
            return JudgeResult(
                score=3.0,
                rationale="Judge returned non-JSON output; defaulted to 3.0.",
                judge_mode="live-llm",
            )
        score = _clamp(float(data.get("score", 3.0)))
        dimensions = {
            str(key): _clamp(float(value))
            for key, value in dict(data.get("dimensions", {})).items()
        }
        return JudgeResult(
            score=score,
            dimensions=dimensions,
            rationale=str(data.get("rationale", "")),
            judge_mode="live-llm",
        )


def _fact_sheet_text(fact_sheet: FactSheet) -> str:
    facts = "\n".join(f"- {fact}" for fact in fact_sheet.facts)
    claims = "\n".join(f"- {claim}" for claim in fact_sheet.approved_claims) or "- none"
    return (
        f"Brand: {fact_sheet.brand}\n"
        f"Product: {fact_sheet.product_name}\n"
        f"Category: {fact_sheet.category}\n"
        f"Facts:\n{facts}\n"
        f"Approved claims:\n{claims}"
    )


def _parse_sections(text: str) -> tuple[str, str]:
    headline = ""
    body_lines: list[str] = []
    in_body = False
    for line in text.strip().splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("HEADLINE:"):
            headline = stripped.split(":", 1)[1].strip()
            in_body = False
        elif upper.startswith("BODY:"):
            body_lines.append(stripped.split(":", 1)[1].strip())
            in_body = True
        elif in_body:
            body_lines.append(stripped)
        elif not headline and stripped:
            headline = stripped
    body = "\n".join(line for line in body_lines if line).strip()
    if not headline:
        headline = text.strip().splitlines()[0][:160] if text.strip() else "Untitled"
    if not body:
        body = text.strip()
    return headline, body


def _extract_json(text: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).replace("```", "").strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp(value: float) -> float:
    return round(max(1.0, min(5.0, value)), 1)
