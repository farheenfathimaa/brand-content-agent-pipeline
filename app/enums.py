"""Shared enumerations for verticals, languages, and brief states."""

from __future__ import annotations

from enum import Enum


class Vertical(str, Enum):
    FOOD_BEVERAGE = "food_beverage"
    MANUFACTURING = "manufacturing"


class Language(str, Enum):
    HI = "hi"
    ES = "es"
    DE = "de"
    FR = "fr"


class BriefState(str, Enum):
    BRIEF = "brief"
    DRAFTING = "drafting"
    GUARDRAIL_CHECK = "guardrail_check"
    REVISION = "revision"
    PENDING_APPROVAL = "pending_approval"
    TRANSLATING = "translating"
    STAGED = "staged"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ViolationCategory(str, Enum):
    BANNED_CLAIM = "banned_claim"
    REQUIRED_DISCLAIMER = "required_disclaimer"
    BRAND_VOICE = "brand_voice"
    UNSUPPORTED_CLAIM = "unsupported_claim"
