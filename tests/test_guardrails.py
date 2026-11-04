"""Guardrail unit tests against the fictional rule packs and fact sheets."""

from __future__ import annotations

import pytest

from app.enums import Severity, Vertical, ViolationCategory
from app.facts import FactSheetRegistry, load_fact_sheets
from app.guardrails.engine import GuardrailEngine, RulePack
from app.schemas import FactSheet

FOOD_DISCLAIMER = "Allergen information: review the package label before consumption."
MFG_DISCLAIMER = "Safety data sheet available on request. Install only per local codes."


@pytest.fixture(scope="module")
def facts() -> FactSheetRegistry:
    return load_fact_sheets()


@pytest.fixture(scope="module")
def engine(facts: FactSheetRegistry) -> GuardrailEngine:
    return GuardrailEngine(product_catalog=facts.catalog_products())


def _check(
    engine: GuardrailEngine,
    fact_sheet: FactSheet,
    *,
    headline: str,
    body: str,
):
    return engine.check(headline=headline, body=body, fact_sheet=fact_sheet)


def _rule_ids(report) -> set[str]:
    return {violation.rule_id for violation in report.violations}


def test_both_rule_packs_load_with_expected_metadata(engine: GuardrailEngine) -> None:
    assert set(engine.packs) == {Vertical.FOOD_BEVERAGE, Vertical.MANUFACTURING}
    for vertical, pack in engine.packs.items():
        assert isinstance(pack, RulePack)
        assert pack.vertical == vertical
        assert pack.pack_id == vertical.value
        assert pack.version == "1.0.0"
        assert pack.brand.name == "Alder Grove"
        assert pack.required_disclaimers
        assert pack.brand_voice.rubric
        # Every pack must forbid exclamation marks and require grounded numbers.
        assert any(item.id == "bv-exclamation" for item in pack.brand_voice.banned_patterns)
        assert pack.groundedness.check_numbers is True


def test_clean_food_copy_passes_all_checks(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-sparkling-citrus")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Sparkling Citrus: No added sugar",
        body=(
            "Alder Grove Sparkling Citrus is made by Alder Grove. "
            "It has 0 g added sugar per 12 fl oz can. "
            "Cans are made from recyclable aluminum. "
            f"{FOOD_DISCLAIMER}"
        ),
    )
    assert report.passed is True, report.model_dump_json(indent=2)
    assert report.violations == []
    assert report.error_count == 0
    assert report.checks == {
        "banned_claims": True,
        "required_disclaimers": True,
        "brand_voice": True,
        "groundedness": True,
    }
    assert report.pack_id == "food_beverage"
    assert report.checked_at


def test_clean_manufacturing_copy_passes_all_checks(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-modular-bracket")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Modular Bracket M8: 18 kg load rating",
        body=(
            "Alder Grove Modular Bracket M8 is made from 304 stainless steel. "
            "It has an 18 kg load rating and fits M8 mounting holes. "
            f"{MFG_DISCLAIMER}"
        ),
    )
    assert report.passed is True, report.model_dump_json(indent=2)
    assert report.pack_id == "manufacturing"


@pytest.mark.parametrize(
    ("body", "expected_rule"),
    [
        ("This tea cures insomnia and is clinically proven.", "fb-health-cure"),
        ("This drink boosts your immune system.", "fb-immunity-boost"),
        ("A gentle detox for your day.", "fb-detox"),
        ("A doctor-recommended way to lose weight.", "fb-doctor-recommended"),
        ("Guaranteed to make your morning better.", "fb-guarantee"),
        ("A total superfood for busy people.", "fb-superfood"),
    ],
)
def test_banned_food_claims_are_blocking_errors(
    engine: GuardrailEngine,
    facts: FactSheetRegistry,
    body: str,
    expected_rule: str,
) -> None:
    sheet = facts.get("ag-herbal-tea")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Evening Herbal Tea",
        body=f"{body} {FOOD_DISCLAIMER}",
    )
    assert report.passed is False
    assert expected_rule in _rule_ids(report)
    flagged = next(item for item in report.violations if item.rule_id == expected_rule)
    assert flagged.category == ViolationCategory.BANNED_CLAIM
    assert flagged.severity == Severity.ERROR
    assert flagged.evidence


