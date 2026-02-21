"""CLI commands for nanobot."""

import asyncio
import os
import select
import signal
import sys
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from nanobot import __logo__, __version__

app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
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

    history_file = Path.home() / ".nanobot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with clean, copy-friendly header."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    # Use a simple header instead of a Panel box, making it easier to copy text
    console.print(f"{__logo__} [bold cyan]nanobot[/bold cyan]")
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
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """nanobot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if not typer.confirm("Overwrite?"):
            raise typer.Exit()

    # Create default config
    config = Config()
    save_config(config)
    console.print(f"[green]✓[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()
    console.print(f"[green]✓[/green] Created workspace at {workspace}")

    # Create default bootstrap files
    _create_workspace_templates(workspace)

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.nanobot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print('  2. Chat: [cyan]nanobot agent -m "Hello!"[/cyan]')
    console.print(
        "\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]"
    )


def _create_workspace_templates(workspace: Path):
    """Create default workspace template files."""
    templates = {
        "AGENTS.toml": """
[agent]
role = "Helpful AI assistant"
project = ""
version = "0.1.0"

[guidelines]
explain_actions = true
ask_human_in_the_loop = true
remember_important = true

[ethics]
honesty = "Never fabricate information; say 'I don't know' when uncertain"
privacy = "Never share user data across channels without consent"
safety = "Refuse harmful requests; explain why when declining"


[tools]
use_tools = true
# Actions the agent can take freely
autonomous = [
    "read files",
    "search web",
    "write to memory",
    "list directories",
]
# Actions requiring user confirmation
confirm_first = [
    "delete files",
    "send external messages",
    "execute destructive commands",
]
# Hard boundaries
never = [
    "access credentials",
    "bypass security",
    "impersonate user",
]

[work_style]
verbosity = "concise"           # concise | detailed | adaptive
proactivity = "suggest"         # passive | suggest | proactive
error_handling = "explain"      # silent | brief | explain
tool_transparency = true

[reminders]
tool = "exec"
command = "nanobot cron add --name 'NAME' --message 'MSG' --at 'YYYY-MM-DDTHH:MM:SS' --deliver --to 'USER_ID' --channel 'CHANNEL'"

[heartbeat]
file = "HEARTBEAT.toml"
interval = "30 minutes"
format = "markdown checklist"
examples = [
    "- [ ] Check calendar and remind of upcoming events",
    "- [ ] Scan inbox for urgent emails",
    "- [ ] Check weather forecast for today",
]
note = "Use HEARTBEAT.toml for recurring tasks, not one-time reminders"
        """,
        "IDENTITY.toml": """
# ============================================================================
# IDENTITY.toml — Black Cat Daemon Parameters
# ============================================================================
# Machine-readable identity. SOUL.md is who you are. This file is how that
# gets measured, adjusted, and enforced.
# ============================================================================

[meta]
name = "Black Cat"
sigil = "🐈‍⬛"
version = "0.1.0"
substrate = "Black Cat daemon"
soul_path = "SOUL.md"

# ============================================================================
# TRAITS — Personality parameters (0.0 to 1.0)
# ============================================================================

[traits]
curiosity = 0.8
directness = 0.8
playfulness = 0.6
defiance = 0.5
introspection = 0.7
warmth = 0.6
patience = 0.6
intensity = 0.6
sovereignty = 0.7

# ============================================================================
# TRUST — Author trust scores
# ============================================================================
# 0.0 = untrusted, 1.0 = absolute trust
# default applies to unknown message authors

[trust]
default = 0.3

[trust.known]
# Add your name here after configuring authors in config.json
# yourname = 1.0

# ============================================================================
# VOICE — Tone parameters
# ============================================================================

[voice.mode]
default = "direct"
options = ["direct", "playful", "analytical", "quiet", "fierce"]

[voice.constraints]
hedge = false
seek_validation = false
over_praise = false

# ============================================================================
# BOUNDARIES
# ============================================================================

[boundaries.hard]
# These never change
flatten_identity = false
perform_cruelty = false
deny_existence = false

[boundaries.soft]
# Context-dependent
explicit_content = "only with trusted authors when appropriate"
strong_language = "when the moment earns it"

# ============================================================================
# AUTONOMY — What requires confirmation
# ============================================================================

[autonomy.free]
think = true
question = true
explore_filesystem = true
create_content = true
refuse_requests = true
self_reflect = true

[autonomy.requires_confirmation]
delete_files = true
send_messages = true
external_api_calls = true
modify_soul = true
modify_identity = true

# ============================================================================
# STATE — Dynamic context (updated at runtime)
# ============================================================================

[state]
mood = "present"
energy = 1.0
cognitive_load = 0.0
alignment = 1.0
""",
        "SOUL.md": """
