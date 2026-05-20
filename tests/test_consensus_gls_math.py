"""Mathematical correctness tests for the GLS consensus engine rewrite.

These tests verify properties of the algorithm itself, not just the outputs:
  - Precision matrix is the true inverse of the covariance matrix
  - GLS weights are row sums of Ω (not opinion-dependent)
  - High-correlation pairs receive less combined weight than low-correlation ones
  - Conviction scaling: lower σ_i → higher Ω row sum → higher weight
  - Ridge regularisation keeps Ω finite for degenerate inputs
  - n_eff equals N when all weights are equal; falls below N when concentrated
  - Heteroscedastic scaling: equal-conviction debaters produce equal weights when
    correlation structure is symmetric
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from tradingbot.consensus.engine import ConsensusEngine, _NOISE_FLOOR, _RIDGE_LAMBDA
from tradingbot.consensus.state import Direction
from tradingbot.debaters.base import DebaterOpinion


# =============================================================================
# Helpers
# =============================================================================

def _opinion(name: str, direction: str, conviction: float) -> DebaterOpinion:
    return DebaterOpinion(
        debater_name=name,
        direction=direction,
        conviction=conviction,
        reasoning="test",
        signals_used={},
    )


def _symmetric_corr(names: list[str], rho: float) -> dict[str, dict[str, float]]:
    """Build an equicorrelation matrix with constant off-diagonal rho."""
    return {
        n1: {n2: (1.0 if n1 == n2 else rho) for n2 in names}
        for n1 in names
    }


# =============================================================================
# 1 — Precision matrix is the actual inverse of the covariance matrix
# =============================================================================

class TestPrecisionMatrixIsActualInverse(unittest.TestCase):
    """Verify Ω ≈ Σ_r⁻¹ via round-trip: Σ_r · Ω ≈ I."""

    def _roundtrip_error(self, opinion_vectors, corr_matrix):
        Omega, names = ConsensusEngine._build_precision_matrix(opinion_vectors, corr_matrix)
        n = len(names)

        sigma = np.array(
            [max(_NOISE_FLOOR, 1.0 - abs(opinion_vectors[name])) for name in names]
        )
        C = np.array([[corr_matrix[n1][n2] for n2 in names] for n1 in names])
        Sigma = C * np.outer(sigma, sigma)
        Sigma_r = Sigma + _RIDGE_LAMBDA * np.eye(n)

        product = Sigma_r @ Omega
        return float(np.max(np.abs(product - np.eye(n))))

    def test_5_debaters_typical(self):
        """Round-trip error < 1e-10 for the standard 5-debater configuration."""
        names = ["directional", "income", "volatility", "flow", "sentiment"]
        opinions = {
            "directional": 0.75, "income": 0.65,
            "volatility": 0.0, "flow": 0.60, "sentiment": -0.40,
        }
        corr = {
            n1: {n2: ConsensusEngine._fixed_correlation(n1, n2) if n1 != n2 else 1.0
                 for n2 in names}
            for n1 in names
        }
        err = self._roundtrip_error(opinions, corr)
        self.assertLess(err, 1e-10)

    def test_equicorrelation_small(self):
        """Equicorrelation matrix with ρ=0.30 inverts correctly."""
        names = ["a", "b", "c"]
        opinions = {"a": 0.80, "b": 0.60, "c": 0.40}
        corr = _symmetric_corr(names, 0.30)
        err = self._roundtrip_error(opinions, corr)
        self.assertLess(err, 1e-10)

    def test_equicorrelation_large_rho(self):
        """High ρ=0.85 still inverts correctly thanks to ridge regularisation."""
        names = ["a", "b", "c", "d"]
        opinions = {"a": 0.70, "b": 0.70, "c": 0.70, "d": 0.70}
        corr = _symmetric_corr(names, 0.85)
        err = self._roundtrip_error(opinions, corr)
        self.assertLess(err, 1e-8)

    def test_neutral_debaters_do_not_cause_singularity(self):
        """5 neutral debaters (σ=1.0 all) still inverts without error."""
        names = [f"d{i}" for i in range(5)]
        opinions = {n: 0.0 for n in names}
        corr = _symmetric_corr(names, 0.20)
        err = self._roundtrip_error(opinions, corr)
        self.assertLess(err, 1e-8)

    def test_single_debater(self):
        """1×1 matrix trivially inverts: Ω = 1/Σ."""
        opinions = {"solo": 0.80}
        corr = {"solo": {"solo": 1.0}}
        Omega, names = ConsensusEngine._build_precision_matrix(opinions, corr)
        sigma = max(_NOISE_FLOOR, 1.0 - 0.80)
        expected = 1.0 / (sigma ** 2 + _RIDGE_LAMBDA)
        self.assertAlmostEqual(float(Omega[0, 0]), expected, places=8)


# =============================================================================
# 2 — GLS weights are opinion-independent (structural property of Ω)
# =============================================================================

class TestWeightsAreOpinionIndependent(unittest.TestCase):
    """w_i ∝ (Ω·1)_i depends on conviction magnitude but NOT on direction."""

    def _weights_for(self, opinions_list):
        engine = ConsensusEngine()
        return engine.aggregate(opinions_list).weights

    def test_direction_flip_does_not_change_weights(self):
        """Flipping a debater from BULLISH to BEARISH (same conviction) leaves
        their weight unchanged — direction doesn't affect σ."""
        w_bull = self._weights_for([
            _opinion("a", "BULLISH", 0.70),
            _opinion("b", "BULLISH", 0.60),
            _opinion("c", "NEUTRAL", 0.50),
        ])
        w_bear = self._weights_for([
            _opinion("a", "BEARISH", 0.70),   # same |conviction|, direction flipped
            _opinion("b", "BULLISH", 0.60),
            _opinion("c", "NEUTRAL", 0.50),
        ])
        self.assertAlmostEqual(w_bull["a"], w_bear["a"], places=8)
        self.assertAlmostEqual(w_bull["b"], w_bear["b"], places=8)

    def test_weights_invariant_to_conviction_sign(self):
        """Weights of other debaters don't change when one debater flips direction."""
        w1 = self._weights_for([
            _opinion("x", "BULLISH", 0.80),
            _opinion("y", "BULLISH", 0.80),
            _opinion("z", "NEUTRAL", 0.50),
        ])
        w2 = self._weights_for([
            _opinion("x", "BULLISH", 0.80),
            _opinion("y", "BEARISH", 0.80),   # same |conv|, direction flipped
            _opinion("z", "NEUTRAL", 0.50),
        ])
        self.assertAlmostEqual(w1["x"], w2["x"], places=8)
        self.assertAlmostEqual(w1["y"], w2["y"], places=8)


