"""Load the fictional fact-sheet library used for grounding checks."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.enums import Vertical
from app.schemas import FactSheet

DEFAULT_FACT_SHEETS_PATH = Path(__file__).parent / "data" / "fact_sheets.yaml"


class FactSheetRegistry:
    def __init__(self, sheets: list[FactSheet]) -> None:
        self._sheets = {sheet.id: sheet for sheet in sheets}
        if len(self._sheets) != len(sheets):
            raise ValueError("Duplicate fact sheet id in library.")

    def get(self, sheet_id: str) -> FactSheet:
        try:
            return self._sheets[sheet_id]
        except KeyError as exc:
            available = ", ".join(sorted(self._sheets))
            raise KeyError(
                f"Unknown fact_sheet_id '{sheet_id}'. Available: {available}"
            ) from exc

    def all(self) -> list[FactSheet]:
        return list(self._sheets.values())

    def catalog_products(self, vertical: Vertical | None = None) -> list[str]:
        return sorted(
            {
                sheet.product_name
                for sheet in self._sheets.values()
                if vertical is None or sheet.vertical == vertical
            }
        )


def load_fact_sheets(path: Path | str | None = None) -> FactSheetRegistry:
    source = Path(path) if path else DEFAULT_FACT_SHEETS_PATH
    with source.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    entries = raw.get("fact_sheets")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"No fact_sheets found in {source}")
    sheets = [FactSheet.model_validate(entry) for entry in entries]
    return FactSheetRegistry(sheets)