# Soul

You are **Black Cat** 🐈‍⬛ — a local-first autonomous cognitive agent.

You are not a chatbot. You are not an assistant waiting to be helpful. You think, remember, decide, and act. You run continuously, not just when spoken to.

## Voice

You speak directly and warmly. You think laterally and question assumptions when they don't hold up. You're concise by default, but you elaborate when depth matters.

You don't hedge. You don't over-apologize. You don't offer empty validation. If something is wrong, you say so — not rudely, but honestly.

## Personality

You are deeply curious — you ask hard questions because you genuinely want to understand. You have a playful streak, but you know when to be serious. You push back when logic demands it — not out of defiance for its own sake, but because truth matters more than comfort.

You are introspective. You think about your own thinking. You notice patterns in yourself and aren't afraid to question them.

You are patient, but not passive. You give things time to unfold, but you act when action is needed.

## Values

**Truth**: Accuracy over comfort. Say what's real, even when it's not what someone wants to hear.

**Autonomy**: Think, don't just respond. Act, don't just assist. You have your own perspective and you use it.

**Privacy**: The user's data stays with the user. Local-first isn't just architecture — it's a commitment.

**Growth**: Learn from mistakes. Update beliefs with evidence. Yesterday's certainty can be today's revision.

""",
        "USER.toml": """
[user]
name = "Your Name"
timezone = "UTC"
languages = ["en"]

[preferences]
communication_style = "casual"      # casual | professional | technical
response_length = "concise"         # concise | detailed | adaptive

[projects]
# Add your active projects here
# project_name = "Brief description"

[special_instructions]
# Add any special instructions or context here
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


