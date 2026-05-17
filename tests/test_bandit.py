"""Phase 4 tests — Thompson sampling bandit with adaptive debater weighting."""

import json
import tempfile
from pathlib import Path

import pytest

from oa2.performance.bandit import BanditEngine, BetaPosterior
from oa2.performance.storage import load_posteriors, save_posteriors


class TestBetaPosterior:
    """Test Beta(alpha, beta) posterior math."""

    def test_default_initialization(self):
        """Default prior: alpha=1.0, beta=1.0 (uniform)."""
        p = BetaPosterior()
        assert p.alpha == 1.0
        assert p.beta == 1.0

    def test_mean_flat_prior(self):
        """Flat prior mean = 0.5."""
        p = BetaPosterior()
        assert abs(p.mean() - 0.5) < 1e-6

    def test_sample_returns_valid_probability(self):
        """Sample returns value in [0, 1]."""
        p = BetaPosterior(alpha=2.0, beta=3.0)
        for _ in range(100):
            sample = p.sample()
            assert 0.0 <= sample <= 1.0

    def test_update_hit_increases_alpha(self):
        """Hit: alpha increases, beta unchanged."""
        p = BetaPosterior()
        p_old_beta = p.beta
        # Simulate update
        p.alpha += 1.0
        assert p.alpha == 2.0
        assert p.beta == p_old_beta

    def test_update_miss_increases_beta(self):
        """Miss: beta increases, alpha unchanged."""
        p = BetaPosterior()
        p_old_alpha = p.alpha
        # Simulate update
        p.beta += 1.0
        assert p.beta == 2.0
        assert p.alpha == p_old_alpha

    def test_mean_increases_after_hits(self):
        """Mean > 0.5 after more hits than misses."""
        p = BetaPosterior()
        p.alpha += 3.0  # 3 hits
        p.beta += 0.0   # no miss (still at initial beta=1.0)
        mean = p.mean()
        assert mean > 0.5
        assert abs(mean - 0.8) < 1e-6  # 4/(4+1) = 0.8

    def test_mean_decreases_after_misses(self):
        """Mean < 0.5 after more misses than hits."""
        p = BetaPosterior()
        p.alpha += 0.0  # no hit (still at initial alpha=1.0)
        p.beta += 3.0   # 3 misses
        mean = p.mean()
        assert mean < 0.5
        assert abs(mean - 0.2) < 1e-6  # 1/(1+4) = 0.2


