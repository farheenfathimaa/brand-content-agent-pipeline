"""End-to-end API tests through FastAPI's TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

API = "/api/v1"


def _create(client: TestClient, payload: dict) -> dict:
    response = client.post(f"{API}/briefs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_root_and_health(client: TestClient) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["mode"] == "mock"
    assert root.json()["api"] == "/api/v1"

    health = client.get(f"{API}/health")
    assert health.status_code == 200
    data = health.json()
    assert data["status"] == "ok"
    assert data["mode"] == "mock"
    assert data["llm_provider"] == "mock"
    assert data["task_tracker"] == "mock"
    assert data["design_provider"] == "local"
    assert data["webhook_configured"] is False
    assert data["prompt_versions"]["draft"] == "v2"
    assert data["time"]


def test_clean_brief_reaches_pending_approval(client: TestClient, clean_payload: dict) -> None:
    data = _create(client, clean_payload)

    assert data["id"].startswith("brf_")
    assert data["state"] == "pending_approval"
    assert data["vertical"] == "food_beverage"
    assert data["revision_count"] == 0
    assert data["guardrail"]["passed"] is True
    assert data["initial_guardrail"]["passed"] is True
    assert data["task_ref"]["provider"] == "mock"
    assert data["task_ref"]["external_id"].startswith("mock-")
    assert data["headline"]
    assert "Allergen information" in data["body"]
    assert data["translations"] == []
    assert data["cms_documents"] == []

    events = client.get(f"{API}/briefs/{data['id']}/events").json()
    assert [event["to_state"] for event in events] == [
        "brief",
        "drafting",
        "guardrail_check",
        "pending_approval",
    ]

    deliveries = client.get(
        f"{API}/webhook-deliveries", params={"brief_id": data["id"]}
    ).json()["deliveries"]
    assert len(deliveries) == 4
    assert {item["status"] for item in deliveries} == {"skipped"}
    assert deliveries[0]["payload"]["type"] == "brief.state_changed"
    assert deliveries[0]["payload"]["data"]["to_state"] == "brief"


def test_approval_translates_stages_and_publishes(
    client: TestClient, clean_payload: dict
) -> None:
    brief = _create(client, clean_payload)
    brief_id = brief["id"]

    approved = client.post(
        f"{API}/briefs/{brief_id}/approve",
        json={"approver": "reviewer@example.com", "notes": "Ship it."},
    )
    assert approved.status_code == 200, approved.text
    staged = approved.json()
    assert staged["state"] == "staged"
    assert staged["approver"] == "reviewer@example.com"
    assert staged["override_reason"] == ""
    assert len(staged["translations"]) == 4
    assert {item["language"] for item in staged["translations"]} == {
        "hi",
        "es",
        "de",
        "fr",
    }
    assert all(item["similarity"] >= 0.72 for item in staged["translations"])
    assert all(item["back_translation"] for item in staged["translations"])
    assert len(staged["cms_documents"]) == 5
    assert {doc["status"] for doc in staged["cms_documents"]} == {"staged"}
    assert {doc["language"] for doc in staged["cms_documents"]} == {
        "en",
        "hi",
        "es",
        "de",
        "fr",
    }

    translations = client.get(f"{API}/briefs/{brief_id}/translations")
    assert translations.status_code == 200
    assert len(translations.json()) == 4

    published = client.post(
        f"{API}/briefs/{brief_id}/publish", json={"actor": "publisher@example.com"}
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["state"] == "published"
    assert {doc["status"] for doc in body["cms_documents"]} == {"published"}

    cms = client.get(f"{API}/cms", params={"brief_id": brief_id, "status": "published"})
    assert cms.status_code == 200
    docs = cms.json()
    assert len(docs) == 5

    doc = docs[0]
    fetched = client.get(f"{API}/cms/{doc['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == doc["id"]
    preview = client.get(f"{API}/cms/{doc['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert "Fictional brand demo" in preview.text
    assert "published" in preview.text


def test_stress_brief_recovers_after_critic_revision(
    client: TestClient, stress_payload: dict
) -> None:
    brief = _create(client, stress_payload)
    brief_id = brief["id"]
    assert brief["state"] == "pending_approval"
    assert brief["revision_count"] == 1
    assert brief["initial_guardrail"]["passed"] is False
    assert brief["guardrail"]["passed"] is True
    assert brief["critique"]

    approved = client.post(
        f"{API}/briefs/{brief_id}/approve",
        json={"approver": "reviewer@example.com"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "staged"


def test_force_approval_requires_override_reason(
    container, clean_payload: dict
) -> None:
    from app.errors import GuardrailBlockedError, ValidationError
    from app.schemas import (
        ApprovalRequest,
        BriefCreate,
        GuardrailReport,
        Violation,
    )

    brief = container.service.create_brief(BriefCreate(**clean_payload))
    # Simulate a brief that still has blocking errors at approval time.
    failing = GuardrailReport(
        passed=False,
        violations=[
            Violation(
                rule_id="test-blocker",
                category="banned_claim",
                severity="error",
                message="Synthetic blocker for the override test.",
            )
        ],
        checks={"banned_claims": False},
        pack_id="food_beverage",
        pack_version="1.0.0",
    )
    container.db.update_brief_fields(
        brief.id, {"guardrail_json": failing.model_dump(mode="json")}
    )

    with pytest.raises(GuardrailBlockedError) as blocked:
        container.service.approve(brief.id, ApprovalRequest(approver="reviewer@example.com"))
    assert blocked.value.status_code == 409

    with pytest.raises(ValidationError) as missing_reason:
        container.service.approve(
            brief.id,
            ApprovalRequest(approver="reviewer@example.com", force=True),
        )
    assert missing_reason.value.status_code == 422

    forced = container.service.approve(
        brief.id,
        ApprovalRequest(
            approver="reviewer@example.com",
            force=True,
            override_reason="Legal approved the exception.",
        ),
    )
    assert forced.state.value == "staged"
    assert forced.override_reason == "Legal approved the exception."


def test_reject_is_terminal(client: TestClient, clean_payload: dict) -> None:
    brief = _create(client, clean_payload)
    brief_id = brief["id"]

    rejected = client.post(
        f"{API}/briefs/{brief_id}/reject",
        json={"actor": "reviewer@example.com", "reason": "Off strategy."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"

    approve_again = client.post(
        f"{API}/briefs/{brief_id}/approve",
        json={"approver": "reviewer@example.com"},
    )
    assert approve_again.status_code == 409
    assert approve_again.json()["error"]["code"] == "invalid_transition"

    reject_again = client.post(
        f"{API}/briefs/{brief_id}/reject",
        json={"actor": "reviewer@example.com", "reason": "Still no."},
    )
    assert reject_again.status_code == 409


def test_publish_before_staging_is_rejected(client: TestClient, clean_payload: dict) -> None:
    brief = _create(client, clean_payload)
    response = client.post(f"{API}/briefs/{brief['id']}/publish", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


def test_list_briefs_and_prompts(client: TestClient, clean_payload: dict) -> None:
    created = _create(client, clean_payload)

    listing = client.get(f"{API}/briefs", params={"limit": 10})
    assert listing.status_code == 200
    summaries = listing.json()
    assert len(summaries) == 1
    assert summaries[0]["id"] == created["id"]
    assert summaries[0]["passed_guardrails"] is True

    prompts = client.get(f"{API}/prompts")
    assert prompts.status_code == 200
    data = {item["name"]: item for item in prompts.json()}
    assert set(data) == {"draft", "critic", "translation", "back_translation", "judge"}
    assert data["draft"]["active_version"] == "v2"
    assert data["draft"]["versions"] == ["v1", "v2"]
    assert data["draft"]["changelog"]


@pytest.mark.parametrize(
    ("payload_update", "status", "code"),
    [
        ({"fact_sheet_id": "does-not-exist"}, 404, "not_found"),
        ({"vertical": "manufacturing"}, 422, "validation_error"),
        ({"languages": []}, 422, None),
        ({"brief": "short"}, 422, None),
        ({"title": "  "}, 422, None),
    ],
)
def test_create_brief_validation(
    client: TestClient,
    clean_payload: dict,
    payload_update: dict,
    status: int,
    code: str | None,
) -> None:
    payload = {**clean_payload, **payload_update}
    response = client.post(f"{API}/briefs", json=payload)
    assert response.status_code == status
    if code:
        assert response.json()["error"]["code"] == code


def test_unknown_resources_return_404(client: TestClient) -> None:
    for path in [
        f"{API}/briefs/brf_missing",
        f"{API}/briefs/brf_missing/events",
        f"{API}/briefs/brf_missing/translations",
        f"{API}/cms/cms_missing",
        f"{API}/cms/cms_missing/preview",
    ]:
        response = client.get(path)
        assert response.status_code == 404, path
        if not path.endswith("/preview"):
            assert response.json()["error"]["code"] == "not_found"


def test_approve_unknown_brief_returns_404(client: TestClient) -> None:
    response = client.post(
        f"{API}/briefs/brf_missing/approve", json={"approver": "someone"}
    )
    assert response.status_code == 404


def test_cms_status_filter_accepts_only_known_values(
    client: TestClient, clean_payload: dict
) -> None:
    brief = _create(client, clean_payload)
    client.post(f"{API}/briefs/{brief['id']}/approve", json={"approver": "reviewer"})

    staged = client.get(f"{API}/cms", params={"status": "staged"})
    assert staged.status_code == 200
    assert len(staged.json()) == 5

    bogus = client.get(f"{API}/cms", params={"status": "archived"})
    assert bogus.status_code == 422
