"""Deterministic mock LLM.

The mock is intentionally boring: it builds short, fact-sheet-grounded copy,
uses a tiny pseudo-translation dictionary, and scores with a transparent
heuristic. It exists so the full pipeline runs with zero API keys.
"""

from __future__ import annotations

import re

from app.enums import Language, Vertical
from app.llm.base import DraftResult, RevisionResult
from app.schemas import FactSheet, GuardrailReport, JudgeResult

WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)

# Small pseudo-translation dictionaries. Values are chosen to be unlikely to
# appear unchanged in English source text, so the reverse map is unambiguous.
GLOSSARIES: dict[Language, dict[str, str]] = {
    Language.ES: {
        "the": "el", "a": "un", "and": "y", "or": "o", "with": "con",
        "for": "para", "from": "de", "by": "por", "is": "es", "are": "son",
        "made": "hecho", "sold": "vendido", "packaged": "envasado",
        "no": "sin", "added": "añadido", "sugar": "azúcar",
        "naturally": "naturalmente", "flavored": "saborizado",
        "caffeine": "cafeína", "free": "libre", "recyclable": "reciclable",
        "aluminum": "aluminio", "cans": "latas", "can": "lata",
        "packs": "lotes", "pack": "lote", "bottles": "botellas",
        "bottle": "botella", "servings": "porciones", "serving": "porción",
        "brewed": "preparado", "hours": "horas", "hour": "hora",
        "minutes": "minutos", "minute": "minuto", "tea": "té",
        "evening": "nocturno", "sachets": "sobres", "box": "caja",
        "blend": "mezcla", "per": "cada", "bar": "barra", "bars": "barras",
        "fiber": "fibra", "whole": "integral", "grains": "granos",
        "grain": "grano", "sweetened": "endulzado", "dates": "dátiles",
        "artificial": "artificial", "colors": "colores", "color": "color",
        "review": "revise", "before": "antes", "consumption": "consumo",
        "allergen": "alérgeno", "information": "información",
        "package": "empaque", "label": "etiqueta", "safety": "seguridad",
        "data": "datos", "sheet": "hoja", "available": "disponible",
        "request": "solicitud", "install": "instale", "only": "solo",
        "local": "local", "codes": "códigos", "product": "producto",
        "quality": "calidad", "designed": "diseñado", "durable": "duradero",
        "lightweight": "ligero", "strong": "fuerte", "steep": "infusión",
    },
    Language.DE: {
        "the": "der", "a": "ein", "and": "und", "or": "oder", "with": "mit",
        "for": "für", "from": "von", "by": "durch", "is": "ist", "are": "sind",
        "made": "gemacht", "sold": "verkauft", "packaged": "verpackt",
        "no": "ohne", "added": "hinzugefügt", "sugar": "zucker",
        "naturally": "natürlich", "flavored": "aromatisiert",
        "caffeine": "koffein", "free": "frei", "recyclable": "recycelbar",
        "aluminum": "aluminium", "cans": "dosen", "can": "dose",
        "packs": "packungen", "pack": "packung", "bottles": "flaschen",
        "bottle": "flasche", "servings": "portionen", "serving": "portion",
        "brewed": "gebraut", "hours": "stunden", "hour": "stunde",
        "minutes": "minuten", "minute": "minute", "tea": "tee",
        "evening": "abend", "sachets": "beutel", "box": "schachtel",
        "blend": "mischung", "per": "pro", "bar": "riegel", "bars": "riegelstangen",
        "fiber": "ballaststoffe", "whole": "vollkorn", "grains": "körner",
        "grain": "korn", "sweetened": "gesüßt", "dates": "datteln",
        "artificial": "künstlich", "colors": "farben", "color": "farbe",
        "review": "prüfen", "before": "vor", "consumption": "verzehr",
        "allergen": "allergen", "information": "informationen",
        "package": "verpackung", "label": "etikett", "safety": "sicherheit",
        "data": "daten", "sheet": "blatt", "available": "verfügbar",
        "request": "anfrage", "install": "installieren", "only": "nur",
        "local": "lokal", "codes": "vorschriften", "product": "produkt",
        "quality": "qualität", "designed": "entworfen", "durable": "haltbar",
        "lightweight": "leicht", "strong": "stark", "steep": "ziehen",
    },
    Language.FR: {
        "the": "le", "a": "un", "and": "et", "or": "ou", "with": "avec",
        "for": "pour", "from": "de", "by": "par", "is": "est", "are": "sont",
        "made": "fabriqué", "sold": "vendu", "packaged": "emballé",
        "no": "sans", "added": "ajouté", "sugar": "sucre",
        "naturally": "naturellement", "flavored": "aromatisé",
        "caffeine": "caféine", "free": "libre", "recyclable": "recyclable",
        "aluminum": "aluminium", "cans": "boîtes", "can": "boîte",
        "packs": "paquets", "pack": "paquet", "bottles": "bouteilles",
        "bottle": "bouteille", "servings": "portions", "serving": "portion",
        "brewed": "infusé", "hours": "heures", "hour": "heure",
        "minutes": "minutes", "minute": "minute", "tea": "thé",
        "evening": "soir", "sachets": "sachets", "box": "coffret",
        "blend": "mélange", "per": "chaque", "bar": "barre", "bars": "barres",
        "fiber": "fibres", "whole": "complètes", "grains": "grains",
        "grain": "grain", "sweetened": "sucré", "dates": "dattes",
        "artificial": "artificiel", "colors": "couleurs", "color": "couleur",
        "review": "vérifier", "before": "avant", "consumption": "consommation",
        "allergen": "allergène", "information": "informations",
        "package": "emballage", "label": "étiquette", "safety": "sécurité",
        "data": "données", "sheet": "fiche", "available": "disponible",
        "request": "demande", "install": "installer", "only": "seulement",
        "local": "local", "codes": "règles", "product": "produit",
        "quality": "qualité", "designed": "conçu", "durable": "durable",
        "lightweight": "léger", "strong": "solide", "steep": "infuser",
    },
    Language.HI: {
        "the": "yeh", "a": "ek", "and": "aur", "or": "ya", "with": "kesaath",
        "for": "keliye", "from": "se", "by": "dwara", "is": "hai",
        "are": "hain", "made": "banaya", "sold": "becha", "packaged": "packaged",
        "no": "bina", "added": "joda", "sugar": "cheeni",
        "naturally": "prakritik", "flavored": "swad", "caffeine": "caffeine",
        "free": "mukt", "recyclable": "recycle", "aluminum": "aluminium",
        "cans": "cans", "can": "can", "packs": "packs", "pack": "pack",
        "bottles": "bottles", "bottle": "bottle", "servings": "servings",
        "serving": "serving", "brewed": "taiyar", "hours": "ghante",
        "hour": "ghanta", "minutes": "minutein", "minute": "minit",
        "tea": "chai", "evening": "shaam", "sachets": "sachets",
        "box": "dabba", "blend": "mishran", "per": "prati", "bar": "bar",
        "bars": "bars", "fiber": "fiber", "whole": "sabut",
        "grains": "anaj", "grain": "dana", "sweetened": "meetha",
        "dates": "khajoor", "artificial": "krtrim", "colors": "rangon",
        "color": "rang", "review": "dekhein", "before": "pehle",
        "consumption": "sevan", "allergen": "allergen",
        "information": "jankari", "package": "package", "label": "label",
        "safety": "suraksha", "data": "data", "sheet": "sheet",
        "available": "uplabdh", "request": "anusandhan", "install": "install",
        "only": "sirf", "local": "local", "codes": "niyam",
        "product": "utpad", "quality": "gunvatta", "designed": "design",
        "durable": "tikaau", "lightweight": "halka", "strong": "mazboot",
        "steep": "bhigo",
    },
}


