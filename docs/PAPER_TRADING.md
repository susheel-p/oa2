# Paper Trading Validation Guide

Complete checklist and decision criteria for the 2-week validation run before going live.

---

## Why 2 Weeks?

Backtest shows strong numbers, but 4 things can only be validated on real data:

1. **Real signal quality** — Does the income debater vote on real option chains (not synthetic)?
2. **Real fills and slippage** — Is the ±2% assumption correct on moomoo?
3. **Live regime detection** — Does the classifier behave correctly on live feeds?
4. **Thompson posterior sizing** — Do Kelly sizes feel right in practice?

**Week 1 shadow** (no orders) → 25+ data points to check signal quality.  
**Week 2 paper** (real orders, fake money) → 10-15 closed trades to validate win rate.

---

## Prerequisites

```bash
# 1. .env has moomoo credentials
cat .env | grep MOOMOO_USERNAME

# 2. OpenD daemon running (download from https://www.moomoo.com/download)
# 3. Latest backtest results exist
ls ~/.tradingbot/backtest/results_*.json | tail -1

# 4. Thompson posteriors loaded
python -c "from tradingbot.learning.knowledge_base import KnowledgeBase, default_kb_path; kb = KnowledgeBase.load(default_kb_path()); print(f'Debaters loaded: {len(kb.posteriors)}')"
```

---

## Week 1: Shadow Mode (5 Days)

**Goal:** Validate signal quality on live data — accuracy must match backtest (≥50%).

### Morning (9:15 AM)

```bash
# Terminal 1: Run signals (no orders submitted)
python scripts/paper_trade.py --shadow

# Terminal 2: Watch logs
tail -f logs/paper_trade_$(date +%Y-%m-%d).jsonl | jq '.consensus_direction'

# Terminal 3: KB status
python scripts/rag_status.py
```

### Evening (after 4 PM)

```bash
python scripts/rag_status.py > ~/.tradingbot/validation/rag_day1.txt
python scripts/rag_impact.py
```

### Daily Metrics to Record

```
=== SHADOW DAY 1 (YYYY-MM-DD) ===
Signals generated: ___
  BULLISH: ___ %     BEARISH: ___ %     NEUTRAL: ___ %

Income debater — APPROVE: ___ %  REJECT: ___ %  ABSTAIN: ___ %
Flow debater   — avg conviction: ___   signal count: ___

Checks:
[ ] Zero errors in log
[ ] Regime classifier produced buckets without crash
[ ] Thompson posteriors loaded
Notes: ___
```

### Week 1 Pass Criteria

| Metric | Target | Backtest |
|--------|--------|----------|
| Consensus accuracy | ≥ 50% | 53.7% |
| Income debater non-ABSTAIN | ≥ 20% | ~30% |
| Signals per day | ≥ 5 | ~22 |
| Zero regime crashes | 100% | N/A |
| Zero log errors | 100% | N/A |

**End-of-Friday decision:**
- 5/5 → Green light for Week 2
- 4/5 → Extend shadow 1 more week
- < 4/5 → Debug debaters, do not proceed

---

## Week 2: Paper Trading (5 Days, 25% Kelly)

**Goal:** Validate fills, slippage, and P&L on real paper account.

### Setup (Before Monday)

```bash
# Verify moomoo paper account access
python -c "from moomoo import OpenSecTradeContext; print('OK')"

# Set Kelly fraction to 25% in tradingbot/sizing/kelly.py
# kelly_fraction = 0.25
```

### Morning (9:15 AM)

```bash
# Terminal 1: Entry daemon
python scripts/paper_trade.py --entry-only

# Terminal 2: Watch fills
tail -f logs/paper_trade_$(date +%Y-%m-%d).jsonl | jq 'select(.status == "sized_approved")'

# Terminal 3: Market monitor
python scripts/market_monitor.py --shadow
```

### Evening (after 4 PM)

```bash
python scripts/rag_impact.py
cp logs/paper_trade_$(date +%Y-%m-%d).jsonl ~/.tradingbot/validation/paper_day_N.jsonl
```

