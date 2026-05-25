# Paper Trading Validation: 2-Week Test Plan

**Status:** ✅ Backtest complete, Monte Carlo validation done, ready to test on live data

**Timeline:** Monday (Week 1) → Friday (Week 2) → Decision for live trading

---

## Summary: Why 2 Weeks?

The backtest shows a **24× edge over baseline** with Monte Carlo realism. But **4 things can only be validated on live data:**

1. **Real signal quality** — Does income debater still vote when synthetic chain data is replaced with real tape?
2. **Real fills & slippage** — ±2% assumption vs moomoo actual spreads
3. **Live regime detection** — Does classifier crash or behave oddly on real-time market data?
4. **Thompson posteriors impact** — Do Kelly sizes feel right, or are they too aggressive/conservative?

**2 weeks is minimum** because:
- Week 1 shadow: 5 days of signals ≥ 25 data points (enough to see pattern)
- Week 2 paper: 5 days of trades ≥ 10–15 closed positions (enough to validate win rate)
- Both needed to make confident go/no-go decision

---

## Quick Start: Begin Week 1 Shadow Mode

### Prerequisites (verify now)

```bash
# 1. .env has moomoo credentials
cat .env | grep MOOMOO_USERNAME

# 2. OpenD daemon ready (or will start it Monday morning)
# Download from: https://www.moomoo.com/download
# Run: ~/Desktop/OpenD/OpenD

# 3. Latest backtest results exist
ls -lh ~/.tradingbot/backtest/results_*.json | tail -1

# 4. Thompson posteriors loaded
python -c "from tradingbot.learning.knowledge_base import KnowledgeBase, default_kb_path; kb = KnowledgeBase.load(default_kb_path()); print(f'Posteriors loaded: {len(kb.posteriors)} debaters')"
```

### Monday Morning: Launch Shadow Mode

```bash
# Print checklists and instructions
python scripts/validate_paper_trading.py --mode shadow --days 5

# Open 3 terminals:

# Terminal 1: Run signals (live market data, no trades)
python scripts/paper_trade.py --shadow

# Terminal 2: Watch for signals in logs
tail -f ~/.tradingbot/logs/paper_*.log | grep -i "consensus\|entry\|income"

# Terminal 3: Check status (optional, for debugging)
python scripts/rag_status.py
```

---

## Week 1 Expectations (Shadow Mode)

### What You'll See

- **5 signals per day** (across 4 tickers × all regimes)
- **~50% BULLISH, 40% BEARISH, 10% NEUTRAL** consensus
- **Income debater:** ~20–30% APPROVE votes (vs 0% on current backtest due to synthetic chain)
- **Flow debater:** conviction in range [0.3, 0.85]
- **Logs:** Should be clean, no exceptions

### What To Record (Daily)

Copy this template to `~/.tradingbot/validation/shadow_day{N}.json`:

```json
{
  "date": "2026-05-27",
  "day": 1,
  "signals_generated": 22,
  "consensus_breakdown": {
    "bullish_pct": 52,
    "bearish_pct": 41,
    "neutral_pct": 7
  },
  "debaters": {
    "income": {
      "approve_pct": 25,
      "reject_pct": 5,
      "abstain_pct": 70
    },
    "flow": {
      "avg_conviction": 0.65,
      "signal_count": 22
    }
  },
  "validation": {
    "no_errors": true,
    "regime_buckets_seen": 6,
    "notes": "Normal operation, flow conviction reasonable"
  }
}
```

### Week 1 Pass Criteria

| Metric | Target | Backtest | Action |
|--------|--------|----------|--------|
| Consensus accuracy | ≥ 50% | 53.7% | Daily check in logs |
| Income debater: non-ABSTAIN | ≥ 20% | ~30% | Should see 15–30% |
| Signals per day | ≥ 5 | ~100 | Should see 20–30 |
| Zero errors/crashes | 100% | N/A | Check logs |

**End of Friday (Week 1):**
- If all 4 pass → **GREEN LIGHT: Proceed to Week 2 paper trading**
- If 3/4 pass → **YELLOW: Extend shadow 1 more week**
- If <3/4 pass → **RED: Debug debaters, don't proceed**

---

## Week 2: Paper Trading at 25% Kelly

### Monday Morning: Switch to Paper Trading

```bash
# Print Week 2 checklists
python scripts/validate_paper_trading.py --mode paper --week 2 --days 5

# Terminal 1: Entry daemon (paper account)
python scripts/paper_trade.py --entry-only

# Terminal 2: Log monitor (watch for ENTRY, fills, exits)
tail -f ~/.tradingbot/logs/paper_*.log | grep -E "ENTRY|filled|EXIT|profit_target"

# Terminal 3: Market monitor (check P&L every minute)
python scripts/market_monitor.py --shadow

# Terminal 4: Moomoo position monitor
watch -n 60 'python -c "from moomoo.client import Client; pos = Client().positions(); print(f\"Open: {len(pos)}, Delta: {sum(p.delta for p in pos):.2f}\")"'
```

