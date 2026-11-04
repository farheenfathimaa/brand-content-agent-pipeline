"""Task-tracker adapters: in-memory mock, ClickUp, GitHub Issues."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.errors import IntegrationError
from app.schemas import TaskRef

logger = logging.getLogger(__name__)


class MockTaskTracker:
    provider = "mock"

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self._counter = 0

    def create_task(
        self, *, title: str, description: str, brief_id: str, metadata: dict
    ) -> TaskRef:
        self._counter += 1
        external_id = f"mock-{self._counter}"
        self.tasks[external_id] = {
            "title": title,
            "description": description,
            "brief_id": brief_id,
            "metadata": metadata,
            "status": "open",
        }
        return TaskRef(
            provider=self.provider,
            external_id=external_id,
            url=f"mock://task/{external_id}",
        )

    def update_status(self, ref: TaskRef, status: str) -> None:
        if ref.external_id in self.tasks:
            self.tasks[ref.external_id]["status"] = status


class ClickUpTaskTracker:
    provider = "clickup"

    def __init__(
        self,
        *,
        api_token: str,
        list_id: str,
        base_url: str = "https://api.clickup.com/api/v2",
        client: httpx.Client | None = None,
    ) -> None:
        self.api_token = api_token
        self.list_id = list_id
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=20)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def create_task(
        self, *, title: str, description: str, brief_id: str, metadata: dict
    ) -> TaskRef:
        del metadata
        response = self._client.post(
            f"{self.base_url}/list/{self.list_id}/task",
            headers=self._headers(),
            json={
                "name": title,
                "description": f"{description}\n\nPipeline brief: {brief_id}",
            },
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationError(
                f"ClickUp create_task failed with HTTP {response.status_code}.",
                details={"provider": "clickup"},
            )
        data = response.json()
        return TaskRef(
            provider=self.provider,
            external_id=str(data["id"]),
            url=str(data.get("url", "")),
        )

    def update_status(self, ref: TaskRef, status: str) -> None:
        response = self._client.put(
            f"{self.base_url}/task/{ref.external_id}",
            headers=self._headers(),
            json={"status": status},
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationError(
                f"ClickUp update_status failed with HTTP {response.status_code}.",
                details={"provider": "clickup", "external_id": ref.external_id},
            )


class GitHubIssuesTaskTracker:
    provider = "github"

    def __init__(
        self,
        *,
        token: str,
        repo: str,
        base_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.repo = repo
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=20)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create_task(
        self, *, title: str, description: str, brief_id: str, metadata: dict
    ) -> TaskRef:
        del metadata
        response = self._client.post(
            f"{self.base_url}/repos/{self.repo}/issues",
            headers=self._headers(),
            json={
                "title": title,
                "body": f"{description}\n\nPipeline brief: `{brief_id}`",
                "labels": ["content-pipeline", "status:brief"],
            },
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationError(
                f"GitHub create_task failed with HTTP {response.status_code}.",
                details={"provider": "github"},
            )
        data = response.json()
        return TaskRef(
            provider=self.provider,
            external_id=str(data["number"]),
            url=str(data.get("html_url", "")),
        )

    def update_status(self, ref: TaskRef, status: str) -> None:
        payload: dict = {
            "labels": ["content-pipeline", f"status:{status}"],
            "state": "closed" if status == "published" else "open",
        }
        response = self._client.patch(
            f"{self.base_url}/repos/{self.repo}/issues/{ref.external_id}",
            headers=self._headers(),
            json=payload,
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationError(
                f"GitHub update_status failed with HTTP {response.status_code}.",
                details={"provider": "github", "external_id": ref.external_id},
            )


def build_task_tracker(settings: Settings, *, client: httpx.Client | None = None):
    if settings.task_tracker == "clickup":
        return ClickUpTaskTracker(
            api_token=settings.clickup_api_token,
            list_id=settings.clickup_list_id,
            base_url=settings.clickup_base_url,
            client=client,
        )
    if settings.task_tracker == "github":
        return GitHubIssuesTaskTracker(
            token=settings.github_token,
            repo=settings.github_repo,
            base_url=settings.github_base_url,
            client=client,
        )
    return MockTaskTracker()
