import numpy as np
from tradingbot.performance.bandit import BanditEngine, BetaPosterior
from tradingbot.consensus.engine import ConsensusEngine
from tradingbot.consensus.state import Direction as ConsensusDirection
from tradingbot.debaters.base import DebaterOpinion

def test_bandit_decay():
    engine = BanditEngine()
    # Apply 10 hits (alpha = 11.0, beta = 1.0)
    for _ in range(10):
        engine.update("directional", 3, hit=True)
    
    # Weight should be very high
    w1 = engine.get_weight("directional", 3, use_mean=True)
    assert abs(w1 - (11.0 / 12.0)) < 1e-6
    
    # Now update with decay 0.5 and a miss
    # alpha should decay to: (11.0 - 1.0) * 0.5 + 1.0 = 6.0
    # beta should decay to: (1.0 - 1.0) * 0.5 + 1.0 = 1.0
    # Then miss adds 1 to beta -> alpha = 6.0, beta = 2.0
    engine.update("directional", 3, hit=False, decay=0.5)
    
    posterior = engine._posteriors[("directional", 3)]
    assert abs(posterior.alpha - 6.0) < 1e-6
    assert abs(posterior.beta - 2.0) < 1e-6
    
    # Weight should now be 6.0 / (6.0 + 2.0) = 0.75
    w2 = engine.get_weight("directional", 3, use_mean=True)
    assert abs(w2 - 0.75) < 1e-6

def test_dynamic_deadband_agreement():
    # When debaters agree, the dead-band remains close to 0.10
    op1 = DebaterOpinion(
        debater_name="d1",
        direction="BULLISH",
        conviction=0.8,
        reasoning="bullish",
        signals_used={},
    )
    op2 = DebaterOpinion(
        debater_name="d2",
        direction="BULLISH",
        conviction=0.8,
        reasoning="bullish",
        signals_used={},
    )
    
    # With dynamic deadband enabled
    engine = ConsensusEngine(dynamic_deadband=True)
    consensus = engine.aggregate([op1, op2])
    assert consensus.direction == ConsensusDirection.BULLISH

def test_dynamic_deadband_disagreement():
    # When debaters disagree, the dead-band is wider.
    # We choose weights and convictions such that raw_score is inside the widened dead-band
    # but would have been outside the baseline 0.10 deadband.
    op1 = DebaterOpinion(
        debater_name="d1",
        direction="BULLISH",
        conviction=0.12,
        reasoning="mild bullish",
        signals_used={},
    )
    op2 = DebaterOpinion(
        debater_name="d2",
        direction="BEARISH",
        conviction=0.08,
        reasoning="mild bearish",
        signals_used={},
    )
    
    # We pass prior weights that heavily favor d1, so raw_score is biased towards d1.
    # Let's say: prior_weights = {"d1": 10.0, "d2": 1.0}
    # This leads to a raw_score around ~ 0.10 - 0.11.
    # But because they disagree (active scores: 0.12 and -0.08), the standard deviation is 0.10.
    # Widened dead-band is 0.10 + 0.10 * 0.10 = 0.11.
    
    # Let's verify consensus with dynamic deadband is NEUTRAL, while without it is BULLISH.
    engine_no_db = ConsensusEngine(prior_weights={"d1": 10.0, "d2": 1.0}, dynamic_deadband=False)
    consensus_no_db = engine_no_db.aggregate([op1, op2])
    
    engine_db = ConsensusEngine(prior_weights={"d1": 10.0, "d2": 1.0}, dynamic_deadband=True)
    consensus_db = engine_db.aggregate([op1, op2])
    
    # Check that they differ due to the widened deadband
    assert consensus_no_db.direction == ConsensusDirection.BULLISH
    assert consensus_db.direction == ConsensusDirection.NEUTRAL
