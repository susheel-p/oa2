# RAG Learning — Quick Start Guide

## What Is This?

The trading bot now learns from past trade outcomes and adapts its position sizing accordingly. Every trade decision is recorded, showing exactly what the Knowledge Base (KB) contributed.

## 5-Minute Setup

### 1. Seed the KB (one-time)
```bash
python scripts/backtest.py --months 12 --bandit
```
This computes how accurate each debater is in each market regime, and saves it as `~/.tradingbot/knowledge_base.json`

### 2. Update KB daily (after market close)
```bash
python scripts/daily_learn.py
```
This incorporates today's outcomes (won or lost) into the KB stats.

### 3. Trade with RAG learning (every morning)
```bash
python scripts/paper_trade.py --full-scan
```
This automatically:
- Loads the KB
- Adjusts position sizing based on learned accuracy
- Logs everything to `logs/paper_trade_2026-05-24.jsonl`

## View RAG Learning Status

**Quick status check:**
```bash
python scripts/rag_status.py
```

**Sample output:**
```
RAG Learning: ENABLED
  KB last updated: 2026-05-24
  Tickers tracked: 22
  Outcomes used: 1450
  Thompson posteriors: 40
```

**Detailed analysis:**
```bash
python scripts/rag_impact.py --days 7
```

Shows how much RAG learning affected position sizes over the last week.

## How It Works (30-second version)

1. **Backtest runs** and notices: "directional debater is 58% accurate on SPY in normal trending regimes, but only 35% on SLV"

2. **KB stores this** as conviction multipliers:
   - SPY: 0.97× (slightly worse than baseline, reduce size slightly)
   - SLV: 0.0× (terrible, block entirely)

3. **Next trade on SPY** gets sized smaller due to lower conviction

4. **Thompson posteriors add confidence scaling:**
   - If we've only seen 10 SPY trades → high uncertainty → size reduced even more
   - If we've seen 100 SPY trades → high confidence → size normal

5. **Decision logs show all of this:**
   ```json
   {
     "ticker": "SPY",
     "rag_learning": {
       "enabled": true,
       "conviction_multipliers": {
         "ticker_multiplier": 0.97,
         "combined_multiplier": 1.00
       },
       "p_bull_adjustment": {
         "p_bull_raw": 0.620,
         "p_bull_adjusted": 0.620
       }
     },
     "sizing": {
       "kelly": {
         "thompson_scaling": {
           "enabled": true,
           "aggregate": {"confidence": 0.926}
         }
       }
     }
   }
   ```

## What Gets Logged?

Every trade decision includes:

| Field | Meaning | Example |
|-------|---------|---------|
| `rag_learning.enabled` | Was KB loaded? | `true` |
| `ticker_multiplier` | Ticker accuracy → conviction | `0.97` (97% of consensus) |
| `regime_multiplier` | Regime accuracy → conviction | `1.10` (110% of consensus) |
| `p_bull_adjustment` | Before/after conviction shift | `0.620 → 0.620` |
| `thompson_scaling.confidence` | Data confidence in sizing | `0.926` (92.6% normal Kelly) |
| `blacklisted` | Is ticker blocked? | `false` (trading allowed) |

## Interpretation Guide

### Conviction Multiplier

- **> 1.0** = Ticker/regime performing well → **boost position size**
- **< 1.0** = Ticker/regime underperforming → **reduce position size**
- **0.0** = Blacklisted (hit_rate < 43% AND dollar_win < 45%) → **block trade**

### Thompson Confidence

- **0.95+** = High confidence (100+ observations) → **use full Kelly**
- **0.85–0.95** = Medium confidence (30–50 observations) → **use 85–95% of Kelly**
- **< 0.85** = Low confidence (< 20 observations) → **conservatively scale down**

## Common Questions

**Q: Why is my position size smaller than usual?**
- A: KB found that this ticker/regime is underperforming. Check:
  ```bash
  python scripts/rag_status.py --ticker SPY
  ```

**Q: Can I see what trades were affected?**
- A: Yes, filter logs:
  ```bash
  cat logs/paper_trade_2026-05-24.jsonl | jq '.rag_learning'
  ```

**Q: How do I disable RAG learning?**
- A: Set env var (for testing only):
  ```bash
  export TRADINGBOT_FLAG_KB_ENABLED=0
  python scripts/paper_trade.py --full-scan
  ```

**Q: When does KB update?**
- A: After `daily_learn.py` runs (around midnight ET)
- A: Or whenever you run backtest with `--bandit` flag

**Q: Can I edit KB manually?**
- A: No, it's auto-generated. Delete it to start fresh:
  ```bash
  rm ~/.tradingbot/knowledge_base.json
  python scripts/backtest.py --bandit
  ```

## Full Documentation

For detailed information, see:
- **[RAG_LOGGING.md](RAG_LOGGING.md)** — Decision log format and interpretation
- **[RAG_IMPLEMENTATION_SUMMARY.md](RAG_IMPLEMENTATION_SUMMARY.md)** — Architecture and internals
- **[RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md)** — Original design plan (Phases 1–6)

## Workflow

```
Daily 9:35 AM:
  python scripts/paper_trade.py --full-scan
    ↓ Prints RAG status
    ↓ Loads KB
    ↓ Scans 22 tickers with RAG multipliers
    ↓ Logs decisions to logs/paper_trade_2026-05-24.jsonl
    ↓ Saves open positions to logs/positions_2026-05-24.json

Continuously (every 5 minutes):
  python scripts/paper_trade.py --exit-only
    ↓ Checks open positions for exit signals

After 4:30 PM ET:
  python scripts/daily_learn.py
    ↓ Reads today's outcomes from logs/
    ↓ Updates KB stats
    ↓ Saves to ~/.tradingbot/knowledge_base.json

Anytime:
  python scripts/rag_status.py
  python scripts/rag_impact.py --days 7
    ↓ View RAG learning status and impact
```

## Next Steps

1. **Run backtest to seed KB:**
   ```bash
   python scripts/backtest.py --months 12 --bandit
   ```

2. **Check KB status:**
   ```bash
   python scripts/rag_status.py
   ```

3. **Run paper trading:**
   ```bash
   python scripts/paper_trade.py --full-scan
   ```

4. **View decision logs:**
   ```bash
   tail -f logs/paper_trade_$(date +%Y-%m-%d).jsonl | jq '.rag_learning'
   ```

---

**RAG Learning is now live. All trades are logged with KB context. Use `rag_status.py` to audit, `rag_impact.py` to measure, and decision logs to understand what KB is doing.**