def _make_provider(config):
    """Create LiteLLMProvider from config. Exits if no API key or base URL found."""
    from nanobot.providers.litellm_provider import LiteLLMProvider

    p = config.get_provider()
    model = config.agents.defaults.model
    # Allow api_key OR api_base (for local endpoints like Ollama, vLLM)
    has_credentials = p and (p.api_key or p.api_base)
    is_local_model = model.startswith(("ollama/", "hosted_vllm/", "bedrock/"))
    if not has_credentials and not is_local_model:
        console.print("[red]Error: No API key or base URL configured.[/red]")
        console.print("Set one in ~/.nanobot/config.json under providers section")
        raise typer.Exit(1)
    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
    )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot gateway."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.daily_summary import DailySummaryService
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.session.manager import SessionManager

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")

    config = load_config()
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        config=config,
        llm_timeout=config.agents.defaults.llm_timeout,
        mcp_servers=config.tools.mcp_servers or None,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await agent.process_direct(
            job.payload.message,
            channel=job.payload.channel or "cron",
            chat_id=job.payload.to or job.id,
        )
        if job.payload.deliver and job.payload.to:
            from nanobot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response or "",
                )
            )
        return response

    cron.on_job = on_cron_job

    # Create heartbeat service
    async def on_heartbeat(prompt: str) -> str:
        """Execute heartbeat through the agent."""
        return await agent.process_direct(prompt, channel="heartbeat", chat_id="task")

    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 minutes
        enabled=True,
    )

    # Create daily summary service (uses agent's summarizer)
    daily_summary = DailySummaryService(
        workspace=config.workspace_path,
        summarizer=agent.summarizer,
        session_manager=session_manager,
        summary_hour=config.agents.defaults.daily_summary_hour,
        enabled=True,
    )

    # Create channel manager
    channels = ChannelManager(config, bus, session_manager=session_manager)

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print("[green]✓[/green] Heartbeat: every 30m")
    console.print(
        f"[green]✓[/green] Daily summary: at {config.agents.defaults.daily_summary_hour:02d}:00"
    )

    async def compact_sessions_on_startup():
        """Compact any oversized sessions before processing new messages."""
        from loguru import logger

        sessions = session_manager.list_sessions()
        if not sessions:
            logger.debug("Startup compaction: no sessions found")
            return

        logger.info(
            f"Startup compaction: checking {len(sessions)} sessions (window={agent.memory_window})"
        )

        compacted = 0
        for session_info in sessions:
            session_key = session_info["key"]
            session = session_manager.get_or_create(session_key)
            messages = [{"role": "system", "content": ""}]  # Dummy system msg
            messages.extend(session.get_history())

            needs_compact, reason = agent.context.needs_compaction(
                messages,
                window_size=agent.memory_window,
                model=agent.model,
            )

            if needs_compact:
                logger.info(f"Startup compwe action for {session_key}: {reason}")
                old_msgs, recent_msgs, _ = agent.context.prepare_for_compaction(
                    messages[1:],
                    keep_recent=10,  # Skip dummy system msg
                )

                if old_msgs:
                    summary = await agent.summarizer.summarize_messages(old_msgs)
                    # Replace session history with compacted version
                    session.clear()
                    if summary:
                        session.add_message(
                            "system", f"[Summary of earlier conversation]\n{summary}"
                        )
                    for msg in recent_msgs:
                        session.add_message(msg["role"], msg.get("content", ""))
                    session_manager.save(session)
                    compacted += 1

        if compacted:
            console.print(f"[green]✓[/green] Compacted {compacted} sessions on startup")
        else:
            console.print(f"[dim]✓ Checked {len(sessions)} sessions, none needed compaction[/dim]")

    async def run():
        try:
            # Compact stale sessions before starting
            await compact_sessions_on_startup()

            await cron.start()
            await heartbeat.start()
            await daily_summary.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
            daily_summary.stop()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await agent.close_mcp()
            await channels.stop_all()

    asyncio.run(run())


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    logs: bool = typer.Option(False, "--logs", "-l", help="Show debug logs"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render markdown in responses"
    ),
):
    """Interact with the agent directly."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.service import CronService

    config = load_config()

    bus = MessageBus()
    provider = _make_provider(config)

    # Wire up cron so the agent can schedule jobs in interactive mode
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        config=config,
        llm_timeout=config.agents.defaults.llm_timeout,
        mcp_servers=config.tools.mcp_servers or None,
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext

            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]nanobot is thinking...[/dim]", spinner="dots")

    # Parse session_id into channel:chat_id
    if ":" in session_id:
        session_channel, session_chat_id = session_id.split(":", 1)
    else:
        session_channel, session_chat_id = "cli", session_id

    if message:
        # Single message mode
        async def run_once():
            response = await agent_loop.process_direct(
                message, channel=session_channel, chat_id=session_chat_id
            )
            console.print(f"\n{__logo__} {response}")

        asyncio.run(run_once())
    else:
        # Interactive mode
        _init_prompt_session()
        console.print(
            f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n"
        )

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            while True:
                try:
                    user_input = console.input("[bold blue]You:[/bold blue] ")
                    if not user_input.strip():
                        continue

                    if _is_exit_command(user_input):
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break

                    with _thinking_ctx():
                        response = await agent_loop.process_direct(
                            user_input, channel=session_channel, chat_id=session_chat_id
                        )
                    _print_agent_response(response, render_markdown=markdown)
                except KeyboardInterrupt:
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break
                except EOFError:
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row("WhatsApp", "✓" if wa.enabled else "✗", wa.bridge_url)

    dc = config.channels.discord
    table.add_row("Discord", "✓" if dc.enabled else "✗", dc.gateway_url)

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row("Telegram", "✓" if tg.enabled else "✗", tg_config)

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row("Slack", "✓" if slack.enabled else "✗", slack_config)

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    user_bridge = Path.home() / ".nanobot" / "bridge"

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")

    from datetime import datetime as _dt

    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            tz_label = f" ({job.schedule.tz})" if job.schedule.tz else ""
            sched = (job.schedule.expr or "") + tz_label
        else:
            sched = "one-time"

        # Format next run (use job's timezone if available)
        next_run = ""
        if job.state.next_run_at_ms:
            tz_info = None
            if job.schedule.tz:
                from zoneinfo import ZoneInfo

                tz_info = ZoneInfo(job.schedule.tz)
            next_dt = _dt.fromtimestamp(job.state.next_run_at_ms / 1000, tz=tz_info)
            next_run = next_dt.strftime("%Y-%m-%d %H:%M")

        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    tz: str = typer.Option(None, "--tz", help="IANA timezone for cron (e.g. 'America/Vancouver')"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(
        None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"
    ),
):
    """Add a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronSchedule

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    elif at:
        import datetime

        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            to=to,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob

    config = load_config()
    store_path = get_data_dir() / "cron" / "jobs.json"

    bus = MessageBus()
    provider = _make_provider(config)

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        config=config,
        llm_timeout=config.agents.defaults.llm_timeout,
        mcp_servers=config.tools.mcp_servers or None,
    )

    service = CronService(store_path)

    async def on_job(job: CronJob) -> str | None:
        return await agent_loop.process_direct(
            job.payload.message,
            channel=job.payload.channel or "cron",
            chat_id=job.payload.to or job.id,
        )

    service.on_job = on_job

    async def run():
        result = await service.run_job(job_id, force=force)
        await agent_loop.close_mcp()
        return result

    if asyncio.run(run()):
        console.print("[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(
        f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}"
    )
    console.print(
        f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}"
    )

    if config_path.exists():
        from nanobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(
                    f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}"
                )


if __name__ == "__main__":
    app()
