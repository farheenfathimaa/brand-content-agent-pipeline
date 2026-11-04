"""HMAC-signed webhook delivery with retries and an audit trail."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.db import Database

logger = logging.getLogger(__name__)

SIGNATURE_PREFIX = "v1="


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    """Sign `timestamp + '.' + body` with HMAC-SHA256."""
    message = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> bool:
    if not secret or not signature or not timestamp:
        return False
    try:
        event_time = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - event_time) > tolerance_seconds:
        return False
    expected = sign_payload(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


def build_envelope(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"evt_{event['id']}",
        "type": "brief.state_changed",
        "api_version": "1.0",
        "created_at": event["created_at"],
        "data": {
            "brief_id": event["brief_id"],
            "from_state": event["from_state"],
            "to_state": event["to_state"],
            "actor": event["actor"],
            "reason": event["reason"],
            "revision_count": event.get("payload", {}).get("revision_count"),
            "guardrails_passed": event.get("payload", {}).get("guardrails_passed"),
        },
    }


class WebhookDispatcher:
    """Never lets a webhook failure roll back a business transition."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.db = db
        self.settings = settings
        self._client = client
        self._sleep = sleep

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        envelope = build_envelope(event)
        body = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        url = self.settings.webhook_url.strip()
        if not url:
            record = {
                "event_id": event["id"],
                "brief_id": event["brief_id"],
                "url": "",
                "status": "skipped",
                "signature": "",
                "timestamp": "",
                "payload_json": body.decode("utf-8"),
                "status_code": None,
                "attempts": 0,
                "error": "WEBHOOK_URL is not configured",
            }
            self.db.add_webhook_delivery(record)
            return record

        timestamp = str(int(time.time()))
        signature = sign_payload(self.settings.webhook_secret, timestamp, body)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "brand-content-agent-pipeline/1.0",
            "X-Webhook-Event": envelope["type"],
            "X-Webhook-Id": envelope["id"],
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": signature,
        }

        attempts = 0
        status_code: int | None = None
        error = ""
        client = self._client or httpx.Client(timeout=self.settings.webhook_timeout_seconds)
        close_client = self._client is None
        try:
            while attempts < self.settings.webhook_max_attempts:
                attempts += 1
                try:
                    response = client.post(url, content=body, headers=headers)
                    status_code = response.status_code
                    if 200 <= response.status_code < 300:
                        record = {
                            "event_id": event["id"],
                            "brief_id": event["brief_id"],
                            "url": url,
                            "status": "delivered",
                            "signature": signature,
                            "timestamp": timestamp,
                            "payload_json": body.decode("utf-8"),
                            "status_code": status_code,
                            "attempts": attempts,
                            "error": "",
                        }
                        self.db.add_webhook_delivery(record)
                        return record
                    error = f"HTTP {response.status_code}"
                except httpx.HTTPError as exc:
                    error = f"{type(exc).__name__}: {exc}"
                if attempts < self.settings.webhook_max_attempts:
                    self._sleep(self.settings.webhook_retry_backoff_seconds * attempts)
        finally:
            if close_client:
                client.close()

        record = {
            "event_id": event["id"],
            "brief_id": event["brief_id"],
            "url": url,
            "status": "failed",
            "signature": signature,
            "timestamp": timestamp,
            "payload_json": body.decode("utf-8"),
            "status_code": status_code,
            "attempts": attempts,
            "error": error,
        }
        self.db.add_webhook_delivery(record)
        logger.warning("Webhook delivery failed for event %s: %s", event["id"], error)
        return record
