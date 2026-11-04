"""Prompt-library, golden-brief, and eval-report tests."""

from __future__ import annotations

import json

import pytest

from app.enums import Vertical
from app.evals.report import render_report
from app.evals.runner import GOLDEN_BRIEFS_PATH, load_golden_briefs, run_eval
from app.facts import load_fact_sheets
from app.prompts import PromptLibrary


@pytest.fixture(scope="module")
def prompts() -> PromptLibrary:
    return PromptLibrary()


def test_prompt_registry_versions_and_active_defaults(prompts: PromptLibrary) -> None:
    expected = {"draft", "critic", "translation", "back_translation", "judge"}
    assert set(prompts.registry["prompts"]) == expected
    assert prompts.registry["library_version"] == "1.0.0"
    assert prompts.active_version("draft") == "v2"
    assert prompts.versions("draft") == ["v1", "v2"]
    assert prompts.resolve_version("draft") == "v2"
    assert prompts.resolve_version("draft", "v1") == "v1"
    assert prompts.active_versions()["draft"] == "v2"
    assert prompts.active_versions({"draft": "v1"})["draft"] == "v1"
    assert all(
        prompts.active_versions()[name] == prompts.active_version(name)
        for name in expected
    )


def test_unknown_prompt_or_version_is_an_error(prompts: PromptLibrary) -> None:
    with pytest.raises(KeyError):
        prompts.get("does-not-exist")
    with pytest.raises(KeyError):
        prompts.resolve_version("draft", "v99")


def test_prompt_template_identity_and_changelog(prompts: PromptLibrary) -> None:
    template = prompts.get("draft", "v2")
    assert template.id == "draft"
    assert template.version == "v2"
    assert template.created == "2026-11-01"
    assert "strict fact-sheet grounding" in template.changelog
    assert prompts.get("draft", "v2") is template  # cached

    changelog = prompts.changelog("draft")
    assert len(changelog) == 2
    assert changelog[0].startswith("v1 (2026-10-27)")
    assert changelog[1].startswith("v2 (2026-11-01)")


def test_prompt_rendering_is_strict(prompts: PromptLibrary) -> None:
    system, user = prompts.render(
        "translation",
        "v1",
        language_name="Hindi",
        text="No added sugar.",
    )
    assert "Hindi" in system
    assert "No added sugar." in user

    with pytest.raises(ValueError) as excinfo:
        prompts.render("translation", "v1", language_name="Hindi")
    assert "text" in str(excinfo.value)

    system, user = prompts.render(
        "draft",
        "v2",
        brand="Alder Grove",
        vertical="food_beverage",
        brief="Write a launch post.",
        fact_sheet="Facts here",
        approved_claims=["No added sugar"],
        required_disclaimers=["Allergen information."],
        max_sentence_words=26,
    )
    assert "HEADLINE" in user and "BODY" in user
    assert "Allergen information." in user
    assert "26" in system


def test_golden_briefs_are_complete_and_consistent() -> None:
    briefs = load_golden_briefs()
    assert len(briefs) == 20
    assert len({brief.id for brief in briefs}) == 20
    assert sum(1 for brief in briefs if brief.stress_test) == 2
    assert {brief.vertical for brief in briefs} == {
        Vertical.FOOD_BEVERAGE,
        Vertical.MANUFACTURING,
    }

    facts = load_fact_sheets()
    for brief in briefs:
        sheet = facts.get(brief.fact_sheet_id)
        assert sheet.vertical == brief.vertical, brief.id
        assert brief.languages, brief.id
        assert len(brief.brief) >= 10
    assert GOLDEN_BRIEFS_PATH.exists()


def test_run_eval_produces_report_and_metrics(tmp_path) -> None:
    result = run_eval(output_dir=tmp_path)

    assert result["errors"] == []
    assert result["meta"]["mode"] == "mock"
    assert result["meta"]["brief_count"] == 20
    assert result["meta"]["translation_similarity_threshold"] == 0.72
    assert result["meta"]["max_revision_loops"] == 2
    assert len(result["results"]) == 20
    assert all(row["state"] == "published" for row in result["results"])
    assert all(row["error"] == "" for row in result["results"])

    metrics = result["metrics"]
    assert metrics["guardrail_pass_rate"] == 100.0
    assert metrics["unsupported_claim_rate"] == 0.0
    assert metrics["initial_unsupported_claim_rate"] == 10.0
    assert metrics["brand_voice_rubric_score"] == 5.0
    assert metrics["back_translation_similarity"] == 1.0
    assert metrics["min_back_translation_similarity"] == 1.0
    assert metrics["translation_pass_rate"] == 100.0
    assert metrics["briefs_revised"] == 2
    assert metrics["overrides"] == 0
    assert metrics["webhook_deliveries"] == 144

    report = tmp_path / "eval_report.md"
    results_path = tmp_path / "eval_results.json"
    assert report.exists() and results_path.exists()
    markdown = report.read_text(encoding="utf-8")
    assert "Mock mode uses deterministic fake providers" in markdown
    assert "**not** evidence of live LLM writing quality" in markdown
    assert "| Guardrail pass rate | 100.0% |" in markdown
    assert "| Unsupported-claim rate | 0.0% |" in markdown
    assert "| `gb-07` | food_beverage | 1 | fail | pass |" in markdown
    assert "| `gb-17` | manufacturing | 1 | fail | pass |" in markdown

    stored = json.loads(results_path.read_text(encoding="utf-8"))
    assert stored["metrics"] == metrics
    assert stored["meta"]["prompt_versions"]["draft"] == "v2"


def test_report_rendering_escapes_no_metrics() -> None:
    result = {
        "meta": {
            "run_id": "eval-test",
            "generated_at": "2026-11-03T00:00:00+00:00",
            "mode": "mock",
            "commit": "abc1234",
            "duration_seconds": 1.0,
            "brief_count": 0,
            "max_revision_loops": 2,
            "translation_similarity_threshold": 0.72,
            "prompt_versions": {"draft": "v2"},
        },
        "metrics": {
            "guardrail_pass_rate": None,
            "unsupported_claim_rate": None,
            "initial_unsupported_claim_rate": None,
            "brand_voice_rubric_score": None,
            "back_translation_similarity": None,
            "min_back_translation_similarity": None,
            "translation_pass_rate": None,
            "briefs_revised": 0,
            "overrides": 0,
            "webhook_deliveries": 0,
        },
        "results": [],
    }
    markdown = render_report(result)
    assert "| Guardrail pass rate | — |" in markdown
    assert "| Brand-voice rubric score | — / 5 |" in markdown
    assert "`abc1234`" in markdown
