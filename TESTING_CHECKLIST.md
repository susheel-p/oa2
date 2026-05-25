# 2-Week Paper Trading Validation Plan

## Week 1: Shadow Mode (No Real Money)
**Goal:** Validate signal quality on live data — should match backtest accuracy ≥ 50%

### Daily Tasks

**Every Morning (before market open 9:30 AM ET):**
```bash
# Start live signal monitoring
python scripts/paper_trade.py --shadow

# In separate terminal, watch logs
tail -f ~/.tradingbot/logs/paper_*.log | grep -E "consensus_direction|debater_opinion"
```

**Every Afternoon (after market close 4:00 PM ET):**
- Record metrics in daily log (see template below)
- Check for errors in logs (regime classifier crash? debater exception?)
- Verify income debater is generating votes (not stuck in ABSTAIN)

### Daily Metrics Template (copy for each day)

```
=== SHADOW MODE - DAY 1 (2026-05-27) ===
SIGNALS:
  Total signals generated: ___
  BULLISH: ___ (  _% of signals)
  BEARISH: ___ (  _% of signals)
  NEUTRAL: ___ (  _% of signals)

DEBATERS:
  Income debater:
    % APPROVE: ___
    % REJECT: ___
    % ABSTAIN: ___
  
  Flow debater:
    Avg conviction: ___
    # signals: ___
  
  Directional debater:
    Regime hits: ___ / ___ (trailing 5 days)
    Accuracy: ___% 

VALIDATION:
  [ ] No errors in log
  [ ] Regime classifier produced 8 different labels? (yes/no)
  [ ] Thompson posteriors loaded from KB? (yes/no)
  
NOTES:
  (Any anomalies, unusual regimes, missing data?)
```

### Week 1 Decision Criteria

| Metric | Target | Backtest | Status |
|--------|--------|----------|--------|
| Consensus accuracy | ≥ 50% | 53.7% | [ ] |
| Income debater: % non-ABSTAIN | ≥ 20% | ~30% | [ ] |
| Signal count | ≥ 5/day | ~100/day | [ ] |
| No regime classifier crashes | 100% uptime | N/A | [ ] |
| Log errors | 0 | N/A | [ ] |

**Decision Point (End of Friday):**
- [ ] **PASS** → All 5 criteria met → Proceed to Week 2 paper trading
- [ ] **MARGINAL** → 4/5 criteria met → Extend shadow mode 1 more week
- [ ] **FAIL** → <4/5 criteria → Debug debater logic, don't proceed

---

## Week 2: Paper Trading at 25% Kelly Size
**Goal:** Validate fills, slippage, and P&L on real (paper) moomoo account

### Pre-Week Setup

```bash
# Verify moomoo connection
cd ~/Desktop/OpenD
./OpenD  # Should show "moomoo OpenD ready"

# Verify .env has credentials
cat ~/.env | grep MOOMOO

# Test paper account access
python -c "from moomoo.client import Client; c = Client(); print(c.account())"

# Set position size limit (25% Kelly)
# Edit tradingbot/sizing/kelly.py line ~150:
kelly_fraction = 0.25  # 25% of computed Kelly
max_position_size = account_balance * 0.01  # max 1% per trade
```

### Daily Tasks

**Every Morning (9:15 AM — 15 min before market):**
```bash
# Start paper trading daemon
python scripts/paper_trade.py --entry-only

# In another terminal, watch for entry signals
tail -f ~/.tradingbot/logs/paper_*.log | grep "ENTRY\|order_submitted\|order_filled"
```

**Every Hour (during market 9:30 AM – 4:00 PM):**
- Monitor P&L on moomoo: Check positions, mark-to-market
- Watch for exits: 50% profit closes, stop losses
- Record any unusual fills or slippage

**Every Evening (4:00 PM – after market close):**
```bash
# Run daily reconciliation
python scripts/rag_impact.py  # Thompson posteriors impact
python scripts/rag_status.py  # KB learning status

# Save day's trade log
cp ~/.tradingbot/logs/paper_*.log ~/.tradingbot/validation/paper_day{N}.log
```

### Daily Metrics Template (copy for each day)

```
=== PAPER TRADING - WEEK 2, DAY 1 (2026-06-03) ===

ENTRY SUMMARY:
  Trades entered today: ___
  Total position size: ___% of account
  Avg Kelly fraction: ___

TRADES DETAIL:
  Trade 1: SPY call entry $425.50 vs signal $425.00 → slippage: +0.12%
  Trade 2: QQQ call entry $510.25 vs signal $510.00 → slippage: +0.05%
  ...

EXIT SUMMARY:
  Closed today: ___
  Via 50% profit target: ___
  Via stop loss: ___
  Via EOD cutoff: ___
  Avg hold duration: ___ hours
  
DAILY P&L:
  Realized P&L: ___% of account
  Unrealized P&L: ___% of account
  Win rate (today): ___% (__/__trades)

CUMULATIVE (Week 2 to date):
  Total trades: ___
  Win rate: ___% (__/__closed trades)
  Total realized P&L: ___% of account
  Avg slippage: ___% (vs ±2.0% backtest)
  Avg profit target hit rate: ___%

GREEKS CHECK:
  Portfolio delta: _____ (cap ±0.30)
  Portfolio vega: _____ (cap ±0.25)
  [ ] Within risk limits

VALIDATION:
  [ ] Thompson posteriors applied to Kelly? (yes/no)
  [ ] Regime detection working on live data? (yes/no)
  [ ] OpenD connection stable? (yes/no)
  [ ] No order rejections? (yes/no)

NOTES:
  (Any concerning fills? Wider spreads? Regime switches?)
```

### Week 2 Decision Criteria

