"""Agent core module."""

from blackcat.agent.context import ContextManager
from blackcat.agent.hook import AgentHook, AgentHookContext, CompositeHook
from blackcat.agent.loop import AgentLoop
from blackcat.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from blackcat.agent.skills import SkillsLoader
from blackcat.agent.subagent import SubagentManager
from blackcat.memory.memory import Journal, MemoryStore  # MemoryStore is alias for backward compat

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "AgentRunner",
    "AgentRunResult",
    "AgentRunSpec",
    "CompositeHook",
    "ContextManager",
    "MemoryStore",
    "Journal",
    "AgentRunSpec",
    "SkillsLoader",
    "SubagentManager",
]
