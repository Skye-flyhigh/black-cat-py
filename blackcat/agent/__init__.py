"""Agent core module."""

from blackcat.agent.context import ContextBuilder
from blackcat.agent.hook import AgentHook, AgentHookContext, CompositeHook
from blackcat.agent.loop import AgentLoop
from blackcat.agent.memory import Dream, MemoryStore
from blackcat.agent.skills import SkillsLoader
from blackcat.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "Dream",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
