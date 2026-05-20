"""Sizing engine — Phase B.

B1/B4: Fractional Kelly with DTE-aware scaling (kelly.py)
B2:    Book-level Greeks hard caps (limits.py)
B3:    CVaR 5-scenario stress check (cvar.py)
"""

from tradingbot.sizing.kelly import KellyResult, compute_kelly, size_from_consensus
from tradingbot.sizing.limits import GreeksBook, LimitCheckResult
from tradingbot.sizing.cvar import CVaRChecker, CVaRResult

__all__ = [
    "KellyResult",
    "compute_kelly",
    "size_from_consensus",
    "GreeksBook",
    "LimitCheckResult",
    "CVaRChecker",
    "CVaRResult",
]
