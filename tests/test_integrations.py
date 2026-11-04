"""Adapter tests with mocked HTTP; no external network calls are made."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db import Database
from app.errors import IntegrationError
from app.integrations.cms import LocalCMS
from app.integrations.design import CanvaAutofillProvider, LocalHTMLDesignProvider
from app.integrations.task_tracker import (
    ClickUpTaskTracker,
    GitHubIssuesTaskTracker,
    MockTaskTracker,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mock_task_tracker_round_trip() -> None:
    tracker = MockTaskTracker()
    ref = tracker.create_task(
        title="Draft post",
        description="Write it",
        brief_id="brf_1",
        metadata={"x": 1},
    )
    assert ref.provider == "mock"
    assert ref.external_id == "mock-1"
    assert ref.url == "mock://task/mock-1"
    assert tracker.tasks["mock-1"]["status"] == "open"
    tracker.update_status(ref, "pending_approval")
    assert tracker.tasks["mock-1"]["status"] == "pending_approval"


def test_clickup_adapter_create_and_update() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "cu-123", "url": "https://clickup/t/cu-123"})
        return httpx.Response(200, json={"id": "cu-123"})

    tracker = ClickUpTaskTracker(
        api_token="test-token",
        list_id="list-42",
        base_url="https://api.clickup.com/api/v2",
        client=_client(handler),
    )
    ref = tracker.create_task(
        title="Draft",
        description="Copy",
        brief_id="brf_1",
        metadata={},
    )
    assert ref.external_id == "cu-123"
    assert ref.url == "https://clickup/t/cu-123"

    tracker.update_status(ref, "pending_approval")

    create, update = requests
    assert str(create.url) == "https://api.clickup.com/api/v2/list/list-42/task"
    assert create.headers["Authorization"] == "test-token"
    assert json.loads(create.content)["name"] == "Draft"
    assert str(update.url) == "https://api.clickup.com/api/v2/task/cu-123"
    assert update.method == "PUT"
    assert json.loads(update.content) == {"status": "pending_approval"}


def test_clickup_adapter_reports_failures() -> None:
    tracker = ClickUpTaskTracker(
        api_token="test-token",
        list_id="list-42",
        client=_client(lambda request: httpx.Response(401, json={"err": "no"})),
    )
    with pytest.raises(IntegrationError) as excinfo:
        tracker.create_task(title="x", description="y", brief_id="brf", metadata={})
    assert excinfo.value.status_code == 502
    assert excinfo.value.code == "integration_error"
    assert excinfo.value.details["provider"] == "clickup"


def test_github_adapter_create_and_update() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "number": 7,
                    "html_url": "https://github.com/example/repo/issues/7",
                },
            )
        return httpx.Response(200, json={"number": 7, "state": "closed"})

    tracker = GitHubIssuesTaskTracker(
        token="gh-token",
        repo="example/repo",
        base_url="https://api.github.com",
        client=_client(handler),
    )
    ref = tracker.create_task(
        title="Draft", description="Copy", brief_id="brf_1", metadata={}
    )
    assert ref.external_id == "7"
    tracker.update_status(ref, "published")

    create, update = requests
    assert str(create.url) == "https://api.github.com/repos/example/repo/issues"
    assert create.headers["Authorization"] == "Bearer gh-token"
    create_body = json.loads(create.content)
    assert create_body["labels"] == ["content-pipeline", "status:brief"]
    assert "`brf_1`" in create_body["body"]
    assert update.method == "PATCH"
    update_body = json.loads(update.content)
    assert update_body["state"] == "closed"
    assert update_body["labels"] == ["content-pipeline", "status:published"]


def test_github_adapter_reports_update_failure() -> None:
    from app.schemas import TaskRef

    tracker = GitHubIssuesTaskTracker(
        token="gh-token",
        repo="example/repo",
        client=_client(lambda request: httpx.Response(500, json={})),
    )
    with pytest.raises(IntegrationError):
        tracker.update_status(TaskRef(provider="github", external_id="7"), "staged")


def test_local_design_provider_writes_html_and_png_fallback(tmp_path) -> None:
    provider = LocalHTMLDesignProvider(tmp_path / "designs", force_fallback=True)
    asset = provider.create_card(
        title="Alder Grove <Sparkling> Citrus",
        body="No added sugar.\nCaffeine-free.",
        language="en",
        brief_id="brf_test",
    )

    assert asset.provider == "local"
    assert asset.kind == "png"
    assert asset.status == "ready"
    assert asset.rendered_with == "pillow-fallback"
    assert "Pillow fallback" in asset.note
    html_path = tmp_path / "designs" / "brf_test-en.html"
    png_path = tmp_path / "designs" / "brf_test-en.png"
    assert asset.path == str(png_path)
    assert html_path.exists() and png_path.exists()
    markup = html_path.read_text(encoding="utf-8")
    assert "&lt;Sparkling&gt;" in markup
    assert markup.count("<p>") == 2
    assert png_path.read_bytes().startswith(b"\x89PNG")


def test_canva_adapter_is_explicitly_mocked_and_not_live_tested() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"job": {"id": "job-1", "status": "in_progress"}}
        )

    provider = CanvaAutofillProvider(
        access_token="canva-token",
        brand_template_id="template-9",
        client=_client(handler),
    )
    asset = provider.create_card(
        title="Headline", body="Body", language="en", brief_id="brf_1"
    )

    assert asset.provider == "canva"
    assert asset.kind == "canva-job"
    assert asset.external_id == "job-1"
    assert asset.status == "in_progress"
    assert "NOT LIVE-TESTED" in asset.note
    assert "Canva Enterprise" in asset.note

    request = requests[0]
    assert str(request.url) == "https://api.canva.com/rest/v1/autofills"
    assert request.headers["Authorization"] == "Bearer canva-token"
    payload = json.loads(request.content)
    assert payload["brand_template_id"] == "template-9"
    assert payload["data"]["headline"] == {"type": "text", "text": "Headline"}
    assert payload["data"]["body"] == {"type": "text", "text": "Body"}


def test_canva_adapter_reports_http_failure() -> None:
    provider = CanvaAutofillProvider(
        access_token="canva-token",
        brand_template_id="template-9",
        client=_client(lambda request: httpx.Response(403, json={"error": "plan"})),
    )
    with pytest.raises(IntegrationError) as excinfo:
        provider.create_card(title="t", body="b", language="en", brief_id="brf")
    assert excinfo.value.details["provider"] == "canva"
    assert "Canva Autofill failed" in excinfo.value.message


def test_canva_get_job() -> None:
    provider = CanvaAutofillProvider(
        access_token="canva-token",
        brand_template_id="template-9",
        client=_client(
            lambda request: httpx.Response(200, json={"job": {"id": "job-1"}})
        ),
    )
    assert provider.get_job("job-1")["job"]["id"] == "job-1"


def test_canva_get_job_failure() -> None:
    provider = CanvaAutofillProvider(
        access_token="canva-token",
        brand_template_id="template-9",
        client=_client(lambda request: httpx.Response(404, json={})),
    )
    with pytest.raises(IntegrationError):
        provider.get_job("job-1")


@pytest.fixture
def cms(tmp_path) -> LocalCMS:
    db = Database(tmp_path / "cms.db")
    db.init_schema()
    db.create_brief(
        {
            "id": "brf_abcdef123456",
            "title": "CMS fixture brief",
            "vertical": "food_beverage",
            "brief": "Fixture brief used to satisfy the CMS foreign key.",
            "fact_sheet_id": "ag-sparkling-citrus",
            "fact_sheet": {},
            "languages": ["en"],
            "metadata": {},
            "state": "brief",
        },
        actor="test",
    )
    return LocalCMS(db)


def test_local_cms_lifecycle_and_slug(cms: LocalCMS) -> None:
    doc = cms.create_draft(
        brief_id="brf_abcdef123456",
        language="en",
        title="Alder Grove Sparkling Citrus!",
        body="First paragraph.\n\nSecond paragraph.",
    )
    assert doc.id.startswith("cms_")
    assert doc.status == "draft"
    assert doc.slug.startswith("alder-grove-sparkling-citrus")
    assert doc.slug.endswith("-en")
    assert cms.get_document(doc.id)["title"] == doc.title

    cms.set_status("brf_abcdef123456", "staged")
    staged = cms.list_documents(brief_id="brf_abcdef123456", status="staged")
    assert len(staged) == 1

    cms.set_status("brf_abcdef123456", "published")
    assert cms.list_documents(status="published")[0]["id"] == doc.id
    assert cms.list_documents(status="staged") == []

    with pytest.raises(ValueError):
        cms.set_status("brf_abcdef123456", "archived")
    assert cms.get_document("cms_missing") is None


def test_local_cms_preview_escapes_html(cms: LocalCMS) -> None:
    doc = cms.create_draft(
        brief_id="brf_abcdef123456",
        language="en",
        title="<script>alert(1)</script>",
        body="<b>bold</b>\n\n<em>emphasis</em>",
    )
    markup = cms.render_html(cms.get_document(doc.id))
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in markup
    assert "&lt;b&gt;bold&lt;/b&gt;" in markup
    assert markup.count("<p>") == 2
