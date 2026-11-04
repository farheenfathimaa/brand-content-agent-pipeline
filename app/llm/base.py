"""Provider-agnostic LLM interface used by the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.enums import Language, Vertical
from app.schemas import FactSheet, GuardrailReport, JudgeResult


@dataclass
class DraftResult:
    headline: str
    body: str


@dataclass
class RevisionResult:
    headline: str
    body: str
    notes: list[str] = field(default_factory=list)
    addressed_rules: list[str] = field(default_factory=list)


class LLMClient(Protocol):
    name: str

    def draft(
        self,
        *,
        brief: str,
        fact_sheet: FactSheet,
        vertical: Vertical,
        required_disclaimers: list[str],
        max_sentence_words: int,
        metadata: dict,
    ) -> DraftResult: ...

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
    ) -> RevisionResult: ...

    def translate(self, *, text: str, language: Language) -> str: ...

    def back_translate(self, *, text: str, language: Language) -> str: ...

    def judge(
        self,
        *,
        text: str,
        fact_sheet: FactSheet,
        rubric: list[dict[str, str]],
        report: GuardrailReport | None = None,
    ) -> JudgeResult: ...
