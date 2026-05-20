"""Exit engine — Phase C.

C1: Position monitor and registry (monitor.py)
C2: Exit rules engine — 6 priority rules (exit.py)
C3: Roll logic for near-expiry short positions (roll.py)
"""

from tradingbot.execution.monitor import ChainProvider, Leg, OpenPosition, PositionMonitor
from tradingbot.execution.exit import ExitDecision, ExitEngine, ExitReason, ExitUrgency
from tradingbot.execution.roll import RollDecision, RollEngine

__all__ = [
    "ChainProvider",
    "Leg",
    "OpenPosition",
    "PositionMonitor",
    "ExitDecision",
    "ExitEngine",
    "ExitReason",
    "ExitUrgency",
    "RollDecision",
    "RollEngine",
]
