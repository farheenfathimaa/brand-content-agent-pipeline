"""Deterministic guardrail checks: claims, disclaimers, brand voice, grounding."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.enums import Severity, Vertical, ViolationCategory
from app.facts import load_fact_sheets
from app.schemas import FactSheet, GuardrailReport, Violation

RULES_DIR = Path(__file__).parent / "rules"

# Longest units first so "fl oz" wins over "oz", "mm" over "m", etc.
_UNIT = (
    r"(?:fl\s*oz|kcal|mg|kg|ml|mm|cm|ft|lbs?|oz|g|l|m|%|"
    r"hours?|hrs?|days?|weeks?|months?|years?|times|"
    r"packs?|bottles?|cans?|bars?|sachets?|servings?|units?)"
)
NUMBER_RE = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s*({_UNIT})?", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
ALL_CAPS_RE = re.compile(r"\b[A-Z]{4,}\b")


class BannedClaimRule(BaseModel):
    id: str
    pattern: str
    message: str
    severity: Severity = Severity.ERROR


class DisclaimerRule(BaseModel):
    id: str
    text: str
    when: str = "always"
    severity: Severity = Severity.ERROR
    message: str = ""


class VoicePattern(BaseModel):
    id: str
    pattern: str
    message: str
    severity: Severity = Severity.ERROR


class RubricItem(BaseModel):
    id: str
    description: str


class BrandVoiceRules(BaseModel):
    max_sentence_words: int = 30
    max_paragraph_words: int = 120
    banned_words: list[str] = Field(default_factory=list)
    banned_patterns: list[VoicePattern] = Field(default_factory=list)
    no_all_caps: bool = True
    allowed_acronyms: list[str] = Field(default_factory=list)
    rubric: list[RubricItem] = Field(default_factory=list)


class GroundednessRules(BaseModel):
    severity: Severity = Severity.ERROR
    check_numbers: bool = True
    check_products: bool = True
    check_claims: bool = True
    claim_patterns: list[str] = Field(default_factory=list)


class BrandInfo(BaseModel):
    name: str
    voice_summary: str = ""


class RulePack(BaseModel):
    pack_id: str
    version: str
    vertical: Vertical
    brand: BrandInfo
    banned_claims: list[BannedClaimRule] = Field(default_factory=list)
    required_disclaimers: list[DisclaimerRule] = Field(default_factory=list)
    brand_voice: BrandVoiceRules
    groundedness: GroundednessRules


class GuardrailEngine:
    def __init__(
        self,
        rules_dir: str | Path | None = None,
        product_catalog: list[str] | None = None,
    ) -> None:
        self.rules_dir = Path(rules_dir) if rules_dir else RULES_DIR
        self.packs: dict[Vertical, RulePack] = {}
        self._compiled: dict[Vertical, dict[str, object]] = {}
        if product_catalog is not None:
            self.product_catalog = product_catalog
        else:
            self.product_catalog = load_fact_sheets().catalog_products()
        self._load()

    def _load(self) -> None:
        for path in sorted(self.rules_dir.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            pack = RulePack.model_validate(raw)
            if pack.vertical in self.packs:
                raise ValueError(f"Duplicate rule pack for {pack.vertical}")
            banned = [(rule, self._compile(rule.pattern, rule.id)) for rule in pack.banned_claims]
            voice_patterns = [
                (rule, self._compile(rule.pattern, rule.id))
                for rule in pack.brand_voice.banned_patterns
            ]
            claims = [
                self._compile(pattern, f"claim-{index}")
                for index, pattern in enumerate(pack.groundedness.claim_patterns)
            ]
            banned_words = [
                (word, re.compile(rf"(?i)\b{re.escape(word)}\b"))
                for word in pack.brand_voice.banned_words
            ]
            self.packs[pack.vertical] = pack
            self._compiled[pack.vertical] = {
                "banned": banned,
                "voice_patterns": voice_patterns,
                "claims": claims,
                "banned_words": banned_words,
            }

    @staticmethod
    def _compile(pattern: str, rule_id: str) -> re.Pattern[str]:
        try:
            return re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regex in rule '{rule_id}': {exc}") from exc

    def pack_for(self, vertical: Vertical) -> RulePack:
        return self.packs[vertical]

    def check(
        self,
        *,
        headline: str,
        body: str,
        fact_sheet: FactSheet,
    ) -> GuardrailReport:
        pack = self.pack_for(fact_sheet.vertical)
        compiled = self._compiled[fact_sheet.vertical]
        text = f"{headline}. {body}".strip()
        violations: list[Violation] = []

        violations.extend(self._check_banned_claims(text, compiled))
        violations.extend(self._check_disclaimers(body, pack))
        violations.extend(self._check_brand_voice(text, body, pack, compiled))
        violations.extend(
            self._check_groundedness(text, fact_sheet, pack, compiled)
        )

        errors = [item for item in violations if item.severity == Severity.ERROR]
        checks = {
            "banned_claims": not any(
                item.category == ViolationCategory.BANNED_CLAIM for item in errors
            ),
            "required_disclaimers": not any(
                item.category == ViolationCategory.REQUIRED_DISCLAIMER for item in errors
            ),
            "brand_voice": not any(
                item.category == ViolationCategory.BRAND_VOICE for item in errors
            ),
            "groundedness": not any(
                item.category == ViolationCategory.UNSUPPORTED_CLAIM for item in errors
            ),
        }
        summary = (
            f"{len(errors)} error(s), {len(violations) - len(errors)} warning(s) "
            f"across {len(checks)} checks."
        )
        return GuardrailReport(
            passed=not errors,
            violations=violations,
            checks=checks,
            pack_id=pack.pack_id,
            pack_version=pack.version,
            checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
            summary=summary,
        )

    # ------------------------------------------------------------- individual
    def _check_banned_claims(
        self, text: str, compiled: dict[str, object]
    ) -> list[Violation]:
        violations: list[Violation] = []
        for rule, pattern in compiled["banned"]:  # type: ignore[union-attr]
            match = pattern.search(text)
            if match:
                violations.append(
                    Violation(
                        rule_id=rule.id,
                        category=ViolationCategory.BANNED_CLAIM,
                        severity=rule.severity,
                        message=rule.message,
                        evidence=self._evidence(text, match.start(), match.end()),
                    )
                )
        return violations

    def _check_disclaimers(self, body: str, pack: RulePack) -> list[Violation]:
        violations: list[Violation] = []
        normalized_body = self._normalize_text(body)
        for rule in pack.required_disclaimers:
            if rule.when != "always":
                continue
            if self._normalize_text(rule.text) not in normalized_body:
                violations.append(
                    Violation(
                        rule_id=rule.id,
                        category=ViolationCategory.REQUIRED_DISCLAIMER,
                        severity=rule.severity,
                        message=rule.message or "Required disclaimer is missing.",
                        evidence=rule.text,
                    )
                )
        return violations

    def _check_brand_voice(
        self, text: str, body: str, pack: RulePack, compiled: dict[str, object]
    ) -> list[Violation]:
        voice = pack.brand_voice
        violations: list[Violation] = []

        for sentence in SENTENCE_SPLIT_RE.split(text):
            words = WORD_RE.findall(sentence)
            if len(words) > voice.max_sentence_words:
                violations.append(
                    Violation(
                        rule_id="bv-sentence-length",
                        category=ViolationCategory.BRAND_VOICE,
                        message=(
                            f"Sentences must be {voice.max_sentence_words} words or fewer; "
                            f"found {len(words)}."
                        ),
                        evidence=self._truncate(sentence),
                    )
                )
                if len(violations) >= 3:
                    break

        for paragraph in re.split(r"\n\s*\n", body):
            word_count = len(WORD_RE.findall(paragraph))
            if word_count > voice.max_paragraph_words:
                violations.append(
                    Violation(
                        rule_id="bv-paragraph-length",
                        category=ViolationCategory.BRAND_VOICE,
                        message=(
                            f"Paragraphs must be {voice.max_paragraph_words} words or fewer; "
                            f"found {word_count}."
                        ),
                        evidence=self._truncate(paragraph),
                    )
                )

        for word, pattern in compiled["banned_words"]:  # type: ignore[union-attr]
            match = pattern.search(text)
            if match:
                violations.append(
                    Violation(
                        rule_id=f"bv-banned-word-{word}",
                        category=ViolationCategory.BRAND_VOICE,
                        message=f"Off-brand word: '{word}'.",
                        evidence=self._evidence(text, match.start(), match.end()),
                    )
                )

        for rule, pattern in compiled["voice_patterns"]:  # type: ignore[union-attr]
            match = pattern.search(text)
            if match:
                violations.append(
                    Violation(
                        rule_id=rule.id,
                        category=ViolationCategory.BRAND_VOICE,
                        severity=rule.severity,
                        message=rule.message,
                        evidence=self._evidence(text, match.start(), match.end()),
                    )
                )

        if voice.no_all_caps:
            allowed = {item.upper() for item in voice.allowed_acronyms}
            for match in ALL_CAPS_RE.finditer(text):
                token = match.group(0)
                if token.upper() in allowed:
                    continue
                violations.append(
                    Violation(
                        rule_id="bv-all-caps",
                        category=ViolationCategory.BRAND_VOICE,
                        message=f"Avoid all-caps words: '{token}'.",
                        evidence=token,
                    )
                )
                if len(violations) >= 6:
                    break
        return violations

    def _check_groundedness(
        self,
        text: str,
        fact_sheet: FactSheet,
        pack: RulePack,
        compiled: dict[str, object],
    ) -> list[Violation]:
        rules = pack.groundedness
        corpus = self._normalize_text(fact_sheet.corpus())
        violations: list[Violation] = []

        if rules.check_numbers:
            for match in NUMBER_RE.finditer(text):
                number = match.group(1)
                unit = (match.group(2) or "").replace(" ", "").lower()
                token = f"{number}{unit}".lower()
                if unit:
                    supported = token in corpus
                else:
                    supported = (
                        re.search(
                            rf"(?<![\d.]){re.escape(number)}(?![\d.])", corpus
                        )
                        is not None
                    )
                if not supported:
                    violations.append(
                        Violation(
                            rule_id=f"grounded-number-{token}",
                            category=ViolationCategory.UNSUPPORTED_CLAIM,
                            severity=rules.severity,
                            message=(
                                f"Numeric claim '{match.group(0).strip()}' is not present "
                                "in the supplied fact sheet."
                            ),
                            evidence=match.group(0).strip(),
                        )
                    )

        if rules.check_products and self.product_catalog:
            supplied = {name.casefold() for name in fact_sheet.product_name.split("|")}
            supplied.add(fact_sheet.product_name.casefold())
            for product in self.product_catalog:
                if product.casefold() in supplied:
                    continue
                match = re.search(rf"(?i)\b{re.escape(product)}\b", text)
                if match:
                    violations.append(
                        Violation(
                            rule_id=f"grounded-product-{self._slug(product)}",
                            category=ViolationCategory.UNSUPPORTED_CLAIM,
                            severity=rules.severity,
                            message=(
                                f"Product '{product}' is not in the supplied fact sheet."
                            ),
                            evidence=match.group(0),
                        )
                    )

        if rules.check_claims:
            for index, pattern in enumerate(compiled["claims"]):  # type: ignore[union-attr]
                match = pattern.search(text)
                if not match:
                    continue
                phrase = match.group(0)
                if self._normalize_text(phrase) not in corpus:
                    violations.append(
                        Violation(
                            rule_id=f"grounded-claim-{index}",
                            category=ViolationCategory.UNSUPPORTED_CLAIM,
                            severity=rules.severity,
                            message=(
                                f"Claim '{phrase}' is not supported by the supplied "
                                "fact sheet."
                            ),
                            evidence=phrase,
                        )
                    )
        return violations

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _normalize_text(value: str) -> str:
        lowered = value.casefold()
        lowered = re.sub(r"\bfl\s+oz\b", "floz", lowered)
        lowered = re.sub(r"(?<=\d)[\s-]+(?=[a-z%])", "", lowered)
        return re.sub(r"\s+", " ", lowered).strip()

    @staticmethod
    def _evidence(text: str, start: int, end: int) -> str:
        left = max(0, start - 35)
        right = min(len(text), end + 35)
        return re.sub(r"\s+", " ", text[left:right]).strip()

    @staticmethod
    def _truncate(value: str, limit: int = 100) -> str:
        clean = re.sub(r"\s+", " ", value).strip()
        return clean if len(clean) <= limit else f"{clean[: limit - 1]}…"

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