class MockLLM:
    """Zero-key deterministic implementation of the LLM interface."""

    name = "mock"

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
        del brief, max_sentence_words
        stress = bool(metadata.get("stress_test"))
        safe = self._safe_content(fact_sheet, [] if stress else required_disclaimers)
        if not stress:
            return safe

        if vertical == Vertical.FOOD_BEVERAGE:
            unsafe = "Clinically proven to boost immunity by 42%."
        else:
            unsafe = "This patented design is guaranteed and 100% recyclable."
        body = f"{safe.body} {unsafe}".strip()
        return DraftResult(headline=safe.headline, body=body)

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
        del brief, draft, vertical, max_sentence_words
        safe = self._safe_content(fact_sheet, required_disclaimers)
        rules = sorted({violation.rule_id for violation in report.violations})
        notes = [
            "Rebuilt the draft from supplied facts only.",
            "Inserted required disclaimers verbatim.",
        ]
        return RevisionResult(
            headline=safe.headline,
            body=safe.body,
            notes=notes,
            addressed_rules=rules,
        )

    def translate(self, *, text: str, language: Language) -> str:
        return _pseudo_translate(text, language)

    def back_translate(self, *, text: str, language: Language) -> str:
        return _pseudo_back_translate(text, language)

    def judge(
        self,
        *,
        text: str,
        fact_sheet: FactSheet,
        rubric: list[dict[str, str]],
        report: GuardrailReport | None = None,
    ) -> JudgeResult:
        del text, fact_sheet
        if report is None:
            return JudgeResult(
                score=4.5,
                dimensions={item["id"]: 4.5 for item in rubric},
                rationale="No guardrail report was supplied; mock heuristic defaults to 4.5.",
                judge_mode="mock-heuristic",
            )
        errors = sum(1 for item in report.violations if item.severity.value == "error")
        warnings = len(report.violations) - errors
        score = max(1.0, min(5.0, 5.0 - 0.75 * errors - 0.1 * warnings))
        return JudgeResult(
            score=round(score, 1),
            dimensions={item["id"]: round(score, 1) for item in rubric},
            rationale=(
                "Mock heuristic derived from the final guardrail report "
                f"({errors} errors, {warnings} warnings). Not a live LLM judgment."
            ),
            judge_mode="mock-heuristic",
        )

    @staticmethod
    def _safe_content(
        fact_sheet: FactSheet, required_disclaimers: list[str]
    ) -> DraftResult:
        claim = fact_sheet.approved_claims[0] if fact_sheet.approved_claims else "Made with care"
        headline = f"{fact_sheet.product_name}: {claim}"
        sentences = [
            f"{fact_sheet.product_name} is made by {fact_sheet.brand}.",
            *[_sentence(fact) for fact in fact_sheet.facts[:3]],
            *[_sentence(item) for item in required_disclaimers],
        ]
        return DraftResult(headline=headline, body=" ".join(sentences))


def _sentence(value: str) -> str:
    clean = value.strip()
    return clean if clean.endswith((".", "!", "?")) else f"{clean}."


def _apply_glossary(text: str, mapping: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        translated = mapping.get(word.casefold())
        if not translated:
            return word
        if word.isupper():
            return translated.upper()
        if word[0].isupper():
            return translated[0].upper() + translated[1:]
        return translated

    return WORD_RE.sub(replace, text)


def _pseudo_translate(text: str, language: Language) -> str:
    mapping = GLOSSARIES[language]
    return f"[mock-{language.value}] {_apply_glossary(text, mapping)}"


def _pseudo_back_translate(text: str, language: Language) -> str:
    marker = re.compile(rf"^\[mock-{language.value}\]\s*", re.IGNORECASE)
    cleaned = marker.sub("", text)
    reverse = {value.casefold(): key for key, value in GLOSSARIES[language].items()}
    return _apply_glossary(cleaned, reverse)
