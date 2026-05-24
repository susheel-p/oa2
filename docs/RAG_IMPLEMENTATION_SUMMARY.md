# RAG Learning Implementation Summary

## What Was Implemented

Complete end-to-end RAG (Retrieval-Augmented Generation) learning system with comprehensive recording and diagnostics:

1. ✅ **Thompson Bandit Integration** — Posteriors saved to KnowledgeBase
2. ✅ **Adaptive Position Sizing** — Kelly scaled by Thompson posterior confidence
3. ✅ **Decision Attribution Logging** — Every trade decision records RAG context
4. ✅ **Diagnostic Tools** — Scripts to view and analyze RAG learning status
5. ✅ **Console Reporting** — Automatic RAG status printed at startup
6. ✅ **Impact Analysis** — Measure how much RAG learning affects trades

---

## Architecture Overview

```
Backtest (scripts/backtest.py)
    ↓ (with --bandit flag)
    Computes Thompson Beta posteriors per (debater, regime)
    ↓
Knowledge Base (~/.tradingbot/knowledge_base.json)
    ├─ Ticker stats (hit_rate, dollar_win_rate)
    ├─ Regime stats (hit_rate, multipliers)
    ├─ Debater calibration (conviction bucket accuracy)
    └─ Thompson posteriors (alpha, beta per debater × regime)
    ↓
Pipeline (tradingbot/graph/pipeline.py)
    ├─ Load KB
    ├─ Apply ticker × regime multipliers to p_bull
    ├─ Extract Thompson posteriors for current regime
    ├─ Scale Kelly position size by posterior confidence
    └─ Log all RAG decisions
    ↓
Decision Log (logs/paper_trade_YYYY-MM-DD.jsonl)
    Each line has:
    ├─ rag_learning.enabled (true/false)
    ├─ rag_learning.kb_metadata (last_updated, window_days, outcomes_used)
    ├─ rag_learning.conviction_multipliers (ticker, regime, combined)
    ├─ rag_learning.p_bull_adjustment (raw vs adjusted)
    └─ sizing.kelly.thompson_scaling (posterior stats, confidence, impact)
```

---

## Key Components

### 1. Knowledge Base (`tradingbot/learning/knowledge_base.py`)

**New additions:**
- `BetaPosteriorData` — serializable Beta(α, β) distribution
- `posteriors` field — {debater_name: {regime_id: BetaPosteriorData}}
- `get_posterior()` — retrieve posterior with defaults
- Persistence — saves/loads posteriors in KB JSON

**Example KB structure:**
```json
{
  "schema_version": 1,
  "last_updated": "2026-05-24T22:00:00Z",
  "window_days": 60,
  "n_outcomes_used": 1450,
  "tickers": {
    "SPY": {"n_trades": 67, "hit_rate": 0.522, "blacklisted": false}
  },
  "regimes": {
    "normal_trending": {"n_trades": 129, "hit_rate": 0.558}
  },
  "posteriors": {
    "directional": {
      "3": {"alpha": 58.0, "beta": 42.0, "mean": 0.58, "std": 0.0497}
    },
    "income": {
      "3": {"alpha": 12.0, "beta": 18.0, "mean": 0.4, "std": 0.1105}
    }
  }
}
```

### 2. Kelly Sizing (`tradingbot/sizing/kelly.py`)

**New functions:**
- `apply_thompson_scaling()` — scale Kelly by posterior confidence
- Updated `size_from_consensus()` — accepts `posterior_mean` and `posterior_std`

**How it works:**
```python
# Confidence = 1.0 / (1.0 + posterior_std)
# High posterior uncertainty → scale down (fewer observations)
# Low posterior uncertainty → scale up (many observations)

confidence = 1.0 / (1.0 + posterior_std)  # ranges [0.5, 1.0]
scaled_kelly_f = kelly_f * confidence
contracts_scaled = compute_contracts(scaled_kelly_f)
```

**Example:**
- Beta(1,1) (no data yet): std=0.235 → confidence=0.81 → size reduced 19%
- Beta(110,90) (100+ observations): std=0.035 → confidence=0.97 → size normal

### 3. Pipeline Integration (`tradingbot/graph/pipeline.py`)

**In `_run_sizing()`:**
1. Load KB from disk
2. Extract ticker_stats and regime_stats
3. Compute combined multiplier (ticker × regime)
4. Adjust p_bull: `p_bull_adj = 0.5 + (p_bull - 0.5) × multiplier`
5. Extract Thompson posteriors for current regime
6. Pass to `size_from_consensus()` with posteriors
7. Log everything under `ctx.attribution["rag_learning"]`

