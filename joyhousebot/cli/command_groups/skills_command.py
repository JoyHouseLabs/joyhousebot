"""Skills command group extracted from legacy commands.py."""

from __future__ import annotations

import re

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table


def _get_skills_loader():
    """Build SkillsLoader from config workspace (or default) and builtin dir."""
    from joyhousebot.config.loader import load_config, get_config_path
    from joyhousebot.agent.skills import SkillsLoader, BUILTIN_SKILLS_DIR
    from joyhousebot.utils.helpers import get_workspace_path

    config_path = get_config_path()
    if config_path.exists():
        config = load_config(config_path)
        workspace = config.workspace_path()
    else:
        workspace = get_workspace_path()
    return SkillsLoader(workspace, BUILTIN_SKILLS_DIR)


def register_skills_commands(app: typer.Typer, console: Console) -> None:
    """Register skills command group."""
    skills_app = typer.Typer(help="Manage skills (list, install from GitHub, show)")
    app.add_typer(skills_app, name="skills")

    @skills_app.command("list")
    def skills_list(
        all_skills: bool = typer.Option(False, "--all", "-a", help="Include unavailable (missing deps)"),
    ) -> None:
        """List installed and builtin skills."""
        loader = _get_skills_loader()
        skills = loader.list_skills(filter_unavailable=not all_skills)
        if not skills:
            console.print("No skills installed. Use [cyan]joyhousebot skills install <repo>[/cyan] or add to workspace/skills/")
            return
        table = Table(title="Skills")
        table.add_column("Name", style="cyan")
        table.add_column("Source")
        table.add_column("Description")
        for s in skills:
            desc = loader._get_skill_description(s["name"]) or ""
            table.add_row(s["name"], s["source"], desc[:60] + "..." if len(desc) > 60 else desc)
        console.print(table)

    @skills_app.command("install")
    def skills_install(
        repo: str = typer.Argument(..., help="GitHub repo: owner/repo or owner/repo/subpath (e.g. sipeed/picoclaw-skills/weather)"),
        branch: str = typer.Option("main", "--branch", "-b", help="Git branch"),
    ) -> None:
        """Install a skill from GitHub (fetches SKILL.md)."""
        from joyhousebot.config.loader import load_config, get_config_path
        from joyhousebot.utils.helpers import get_workspace_path
        from joyhousebot.skill_installer import install_from_github

        config_path = get_config_path()
        workspace = load_config(config_path).workspace_path() if config_path.exists() else get_workspace_path()
        try:
            name = install_from_github(workspace, repo, branch=branch)
            console.print(f"[green]✓[/green] Skill [cyan]{name}[/cyan] installed from {repo}")
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e

    @skills_app.command("list-builtin")
    def skills_list_builtin() -> None:
        """List builtin skills (shipped with joyhousebot)."""
        from joyhousebot.agent.skills import BUILTIN_SKILLS_DIR

        if not BUILTIN_SKILLS_DIR.exists():
            console.print("No builtin skills directory found.")
            return
        table = Table(title="Builtin Skills")
        table.add_column("Name", style="cyan")
        table.add_column("Description")
        count = 0
        for d in sorted(BUILTIN_SKILLS_DIR.iterdir()):
            if d.is_dir():
                skill_file = d / "SKILL.md"
                if skill_file.exists():
                    content = skill_file.read_text(encoding="utf-8")
                    desc = ""
                    if content.startswith("---"):
                        m = re.search(r"description:\s*(.+?)(?:\n|$)", content)
                        if m:
                            desc = m.group(1).strip().strip('"\'')
                    table.add_row(d.name, desc[:70] + "..." if len(desc) > 70 else desc)
                    count += 1
        if count == 0:
            console.print("No builtin skills found.")
        else:
            console.print(table)

    @skills_app.command("show")
    def skills_show(
        name: str = typer.Argument(..., help="Skill name"),
    ) -> None:
        """Show skill content (SKILL.md)."""
        loader = _get_skills_loader()
        content = loader.load_skill(name)
        if not content:
            console.print(f"[red]Skill '{name}' not found.[/red]")
            raise typer.Exit(1)
        console.print(Markdown(f"# Skill: {name}\n\n{content}"))

    @skills_app.command("search")
    def skills_search(
        query: str = typer.Argument(None, help="Filter by name or description (optional)"),
    ) -> None:
        """Search installed and builtin skills by name or description."""
        loader = _get_skills_loader()
        all_skills = loader.list_skills(filter_unavailable=False)
        if query:
            q = query.lower()
            all_skills = [
                s for s in all_skills if q in s["name"].lower() or q in (loader._get_skill_description(s["name"]) or "").lower()
            ]
        if not all_skills:
            console.print("No matching skills." if query else "No skills installed.")
            return
        table = Table(title="Skills" + (f" (matching '{query}')" if query else ""))
        table.add_column("Name", style="cyan")
        table.add_column("Source")
        table.add_column("Description")
        for s in all_skills:
            desc = loader._get_skill_description(s["name"]) or ""
            table.add_row(s["name"], s["source"], desc[:60] + "..." if len(desc) > 60 else desc)
        console.print(table)

