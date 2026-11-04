# Runbook

Operational notes for the Brand Content Agent Pipeline. This is a single-process demonstration service: FastAPI + SQLite + an in-process webhook dispatcher. Treat it as an example, not a multi-tenant production deployment.

## 1. Prerequisites

- Python 3.11+ (CI uses 3.11)
- `make` for the convenience targets; direct commands are listed below
- Optional: Docker + Docker Compose
- Optional live credentials: Groq, Gemini, ClickUp, GitHub, Canva Enterprise

No credentials are required for `APP_MODE=mock`.

## 2. Configuration

Copy the template and edit only what you need:

```bash
cp .env.example .env
```

Pydantic Settings reads `.env` from the working directory. Environment variables override `.env`. Secrets belong in a secret manager or local `.env`; never commit them.

Live mode validates configuration at startup and exits with an actionable message when required values are missing. Mock mode ignores provider keys.

## 3. Start, stop, and verify

### Local

```bash
make dev-install
make test
make run
```

Direct equivalent:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f app
docker compose down
```

Data, designs, and reports are stored in named volumes:

- `brand-content-agent-pipeline_pipeline-data` → `/app/data`
- `brand-content-agent-pipeline_pipeline-outputs` → `/app/outputs`
- `brand-content-agent-pipeline_pipeline-reports` → `/app/reports`

Copy artifacts out with:

```bash
docker compose cp app:/app/reports/eval_report.md ./eval_report.md
docker compose cp app:/app/outputs/designs/. ./designs/
```

### Health checks

```bash
curl -sS http://localhost:8000/health | python -m json.tool
```

Expected in mock mode:

- `status: ok`
- `mode: mock`
- `llm_provider: mock`
- `task_tracker: mock`
- `design_provider: local`
- `webhook_configured: false` unless a URL is set
- prompt versions show `draft: v2`

The container health check calls the same endpoint. The first minute may show `starting` while dependencies import.

## 4. Normal workflow

1. `POST /api/v1/briefs` creates the brief and runs it to `pending_approval`.
2. Review `guardrail`, `initial_guardrail`, `critique`, and `revision_count` on `GET /api/v1/briefs/{id}`.
3. Approve or reject:
   - `POST /api/v1/briefs/{id}/approve` → translation, back-translation check, CMS staging.
   - `POST /api/v1/briefs/{id}/reject` → terminal `rejected`.
4. Publish only after staging: `POST /api/v1/briefs/{id}/publish`.
5. Preview documents at `GET /api/v1/cms/{doc_id}/preview`.

Every accepted transition appends an event and attempts a webhook. Query them at:

```bash
curl -sS http://localhost:8000/api/v1/briefs/BRIEF_ID/events
curl -sS 'http://localhost:8000/api/v1/webhook-deliveries?brief_id=BRIEF_ID'
```

## 5. State machine and recovery

| State | Meaning | Outgoing |
| --- | --- | --- |
| `brief` | Row created, not drafted | `drafting`, `failed` |
| `drafting` | LLM writing first draft | `guardrail_check`, `failed` |
| `guardrail_check` | Deterministic checks running | `revision`, `pending_approval`, `failed` |
| `revision` | Critic rewriting reported violations | `guardrail_check`, `failed` |
| `pending_approval` | Human decision required | `translating`, `rejected`, `failed` |
| `translating` | Translation + back-translation | `staged`, `failed` |
| `staged` | CMS documents staged | `published`, `failed` |
| `published` | Terminal success | none |
| `rejected` | Terminal human rejection | none |
| `failed` | Terminal provider/pipeline failure | none |

There is intentionally no retry endpoint. A failed brief is an audit record. To retry after fixing the cause, create a new brief; do not flip states in SQLite manually.

The only state-changing override is force approval from `pending_approval` when guardrail errors remain. It requires `force=true` **and** a non-empty `override_reason`:

```bash
curl -sS -X POST http://localhost:8000/api/v1/briefs/BRIEF_ID/approve \
  -H 'Content-Type: application/json' \
  -d '{
    "approver": "reviewer@example.com",
    "force": true,
    "override_reason": "Legal reviewed the exception; see ticket 123."
  }'
```

Force approval without a reason is HTTP 422. Ordinary approval while errors remain is HTTP 409. Both the approver and the reason are stored on the brief and in the transition event.

## 6. Webhooks

Configure a receiver and shared secret:

```dotenv
WEBHOOK_URL=https://receiver.example/hook
WEBHOOK_SECRET=<long-random-shared-secret>
```

Signature contract:

```text
signed_message = X-Webhook-Timestamp + "." + raw_request_body
X-Webhook-Signature = "v1=" + hex(HMAC_SHA256(WEBHOOK_SECRET, signed_message))
```

Receiver checklist:

1. Read the **raw** body before JSON parsing.
2. Reject timestamps outside an acceptable window (the included verifier defaults to 300 seconds).
3. Compare signatures in constant time.
4. Deduplicate on `X-Webhook-Id`; retries repeat the same event.
5. Return any 2xx to acknowledge. Everything else triggers retry with backoff.

Python verification using this repository:

```python
from app.webhooks import verify_signature

if not verify_signature(secret, timestamp, raw_body, signature):
    raise ValueError("bad webhook signature")
