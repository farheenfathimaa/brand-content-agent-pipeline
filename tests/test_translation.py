"""Translation, back-translation similarity, and staging-failure tests."""

from __future__ import annotations

import pytest

from app.enums import Language
from app.errors import TranslationQualityError
from app.llm.mock import MockLLM
from app.schemas import BriefCreate, TranslationResult
from app.translation import TranslationService, similarity_score


def test_similarity_score_bounds_and_normalization() -> None:
    assert similarity_score("No added sugar.", "No added sugar.") == 1.0
    assert similarity_score("No added   sugar.", "no added sugar.") == 1.0
    assert similarity_score("No added sugar.", "") == 0.0
    assert 0.0 < similarity_score("No added sugar.", "Completely different text") < 0.6
    assert 0.0 <= similarity_score("abc", "xyz") <= 1.0


@pytest.mark.parametrize("language", list(Language))
def test_mock_translation_round_trip_is_recognizable(language: Language) -> None:
    llm = MockLLM()
    source = (
        "Alder Grove Sparkling Citrus has no added sugar and is packaged in "
        "recyclable aluminum cans. Allergen information: review the package label."
    )
    translated = llm.translate(text=source, language=language)
    back = llm.back_translate(text=translated, language=language)
    assert translated.startswith(f"[mock-{language.value}]")
    assert back
    assert similarity_score(source, back) == 1.0


def test_translate_all_returns_every_language() -> None:
    service = TranslationService(MockLLM(), threshold=0.99)
    results = service.translate_all(
        "No added sugar and recyclable cans.", [Language.HI, Language.ES, Language.DE]
    )
    assert [item.language for item in results] == [Language.HI, Language.ES, Language.DE]
    assert all(isinstance(item, TranslationResult) for item in results)
    assert all(item.similarity >= 0.99 for item in results)
    assert all(item.attempt == 1 for item in results)


def test_translation_retries_before_succeeding() -> None:
    class FlakyLLM(MockLLM):
        def __init__(self) -> None:
            self.back_calls = 0

        def back_translate(self, *, text: str, language: Language) -> str:
            self.back_calls += 1
            if self.back_calls == 1:
                return "unrelated first attempt"
            return "No added sugar and recyclable cans."

    llm = FlakyLLM()
    service = TranslationService(llm, threshold=0.8, max_attempts=2)
    result = service.translate_one(
        "No added sugar and recyclable cans.", Language.ES
    )
    assert result.attempt == 2
    assert result.similarity >= 0.8
    assert llm.back_calls == 2


def test_translation_failure_includes_attempt_details() -> None:
    class BadLLM(MockLLM):
        def __init__(self) -> None:
            self.back_calls = 0

        def back_translate(self, *, text: str, language: Language) -> str:
            self.back_calls += 1
            return ""

    llm = BadLLM()
    service = TranslationService(llm, threshold=0.72, max_attempts=3)
    with pytest.raises(TranslationQualityError) as excinfo:
        service.translate_one("No added sugar.", Language.DE)
    error = excinfo.value
    assert error.status_code == 422
    assert error.code == "translation_quality_failed"
    assert error.details["language"] == "de"
    assert error.details["similarity"] == 0.0
    assert error.details["attempts"] == 3
    assert llm.back_calls == 3


def test_translate_all_reports_each_failing_language() -> None:
    class SelectiveLLM(MockLLM):
        def back_translate(self, *, text: str, language: Language) -> str:
            if language == Language.ES:
                return ""
            return super().back_translate(text=text, language=language)

    service = TranslationService(SelectiveLLM(), threshold=0.72, max_attempts=2)
    with pytest.raises(TranslationQualityError) as excinfo:
        service.translate_all("No added sugar.", [Language.HI, Language.ES])
    failures = excinfo.value.details["failures"]
    assert len(failures) == 1
    assert failures[0]["language"] == "es"


def test_staging_failure_marks_brief_failed_and_cleans_cms(
    container, clean_payload: dict, monkeypatch
) -> None:
    from app.schemas import ApprovalRequest

    brief = container.service.create_brief(BriefCreate(**clean_payload))

    def explode(text, languages):  # noqa: ANN001, ANN202
        raise TranslationQualityError(
            "synthetic translation outage",
            details={"simulated": True},
        )

    monkeypatch.setattr(container.translator, "translate_all", explode)

    with pytest.raises(TranslationQualityError):
        container.service.approve(
            brief.id, ApprovalRequest(approver="reviewer@example.com")
        )

    failed = container.service.get_brief(brief.id)
    assert failed.state.value == "failed"
    assert failed.translations == []
    assert failed.cms_documents == []
    events = container.service.get_events(brief.id)
    assert [event.to_state for event in events[-2:]] == ["translating", "failed"]
    assert events[-1].actor == "system"
    assert "synthetic translation outage" in events[-1].reason
