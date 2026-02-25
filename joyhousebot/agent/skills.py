"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    """
    Loader for agent skills.
    
    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """
    
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
    
    def list_skills(
        self,
        filter_unavailable: bool = True,
        allowed_names: set[str] | None = None,
    ) -> list[dict[str, str]]:
        """
        List all available skills.
        
        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
            allowed_names: If set, only include skills whose name is in this set (e.g. enabled list).
        
        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []
        seen_names: set[str] = set()

        for source, root in self._iter_skill_roots():
            if not root.exists() or not root.is_dir():
                continue
            for skill_dir in root.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                if skill_dir.name in seen_names:
                    continue
                seen_names.add(skill_dir.name)
                skills.append({"name": skill_dir.name, "path": str(skill_file), "source": source})
        
        if allowed_names is not None:
            skills = [s for s in skills if s["name"] in allowed_names]
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def _iter_skill_roots(self) -> list[tuple[str, Path]]:
        """Resolve all skill source roots in precedence order."""
        roots: list[tuple[str, Path]] = []
        roots.append(("workspace", self.workspace_skills))
        if self.builtin_skills:
            roots.append(("builtin", self.builtin_skills))

        try:
            from joyhousebot.config.access import get_config
            from joyhousebot.plugins.skills import resolve_plugin_skill_dirs

            config = get_config()
            plugin_dirs = resolve_plugin_skill_dirs(self.workspace, config)
            for plugin_dir in plugin_dirs:
                roots.append(("plugin", plugin_dir))
        except Exception:
            # Keep skills resilient even when plugin host/config is unavailable.
            pass

        return roots
    
    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.
        
        Args:
            name: Skill name (directory name).
        
        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")
        
        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")
        
        # Check plugin skill roots (from resolve_plugin_skill_dirs)
        for _source, root in self._iter_skill_roots():
            if _source == "plugin":
                skill_file = root / name / "SKILL.md"
                if skill_file.exists():
                    return skill_file.read_text(encoding="utf-8")
        
        return None
    
    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.
        
        Args:
            skill_names: List of skill names to load.
        
        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")
        
        return "\n\n---\n\n".join(parts) if parts else ""
    
    def build_skills_summary(self, allowed_names: set[str] | None = None) -> str:
        """
        Build a summary of all skills (name, description, path, availability).
        
        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.
        
        Args:
            allowed_names: If set, only include these skills (e.g. enabled list).
        
        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False, allowed_names=allowed_names)
        if not all_skills:
            return ""
        
        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            
            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")
            
            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
            
            lines.append(f"  </skill>")
        lines.append("</skills>")
        
        return "\n".join(lines)
    
    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)
    
    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name
    
    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content
    
    def _parse_joyhousebot_metadata(self, raw: str) -> dict:
        """Parse joyhousebot metadata JSON from frontmatter."""
        try:
            data = json.loads(raw)
            return data.get("joyhousebot", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True
    
    def _get_skill_meta(self, name: str) -> dict:
        """Get joyhousebot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_joyhousebot_metadata(meta.get("metadata", ""))
    
    def get_always_skills(self, allowed_names: set[str] | None = None) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True, allowed_names=allowed_names):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_joyhousebot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result
    
    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.
        
        Args:
            name: Skill name.
        
        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content:
            return None
        
        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Simple YAML parsing
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata
        
        return None
