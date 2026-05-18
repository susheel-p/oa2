"""Phase 3 tests — consensus engine with GLS aggregation."""

import pytest

from oa2.consensus.engine import ConsensusEngine
from oa2.consensus.state import Consensus, Direction
from oa2.debaters.base import DebaterOpinion


@pytest.fixture
def sample_opinions():
    """Create 5 sample debater opinions for testing."""
    return [
        DebaterOpinion(
            debater_name="directional",
            direction="BULLISH",
            conviction=0.75,
            reasoning="Price above VWAP",
            signals_used={"price": 520, "vwap": 519},
        ),
        DebaterOpinion(
            debater_name="income",
            direction="BULLISH",
            conviction=0.65,
            reasoning="IV expensive, sells premium",
            signals_used={"iv_rank": 0.70},
        ),
        DebaterOpinion(
            debater_name="volatility",
            direction="NEUTRAL",
            conviction=0.50,
            reasoning="Mixed vol signals",
            signals_used={"vol_exp": 1, "vol_comp": 1},
        ),
        DebaterOpinion(
            debater_name="flow",
            direction="BULLISH",
            conviction=0.60,
            reasoning="Call sweeps, low PCR",
            signals_used={"pcr": 0.50},
        ),
        DebaterOpinion(
            debater_name="sentiment",
            direction="BEARISH",
            conviction=0.40,
            reasoning="Weak bearish sentiment",
            signals_used={"composite_score": -0.15},
        ),
    ]


