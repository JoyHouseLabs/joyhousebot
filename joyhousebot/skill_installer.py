"""Install skills from GitHub (SKILL.md)."""

import re
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BRANCH = "main"
RAW_BASE = "https://raw.githubusercontent.com"


def install_from_github(
    workspace: Path,
    repo: str,
    branch: str = DEFAULT_BRANCH,
) -> str:
    """
    Install a skill from GitHub by fetching SKILL.md.

    Repo format:
      - owner/repo           -> fetch repo/main/SKILL.md, skill name = repo
      - owner/repo/subpath   -> fetch repo/main/subpath/SKILL.md, skill name = subpath

    Args:
        workspace: Workspace root (skill will be written to workspace/skills/<name>/SKILL.md).
        repo: GitHub repo path, e.g. "openclaw/skills" or "sipeed/picoclaw-skills/weather".
        branch: Git branch (default main).

    Returns:
        Installed skill name (directory name under workspace/skills).

    Raises:
        ValueError: If repo format is invalid or fetch fails.
    """
    repo = repo.strip().rstrip("/")
    parts = [p for p in repo.split("/") if p]
    if len(parts) < 2:
        raise ValueError(
            "Invalid repo: use owner/repo or owner/repo/subpath "
            "(e.g. openclaw/skills or sipeed/picoclaw-skills/weather)"
        )

    owner, repo_name = parts[0], parts[1]
    if len(parts) == 2:
        path_in_repo = "SKILL.md"
        skill_name = _safe_skill_name(repo_name)
    else:
        subpath = "/".join(parts[2:])
        path_in_repo = f"{subpath}/SKILL.md"
        skill_name = _safe_skill_name(parts[-1])

    url = f"{RAW_BASE}/{owner}/{repo_name}/{branch}/{path_in_repo}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}: {url}")
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise ValueError(f"Failed to fetch skill: HTTP {e.code} - {url}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"Failed to fetch skill: {e.reason}") from e

    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = skills_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        raise ValueError(f"Skill '{skill_name}' already exists. Remove it first or use a different repo path.")

    skill_file.write_text(body, encoding="utf-8")
    return skill_name


def _safe_skill_name(name: str) -> str:
    """Make a safe directory name (alphanumeric, dash, underscore)."""
    name = re.sub(r"[^\w\-]", "_", name)
    return name.strip("_") or "skill"
