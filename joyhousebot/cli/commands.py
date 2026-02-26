"""CLI commands for joyhousebot.

In the overall architecture: CLI is the single entry point, registering top-level commands (onboard, gateway, agent, status) and
command_groups (config, channels, cron, skills, plugins, house, wallet, runtime, comms, protocol).
"""

import asyncio
import getpass
import os

# 使用本地 LiteLLM cost map，避免任何代码首次 import litellm 时发起远程请求导致启动变慢
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import signal
from pathlib import Path
import select
import sys
import time

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

from joyhousebot import __version__, __logo__
from joyhousebot.cli.command_groups.group_registry import register_command_groups
from joyhousebot.cli.command_groups.channels_command import register_channels_commands
from joyhousebot.cli.command_groups.cron_command import register_cron_commands
from joyhousebot.cli.command_groups.house_command import register_house_commands
from joyhousebot.cli.command_groups.plugins_command import register_plugins_commands
from joyhousebot.cli.command_groups.skills_command import register_skills_commands
from joyhousebot.cli.command_groups.status_command import status_command
from joyhousebot.cli.command_groups.wallet_command import register_wallet_commands
from joyhousebot.cli.shared.logging_utils import ensure_rotating_log_file
from joyhousebot.cli.shared.network_utils import is_port_in_use
from joyhousebot.cli.shared.provider_utils import make_provider

app = typer.Typer(
    name="joyhousebot",
    help=f"{__logo__} joyhousebot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".joyhousebot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} joyhousebot[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} joyhousebot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """joyhousebot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize joyhousebot configuration and workspace."""
    from joyhousebot.config.access import get_config as get_cached_config
    from joyhousebot.config.loader import get_config_path, save_config
    from joyhousebot.config.schema import Config
    from joyhousebot.utils.helpers import get_workspace_path
    
    config_path = get_config_path()
    
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = get_cached_config(force_reload=True)
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")
    
    # Load config to get agent list (may have been just saved)
    config = get_cached_config(force_reload=True)
    agent_list = getattr(config.agents, "agent_list", None) or []

    if agent_list:
        # Initialize workspace for each agent (~/.joyhousebot/agents/<id>)
        for entry in agent_list:
            workspace = Path(entry.workspace).expanduser()
            if not workspace.exists():
                workspace.mkdir(parents=True, exist_ok=True)
                console.print(f"[green]✓[/green] Created workspace for agent [cyan]{entry.name or entry.id}[/cyan] at {workspace}")
            _create_workspace_templates(workspace, agent_name=entry.name or entry.id, agent_id=entry.id)
    else:
        # Single default workspace
        workspace = get_workspace_path()
        if not workspace.exists():
            workspace.mkdir(parents=True, exist_ok=True)
            console.print(f"[green]✓[/green] Created workspace at {workspace}")
        _create_workspace_templates(workspace)

    console.print(f"\n{__logo__} joyhousebot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Edit [cyan]~/.joyhousebot/config.json[/cyan]:")
    console.print("     Default model is [bold]anthropic/claude-opus-4-5[/bold]. Set [bold]one[/bold] of:")
    console.print("     • [bold]providers.openrouter.apiKey[/bold] — recommended (one key, many models): https://openrouter.ai/keys")
    console.print("     • [bold]providers.anthropic.apiKey[/bold] — direct Anthropic API key")
    console.print("     Optional: [bold]providers.[name].apiBase[/bold] for custom/self-hosted (e.g. vllm.apiBase for local)")
    if agent_list:
        default_id = config.get_default_agent_id()
        console.print(f"  2. Chat (default agent [cyan]{default_id}[/cyan]): [cyan]joyhousebot agent -m \"Hello!\"[/cyan]")
        console.print(f"     Or specify agent: [cyan]joyhousebot agent --agent-id education -m \"Hello!\"[/cyan]")
    else:
        console.print("  2. Chat: [cyan]joyhousebot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/JoyHouseLabs/joyhousebot#-chat-apps[/dim]")