class TestConsensusEngine:
    """Test consensus aggregation logic."""

    def test_engine_instantiate(self):
        """Engine can be instantiated."""
        engine = ConsensusEngine()
        assert engine is not None

    def test_engine_with_regime(self):
        """Engine accepts optional regime parameter."""
        engine = ConsensusEngine(regime="3")
        assert engine.regime == "3"

    def test_unanimous_bullish(self):
        """All 5 debaters BULLISH → consensus BULLISH."""
        bullish_opinions = [
            DebaterOpinion(
                debater_name=f"debater_{i}",
                direction="BULLISH",
                conviction=0.70,
                reasoning="bullish",
                signals_used={},
            )
            for i in range(5)
        ]

        engine = ConsensusEngine()
        consensus = engine.aggregate(bullish_opinions)

        assert consensus.direction == Direction.BULLISH
        assert consensus.score > 0.7
        assert consensus.n_eff > 3.0
        assert consensus.p_bull > 0.80

    def test_unanimous_bearish(self):
        """All 5 debaters BEARISH → consensus BEARISH."""
        bearish_opinions = [
            DebaterOpinion(
                debater_name=f"debater_{i}",
                direction="BEARISH",
                conviction=0.75,
                reasoning="bearish",
                signals_used={},
            )
            for i in range(5)
        ]

        engine = ConsensusEngine()
        consensus = engine.aggregate(bearish_opinions)

        assert consensus.direction == Direction.BEARISH
        assert consensus.score < 0.3
        assert consensus.p_bull < 0.20

    def test_mixed_opinions_with_weights(self, sample_opinions):
        """Mixed opinions → weighted consensus.

        With conviction-scaled GLS the high-conviction directional debater
        (BULLISH×0.75, σ=0.25) receives the largest weight and pulls the
        normalised score toward 0.8–0.9.  The old bound of <0.8 was tuned
        to the previous broken algorithm (scalar-collapsed precision).
        """
        engine = ConsensusEngine()
        consensus = engine.aggregate(sample_opinions)

        # 3 bullish (0.75, 0.65, 0.60), 1 bearish (0.40), 1 neutral (0.50)
        # Weighted average should be bullish; not unanimous (score < 1.0)
        assert consensus.direction == Direction.BULLISH
        assert 0.5 < consensus.score < 1.0
        assert consensus.n_eff > 1.0

    def test_consensus_has_all_weights(self, sample_opinions):
        """Consensus includes weights for all debaters."""
        engine = ConsensusEngine()
        consensus = engine.aggregate(sample_opinions)

        assert len(consensus.weights) == 5
        assert "directional" in consensus.weights
        assert "income" in consensus.weights
        assert "volatility" in consensus.weights
        assert "flow" in consensus.weights
        assert "sentiment" in consensus.weights

    def test_weights_sum_to_one(self, sample_opinions):
        """GLS weights sum to 1.0."""
        engine = ConsensusEngine()
        consensus = engine.aggregate(sample_opinions)

        total_weight = sum(consensus.weights.values())
        assert abs(total_weight - 1.0) < 1e-6

    def test_n_eff_less_than_or_equal_n(self, sample_opinions):
        """Effective sample size <= actual sample size (correlation reduces it)."""
        engine = ConsensusEngine()
        consensus = engine.aggregate(sample_opinions)

        assert consensus.n_eff <= len(sample_opinions)
        assert consensus.n_eff > 0.5

    def test_empty_opinions_returns_neutral(self):
        """Empty opinion list → neutral consensus."""
        engine = ConsensusEngine()
        consensus = engine.aggregate([])

        assert consensus.direction == Direction.NEUTRAL
        assert consensus.score == 0.5
        assert consensus.p_bull == 0.5

    def test_single_opinion(self):
        """Single opinion → consensus inherits its direction."""
        opinion = DebaterOpinion(
            debater_name="directional",
            direction="BULLISH",
            conviction=0.80,
            reasoning="strong bullish",
            signals_used={},
        )

        engine = ConsensusEngine()
        consensus = engine.aggregate([opinion])

        assert consensus.direction == Direction.BULLISH
        assert consensus.score > 0.7
        assert consensus.weights["directional"] == 1.0

    def test_high_conviction_gets_higher_weight(self):
        """High conviction debater gets higher GLS weight."""
        high_conv_opinion = DebaterOpinion(
            debater_name="high_conviction",
            direction="BULLISH",
            conviction=0.95,
            reasoning="very confident bullish",
            signals_used={},
        )
        low_conv_opinion = DebaterOpinion(
            debater_name="low_conviction",
            direction="BULLISH",
            conviction=0.30,
            reasoning="weakly bullish",
            signals_used={},
        )
        # Add neutral fillers
        fillers = [
            DebaterOpinion(
                debater_name=f"filler_{i}",
                direction="NEUTRAL",
                conviction=0.50,
                reasoning="neutral",
                signals_used={},
            )
            for i in range(3)
        ]

        engine = ConsensusEngine()
        consensus = engine.aggregate([high_conv_opinion, low_conv_opinion] + fillers)

        # High conviction should have higher weight
        assert consensus.weights["high_conviction"] > consensus.weights["low_conviction"]

    def test_neutral_opinions_excluded_from_consensus(self):
        """Neutral opinions (score=0) don't pull consensus toward neutral."""
        bullish_opinions = [
            DebaterOpinion(
                debater_name="b1",
                direction="BULLISH",
                conviction=0.80,
                reasoning="bullish",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="b2",
                direction="BULLISH",
                conviction=0.70,
                reasoning="bullish",
                signals_used={},
            ),
        ]
        neutral_opinions = [
            DebaterOpinion(
                debater_name=f"n{i}",
                direction="NEUTRAL",
                conviction=0.50,
                reasoning="neutral",
                signals_used={},
            )
            for i in range(3)
        ]

        engine = ConsensusEngine()
        consensus = engine.aggregate(bullish_opinions + neutral_opinions)

        # Should still be clearly bullish
        assert consensus.direction == Direction.BULLISH
        assert consensus.score > 0.6

    def test_p_bull_bounded_zero_one(self, sample_opinions):
        """p_bull probability always in [0, 1]."""
        engine = ConsensusEngine()
        consensus = engine.aggregate(sample_opinions)

        assert 0.0 <= consensus.p_bull <= 1.0

    def test_p_bull_reflects_direction(self):
        """Bullish consensus → p_bull > 0.5, bearish → p_bull < 0.5."""
        bullish = [
            DebaterOpinion(
                debater_name=f"b{i}",
                direction="BULLISH",
                conviction=0.75,
                reasoning="bullish",
                signals_used={},
            )
            for i in range(5)
        ]

        bearish = [
            DebaterOpinion(
                debater_name=f"b{i}",
                direction="BEARISH",
                conviction=0.75,
                reasoning="bearish",
                signals_used={},
            )
            for i in range(5)
        ]

        engine = ConsensusEngine()
        bullish_consensus = engine.aggregate(bullish)
        bearish_consensus = engine.aggregate(bearish)

        assert bullish_consensus.p_bull > 0.5
        assert bearish_consensus.p_bull < 0.5

    def test_consensus_reproducible(self, sample_opinions):
        """Same opinions → same consensus (reproducible)."""
        engine = ConsensusEngine()
        consensus1 = engine.aggregate(sample_opinions)
        consensus2 = engine.aggregate(sample_opinions)

        assert consensus1.direction == consensus2.direction
        assert abs(consensus1.score - consensus2.score) < 1e-6
        assert abs(consensus1.p_bull - consensus2.p_bull) < 1e-6

    def test_vectorization(self):
        """Opinion vectorization: BULLISH × conv → +conv, BEARISH × conv → -conv, NEUTRAL → 0."""
        opinions = [
            DebaterOpinion(
                debater_name="bullish",
                direction="BULLISH",
                conviction=0.75,
                reasoning="bullish",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="bearish",
                direction="BEARISH",
                conviction=0.60,
                reasoning="bearish",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="neutral",
                direction="NEUTRAL",
                conviction=0.50,
                reasoning="neutral",
                signals_used={},
            ),
        ]

        engine = ConsensusEngine()
        vectors = engine._vectorize_opinions(opinions)

        assert vectors["bullish"] == 0.75
        assert vectors["bearish"] == -0.60
        assert vectors["neutral"] == 0.0

    def test_score_zero_point_five_is_neutral(self):
        """Consensus score of exactly 0.5 should be NEUTRAL."""
        # Create a perfectly balanced set
        equal_bullish = DebaterOpinion(
            debater_name="bullish",
            direction="BULLISH",
            conviction=0.50,
            reasoning="mildly bullish",
            signals_used={},
        )
        equal_bearish = DebaterOpinion(
            debater_name="bearish",
            direction="BEARISH",
            conviction=0.50,
            reasoning="mildly bearish",
            signals_used={},
        )
        neutral = DebaterOpinion(
            debater_name="neutral",
            direction="NEUTRAL",
            conviction=0.50,
            reasoning="neutral",
            signals_used={},
        )

        engine = ConsensusEngine()
        consensus = engine.aggregate([equal_bullish, equal_bearish, neutral])

        # Should be neutral
        assert consensus.direction == Direction.NEUTRAL
        assert abs(consensus.score - 0.5) < 0.15  # Allow some tolerance

    def test_high_n_eff_with_independent_opinions(self):
        """Independent opinions (different signals) → higher N_eff."""
        # Create opinions that are less correlated
        opinions = [
            DebaterOpinion(
                debater_name="directional",
                direction="BULLISH",
                conviction=0.70,
                reasoning="price momentum",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="income",
                direction="BULLISH",
                conviction=0.60,
                reasoning="IV expensive",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="volatility",
                direction="BULLISH",
                conviction=0.65,
                reasoning="vol expansion",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="flow",
                direction="BULLISH",
                conviction=0.55,
                reasoning="call sweeps",
                signals_used={},
            ),
            DebaterOpinion(
                debater_name="sentiment",
                direction="BULLISH",
                conviction=0.50,
                reasoning="bullish sentiment",
                signals_used={},
            ),
        ]

        engine = ConsensusEngine()
        consensus = engine.aggregate(opinions)

        # With all same direction, N_eff should be reasonably high
        # (correlation reduces it but not too much if all agreeing)
        assert consensus.n_eff > 2.0