### Daily Metrics to Record

```
=== PAPER DAY 1 (YYYY-MM-DD) ===
Trades entered: ___
Trades closed:  ___  (profit target: ___  stop loss: ___  EOD: ___)
Daily P&L: ___ %      Unrealized: ___ %
Win rate (today): ___ %     Cumulative: ___ %
Avg slippage: ___ % (target ≤ 2.5%)

Greeks:
  Portfolio delta: _____ (limit ±0.30)
  Portfolio vega:  _____ (limit ±$50/1%)
[ ] Within risk limits

Checks:
[ ] Thompson posteriors applied
[ ] No order rejections
[ ] OpenD stable
Notes: ___
```

### Week 2 Pass Criteria

| Metric | Target | Backtest |
|--------|--------|----------|
| Win rate | ≥ 45% | 56.0% |
| Total P&L | ≥ 0% | ~+0.7% |
| Avg slippage | ≤ 2.5% | ±2.0% |
| Profit target hit rate | ≥ 50% | 54.5% |
| Trade count | ≥ 10 | N/A |

**End-of-Friday decision:**
- Win rate ≥45% AND P&L ≥0% → **Ready for live**
- Win rate 40-45%, P&L near 0% → **Extend 1 more week** (slippage is real but system works)
- Win rate < 40% OR P&L < -2% → **Do not proceed**; debug first

---

## Decision Flowchart

```
WEEK 1: SHADOW (5 days)
  Accuracy ≥50% AND income debater voting AND no crashes?
  YES → Week 2
  NO  → Extend shadow or debug

WEEK 2: PAPER (5 days, 25% Kelly)
  Win rate ≥45% AND P&L ≥0%?
  YES → Deploy live at 50% Kelly ($5K–$10K)
  ~NO → Extend 1 more week at 25%
  NO  → Return to backtest
```

---

## Hard Stops

Stop all entries immediately if ANY of these occur:

| Condition | Threshold |
|-----------|-----------|
| Daily P&L | < -2% of account |
| Weekly P&L | < -5% of account |
| Consensus accuracy | < 48% |
| Order rejection rate | > 10% |
| OpenD connection | Down |
| Regime classifier | Crash |

**When halted:**
1. Cancel all pending orders
2. Close open positions at market
3. Send Telegram alert: "Paper trading halted — [reason]"
4. Investigate before resuming

---

## Good Signs vs Red Flags

| Signal | Good | Yellow | Red |
|--------|------|--------|-----|
| Consensus accuracy | 50–55% | 48–50% | < 48% |
| Income debater vote rate | 20–35% APPROVE | 15–20% | 0% (broken) |
| Slippage | ≤ 2% | 2–3% | > 3% |
| Win rate by end of Week 2 | ≥ 50% | 40–45% | < 40% |
| Daily P&L | Positive | -1% to 0% | < -2% |

---

## Go-Live Checklist (if Week 2 passes)

- [ ] Week 2 summary saved
- [ ] All hard stops tested
- [ ] Thompson posteriors current (backtest run this week)
- [ ] Telegram alerts wired and tested
- [ ] Real account prepared ($5K–$10K)
- [ ] Kelly fraction set to 50%
- [ ] Max daily loss set to 1% (tighter for first week live)

```bash
# Day 1 live
python scripts/paper_trade.py --entry-only
# kelly_fraction = 0.50 in tradingbot/sizing/kelly.py
# daily_loss_limit = 0.01 in tradingbot/execution/exit.py
```

---

## Common Debug Commands

```bash
# Signals not generating
python scripts/smoke_test.py

# moomoo connection fails
python -c "from tradingbot.dataflows.moomoo_data import check_opend; check_opend()"

# Thompson posteriors not loading
python scripts/rag_status.py --posteriors-only

# Income debater stuck in ABSTAIN — check synthetic chain flag
grep "TRADINGBOT_FLAG_SYNTHETIC_CHAIN" tradingbot/core/feature_flags.py
```
