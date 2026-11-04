#!/usr/bin/env python3
"""Tiny local webhook receiver for demoing HMAC-signed transitions.

Usage:
  WEBHOOK_SECRET=<same value as the app> python scripts/webhook_listener.py

Runs on http://localhost:9000/hook by default. Set WEBHOOK_URL to that URL in
the app's environment (or .env) and transitions will be delivered here.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.webhooks import verify_signature  # noqa: E402

SECRET = os.environ.get("WEBHOOK_SECRET", "")
PORT = int(os.environ.get("WEBHOOK_PORT", "9000"))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        timestamp = self.headers.get("X-Webhook-Timestamp", "")
        signature = self.headers.get("X-Webhook-Signature", "")
        valid = verify_signature(SECRET, timestamp, body, signature)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body.decode("utf-8", errors="replace")}
        print(
            f"{'VALID  ' if valid else 'INVALID'} "
            f"{payload.get('type', '?')} {payload.get('data', {})}",
            flush=True,
        )
        self.send_response(200 if valid else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}' if valid else b'{"error": "bad signature"}')

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main() -> int:
    if not SECRET:
        print("WEBHOOK_SECRET is required.", file=sys.stderr)
        return 2
    print(f"Listening on http://localhost:{PORT}/hook")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
