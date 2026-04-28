"""Agent core module."""

from blackcat.agent.context import ContextBuilder
from blackcat.agent.hook import AgentHook, AgentHookContext, CompositeHook
from blackcat.agent.loop import AgentLoop

# Memory system re-exports (for backwards compatibility)
from blackcat.agent.memory import AutoCompact, Consolidator, Dream, MemoryStore
from blackcat.agent.skills import SkillsLoader
from blackcat.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "AutoCompact",
    "CompositeHook",
    "Consolidator",
    "ContextBuilder",
    "Dream",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
