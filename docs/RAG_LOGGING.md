# RAG Learning Status & Logging

## Overview

The trading bot now records comprehensive RAG (Retrieval-Augmented Generation) learning data to show:
1. **When RAG learning is applied** to trades
2. **Which KB values** (ticker multipliers, regime multipliers) were used
3. **Thompson posterior confidence** used in position sizing
4. **Debater accuracy** per regime and conviction bucket

This document explains how to view and interpret RAG learning status.

---

## Quick Status Check

View current RAG learning state with:

```bash
python scripts/rag_status.py                    # Full status report
python scripts/rag_status.py --ticker SPY       # Status for one ticker
python scripts/rag_status.py --posteriors-only  # Show Thompson posteriors only
```

**Output includes:**
- KB last updated time and window (e.g., 60 days)
- Ticker performance (hit rate, dollar-weighted win %, blacklist status)
- Regime performance (hit rate, conviction multiplier applied)
- Thompson posteriors (mean, std, confidence per debater per regime)
- Debater calibration (which conviction buckets are accurate)

---

## Decision Log Attribution

Every trade decision logs RAG learning context. Look for `"rag_learning"` section in logs:

```json
{
  "trade_id": "abc123",
  "ticker": "SPY",
  "decision": "APPROVED",
  "rag_learning": {
    "enabled": true,
    "kb_metadata": {
      "last_updated": "2026-05-23T22:00:00Z",
      "window_days": 60,
      "n_outcomes_used": 1450,
      "ticker_stats": {
        "n_trades": 67,
        "hit_rate": 0.522,
        "dollar_weighted_win_rate": 0.541,
        "blacklisted": false
      },
      "regime_stats": {
        "n_trades": 129,
        "hit_rate": 0.558
      }
    },
    "conviction_multipliers": {
      "ticker_multiplier": 0.97,
      "regime_multiplier": 1.10,
      "combined_multiplier": 1.07
    },
    "p_bull_adjustment": {
      "p_bull_raw": 0.620,
      "p_bull_adjusted": 0.663,
      "adjustment_factor": 1.07
    }
  },
  "sizing": {
    "kelly": {
      "kelly_f": 0.08,
      "dte_scalar": 1.00,
      "edge": 0.663,
      "odds": 0.667,
      "kelly_contracts": 5,
      "thompson_scaling": {
        "enabled": true,
        "reason": "Thompson posteriors loaded for regime 3",
        "regime_id": 3,
        "posteriors": {
          "directional": {
            "alpha": 58.0,
            "beta": 42.0,
            "mean": 0.5800,
            "std": 0.0497
          },
          "income": {
            "alpha": 45.0,
            "beta": 55.0,
            "mean": 0.4500,
            "std": 0.0499
          }
        },
        "aggregate": {
          "mean": 0.524,
          "std": 0.0398,
          "confidence": 0.962
        }
      }
    }
  }
}
```

### Key Fields Explained

**rag_learning.enabled:**
- `true` = KB was successfully loaded and applied
- `false` = KB missing or error (falls back to consensus p_bull)

**ticker_stats:**
- `n_trades` = how many trades on this ticker in the 60-day window
- `hit_rate` = direction accuracy (close > entry_price)
- `dollar_weighted_win_rate` = P&L-weighted win rate
- `blacklisted` = true if hit_rate < 0.43 AND dollar_win < 0.45 (trade blocked)

**regime_stats:**
- Per-regime accuracy over the window
- Used to multiply conviction: `p_bull_adj = 0.5 + (p_bull - 0.5) × regime_mult`

**conviction_multipliers:**
- `ticker_multiplier` = ranges [0.5, 1.3] based on ticker accuracy
- `regime_multiplier` = ranges [0.5, 1.3] based on regime accuracy
- `combined_multiplier` = ticker × regime (clipped to [0.4, 1.5])
- Values > 1.0 boost confidence; < 1.0 reduce it

**thompson_scaling:**
- `enabled` = true if Thompson posteriors loaded from KB
- `posteriors` = per-debater accuracy in this regime (alpha/beta from Bayesian update)
- `aggregate.confidence` = 1/(1+std), used to scale Kelly position size
  - Ranges [0.5, 1.0]
  - High confidence (many observations) → larger positions
  - Low confidence (few observations) → smaller positions

---

## Decision Log Location

Trade decisions are logged to:
```
logs/paper_trade_<YYYY-MM-DD>.jsonl
```

Each line is one trade decision. To view RAG learning for a specific date:

