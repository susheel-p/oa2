"""Exit engine — Phase C.

C1: Position monitor and registry (monitor.py)
C2: Exit rules engine — 6 priority rules (exit.py)
C3: Roll logic for near-expiry short positions (roll.py)
"""

from oa2.execution.monitor import OpenPosition, PositionMonitor
from oa2.execution.exit import ExitDecision, ExitEngine, ExitReason, ExitUrgency
from oa2.execution.roll import RollDecision, RollEngine

__all__ = [
    "OpenPosition",
    "PositionMonitor",
    "ExitDecision",
    "ExitEngine",
    "ExitReason",
    "ExitUrgency",
    "RollDecision",
    "RollEngine",
]
