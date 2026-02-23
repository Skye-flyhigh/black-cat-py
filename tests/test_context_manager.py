"""Tests for the ContextManager (prompt assembly, trust, token management)."""

from pathlib import Path

import pytest

from nanobot.agent.context import ContextManager


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
[core]
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


# ── Trust system ───────────────────────────────────────────────────


def test_trust_level_trusted(ctx):
    level = ctx.get_trust_level("skye")
    assert level == "trusted"


def test_trust_level_moderate(ctx):
    """friend has score 0.7, which is >0.4 but not >0.7, so moderate."""
    level = ctx.get_trust_level("friend")
    assert level == "moderate"


def test_trust_level_unknown(ctx):
    level = ctx.get_trust_level("stranger")
    assert level == "low"


def test_trust_level_case_insensitive(ctx):
    level = ctx.get_trust_level("Skye")
    assert level == "trusted"


def test_trust_level_no_identity(tmp_path):
    (tmp_path / "memory").mkdir()
    ctx = ContextManager(workspace=tmp_path)
    assert ctx.get_trust_level("anyone") == "unknown"


# ── Tool permissions ───────────────────────────────────────────────


def test_allowed_tools_trusted(ctx):
    perms = ctx.get_allowed_tools("skye")
    # Trusted author gets everything autonomous
    assert "delete_files" in perms["autonomous"]
    assert "execute_commands" in perms["autonomous"]
    assert perms["confirmation_required"] == []


def test_allowed_tools_untrusted(ctx):
    perms = ctx.get_allowed_tools("stranger")
    assert "explore_filesystem" in perms["autonomous"]
    assert "delete_files" in perms["confirmation_required"]


# ── Identity loading ──────────────────────────────────────────────


def test_load_identity(ctx):
    identity = ctx.load_identity()
    assert "SOUL.md" in identity
    assert "helpful assistant" in identity["SOUL.md"]
    assert "IDENTITY.toml" in identity


def test_load_identity_missing_files(tmp_path):
    (tmp_path / "memory").mkdir()
    ctx = ContextManager(workspace=tmp_path)
    identity = ctx.load_identity()
    assert identity == {}


# ── Trait formatting ───────────────────────────────────────────────


def test_format_traits(ctx):
    traits = {"curiosity": 0.9, "patience": 0.2, "warmth": 0.5}
    result = ctx._format_traits(traits)
    assert "curiosity: high" in result
    assert "patience: low" in result
    assert "warmth: moderate" in result


def test_format_trust(ctx):
    trust = {"default": 0.3, "known": {"skye": 1.0, "bob": 0.5}}
    result = ctx._format_trust(trust)
    assert "low" in result  # default 0.3 = low
    assert "skye" in result


# ── System prompt building ─────────────────────────────────────────


def test_build_core_prompt(ctx):
    prompt = ctx.build_core_prompt(author="skye", channel="telegram")
    assert "Soul" in prompt or "helpful assistant" in prompt
    assert "trusted" in prompt.lower()
    assert "telegram" in prompt.lower()
    assert "Environment" in prompt


def test_build_core_prompt_includes_memory(ctx):
    ctx.memory.write_long_term("User likes coffee")
    prompt = ctx.build_core_prompt(author="skye")
    assert "coffee" in prompt


# ── Message assembly ──────────────────────────────────────────────


def test_build_messages_structure(ctx):
    messages = ctx.build_messages(
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        current_message="how are you?",
        author="skye",
    )
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "user"
    assert messages[3]["content"] == "how are you?"


def test_build_messages_empty_history(ctx):
    messages = ctx.build_messages(history=[], current_message="hello")
    assert len(messages) == 2  # system + user
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "hello"


# ── User content with media ───────────────────────────────────────


def test_build_user_content_text_only(ctx):
    result = ctx._build_user_content("hello", None)
    assert result == "hello"


def test_build_user_content_no_media(ctx):
    result = ctx._build_user_content("hello", [])
    assert result == "hello"


def test_build_user_content_with_image(ctx, tmp_path):
    img = tmp_path / "photo.png"
    # Create a minimal PNG (1x1 pixel)
    import base64

    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img.write_bytes(png_data)

    result = ctx._build_user_content("describe this", [str(img)])
    assert isinstance(result, list)
    assert any(item.get("type") == "image_url" for item in result)
    assert any(item.get("type") == "text" for item in result)


