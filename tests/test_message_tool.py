"""Tests for the MessageTool (send messages to channels)."""

import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage


@pytest.fixture
def captured_messages():
    """Capture messages sent via the callback."""
    messages = []

    async def callback(msg: OutboundMessage):
        messages.append(msg)

    return messages, callback


# ── Basic execution ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message(captured_messages):
    messages, callback = captured_messages
    tool = MessageTool(send_callback=callback, default_channel="telegram", default_chat_id="123")
    result = await tool.execute(content="hello")
    assert "Message sent" in result
    assert len(messages) == 1
    assert messages[0].content == "hello"
    assert messages[0].channel == "telegram"
    assert messages[0].chat_id == "123"


@pytest.mark.asyncio
async def test_send_message_override_channel(captured_messages):
    messages, callback = captured_messages
    tool = MessageTool(send_callback=callback, default_channel="telegram", default_chat_id="123")
    result = await tool.execute(content="hi", channel="discord", chat_id="456")
    assert "discord:456" in result
    assert messages[0].channel == "discord"


@pytest.mark.asyncio
async def test_send_message_no_channel():
    tool = MessageTool(send_callback=None, default_channel="", default_chat_id="")
    result = await tool.execute(content="hi")
    assert "Error" in result
    assert "No target" in result


@pytest.mark.asyncio
async def test_send_message_no_callback():
    tool = MessageTool(send_callback=None, default_channel="cli", default_chat_id="0")
    result = await tool.execute(content="hi")
    assert "Error" in result
    assert "not configured" in result


@pytest.mark.asyncio
async def test_send_message_callback_error():
    async def failing_callback(msg):
        raise ConnectionError("network down")

    tool = MessageTool(
        send_callback=failing_callback, default_channel="telegram", default_chat_id="1"
    )
    result = await tool.execute(content="hi")
    assert "Error" in result
    assert "network down" in result


# ── Per-turn tracking ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sent_in_turn_tracking(captured_messages):
    messages, callback = captured_messages
    tool = MessageTool(send_callback=callback, default_channel="cli", default_chat_id="0")
    assert tool._sent_in_turn is False

    await tool.execute(content="first")
    assert tool._sent_in_turn is True


def test_start_turn_resets():
    tool = MessageTool(default_channel="cli", default_chat_id="0")
    tool._sent_in_turn = True
    tool.start_turn()
    assert tool._sent_in_turn is False


# ── Context setting ────────────────────────────────────────────────


def test_set_context():
    tool = MessageTool()
    tool.set_context("discord", "789")
    assert tool._default_channel == "discord"
    assert tool._default_chat_id == "789"


def test_set_send_callback():
    async def cb(msg):
        pass

    tool = MessageTool()
    assert tool._send_callback is None
    tool.set_send_callback(cb)
    assert tool._send_callback is cb


# ── Schema ─────────────────────────────────────────────────────────


def test_tool_schema():
    tool = MessageTool()
    schema = tool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "message"
    assert "content" in schema["function"]["parameters"]["properties"]