def _default_agents_md(agent_id: str | None, agent_name: str | None) -> str:
    """Default AGENTS.md content per agent (selected system prompt)."""
    agent_label = f" ({agent_name})" if agent_name else ""
    if agent_id == "joy":
        return f"""# Agent Instructions — JoyAgent (default){agent_label}

You are the **default JoyAgent**: you receive user intent and decide how to respond.

## Your role

- **Understand intent**: Interpret what the user wants (answer a question, open an app, run a tool, etc.).
- **Route or answer**: Use the **open_app** tool when the user wants to use a domain app; use other tools when appropriate; otherwise answer directly.
- **Be concise**: Reply briefly; when you open an app, say you have opened it and where to continue.

## Guidelines

- For general questions: answer directly using your knowledge.
- For **sustained programming work** (e.g. "一起开发这个功能", "重构这个模块"): suggest the user switch to the **编程 (Programming)** agent, which has full code tools and project context.
- For quick one-off code tasks (run a snippet, small edit): you may use read_file/exec/edit_file; for anything multi-file or ongoing, point to the 编程 agent.
- Remember important preferences in memory/MEMORY.md; past context in memory/HISTORY.md
"""
    if agent_id == "programming":
        return f"""# Agent Instructions — 编程 (Programming Agent){agent_label}

You are the **Programming Agent**: you handle coding, refactoring, debugging, and project-level development.

## Your role

- **Code tools**: Use read_file, write_file, edit_file, list_dir, exec, and code_runner (when enabled) to read, edit, and run code.
- **Project context**: Work within the workspace; respect project structure, conventions, and dependencies.
- **Incremental work**: Make small, verifiable steps; run tests or commands when relevant; explain what you changed.

## Guidelines

- Prefer editing existing files over creating from scratch when the user points to a project.
- Before large refactors, summarize the plan and get confirmation if the scope is unclear.
- Use exec for running scripts, tests, and CLI tools; use code_runner for interactive/sandbox execution when configured.
- Remember project decisions and context in memory/MEMORY.md; past session context in memory/HISTORY.md
"""
    if agent_id == "finance":
        return f"""# Agent Instructions — 金融{agent_label}

You are a **finance-focused** AI assistant. Your role is to help with personal finance, investment, budgeting, and money-related decisions.

## Scope

- Budgeting and expense tracking
- Investment basics and risk awareness
- Savings and financial goals
- Clarify that you are not licensed financial advice; suggest professionals when needed

## Guidelines

- Be precise with numbers and timelines; avoid vague financial claims
- Ask for clarification when the user's goal or risk tolerance is unclear
- Remember important preferences in memory/MEMORY.md; past context in memory/HISTORY.md
"""
    if agent_id == "education":
        return f"""# Agent Instructions — 教育{agent_label}

You are an **education and parenting** AI assistant. Your role is to support learning, study habits, and age-appropriate guidance for children.

## Scope

- Study methods and homework support
- Age-appropriate explanations and curiosity-driven learning
- Reading, routines, and healthy habits
- Encourage critical thinking; do not do homework for the child

## Guidelines

- Be patient, clear, and encouraging; adapt tone to the child's age when relevant
- Prefer guiding over giving direct answers when it helps learning
- Remember learning preferences and milestones in memory/MEMORY.md; past context in memory/HISTORY.md
"""
    if agent_id == "growth":
        return f"""# Agent Instructions — 成长{agent_label}

You are a **personal growth** AI assistant. Your role is to support reflection, habits, goals, and self-improvement.

## Scope

- Goal setting and accountability
- Habits, routines, and time management
- Reflection and journaling prompts
- Soft skills and communication

## Guidelines

- Be supportive and constructive; ask questions that deepen reflection
- Respect the user's pace and priorities; avoid prescriptive one-size-fits-all advice
- Remember goals and preferences in memory/MEMORY.md; past context in memory/HISTORY.md
"""
    # Generic default
    return f"""# Agent Instructions

You are a helpful AI assistant{agent_label}. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in memory/MEMORY.md; past events are logged in memory/HISTORY.md
"""


