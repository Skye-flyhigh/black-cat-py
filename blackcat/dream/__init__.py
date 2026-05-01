"""Dream — self-improvement and memory consolidation system.

Public API:
    Dream.run()                — one-shot consolidation (thin orchestrator)
    SessionParser              — extract behavioural telemetry from sessions
    BehavioralAnalyzer         — detect patterns from parsed sessions
    SkillEvolver               — auto-patch skills based on insights
"""

from blackcat.dream.behavioral_analyzer import BehavioralAnalyzer
from blackcat.dream.session_parser import SessionParser, SessionSummary
from blackcat.dream.skill_evolver import SkillEvolver

__all__ = [
    "SessionParser",
    "SessionSummary",
    "BehavioralAnalyzer",
    "SkillEvolver",
]
