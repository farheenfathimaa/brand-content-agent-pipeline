"""Design providers: local HTML -> PNG (Playwright, Pillow fallback), Canva Autofill."""

from __future__ import annotations

import html
import logging
import textwrap
from pathlib import Path

import httpx

from app.config import Settings
from app.errors import IntegrationError
from app.schemas import DesignAsset

logger = logging.getLogger(__name__)


class LocalHTMLDesignProvider:
    """Default provider. Renders a 1200x630 card from local HTML.

    Playwright is the primary renderer. If a Chromium browser is not installed,
    the provider falls back to a Pillow-drawn PNG so the zero-key quickstart
    still completes. The asset records which renderer was used.
    """

    provider = "local"

    def __init__(
        self, output_dir: str | Path = "outputs/designs", *, force_fallback: bool = False
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.force_fallback = force_fallback

    def create_card(
        self, *, title: str, body: str, language: str, brief_id: str
    ) -> DesignAsset:
        stem = f"{brief_id}-{language}"
        html_path = self.output_dir / f"{stem}.html"
        png_path = self.output_dir / f"{stem}.png"
        markup = self._render_html(title, body, language)
        html_path.write_text(markup, encoding="utf-8")

        rendered_with = "playwright"
        note = "Rendered with Playwright from local HTML."
        try:
            if self.force_fallback:
                raise RuntimeError("force_fallback enabled")
            self._playwright_png(markup, png_path)
        except Exception as exc:  # noqa: BLE001 - fallback is intentional
            rendered_with = "pillow-fallback"
            note = (
                "Playwright/Chromium unavailable; used the Pillow fallback. "
                f"Renderer error: {type(exc).__name__}. Install Chromium with "
                "`playwright install chromium` for the primary path."
            )
            self._pillow_png(title, body, language, png_path)

        return DesignAsset(
            provider=self.provider,
            kind="png",
            status="ready",
            path=str(png_path),
            rendered_with=rendered_with,
            note=note,
        )

    def _render_html(self, title: str, body: str, language: str) -> str:
        paragraphs = "".join(
            f"<p>{html.escape(paragraph.strip())}</p>"
            for paragraph in body.split("\n")
            if paragraph.strip()
        )
        return f"""<!doctype html>
<html lang="{html.escape(language)}">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; width: 1200px; height: 630px; overflow: hidden;
    font-family: Inter, "Segoe UI", Arial, sans-serif;
    background: linear-gradient(135deg, #10261f 0%, #1e4d3a 55%, #d8a353 100%);
    color: #f7f4ec; padding: 64px 72px; display: flex; flex-direction: column;
  }}
  .eyebrow {{ font-size: 20px; letter-spacing: 4px; text-transform: uppercase; opacity: .85; }}
  h1 {{ font-size: 58px; line-height: 1.05; margin: 24px 0 22px; max-width: 980px; }}
  p {{ font-size: 24px; line-height: 1.35; margin: 0 0 12px; max-width: 960px; opacity: .95; }}
  .footer {{ margin-top: auto; font-size: 18px; letter-spacing: 1px; opacity: .8; }}
  .chip {{ display: inline-block; padding: 7px 14px; border: 1px solid rgba(247,244,236,.45);
           border-radius: 999px; font-size: 16px; text-transform: uppercase; letter-spacing: 2px; }}
</style>
</head>
<body>
  <div><span class="chip">{html.escape(language)}</span></div>
  <div class="eyebrow">Alder Grove · fictional brand demo</div>
  <h1>{html.escape(title)}</h1>
  {paragraphs}
  <div class="footer">Local design provider · no API key required</div>
</body>
</html>"""

    @staticmethod
    def _playwright_png(markup: str, png_path: Path) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 630})
                page.set_content(markup, wait_until="load")
                page.screenshot(path=str(png_path), full_page=False)
            finally:
                browser.close()

    @staticmethod
    def _pillow_png(title: str, body: str, language: str, png_path: Path) -> None:
        from PIL import Image, ImageDraw, ImageFont

        width, height = 1200, 630
        image = Image.new("RGB", (width, height), "#163a2c")
        draw = ImageDraw.Draw(image)
        for y in range(height):
            ratio = y / height
            draw.line(
                [(0, y), (width, y)],
                fill=(
                    int(16 + 210 * ratio),
                    int(38 + 70 * ratio),
                    int(31 + 10 * ratio),
                ),
            )
        try:
            title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 52)
            body_font = ImageFont.truetype("DejaVuSans.ttf", 24)
            small_font = ImageFont.truetype("DejaVuSans.ttf", 18)
        except OSError:
            title_font = ImageFont.load_default(size=52)
            body_font = ImageFont.load_default(size=24)
            small_font = ImageFont.load_default(size=18)

        draw.text((70, 62), f"{language.upper()} · FICTIONAL BRAND DEMO", fill="#f0e4c8", font=small_font)
        wrapped_title = textwrap.wrap(title, width=34)
        y = 140
        for line in wrapped_title[:3]:
            draw.text((70, y), line, fill="#fffaf0", font=title_font)
            y += 62
        y += 18
        clipped = " ".join(body.split())
        for line in textwrap.wrap(clipped, width=64)[:5]:
            draw.text((70, y), line, fill="#efe9dd", font=body_font)
            y += 34
        draw.text((70, 560), "Local design provider · no API key required", fill="#f0e4c8", font=small_font)
        image.save(png_path, format="PNG")


