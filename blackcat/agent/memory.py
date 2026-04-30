"""Memory system — re-exports for backwards compatibility.

For new code, import from the specific modules:
- from blackcat.memory.memory import MemoryStore
- from blackcat.agent.consolidate import Consolidator
- from blackcat.memory.dream import Dream
- from blackcat.agent.autocompact import AutoCompact
"""

from blackcat.agent.autocompact import AutoCompact
from blackcat.agent.consolidate import Consolidator
from blackcat.memory.dream import Dream
from blackcat.memory.memory import MemoryStore

__all__ = ["MemoryStore", "Consolidator", "Dream", "AutoCompact"]
