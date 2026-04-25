"""Test sliding window compaction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from blackcat.agent.context import ContextManager
from blackcat.agent.summarizer import Summarizer
from blackcat.providers.openai_compat_provider import OpenAICompatProvider
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from conftest import LLM_TEST_MODEL


@pytest.fixture
def mock_session():
    """Create a mock session with add_message method."""
    session = MagicMock()
    session.add_message = MagicMock()
    return session


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with basic identity files."""
    # SOUL.md
    soul = tmp_path / "SOUL.md"
    soul.write_text("# Soul\nI am a helpful assistant.\n", encoding="utf-8")

    # IDENTITY.toml
    identity = tmp_path / "IDENTITY.toml"
    identity.write_text(
        """
[identity]
name = "TestBot"

[traits]
curiosity = 0.8
directness = 0.6
playfulness = 0.3

[trust]
default = 0.3

[trust.known]
skye = 1.0
friend = 0.7

[autonomy.free]
explore_filesystem = true
web_search = true

[autonomy.requires_confirmation]
delete_files = true
execute_commands = true
""",
        encoding="utf-8",
    )

    # memory dir
    (tmp_path / "memory").mkdir()

    return tmp_path


@pytest.fixture
def ctx(workspace):
    return ContextManager(workspace=workspace)


@pytest.fixture
def sample_messages():
    """Message list designed for compaction testing."""
    return [
        # System message (preserved separately)
        {"role": "system", "content": "You are Nyx. Be direct."},

        # Old messages to summarize (8 messages)
        {"role": "user", "content": "What's the weather in London?"},
        {"role": "assistant", "content": "Raining, 12°C."},
        {"role": "user", "content": "And Paris?"},
        {"role": "assistant", "content": "Partly cloudy, 15°C."},
        {"role": "user", "content": "Tea or coffee?"},
        {"role": "assistant", "content": "Tea is more comforting."},
        {"role": "user", "content": "What's 2+2?"},
        {"role": "assistant", "content": "4."},

        # Recent messages to preserve verbatim (2 messages)
        {"role": "user", "content": "Tell me a joke"},
        {"role": "assistant", "content": "Why do programmers prefer dark mode? Light attracts bugs."},
    ]


@pytest.fixture
def summarizer(ollama_available):
    """Summarizer backed by local Ollama."""
    provider = OpenAICompatProvider(
        api_key="ollama",
        default_model=LLM_TEST_MODEL,
    )
    return Summarizer(provider=provider, model=LLM_TEST_MODEL, timeout=60)


@pytest.mark.asyncio
class TestSlidingWindow:
    """Test context compaction logic."""

    async def test_no_compaction_needed_under_window(self, ctx, sample_messages, mock_session):
        """When under window_size, return original messages."""
        engine = ctx

        result, was_compacted = await engine.sliding_window(
            sample_messages,
            mock_session,
            window_size=20,  # Higher than message count
            keep_recent=2
        )

        assert was_compacted is False
        assert result == sample_messages
        assert len(result) == 11

    async def test_compaction_triggered_by_count(self, ctx, sample_messages, mock_session):
        """Compaction triggers when message count exceeds window_size."""
        engine = ctx

        # Mock the summarizer to avoid real LLM call
        engine.summarizer = AsyncMock()
        engine.summarizer.summarize_messages = AsyncMock(return_value=(
            "User asked about weather in London and Paris, then about drink preferences "
            "and simple math. Assistant provided helpful, concise responses."
        ))

        result, was_compacted = await engine.sliding_window(
            sample_messages,
            mock_session,
            window_size=8,  # Lower than message count (11)
            keep_recent=2
        )

        assert was_compacted is True
        # Result should be: system + summary + 2 recent = 4 messages
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are Nyx. Be direct."
        assert result[1]["role"] == "system"  # Summary is system role
        assert "weather" in result[1]["content"].lower()
        # Recent messages preserved
        assert result[2]["content"] == "Tell me a joke"
        assert "bugs" in result[3]["content"]

    async def test_no_old_messages_returns_original(self, ctx, sample_messages, mock_session):
        """If keep_recent consumes all messages, don't compact."""
        engine = ctx

        result, was_compacted = await engine.sliding_window(
            sample_messages,
            mock_session,
            window_size=5,
            keep_recent=20  # Keeps more than we have
        )

        assert was_compacted is False
        assert result == sample_messages

    async def test_missing_summarizer_skips_compaction(self, ctx, sample_messages, mock_session):
        """If no summarizer configured, log warning and skip."""
        engine = ctx
        engine.summarizer = None

        result, was_compacted = await engine.sliding_window(
            sample_messages,
            mock_session,
            window_size=5,
            keep_recent=2
        )

        assert was_compacted is False
        assert result == sample_messages


@pytest.mark.llm
@pytest.mark.asyncio
async def test_real_compaction_with_ollama(ctx, sample_messages, mock_session, ollama_available, llm_provider):
    """Integration test with real Ollama summarizer."""
    engine = ctx
    engine.summarizer = Summarizer(provider=llm_provider, model=LLM_TEST_MODEL)

    result, was_compacted = await engine.sliding_window(
        sample_messages,
        mock_session,
        window_size=8,
        keep_recent=2
    )

    assert was_compacted is True
    assert len(result) == 4  # system + summary + 2 recent

    # Verify summary is coherent
    summary = result[1]["content"]
    assert len(summary) > 50  # Should have substance
    assert "weather" in summary.lower() or "paris" in summary.lower() or "london" in summary.lower()
