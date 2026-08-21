"""Database-only reader for published independent Skill assets."""

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
        skills: list[dict[str, str]] = []
        for definition in self.runtime_store.list_skills(active_only=True):
            current = dict(definition.get("current") or {})
            skill_id = str(definition.get("skill_id") or "")
            if not current or not skill_id.startswith("skill."):
                continue
            name = skill_id.removeprefix("skill.")
            if allowed_names is not None and name not in allowed_names:
                continue
            skills.append(
                {
                    "name": name,
                    "skill_id": skill_id,
                    "version": str(current.get("version") or ""),
                    "content_sha256": str(current.get("content_sha256") or ""),
                    "source": "skill_catalog",
                }
            )
        return sorted(skills, key=lambda item: item["name"])

    def load_skill(self, name: str, version: str | None = None) -> str | None:
        definition = self.runtime_store.get_published_skill(f"skill.{name}", version)
        if definition is None:
            return None
        content = definition.get("instruction_content")
        return content if isinstance(content, str) and content.strip() else None

    def load_skills_for_context(
        self, skill_names: list[str], *, versions: dict[str, str] | None = None
    ) -> str:
        parts = []
        for name in skill_names:
            content = self.load_skill(name, (versions or {}).get(name))
            if content:
                parts.append(f"### Skill: {name}\n\n{self._strip_frontmatter(content)}")
        return "\n\n---\n\n".join(parts)

    def build_skills_summary(self, allowed_names: set[str] | None = None) -> str:
        rows = self.list_skills(filter_unavailable=False, allowed_names=allowed_names)
        if not rows:
            return ""
        lines = ["<skills>"]
        for row in rows:
            definition = self.runtime_store.get_published_skill(
                row["skill_id"], row["version"]
            )
            description = str((definition or {}).get("description") or row["name"])
            lines.extend(
                (
                    '  <skill available="true">',
                    f"    <name>{self._escape_xml(row['name'])}</name>",
                    f"    <version>{self._escape_xml(row['version'])}</version>",
                    f"    <description>{self._escape_xml(description)}</description>",
                    "  </skill>",
                )
            )
        lines.append("</skills>")
        return "\n".join(lines)

    def get_always_skills(self, allowed_names: set[str] | None = None) -> list[str]:
        """Activation belongs to an Agent revision binding, not the Skill asset."""
        del allowed_names
        return []

    def get_skill_metadata(self, name: str) -> dict[str, str] | None:
        definition = self.runtime_store.get_published_skill(f"skill.{name}")
        if definition is None:
            return None
        return {
            "name": str(definition.get("name") or name),
            "description": str(definition.get("description") or name),
            "version": str(definition.get("version") or ""),
            "content_sha256": str(definition.get("content_sha256") or ""),
        }

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