```bash
# Show all trades from May 23
cat logs/paper_trade_2026-05-23.jsonl | jq '.rag_learning'

# Show trades where RAG learning was enabled
cat logs/paper_trade_2026-05-23.jsonl | jq 'select(.rag_learning.enabled == true)'

# Show ticker multipliers applied
cat logs/paper_trade_2026-05-23.jsonl | jq '.rag_learning.conviction_multipliers'
```

---

## Interpreting Thompson Posteriors

Thompson posteriors show Bayesian confidence in each debater's accuracy per regime.

### Example: directional debater in regime 3 (normal_trending)

```json
"directional": {
  "alpha": 58.0,
  "beta": 42.0,
  "mean": 0.5800,
  "std": 0.0497,
  "confidence": 0.953
}
```

**What it means:**
- Observed 57 wins and 41 losses (alpha-1, beta-1) in this regime
- Expected win rate: 58%
- Uncertainty (std): 0.0497 → 95.3% confidence
- Position sizing scales by 95.3% of standard Kelly

**Interpretation:**
- High observations (57+41=98) → tight posterior → high confidence
- Win rate 58% > 50% baseline → directional is accurate in this regime
- Kelly sizing gets 95% of full Kelly, not reduced

### Example: income debater in regime 5 (normal_neutral)

```json
"income": {
  "alpha": 12.0,
  "beta": 18.0,
  "mean": 0.4000,
  "std": 0.1105,
  "confidence": 0.901
}
```

**What it means:**
- Only 11 wins and 17 losses observed (sparse data)
- Expected win rate: 40% (below 50% baseline!)
- High uncertainty (std=0.11) → 90.1% confidence
- Kelly gets scaled down

**Interpretation:**
- Income is underperforming in normal_neutral regime
- Few observations (11+17=28) but clear pattern: loss-making
- Position sizing gets reduced to 90% of Kelly
- Next step: either retrain income debater or reduce its weight in consensus

---

## RAG Learning Activation Timeline

1. **Backtest runs** (daily morning or on-demand):
   ```bash
   python scripts/backtest.py --months 12 --bandit
   ```
   → Saves Thompson posteriors to `~/.tradingbot/knowledge_base.json`

2. **Daily learning script** (after market close):
   ```bash
   python scripts/daily_learn.py
   ```
   → Updates KB with today's outcomes, recomputes stats

3. **Live trading** (9:35 AM entry scan):
   ```bash
   python scripts/paper_trade.py --full-scan
   ```
   → Loads KB, applies multipliers, logs RAG attribution

4. **Check status anytime**:
   ```bash
   python scripts/rag_status.py
   ```
   → Shows current KB state

---

## Disabling RAG Learning (Diagnostic)

To temporarily disable RAG learning for a test run:

```bash
# Set env var to disable KB loading
export TRADINGBOT_FLAG_KB_ENABLED=0
python scripts/paper_trade.py --full-scan
```

Decision logs will show `"enabled": false` in `rag_learning` section.

---

## Troubleshooting RAG Status

**Q: KB shows `last_updated` from days ago?**
- Run `python scripts/daily_learn.py` to refresh stats

**Q: Thompson posteriors section is empty?**
- Run backtest with `--bandit` flag to warm-start posteriors
- Then run `python scripts/daily_learn.py` to include outcomes

**Q: Ticker is blacklisted but I want to trade it?**
- Blacklist removes trades entirely (multiplier = 0.0)
- Edit `tradingbot/strategy/quality_gates.py` to whitelist, or
- Manually boost ticker stats in KB by trading it more (need >= 20 observations)

**Q: Posterior `confidence` is very low (e.g., 0.50)?**
- Debater has very few observations in that regime (sparse data)
- Position sizing will be conservative
- After 30+ more trades in that regime, confidence rises

---

## Next Steps

**Phase 2:** RAG context injection into consensus weighting
- Instead of fixed debater weights, use Thompson posteriors to weight debaters
- Accurate debaters get higher weight; inaccurate ones reduced

**Phase 3:** Pattern mining
- Find "XLE + normal_trending" has 62% hit rate (vs 58% baseline)
- Find "high conviction in mean-revert" has 35% hit rate (reduce by 50%)

**Phase 4:** Automated alerts
- Email/Slack when ticker is blacklisted
- Alert when debater confidence drops below 70% in a regime
- Weekly report: "Best performing: XLE (62% hit), Worst: SLV (35% hit)"