def test_build_user_content_nonexistent_media(ctx):
    result = ctx._build_user_content("hello", ["/nonexistent/image.png"])
    assert result == "hello"


# ── Token counting ─────────────────────────────────────────────────


def test_count_tokens(ctx):
    count = ctx.count_tokens("hello world")
    assert count > 0
    assert isinstance(count, int)


def test_count_tokens_empty(ctx):
    assert ctx.count_tokens("") == 0


def test_token_budget(ctx):
    budget = ctx.token_budget(1000, "small text")
    assert budget > 0
    assert budget < 1000


# ── Tool result and assistant message helpers ──────────────────────


def test_add_tool_result(ctx):
    messages = [{"role": "system", "content": "sys"}]
    ctx.add_tool_result(messages, "call_1", "shell", "output text")
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call_1"
    assert messages[-1]["name"] == "shell"
    assert messages[-1]["content"] == "output text"


def test_add_assistant_message(ctx):
    messages = []
    ctx.add_assistant_message(messages, "hello")
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "hello"


def test_add_assistant_message_always_has_content_key(ctx):
    messages = []
    ctx.add_assistant_message(messages, None, tool_calls=[{"id": "c1"}])
    assert "content" in messages[-1]
    assert messages[-1]["content"] is None


def test_add_assistant_message_with_reasoning(ctx):
    messages = []
    ctx.add_assistant_message(messages, "answer", reasoning_content="thinking...")
    assert messages[-1]["reasoning_content"] == "thinking..."


# ── Context pruning ───────────────────────────────────────────────


def test_context_pruning_within_budget(ctx):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = ctx.context_pruning(messages, max_tokens=100000)
    assert result == messages  # No pruning needed


def test_context_pruning_removes_old(ctx):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(50):
        messages.append({"role": "user", "content": f"message {i}" * 100})
        messages.append({"role": "assistant", "content": f"reply {i}" * 100})

    result = ctx.context_pruning(messages, max_tokens=100, keep_recent=4)
    # Should keep system + last 4 messages
    assert result[0]["role"] == "system"
    assert len(result) == 5


def test_context_pruning_empty():
    ctx = ContextManager.__new__(ContextManager)
    result = ctx.context_pruning([], max_tokens=100)
    assert result == []


# ── Compaction ─────────────────────────────────────────────────────


def test_needs_compaction_by_messages(ctx):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(20):
        messages.append({"role": "user", "content": f"msg {i}"})
        messages.append({"role": "assistant", "content": f"reply {i}"})

    needs, reason = ctx.needs_compaction(messages, window_size=10)
    assert needs is True
    assert "messages" in reason


def test_needs_compaction_small(ctx):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    needs, reason = ctx.needs_compaction(messages, window_size=10)
    assert needs is False
    assert reason == ""


def test_prepare_for_compaction(ctx):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(10):
        messages.append({"role": "user", "content": f"msg {i}"})

    old, recent, sys_msg = ctx.prepare_for_compaction(messages, keep_recent=3)
    assert sys_msg is not None
    assert sys_msg["content"] == "sys"
    assert len(recent) == 3
    assert len(old) == 7


def test_prepare_for_compaction_small(ctx):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    old, recent, sys_msg = ctx.prepare_for_compaction(messages, keep_recent=5)
    assert old == []
    assert len(recent) == 1


def test_apply_compaction(ctx):
    sys_msg = {"role": "system", "content": "sys"}
    recent = [{"role": "user", "content": "recent msg"}]
    result = ctx.apply_compaction(sys_msg, "This is a summary", recent)
    assert len(result) == 3
    assert result[0] == sys_msg
    assert "Summary" in result[1]["content"]
    assert result[2] == recent[0]


def test_apply_compaction_empty_summary(ctx):
    sys_msg = {"role": "system", "content": "sys"}
    recent = [{"role": "user", "content": "hi"}]
    result = ctx.apply_compaction(sys_msg, "", recent)
    assert len(result) == 2  # No summary message added
