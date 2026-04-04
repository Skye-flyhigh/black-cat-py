"""Unit tests for AgentRunner and AgentRunSpec."""

import pytest

from blackcat.agent.hook import AgentHook, AgentHookContext
from blackcat.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from blackcat.agent.tools.base import Tool
from blackcat.agent.tools.registry import ToolRegistry
from blackcat.providers.base import LLMResponse, ToolCallRequest


class FakeTool(Tool):
    """Test tool with schema."""

    name = "fake"
    description = "A fake tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return kwargs.get("text", "")


class EchoTool(Tool):
    """Echo tool for testing."""

    name = "echo"
    description = "Echo input"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, **kwargs):
        return kwargs.get("text", "")


class LoopTool(Tool):
    """Tool that returns 'looping' for testing iterations."""

    name = "loop"
    description = "Loop"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "looping"


class FakeProvider:
    """Minimal fake LLM provider for testing."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self.responses = responses or []
        self.call_count = 0
        self.supports_prompt_caching = False
        self.generation = type("Generation", (), {"max_tokens": 4096})()

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content="No more responses", tool_calls=[])

    async def chat_with_retry(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        return await self.chat(messages=messages, tools=tools, model=model)


class TestAgentRunSpec:
    """Tests for AgentRunSpec dataclass."""

    def test_required_fields(self):
        """Spec requires messages and tools."""
        tools = ToolRegistry()
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            model="test",
            max_iterations=10,
            max_tool_result_chars=50000,
        )
        assert len(spec.initial_messages) == 1
        assert spec.tools == tools
        assert spec.model == "test"
        assert spec.max_iterations == 10

    def test_defaults(self):
        """Spec should have sensible defaults."""
        tools = ToolRegistry()
        spec = AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test",
            max_iterations=10,
            max_tool_result_chars=50000,
        )
        assert spec.hook is None
        assert spec.error_message is not None
        assert spec.concurrent_tools is False
        assert spec.fail_on_tool_error is False
        assert spec.workspace is None
        assert spec.session_key is None


class TestAgentRunResult:
    """Tests for AgentRunResult dataclass."""

    def test_defaults(self):
        """Result should have sensible defaults."""
        result = AgentRunResult(
            final_content="done",
            messages=[{"role": "assistant", "content": "done"}],
        )
        assert result.final_content == "done"
        assert len(result.messages) == 1
        assert result.tools_used == []
        assert result.usage == {}
        assert result.stop_reason == "completed"
        assert result.error is None
        assert result.tool_events == []


class TestAgentRunner:
    """Tests for AgentRunner."""

    @pytest.mark.asyncio
    async def test_simple_response(self):
        """Runner should return final content for simple responses."""
        provider = FakeProvider(responses=[
            LLMResponse(content="Hello!", tool_calls=[]),
        ])
        runner = AgentRunner(provider)
        tools = ToolRegistry()

        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Say hi"}],
            tools=tools,
            model="fake",
            max_iterations=5,
            max_tool_result_chars=50000,
        ))

        assert result.final_content == "Hello!"
        assert result.stop_reason == "completed"
        assert result.tools_used == []

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self):
        """Runner should execute tools and continue."""
        # First response: tool call. Second: final answer.
        provider = FakeProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="1", name="echo", arguments={"text": "hi"})],
            ),
            LLMResponse(content="Done!", tool_calls=[]),
        ])

        tools = ToolRegistry()
        tools.register(EchoTool())

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Test"}],
            tools=tools,
            model="fake",
            max_iterations=5,
            max_tool_result_chars=50000,
        ))

        assert result.final_content == "Done!"
        assert "echo" in result.tools_used

    @pytest.mark.asyncio
    async def test_max_iterations(self):
        """Runner should stop after max iterations."""
        # Always return tool calls (infinite loop)
        provider = FakeProvider(responses=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id=str(i), name="loop", arguments={})],
            )
            for i in range(10)
        ])

        tools = ToolRegistry()
        tools.register(LoopTool())

        runner = AgentRunner(provider)
        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Loop"}],
            tools=tools,
            model="fake",
            max_iterations=3,
            max_tool_result_chars=50000,
        ))

        assert result.stop_reason == "max_iterations"
        assert len(result.tools_used) == 3

    @pytest.mark.asyncio
    async def test_hook_before_iteration(self):
        """Runner should call hook before each iteration."""
        provider = FakeProvider(responses=[
            LLMResponse(content="Done", tool_calls=[]),
        ])

        class CountingHook(AgentHook):
            def __init__(self):
                self.iterations = []

            async def before_iteration(self, context: AgentHookContext) -> None:
                self.iterations.append(context.iteration)

        hook = CountingHook()
        runner = AgentRunner(provider)
        tools = ToolRegistry()

        await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Test"}],
            tools=tools,
            model="fake",
            max_iterations=5,
            max_tool_result_chars=50000,
            hook=hook,
        ))

        assert hook.iterations == [0]

    @pytest.mark.asyncio
    async def test_hook_after_iteration(self):
        """Runner should call hook after each iteration."""
        provider = FakeProvider(responses=[
            LLMResponse(content="Done", tool_calls=[]),
        ])

        class CountingHook(AgentHook):
            def __init__(self):
                self.iterations = []

            async def after_iteration(self, context: AgentHookContext) -> None:
                self.iterations.append(context.iteration)

        hook = CountingHook()
        runner = AgentRunner(provider)
        tools = ToolRegistry()

        await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Test"}],
            tools=tools,
            model="fake",
            max_iterations=5,
            max_tool_result_chars=50000,
            hook=hook,
        ))

        assert hook.iterations == [0]

    @pytest.mark.asyncio
    async def test_hook_finalize_content(self):
        """Runner should use hook to finalize content."""
        provider = FakeProvider(responses=[
            LLMResponse(content="  hello world  ", tool_calls=[]),
        ])

        class TrimHook(AgentHook):
            def finalize_content(self, context, content):
                return content.strip() if content else content

        hook = TrimHook()
        runner = AgentRunner(provider)
        tools = ToolRegistry()

        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Test"}],
            tools=tools,
            model="fake",
            max_iterations=5,
            max_tool_result_chars=50000,
            hook=hook,
        ))

        assert result.final_content == "hello world"

    @pytest.mark.asyncio
    async def test_error_response(self):
        """Runner should handle error responses."""
        provider = FakeProvider(responses=[
            LLMResponse(content="Something went wrong", tool_calls=[], finish_reason="error"),
        ])

        runner = AgentRunner(provider)
        tools = ToolRegistry()

        result = await runner.run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Test"}],
            tools=tools,
            model="fake",
            max_iterations=5,
            max_tool_result_chars=50000,
        ))

        assert result.stop_reason == "error"
        assert result.error is not None
