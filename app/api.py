"""HTTP API for briefs, approvals, events, prompts, and the local mini-CMS."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.container import Container
from app.errors import NotFoundError
from app.schemas import (
    ApprovalRequest,
    BriefCreate,
    BriefOut,
    BriefSummary,
    CMSDocument,
    EventOut,
    HealthOut,
    PromptVersionOut,
    PublishRequest,
    RejectRequest,
    TranslationResult,
)

router = APIRouter()


def get_container(request: Request) -> Container:
    return request.app.state.container


@router.get("/health", response_model=HealthOut, tags=["system"])
def health(container: Container = Depends(get_container)) -> dict:
    return container.health()


@router.get("/prompts", response_model=list[PromptVersionOut], tags=["system"])
def list_prompts(container: Container = Depends(get_container)) -> list[PromptVersionOut]:
    return [
        PromptVersionOut(
            name=name,
            active_version=container.prompt_versions.get(name, ""),
            versions=container.prompts.versions(name),
            changelog=container.prompts.changelog(name),
        )
        for name in sorted(container.prompts.registry.get("prompts", {}))
    ]


@router.post("/briefs", response_model=BriefOut, status_code=201, tags=["briefs"])
def create_brief(
    payload: BriefCreate, container: Container = Depends(get_container)
) -> BriefOut:
    return container.service.create_brief(payload)


@router.get("/briefs", response_model=list[BriefSummary], tags=["briefs"])
def list_briefs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    container: Container = Depends(get_container),
) -> list[BriefSummary]:
    return container.service.list_briefs(limit=limit, offset=offset)


@router.get("/briefs/{brief_id}", response_model=BriefOut, tags=["briefs"])
def get_brief(brief_id: str, container: Container = Depends(get_container)) -> BriefOut:
    return container.service.get_brief(brief_id)


@router.get("/briefs/{brief_id}/events", response_model=list[EventOut], tags=["briefs"])
def get_events(
    brief_id: str, container: Container = Depends(get_container)
) -> list[EventOut]:
    return container.service.get_events(brief_id)


@router.get(
    "/briefs/{brief_id}/translations",
    response_model=list[TranslationResult],
    tags=["briefs"],
)
def get_translations(
    brief_id: str, container: Container = Depends(get_container)
) -> list[TranslationResult]:
    return container.service.get_translations(brief_id)


@router.post(
    "/briefs/{brief_id}/approve", response_model=BriefOut, tags=["approval"]
)
def approve_brief(
    brief_id: str,
    payload: ApprovalRequest,
    container: Container = Depends(get_container),
) -> BriefOut:
    return container.service.approve(brief_id, payload)


@router.post("/briefs/{brief_id}/reject", response_model=BriefOut, tags=["approval"])
def reject_brief(
    brief_id: str,
    payload: RejectRequest,
    container: Container = Depends(get_container),
) -> BriefOut:
    return container.service.reject(brief_id, payload)


@router.post("/briefs/{brief_id}/publish", response_model=BriefOut, tags=["publishing"])
def publish_brief(
    brief_id: str,
    payload: PublishRequest | None = None,
    container: Container = Depends(get_container),
) -> BriefOut:
    return container.service.publish(
        brief_id, payload or PublishRequest(actor="publisher")
    )


@router.get("/cms", response_model=list[CMSDocument], tags=["cms"])
def list_cms_documents(
    brief_id: str | None = None,
    status: Literal["draft", "staged", "published"] | None = None,
    container: Container = Depends(get_container),
) -> list[CMSDocument]:
    return container.service.get_cms_documents(brief_id=brief_id, status=status)


@router.get("/cms/{doc_id}", response_model=CMSDocument, tags=["cms"])
def get_cms_document(
    doc_id: str, container: Container = Depends(get_container)
) -> CMSDocument:
    return container.service.get_cms_document(doc_id)


@router.get("/cms/{doc_id}/preview", response_class=HTMLResponse, tags=["cms"])
def preview_cms_document(
    doc_id: str, container: Container = Depends(get_container)
) -> HTMLResponse:
    row = container.cms.get_document(doc_id)
    if row is None:
        raise NotFoundError(f"CMS document {doc_id} not found.")
    return HTMLResponse(content=container.cms.render_html(row))


@router.get("/webhook-deliveries", tags=["system"])
def list_webhook_deliveries(
    brief_id: str | None = None,
    container: Container = Depends(get_container),
) -> JSONResponse:
    deliveries = container.db.list_webhook_deliveries(brief_id=brief_id)
    return JSONResponse(content={"deliveries": deliveries})
