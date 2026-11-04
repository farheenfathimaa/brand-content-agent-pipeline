"""Pydantic request/response models and guardrail result types."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import BriefState, Language, Severity, Vertical, ViolationCategory


class FactSheet(BaseModel):
    """A fictional, supplied source of truth for a piece of content."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=2, max_length=80)
    brand: str = Field(min_length=2, max_length=80)
    vertical: Vertical
    product_name: str = Field(min_length=2, max_length=120)
    category: str = Field(default="", max_length=120)
    facts: list[str] = Field(min_length=1)
    approved_claims: list[str] = Field(default_factory=list)
    notes: str = ""

    def corpus(self) -> str:
        parts = [self.product_name, self.category, *self.facts, *self.approved_claims, self.notes]
        return " ".join(part for part in parts if part)


class BriefCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    vertical: Vertical
    brief: str = Field(min_length=10, max_length=5000)
    fact_sheet_id: str = Field(min_length=2, max_length=80)
    languages: list[Language] = Field(
        default_factory=lambda: [Language.HI, Language.ES, Language.DE, Language.FR]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "brief")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, value: list[Language]) -> list[Language]:
        if not value:
            raise ValueError("at least one language is required")
        seen: list[Language] = []
        for language in value:
            if language not in seen:
                seen.append(language)
        return seen


class Violation(BaseModel):
    rule_id: str
    category: ViolationCategory
    severity: Severity = Severity.ERROR
    message: str
    evidence: str = ""


class GuardrailReport(BaseModel):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    pack_id: str = ""
    pack_version: str = ""
    checked_at: str = ""
    summary: str = ""

    @property
    def error_count(self) -> int:
        return sum(1 for violation in self.violations if violation.severity == Severity.ERROR)

    @property
    def unsupported_claims(self) -> list[Violation]:
        return [
            violation
            for violation in self.violations
            if violation.category == ViolationCategory.UNSUPPORTED_CLAIM
        ]


class CriticNote(BaseModel):
    summary: str
    addressed_rules: list[str] = Field(default_factory=list)


class TranslationResult(BaseModel):
    language: Language
    text: str
    back_translation: str
    similarity: float = Field(ge=0.0, le=1.0)
    attempt: int = 1


class JudgeResult(BaseModel):
    score: float = Field(ge=1.0, le=5.0)
    dimensions: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    judge_mode: str = "mock"


class CMSDocument(BaseModel):
    id: str
    brief_id: str
    language: str
    title: str
    body: str
    status: str
    slug: str
    created_at: str
    updated_at: str


class TaskRef(BaseModel):
    provider: str
    external_id: str
    url: str = ""


class DesignAsset(BaseModel):
    provider: str
    kind: str
    status: str
    path: str = ""
    external_id: str = ""
    rendered_with: str = ""
    note: str = ""


class BriefOut(BaseModel):
    id: str
    title: str
    vertical: Vertical
    brief: str
    fact_sheet_id: str
    fact_sheet: FactSheet
    languages: list[Language]
    state: BriefState
    headline: str = ""
    body: str = ""
    guardrail: GuardrailReport | None = None
    initial_guardrail: GuardrailReport | None = None
    critique: list[CriticNote] = Field(default_factory=list)
    revision_count: int = 0
    approver: str = ""
    override_reason: str = ""
    task_ref: TaskRef | None = None
    design_asset: DesignAsset | None = None
    translations: list[TranslationResult] = Field(default_factory=list)
    cms_documents: list[CMSDocument] = Field(default_factory=list)
    created_at: str
    updated_at: str


class BriefSummary(BaseModel):
    id: str
    title: str
    vertical: Vertical
    state: BriefState
    revision_count: int
    passed_guardrails: bool
    created_at: str
    updated_at: str


class EventOut(BaseModel):
    id: int
    brief_id: str
    from_state: str | None
    to_state: str
    actor: str
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=2, max_length=200)
    notes: str = Field(default="", max_length=2000)
    force: bool = False
    override_reason: str = Field(default="", max_length=1000)


class RejectRequest(BaseModel):
    actor: str = Field(min_length=2, max_length=200)
    reason: str = Field(min_length=3, max_length=2000)


class PublishRequest(BaseModel):
    actor: str = Field(default="publisher", min_length=2, max_length=200)


class PromptVersionOut(BaseModel):
    name: str
    active_version: str
    versions: list[str]
    changelog: list[str]


class HealthOut(BaseModel):
    status: str
    mode: str
    database: str
    llm_provider: str
    task_tracker: str
    design_provider: str
    webhook_configured: bool
    prompt_versions: dict[str, str]
    time: datetime
