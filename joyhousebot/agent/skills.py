"""Database-only reader for published prompt Skill capabilities."""

from __future__ import annotations

import re
from typing import Any


class SkillsLoader:
    """Read immutable Skill versions from the shared runtime store."""

    def __init__(self, runtime_store: Any):
        if runtime_store is None:
            raise ValueError("runtime_store is required for Skills")
        self.runtime_store = runtime_store

    def list_skills(
        self,
        filter_unavailable: bool = True,
        allowed_names: set[str] | None = None,
    ) -> list[dict[str, str]]:
        del filter_unavailable
        skills = []
        for definition in self.runtime_store.list_capability_definitions():
            ref = dict(definition.get("ref") or {})
            capability_id = str(ref.get("capability_id") or "")
            if ref.get("kind") != "skill" or not capability_id.startswith("skill."):
                continue
            name = capability_id.removeprefix("skill.")
            if allowed_names is not None and name not in allowed_names:
                continue
            if not self._runtime_settings(capability_id)["enabled"]:
                continue
            skills.append(
                {
                    "name": name,
                    "version": str(ref.get("version") or ""),
                    "source": "catalog",
                }
            )
        return sorted(skills, key=lambda item: item["name"])

    def load_skill(self, name: str) -> str | None:
        definition = self.runtime_store.get_capability_definition(f"skill.{name}")
        if definition is None or not self._runtime_settings(f"skill.{name}")["enabled"]:
            return None
        content = self._configuration(definition).get("instruction_content")
        return content if isinstance(content, str) and content.strip() else None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                parts.append(f"### Skill: {name}\n\n{self._strip_frontmatter(content)}")
        return "\n\n---\n\n".join(parts)

    def build_skills_summary(self, allowed_names: set[str] | None = None) -> str:
        rows = self.list_skills(filter_unavailable=False, allowed_names=allowed_names)
        if not rows:
            return ""
        lines = ["<skills>"]
        for row in rows:
            definition = self.runtime_store.get_capability_definition(
                f"skill.{row['name']}", row["version"]
            )
            description = str((definition or {}).get("description") or row["name"])
            lines.extend(
                (
                    '  <skill available="true">',
                    f"    <name>{self._escape_xml(row['name'])}</name>",
                    f"    <description>{self._escape_xml(description)}</description>",
                    "  </skill>",
                )
            )
        lines.append("</skills>")
        return "\n".join(lines)

    def get_always_skills(self, allowed_names: set[str] | None = None) -> list[str]:
        result = []
        for row in self.list_skills(allowed_names=allowed_names):
            definition = self.runtime_store.get_capability_definition(
                f"skill.{row['name']}", row["version"]
            )
            if definition is not None and bool(self._configuration(definition).get("always")):
                result.append(row["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict[str, str] | None:
        definition = self.runtime_store.get_capability_definition(f"skill.{name}")
        if definition is None:
            return None
        metadata = {
            "name": str(definition.get("name") or name),
            "description": str(definition.get("description") or name),
        }
        configuration = self._configuration(definition)
        if "always" in configuration:
            metadata["always"] = str(bool(configuration["always"])).lower()
        return metadata

    def _runtime_settings(self, capability_id: str) -> dict[str, Any]:
        getter = getattr(self.runtime_store, "get_capability_runtime_settings", None)
        value = getter(capability_id) if callable(getter) else {}
        return {"enabled": bool(value.get("enabled", True)), "configuration": dict(value.get("configuration") or {})}

    def _configuration(self, definition: dict[str, Any]) -> dict[str, Any]:
        capability_id = str(dict(definition.get("ref") or {}).get("capability_id") or "")
        # Settings only overlay supplied keys. This preserves immutable
        # instruction content until an operator intentionally replaces it.
        return {**dict(definition.get("configuration") or {}), **self._runtime_settings(capability_id)["configuration"]}

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    @staticmethod
    def _escape_xml(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
