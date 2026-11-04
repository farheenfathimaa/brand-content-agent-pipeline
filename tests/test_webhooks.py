"""Webhook signing, verification, delivery, retry, and audit tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest

from app.config import Settings
from app.db import Database
from app.webhooks import (
    SIGNATURE_PREFIX,
    WebhookDispatcher,
    build_envelope,
    sign_payload,
    verify_signature,
)


def _event(event_id: int = 1, **overrides) -> dict:
    event = {
        "id": event_id,
        "brief_id": "brf_test123",
        "from_state": "drafting",
        "to_state": "guardrail_check",
        "actor": "guardrail-engine",
        "reason": "Initial guardrail check.",
        "payload": {"guardrails_passed": True, "revision_count": 0},
        "created_at": "2026-11-03T12:00:00+00:00",
    }
    event.update(overrides)
    return event


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(tmp_path / "webhooks.db")
    database.init_schema()
    return database


@pytest.fixture
def webhook_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode="mock",
        database_path=str(tmp_path / "webhooks.db"),
        webhook_url="",
        webhook_secret="shared-secret",
        generate_design_assets=False,
    )


def test_sign_payload_matches_documented_hmac_construction() -> None:
    timestamp = "1762171200"
    body = b'{"hello":"world"}'
    expected = hmac.new(
        b"shared-secret",
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    assert sign_payload("shared-secret", timestamp, body) == f"v1={expected}"
    assert sign_payload("shared-secret", timestamp, body).startswith(SIGNATURE_PREFIX)


def test_verify_signature_accepts_valid_payload() -> None:
    timestamp = str(int(time.time()))
    body = b'{"type":"brief.state_changed"}'
    signature = sign_payload("shared-secret", timestamp, body)
    assert verify_signature("shared-secret", timestamp, body, signature) is True


@pytest.mark.parametrize(
    ("secret", "timestamp", "body", "signature", "now"),
    [
        ("wrong-secret", "1762171200", b"{}", "v1=deadbeef", 1762171200),
        ("shared-secret", "1762171200", b'{"tampered":true}', "v1=deadbeef", 1762171200),
        ("shared-secret", "", b"{}", "v1=deadbeef", 1762171200),
        ("shared-secret", "not-a-number", b"{}", "v1=deadbeef", 1762171200),
        ("", "1762171200", b"{}", "v1=deadbeef", 1762171200),
        ("shared-secret", "1762171200", b"{}", "", 1762171200),
    ],
)
def test_verify_signature_rejects_bad_inputs(
    secret, timestamp, body, signature, now
) -> None:
    assert (
        verify_signature(secret, timestamp, body, signature, now=now)
        is False
    )


def test_verify_signature_rejects_stale_timestamp() -> None:
    timestamp = "1762171200"
    body = b'{"event":1}'
    signature = sign_payload("shared-secret", timestamp, body)
    assert (
        verify_signature(
            "shared-secret", timestamp, body, signature, tolerance_seconds=300, now=1762171200
        )
        is True
    )
    assert (
        verify_signature(
            "shared-secret", timestamp, body, signature, tolerance_seconds=300, now=1762171800
        )
        is False
    )


def test_envelope_shape() -> None:
    envelope = build_envelope(_event())
    assert envelope["id"] == "evt_1"
    assert envelope["type"] == "brief.state_changed"
    assert envelope["api_version"] == "1.0"
    assert envelope["data"] == {
        "brief_id": "brf_test123",
        "from_state": "drafting",
        "to_state": "guardrail_check",
        "actor": "guardrail-engine",
        "reason": "Initial guardrail check.",
        "revision_count": 0,
        "guardrails_passed": True,
    }


def test_dispatcher_records_skipped_when_no_url(db: Database, webhook_settings: Settings) -> None:
    dispatcher = WebhookDispatcher(db, webhook_settings, sleep=lambda _: None)
    record = dispatcher.publish(_event())

    assert record["status"] == "skipped"
    assert record["attempts"] == 0
    assert record["url"] == ""
    stored = db.list_webhook_deliveries(brief_id="brf_test123")
    assert len(stored) == 1
    assert stored[0]["status"] == "skipped"
    assert stored[0]["payload"]["type"] == "brief.state_changed"


def test_dispatcher_delivers_signed_payload(db: Database, webhook_settings: Settings) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"received": True})

    settings = webhook_settings.model_copy(
        update={"webhook_url": "https://receiver.example/hook"}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    dispatcher = WebhookDispatcher(db, settings, client=client, sleep=lambda _: None)
    record = dispatcher.publish(_event())

    assert record["status"] == "delivered"
    assert record["attempts"] == 1
    assert record["status_code"] == 200
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://receiver.example/hook"
    assert request.headers["X-Webhook-Event"] == "brief.state_changed"
    assert request.headers["X-Webhook-Id"] == "evt_1"
    assert request.headers["X-Webhook-Signature"].startswith("v1=")

    timestamp = request.headers["X-Webhook-Timestamp"]
    signature = request.headers["X-Webhook-Signature"]
    assert verify_signature(
        "shared-secret", timestamp, request.content, signature, now=float(timestamp)
    )
    payload = json.loads(request.content)
    assert payload["data"]["to_state"] == "guardrail_check"
    assert payload["data"]["guardrails_passed"] is True
    client.close()


def test_dispatcher_retries_then_succeeds(db: Database, webhook_settings: Settings) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, text="try later")
        return httpx.Response(201, json={"ok": True})

    settings = webhook_settings.model_copy(
        update={
            "webhook_url": "https://receiver.example/hook",
            "webhook_max_attempts": 3,
            "webhook_retry_backoff_seconds": 0.01,
        }
    )
    dispatcher = WebhookDispatcher(
        db,
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    record = dispatcher.publish(_event())

    assert record["status"] == "delivered"
    assert record["attempts"] == 3
    assert calls["count"] == 3
    assert sleeps == [0.01, 0.02]


def test_dispatcher_records_terminal_http_failure(db: Database, webhook_settings: Settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500, json={"error": "boom"})

    settings = webhook_settings.model_copy(
        update={
            "webhook_url": "https://receiver.example/hook",
            "webhook_max_attempts": 3,
            "webhook_retry_backoff_seconds": 0.0,
        }
    )
    dispatcher = WebhookDispatcher(
        db,
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    record = dispatcher.publish(_event())

    assert record["status"] == "failed"
    assert record["attempts"] == 3
    assert record["status_code"] == 500
    assert record["error"] == "HTTP 500"
    assert calls["count"] == 3
    stored = db.list_webhook_deliveries(brief_id="brf_test123")
    assert stored[-1]["status"] == "failed"


def test_dispatcher_records_transport_exception(db: Database, webhook_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    settings = webhook_settings.model_copy(
        update={
            "webhook_url": "https://receiver.example/hook",
            "webhook_max_attempts": 2,
            "webhook_retry_backoff_seconds": 0.0,
        }
    )
    dispatcher = WebhookDispatcher(
        db,
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    record = dispatcher.publish(_event())

    assert record["status"] == "failed"
    assert record["attempts"] == 2
    assert record["status_code"] is None
    assert "ConnectError" in record["error"]


def test_delivery_failure_does_not_raise(db: Database, webhook_settings: Settings) -> None:
    settings = webhook_settings.model_copy(
        update={"webhook_url": "https://receiver.example/hook"}
    )
    dispatcher = WebhookDispatcher(
        db,
        settings,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(502, text="bad gateway")
            )
        ),
        sleep=lambda _: None,
    )
    # Business transitions must not fail because a receiver is down.
    record = dispatcher.publish(_event())
    assert record["status"] == "failed"
