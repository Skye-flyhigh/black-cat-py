"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.config.schema import Config, ExecToolConfig
    from nanobot.cron.service import CronService

from nanobot.agent.context import ContextManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.summarizer import Summarizer
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import SessionManager


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
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        config: Config | None = None,
        llm_timeout: int | None = 60,
    ):
        from nanobot.config.schema import ExecToolConfig as ExecToolConfigRuntime
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfigRuntime()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.config = config
        self.llm_timeout = llm_timeout

        # Summarizer for context compaction (created first, passed to ContextManager)
        summarizer_model = config.agents.defaults.summarizer_model if config else None
        self.summarizer = Summarizer(
            provider=provider,
            model=summarizer_model,  # Falls back to main model if None
            timeout=llm_timeout,
        )

        self.context = ContextManager(workspace, summarizer=self.summarizer)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            llm_timeout=llm_timeout,
        )

        self.memory_window = config.agents.defaults.memory_window if config else 50
        self._running = False
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _resolve_author(self, sender_id: str, channel: str) -> str:
        """Resolve sender_id to author name using config, or return sender_id as-is."""
        if self.config:
            return self.config.resolve_author(sender_id, channel)
        return sender_id  # Fallback: use raw sender_id if no config

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message (regular or system).

        Args:
            msg: The inbound message to process.

        Returns:
            The response message, or None if no response needed.
        """
        is_system = msg.channel == "system"

        # Parse origin (system messages encode origin in chat_id as "channel:chat_id")
        if is_system:
            if ":" in msg.chat_id:
                origin_channel, origin_chat_id = msg.chat_id.split(":", 1)
            else:
                origin_channel, origin_chat_id = "cli", msg.chat_id
            session_key = f"{origin_channel}:{origin_chat_id}"
            logger.info(f"Processing system message from {msg.sender_id}")
        else:
            origin_channel, origin_chat_id = msg.channel, msg.chat_id
            session_key = msg.session_key
            preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            logger.info(f"Processing message from {origin_channel}:{msg.sender_id}: {preview}")

        # Get or create session
        session = self.sessions.get_or_create(session_key)

        # Update tool contexts
        self._update_tool_contexts(origin_channel, origin_chat_id)

        # Build initial messages
        author = self._resolve_author(msg.sender_id, msg.channel)
        messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            author=author,
            channel=origin_channel,
            chat_id=origin_chat_id,
            media=msg.media if msg.media and not is_system else None,
        )

        # Compact context if needed
        messages, _ = await self.context.compact_if_needed(
            messages,
            window_size=self.memory_window,
            model=self.model,
        )

        # Agent loop
        final_content = await self._run_agent_loop(messages)

        if not final_content or not final_content.strip():
            final_content = "Background task completed." if is_system else "I've completed processing but have no response to give."

        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {origin_channel}:{msg.sender_id}: {preview}")

        # Save to session
        user_content = f"[System: {msg.sender_id}] {msg.content}" if is_system else msg.content
        session.add_message("user", user_content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )

    def _update_tool_contexts(self, channel: str, chat_id: str) -> None:
        """Update tool contexts with current channel and chat_id."""
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(channel, chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(channel, chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(channel, chat_id)

    async def _run_agent_loop(self, messages: list[dict]) -> str | None:
        """
        Run the agent loop: call LLM, execute tools, repeat until done.

        Returns:
            The final response content, or None if max iterations reached.
        """
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                timeout=self.llm_timeout,
            )

            if response.has_tool_calls:
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                # Execute tools
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # No tool calls, we're done
                return response.content

        return None
    
    async def process_direct(
        self,
        content: str,
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).

        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content
        )
        
        response = await self._process_message(msg)
        return response.content if response else ""
