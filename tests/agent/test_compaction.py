"""Tests for context compaction and sliding window logic."""

import pytest

from blackcat.agent.context import ContextManager


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

[trust]
default = 0.3

[trust.known]
skye = 1.0

[autonomy.free]
explore_filesystem = true
""",
        encoding="utf-8",
    )

    # memory dir
    (tmp_path / "memory").mkdir()

    return tmp_path


@pytest.fixture
def ctx(workspace):
    return ContextManager(workspace=workspace)


# ── needs_compaction: message count ─────────────────────────────────


def test_needs_compaction_under_threshold(ctx):
    """Should NOT compact when under message threshold."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "how are you"},
        {"role": "assistant", "content": "good"},
    ]
    needs_compact, reason = ctx.needs_compaction(messages, window_size=10)
    assert needs_compact is False
    assert reason == ""


def test_needs_compaction_over_threshold(ctx):
    """Should compact when over message threshold."""
    # Create 52 conversation messages (over default 51 threshold)
    messages = []
    for i in range(26):
        messages.append({"role": "user", "content": f"msg {i}"})
        messages.append({"role": "assistant", "content": f"resp {i}"})

    needs_compact, reason = ctx.needs_compaction(messages, window_size=50)
    assert needs_compact is True
    assert "messages" in reason
    assert "52/50" in reason or "52" in reason  # Exact format may vary


def test_needs_compaction_counts_only_user_assistant(ctx):
    """Should only count user and assistant roles, not tool/function."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "content": "result1"},
        {"role": "function", "content": "result2"},
        {"role": "system", "content": "summary"},
    ]
    # Only 2 conversation messages
    needs_compact, reason = ctx.needs_compaction(messages, window_size=10)
    assert needs_compact is False


# ── needs_compaction: token-based ──────────────────────────────────


def test_needs_compaction_token_threshold(ctx):
    """Should compact when token usage exceeds threshold."""
    # Create messages with lots of content
    messages = [
        {"role": "user", "content": "x" * 1000},
        {"role": "assistant", "content": "y" * 1000},
    ]

    # Set low max_tokens to trigger compaction
    needs_compact, reason = ctx.needs_compaction(
        messages, window_size=100, max_tokens=500, token_threshold=0.75
    )
    assert needs_compact is True
    assert "tokens" in reason


def test_needs_compaction_token_under_threshold(ctx):
    """Should NOT compact when token usage is under threshold."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]

    needs_compact, reason = ctx.needs_compaction(
        messages, window_size=100, max_tokens=10000
    )
    assert needs_compact is False


