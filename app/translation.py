"""Translation plus lexical back-translation similarity checks."""

from __future__ import annotations

import difflib
import re

from app.enums import Language
from app.errors import TranslationQualityError
from app.llm.base import LLMClient
from app.schemas import TranslationResult


def similarity_score(source: str, back_translation: str) -> float:
    """Lexical similarity, not a semantic guarantee. Documented as such."""

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.casefold()).strip()

    return round(
        difflib.SequenceMatcher(None, normalize(source), normalize(back_translation)).ratio(),
        4,
    )


class TranslationService:
    def __init__(
        self,
        llm: LLMClient,
        *,
        threshold: float = 0.72,
        max_attempts: int = 2,
    ) -> None:
        self.llm = llm
        self.threshold = threshold
        self.max_attempts = max(1, max_attempts)

    def translate_all(
        self, text: str, languages: list[Language]
    ) -> list[TranslationResult]:
        results: list[TranslationResult] = []
        failures: list[dict[str, object]] = []
        for language in languages:
            try:
                results.append(self.translate_one(text, language))
            except TranslationQualityError as exc:
                failures.append(
                    {
                        "language": language.value,
                        "similarity": exc.details.get("similarity"),
                        "threshold": self.threshold,
                    }
                )
        if failures:
            raise TranslationQualityError(
                "Back-translation similarity fell below the threshold for "
                f"{len(failures)} language(s).",
                details={"threshold": self.threshold, "failures": failures},
            )
        return results

    def translate_one(self, text: str, language: Language) -> TranslationResult:
        last_similarity = 0.0
        last_back_translation = ""
        for attempt in range(1, self.max_attempts + 1):
            translated = self.llm.translate(text=text, language=language)
            back = self.llm.back_translate(text=translated, language=language)
            last_similarity = similarity_score(text, back)
            last_back_translation = back
            if last_similarity >= self.threshold:
                return TranslationResult(
                    language=language,
                    text=translated,
                    back_translation=back,
                    similarity=last_similarity,
                    attempt=attempt,
                )
        raise TranslationQualityError(
            f"Back-translation similarity for {language.value} was {last_similarity:.3f}, "
            f"below the {self.threshold:.2f} threshold after {self.max_attempts} attempt(s).",
            details={
                "language": language.value,
                "similarity": last_similarity,
                "threshold": self.threshold,
                "back_translation": last_back_translation,
                "attempts": self.max_attempts,
            },
        )