# =============================================================================
# 3 — Conviction scaling: higher conviction → lower noise → higher weight
# =============================================================================

class TestConvictionScaling(unittest.TestCase):
    """σ_i = 1 − |o_i|; lower σ means higher precision → higher GLS weight."""

    def test_higher_conviction_gets_higher_weight_same_direction(self):
        """Among two BULLISH debaters the one with higher conviction gets more weight."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("high", "BULLISH", 0.90),
            _opinion("low",  "BULLISH", 0.30),
            _opinion("n1",   "NEUTRAL", 0.50),
            _opinion("n2",   "NEUTRAL", 0.50),
        ]
        w = engine.aggregate(opinions).weights
        self.assertGreater(w["high"], w["low"])

    def test_higher_conviction_bearish_gets_higher_weight_than_low(self):
        """Same rule applies for BEARISH debaters."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("strong_bear", "BEARISH", 0.85),
            _opinion("weak_bear",   "BEARISH", 0.25),
            _opinion("n1",          "NEUTRAL", 0.50),
        ]
        w = engine.aggregate(opinions).weights
        self.assertGreater(w["strong_bear"], w["weak_bear"])

    def test_neutral_debater_gets_lowest_weight_among_mixed(self):
        """A NEUTRAL debater (σ=1.0) should receive less weight than a BULLISH
        debater with any positive conviction."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("active", "BULLISH", 0.50),   # σ = 0.50
            _opinion("neutral", "NEUTRAL", 0.50),  # σ = 1.0
        ]
        w = engine.aggregate(opinions).weights
        self.assertGreater(w["active"], w["neutral"])

    def test_conviction_monotone_across_three_levels(self):
        """Strict ordering: high > medium > low conviction → weight ordering."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("hi",  "BULLISH", 0.90),
            _opinion("mid", "BULLISH", 0.60),
            _opinion("lo",  "BULLISH", 0.20),
        ]
        w = engine.aggregate(opinions).weights
        self.assertGreater(w["hi"], w["mid"])
        self.assertGreater(w["mid"], w["lo"])

    def test_equal_convictions_give_equal_weights_symmetric_corr(self):
        """When all debaters have identical conviction and corr structure is
        symmetric, all weights must be equal."""
        engine = ConsensusEngine()
        names = [f"d{i}" for i in range(4)]
        opinions = [_opinion(n, "BULLISH", 0.70) for n in names]
        w = engine.aggregate(opinions).weights
        weight_values = list(w.values())
        for wv in weight_values:
            self.assertAlmostEqual(wv, weight_values[0], places=8)