### What You'll See

- **2–5 positions entered per day** (vs 20+ signals, because Kelly sizing filters)
- **Moomoo paper fills** (compare to backtest entry prices)
- **Exit triggers:** Some close at 50% profit, some at stop loss, some held to expiry
- **P&L:** Should be breakeven to +1% (slippage cost vs backtest)

### Daily Record (Week 2)

Copy to `~/.tradingbot/validation/paper_day{N}.json`:

```json
{
  "date": "2026-06-03",
  "week": 2,
  "day": 1,
  "entries": [
    {
      "ticker": "SPY",
      "signal_price": 425.00,
      "filled_price": 425.12,
      "slippage_pct": 0.028,
      "kelly_fraction": 0.25,
      "position_size_notional": 10625
    }
  ],
  "exits": [
    {
      "ticker": "SPY",
      "entry_price": 425.12,
      "exit_price": 431.87,
      "exit_reason": "profit_target",
      "hold_minutes": 127,
      "pnl_pct": 1.588
    }
  ],
  "daily_summary": {
    "trades_entered": 3,
    "trades_closed": 2,
    "daily_pnl_pct": 0.42,
    "cumulative_win_rate": 0.667,
    "avg_slippage_pct": 0.018
  }
}
```

### Week 2 Pass Criteria

| Metric | Target | Backtest | Status |
|--------|--------|----------|--------|
| Win rate | ≥ 45% | 56.0% | Track daily |
| Total P&L | ≥ 0% | (varies) | Cumulative by Friday |
| Avg slippage | ≤ 2.5% | ±2.0% | Compare fills |
| Profit target hit | ≥ 50% | 54.5% | Count exits |
| Trade count | ≥ 10 | N/A | Should have 10–15 by Friday |

**End of Friday (Week 2):**
- If win rate ≥ 45% AND P&L ≥ 0% → **READY FOR LIVE**
- If win rate ≥ 40% AND P&L near 0% → **CONTINUE 1 MORE WEEK** (slippage impact confirmed, system works)
- If win rate < 40% OR P&L < -2% → **DO NOT PROCEED** (debug required)

---

## Decision Flowchart

```
WEEK 1: SHADOW MODE (5 days)
  ↓
  Consensus accuracy ≥ 50%?
  ├─ NO  → Extend shadow 1 more week
  └─ YES ↓
  Income debater voting (≥20% non-ABSTAIN)?
  ├─ NO  → Debug income debater logic
  └─ YES ↓
  Zero regime classifier crashes?
  ├─ NO  → Fix classifier, don't proceed
  └─ YES ↓
  
  ✅ READY FOR WEEK 2

WEEK 2: PAPER TRADING (5 days, 25% Kelly)
  ↓
  Win rate ≥ 45% AND P&L ≥ 0%?
  ├─ YES ↓
      ✅ READY FOR LIVE
      → Deploy at 50% Kelly ($5K–$10K)
  ├─ ~NO (40% < WR < 45%, P&L near 0%) ↓
      ⚠️  EXTEND 1 MORE WEEK
      → Slippage impact confirmed, system works
      → Stay at 25%, tighten profit target to 40%
  └─ NO (WR < 40% OR P&L < -2%) ↓
      ❌ DO NOT PROCEED
      → Audit debater logic
      → Return to backtest tuning
```

---

## Hard Stops (Automatic Halt)

**If ANY of these occur, STOP ALL ENTRIES immediately:**

1. **Daily P&L < -2%** — Account loss exceeds limit
2. **OpenD connection fails** — No market access
3. **Regime classifier crashes** — Regime detection broken
4. **Order rejection rate > 10%** — Liquidity problem
5. **Consensus accuracy drops to < 48%** — Signal quality degraded

**When halted:**
- Cancel pending orders
- Close open positions (market order)
- Investigate root cause
- Send Telegram alert
- Don't resume without fixing issue

---

## Daily Monitoring Routine

### Every Morning (9:15 AM)
```bash
# Start shadow or paper daemon (see launch commands above)
python scripts/paper_trade.py --shadow  # Week 1
# OR
python scripts/paper_trade.py --entry-only  # Week 2
```

### Every Hour (During Market 9:30 AM – 4:00 PM)
- Watch logs for signals/entries
- Check moomoo for unexpected P&L moves
- Note any unusual fills or slippage

