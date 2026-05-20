"""CVaR scenario stress check — Phase B3.

Runs five deterministic stress scenarios against a proposed trade and
rejects or scales the size if any scenario produces a loss exceeding the
configured CVaR budget.

Scenarios:
    1. Underlying -3% intraday (normal intraday drawdown)
    2. Underlying -5% intraday (large intraday move / bad gap)
    3. VIX +10 points (moderate IV spike)
    4. VIX +20 points (crisis IV spike, e.g., Aug 2015, Feb 2018 events)
    5. Correlation spike: all positions move adversely simultaneously

Each scenario computes an approximate P&L using first-order Greeks
(delta + vega). This is a rough estimate — actual options P&L is non-linear
and includes gamma, theta, and cross-Greeks. However, for a pre-trade
gate this is appropriate: it catches gross tail risk without requiring a
full options pricer.

P&L approximation per scenario:
    Underlying shock dS%:  dPL = delta × (price × dS%)
    IV shock dIV%:         dPL = vega × dIV%

For the correlation spike scenario, all open positions also contribute
their worst-case P&L (directional, not diversified), added to the proposed
trade's worst-case.

Usage:
    checker = CVaRChecker(account_size=50_000, budget_pct=0.05)
    result = checker.check(
        delta=35.0,
        vega=12.0,
        price=450.0,
        contracts=2,
        book_delta=80.0,
        book_vega=30.0,
    )
    if not result.approved:
        print(result.reject_reason)
"""

from __future__ import annotations

from dataclasses import dataclass


# Stress scenario magnitudes
_SCENARIOS = [
    ("underlying_down_3pct",   -0.03,  0.0,   "Underlying -3%"),
    ("underlying_down_5pct",   -0.05,  0.0,   "Underlying -5%"),
    ("vix_up_10pts",            0.0,  10.0,   "VIX +10 pts"),
    ("vix_up_20pts",            0.0,  20.0,   "VIX +20 pts"),
    ("correlation_spike",      -0.05, 15.0,   "Correlation spike (adverse)"),
]

_DEFAULT_BUDGET_PCT = 0.05    # 5% of account is max CVaR loss
_VEGA_PER_IV_PCT = 100.0      # vega is expressed per 1% IV move; 10 pts = 10× vega


@dataclass
class ScenarioPnL:
    """P&L estimate for a single stress scenario."""
    name: str
    label: str
    pnl_proposed: float       # P&L on proposed trade (contracts already applied)
    pnl_book: float           # P&L on existing book
    pnl_total: float          # combined
    breaches_budget: bool


@dataclass
class CVaRResult:
    """Output of the CVaR stress check."""
    approved: bool
    reject_reason: str | None
    worst_scenario: str | None
    worst_pnl: float
    budget_dollars: float
    scenarios: list[ScenarioPnL]

    @property
    def viable(self) -> bool:
        return self.approved


class CVaRChecker:
    """Pre-trade CVaR stress check using first-order Greeks.

    Args:
        account_size: total account equity in dollars.
        budget_pct: maximum allowable loss as fraction of account (default 5%).
        include_book: if True, include existing book Greeks in scenario P&L.
    """

    def __init__(
        self,
        account_size: float,
        budget_pct: float = _DEFAULT_BUDGET_PCT,
        include_book: bool = True,
    ):
        self.account_size = account_size
        self.budget_pct = budget_pct
        self.include_book = include_book

    @property
    def budget_dollars(self) -> float:
        return self.account_size * self.budget_pct

    def check(
        self,
        delta: float,
        vega: float,
        price: float,
        contracts: int,
        book_delta: float = 0.0,
        book_vega: float = 0.0,
    ) -> CVaRResult:
        """Run all five stress scenarios against the proposed trade.

        Args:
            delta: dollar-delta per contract of the proposed trade.
            vega:  dollar-vega per contract (per 1% IV move) of proposed trade.
            price: current price of the underlying in dollars.
            contracts: number of contracts being proposed.
            book_delta: current net dollar-delta of all open positions.
            book_vega:  current net dollar-vega of all open positions.

        Returns:
            CVaRResult — approved=True if no scenario breaches budget.
        """
        total_delta_proposed = delta * contracts
        total_vega_proposed = vega * contracts

        scenario_results = []
        worst_pnl = 0.0
        worst_name = None

        for scenario_key, ds_pct, div_pts, label in _SCENARIOS:
            # Proposed trade P&L under this scenario
            pnl_proposed = 0.0
            if ds_pct != 0:
                pnl_proposed += total_delta_proposed * (price * ds_pct)
            if div_pts != 0:
                pnl_proposed += total_vega_proposed * div_pts

            # Existing book P&L under this scenario
            pnl_book = 0.0
            if self.include_book:
                if ds_pct != 0:
                    pnl_book += book_delta * (price * ds_pct)
                if div_pts != 0:
                    pnl_book += book_vega * div_pts

            pnl_total = pnl_proposed + pnl_book
            breaches = pnl_total < -self.budget_dollars

            scenario_results.append(ScenarioPnL(
                name=scenario_key,
                label=label,
                pnl_proposed=round(pnl_proposed, 2),
                pnl_book=round(pnl_book, 2),
                pnl_total=round(pnl_total, 2),
                breaches_budget=breaches,
            ))

            if pnl_total < worst_pnl:
                worst_pnl = pnl_total
                worst_name = label

        # Find any breach
        breaching = [s for s in scenario_results if s.breaches_budget]

        if breaching:
            worst = min(breaching, key=lambda s: s.pnl_total)
            return CVaRResult(
                approved=False,
                reject_reason=(
                    f"CVaR breach in '{worst.label}': estimated loss {worst.pnl_total:+.2f} "
                    f"exceeds budget -{self.budget_dollars:.2f} "
                    f"({self.budget_pct*100:.0f}% of ${self.account_size:,.0f})"
                ),
                worst_scenario=worst.label,
                worst_pnl=round(worst_pnl, 2),
                budget_dollars=round(self.budget_dollars, 2),
                scenarios=scenario_results,
            )

        return CVaRResult(
            approved=True,
            reject_reason=None,
            worst_scenario=worst_name,
            worst_pnl=round(worst_pnl, 2),
            budget_dollars=round(self.budget_dollars, 2),
            scenarios=scenario_results,
        )

    def max_contracts_within_budget(
        self,
        delta: float,
        vega: float,
        price: float,
        requested: int,
        book_delta: float = 0.0,
        book_vega: float = 0.0,
    ) -> int:
        """Find the largest contract count that passes all scenarios.

        Returns 0 if even 1 contract breaches the budget.
        Returns requested if all fit.
        """
        for n in range(requested, 0, -1):
            result = self.check(
                delta=delta,
                vega=vega,
                price=price,
                contracts=n,
                book_delta=book_delta,
                book_vega=book_vega,
            )
            if result.approved:
                return n
        return 0
