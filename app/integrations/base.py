"""Adapter interfaces for task trackers, design providers, and the mini-CMS."""

from __future__ import annotations

from typing import Protocol

from app.schemas import DesignAsset, TaskRef


class TaskTracker(Protocol):
    provider: str

    def create_task(
        self, *, title: str, description: str, brief_id: str, metadata: dict
    ) -> TaskRef: ...

    def update_status(self, ref: TaskRef, status: str) -> None: ...


class DesignProvider(Protocol):
    provider: str

    def create_card(
        self, *, title: str, body: str, language: str, brief_id: str
    ) -> DesignAsset: ...
