"""Application configuration, loaded from environment variables or .env."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Defaults are deliberately safe and key-free: APP_MODE=mock means the whole
    pipeline runs with deterministic mock providers.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "brand-content-agent-pipeline"
    app_mode: Literal["mock", "live"] = "mock"
    log_level: str = "INFO"
    database_path: str = "data/pipeline.db"

    # Webhooks. If webhook_url is unset, deliveries are recorded as "skipped".
    # If webhook_url is set and webhook_secret is blank, an ephemeral secret is
    # generated at startup (deliveries are still signed, but the secret is not
    # stable across restarts). Set WEBHOOK_SECRET for stable local testing.
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 3
    webhook_retry_backoff_seconds: float = 0.15

    # Pipeline policy.
    max_revision_loops: int = 2
    translation_similarity_threshold: float = 0.72
    generate_design_assets: bool = True
    design_output_dir: str = "outputs/designs"

    # LLM providers. The mock client is used unless APP_MODE=live.
    llm_primary_model: str = "groq/llama-3.3-70b-versatile"
    llm_fallback_model: str = "gemini/gemini-2.0-flash"
    llm_anthropic_model: str = ""
    llm_temperature: float = 0.3
    llm_max_tokens: int = 1200
    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # Prompt library version overrides. Blank means the registry's active version.
    prompt_draft_version: str = ""
    prompt_critic_version: str = ""
    prompt_translation_version: str = ""
    prompt_judge_version: str = ""

    # Integrations.
    task_tracker: Literal["mock", "clickup", "github"] = "mock"
    clickup_api_token: str = ""
    clickup_list_id: str = ""
    clickup_base_url: str = "https://api.clickup.com/api/v2"
    github_token: str = ""
    github_repo: str = ""  # owner/name
    github_base_url: str = "https://api.github.com"

    design_provider: Literal["local", "canva"] = "local"
    canva_access_token: str = ""
    canva_brand_template_id: str = ""
    canva_base_url: str = "https://api.canva.com/rest"

    @property
    def is_mock(self) -> bool:
        return self.app_mode == "mock"

    def available_llm_keys(self) -> list[str]:
        keys: list[str] = []
        if self.groq_api_key:
            keys.append("groq")
        if self.gemini_api_key:
            keys.append("gemini")
        if self.anthropic_api_key and self.llm_anthropic_model:
            keys.append("anthropic")
        return keys

    def validate_live(self) -> None:
        """Fail fast with actionable messages when live mode is misconfigured."""
        if self.app_mode != "live":
            return
        if not self.available_llm_keys():
            raise ValueError(
                "APP_MODE=live requires at least one of GROQ_API_KEY, GEMINI_API_KEY, "
                "or (ANTHROPIC_API_KEY + LLM_ANTHROPIC_MODEL)."
            )
        if self.task_tracker == "clickup" and not (
            self.clickup_api_token and self.clickup_list_id
        ):
            raise ValueError(
                "TASK_TRACKER=clickup requires CLICKUP_API_TOKEN and CLICKUP_LIST_ID."
            )
        if self.task_tracker == "github" and not (self.github_token and self.github_repo):
            raise ValueError("TASK_TRACKER=github requires GITHUB_TOKEN and GITHUB_REPO.")
        if self.design_provider == "canva" and not (
            self.canva_access_token and self.canva_brand_template_id
        ):
            raise ValueError(
                "DESIGN_PROVIDER=canva requires CANVA_ACCESS_TOKEN and "
                "CANVA_BRAND_TEMPLATE_ID (Canva Autofill requires Canva Enterprise)."
            )
        if self.webhook_url and not self.webhook_secret:
            raise ValueError(
                "WEBHOOK_URL is set but WEBHOOK_SECRET is blank. Set a shared secret "
                "so receivers can verify HMAC signatures."
            )


def load_settings(**overrides: object) -> Settings:
    """Build settings with optional explicit overrides (handy for tests/eval)."""
    return Settings(**overrides)
