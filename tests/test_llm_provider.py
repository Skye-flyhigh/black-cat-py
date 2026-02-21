"""Integration tests for LiteLLMProvider against a live Ollama instance.

These tests require Ollama running at localhost:11434 with ministral-3:8b.
They are skipped automatically if Ollama is not reachable.

Run explicitly:
    pytest tests/test_llm_provider.py -v
"""

import pytest

from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.providers.litellm_provider import LiteLLMProvider
from tests.conftest import LLM_TEST_MODEL


@pytest.fixture
def provider(ollama_available):
    """LiteLLMProvider connected to local Ollama.

    No api_base needed — LiteLLM handles ollama/ prefix natively.
    """
    return LiteLLMProvider(
        api_key="ollama",
        default_model=LLM_TEST_MODEL,
    )


# ── Basic completion ──────────────────────────────────────────────


@pytest.mark.llm
@pytest.mark.asyncio
async def test_basic_completion(provider):
    """LLM should return a non-empty text response."""
    response = await provider.chat(
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Be very brief."},
            {"role": "user", "content": "What is 2 + 2? Reply with just the number."},
        ],
        max_tokens=32,
        temperature=0.0,
    )

    assert isinstance(response, LLMResponse)
    assert response.content is not None
    assert len(response.content.strip()) > 0
    assert response.finish_reason in ("stop", "length")
    assert "4" in response.content


@pytest.mark.llm
@pytest.mark.asyncio
async def test_completion_with_history(provider):
    """LLM should use conversation history for context."""
    response = await provider.chat(
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Be very brief."},
            {"role": "user", "content": "My name is Skye."},
            {"role": "assistant", "content": "Nice to meet you, Skye!"},
            {"role": "user", "content": "What is my name? Reply with just the name."},
        ],
        max_tokens=32,
        temperature=0.0,
    )

    assert response.content is not None
    assert "Skye" in response.content


@pytest.mark.llm
@pytest.mark.asyncio
async def test_completion_respects_max_tokens(provider):
    """Response should respect max_tokens limit."""
    response = await provider.chat(
        messages=[
            {"role": "user", "content": "Write a very long story about a cat."},
        ],
        max_tokens=10,
        temperature=0.0,
    )

    assert response.content is not None
    # With max_tokens=10, the response should be short
    # (token count != char count, but it should be well under 200 chars)
    assert len(response.content) < 200


@pytest.mark.llm
@pytest.mark.asyncio
async def test_completion_usage_stats(provider):
    """Response should include token usage statistics."""
    response = await provider.chat(
        messages=[{"role": "user", "content": "Say hi."}],
        max_tokens=32,
        temperature=0.0,
    )

    assert response.usage is not None
    assert response.usage.get("prompt_tokens", 0) > 0
    assert response.usage.get("completion_tokens", 0) > 0
    assert response.usage.get("total_tokens", 0) > 0


@pytest.mark.llm
@pytest.mark.asyncio
async def test_completion_error_handling(provider):
    """Invalid model should return error response, not crash."""
    response = await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/nonexistent-model-xyz",
        max_tokens=32,
    )

    assert response.finish_reason == "error"
    assert response.content is not None
    assert "Error" in response.content


# ── Tool calling ──────────────────────────────────────────────────


WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a location.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name, e.g. 'London'",
                },
            },
            "required": ["location"],
        },
    },
}


@pytest.mark.llm
@pytest.mark.asyncio
async def test_tool_calling(provider):
    """LLM should invoke a tool when appropriate."""
    response = await provider.chat(
        messages=[
            {"role": "system", "content": "Use the get_weather tool to answer weather questions."},
            {"role": "user", "content": "What's the weather in Tokyo?"},
        ],
        tools=[WEATHER_TOOL],
        max_tokens=256,
        temperature=0.0,
    )

    assert response.has_tool_calls
    assert len(response.tool_calls) >= 1

    tc = response.tool_calls[0]
    assert isinstance(tc, ToolCallRequest)
    assert tc.name == "get_weather"
    assert "location" in tc.arguments
    assert isinstance(tc.id, str)


@pytest.mark.llm
@pytest.mark.asyncio
async def test_tool_result_continuation(provider):
    """LLM should use tool results to form a final response."""
    # Step 1: LLM requests tool call
    messages = [
        {"role": "system", "content": "Use get_weather to answer. Be brief."},
        {"role": "user", "content": "What's the weather in Paris?"},
    ]
    response1 = await provider.chat(
        messages=messages,
        tools=[WEATHER_TOOL],
        max_tokens=256,
        temperature=0.0,
    )

    assert response1.has_tool_calls
    tc = response1.tool_calls[0]

    # Step 2: Add tool result and get final answer
    import json as _json

    messages.append({
        "role": "assistant",
        "content": response1.content,
        "tool_calls": [{
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": _json.dumps(tc.arguments)},
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "name": tc.name,
        "content": "Sunny, 22°C, light breeze.",
    })

    response2 = await provider.chat(
        messages=messages,
        tools=[WEATHER_TOOL],
        max_tokens=256,
        temperature=0.0,
    )

    # Model should produce a final response (text content or tool call with answer)
    assert response2.content is not None or response2.has_tool_calls


@pytest.mark.llm
@pytest.mark.asyncio
async def test_no_weather_tool_for_math(provider):
    """LLM should not call get_weather for a math question."""
    response = await provider.chat(
        messages=[
            {"role": "user", "content": "What is 2 + 2? Just give the number."},
        ],
        tools=[WEATHER_TOOL],
        max_tokens=64,
        temperature=0.0,
    )

    # The model should not call get_weather for a math question.
    # Small models sometimes use tool format for non-tool answers — that's OK
    # as long as they don't call get_weather specifically.
    if response.has_tool_calls:
        assert all(tc.name != "get_weather" for tc in response.tool_calls)


# ── Model resolution ──────────────────────────────────────────────


def test_resolve_model_ollama(ollama_available):
    """Ollama models via ollama/ prefix don't trigger vLLM detection."""
    provider = LiteLLMProvider(
        api_key="ollama",
        default_model=LLM_TEST_MODEL,
    )
    # No api_base → no gateway detection → not vLLM
    assert provider.is_vllm is False
    # Model should resolve as-is (ollama/ prefix is native to LiteLLM)
    resolved = provider._resolve_model(LLM_TEST_MODEL)
    assert resolved.startswith("ollama/")


def test_resolve_model_standard():
    """Standard models should resolve correctly (no network needed)."""
    provider = LiteLLMProvider(
        api_key="sk-test",
        default_model="anthropic/claude-sonnet-4-5",
    )
    resolved = provider._resolve_model("deepseek-chat")
    assert resolved == "deepseek/deepseek-chat"


def test_resolve_model_gateway():
    """Gateway models should get gateway prefix."""
    provider = LiteLLMProvider(
        api_key="sk-or-test",
        default_model="claude-sonnet-4-5",
    )
    assert provider.is_openrouter is True
    resolved = provider._resolve_model("claude-sonnet-4-5")
    assert resolved == "openrouter/claude-sonnet-4-5"


def test_resolve_model_aihubmix_strips_prefix():
    """AiHubMix should strip provider prefix and re-prefix as openai/."""
    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="https://aihubmix.com/v1",
        default_model="anthropic/claude-sonnet-4-5",
    )
    assert provider.is_aihubmix is True
    resolved = provider._resolve_model("anthropic/claude-sonnet-4-5")
    assert resolved == "openai/claude-sonnet-4-5"
