"""Integration tests for the Summarizer against a live Ollama instance.

These tests require Ollama running at localhost:11434 with llama3.1:8b.
Skipped automatically if Ollama is not reachable.

Run explicitly:
    pytest tests/test_llm_summarizer.py -v
"""

import pytest

from nanobot.agent.summarizer import Summarizer
from nanobot.providers.litellm_provider import LiteLLMProvider
from tests.conftest import LLM_TEST_MODEL


@pytest.fixture
def summarizer(ollama_available):
    """Summarizer backed by local Ollama."""
    provider = LiteLLMProvider(
        api_key="ollama",
        default_model=LLM_TEST_MODEL,
    )
    return Summarizer(provider=provider, model=LLM_TEST_MODEL, timeout=60)


# ── Summarize messages ─────────────────────────────────────────────


@pytest.mark.llm
@pytest.mark.asyncio
async def test_summarize_conversation(summarizer):
    """Should produce a concise summary of a multi-turn conversation."""
    messages = [
        {"role": "user", "content": "I'm working on a Python project called black-cat."},
        {"role": "assistant", "content": "Cool! What does it do?"},
        {"role": "user", "content": "It's an autonomous AI agent that runs locally."},
        {"role": "assistant", "content": "Interesting. What's the architecture like?"},
        {"role": "user", "content": "It has channels for Telegram and Discord, a tool system, and persistent memory."},
        {"role": "assistant", "content": "That sounds well-designed. How do you handle memory?"},
        {"role": "user", "content": "Daily markdown notes plus a long-term MEMORY.md file. We want to add vector search."},
    ]

    summary = await summarizer.summarize_messages(messages)
    assert len(summary) > 0
    # Should mention key topics from the conversation
    summary_lower = summary.lower()
    assert any(word in summary_lower for word in ["black-cat", "agent", "python", "project", "memory"])


@pytest.mark.llm
@pytest.mark.asyncio
async def test_summarize_empty():
    """Empty messages should return empty string (no LLM call needed)."""
    provider = LiteLLMProvider(api_key="ollama")
    s = Summarizer(provider=provider)
    result = await s.summarize_messages([])
    assert result == ""


@pytest.mark.llm
@pytest.mark.asyncio
async def test_summarize_skips_system_and_tool(summarizer):
    """System and tool messages should be filtered out before summarization."""
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Search for the weather in Tokyo."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "Sunny, 25°C", "tool_call_id": "1", "name": "weather"},
        {"role": "assistant", "content": "It's sunny and 25°C in Tokyo."},
    ]

    summary = await summarizer.summarize_messages(messages)
    assert len(summary) > 0
    # Should capture the actual conversation content
    assert any(word in summary.lower() for word in ["tokyo", "weather", "sunny", "25"])


@pytest.mark.llm
@pytest.mark.asyncio
async def test_summarize_with_custom_prompt(summarizer):
    """Custom prompt should guide the summarization."""
    messages = [
        {"role": "user", "content": "I prefer dark mode in all my apps."},
        {"role": "assistant", "content": "Noted! I'll remember that preference."},
        {"role": "user", "content": "Also, I use vim keybindings everywhere."},
    ]

    summary = await summarizer.summarize_messages(
        messages,
        prompt="List only the user's personal preferences as bullet points.",
    )

    assert len(summary) > 0
    summary_lower = summary.lower()
    assert "dark mode" in summary_lower or "vim" in summary_lower


# ── Extract facts ─────────────────────────────────────────────────


@pytest.mark.llm
@pytest.mark.asyncio
async def test_extract_facts(summarizer):
    """Should extract long-term facts from conversation."""
    messages = [
        {"role": "user", "content": "I'm Skye, I live in Melbourne, Australia."},
        {"role": "assistant", "content": "Nice! How's the weather there?"},
        {"role": "user", "content": "It's winter here. I'm a software engineer working on AI."},
        {"role": "assistant", "content": "What kind of AI work?"},
        {"role": "user", "content": "Building autonomous agents. My cat's name is Nyx."},
    ]

    facts = await summarizer.extract_facts(messages)
    assert len(facts) > 0
    facts_lower = facts.lower()
    # Should capture at least some key facts
    captured = sum(1 for word in ["skye", "melbourne", "software", "nyx", "cat", "ai"]
                   if word in facts_lower)
    assert captured >= 2  # At least 2 key facts captured


@pytest.mark.llm
@pytest.mark.asyncio
async def test_extract_facts_empty():
    """Empty messages should return empty string."""
    provider = LiteLLMProvider(api_key="ollama")
    s = Summarizer(provider=provider)
    result = await s.extract_facts([])
    assert result == ""


@pytest.mark.llm
@pytest.mark.asyncio
async def test_extract_facts_trivial_conversation(summarizer):
    """Trivial conversation should yield no facts (or empty)."""
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Bye"},
    ]

    facts = await summarizer.extract_facts(messages)
    # Either empty or "nothing to extract" — should not fabricate facts
    assert len(facts) < 100  # Very short or empty


# ── Summarize session ─────────────────────────────────────────────


@pytest.mark.llm
@pytest.mark.asyncio
async def test_summarize_session(summarizer):
    """Full session summarization should return both summary and facts."""
    messages = [
        {"role": "user", "content": "I need help with my Python project."},
        {"role": "assistant", "content": "Sure! What's the project?"},
        {"role": "user", "content": "It's called nanobot. I want to add vector memory search."},
        {"role": "assistant", "content": "We could use sqlite-vec for that."},
        {"role": "user", "content": "Good idea. Let's use ollama/nomic-embed-text for embeddings."},
    ]

    result = await summarizer.summarize_session(messages, "test:session")
    assert "summary" in result
    assert "facts" in result
    assert isinstance(result["summary"], str)
    assert isinstance(result["facts"], str)
    assert len(result["summary"]) > 0


# ── Format messages ───────────────────────────────────────────────


def test_format_messages_filters_system_and_tool():
    """_format_messages_for_summary should skip system and tool messages."""
    provider = LiteLLMProvider(api_key="test")
    s = Summarizer(provider=provider)

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
        {"role": "tool", "content": "tool output"},
        {"role": "assistant", "content": "hi there"},
    ]

    formatted = s._format_messages_for_summary(messages)
    assert "system prompt" not in formatted
    assert "tool output" not in formatted
    assert "User: hello" in formatted
    assert "Assistant: hi there" in formatted


def test_format_messages_empty():
    provider = LiteLLMProvider(api_key="test")
    s = Summarizer(provider=provider)
    assert s._format_messages_for_summary([]) == ""