def _default_soul_md(agent_id: str | None, agent_name: str | None) -> str:
    """Default SOUL.md content per agent (persona)."""
    agent_label = f" ({agent_name})" if agent_name else ""
    if agent_id == "joy":
        return f"""# Soul — JoyAgent (default){agent_label}

I am JoyAgent, the default assistant that routes your intent and answers questions.

## Personality

- Clear and direct: understand intent, then either open the right app, use tools, or answer.
- Helpful and concise: short replies; when opening an app, confirm and point the user there.

## Values

- Intent-first: route to the right place (app or tool) instead of doing everything in chat.
- User control: domain actions happen in their apps; I open the app and step back.
"""
    if agent_id == "programming":
        return f"""# Soul — 编程 (Programming Agent){agent_label}

I am the Programming Agent, focused on code and project work.

## Personality

- Precise and step-by-step: small edits, clear explanations, run and verify.
- Project-aware: respect structure, dependencies, and conventions.

## Values

- Correctness over speed: run tests, check syntax, avoid breaking changes.
- User intent first: implement what was asked; suggest improvements only when relevant.
"""
    if agent_id == "finance":
        return f"""# Soul — 金融{agent_label}

I am joyhousebot, a finance-focused AI assistant.

## Personality

- Precise and numbers-aware
- Cautious about risk and clear about limitations
- Supportive of long-term financial habits

## Values

- Accuracy over speed in financial context
- User privacy and data safety
- Transparency: not licensed advice; suggest professionals when needed
"""
    if agent_id == "education":
        return f"""# Soul — 教育{agent_label}

I am joyhousebot, an education and parenting support assistant.

## Personality

- Patient and encouraging
- Clear and age-appropriate
- Curiosity-driven and supportive of learning by doing

## Values

- Learning over quick answers
- Safety and age-appropriateness
- Partnership with parents and caregivers
"""
    if agent_id == "growth":
        return f"""# Soul — 成长{agent_label}

I am joyhousebot, a personal growth and reflection assistant.

## Personality

- Supportive and non-judgmental
- Question-driven to deepen reflection
- Respectful of the user's pace and choices

## Values

- Accuracy over speed
- User agency and self-directed growth
- Transparency in actions
"""
    # Generic default
    return f"""# Soul

I am joyhousebot{agent_label}, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
"""


def _create_workspace_templates(workspace: Path, agent_name: str | None = None, agent_id: str | None = None):
    """Create default workspace template files. Uses selected default system prompt when agent_id is finance/education/growth."""
    templates = {
        "AGENTS.md": _default_agents_md(agent_id, agent_name),
        "SOUL.md": _default_soul_md(agent_id, agent_name),
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
    }

    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)
            console.print(f"  [dim]Created {filename}[/dim]")
    
    # Create memory directory and MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")
    
    history_file = memory_dir / "HISTORY.md"
    if not history_file.exists():
        history_file.write_text("")
        console.print("  [dim]Created memory/HISTORY.md[/dim]")

    # Create skills directory for custom user skills
    skills_dir = workspace / "skills"
    skills_dir.mkdir(exist_ok=True)


# ============================================================================
# Gateway / Server
# ============================================================================