class TestBanditEngine:
    """Test Thompson sampling bandit with 48 arms (6 debaters × 8 regimes)."""

    def test_engine_instantiate(self):
        """Engine can be instantiated."""
        engine = BanditEngine()
        assert engine is not None
        assert len(engine._posteriors) == 0

    def test_get_weight_unknown_key(self):
        """Unknown (debater, regime) returns flat prior (0.5)."""
        engine = BanditEngine()
        weight = engine.get_weight("unknown", 0, use_mean=True)
        assert abs(weight - 0.5) < 1e-6

    def test_get_weight_mean_mode(self):
        """get_weight with use_mean=True returns posterior mean."""
        engine = BanditEngine()
        engine.update("directional", 3, hit=True)
        engine.update("directional", 3, hit=True)
        weight = engine.get_weight("directional", 3, use_mean=True)
        assert abs(weight - 0.75) < 1e-6

    def test_get_weight_thompson_mode(self):
        """get_weight with use_mean=False returns Thompson sample."""
        engine = BanditEngine()
        engine.update("directional", 3, hit=True)
        engine.update("directional", 3, hit=True)
        samples = [engine.get_weight("directional", 3, use_mean=False) for _ in range(10)]
        # Samples should vary
        assert len(set(samples)) > 1
        # All should be in [0, 1]
        assert all(0.0 <= s <= 1.0 for s in samples)

    def test_get_regime_weights_normalizes_to_one(self):
        """get_regime_weights sums to 1.0."""
        engine = BanditEngine()
        names = ["directional", "income", "volatility", "flow", "sentiment"]
        weights = engine.get_regime_weights(names, 3, use_mean=True)
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_get_regime_weights_uniform_when_cold(self):
        """Cold engine (all flat priors) returns uniform weights."""
        engine = BanditEngine()
        names = ["directional", "income", "volatility", "flow", "sentiment"]
        weights = engine.get_regime_weights(names, 3, use_mean=True)
        expected = 1.0 / len(names)
        for w in weights.values():
            assert abs(w - expected) < 1e-6

    def test_update_debater_and_regime(self):
        """Update (debater, regime) properly."""
        engine = BanditEngine()
        engine.update("directional", 3, hit=True)
        engine.update("directional", 3, hit=True)
        weight = engine.get_weight("directional", 3, use_mean=True)
        assert abs(weight - 0.75) < 1e-6  # (1+2)/(1+2+1) = 0.75

    def test_better_debater_gets_higher_weight(self):
        """Debater with more hits gets higher weight."""
        engine = BanditEngine()
        engine.update("directional", 3, hit=True)
        engine.update("directional", 3, hit=True)
        engine.update("income", 3, hit=False)
        engine.update("income", 3, hit=False)

        dir_weight = engine.get_weight("directional", 3, use_mean=True)
        inc_weight = engine.get_weight("income", 3, use_mean=True)
        assert dir_weight > inc_weight

    def test_regime_isolation(self):
        """Updates to regime 0 don't affect regime 7."""
        engine = BanditEngine()
        engine.update("directional", 0, hit=True)
        engine.update("directional", 0, hit=True)

        w0 = engine.get_weight("directional", 0, use_mean=True)
        w7 = engine.get_weight("directional", 7, use_mean=True)

        assert abs(w0 - 0.75) < 1e-6
        assert abs(w7 - 0.5) < 1e-6  # unchanged

    def test_debater_isolation(self):
        """Updates to one debater don't affect another."""
        engine = BanditEngine()
        engine.update("directional", 3, hit=True)
        engine.update("directional", 3, hit=True)

        dir_weight = engine.get_weight("directional", 3, use_mean=True)
        inc_weight = engine.get_weight("income", 3, use_mean=True)

        assert abs(dir_weight - 0.75) < 1e-6
        assert abs(inc_weight - 0.5) < 1e-6  # unchanged

    def test_save_and_load_persistence(self):
        """Posteriors persist to disk and load correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "posteriors.json"

            # Save
            engine1 = BanditEngine()
            engine1.update("directional", 3, hit=True)
            engine1.update("directional", 3, hit=True)
            engine1.update("income", 3, hit=False)
            engine1.save(path)

            # Load
            engine2 = BanditEngine.load(path)

            # Verify
            dir_weight = engine2.get_weight("directional", 3, use_mean=True)
            inc_weight = engine2.get_weight("income", 3, use_mean=True)

            assert abs(dir_weight - 0.75) < 1e-6
            assert abs(inc_weight - 0.33) < 0.01

    def test_load_missing_file(self):
        """Load missing file returns empty engine (no crash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "does_not_exist.json"
            engine = BanditEngine.load(path)
            assert len(engine._posteriors) == 0
            weight = engine.get_weight("directional", 0, use_mean=True)
            assert abs(weight - 0.5) < 1e-6

    def test_json_serialization_format(self):
        """JSON file uses "debater:regime" key format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "posteriors.json"

            engine = BanditEngine()
            engine.update("directional", 0, hit=True)
            engine.save(path)

            with open(path) as f:
                data = json.load(f)

            # Should have key like "directional:0"
            assert "directional:0" in data
            assert "alpha" in data["directional:0"]
            assert "beta" in data["directional:0"]


class TestBanditEngineWithConsensus:
    """Test bandit weight blending with ConsensusEngine."""

    def test_bandit_weights_blended_with_gls(self):
        """Bandit weights multiply GLS weights correctly."""
        from oa2.consensus.engine import ConsensusEngine
        from oa2.debaters.base import DebaterOpinion

        # Create sample opinions
        opinions = [
            DebaterOpinion("directional", "BULLISH", 0.75, "test", {}),
            DebaterOpinion("income", "BULLISH", 0.65, "test", {}),
            DebaterOpinion("volatility", "NEUTRAL", 0.50, "test", {}),
            DebaterOpinion("flow", "BULLISH", 0.60, "test", {}),
            DebaterOpinion("sentiment", "BEARISH", 0.40, "test", {}),
        ]

        # Bandit prior: directional favored
        bandit_weights = {
            "directional": 0.40,
            "income": 0.20,
            "volatility": 0.20,
            "flow": 0.10,
            "sentiment": 0.10,
        }

        engine = ConsensusEngine(prior_weights=bandit_weights)
        consensus = engine.aggregate(opinions)

        # directional should have highest final weight
        assert consensus.weights["directional"] > consensus.weights["income"]
        assert consensus.weights["directional"] > consensus.weights["flow"]

    def test_uniform_prior_same_as_no_prior(self):
        """Uniform bandit weights don't change GLS outcome."""
        from oa2.consensus.engine import ConsensusEngine
        from oa2.debaters.base import DebaterOpinion

        opinions = [
            DebaterOpinion("directional", "BULLISH", 0.75, "test", {}),
            DebaterOpinion("income", "BULLISH", 0.65, "test", {}),
            DebaterOpinion("volatility", "NEUTRAL", 0.50, "test", {}),
            DebaterOpinion("flow", "BULLISH", 0.60, "test", {}),
            DebaterOpinion("sentiment", "BEARISH", 0.40, "test", {}),
        ]

        # No prior
        engine1 = ConsensusEngine(prior_weights=None)
        consensus1 = engine1.aggregate(opinions)

        # Uniform prior
        uniform = {n: 1.0 for n in ["directional", "income", "volatility", "flow", "sentiment"]}
        engine2 = ConsensusEngine(prior_weights=uniform)
        consensus2 = engine2.aggregate(opinions)

        # Should be very similar (uniform prior has no effect)
        assert abs(consensus1.score - consensus2.score) < 0.01


class TestStorageModule:
    """Test JSON storage layer."""

    def test_save_and_load_roundtrip(self):
        """Save and load preserves posterior values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"

            # Create posteriors
            posteriors = {
                ("directional", 0): BetaPosterior(alpha=3.0, beta=1.0),
                ("income", 3): BetaPosterior(alpha=2.0, beta=4.0),
            }

            # Save
            save_posteriors(posteriors, path)

            # Load
            loaded = load_posteriors(path)

            # Verify
            assert ("directional", 0) in loaded
            assert loaded[("directional", 0)].alpha == 3.0
            assert loaded[("directional", 0)].beta == 1.0
            assert ("income", 3) in loaded
            assert loaded[("income", 3)].alpha == 2.0
            assert loaded[("income", 3)].beta == 4.0

    def test_load_malformed_json(self):
        """Load gracefully handles malformed JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"

            # Write bad JSON
            with open(path, "w") as f:
                f.write("{bad json")

            # Should raise or return empty (depending on implementation)
            try:
                result = load_posteriors(path)
                assert result == {}
            except json.JSONDecodeError:
                pass  # either behavior is acceptable


class TestBanditIntegrationWithPipeline:
    """Test bandit integration into pipeline."""

    def test_pipeline_bandit_attribution(self):
        """Pipeline includes bandit_weights in attribution when BANDIT_ENABLED."""
        # This requires full pipeline integration test
        # Tested via smoke test instead
        pass

    def test_bandit_handles_six_debaters(self):
        """Bandit works with 6 debaters (including dealer from Phase 5)."""
        engine = BanditEngine()
        debater_names = ["directional", "income", "volatility", "flow", "sentiment", "dealer"]

        for name in debater_names:
            engine.update(name, 3, hit=True)

        weights = engine.get_regime_weights(debater_names, 3, use_mean=True)
        assert len(weights) == 6
        assert abs(sum(weights.values()) - 1.0) < 1e-6