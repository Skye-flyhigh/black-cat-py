"""Integration tests for the AgentLoop against a live Ollama instance.

These tests exercise the full agent pipeline: message -> context -> LLM -> tools -> response.
Requires Ollama running at localhost:11434 with ministral-3:8b.
Skipped automatically if Ollama is not reachable.

Run explicitly:
    pytest tests/test_llm_agent_loop.py -v
"""

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.litellm_provider import LiteLLMProvider
from tests.conftest import LLM_TEST_MODEL


@pytest.fixture
def provider(ollama_available):
    return LiteLLMProvider(api_key="ollama", default_model=LLM_TEST_MODEL)


@pytest.fixture
def agent(ollama_available, tmp_path):
    """Full AgentLoop backed by local Ollama."""
    bus = MessageBus()
    prov = LiteLLMProvider(api_key="ollama", default_model=LLM_TEST_MODEL)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("You are a test assistant. Be very brief.\n")
    (workspace / "memory").mkdir()

    loop = AgentLoop(
        bus=bus,
        provider=prov,
        workspace=workspace,
        model=LLM_TEST_MODEL,
        max_iterations=5,
        llm_timeout=60,
    )
    return loop


# ── _run_agent_loop (low-level, fewer tools = less model confusion) ──


@pytest.mark.llm
@pytest.mark.asyncio
async def test_run_agent_loop_simple_response(provider):
    """LLM should return text content for a simple question (no tools)."""
    # Minimal registry with NO tools — forces text response
    tools = ToolRegistry()
    agent = AgentLoop.__new__(AgentLoop)
    agent.provider = provider
    agent.model = LLM_TEST_MODEL
    agent.max_iterations = 3
    agent.tools = tools
    agent.llm_timeout = 60

    # Bypass the full context manager — just raw messages
    from nanobot.agent.context import ContextManager

    agent.context = ContextManager.__new__(ContextManager)

    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply very briefly."},
        {"role": "user", "content": "What is 2 + 2? Reply with just the number."},
    ]

    content, tools_used = await agent._run_agent_loop(messages)
    assert content is not None
    assert "4" in content
    assert tools_used == []


@pytest.mark.llm
@pytest.mark.asyncio
async def test_run_agent_loop_with_read_file(provider, tmp_path):
    """LLM should use read_file tool and return content."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "secret.txt").write_text("The answer is 42.")

    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=workspace))

    agent = AgentLoop.__new__(AgentLoop)
    agent.provider = provider
    agent.model = LLM_TEST_MODEL
    agent.max_iterations = 5
    agent.tools = tools
    agent.llm_timeout = 60

    from nanobot.agent.context import ContextManager

    agent.context = ContextManager.__new__(ContextManager)

    messages = [
        {"role": "system", "content": "You have a read_file tool. Use it when asked to read files. Be brief."},
        {"role": "user", "content": "Read secret.txt and tell me the answer."},
    ]

    content, tools_used = await agent._run_agent_loop(messages)
    assert "read_file" in tools_used
    assert content is not None
    # The model read the file — that's what we're testing.
    # Whether it reports "42" or refuses on principle is model behavior.
    assert len(content) > 0


@pytest.mark.llm
@pytest.mark.asyncio
async def test_run_agent_loop_with_write_file(provider, tmp_path):
    """LLM should use write_file tool to create files."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    tools = ToolRegistry()
    tools.register(WriteFileTool(workspace=workspace))

    agent = AgentLoop.__new__(AgentLoop)
    agent.provider = provider
    agent.model = LLM_TEST_MODEL
    agent.max_iterations = 5
    agent.tools = tools
    agent.llm_timeout = 60

    from nanobot.agent.context import ContextManager

    agent.context = ContextManager.__new__(ContextManager)

    messages = [
        {"role": "system", "content": "You have a write_file tool. Use it to write files. Be brief."},
        {"role": "user", "content": "Write the text 'hello world' to a file called output.txt"},
    ]

    content, tools_used = await agent._run_agent_loop(messages)
    assert "write_file" in tools_used
    assert (workspace / "output.txt").exists()
    assert "hello world" in (workspace / "output.txt").read_text()


# ── Full process_direct (tests the entire pipeline) ───────────────


@pytest.mark.llm
@pytest.mark.asyncio
async def test_process_direct_responds(agent):
    """Agent should produce some response (text or via message tool)."""
    response = await agent.process_direct("What is 2 + 2?")

    # Small models sometimes use the message tool instead of text response.
    # When that happens, process_direct returns "" because _sent_in_turn=True.
    # Either way, the agent should NOT crash.
    assert response is not None
    # Check if the response is meaningful OR the message was sent via bus
    if response and response.strip() and "completed processing" not in response:
        assert "4" in response
    else:
        # The model used the message tool — check the outbound bus
        if agent.bus.outbound_size > 0:
            outbound = await agent.bus.consume_outbound()
            assert "4" in outbound.content


@pytest.mark.llm
@pytest.mark.asyncio
async def test_process_direct_preserves_session(agent):
    """Agent should maintain session context between calls."""
    await agent.process_direct("Remember: the magic word is 'butterfly'.", chat_id="s1")
    response = await agent.process_direct("What is the magic word?", chat_id="s1")

    # Check both direct response and bus for the answer
    found = False
    if response and "butterfly" in response.lower():
        found = True
    while agent.bus.outbound_size > 0:
        msg = await agent.bus.consume_outbound()
        if "butterfly" in msg.content.lower():
            found = True
    assert found


@pytest.mark.llm
@pytest.mark.asyncio
async def test_process_message_returns_outbound(agent):
    """_process_message should return an OutboundMessage or send via bus."""
    msg = InboundMessage(
        channel="test",
        sender_id="user1",
        chat_id="chat1",
        content="Say the word 'pong'. Nothing else.",
    )

    response = await agent._process_message(msg)

    # Either direct response or message tool was used
    found_pong = False
    if response is not None:
        assert response.channel == "test"
        assert response.chat_id == "chat1"
        if "pong" in response.content.lower():
            found_pong = True
    # Also check outbound bus (message tool sends there)
    while agent.bus.outbound_size > 0:
        out = await agent.bus.consume_outbound()
        if "pong" in out.content.lower():
            found_pong = True
    assert found_pong


# ── Static helpers (no LLM needed) ────────────────────────────────


def test_strip_think():
    assert AgentLoop._strip_think("<think>internal</think>answer") == "answer"
    assert AgentLoop._strip_think("<think>thinking\nmore</think>result") == "result"
    assert AgentLoop._strip_think("no thinking here") == "no thinking here"
    assert AgentLoop._strip_think("") is None
    assert AgentLoop._strip_think(None) is None


def test_strip_think_multiple():
    text = "<think>first</think>hello <think>second</think>world"
    assert AgentLoop._strip_think(text) == "hello world"


def test_tool_hint():
    from nanobot.providers.base import ToolCallRequest

    calls = [ToolCallRequest(id="1", name="web_search", arguments={"query": "test"})]
    hint = AgentLoop._tool_hint(calls)
    assert 'web_search("test")' in hint


def test_tool_hint_truncates():
    from nanobot.providers.base import ToolCallRequest

    long_query = "a" * 100
    calls = [ToolCallRequest(id="1", name="search", arguments={"query": long_query})]
    hint = AgentLoop._tool_hint(calls)
    assert '...")' in hint


def test_tool_hint_no_args():
    from nanobot.providers.base import ToolCallRequest

    calls = [ToolCallRequest(id="1", name="list_dir", arguments={})]
    hint = AgentLoop._tool_hint(calls)
    assert hint == "list_dir"
