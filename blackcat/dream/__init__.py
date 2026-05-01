"""Dream — self-improvement and memory consolidation system.

Public API:
    Curator  — detect patterns from parsed sessions
    Tuner    — auto-patch skills based on insights
"""

from blackcat.dream.curator import Curator
from blackcat.dream.tuner import Tuner

__all__ = [
    "Curator",
    "Tuner",
]
