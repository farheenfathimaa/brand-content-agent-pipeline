"""Domain errors with HTTP-friendly status codes."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class InvalidTransitionError(AppError):
    status_code = 409
    code = "invalid_transition"


class GuardrailBlockedError(AppError):
    status_code = 409
    code = "guardrail_blocked"


class TranslationQualityError(AppError):
    status_code = 422
    code = "translation_quality_failed"


class LLMError(AppError):
    status_code = 502
    code = "llm_unavailable"


class PipelineFailure(AppError):
    status_code = 500
    code = "pipeline_failure"


class IntegrationError(AppError):
    status_code = 502
    code = "integration_error"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