### Every Evening (4:00 PM – After Close)
```bash
# Save daily metrics
python scripts/rag_status.py > ~/.tradingbot/validation/rag_day{N}.txt
python scripts/rag_impact.py > ~/.tradingbot/validation/impact_day{N}.txt

# Copy log for archive
cp ~/.tradingbot/logs/paper_*.log ~/.tradingbot/validation/paper_day{N}.log

# Record metrics in daily JSON (from template above)
# Edit ~/.tradingbot/validation/shadow_day{N}.json or paper_day{N}.json
```

---

## Success Indicators

✅ **Good signs (you're on track):**
- Consensus accuracy 50–55% (matches backtest ±2pp)
- Income debater: 20–35% APPROVE rate (not stuck in ABSTAIN)
- Slippage ±2% or better (assumption holds)
- Win rate trending toward 50%+ by mid-Week 2
- No crashes or errors in logs

⚠️ **Yellow flags (investigate but continue):**
- Consensus accuracy 48–50% (slightly below backtest)
- Slippage 2–3% (worse than expected, but manageable)
- Win rate 40–45% by end of Week 2 (below 45% gate, but not failing)
- Income debater abstaining >70% (signal quality lower than backtest)

❌ **Red flags (STOP and don't proceed):**
- Win rate < 40% by end of Week 2
- P&L < -2% on any day
- Consensus accuracy < 48%
- Income/volatility debaters 0% voting (broken signal)
- More than 1 regime classifier crash

---

## Files Reference

| File | Purpose |
|------|---------|
| `TESTING_CHECKLIST.md` | Daily templates & decision criteria |
| `scripts/validate_paper_trading.py` | Checklists & validation logic |
| `scripts/paper_trade.py --shadow` | Week 1: Log signals only |
| `scripts/paper_trade.py --entry-only` | Week 2: Real paper trades |
| `~/.tradingbot/validation/` | Store daily metrics (create yourself) |
| `~/.tradingbot/logs/paper_*.log` | Raw signal & trade logs |

---

## After Week 2: Go / No-Go Decision

### If READY FOR LIVE (Win rate ≥ 45%, P&L ≥ 0%)

```bash
# 1. Update Kelly sizing to 50% (was 25% for paper)
# Edit tradingbot/sizing/kelly.py:
kelly_fraction = 0.50  # up from 0.25

# 2. Reduce daily loss limit to 1% (extra safety)
# Edit tradingbot/execution/exit.py:
daily_loss_limit = 0.01  # was 0.02

# 3. Deploy to real account
python scripts/paper_trade.py --entry-only

# 4. Monitor closely first week (1% daily loss = STOP)
```

### If NOT READY (Win rate < 40% or P&L < -2%)

```bash
# 1. Don't proceed to live
# 2. Investigate:
#    - Is income debater voting? (abstaining too much?)
#    - Is slippage worse than ±2%? (moomoo liquidity issue?)
#    - Did regime classifier have issues? (live data anomaly?)
# 3. Return to backtest tuning
# 4. Try again in 2 weeks
```

---

## Questions? Debugging?

**If signals not generating:**
```bash
python -c "from scripts.paper_trade import run_entry_only; run_entry_only(shadow=True, verbose=True)"
```

**If moomoo connection fails:**
```bash
# Restart OpenD
pkill -f OpenD
~/Desktop/OpenD/OpenD

# Test connection
python -c "from moomoo.client import Client; print(Client().ping())"
```

**If Thompson posteriors not loading:**
```bash
python -c "from tradingbot.learning.knowledge_base import KnowledgeBase, default_kb_path; kb = KnowledgeBase.load(default_kb_path()); print(kb.posteriors.keys())"
```

**If income debater stuck in ABSTAIN:**
```bash
# Check if synthetic chain is enabled in live pipeline
grep "synthetic_chain" tradingbot/graph/pipeline.py
# Should see: if config.get("TRADINGBOT_FLAG_SYNTHETIC_CHAIN")
```

---

## Timeline Checklist

- [ ] **Monday (Week 1):** Launch shadow mode
- [ ] **Tue–Fri (Week 1):** Record daily metrics, verify accuracy ≥ 50%
- [ ] **Friday EOD (Week 1):** Decide: green light for Week 2?
- [ ] **Monday (Week 2):** Switch to paper trading (if Week 1 pass)
- [ ] **Tue–Fri (Week 2):** Record daily P&L, track win rate
- [ ] **Friday EOD (Week 2):** Decide: ready for live?
- [ ] **Monday (Week 3, if pass):** Deploy to real account at 50% Kelly

---

**You're ready. 2 weeks from now, you'll know if this works. Let's go.** 🚀
