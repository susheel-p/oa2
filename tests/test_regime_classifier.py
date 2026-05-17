"""Phase 2 tests — regime classifier with 8-bucket vol × trend state machine."""

import pytest

from oa2.regime.classifier import RegimeClassifier
from oa2.regime.state import RegimeClassification, TrendState, VolState


class TestRegimeClassifier:
    """Test regime classification logic."""

    def test_classifier_instantiate(self):
        """Classifier can be instantiated."""
        classifier = RegimeClassifier()
        assert classifier is not None

    def test_vol_compression_normal_trend(self):
        """Low IV rank + trending price → VOL_COMPRESSION + TRENDING (regime 0)."""
        context = {
            "current_price": 531.0,
            "prices_20d": [500.0] + [500.0 + i * 1.6 for i in range(19)],  # strong uptrend
            "vol_regime": {
                "iv_rank": 0.20,
                "rv_iv_ratio": 0.75,
                "vix": 12.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.vol_state == VolState.VOL_COMPRESSION
        assert regime.trend_state == TrendState.TRENDING
        assert regime.regime_id == 0
        assert regime.confidence > 0.5

    def test_vol_expansion_mean_revert(self):
        """High IV rank + mean-reverting price → VOL_EXPANSION + MEAN_REVERT (regime 7)."""
        context = {
            "current_price": 485.0,
            "prices_20d": [520.0] + [520.0 - i * 1.75 for i in range(19)],  # strong downtrend
            "vol_regime": {
                "iv_rank": 0.75,
                "rv_iv_ratio": 1.05,
                "vix": 28.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.vol_state == VolState.VOL_EXPANSION
        assert regime.trend_state == TrendState.MEAN_REVERTING
        assert regime.regime_id == 7
        assert regime.confidence > 0.4

    def test_normal_neutral_trend(self):
        """Mid IV rank + flat price → NORMAL + NEUTRAL_TREND (regime 5)."""
        context = {
            "current_price": 510.0,
            "prices_20d": [510.0] * 20,  # flat
            "vol_regime": {
                "iv_rank": 0.50,
                "rv_iv_ratio": 1.0,
                "vix": 20.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.vol_state == VolState.NORMAL
        assert regime.trend_state == TrendState.NEUTRAL_TREND
        assert regime.regime_id == 5

    def test_crisis_overrides_vol_state(self):
        """High RV/IV ratio (>1.20) → CRISIS regardless of IV rank."""
        context = {
            "current_price": 510.0,
            "prices_20d": [505.0, 508.0, 510.0] + [510.0] * 17,
            "vol_regime": {
                "iv_rank": 0.40,  # Normal
                "rv_iv_ratio": 1.30,  # Crisis signal
                "vix": 22.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.vol_state == VolState.CRISIS
        # Crisis maps to vol_expansion in regime_id
        assert regime.regime_id in [6, 7]

    def test_crisis_high_vix(self):
        """VIX > 35 → CRISIS regardless of IV rank."""
        context = {
            "current_price": 510.0,
            "prices_20d": [510.0] * 20,
            "vol_regime": {
                "iv_rank": 0.30,  # Compression
                "rv_iv_ratio": 0.95,  # Normal
                "vix": 40.0,  # Crisis signal
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.vol_state == VolState.CRISIS

    def test_missing_price_history_defaults_to_neutral_trend(self):
        """No price history → neutral trend."""
        context = {
            "current_price": 510.0,
            "prices_20d": [],
            "vol_regime": {
                "iv_rank": 0.70,
                "rv_iv_ratio": 1.0,
                "vix": 20.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.trend_state == TrendState.NEUTRAL_TREND
        assert regime.vol_state == VolState.VOL_EXPANSION
        assert regime.regime_id == 6

    def test_confidence_high_when_signals_extreme(self):
        """High confidence when IV rank, RV/IV, price slope, VIX all extreme."""
        context = {
            "current_price": 530.0,
            "prices_20d": [500.0] + [500.0 + i * 1.5 for i in range(19)],
            "vol_regime": {
                "iv_rank": 0.05,  # Extreme low
                "rv_iv_ratio": 0.50,  # Extreme low
                "vix": 10.0,  # Extreme low
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.confidence > 0.7

    def test_confidence_low_when_signals_neutral(self):
        """Low confidence when signals are all around neutral."""
        context = {
            "current_price": 510.0,
            "prices_20d": [510.0] * 20,
            "vol_regime": {
                "iv_rank": 0.50,
                "rv_iv_ratio": 1.0,
                "vix": 20.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        assert regime.confidence < 0.6

    def test_posterior_distribution(self):
        """Posterior distribution sums to ~1.0."""
        context = {
            "current_price": 520.0,
            "prices_20d": [500.0] + [510.0 + i * 0.5 for i in range(19)],
            "vol_regime": {
                "iv_rank": 0.60,
                "rv_iv_ratio": 1.0,
                "vix": 20.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        total_posterior = sum(regime.posterior.values())
        assert 0.99 <= total_posterior <= 1.01

    def test_posterior_gives_highest_prob_to_actual_regime(self):
        """Posterior should weight actual regime higher than others."""
        context = {
            "current_price": 525.0,
            "prices_20d": [500.0] + [500.0 + i * 1.25 for i in range(19)],
            "vol_regime": {
                "iv_rank": 0.75,
                "rv_iv_ratio": 1.0,
                "vix": 22.0,
            },
        }

        classifier = RegimeClassifier()
        regime = classifier.classify(context)

        # Primary signal: VOL_EXP + TRENDING → regime 6
        # Find regime 6 posterior (VOL_EXP_TREND key)
        actual_prob = regime.posterior.get("VOL_EXP_TREND", 0.0)
        avg_other = sum(p for k, p in regime.posterior.items() if k != "VOL_EXP_TREND") / len(
            [k for k in regime.posterior if k != "VOL_EXP_TREND"]
        )

        assert actual_prob > avg_other

    def test_vol_compression_bucket(self):
        """IV rank < 0.35 → VOL_COMPRESSION."""
        classifier = RegimeClassifier()

        for iv_rank in [0.10, 0.25, 0.34]:
            context = {
                "current_price": 510.0,
                "prices_20d": [510.0] * 20,
                "vol_regime": {
                    "iv_rank": iv_rank,
                    "rv_iv_ratio": 1.0,
                    "vix": 15.0,
                },
            }
            regime = classifier.classify(context)
            assert regime.vol_state == VolState.VOL_COMPRESSION, f"Failed for iv_rank={iv_rank}"

    def test_normal_bucket(self):
        """0.35 <= IV rank <= 0.65 → NORMAL."""
        classifier = RegimeClassifier()

        for iv_rank in [0.35, 0.50, 0.65]:
            context = {
                "current_price": 510.0,
                "prices_20d": [510.0] * 20,
                "vol_regime": {
                    "iv_rank": iv_rank,
                    "rv_iv_ratio": 1.0,
                    "vix": 15.0,
                },
            }
            regime = classifier.classify(context)
            assert regime.vol_state == VolState.NORMAL, f"Failed for iv_rank={iv_rank}"

    def test_vol_expansion_bucket(self):
        """IV rank > 0.65 → VOL_EXPANSION."""
        classifier = RegimeClassifier()

        for iv_rank in [0.66, 0.75, 0.95]:
            context = {
                "current_price": 510.0,
                "prices_20d": [510.0] * 20,
                "vol_regime": {
                    "iv_rank": iv_rank,
                    "rv_iv_ratio": 1.0,
                    "vix": 22.0,
                },
            }
            regime = classifier.classify(context)
            assert regime.vol_state == VolState.VOL_EXPANSION, f"Failed for iv_rank={iv_rank}"

    def test_regime_classification_is_reproducible(self):
        """Same context → same regime_id, confidence, state."""
        context = {
            "current_price": 515.0,
            "prices_20d": [500.0] + [500.0 + i * 0.75 for i in range(19)],
            "vol_regime": {
                "iv_rank": 0.55,
                "rv_iv_ratio": 0.95,
                "vix": 19.0,
            },
        }

        classifier = RegimeClassifier()
        regime1 = classifier.classify(context)
        regime2 = classifier.classify(context)

        assert regime1.regime_id == regime2.regime_id
        assert regime1.vol_state == regime2.vol_state
        assert regime1.trend_state == regime2.trend_state
        assert abs(regime1.confidence - regime2.confidence) < 1e-6