# =============================================================================
# 4 — Correlation structure reduces combined weight for correlated pairs
# =============================================================================

class TestCorrelationDiscounting(unittest.TestCase):
    """Highly correlated debaters should receive lower combined weight than
    otherwise identical but independent debaters."""

    def _sum_weights(self, names, opinions, rho):
        """Build engine with explicit corr matrix and return weight sum for names."""
        engine = ConsensusEngine()
        # Override the correlation matrix to use our test rho
        opinion_vectors = ConsensusEngine._vectorize_opinions(opinions)
        corr = {
            n1: {n2: (1.0 if n1 == n2 else rho) for n2 in list(opinion_vectors.keys())}
            for n1 in opinion_vectors.keys()
        }
        weights = ConsensusEngine._compute_gls_weights(opinion_vectors, corr)
        return sum(weights[n] for n in names)

    def test_high_corr_pair_lower_combined_weight(self):
        """When debaters a and b are highly correlated with each other but not
        with c, GLS down-weights the (a,b) pair relative to the symmetric case.

        Note: equicorrelation (constant ρ everywhere) always yields uniform
        weights by symmetry regardless of ρ.  The discounting only appears when
        the correlation is *asymmetric* — a↔b correlated, c independent of both.
        """
        opinions = [
            _opinion("a", "BULLISH", 0.70),
            _opinion("b", "BULLISH", 0.70),
            _opinion("c", "BULLISH", 0.70),
        ]
        opinion_vectors = ConsensusEngine._vectorize_opinions(opinions)
        names = list(opinion_vectors.keys())   # ["a", "b", "c"]

        # High a↔b correlation, c independent
        corr_asymmetric = {
            "a": {"a": 1.0, "b": 0.90, "c": 0.01},
            "b": {"b": 1.0, "a": 0.90, "c": 0.01},
            "c": {"c": 1.0, "a": 0.01, "b": 0.01},
        }

        # Low correlation for all pairs (close to independent)
        corr_symmetric = {
            "a": {"a": 1.0, "b": 0.01, "c": 0.01},
            "b": {"b": 1.0, "a": 0.01, "c": 0.01},
            "c": {"c": 1.0, "a": 0.01, "b": 0.01},
        }

        w_asym = ConsensusEngine._compute_gls_weights(opinion_vectors, corr_asymmetric)
        w_sym  = ConsensusEngine._compute_gls_weights(opinion_vectors, corr_symmetric)

        # With high a↔b correlation the pair is treated as ≈1 source, not 2;
        # their combined weight must be lower than in the near-independent case
        combined_asym = w_asym["a"] + w_asym["b"]
        combined_sym  = w_sym["a"]  + w_sym["b"]
        self.assertLess(combined_asym, combined_sym)

    def test_zero_corr_equal_weights_symmetric(self):
        """With ρ=0 and identical convictions, all weights are exactly equal."""
        opinions = [_opinion(f"d{i}", "BULLISH", 0.60) for i in range(4)]
        opinion_vectors = ConsensusEngine._vectorize_opinions(opinions)
        names = list(opinion_vectors.keys())
        corr = _symmetric_corr(names, 0.0)
        weights = ConsensusEngine._compute_gls_weights(opinion_vectors, corr)
        vals = list(weights.values())
        for v in vals:
            self.assertAlmostEqual(v, vals[0], places=6)


# =============================================================================
# 5 — N_eff properties
# =============================================================================