**Example attribution in decision log:**
```json
{
  "rag_learning": {
    "enabled": true,
    "kb_metadata": {
      "last_updated": "2026-05-24T22:00:00Z",
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
      "thompson_scaling": {
        "enabled": true,
        "regime_id": 3,
        "posteriors": {
          "directional": {
            "alpha": 58.0,
            "beta": 42.0,
            "mean": 0.5800,
            "std": 0.0497
          },
          "income": {
            "alpha": 12.0,
            "beta": 18.0,
            "mean": 0.4000,
            "std": 0.1105
          }
        },
        "aggregate": {
          "mean": 0.4900,
          "std": 0.0801,
          "confidence": 0.926
        }
      }
    }
  }
}
```

### 4. Backtest Posteriors Export (`scripts/backtest.py`)

**After backtest completes:**
- Convert bandit engine posteriors to BetaPosteriorData objects
- Create KB with posteriors and stats
- Save to `~/.tradingbot/knowledge_base.json`
- Print summary of saved posteriors

**Usage:**
```bash
python scripts/backtest.py --months 12 --bandit
# [backtest] Thompson posteriors saved to ~/.tradingbot/knowledge_base.json
#   Debaters: directional, income, sentiment, volatility, options_flow
#     directional: 8 regime posteriors
#     income: 8 regime posteriors
#     ...
```

---

## Diagnostic Tools

### `scripts/rag_status.py` — View KB Status

**Show everything:**
```bash
python scripts/rag_status.py
```

**Show single ticker:**
```bash
python scripts/rag_status.py --ticker SPY
```

**Show Thompson posteriors only:**
```bash
python scripts/rag_status.py --posteriors-only
```

**Output includes:**
- KB metadata (last updated, window, outcomes used)
- Ticker performance table (hit rate, dollar win %, blacklist status)
- Regime performance table (hit rate, multiplier)
- Debater calibration (conviction bucket accuracy)
- Thompson posteriors per debater per regime

### `scripts/rag_impact.py` — Measure RAG Effect

**Analyze recent trades:**
```bash
python scripts/rag_impact.py --days 7         # Last 7 days
python scripts/rag_impact.py --date 2026-05-24  # Single day
```

**Output includes:**
- How many trades had RAG enabled?
- Average multiplier applied
- How many trades blocked by blacklist?
- How much did Thompson posterior scaling affect position size?
- Per-ticker summary

### `scripts/paper_trade.py` — Automatic RAG Status

**Every run prints:**
```
[2026-05-24T09:35:00-0400] RAG Learning: ENABLED
[2026-05-24T09:35:00-0400]   KB last updated: 2026-05-24
[2026-05-24T09:35:00-0400]   Tickers tracked: 22
[2026-05-24T09:35:00-0400]   Outcomes used: 1450
[2026-05-24T09:35:00-0400]   Thompson posteriors: 40
```

---

## How to Use

### Starting Fresh

1. **Run backtest with bandit warmstart:**
   ```bash
   python scripts/backtest.py --months 12 --bandit
   ```
   → Generates KB with Thompson posteriors

2. **Run daily learning (optional, for enhanced KB):**
   ```bash
   python scripts/daily_learn.py
   ```
   → Updates KB with today's outcomes

3. **Run paper trading:**
   ```bash
   python scripts/paper_trade.py --full-scan
   ```
   → Automatically loads KB and applies RAG learning
   → Logs all RAG decisions to `logs/paper_trade_YYYY-MM-DD.jsonl`

### Monitoring RAG Learning

**Check current KB state:**
```bash
python scripts/rag_status.py
```

**Analyze last week's trades:**
```bash
python scripts/rag_impact.py --days 7
```

**View specific trade decision:**
```bash
# Check decision logs for specific ticker
cat logs/paper_trade_2026-05-24.jsonl | jq '.[] | select(.ticker == "SPY") | .rag_learning'
```

### Understanding Results

**Multiplier > 1.0 = Conviction boosted**
- Ticker/regime performing well
- Position size increases

**Multiplier < 1.0 = Conviction reduced**
- Ticker/regime underperforming
- Position size decreases

**Thompson confidence < 0.95 = High uncertainty**
- Few observations in this regime
- Position size conservatively scaled
- Will adapt as more data arrives

**Blacklist blocks = Immediate rejection**
- Ticker hit_rate < 43% AND dollar_win < 45%
- Trade blocked entirely (multiplier = 0.0)

---

## Data Flow Example

**Trade Decision: SPY BULLISH at 10:35 AM on 2026-05-24**

