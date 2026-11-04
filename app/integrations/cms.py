"""A local mini-CMS with draft -> staged -> published documents."""

from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime

from app.db import Database
from app.schemas import CMSDocument

VALID_STATUSES = {"draft", "staged", "published"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(value: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (slug[:limit].strip("-") or "document")


class LocalCMS:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_draft(
        self, *, brief_id: str, language: str, title: str, body: str
    ) -> CMSDocument:
        doc = CMSDocument(
            id=f"cms_{uuid.uuid4().hex[:12]}",
            brief_id=brief_id,
            language=language,
            title=title,
            body=body,
            status="draft",
            slug=f"{_slug(title)}-{brief_id[-6:]}-{language}",
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.create_cms_document(doc.model_dump())
        return doc

    def set_status(self, brief_id: str, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid CMS status '{status}'.")
        self.db.set_cms_status(brief_id, status)

    def list_documents(
        self, *, brief_id: str | None = None, status: str | None = None
    ) -> list[dict]:
        return self.db.list_cms_documents(brief_id=brief_id, status=status)

    def get_document(self, doc_id: str) -> dict | None:
        return self.db.get_cms_document(doc_id)

    def render_html(self, document: dict) -> str:
        status = html.escape(str(document["status"]))
        color = {"draft": "#8a6d3b", "staged": "#1d5f8a", "published": "#1e6b45"}.get(
            str(document["status"]), "#444"
        )
        paragraphs = "".join(
            f"<p>{html.escape(paragraph.strip())}</p>"
            for paragraph in str(document["body"]).split("\n")
            if paragraph.strip()
        )
        return f"""<!doctype html>
<html lang="{html.escape(str(document['language']))}">
<head>
<meta charset="utf-8">
<title>{html.escape(str(document['title']))}</title>
<style>
  body {{ font-family: Georgia, serif; max-width: 760px; margin: 48px auto;
         padding: 0 24px; color: #1d241f; line-height: 1.6; }}
  .bar {{ display: flex; gap: 12px; align-items: center; font-family: sans-serif; }}
  .badge {{ background: {color}; color: white; padding: 4px 10px; border-radius: 999px;
            font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}
  .meta {{ color: #667; font-family: sans-serif; font-size: 13px; }}
  h1 {{ font-size: 38px; line-height: 1.15; margin: 26px 0 10px; }}
  .note {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid #ddd;
           color: #667; font-family: sans-serif; font-size: 12px; }}
</style>
</head>
<body>
  <div class="bar"><span class="badge">{status}</span>
    <span class="meta">{html.escape(str(document['language']))} · local mini-CMS</span></div>
  <h1>{html.escape(str(document['title']))}</h1>
  {paragraphs}
  <div class="note">Fictional brand demo. Alder Grove is not a real company.
    Document: {html.escape(str(document['id']))} · slug: {html.escape(str(document['slug']))}</div>
</body>
</html>"""
