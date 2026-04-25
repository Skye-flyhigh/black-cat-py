"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from blackcat.agent.handler import MessageHandler
from blackcat.agent.hook import AgentHook, AgentHookContext, CompositeHook
from blackcat.agent.runner import AgentRunner, AgentRunSpec
from blackcat.agent.tools.lens import (
    LensCodeActionTool,
    LensCompletionTool,
    LensDefinitionTool,
    LensDiagnosticsTool,
    LensDocumentSymbolTool,
    LensFormatTool,
    LensHoverTool,
    LensReferencesTool,
    LensRenameTool,
    LensSignatureHelpTool,
    LensWorkspaceSymbolTool,
)
from blackcat.agent.tools.notebook import NotebookEditTool
from blackcat.agent.tools.search import GlobTool, GrepTool
from blackcat.command.router import CommandRouter
from blackcat.config.schema import ChannelsConfig, ExecToolConfig, WebToolsConfig
from blackcat.utils.document import extract_documents

if TYPE_CHECKING:
    from blackcat.config.schema import Config
    from blackcat.cron.service import CronService
    from blackcat.session.manager import Session

from blackcat.agent.context import ContextManager
from blackcat.agent.subagent import SubagentManager
from blackcat.agent.summarizer import Summarizer
from blackcat.agent.tools.cron import CronTool
from blackcat.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from blackcat.agent.tools.message import MessageTool
from blackcat.agent.tools.registry import ToolRegistry
from blackcat.agent.tools.shell import ExecTool
from blackcat.agent.tools.skills import (
    SkillCreateTool,
    SkillGetReferenceTool,
    SkillGetTool,
    SkillListReferencesTool,
    SkillListTool,
    SkillUpdateTool,
)
from blackcat.agent.tools.spawn import SpawnTool
from blackcat.agent.tools.web import WebFetchTool, WebSearchTool
from blackcat.bus.events import InboundMessage, OutboundMessage
from blackcat.bus.queue import MessageBus
from blackcat.providers.base import LLMProvider
from blackcat.session.manager import SessionManager

_MAX_INJECTIONS_PER_TURN = 10  # Default limit for mid-turn message injections