```

Local demo receiver:

```bash
WEBHOOK_SECRET=dev-secret python scripts/webhook_listener.py
# Listens on http://localhost:9000/hook
# Set WEBHOOK_URL and WEBHOOK_SECRET in the app's .env to the same values.
```

Delivery behavior:

- No `WEBHOOK_URL`: every event is recorded as `skipped`.
- Non-2xx or transport error: retried up to `WEBHOOK_MAX_ATTEMPTS` with linear backoff, then recorded as `failed`.
- Webhook failure never rolls back a content transition.
- Failed deliveries are **not** replayed automatically. Replay requires a separate job, which is out of scope.

## 7. Prompt changes and evals

Never edit a released prompt in place.

1. Add a new YAML file under the prompt family.
2. Register the version in `app/prompts/registry.yaml`.
3. Record it in `app/prompts/CHANGELOG.md`.
4. Run `make eval`.
5. Compare the new report with the previous committed report.
6. Only then change the active version.

Run the eval directly:

```bash
.venv/bin/python -m app.evals.runner --output-dir reports
```

Artifacts:

- `reports/eval_report.md` — human-readable metrics, prompt versions, per-brief rows, interpretation.
- `reports/eval_results.json` — machine-readable run metadata, metrics, rows, errors.

The command exits non-zero if any golden brief raises. It does not fail merely because a metric is poor; read the report.

**Mock-mode interpretation:** the numbers validate the harness and policies, not live model quality. The mock judge is a heuristic over guardrail results and the mock translator is a dictionary transform. Use live mode and human review before drawing quality conclusions.

## 8. Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Startup: `APP_MODE=live requires at least one of GROQ_API_KEY...` | Live mode without credentials | Set a key or switch to `APP_MODE=mock` |
| Startup: `WEBHOOK_URL is set but WEBHOOK_SECRET is blank` | Incomplete webhook config | Set a stable shared secret |
| `LLMError` / HTTP 502: all configured LLM providers failed | Bad key, quota, outage, model rename | Check provider status and key, verify model IDs, inspect logs |
| HTTP 409 `guardrail_blocked` | Final draft still has error-severity violations | Fix the content/rules, or force with a documented override reason |
| HTTP 422 `translation_quality_failed` | Similarity below threshold after retries | Inspect `details.failures`; improve translation prompt/model or review the source |
| HTTP 409 `invalid_transition` | Action is not legal for the current state | Fetch the brief and events; do not mutate SQLite |
| Task tracker errors in logs but pipeline continues | Tracker is best-effort by design | Fix credentials/API access separately; content is not blocked |
| `design_asset.status == "failed"` but content staged | Design is best-effort | Check logs; install Chromium or review Canva configuration |
| Webhook rows are `skipped` | `WEBHOOK_URL` blank | Expected for local demo; set URL + secret to deliver |
| Webhook rows are `failed` | Receiver down, non-2xx, stale secret, network egress blocked | Check `error`, receiver logs, and shared secret; replay manually if needed |
| Canva HTTP error | Missing Enterprise plan, scope, template, or field mismatch | Validate against current Canva docs; the adapter is not live-tested |
| `Autoincrement`/SQLite locked under load | Single-node SQLite contention | Not supported for concurrent scale; use a real database/queue |

Raise log verbosity with `LOG_LEVEL=DEBUG` and restart.

## 9. Database operations

SQLite runs in WAL mode. For a consistent backup, use SQLite's backup API rather than copying a live file:

```bash
python - <<'PY'
import sqlite3
src = sqlite3.connect("data/pipeline.db")
dst = sqlite3.connect("backup-pipeline.db")
with dst:
    src.backup(dst)
src.close()
dst.close()
PY
```

Restore:

1. Stop the service.
2. Move the current database and any `-wal`/`-shm` files aside.
3. Place the backup at `DATABASE_PATH`.
4. Start the service and check `/health` plus `GET /api/v1/briefs`.

Reset a demo environment:

```bash
docker compose down -v          # removes named volumes
# or, locally:
rm -f data/pipeline.db data/pipeline.db-wal data/pipeline.db-shm
rm -rf outputs/designs/*
```

## 10. Secret rotation and incident response

If a provider key is exposed:

1. Revoke/rotate it at the provider immediately. Treat it as compromised even if it only appeared in chat or logs.
2. Replace it in the secret store or `.env`, not in Git.
3. Restart the service and verify `/health` and a test brief.
4. Search the repository and CI logs for the leaked value before pushing.

For webhook secret rotation, coordinate with the receiver: signatures fail during the window when the two sides disagree. Rotate the receiver and app together, then confirm new deliveries are `delivered`. Delivery rows store signatures, not the secret.

## 11. Production hardening checklist

Before exposing this outside a local demo:

- Put authentication, authorization, and rate limits in front of the API. Approval endpoints currently trust the caller-supplied approver string.
- Terminate TLS and restrict egress to configured providers.
- Replace SQLite and the in-process dispatcher with a transactional database and a durable outbox/queue.
- Add idempotency keys and receiver replay tooling.
- Move secrets to a managed secret store and enable audit logging.
- Validate every live adapter (ClickUp, GitHub, Canva) against real accounts in a sandbox.
- Add semantic translation QA and human localization review.
- Expand guardrails: spelled-out numbers, claim taxonomies, locale-specific regulations, and legal review.
- Add monitoring for failed webhooks, stuck briefs, provider latency, and eval regressions.
- Re-run the eval against live providers and record the prompt/model versions with each release.
