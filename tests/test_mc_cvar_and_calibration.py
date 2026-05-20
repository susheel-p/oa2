"""Tests for Monte Carlo CVaR (P0#3) and p_bull Calibrator (P0#2)."""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import pytest

from tradingbot.consensus.calibration import Calibrator, _sigmoid
from tradingbot.sizing.mc_cvar import MonteCarloCVaR, LegSpec, _bsm_price


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------


class TestCalibratorIdentity:
    def test_default_state_is_identity(self):
        cal = Calibrator()
        assert cal.state.mode == "identity"
        for p in (0.05, 0.3, 0.5, 0.7, 0.95):
            assert cal.transform(p) == pytest.approx(p, abs=1e-6)

    def test_insufficient_samples_stays_identity(self):
        cal = Calibrator(min_samples=50)
        cal.fit([0.4, 0.5, 0.6], [0, 1, 1])
        assert cal.state.mode == "identity"
        assert cal.state.n_samples == 3
        assert cal.transform(0.7) == pytest.approx(0.7, abs=1e-6)


class TestCalibratorPlatt:
    def test_platt_fit_improves_brier(self):
        # 80 points with miscalibrated raw probs — should improve under Platt
        ps, ys = [], []
        for i in range(80):
            true_p = 0.3 + 0.5 * (i / 80)
            raw_p = 0.5 + 0.4 * (true_p - 0.5)  # systematically pushed toward 0.5
            ps.append(raw_p)
            ys.append(1 if (i % 100) / 100 < true_p else 0)
        cal = Calibrator(min_samples=50, mode="platt")
        state = cal.fit(ps, ys)
        assert state.mode == "platt"
        assert state.n_samples == 80
        assert state.brier_after is not None
        assert state.brier_after <= state.brier_before + 1e-6

    def test_platt_clipping(self):
        cal = Calibrator(min_samples=10, mode="platt")
        cal.fit([0.5] * 50, [1] * 50)
        # Even with extreme inputs, output remains in (0,1)
        for p in (-5.0, 0.0, 1.0, 5.0):
            out = cal.transform(p)
            assert 0.0 < out < 1.0


class TestCalibratorPersistence:
    def test_save_load_roundtrip(self):
        cal = Calibrator(min_samples=10, mode="platt")
        cal.fit([0.3, 0.4, 0.5, 0.6, 0.7] * 10, [0, 0, 1, 1, 1] * 10)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cal.json"
            cal.save(path)
            loaded = Calibrator.load(path)
        assert loaded.state.mode == cal.state.mode
        assert loaded.state.a == pytest.approx(cal.state.a)
        assert loaded.state.b == pytest.approx(cal.state.b)
        assert loaded.transform(0.6) == pytest.approx(cal.transform(0.6), abs=1e-6)

    def test_load_missing_returns_identity(self):
        with tempfile.TemporaryDirectory() as td:
            cal = Calibrator.load(Path(td) / "nonexistent.json")
        assert cal.state.mode == "identity"


# ---------------------------------------------------------------------------
# MC CVaR — Taylor mode
# ---------------------------------------------------------------------------


class TestMCCVaRTaylor:
    def test_approves_small_position(self):
        mc = MonteCarloCVaR(account_size=50_000, rng_seed=0, n_paths=2000)
        result = mc.check(
            delta=5.0, gamma=0.1, vega=2.0, theta=-1.0,
            price=100.0, contracts=1,
        )
        assert result.approved is True
        assert result.mode == "taylor"
        assert result.cvar_loss > -result.budget_dollars

    def test_rejects_oversized_position(self):
        mc = MonteCarloCVaR(account_size=10_000, rng_seed=0, n_paths=2000, budget_pct=0.02)
        # Huge delta+vega exposure that will breach a 2%/$200 budget
        result = mc.check(
            delta=500.0, gamma=10.0, vega=200.0, theta=-50.0,
            price=400.0, contracts=10,
        )
        assert result.approved is False
        assert result.reject_reason is not None
        assert "MC CVaR breach" in result.reject_reason

    def test_cvar_is_worse_than_var(self):
        """CVaR (mean of tail) must be <= VaR (boundary of tail)."""
        mc = MonteCarloCVaR(account_size=50_000, rng_seed=0, n_paths=2000)
        result = mc.check(
            delta=20.0, gamma=0.5, vega=10.0, theta=-3.0,
            price=420.0, contracts=3,
        )
        # Both negative or near zero; CVaR is mean of worst α — never above VaR
        assert result.cvar_loss <= result.var_loss + 1e-6

    def test_fat_tail_widens_loss(self):
        params = dict(
            delta=20.0, gamma=0.5, vega=10.0, theta=-3.0,
            price=420.0, contracts=3,
        )
        thin = MonteCarloCVaR(account_size=50_000, rng_seed=0, n_paths=4000,
                              fat_tail=False).check(**params)
        fat = MonteCarloCVaR(account_size=50_000, rng_seed=0, n_paths=4000,
                             fat_tail=True).check(**params)
        # Fat-tail Student-t draws produce wider extremes
        assert fat.worst_pnl <= thin.worst_pnl

    def test_max_contracts_monotone(self):
        mc = MonteCarloCVaR(account_size=10_000, rng_seed=0, n_paths=1500, budget_pct=0.02)
        n = mc.max_contracts_within_budget(
            delta=200.0, gamma=2.0, vega=100.0, theta=-20.0,
            price=400.0, requested=10,
        )
        assert 0 <= n <= 10


# ---------------------------------------------------------------------------
# MC CVaR — BSM mode + helpers
# ---------------------------------------------------------------------------


class TestBSMPricer:
    def test_call_intrinsic_at_expiry(self):
        assert _bsm_price(110, 100, 0.0, 0.30, "C") == pytest.approx(10.0)
        assert _bsm_price(90, 100, 0.0, 0.30, "C") == pytest.approx(0.0)

    def test_put_intrinsic_at_expiry(self):
        assert _bsm_price(90, 100, 0.0, 0.30, "P") == pytest.approx(10.0)
        assert _bsm_price(110, 100, 0.0, 0.30, "P") == pytest.approx(0.0)

    def test_atm_call_put_parity_approx(self):
        # r=0, no dividend → ATM call ≈ ATM put
        c = _bsm_price(100, 100, 30 / 365, 0.30, "C")
        p = _bsm_price(100, 100, 30 / 365, 0.30, "P")
        assert c == pytest.approx(p, abs=1e-9)


class TestMCCVaRBSM:
    def test_long_call_loss_bounded_by_premium(self):
        # Long ATM call: max loss is the premium paid (per contract × 100)
        entry = _bsm_price(420, 420, 30 / 365, 0.25, "C")
        legs = [LegSpec(strike=420, expiry_days=30, iv=0.25,
                        right="C", side=+1, entry_price=entry)]
        mc = MonteCarloCVaR(account_size=50_000, rng_seed=0, n_paths=2000)
        result = mc.check(price=420, contracts=1, legs=legs)
        assert result.mode == "bsm"
        # Worst path loss should not exceed total premium paid + small numerical slack
        max_loss = entry * 100 * 1  # one contract
        assert result.worst_pnl >= -(max_loss * 1.05)
