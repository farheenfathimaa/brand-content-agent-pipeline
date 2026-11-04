"""State-machine unit tests and pipeline transition-order tests."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.container import Container, build_container
from app.enums import BriefState
from app.errors import InvalidTransitionError, PipelineFailure
from app.llm.mock import MockLLM
from app.schemas import ApprovalRequest, BriefCreate
from app.state_machine import ALLOWED_TRANSITIONS, TERMINAL_STATES, StateMachine

S = BriefState


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (None, S.BRIEF),
        (S.BRIEF, S.DRAFTING),
        (S.DRAFTING, S.GUARDRAIL_CHECK),
        (S.GUARDRAIL_CHECK, S.REVISION),
        (S.GUARDRAIL_CHECK, S.PENDING_APPROVAL),
        (S.REVISION, S.GUARDRAIL_CHECK),
        (S.PENDING_APPROVAL, S.TRANSLATING),
        (S.PENDING_APPROVAL, S.REJECTED),
        (S.TRANSLATING, S.STAGED),
        (S.STAGED, S.PUBLISHED),
        (S.BRIEF, S.FAILED),
        (S.STAGED, S.FAILED),
    ],
)
def test_allowed_transitions(from_state, to_state) -> None:
    assert StateMachine.can_transition(from_state, to_state) is True
    StateMachine.assert_transition(from_state, to_state)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (None, S.DRAFTING),
        (S.BRIEF, S.PENDING_APPROVAL),
        (S.DRAFTING, S.PUBLISHED),
        (S.PENDING_APPROVAL, S.PUBLISHED),
        (S.PUBLISHED, S.BRIEF),
        (S.REJECTED, S.DRAFTING),
        (S.FAILED, S.BRIEF),
        (S.STAGED, S.TRANSLATING),
    ],
)
def test_disallowed_transitions(from_state, to_state) -> None:
    assert StateMachine.can_transition(from_state, to_state) is False
    with pytest.raises(InvalidTransitionError) as excinfo:
        StateMachine.assert_transition(from_state, to_state)
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "invalid_transition"
    assert excinfo.value.details["to_state"] == to_state.value


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == set()
        assert StateMachine.is_terminal(state) is True
        for target in BriefState:
            assert StateMachine.can_transition(state, target) is False
    assert StateMachine.is_terminal(S.PENDING_APPROVAL) is False


def test_clean_brief_emits_ordered_events(container: Container, clean_payload: dict) -> None:
    brief = container.service.create_brief(BriefCreate(**clean_payload))
    events = container.service.get_events(brief.id)

    assert [event.to_state for event in events] == [
        "brief",
        "drafting",
        "guardrail_check",
        "pending_approval",
    ]
    assert events[0].from_state is None
    assert events[0].actor == "api"
    assert events[1].actor == "system"
    assert events[2].actor == "guardrail-engine"
    for previous, current in zip(events, events[1:], strict=False):
        assert current.from_state == previous.to_state
        assert StateMachine.can_transition(
            BriefState(previous.to_state), BriefState(current.to_state)
        )
    assert brief.revision_count == 0
    assert brief.initial_guardrail is not None
    assert brief.initial_guardrail.passed is True


def test_stress_brief_revises_then_reaches_approval(
    container: Container, stress_payload: dict
) -> None:
    brief = container.service.create_brief(BriefCreate(**stress_payload))
    events = container.service.get_events(brief.id)
    states = [event.to_state for event in events]

    assert states == [
        "brief",
        "drafting",
        "guardrail_check",
        "revision",
        "guardrail_check",
        "pending_approval",
    ]
    assert brief.revision_count == 1
    assert brief.initial_guardrail is not None and brief.initial_guardrail.passed is False
    assert brief.guardrail is not None and brief.guardrail.passed is True
    assert brief.critique
    assert brief.critique[-1].addressed_rules


def test_approval_moves_to_staged_through_translating(
    container: Container, clean_payload: dict
) -> None:
    brief = container.service.create_brief(BriefCreate(**clean_payload))
    staged = container.service.approve(
        brief.id, ApprovalRequest(approver="qa@example.com", notes="ok")
    )
    assert staged.state == BriefState.STAGED
    events = container.service.get_events(brief.id)
    assert [event.to_state for event in events[-2:]] == ["translating", "staged"]
    assert events[-1].payload["translations"] == 4


def test_run_to_approval_rejects_a_non_brief_state(
    container: Container, clean_payload: dict
) -> None:
    brief = container.service.create_brief(BriefCreate(**clean_payload))
    with pytest.raises(InvalidTransitionError) as excinfo:
        container.service.run_to_approval(brief.id)
    assert excinfo.value.details["current_state"] == "pending_approval"


def test_pipeline_failure_is_persisted_and_exposed(tmp_path) -> None:
    class ExplodingLLM(MockLLM):
        name = "exploding"

        def draft(self, **kwargs):  # noqa: ANN003, ANN201
            raise RuntimeError("provider exploded")

    settings = Settings(
        _env_file=None,
        app_mode="mock",
        database_path=str(tmp_path / "boom.db"),
        webhook_url="",
        generate_design_assets=False,
    )
    container = build_container(settings, llm=ExplodingLLM())
    payload = BriefCreate(
        title="Failure path",
        vertical="food_beverage",
        brief="Write a launch post for Alder Grove Sparkling Citrus.",
        fact_sheet_id="ag-sparkling-citrus",
        languages=["hi"],
    )
    with pytest.raises(PipelineFailure) as excinfo:
        container.service.create_brief(payload)
    assert excinfo.value.status_code == 500

    summaries = container.service.list_briefs()
    assert len(summaries) == 1
    failed = container.service.get_brief(summaries[0].id)
    assert failed.state == BriefState.FAILED
    events = container.service.get_events(failed.id)
    assert events[-1].to_state == "failed"
    assert "provider exploded" in events[-1].reason
