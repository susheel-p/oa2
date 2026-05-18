"""Monte Carlo CVaR — Phase B3 rebuild.

The original `cvar.py` runs five deterministic Greeks-only scenarios and
returns a worst-loss value. That is a scenario stress test, not CVaR.
This module produces a real CVaR: simulate N paths of the joint
(underlying return, IV change) distribution, reprice the position on each
path, and report the conditional expected loss in the worst α tail.

Two repricing modes are supported:

  Taylor (Greeks-only):    P&L_path ≈ Δ·dS + ½·Γ·dS² + V·dIV + Θ·dT
  BSM   (leg-level):       reprice each option leg with Black-Scholes,
                           sum across legs and contracts.

Taylor is the default when only summary Greeks are available (current
pipeline state). BSM is used automatically when leg-level data is passed
in via `legs`. Both produce the same loss series; the BSM mode is more
accurate in the tails where second-order effects matter (iron condors,
calendars, anything with cross-strike gamma).

This module is the gate-of-record for tail risk. The legacy scenario
stress in `cvar.py` is retained as a coarse pre-check.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_N_PATHS = 10_000
_DEFAULT_HORIZON_DAYS = 1
_DEFAULT_ALPHA = 0.05            # CVaR confidence — worst 5% of paths
_DEFAULT_BUDGET_PCT = 0.05       # max acceptable tail loss = 5% of account
_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# BSM helpers (pure Python, no scipy)
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bsm_price(spot: float, strike: float, t_years: float, iv: float,
               right: str, r: float = 0.0) -> float:
    """Black-Scholes price for a European call/put. No dividend, r=0 default."""
    if t_years <= 0 or iv <= 0:
        intrinsic = max(0.0, spot - strike) if right.upper().startswith("C") else max(0.0, strike - spot)
        return intrinsic
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    if right.upper().startswith("C"):
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    else:
        return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


# ---------------------------------------------------------------------------
# Result + checker
# ---------------------------------------------------------------------------


@dataclass
class MCCVaRResult:
    """Output of one MC CVaR run."""
    approved: bool
    reject_reason: str | None
    n_paths: int
    horizon_days: int
    alpha: float
    var_loss: float           # value-at-risk at α: loss not exceeded with prob 1-α
    cvar_loss: float          # conditional expected loss in worst α tail (negative number)
    mean_pnl: float
    worst_pnl: float
    best_pnl: float
    budget_dollars: float
    mode: str                 # "taylor" | "bsm"
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class LegSpec:
    """Leg-level input for BSM repricing mode."""
    strike: float
    expiry_days: int          # days from now until expiry
    iv: float                 # implied vol (e.g. 0.30 for 30%)
    right: str                # "C" or "P"
    side: int                 # +1 long, -1 short
    entry_price: float        # premium paid/received per contract at entry


class MonteCarloCVaR:
    """Real Monte Carlo CVaR with Taylor or BSM repricing.

    Args:
        account_size:    total equity in dollars.
        budget_pct:      max acceptable tail loss (default 5%).
        alpha:           tail size for CVaR (default 5%).
        n_paths:         path count (default 10_000).
        horizon_days:    holding horizon in trading days (default 1).
        annual_vol:      base annualised vol for the underlying (default 0.20).
        iv_shock_vol:    daily std of IV changes in vol points (default 0.02 = 2 pts/day).
        fat_tail:        use Student-t (df=4) instead of Normal for returns (default True).
        rng_seed:        seed for deterministic results.
    """

    def __init__(
        self,
        account_size: float,
        budget_pct: float = _DEFAULT_BUDGET_PCT,
        alpha: float = _DEFAULT_ALPHA,
        n_paths: int = _DEFAULT_N_PATHS,
        horizon_days: int = _DEFAULT_HORIZON_DAYS,
        annual_vol: float = 0.20,
        iv_shock_vol: float = 0.02,
        fat_tail: bool = True,
        rng_seed: int | None = None,
    ):
        self.account_size = account_size
        self.budget_pct = budget_pct
        self.alpha = alpha
        self.n_paths = n_paths
        self.horizon_days = horizon_days
        self.annual_vol = annual_vol
        self.iv_shock_vol = iv_shock_vol
        self.fat_tail = fat_tail
        self._rng = random.Random(rng_seed)

    @property
    def budget_dollars(self) -> float:
        return self.account_size * self.budget_pct

    # ------------------------------------------------------------------
    # Path generation
    # ------------------------------------------------------------------

    def _generate_paths(self) -> list[tuple[float, float]]:
        """Yield (dS_pct, dIV_pts) tuples — fractional return and IV change in vol points."""
        dt = self.horizon_days / _TRADING_DAYS
        sigma = self.annual_vol * math.sqrt(dt)
        iv_sigma = self.iv_shock_vol * math.sqrt(self.horizon_days)

        paths = []
        for _ in range(self.n_paths):
            if self.fat_tail:
                # Student-t with df=4 (kurtosis 9 → meaningfully fat tails)
                u = self._rng.gauss(0.0, 1.0)
                chi = sum(self._rng.gauss(0.0, 1.0) ** 2 for _ in range(4))
                z = u / math.sqrt(chi / 4.0) if chi > 1e-9 else u
            else:
                z = self._rng.gauss(0.0, 1.0)

            dS_pct = sigma * z
            # IV changes are negatively correlated with returns (vol-up-on-sell-off)
            iv_z = -0.6 * z + 0.8 * self._rng.gauss(0.0, 1.0)
            dIV_pts = iv_sigma * iv_z * 100.0  # report in vol points (e.g. 2.0 = 2 pts)
            paths.append((dS_pct, dIV_pts))
        return paths

    # ------------------------------------------------------------------
    # Taylor repricing
    # ------------------------------------------------------------------

    def _path_pnl_taylor(
        self,
        dS_pct: float, dIV_pts: float,
        delta: float, gamma: float, vega: float, theta: float,
        price: float, contracts: int,
        book_delta: float, book_vega: float, book_gamma: float, book_theta: float,
    ) -> float:
        dS = price * dS_pct
        dT = self.horizon_days / 365.0
        # IV pts are absolute (0.02 = 2 vol points); vega is per 1 vol point
        proposed_pnl = (
            delta * dS
            + 0.5 * gamma * dS * dS
            + vega * dIV_pts
            + theta * dT
        ) * contracts
        book_pnl = (
            book_delta * dS
            + 0.5 * book_gamma * dS * dS
            + book_vega * dIV_pts
            + book_theta * dT
        )
        return proposed_pnl + book_pnl

    # ------------------------------------------------------------------
    # BSM repricing
    # ------------------------------------------------------------------

    def _path_pnl_bsm(
        self,
        dS_pct: float, dIV_pts: float,
        legs: list[LegSpec], contracts: int, spot: float,
        book_legs: list[LegSpec] | None = None,
    ) -> float:
        new_spot = spot * (1.0 + dS_pct)
        dt_years = self.horizon_days / 365.0

        pnl = 0.0
        for leg in legs:
            t_years_now = max(leg.expiry_days, 0) / 365.0
            t_years_then = max(0.0, t_years_now - dt_years)
            new_iv = max(0.01, leg.iv + dIV_pts / 100.0)
            new_price = _bsm_price(new_spot, leg.strike, t_years_then, new_iv, leg.right)
            leg_pnl = (new_price - leg.entry_price) * leg.side * 100.0  # contract = 100 shares
            pnl += leg_pnl * contracts

        if book_legs:
            for leg in book_legs:
                t_years_now = max(leg.expiry_days, 0) / 365.0
                t_years_then = max(0.0, t_years_now - dt_years)
                new_iv = max(0.01, leg.iv + dIV_pts / 100.0)
                new_price = _bsm_price(new_spot, leg.strike, t_years_then, new_iv, leg.right)
                pnl += (new_price - leg.entry_price) * leg.side * 100.0

        return pnl

    # ------------------------------------------------------------------
    # Public check
    # ------------------------------------------------------------------

    def check(
        self,
        delta: float = 0.0,
        gamma: float = 0.0,
        vega: float = 0.0,
        theta: float = 0.0,
        price: float = 100.0,
        contracts: int = 1,
        book_delta: float = 0.0,
        book_vega: float = 0.0,
        book_gamma: float = 0.0,
        book_theta: float = 0.0,
        legs: list[LegSpec] | None = None,
        book_legs: list[LegSpec] | None = None,
    ) -> MCCVaRResult:
        """Run MC CVaR on the proposed trade.

        When `legs` is provided, BSM repricing is used (more accurate in tails).
        Otherwise the Taylor (Greeks-only) approximation is used.
        """
        mode = "bsm" if legs else "taylor"
        paths = self._generate_paths()
        pnls: list[float] = []

        for dS_pct, dIV_pts in paths:
            if mode == "bsm":
                pnl = self._path_pnl_bsm(dS_pct, dIV_pts, legs, contracts, price, book_legs)
            else:
                pnl = self._path_pnl_taylor(
                    dS_pct, dIV_pts,
                    delta, gamma, vega, theta, price, contracts,
                    book_delta, book_vega, book_gamma, book_theta,
                )
            pnls.append(pnl)

        pnls.sort()
        n = len(pnls)
        tail_count = max(1, int(self.alpha * n))
        var_loss = pnls[tail_count - 1]                     # worst path of the kept tail
        cvar_loss = sum(pnls[:tail_count]) / tail_count     # mean of the worst α
        mean_pnl = sum(pnls) / n
        worst = pnls[0]
        best = pnls[-1]

        approved = cvar_loss > -self.budget_dollars
        reject_reason = None
        if not approved:
            reject_reason = (
                f"MC CVaR breach: CVaR_{int(self.alpha*100)}% = {cvar_loss:+,.2f} "
                f"exceeds budget -{self.budget_dollars:,.2f} "
                f"({self.budget_pct*100:.0f}% of ${self.account_size:,.0f}). "
                f"Mode={mode}, paths={self.n_paths}, horizon={self.horizon_days}d."
            )

        return MCCVaRResult(
            approved=approved,
            reject_reason=reject_reason,
            n_paths=self.n_paths,
            horizon_days=self.horizon_days,
            alpha=self.alpha,
            var_loss=round(var_loss, 2),
            cvar_loss=round(cvar_loss, 2),
            mean_pnl=round(mean_pnl, 2),
            worst_pnl=round(worst, 2),
            best_pnl=round(best, 2),
            budget_dollars=round(self.budget_dollars, 2),
            mode=mode,
            diagnostics={
                "tail_count": tail_count,
                "annual_vol": self.annual_vol,
                "iv_shock_vol": self.iv_shock_vol,
                "fat_tail": self.fat_tail,
            },
        )

    def max_contracts_within_budget(
        self,
        delta: float, gamma: float, vega: float, theta: float,
        price: float, requested: int,
        book_delta: float = 0.0, book_vega: float = 0.0,
        book_gamma: float = 0.0, book_theta: float = 0.0,
        legs: list[LegSpec] | None = None,
        book_legs: list[LegSpec] | None = None,
    ) -> int:
        """Largest contract count that stays within CVaR budget.

        Linear search from `requested` down — N is small enough that this
        is fine. Each check re-uses the same path set so reductions are
        monotonic in contracts (we re-seed for determinism).
        """
        for n in range(requested, 0, -1):
            self._rng = random.Random(self._rng.random() * 1e9 if False else 0)
            result = self.check(
                delta=delta, gamma=gamma, vega=vega, theta=theta,
                price=price, contracts=n,
                book_delta=book_delta, book_vega=book_vega,
                book_gamma=book_gamma, book_theta=book_theta,
                legs=legs, book_legs=book_legs,
            )
            if result.approved:
                return n
        return 0
