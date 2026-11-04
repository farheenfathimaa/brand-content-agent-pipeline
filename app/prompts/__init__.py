"""Versioned prompt library with registry lookups and strict rendering."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, Field

PROMPTS_DIR = Path(__file__).parent


class PromptTemplate(BaseModel):
    id: str
    version: str
    created: str
    changelog: str = ""
    variables: list[str] = Field(default_factory=list)
    system: str
    user: str


class PromptLibrary:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else PROMPTS_DIR
        self.registry_path = self.root / "registry.yaml"
        with self.registry_path.open("r", encoding="utf-8") as handle:
            self.registry = yaml.safe_load(handle) or {}
        self._templates: dict[tuple[str, str], PromptTemplate] = {}
        self._env = Environment(undefined=StrictUndefined, autoescape=False)

    def _entry(self, name: str) -> dict:
        prompts = self.registry.get("prompts", {})
        if name not in prompts:
            raise KeyError(f"Unknown prompt '{name}'. Known: {', '.join(sorted(prompts))}")
        return prompts[name]

    def versions(self, name: str) -> list[str]:
        return list(self._entry(name).get("versions", {}).keys())

    def active_version(self, name: str) -> str:
        return str(self._entry(name)["active"])

    def resolve_version(self, name: str, override: str = "") -> str:
        version = override.strip() or self.active_version(name)
        if version not in self.versions(name):
            raise KeyError(
                f"Prompt '{name}' has no version '{version}'. "
                f"Known: {', '.join(self.versions(name))}"
            )
        return version

    def get(self, name: str, version: str = "") -> PromptTemplate:
        resolved = self.resolve_version(name, version)
        key = (name, resolved)
        if key in self._templates:
            return self._templates[key]
        entry = self._entry(name)["versions"][resolved]
        path = self.root / entry["path"]
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        template = PromptTemplate.model_validate(raw)
        if template.id != name or template.version != resolved:
            raise ValueError(
                f"Prompt file {path} declares ({template.id}, {template.version}) "
                f"but registry expects ({name}, {resolved})."
            )
        self._templates[key] = template
        return template

    def render(self, name: str, version: str = "", **variables: object) -> tuple[str, str]:
        template = self.get(name, version)
        missing = [item for item in template.variables if item not in variables]
        if missing:
            raise ValueError(f"Missing prompt variables for {name}: {', '.join(missing)}")
        system = self._env.from_string(template.system).render(**variables).strip()
        user = self._env.from_string(template.user).render(**variables).strip()
        return system, user

    def active_versions(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        overrides = overrides or {}
        names = sorted(self.registry.get("prompts", {}))
        return {
            name: self.resolve_version(name, overrides.get(name, "")) for name in names
        }

    def changelog(self, name: str) -> list[str]:
        entry = self._entry(name)
        lines: list[str] = []
        for version, meta in entry.get("versions", {}).items():
            lines.append(f"{version} ({meta.get('released', 'unknown')}): {meta.get('notes', '')}")
        return lines
