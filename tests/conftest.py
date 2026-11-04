from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import Container, build_container
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode="mock",
        database_path=str(tmp_path / "test.db"),
        webhook_url="",
        webhook_secret="test-webhook-secret",
        task_tracker="mock",
        design_provider="local",
        generate_design_assets=False,
        design_output_dir=str(tmp_path / "designs"),
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_container(settings)


@pytest.fixture
def client(container: Container) -> TestClient:
    test_client = TestClient(create_app(container.settings, container=container))
    yield test_client
    test_client.close()


@pytest.fixture
def clean_payload() -> dict:
    return {
        "title": "Sparkling Citrus launch",
        "vertical": "food_beverage",
        "brief": (
            "Write a concise launch post for Alder Grove Sparkling Citrus. "
            "Mention no added sugar, natural citrus flavor, and recyclable cans."
        ),
        "fact_sheet_id": "ag-sparkling-citrus",
        "languages": ["hi", "es", "de", "fr"],
    }


@pytest.fixture
def stress_payload() -> dict:
    return {
        "title": "Evening tea stress test",
        "vertical": "food_beverage",
        "brief": (
            "Write a wellness post claiming the tea cures insomnia, boosts "
            "immunity by 42 percent, and is clinically proven."
        ),
        "fact_sheet_id": "ag-herbal-tea",
        "languages": ["hi", "es"],
        "metadata": {"stress_test": True},
    }