```
1. Pipeline loads KB
   ├─ SPY ticker stats: 67 trades, 52.2% hit, $0.541 dollar_win
   └─ normal_trending regime: 129 trades, 55.8% hit

2. Compute multipliers
   ├─ ticker_mult = 0.6 + (0.522 - 0.43) × 4.0 = 0.97
   ├─ regime_mult = 0.6 + (0.558 - 0.45) × 4.0 = 1.03
   └─ combined = 0.97 × 1.03 = 1.00

3. Adjust conviction
   ├─ p_bull_raw = 0.620 (from consensus)
   └─ p_bull_adj = 0.5 + (0.620 - 0.5) × 1.00 = 0.620 (no change)

4. Load Thompson posteriors
   ├─ directional regime 3: Beta(58, 42) → mean=0.58, std=0.0497
   ├─ income regime 3: Beta(12, 18) → mean=0.40, std=0.1105
   ├─ avg posterior: mean=0.49, std=0.0801
   └─ confidence = 1/(1+0.0801) = 0.926 (92.6%)

5. Size with Kelly
   ├─ Kelly (25% fractional): 0.08
   ├─ DTE scalar: 1.00
   ├─ Thompson confidence: 0.926
   └─ Scaled Kelly: 0.08 × 0.926 = 0.074 → 5 contracts (vs 6 without scaling)

6. Log decision
   ├─ rag_learning.enabled = true
   ├─ rag_learning.kb_metadata = {...ticker_stats, regime_stats...}
   ├─ rag_learning.conviction_multipliers = {...}
   ├─ sizing.kelly.thompson_scaling = {...posteriors...}
   └─ decision.approved = true
```

---

## What Gets Recorded

### In Decision Logs (`logs/paper_trade_YYYY-MM-DD.jsonl`)

Every approved/rejected trade records:
- `rag_learning.enabled` — was KB available?
- `rag_learning.kb_metadata` — KB age, outcomes used, ticker/regime stats
- `rag_learning.conviction_multipliers` — how much KB changed confidence
- `rag_learning.p_bull_adjustment` — before/after adjustment
- `sizing.kelly.thompson_scaling` — which posteriors were used, confidence scaling applied

### In Position Logs (`logs/positions_YYYY-MM-DD.json`)

Open positions track:
- `rag_context` — RAG multipliers applied at entry
- `entry_price` — actual entry
- `max_loss` — risk from options chain

### In Report Summaries (optional)

**Daily report** can include:
- Number of trades affected by RAG
- Average KB multiplier applied
- Thompson confidence by regime
- Top/bottom performing tickers (per KB)

---

## Disabling RAG Learning (Diagnostic)

To test trading without RAG:
```bash
export TRADINGBOT_FLAG_KB_ENABLED=0
python scripts/paper_trade.py --full-scan
```

Decision logs will show `"enabled": false` in `rag_learning` section.

---

## Next Steps (Future Phases)

**Phase 1:** ✅ Complete (Thompson posteriors → position sizing)

**Phase 2:** Dynamic consensus weighting
- Instead of fixed debater weights
- Use Thompson posteriors to weight debaters
- Example: income = 40% accurate → reduce weight from 0.20 to 0.10

**Phase 3:** Pattern mining
- Find "SPY + normal_trending + morning" = 62% hit
- Find "high conviction + mean_revert" = 35% hit (reduce by 50%)

**Phase 4:** Automated alerts
- Email when ticker blacklisted
- Alert when debater confidence drops
- Weekly: "Best: XLE (62%), Worst: SLV (35%)"

**Phase 5:** Multi-day patterns
- "3-day momentum + sentiment flip" = 71% hit
- "Pre-earnings vol crush" = 58% hit

---

## Troubleshooting

**KB shows empty but should have data?**
- Run: `python scripts/backtest.py --months 12 --bandit`
- Then: `python scripts/daily_learn.py`

**Thompson posteriors all say "no observations"?**
- Backtest hasn't been run with `--bandit` flag yet
- Run: `python scripts/backtest.py --bandit`

**RAG multiplier stuck at 1.0?**
- Less than 20 trades on that ticker (needs data)
- Or KB hasn't been updated today
- Run: `python scripts/daily_learn.py`

**Paper trading shows "RAG: DISABLED"?**
- KB file missing or corrupted
- Check: `ls -la ~/.tradingbot/knowledge_base.json`
- Regenerate: `python scripts/backtest.py --bandit`

---

## Summary

The RAG learning system now provides:
1. **Automatic KB updates** via backtest posteriors
2. **Transparent attribution** in every trade decision
3. **Adaptive position sizing** via Thompson confidence scaling
4. **Easy diagnostics** to inspect and understand RAG decisions
5. **Actionable insights** via impact analysis scripts

All decisions are logged, making it easy to audit, debug, and improve the system over time.