class CanvaAutofillProvider:
    """Canva Connect Autofill adapter.

    NOT LIVE-TESTED. Canva Autofill requires Canva Enterprise and a brand
    template whose autofill fields match the field names sent here. This adapter
    is exercised only with mocked HTTP in tests. It is coded against Canva's
    documented endpoints:

      POST /v1/autofills
      GET  /v1/autofills/{jobId}

    Verify field names and response shapes against the current Canva docs before
    production use.
    """

    provider = "canva"

    def __init__(
        self,
        *,
        access_token: str,
        brand_template_id: str,
        base_url: str = "https://api.canva.com/rest",
        client: httpx.Client | None = None,
        field_names: dict[str, str] | None = None,
    ) -> None:
        self.access_token = access_token
        self.brand_template_id = brand_template_id
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)
        self.field_names = field_names or {
            "title": "headline",
            "body": "body",
            "language": "language",
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def create_card(
        self, *, title: str, body: str, language: str, brief_id: str
    ) -> DesignAsset:
        del brief_id
        payload = {
            "brand_template_id": self.brand_template_id,
            "data": {
                self.field_names["title"]: {"type": "text", "text": title},
                self.field_names["body"]: {"type": "text", "text": body},
                self.field_names["language"]: {"type": "text", "text": language},
            },
        }
        response = self._client.post(
            f"{self.base_url}/v1/autofills",
            headers=self._headers(),
            json=payload,
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationError(
                f"Canva Autofill failed with HTTP {response.status_code}.",
                details={"provider": "canva"},
            )
        data = response.json()
        job = data.get("job", {})
        return DesignAsset(
            provider=self.provider,
            kind="canva-job",
            status=str(job.get("status", "submitted")),
            external_id=str(job.get("id", "")),
            note=(
                "NOT LIVE-TESTED. Canva Autofill requires Canva Enterprise. "
                "Poll GET /v1/autofills/{jobId} for the finished design."
            ),
        )

    def get_job(self, job_id: str) -> dict:
        response = self._client.get(
            f"{self.base_url}/v1/autofills/{job_id}", headers=self._headers()
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationError(
                f"Canva job lookup failed with HTTP {response.status_code}.",
                details={"provider": "canva", "job_id": job_id},
            )
        return response.json()


def build_design_provider(settings: Settings) -> LocalHTMLDesignProvider | CanvaAutofillProvider:
    if settings.design_provider == "canva":
        return CanvaAutofillProvider(
            access_token=settings.canva_access_token,
            brand_template_id=settings.canva_brand_template_id,
            base_url=settings.canva_base_url,
        )
    return LocalHTMLDesignProvider(settings.design_output_dir)