def test_manufacturing_absolute_claims_are_blocked(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-packaging-film")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Recyclable Packaging Film",
        body=(
            "This film is guaranteed 100% recyclable, unbreakable, and "
            f"maintenance-free. {MFG_DISCLAIMER}"
        ),
    )
    assert report.passed is False
    rules = _rule_ids(report)
    assert {"mf-guarantee", "mf-never-fails", "mf-maintenance-free"} <= rules
    assert all(
        item.severity == Severity.ERROR
        for item in report.violations
        if item.category == ViolationCategory.BANNED_CLAIM
    )


def test_missing_required_disclaimer_is_an_error(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-sparkling-citrus")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Sparkling Citrus is caffeine-free",
        body="Alder Grove Sparkling Citrus is made with cold-pressed citrus peel.",
    )
    assert report.passed is False
    missing = next(item for item in report.violations if item.rule_id == "fb-allergen")
    assert missing.category == ViolationCategory.REQUIRED_DISCLAIMER
    assert missing.evidence == FOOD_DISCLAIMER
    assert report.checks["required_disclaimers"] is False


def test_disclaimer_is_matched_after_whitespace_normalization(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-oat-bars")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Oat & Seed Bars",
        body=(
            "Alder Grove Oat & Seed Bars have 5 g fiber per bar. "
            "Allergen   information:\nreview the package label before consumption."
        ),
    )
    assert "fb-allergen" not in _rule_ids(report)


def test_supported_numbers_are_not_flagged(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-sparkling-citrus")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Sparkling Citrus",
        body=(
            "Alder Grove Sparkling Citrus has 0 g added sugar per 12 fl oz can "
            f"and comes in 4-can packs. {FOOD_DISCLAIMER}"
        ),
    )
    unsupported = {item.rule_id for item in report.unsupported_claims}
    assert not any(rule.startswith("grounded-number-") for rule in unsupported)
    assert "grounded-number-12floz" not in unsupported
    assert "grounded-number-4can" not in unsupported


def test_invented_numbers_are_unsupported_claims(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-sparkling-citrus")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Sparkling Citrus",
        body=(
            "Alder Grove Sparkling Citrus has 99 mg of citrus peel extract. "
            f"{FOOD_DISCLAIMER}"
        ),
    )
    assert "grounded-number-99mg" in _rule_ids(report)
    assert any(
        item.category == ViolationCategory.UNSUPPORTED_CLAIM
        for item in report.violations
        if item.rule_id == "grounded-number-99mg"
    )


def test_other_brand_products_are_unsupported(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-sparkling-citrus")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Sparkling Citrus",
        body=(
            "Try this alongside Alder Grove Cold Brew for an afternoon pick-me-up. "
            f"{FOOD_DISCLAIMER}"
        ),
    )
    assert "grounded-product-alder-grove-cold-brew" in _rule_ids(report)
    assert report.checks["groundedness"] is False


def test_claim_patterns_must_be_grounded_in_the_fact_sheet(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-oat-bars")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Oat & Seed Bars",
        body=(
            "Alder Grove Oat & Seed Bars are 100% organic and award-winning. "
            f"{FOOD_DISCLAIMER}"
        ),
    )
    rule_ids = _rule_ids(report)
    assert any(rule.startswith("grounded-claim-") for rule in rule_ids)
    evidence = " ".join(item.evidence for item in report.unsupported_claims).casefold()
    assert "100% organic" in evidence or "award-winning" in evidence


def test_voice_rules_catch_exclamation_banned_words_and_all_caps(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-sparkling-citrus")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Sparkling Citrus",
        body=(
            "This revolutionary drink is AMAZING! "
            f"{FOOD_DISCLAIMER}"
        ),
    )
    rules = _rule_ids(report)
    assert "bv-exclamation" in rules
    assert "bv-banned-word-revolutionary" in rules
    assert "bv-all-caps" in rules
    assert report.checks["brand_voice"] is False


def test_allowed_acronyms_do_not_trigger_all_caps_rule(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-modular-bracket")
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Modular Bracket M8",
        body=(
            "The M8 mounting holes match standard 304 stainless steel hardware. "
            f"{MFG_DISCLAIMER}"
        ),
    )
    assert "bv-all-caps" not in _rule_ids(report)


def test_long_sentences_and_paragraphs_are_voice_errors(
    engine: GuardrailEngine, facts: FactSheetRegistry
) -> None:
    sheet = facts.get("ag-herbal-tea")
    long_sentence = " ".join(["calm"] * 31) + "."
    report = _check(
        engine,
        sheet,
        headline="Alder Grove Evening Herbal Tea",
        body=f"{long_sentence} {FOOD_DISCLAIMER}",
    )
    assert "bv-sentence-length" in _rule_ids(report)
    assert report.checks["brand_voice"] is False
