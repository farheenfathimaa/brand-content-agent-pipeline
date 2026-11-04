"""Explicit brief state machine.

Transitions are declared once and enforced in one place. Terminal states have
no outgoing transitions. Every accepted transition is persisted and webhooked.
"""

from __future__ import annotations

from app.enums import BriefState
from app.errors import InvalidTransitionError

S = BriefState

# None represents "before the brief row existed".
ALLOWED_TRANSITIONS: dict[BriefState | None, set[BriefState]] = {
    None: {S.BRIEF},
    S.BRIEF: {S.DRAFTING, S.FAILED},
    S.DRAFTING: {S.GUARDRAIL_CHECK, S.FAILED},
    S.GUARDRAIL_CHECK: {S.REVISION, S.PENDING_APPROVAL, S.FAILED},
    S.REVISION: {S.GUARDRAIL_CHECK, S.FAILED},
    S.PENDING_APPROVAL: {S.TRANSLATING, S.REJECTED, S.FAILED},
    S.TRANSLATING: {S.STAGED, S.FAILED},
    S.STAGED: {S.PUBLISHED, S.FAILED},
    S.PUBLISHED: set(),
    S.REJECTED: set(),
    S.FAILED: set(),
}

TERMINAL_STATES = {S.PUBLISHED, S.REJECTED, S.FAILED}


class StateMachine:
    @staticmethod
    def can_transition(from_state: BriefState | None, to_state: BriefState) -> bool:
        return to_state in ALLOWED_TRANSITIONS.get(from_state, set())

    @classmethod
    def assert_transition(
        cls, from_state: BriefState | None, to_state: BriefState
    ) -> None:
        if not cls.can_transition(from_state, to_state):
            raise InvalidTransitionError(
                f"Cannot move brief from '{from_state.value if from_state else 'none'}' "
                f"to '{to_state.value}'.",
                details={
                    "from_state": from_state.value if from_state else None,
                    "to_state": to_state.value,
                    "allowed": sorted(
                        state.value for state in ALLOWED_TRANSITIONS.get(from_state, set())
                    ),
                },
            )

    @staticmethod
    def is_terminal(state: BriefState) -> bool:
        return state in TERMINAL_STATES