| Metric | Target | Backtest | Status |
|--------|--------|----------|--------|
| Win rate | ≥ 45% | 56.0% | [ ] |
| Total P&L | ≥ 0% | ~+0.7% | [ ] |
| Avg slippage | ≤ 2.5% | ±2.0% | [ ] |
| Profit target hit rate | ≥ 50% | 54.5% | [ ] |
| Trade count | ≥ 10 | N/A | [ ] |
| No order rejects | 100% | N/A | [ ] |

**Decision Point (End of Friday):**
- [ ] **READY FOR LIVE** → Win rate ≥45% AND P&L ≥0% AND slippage ≤2.5%
  - → Deploy to real account at 50% Kelly size ($5K–$10K)
  
- [ ] **CONTINUE 1 MORE WEEK** → P&L near breakeven (0% ± 1%) but win rate ≥40%
  - → Indicates slippage impact is real, but system still works
  - → Stay at 25% paper size, tighten profit target to 40%
  - → Re-evaluate after Week 3
  
- [ ] **DO NOT PROCEED** → Win rate < 40% OR P&L < -2%
  - → Return to shadow mode
  - → Audit debater logic (income still at 45% in backtest)
  - → Consider regime classifier sensitivity on live data

---

## Daily Command Reference

**Shadow Mode (Week 1):**
```bash
# Terminal 1: Run signals
python scripts/paper_trade.py --shadow

# Terminal 2: Watch logs
tail -f ~/.tradingbot/logs/paper_trade_*.log

# Terminal 3: Check status
python scripts/rag_status.py
```

**Paper Trading (Week 2):**
```bash
# Terminal 1: Entry daemon
python scripts/paper_trade.py --entry-only

# Terminal 2: Monitor logs
tail -f ~/.tradingbot/logs/paper_trade_*.log

# Terminal 3: Market monitor (every minute)
python scripts/market_monitor.py --shadow

# Terminal 4: Watch positions
watch -n 60 'python -c "from moomoo.client import Client; print(Client().positions())"'
```

**Daily Summary:**
```bash
# Run after market close
python scripts/rag_impact.py
python scripts/rag_status.py
python scripts/backtest_tracker.py  # Compare live vs backtest
```

---

## Risk Stops (Hard Limits)

**STOP ALL ENTRIES IF ANY OF THESE OCCUR:**

- [ ] Daily P&L < **-2%** of account (hard loss limit)
- [ ] Weekly P&L < **-5%** of account (rebalance signal)
- [ ] Directional accuracy < **48%** on any day (below backtest gate)
- [ ] OpenD connection down (no access to moomoo)
- [ ] Regime classifier produces NaN or crash (regime detection broken)
- [ ] Order rejection rate > 10% (liquidity or connectivity issue)

**If ANY hard limit hit:**
1. Cancel all pending orders
2. Do NOT enter new positions
3. Close existing positions at market
4. Log incident with time, reason, action taken
5. Send Telegram alert: "⚠️ Paper trading halted — [reason]"
6. Investigate root cause before resuming

---

## Success Markers to Watch For

✅ **Good signs (proceed confidently):**
- Consensus accuracy ≥ 50% in Week 1
- Income debater voting (15%+ APPROVE rate)
- Paper trades hitting 50% profit targets (>50% of closed trades)
- Slippage within ±2.5% (close to backtest assumption)
- Win rate stabilizing around 50%+ by end of Week 2

⚠️ **Warning signs (investigate):**
- Income debater stuck in ABSTAIN (>80% abstain rate)
- Paper P&L -1% to 0% (slippage worse than expected)
- Win rate dropping below 40% mid-week
- Unusual spreads on options (liquidity issue?)
- Regime classifier flipping between buckets (instability?)

❌ **Failure signals (DO NOT PROCEED TO LIVE):**
- Win rate < 40% by end of Week 2
- Cumulative P&L < -2%
- > 20% order rejections
- Income/volatility debaters showing 0% votes (signal degradation)
- Directional accuracy < 48% (below gate threshold)

---

## Transition to Live (if criteria met)

**Go-Live Checklist:**
- [ ] Week 2 summary saved: `~/.tradingbot/validation/week2_summary.json`
- [ ] All hard stops vetted and tested
- [ ] Thompson posteriors up-to-date (from latest backtest)
- [ ] Kelly sizing validated (no infinite or zero-size positions)
- [ ] Exit rules tested (50% close, stop loss, EOD cutoff)
- [ ] Telegram alerts wired (test message sent & received)
- [ ] Real account prepared ($5K–$10K allocation)
- [ ] Daily monitoring routine established
- [ ] Logs rotating correctly (no disk fill risk)

**Day 1 Live:**
```bash
# Start with ENTRY-ONLY mode (no exits, manual management)
python scripts/paper_trade.py --entry-only

# Reduce Kelly to 50% initially (extra safety)
# kelly_fraction = 0.50 in tradingbot/sizing/kelly.py

# Max daily loss: 1% of account (vs 2% in paper)
# Stops at: -1% P&L, not -2%

# Increase to 100% Kelly after 1 week if P&L ≥ 0%
```

---

## Notes & Questions

**For each day, record:**
- Any unexpected behavior
- Unusual market conditions (gaps, halts, low liquidity)
- Whether debaters behaved as expected on live data
- Confidence level for proceeding (1–10 scale)

**Questions to answer by end of Week 2:**
1. Did income debater voting match backtest (20–30% APPROVE)?
2. Did slippage exceed ±2% assumption? By how much?
3. Did profit targets trigger as expected (54% of trades)?
4. Did regime classification have any crashes or weird buckets?
5. Is win rate ≥ 45%? Trending up or down?

If answers are "yes, yes, yes, no crashes, trending up" → **GREEN LIGHT FOR LIVE.**