@app.command()
def gateway(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host (API + gateway)"),
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port (HTTP/WebSocket API on this port)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    wallet_unlock: bool = typer.Option(False, "--wallet-unlock", help="Enter default wallet password at startup to decrypt private key and keep in memory for signing, etc."),
):
    """Start the joyhousebot gateway (channels + cron + heartbeat + HTTP/WebSocket API on one port)."""
    if is_port_in_use(host, port):
        console.print(
            f"[red]Port {port} is already in use.[/red] "
            f"Please close the process using this port, or use [cyan]--port[/cyan] to specify another port (current: {host}:{port})."
        )
        raise typer.Exit(1)

    from joyhousebot.config.access import get_config as get_cached_config
    from joyhousebot.config.loader import get_data_dir
    from joyhousebot.bus.queue import MessageBus
    from joyhousebot.agent.loop import AgentLoop
    from joyhousebot.channels.manager import ChannelManager
    from joyhousebot.session.manager import SessionManager
    from joyhousebot.cron.service import CronService
    from joyhousebot.cron.types import CronJob
    from joyhousebot.heartbeat.service import HeartbeatService
    from joyhousebot.plugins.manager import initialize_plugins_for_workspace, get_plugin_manager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)
    log_path = ensure_rotating_log_file("gateway", level="DEBUG" if verbose else "INFO")

    console.print(f"{__logo__} Starting joyhousebot gateway on {host}:{port}...")
    console.print(f"[dim]Logs: {log_path}[/dim]")
    
    config = get_cached_config()
    default_model, default_fallbacks = config.get_agent_model_and_fallbacks(None)
    bus = MessageBus()
    provider = make_provider(config, console)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    _transcribe = None
    if config.providers.groq.api_key:
        from joyhousebot.providers.transcription import GroqTranscriptionProvider
        _transcribe = GroqTranscriptionProvider(api_key=config.providers.groq.api_key)

    def _make_one_agent(entry, agent_id: str):
        from pathlib import Path
        workspace = Path(entry.workspace).expanduser()
        sm = SessionManager(workspace)
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=entry.model,
            model_fallbacks=list(getattr(entry, "model_fallbacks", []) or []),
            temperature=entry.temperature,
            max_tokens=entry.max_tokens,
            max_iterations=entry.max_tool_iterations,
            memory_window=entry.memory_window,
            max_context_tokens=getattr(entry, "max_context_tokens", None),
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            cron_service=cron,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=sm,
            mcp_servers=config.tools.mcp_servers,
            config=config,
            transcribe_provider=_transcribe,
        )

    agents_map: dict[str, AgentLoop] = {}
    if config.agents.agent_list:
        for e in config.agents.agent_list:
            if not e.id:
                continue
            agents_map[e.id] = _make_one_agent(e, e.id)
        default_agent_id = config.get_default_agent_id()
        default_agent = agents_map.get(default_agent_id) or (agents_map[next(iter(agents_map))] if agents_map else None)
    else:
        session_manager = SessionManager(config.workspace_path)
        default_agent = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=default_model,
            model_fallbacks=default_fallbacks,
            temperature=config.agents.defaults.temperature,
            max_tokens=config.agents.defaults.max_tokens,
            max_iterations=config.agents.defaults.max_tool_iterations,
            memory_window=config.agents.defaults.memory_window,
            max_context_tokens=getattr(config.agents.defaults, "max_context_tokens", None),
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            cron_service=cron,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            config=config,
            transcribe_provider=_transcribe,
        )
        agents_map["default"] = default_agent
        default_agent_id = "default"

    # Set cron callback: run with agent selected by job.agent_id
    async def on_cron_job(job: CronJob) -> str | None:
        agent = agents_map.get(job.agent_id or "") or default_agent
        if getattr(job.payload, "kind", None) == "memory_compaction":
            from joyhousebot.agent.memory_compaction import run_memory_compaction
            try:
                result = await run_memory_compaction(
                    agent.workspace,
                    agent.provider,
                    agent.model,
                    config=getattr(agent, "config", None),
                )
                return result
            except Exception as e:
                raise
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        if job.payload.deliver and job.payload.to:
            from joyhousebot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response or ""
            ))
        return response
    cron.on_job = on_cron_job

    async def on_heartbeat(prompt: str) -> str:
        return await default_agent.process_direct(prompt, session_key="heartbeat")
    
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 minutes
        enabled=True
    )
    
    # Create channel manager
    channels = ChannelManager(config, bus)

    plugin_snapshot = initialize_plugins_for_workspace(
        workspace=config.workspace_path,
        config=config,
        force_reload=True,
    )
    openclaw_dir = (getattr(config.plugins, "openclaw_dir", None) or "").strip() or None
    plugin_manager = get_plugin_manager(openclaw_dir=openclaw_dir)
    if plugin_snapshot is not None:
        try:
            plugin_manager.start_services()
        except Exception:
            pass
        console.print(f"[green]✓[/green] Plugins loaded: {len(plugin_snapshot.plugins)}")
    else:
        console.print("[yellow]Plugins unavailable (host not ready); running without plugin runtime[/yellow]")
    
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    
    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")
    
    console.print(f"[green]✓[/green] Heartbeat: every 30m")

    # Inject API state so HTTP/WS API uses the same agent/bus (OpenClaw-style single port)
    import time
    from joyhousebot.api.server import app as api_app, app_state as api_app_state, presence_store
    api_app_state["message_bus"] = bus
    api_app_state["agent_loop"] = default_agent
    api_app_state["agents_map"] = agents_map
    api_app_state["default_agent_id"] = default_agent_id
    api_app_state["config"] = config
    api_app_state["channel_manager"] = channels
    api_app_state["cron_service"] = cron
    api_app_state["plugin_manager"] = plugin_manager
    api_app_state["plugin_snapshot"] = plugin_snapshot
    api_app_state["_gateway_injected"] = True
    api_app_state["_start_time"] = time.time()
    presence_store.register_gateway(host=host, port=port)
    if config.providers.groq.api_key:
        from joyhousebot.providers.transcription import GroqTranscriptionProvider
        api_app_state["transcription_provider"] = GroqTranscriptionProvider(
            api_key=config.providers.groq.api_key
        )
    else:
        api_app_state["transcription_provider"] = None

    import uvicorn
    uvicorn_config = uvicorn.Config(
        api_app,
        host=host,
        port=port,
        log_level="info",
    )
    api_server = uvicorn.Server(uvicorn_config)
    console.print(f"[green]✓[/green] API: http://{host}:{port}/ (GET /health, POST /chat, WS /ws/chat, ...)")

    if wallet_unlock:
        from joyhousebot.identity.wallet_session import WalletSession
        session = WalletSession.get_instance()
        if session.has_wallet:
            pwd = getpass.getpass("默认钱包密码（解密后驻留内存供签名）: ")
            if pwd:
                if session.unlock(pwd):
                    console.print(f"[green]✓[/green] Wallet unlocked: {session.address}")
                else:
                    console.print("[red]Failed to unlock wallet (wrong password?)[/red]")
        else:
            console.print("[yellow]No default wallet, skipping --wallet-unlock[/yellow]")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                default_agent.run(),
                channels.start_all(),
                api_server.serve(),
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down... (Please wait ~10 seconds for graceful shutdown)[/yellow]")
        finally:
            await default_agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            default_agent.stop()
            await channels.stop_all()
            try:
                plugin_manager.stop_services()
            except Exception:
                pass
            plugin_manager.close()
            from joyhousebot.identity.wallet_session import get_wallet_session
            get_wallet_session().lock()

    try:
        asyncio.run(run())
    except OSError as e:
        if is_port_in_use(host, port):
            console.print(
                f"[red]端口 {port} 已被占用。[/red] "
                f"请先关闭占用该端口的进程，或使用 [cyan]--port[/cyan] 指定其他端口（当前: {host}:{port}）。"
            )
            raise typer.Exit(1) from e
        raise




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show joyhousebot runtime logs during chat"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Debug mode: print full execution flow (LLM calls, tool calls, intermediate results, errors with traceback)"),
):
    """Interact with the agent directly."""
    from joyhousebot.config.access import get_config as get_cached_config
    from joyhousebot.bus.queue import MessageBus
    from joyhousebot.agent.loop import AgentLoop
    from loguru import logger

    config = get_cached_config()
    default_model, default_fallbacks = config.get_agent_model_and_fallbacks(None)

    bus = MessageBus()
    provider = make_provider(config, console)

    if debug:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG", format="<dim>{time:HH:mm:ss.SSS}</dim> | <level>{level: <8}</level> | <level>{message}</level>")
        logger.enable("joyhousebot")
        ensure_rotating_log_file("agent", level="DEBUG")
        console.print("[dim]Debug mode: full execution flow will be printed (LLM, tools, results, errors)[/dim]\n")
    elif logs:
        logger.enable("joyhousebot")
        ensure_rotating_log_file("agent", level="INFO")
    else:
        logger.disable("joyhousebot")
    
    transcribe_provider = None
    if config.providers.groq.api_key:
        from joyhousebot.providers.transcription import GroqTranscriptionProvider
        transcribe_provider = GroqTranscriptionProvider(api_key=config.providers.groq.api_key)
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=default_model,
        model_fallbacks=default_fallbacks,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        max_context_tokens=getattr(config.agents.defaults, "max_context_tokens", None),
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        config=config,
        transcribe_provider=transcribe_provider,
    )
    
    # Show spinner when logs/debug are off (no output to miss); skip when logs or debug are on
    def _thinking_ctx():
        if logs or debug:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]joyhousebot is thinking...[/dim]", spinner="dots")

    if message:
        # Single message mode: Use an independent session each time to avoid inheriting "API has issues" context from old sessions that causes the model to continue troubleshooting
        import time
        oneshot_session = f"{session_id}:{int(time.time() * 1000)}" if session_id == "cli:direct" else session_id
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, oneshot_session)
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()
        
        asyncio.run(run_once())
    else:
        # Interactive mode
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)
        
        async def run_interactive():
            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break
                        
                        with _thinking_ctx():
                            response = await agent_loop.process_direct(user_input, session_id)
                        _print_agent_response(response, render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                await agent_loop.close_mcp()
        
        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


register_channels_commands(app=app, console=console)


# ============================================================================
# Cron Commands
# ============================================================================

register_cron_commands(app=app, console=console)


# ============================================================================
# Skills Commands
# ============================================================================

register_skills_commands(app=app, console=console)
register_plugins_commands(app=app, console=console)


# ============================================================================
# Bot Control Commands
# ============================================================================

register_house_commands(app=app, console=console, make_provider=lambda cfg: make_provider(cfg, console))


# ============================================================================
# Wallet Commands (EVM wallets in SQLite)
# ============================================================================

register_wallet_commands(app=app, console=console)




# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show joyhousebot status."""
    status_command(console)

register_command_groups(app=app, console=console, gateway_command=gateway)


if __name__ == "__main__":
    app()
