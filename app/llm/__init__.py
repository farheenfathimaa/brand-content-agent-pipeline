"""LLM factory. Mock is the default; live mode requires a provider key."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import DraftResult, LLMClient, RevisionResult
from app.llm.mock import MockLLM
from app.prompts import PromptLibrary

__all__ = ["DraftResult", "LLMClient", "RevisionResult", "build_llm"]


def build_llm(settings: Settings, prompts: PromptLibrary) -> LLMClient:
    if settings.is_mock:
        return MockLLM()
    from app.llm.litellm_client import LiteLLMClient

    overrides = {
        "draft": settings.prompt_draft_version,
        "critic": settings.prompt_critic_version,
        "translation": settings.prompt_translation_version,
        "back_translation": settings.prompt_translation_version,
        "judge": settings.prompt_judge_version,
    }
    return LiteLLMClient(settings, prompts, overrides)
