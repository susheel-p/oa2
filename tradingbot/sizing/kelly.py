"""Fractional Kelly position sizer with DTE-aware scaling.

Phase B1 + B4: Computes the recommended number of contracts for a proposed
options trade using the fractional Kelly criterion, then scales that size
by DTE risk bucket.

Kelly formula for binary bet:
    f* = (edge × (odds + 1) - 1) / odds

Where:
    edge  = win probability (p_bull for BULLISH trade, 1-p_bull for BEARISH)
    odds  = max_profit / max_loss (risk/reward ratio from chain snapshot)

Quarter-Kelly (fraction=0.25) is used as the default. Options have path-
dependent P&L and fat-tail risk; full Kelly is unsuitable.

DTE scaling (Phase B4):
    DTE 0-2:   0.50 × Kelly  (extreme gamma risk, short-dated)
    DTE 3-6:   0.75 × Kelly  (elevated gamma risk)
    DTE 7-21:  1.00 × Kelly  (standard; full Kelly fraction applied)
    DTE 22-45: 1.00 × Kelly  (standard; premium selling zone)
    DTE 46+:   0.75 × Kelly  (calendar/diagonal; uncertain P&L path)

Output is always an integer >= 1 when a trade is viable, or 0 when the
edge is negative or insufficient to meet the minimum-contracts floor.
The output is bounded by B2 hard caps (see limits.py) before the trade
is approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


_KELLY_FRACTION = 0.25      # quarter-Kelly default
_MIN_EDGE = 0.52            # below this win probability → size 0 (no edge)
_MIN_CONTRACTS = 1          # floor when trade is viable
_MAX_CONTRACTS = 20         # ceiling before book limits apply (safety rail)


@dataclass
class KellyResult:
    """Output of the Kelly sizer."""
    contracts: int              # recommended contract count (0 = no trade)
    kelly_f: float              # raw fractional Kelly fraction [0, 1]
    dte_scalar: float           # DTE scaling factor applied
    edge: float                 # win probability input
    odds: float                 # max_profit / max_loss input
    account_size: float         # account size used in calculation
    max_dollars_at_risk: float  # contracts × max_loss_per_contract
    reject_reason: str | None   # non-None when contracts=0

    @property
    def viable(self) -> bool:
        return self.contracts > 0


def dte_scalar(dte: int) -> float:
    """Map DTE to Kelly scaling factor.

    Phase B4: DTE is a first-class variable. Very short positions carry
    gamma and assignment risk that Kelly does not capture; reduce size.
    Very long positions carry path uncertainty; also reduce size.
    """
    if dte <= 2:
        return 0.50   # extreme gamma risk — halve size
    elif dte <= 6:
        return 0.75   # elevated short-gamma risk
    elif dte <= 45:
        return 1.00   # standard zone (7-45 DTE)
    else:
        return 0.75   # long-dated: calendar/diagonal, uncertain path


def compute_kelly(
    edge: float,
    max_profit: float,
    max_loss: float,
    dte: int,
    account_size: float,
    kelly_fraction: float = _KELLY_FRACTION,
    min_edge: float = _MIN_EDGE,
) -> KellyResult:
    """Compute fractional Kelly contract count for a proposed trade.

    Args:
        edge: win probability from consensus engine (p_bull for bullish,
              1 - p_bull for bearish). Must be in (0, 1).
        max_profit: maximum profit per contract in dollars (from chain snapshot).
        max_loss: maximum loss per contract in dollars (from chain snapshot).
              Always passed as a positive number (absolute value).
        dte: days to expiration of the primary leg.
        account_size: total account equity in dollars.
        kelly_fraction: fractional Kelly multiplier (default 0.25).
        min_edge: minimum win probability required to place any trade.

    Returns:
        KellyResult with contract count and supporting diagnostics.
    """
    # Validate inputs
    if edge <= min_edge:
        return KellyResult(
            contracts=0,
            kelly_f=0.0,
            dte_scalar=dte_scalar(dte),
            edge=edge,
            odds=0.0,
            account_size=account_size,
            max_dollars_at_risk=0.0,
            reject_reason=f"Edge {edge:.3f} below minimum {min_edge:.3f}",
        )

    if max_loss <= 0:
        return KellyResult(
            contracts=0,
            kelly_f=0.0,
            dte_scalar=dte_scalar(dte),
            edge=edge,
            odds=0.0,
            account_size=account_size,
            max_dollars_at_risk=0.0,
            reject_reason="max_loss must be positive",
        )

    if max_profit <= 0:
        return KellyResult(
            contracts=0,
            kelly_f=0.0,
            dte_scalar=dte_scalar(dte),
            edge=edge,
            odds=0.0,
            account_size=account_size,
            max_dollars_at_risk=0.0,
            reject_reason="max_profit must be positive",
        )

    # Odds ratio (risk/reward)
    odds = max_profit / max_loss

    # Full Kelly fraction: f* = (edge × (odds + 1) - 1) / odds
    kelly_f_full = (edge * (odds + 1) - 1) / odds

    if kelly_f_full <= 0:
        return KellyResult(
            contracts=0,
            kelly_f=kelly_f_full,
            dte_scalar=dte_scalar(dte),
            edge=edge,
            odds=odds,
            account_size=account_size,
            max_dollars_at_risk=0.0,
            reject_reason=f"Negative Kelly fraction ({kelly_f_full:.4f}) — expected value is negative",
        )

    # Apply fractional Kelly
    kelly_f = kelly_f_full * kelly_fraction

    # Apply DTE scaling
    d_scalar = dte_scalar(dte)
    kelly_f_scaled = kelly_f * d_scalar

    # Dollar amount to risk per Kelly
    dollars_at_risk = account_size * kelly_f_scaled

    # Convert to contracts (each contract = max_loss premium at risk)
    contracts_raw = dollars_at_risk / max_loss

    # Round down and apply floor/ceiling
    contracts = max(_MIN_CONTRACTS, int(contracts_raw))
    contracts = min(contracts, _MAX_CONTRACTS)

    return KellyResult(
        contracts=contracts,
        kelly_f=round(kelly_f_scaled, 5),
        dte_scalar=d_scalar,
        edge=edge,
        odds=round(odds, 3),
        account_size=account_size,
        max_dollars_at_risk=round(contracts * max_loss, 2),
        reject_reason=None,
    )


def size_from_consensus(
    p_bull: float,
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"],
    max_profit: float,
    max_loss: float,
    dte: int,
    account_size: float,
    kelly_fraction: float = _KELLY_FRACTION,
) -> KellyResult:
    """High-level interface: derive edge from consensus p_bull, then size.

    Args:
        p_bull: calibrated bullish probability from ConsensusEngine [0, 1].
        direction: the proposed trade direction (BULLISH or BEARISH).
              NEUTRAL always returns size 0 — no trade.
        max_profit: maximum profit per contract in dollars.
        max_loss: maximum loss per contract in dollars (positive number).
        dte: days to expiration.
        account_size: total account equity in dollars.
        kelly_fraction: fractional Kelly multiplier (default 0.25).

    Returns:
        KellyResult with contract count recommendation.
    """
    if direction == "NEUTRAL":
        return KellyResult(
            contracts=0,
            kelly_f=0.0,
            dte_scalar=dte_scalar(dte),
            edge=0.5,
            odds=0.0,
            account_size=account_size,
            max_dollars_at_risk=0.0,
            reject_reason="NEUTRAL direction — no trade",
        )

    # Convert p_bull to edge for each direction
    if direction == "BULLISH":
        edge = p_bull
    else:  # BEARISH
        edge = 1.0 - p_bull

    return compute_kelly(
        edge=edge,
        max_profit=max_profit,
        max_loss=max_loss,
        dte=dte,
        account_size=account_size,
        kelly_fraction=kelly_fraction,
    )
