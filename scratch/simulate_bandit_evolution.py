import numpy as np
from oa2.performance.bandit import BanditEngine
from oa2.consensus.engine import ConsensusEngine
from oa2.debaters.base import DebaterOpinion, Direction

def simulate_regime_shift():
    print("=" * 70)
    print("SIMULATING ADAPTIVE BANDIT & DYNAMIC DEAD-BAND EVOLUTION")
    print("=" * 70)
    
    # 260 days (52 weeks of 5 days)
    n_days = 260
    
    # We will run 3 configurations:
    # 1. Baseline: Static equal weights, static dead-band (0.10)
    # 2. Bandit No-Decay: Online learning, decay=1.0 (no decay), dynamic dead-band
    # 3. Bandit Decay: Online learning, decay=0.90 (fast decay), dynamic dead-band
    
    configs = [
        {"name": "1. Static Baseline", "use_bandit": False, "decay": 1.0, "dynamic_db": False},
        {"name": "2. Bandit (No Decay)", "use_bandit": True, "decay": 1.0, "dynamic_db": True},
        {"name": "3. Bandit (Decay 0.90)", "use_bandit": True, "decay": 0.90, "dynamic_db": True},
    ]
    
    # Set seed for reproducible simulation
    np.random.seed(42)
    
    # Pre-generate true outcomes:
    # 55% BULLISH, 45% BEARISH (excluding neutral)
    true_outcomes = np.random.choice(["BULLISH", "BEARISH"], size=n_days, p=[0.55, 0.45])
    
    for cfg in configs:
        bandit = BanditEngine()
        correct_trades = 0
        total_active_trades = 0
        
        # We will track weekly results
        weekly_returns = []
        weekly_accuracy = []
        current_week_returns = []
        current_week_hits = 0
        current_week_active = 0
        
        # Track weights for directional vs flow vs sentiment in regime "3"
        regime_weights_history = []
        deadband_sizes = []
        
        for day in range(n_days):
            week_idx = day // 5
            
            # Keep regime_id = 3 for the entire duration to test unlearning within the same regime
            regime_id = 3
            if week_idx < 26:
                # Directional is very accurate (80%), Flow is poor (40%), Sentiment is mediocre (50%)
                p_directional = 0.80
                p_flow = 0.40
                p_sentiment = 0.50
            else:
                # REGIME REVERSAL!
                # Directional becomes poor (30%), Flow becomes very accurate (80%), Sentiment stays mediocre (50%)
                p_directional = 0.30
                p_flow = 0.80
                p_sentiment = 0.50
                
            outcome = true_outcomes[day]
            opposite = "BEARISH" if outcome == "BULLISH" else "BULLISH"
            
            # Generate opinions:
            # Directional opinion
            dir_correct = np.random.rand() < p_directional
            dir_opinion = outcome if dir_correct else opposite
            op_directional = DebaterOpinion(
                debater_name="directional",
                direction=dir_opinion,
                conviction=0.70,
                reasoning="dir",
                signals_used={}
            )
            
            # Flow opinion
            flow_correct = np.random.rand() < p_flow
            flow_opinion = outcome if flow_correct else opposite
            op_flow = DebaterOpinion(
                debater_name="flow",
                direction=flow_opinion,
                conviction=0.65,
                reasoning="flow",
                signals_used={}
            )
            
            # Sentiment opinion
            sent_correct = np.random.rand() < p_sentiment
            sent_opinion = outcome if sent_correct else opposite
            op_sentiment = DebaterOpinion(
                debater_name="sentiment",
                direction=sent_opinion,
                conviction=0.60,
                reasoning="sent",
                signals_used={}
            )
            
            opinions = [op_directional, op_flow, op_sentiment]
            
            # Get prior weights from bandit
            prior_weights = None
            if cfg["use_bandit"]:
                prior_weights = {
                    "directional": bandit.get_weight("directional", regime_id),
                    "flow": bandit.get_weight("flow", regime_id),
                    "sentiment": bandit.get_weight("sentiment", regime_id),
                }
                if week_idx in (25, 26, 28, 30) and day % 5 == 0:
                    regime_weights_history.append((week_idx, {k: round(v, 3) for k, v in prior_weights.items()}))
            
            # Aggregate consensus
            engine = ConsensusEngine(prior_weights=prior_weights, dynamic_deadband=cfg["dynamic_db"])
            
            # Let's inspect the dead-band size for this aggregation
            if cfg["dynamic_db"]:
                # The dead-band width is calculated based on active scores std dev
                scores = [0.70 if op_directional.direction == "BULLISH" else -0.70,
                          0.65 if op_flow.direction == "BULLISH" else -0.65,
                          0.60 if op_sentiment.direction == "BULLISH" else -0.60]
                dispersion = np.std(scores)
                db_size = 0.10 + 0.10 * dispersion
                deadband_sizes.append(db_size)
            
            consensus = engine.aggregate(opinions)
            pred_dir = consensus.direction.value
            
            # Simulate return
            if pred_dir == "NEUTRAL":
                day_return = 0.0
            else:
                total_active_trades += 1
                current_week_active += 1
                hit = (pred_dir == outcome)
                day_return = 0.10 if hit else -0.10
                if hit:
                    correct_trades += 1
                    current_week_hits += 1
                    
            current_week_returns.append(day_return)
            
            # Update bandit online
            if cfg["use_bandit"]:
                bandit.update("directional", regime_id, hit=(op_directional.direction == outcome), decay=cfg["decay"])
                bandit.update("flow", regime_id, hit=(op_flow.direction == outcome), decay=cfg["decay"])
                bandit.update("sentiment", regime_id, hit=(op_sentiment.direction == outcome), decay=cfg["decay"])
                
            # End of week tracking
            if (day + 1) % 5 == 0:
                weekly_returns.append(sum(current_week_returns))
                weekly_accuracy.append(current_week_hits / current_week_active if current_week_active else 0.0)
                current_week_returns = []
                current_week_hits = 0
                current_week_active = 0
                
        # Report findings
        print(f"\nConfiguration: {cfg['name']}")
        print(f"  Total Active Trades: {total_active_trades}")
        print(f"  Overall Accuracy (Active): {correct_trades / total_active_trades * 100:.1f}%" if total_active_trades else "  No active trades")
        print(f"  Total Return: {sum(weekly_returns)*100:+.1f}%")
        
        # Show performance before and after regime shift
        pre_shift_returns = sum(weekly_returns[:26])
        post_shift_returns = sum(weekly_returns[26:])
        print(f"  Pre-shift Return (Weeks 0-25): {pre_shift_returns*100:+.1f}%")
        print(f"  Post-shift Return (Weeks 26-51): {post_shift_returns*100:+.1f}%")
        
        if cfg["use_bandit"]:
            print("  Bandit weight evolution around regime shift:")
            for week, weights in regime_weights_history:
                print(f"    Week {week:02d}: {weights}")
                
        if cfg["dynamic_db"]:
            print(f"  Average Dynamic Dead-band: {np.mean(deadband_sizes):.3f}")

if __name__ == "__main__":
    simulate_regime_shift()