class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: "AgentLoop",
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> None:
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        prev_clean = AgentLoop._strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = AgentLoop._strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean):] if new_clean and prev_clean else (new_clean or "")
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = AgentLoop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = AgentLoop._strip_think(AgentLoop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(self._channel, self._chat_id, self._message_id)

    async def after_iteration(self, context: AgentHookContext) -> None:
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return AgentLoop._strip_think(content)


class _LoopHookChain(AgentHook): # FIXME: find out what LoopHookChain is
    """Run the core hook before extra hooks."""

    __slots__ = ("_primary", "_extras")

    def __init__(self, primary: AgentHook, extra_hooks: list[AgentHook]) -> None:
        self._primary = primary
        self._extras = CompositeHook(extra_hooks)

    def wants_streaming(self) -> bool:
        return self._primary.wants_streaming() or self._extras.wants_streaming()

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._primary.before_iteration(context)
        await self._extras.before_iteration(context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._primary.on_stream(context, delta)
        await self._extras.on_stream(context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._primary.on_stream_end(context, resuming=resuming)
        await self._extras.on_stream_end(context, resuming=resuming)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._primary.before_execute_tools(context)
        await self._extras.before_execute_tools(context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._primary.after_iteration(context)
        await self._extras.after_iteration(context)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        content = self._primary.finalize_content(context, content)
        return self._extras.finalize_content(context, content)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _PENDING_USER_TURN_KEY = "pending_user_turn"

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        context: ContextManager | None = None,
        model: str | None = None,
        max_iterations: int = 20,
        max_tool_result_chars: int = 50000,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        provider_retry_mode: str = "standard",
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        config: Config | None = None,
        llm_timeout: int | None = 60,
        mcp_servers: dict | None = None,
        reasoning_effort: str | None = None,
        hooks: list[AgentHook] | None = None,
        channels_config: ChannelsConfig | None = None,
        web_config: WebToolsConfig | None = None,
        timezone: str | None = None,
    ):

        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self.context_window_tokens = context_window_tokens
        self.context_block_limit = context_block_limit
        self.channels_config = channels_config
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.web_config= web_config or WebToolsConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.config = config
        self.llm_timeout = llm_timeout
        self.reasoning_effort = reasoning_effort
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []

        # Summarizer for context compaction (created first, passed to ContextManager)
        summarizer_model = config.agents.defaults.summarizer_model if config else None
        self.summarizer = Summarizer(
            provider=provider,
            model=summarizer_model,  # Falls back to main model if None
            timeout=llm_timeout,
        )

        self.context = context or ContextManager(workspace, summarizer=self.summarizer)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)

        if config and config.tools.lens.enabled:
            from blackcat.lens import LensClient
            self.lens_client = LensClient(config.tools.lens)
            self.context.set_lens_client(self.lens_client)
            logger.info("Lens LSP client initialized with {} workspaces", len(config.tools.lens.workspaces))
        else:
            self.lens_client = None
            if config:
                logger.debug("Lens disabled: config.tools.lens.enabled = {}", config.tools.lens.enabled)
            else:
                logger.debug("Lens disabled: no config provided")

        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            max_tool_result_chars=self.max_tool_result_chars,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self.memory_window = config.agents.defaults.memory_window if config else 51
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._pending_queues: dict[str, asyncio.Queue] = {}
        # BLACKCAT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("BLACKCAT_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.commands = CommandRouter()

        # Unified session for simplified task management
        self._unified_session = config.agents.defaults.unified_session if config else False


        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (workspace for relative paths, restrict if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool, GrepTool, GlobTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))

        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy)
            )
            self.tools.register(WebFetchTool(proxy=self.web_config.proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone= "UTC")
            )

        # lens tools (for code intelligence)
        if self.lens_client and self.config and self.config.tools.lens.enabled:
            self.tools.register(LensDefinitionTool(self.lens_client))
            self.tools.register(LensReferencesTool(self.lens_client))
            self.tools.register(LensHoverTool(self.lens_client))
            # Pass workspace configs to diagnostics tool for per-workspace overrides
            self.tools.register(LensDiagnosticsTool(
                self.lens_client,
                default_source=self.config.tools.lens.diagnostics_source,
            ))
            self.tools.register(LensWorkspaceSymbolTool(self.lens_client))
            self.tools.register(LensDocumentSymbolTool(self.lens_client))
            self.tools.register(LensCompletionTool(self.lens_client))
            self.tools.register(LensRenameTool(self.lens_client))
            self.tools.register(LensCodeActionTool(self.lens_client))
            self.tools.register(LensFormatTool(self.lens_client))
            self.tools.register(LensSignatureHelpTool(self.lens_client))

        # Skill management tools
        self.tools.register(SkillListTool(workspace=self.workspace))
        self.tools.register(SkillGetTool(workspace=self.workspace))
        self.tools.register(SkillCreateTool(workspace=self.workspace))
        self.tools.register(SkillUpdateTool(workspace=self.workspace))
        self.tools.register(SkillListReferencesTool(workspace=self.workspace))
        self.tools.register(SkillGetReferenceTool(workspace=self.workspace))

        self.tools.export_md(self.workspace / "TOOLS.md")

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (lazy, one-time).

        Called on first message. If connection fails, retries on next message.
        """
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from blackcat.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stacks = await connect_mcp_servers(self._mcp_servers, self.tools)
            if self._mcp_stacks:
                self._mcp_connected = True
                self.tools.export_mcp_md(self.workspace / "MCP.md")
                logger.info("MCP tools exported to MCP.md")
            else:
                logger.warning('No MCP servers connected succesfully (will retry next message)')

        except asyncio.CancelledError:
            logger.warning("MCP connection cancelled (will retry next message)")
            self._mcp_stacks.clear()
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            self._mcp_stacks.clear()
        finally:
            self._mcp_connecting = False


    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            tool = self.tools.get(name)
            if tool and hasattr(tool, "set_context"):
                if name == "message":
                    tool.set_context(channel, chat_id, message_id)  # type: ignore[union-attr]
                else:
                    tool.set_context(channel, chat_id)  # type: ignore[union-attr]

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            # Dispatch message to _dispatch which handles session management
            task = asyncio.create_task(self._dispatch(msg))
            session_key = msg.session_key
            self._active_tasks.setdefault(session_key, []).append(task)
            task.add_done_callback(
                lambda t, k=session_key: self._active_tasks.get(k, [])
                and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k, [])
                else None
            )
    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent.

        Responsibilities:
        - Session management (get/create, lock per session)
        - Checkpoint restore (before processing) and save (after tools)
        - Call MessageHandler for origin parsing, document extraction, response formatting
        - Persistence via _save_turn after handler returns
        - Pending queue drainage for mid-turn message injection
        """
        session_key = msg.session_key
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()

        try:
            async with lock, gate:
                # Get or create session and restore checkpoint if needed
                session = self.sessions.get_or_create(session_key)
                if self._restore_runtime_checkpoint(session):
                    self.sessions.save(session)
                    logger.info("Restored runtime checkpoint for session {}", session_key)

                try:
                    on_stream: Callable[[str], Awaitable[None]] | None = None
                    on_stream_end: Callable[..., Awaitable[None]] | None = None
                    if msg.metadata.get("_wants_stream"):
                        # Split one answer into distinct stream segments.
                        stream_base_id = f"{session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        async def _on_stream(delta: str) -> None:
                            meta = dict(msg.metadata or {})
                            meta["_stream_delta"] = True
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content=delta,
                                metadata=meta,
                            ))

                        async def _on_stream_end(*, resuming: bool = False) -> None:
                            nonlocal stream_segment
                            meta = dict(msg.metadata or {})
                            meta["_stream_end"] = True
                            meta["_resuming"] = resuming
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="",
                                metadata=meta,
                            ))
                            stream_segment += 1

                        on_stream = _on_stream
                        on_stream_end = _on_stream_end

                    # Use MessageHandler for origin parsing, document extraction, response formatting
                    handler = MessageHandler(self, msg)
                    response = await handler.process(
                        session_key=session_key,
                        on_stream=on_stream,
                        on_stream_end=on_stream_end,
                    )

                    # Save turn state for crash recovery
                    await self._save_turn(session)

                    if response is not None:
                        await self.bus.publish_outbound(response)
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="", metadata=msg.metadata or {},
                        ))
                except asyncio.CancelledError:
                    logger.info("Task cancelled for session {}", session_key)
                    # Preserve partial context from the interrupted turn
                    if self._restore_runtime_checkpoint(session):
                        self.sessions.save(session)
                        logger.info("Restored partial context for cancelled session {}", session_key)
                    raise
                except Exception:
                    logger.exception("Error processing message for session {}", session_key)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))
        finally:
            # Clear checkpoint after successful completion
            if session:
                self._clear_runtime_checkpoint(session)
                self.sessions.save(session)

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        for name, stack in self._mcp_stacks.items():
            try:
                await stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
        self._mcp_stacks.clear()

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")


    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>...</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""

        def _fmt(tc):
            args = tc.arguments
            if isinstance(args, list) and args:
                args = args[0]
            val = next(iter(args.values()), None) if isinstance(args, dict) and args else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}...")' if len(val) > 40 else f'{tc.name}("{val}")'

        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
        )

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Drain follow-up messages from the pending queue.

            When no messages are immediately available but sub-agents
            spawned in this dispatch are still running, blocks until at
            least one result arrives (or timeout).  This keeps the runner
            loop alive so subsequent sub-agent completions are consumed
            in-order rather than dispatched separately.
            """
            if pending_queue is None:
                return []

            def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                runtime_ctx = self.context._build_runtime_context(
                    pending_msg.channel,
                    pending_msg.chat_id,
                    self.context.timezone,
                )
                if isinstance(user_content, str):
                    merged: str | list[dict[str, Any]] = f"{runtime_ctx}\n\n{user_content}"
                else:
                    merged = [{"type": "text", "text": runtime_ctx}] + user_content
                return {"role": "user", "content": merged}

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    items.append(_to_user_message(pending_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            # Block if nothing drained but sub-agents spawned in this dispatch
            # are still running.  Keeps the runner loop alive so subsequent
            # completions are injected in-order rather than dispatched separately.
            if (not items
                    and session is not None
                    and self.subagents.get_running_count_by_session(session.key) > 0):
                try:
                    msg = await asyncio.wait_for(pending_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timeout waiting for sub-agent completion in session {}",
                        session.key,
                    )
                    return items
                items.append(_to_user_message(msg))
                while len(items) < limit:
                    try:
                        items.append(_to_user_message(pending_queue.get_nowait()))
                    except asyncio.QueueEmpty:
                        break

            return items

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            retry_wait_callback=on_retry_wait,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
        ))
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            # Push final content through stream so streaming channels (e.g. Feishu)
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        from datetime import datetime

        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self._clear_pending_user_turn(session)
        return True

    async def _save_turn(self, session: Session) -> None:
        """Save current turn state to session for crash recovery.

        This is called after MessageHandler.process() completes to persist
        the conversation state. The checkpoint can be restored on restart
        to recover from interruptions.
        """
        # For now, just clear any pending checkpoint since the turn completed
        self._clear_runtime_checkpoint(session)
        self._clear_pending_user_turn(session)
        self.sessions.save(session)

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [],
        )

        handler = MessageHandler(self, msg)

        return await handler.process(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*.

        Returns the total number of cancelled tasks + subagents.
        """
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not getattr(msg, 'session_key_override', None):
            return "unified"
        return msg.session_key