# ── sliding_window ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sliding_window_no_compaction_needed(ctx):
    """Should return original messages when no compaction needed."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    result, compacted = await ctx.sliding_window(
        messages,
        session=None,  # No summarizer, but won't be called
        window_size=10,
    )
    assert compacted is False
    assert result == messages


@pytest.mark.asyncio
async def test_sliding_window_without_summarizer(ctx):
    """Should return original messages when summarizer not configured."""
    # Create many messages that need compaction
    messages = [
        {"role": "user", "content": f"msg {i}"}
        if i % 2 == 0
        else {"role": "assistant", "content": f"resp {i}"}
        for i in range(100)
    ]

    result, compacted = await ctx.sliding_window(
        messages,
        session=None,  # No summarizer
        window_size=10,
    )
    # Should NOT compact (no summarizer), returns original
    assert compacted is False
    assert result == messages


# ── Session.get_history interaction ────────────────────────────────


def test_get_history_default_cap():
    """Test that get_history caps at default 50 messages."""
    from blackcat.session.manager import Session

    session = Session(key="test:123")

    # Add 100 messages
    for i in range(50):
        session.add_message("user", f"user {i}")
        session.add_message("assistant", f"assistant {i}")

    history = session.get_history()  # Default max_messages=50
    assert len(history) == 50


def test_get_history_custom_cap():
    """Test that get_history respects custom max_messages."""
    from blackcat.session.manager import Session

    session = Session(key="test:123")

    # Add 200 messages
    for i in range(100):
        session.add_message("user", f"user {i}")
        session.add_message("assistant", f"assistant {i}")

    history = session.get_history(max_messages=100)
    assert len(history) == 100


def test_get_history_from_last_system():
    """Test that get_history filters from last system message."""
    from blackcat.session.manager import Session

    session = Session(key="test:123")

    # Add messages, then system summary, then more messages
    session.add_message("user", "old user 1")
    session.add_message("assistant", "old assistant 1")
    session.add_message("system", "[summary of conversation]")
    session.add_message("user", "new user 1")
    session.add_message("assistant", "new assistant 1")
    session.add_message("user", "new user 2")

    history = session.get_history(max_messages=100)
    # Should only include messages from last system onwards
    assert len(history) == 4  # system + 3 new messages
    assert history[0]["role"] == "system"


def test_get_history_with_system_and_cap():
    """Test get_history with both system filter and cap."""
    from blackcat.session.manager import Session

    session = Session(key="test:123")

    # Add system message, then 60 messages after it
    session.add_message("system", "[summary]")
    for i in range(30):
        session.add_message("user", f"user {i}")
        session.add_message("assistant", f"assistant {i}")

    # With default cap of 50
    history = session.get_history()
    # 61 messages total (1 system + 60 conv), capped to 50
    assert len(history) == 50
    # Last 50 messages: 20 from end of conv (user 10-29, assistant 10-29)
    # First message should be user (not system, since system is at position 0)
    assert history[0]["role"] == "user"


# ── Integration: handler.py fix ───────────────────────────────────


def test_compaction_integration_simulation(ctx):
    """
    Simulate the handler.py flow to verify compaction triggers correctly.

    This tests the fix where get_history(max_messages=1000) allows
    sliding_window to see the full history and trigger compaction.
    """
    from blackcat.session.manager import Session

    session = Session(key="test:123")

    # Add a system summary, then 300 conversation messages
    session.add_message("system", "[conversation summary]")
    for i in range(150):
        session.add_message("user", f"user {i}")
        session.add_message("assistant", f"assistant {i}")

    # Simulate OLD behavior (max_messages=50)
    old_history = session.get_history(max_messages=50)

    # Simulate NEW behavior (max_messages=1000)
    new_history = session.get_history(max_messages=1000)

    # OLD: Would NOT trigger compaction (capped at 50 messages, ~25 conv)
    old_needs_compact, _ = ctx.needs_compaction(old_history, window_size=51)

    # NEW: SHOULD trigger compaction (sees all 150 conv msgs)
    new_needs_compact, new_reason = ctx.needs_compaction(new_history, window_size=51)

    assert old_needs_compact is False, "OLD behavior should NOT compact (capped at 50)"
    assert new_needs_compact is True, "NEW behavior SHOULD compact (sees all 150 msgs)"
    assert "messages" in new_reason


# ── Edge case: empty old_messages ───────────────────────────────────


@pytest.mark.asyncio
async def test_sliding_window_empty_old_messages(ctx):
    """
    Verify that sliding_window handles the edge case where old_messages
    contains only system/tool/empty messages that would be filtered out
    by the summarizer, resulting in an empty summary.

    This was a bug: compaction would silently drop context with no summary.
    """
    from blackcat.session.manager import Session

    session = Session(key="test:123")

    # Create messages that trigger compaction (over window_size)
    # But old_messages portion contains only system/tool messages
    messages = []

    # Add many system messages (these get filtered by summarizer)
    for i in range(30):
        messages.append({"role": "system", "content": f"[system message {i}]"})

    # Add many tool results (also filtered)
    for i in range(30):
        messages.append({"role": "tool", "content": f"tool_result_{i}", "tool_call_id": f"call_{i}"})

    # Add recent user/assistant messages (these should be kept)
    for i in range(10):
        messages.append({"role": "user", "content": f"recent user {i}"})
        messages.append({"role": "assistant", "content": f"recent assistant {i}"})

    # Trigger compaction with low window_size
    result, was_compacted = await ctx.sliding_window(
        messages,
        session=session,
        window_size=10,  # Low threshold to force compaction check
        keep_recent=5,   # Keep only 5 recent messages
    )

    # Should NOT compact (no summarizable content in old_messages)
    # Should return original messages unchanged
    assert was_compacted is False, "Should NOT compact when old_messages has no content"
    assert result == messages, "Should return original messages unchanged"