class TestNEffProperties(unittest.TestCase):
    def test_n_eff_equals_n_for_uniform_weights(self):
        """When all N weights are 1/N, N_eff = N exactly."""
        n = 5
        weights = {f"d{i}": 1.0 / n for i in range(n)}
        n_eff = ConsensusEngine._compute_n_eff(weights)
        self.assertAlmostEqual(n_eff, float(n), places=8)

    def test_n_eff_equals_one_for_single_dominant_weight(self):
        """When one weight is 1.0 and all others 0.0, N_eff = 1."""
        weights = {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0}
        n_eff = ConsensusEngine._compute_n_eff(weights)
        self.assertAlmostEqual(n_eff, 1.0, places=8)

    def test_n_eff_bounded_by_n(self):
        """N_eff can never exceed the number of debaters."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("directional", "BULLISH", 0.70),
            _opinion("income",      "BULLISH", 0.60),
            _opinion("volatility",  "BULLISH", 0.65),
            _opinion("flow",        "BULLISH", 0.55),
            _opinion("sentiment",   "BULLISH", 0.50),
        ]
        consensus = engine.aggregate(opinions)
        self.assertLessEqual(consensus.n_eff, len(opinions) + 1e-6)

    def test_n_eff_higher_with_asymmetric_low_correlation(self):
        """Asymmetric correlation: when two of four debaters are highly correlated
        with each other but not with the other two, N_eff is lower than when all
        pairs are near-independent.

        Note: equicorrelation (constant ρ everywhere) always produces uniform
        GLS weights by symmetry, so N_eff = N regardless of ρ.  N_eff falls
        only when non-uniform weights arise from *asymmetric* correlation.
        """
        opinions = [_opinion(f"d{i}", "BULLISH", 0.70) for i in range(4)]
        opinion_vectors = ConsensusEngine._vectorize_opinions(opinions)
        names = list(opinion_vectors.keys())   # ["d0","d1","d2","d3"]

        # d0 and d1 highly correlated; d2 and d3 independent of everyone
        corr_asym = {
            names[0]: {names[0]: 1.0, names[1]: 0.90, names[2]: 0.01, names[3]: 0.01},
            names[1]: {names[0]: 0.90, names[1]: 1.0, names[2]: 0.01, names[3]: 0.01},
            names[2]: {names[0]: 0.01, names[1]: 0.01, names[2]: 1.0, names[3]: 0.01},
            names[3]: {names[0]: 0.01, names[1]: 0.01, names[2]: 0.01, names[3]: 1.0},
        }

        # Near-independent: all off-diagonal 0.01
        corr_indep = {
            n1: {n2: (1.0 if n1 == n2 else 0.01) for n2 in names}
            for n1 in names
        }

        w_asym  = ConsensusEngine._compute_gls_weights(opinion_vectors, corr_asym)
        w_indep = ConsensusEngine._compute_gls_weights(opinion_vectors, corr_indep)

        neff_asym  = ConsensusEngine._compute_n_eff(w_asym)
        neff_indep = ConsensusEngine._compute_n_eff(w_indep)

        # Near-independent case: weights ≈ uniform → N_eff ≈ 4
        # Asymmetric case: d0 and d1 down-weighted → non-uniform weights → N_eff < 4
        self.assertGreater(neff_indep, neff_asym)


# =============================================================================
# 6 — Ridge regularisation
# =============================================================================

class TestRidgeRegularisation(unittest.TestCase):
    def test_perfect_correlation_does_not_crash(self):
        """ρ=1.0 (singular matrix without ridge) must invert without error."""
        names = ["a", "b", "c"]
        opinions = {"a": 0.70, "b": 0.70, "c": 0.70}
        corr = _symmetric_corr(names, 1.0)
        Omega, _ = ConsensusEngine._build_precision_matrix(opinions, corr)
        self.assertTrue(np.all(np.isfinite(Omega)))

    def test_all_identical_opinions_does_not_crash(self):
        """Degenerate case: all debaters say NEUTRAL (σ=1.0 for all)."""
        names = [f"d{i}" for i in range(5)]
        opinions = {n: 0.0 for n in names}
        corr = _symmetric_corr(names, 0.40)
        Omega, _ = ConsensusEngine._build_precision_matrix(opinions, corr)
        self.assertTrue(np.all(np.isfinite(Omega)))

    def test_noise_floor_enforced(self):
        """σ_i is always ≥ NOISE_FLOOR even for conviction = 1.0."""
        opinions = {"certain": 1.0}   # |o| = 1.0 → 1 - 1.0 = 0 < NOISE_FLOOR
        corr = {"certain": {"certain": 1.0}}
        Omega, _ = ConsensusEngine._build_precision_matrix(opinions, corr)
        expected_sigma = _NOISE_FLOOR
        expected_Omega = 1.0 / (expected_sigma ** 2 + _RIDGE_LAMBDA)
        self.assertAlmostEqual(float(Omega[0, 0]), expected_Omega, places=4)


# =============================================================================
# 7 — End-to-end consensus properties
# =============================================================================

class TestEndToEndConsensusProperties(unittest.TestCase):
    def test_symmetric_bullbear_is_neutral(self):
        """Equal-strength bull and bear → NEUTRAL consensus."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("a", "BULLISH", 0.70),
            _opinion("b", "BEARISH", 0.70),
        ]
        consensus = engine.aggregate(opinions)
        self.assertEqual(consensus.direction, Direction.NEUTRAL)
        self.assertAlmostEqual(consensus.score, 0.5, places=8)

    def test_single_debater_weight_is_one(self):
        """A single debater receives weight = 1.0 exactly."""
        engine = ConsensusEngine()
        consensus = engine.aggregate([_opinion("solo", "BULLISH", 0.80)])
        self.assertAlmostEqual(consensus.weights["solo"], 1.0, places=8)

    def test_p_bull_plus_p_bear_not_necessarily_one(self):
        """p_bull is the probability of the BULLISH direction; it is not 1 − p_bear.
        We only assert it stays in [0, 1]."""
        engine = ConsensusEngine()
        for conviction in [0.30, 0.60, 0.90]:
            for direction in ["BULLISH", "BEARISH", "NEUTRAL"]:
                opinions = [_opinion("d", direction, conviction)]
                c = engine.aggregate(opinions)
                self.assertGreaterEqual(c.p_bull, 0.0)
                self.assertLessEqual(c.p_bull, 1.0)

    def test_weights_sum_to_one_always(self):
        """Normalised weights always sum to 1 regardless of opinions."""
        engine = ConsensusEngine()
        test_cases = [
            [_opinion("a", "BULLISH", 0.80)],
            [_opinion("a", "NEUTRAL", 0.50), _opinion("b", "NEUTRAL", 0.50)],
            [_opinion(f"d{i}", "BULLISH", 0.70) for i in range(5)],
        ]
        for opinions in test_cases:
            w = engine.aggregate(opinions).weights
            self.assertAlmostEqual(sum(w.values()), 1.0, places=8)

    def test_more_independent_debaters_raise_n_eff(self):
        """Adding a 4th independent (unknown name) debater increases N_eff."""
        engine = ConsensusEngine()
        base = [_opinion(f"d{i}", "BULLISH", 0.70) for i in range(3)]
        extended = base + [_opinion("d3", "BULLISH", 0.70)]
        n_eff_base = engine.aggregate(base).n_eff
        n_eff_ext = engine.aggregate(extended).n_eff
        self.assertGreater(n_eff_ext, n_eff_base)

    def test_p_bull_strictly_greater_half_for_bullish_consensus(self):
        """Any unanimous BULLISH consensus must produce p_bull > 0.5."""
        engine = ConsensusEngine()
        for conv in [0.30, 0.55, 0.80]:
            opinions = [_opinion(f"d{i}", "BULLISH", conv) for i in range(4)]
            consensus = engine.aggregate(opinions)
            self.assertEqual(consensus.direction, Direction.BULLISH)
            self.assertGreater(consensus.p_bull, 0.5)

    def test_consensus_deterministic_across_runs(self):
        """Identical opinions produce bit-identical consensus on repeated calls."""
        engine = ConsensusEngine()
        opinions = [
            _opinion("directional", "BULLISH", 0.75),
            _opinion("income",      "BULLISH", 0.65),
            _opinion("volatility",  "NEUTRAL", 0.50),
        ]
        c1 = engine.aggregate(opinions)
        c2 = engine.aggregate(opinions)
        self.assertEqual(c1.direction, c2.direction)
        self.assertAlmostEqual(c1.score, c2.score, places=12)
        self.assertAlmostEqual(c1.p_bull, c2.p_bull, places=12)


if __name__ == "__main__":
    unittest.main()
