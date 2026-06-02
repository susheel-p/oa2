# Telegram Message Drafts — oa2 Trading System

All messages include meaningful context and professional formatting. Use these as templates.

---

## 1. 🌅 Premarket Market Scan (8:00 AM)

**Current:** ❌ "Premarket signals — 2026-06-02 Scanned: 22 | Bull: 15 | Bear: 0 | Neutral: 7 Bullish:"

**Proposed:** ✅

```
🌅 Premarket Market Scan — 2026-06-02
Scanned: 22 | Bull: 15 | Neutral: 7

Regime: Neutral-Vol Bullish (R5)
VIX: 16.2
Premarket Movers: SPY +0.3% | QQQ +0.5%

Status: Ready for 9:00 AM full-scan
```

**Why better:**
- Clear title instead of vague "Premarket signals"
- Includes regime context (tells you market condition)
- Shows VIX (volatility context)
- Shows premarket price action (gaps, movers)
- Sets expectations for next step

---

## 2. 📊 Daily Market Scan (9:45 AM, after full-scan)

**Current:** ❌ None (or generic summary)

**Proposed:** ✅

```
📊 Market Scan — 2026-06-02
Scanned: 22 | Bull: 15 | Bear: 0 | Neutral: 7

Approved Trades: 3
Rejected: 19

Account Size: $50,000

Top Approved:
• SPY: Short Call Spread (320/325) 2 DTE, 2 contracts
• QQQ: Iron Condor (395/400/410/415) 14 DTE, 1 contract
• IWM: Debit Put Spread (205/200) 7 DTE, 1 contract
```

**Why better:**
- Detailed breakdown of what was approved
- Shows structure and DTE (helps understand risk profile)
- Capital allocation visible (contracts count)
- Clear distinction from premarket scan

---

## 3. ✅ Trade Approved (Real-time)

**Current:** ✅ Good, but can add context

```
Trade approved: SPY BULLISH
Structure: Short Call Spread  320/325  exp 2026-06-04
Size: 2 contract(s)
p_bull = 0.725
```

**Proposed:** ✅ (Better)

```
✅ Trade Approved: SPY BULLISH

Structure: Short Call Spread 320/325
Expiry: 2 DTE (2026-06-04)
Size: 2 contracts

Entry Premium: $186.00
Max Risk: $814.00
Kelly: 15.2%

Conviction: p_bull=72.5%
Regime: R5 (Neutral-Vol Bullish)
```

**Why better:**
- Shows premium received (helps understand value)
- Shows max risk (critical for risk awareness)
- Shows Kelly fraction applied (sizing confidence)
- Regime context explains WHY the signal fired
- Better visual hierarchy

---

## 4. 📤 Position Exit (Real-time)

**Current:** ✅ Exists but minimal

```
Position EXIT: SPY [trade_id]
Reason: 50% profit  Urgency: EXECUTE
P&L: +$125.50
```

**Proposed:** ✅ (Better)

```
📤 Position EXIT: SPY

Structure: Short Call Spread 320/325
Exit Reason: 50% profit target hit
Urgency: EXECUTE

P&L: +$125.50 (+18.2%)
Hold Duration: 3d 2h 47m

Entry: 2026-06-02 08:45:12
Exit: 2026-06-02 12:32:59
```

**Why better:**
- Shows profit as percentage (more intuitive)
- Shows hold duration (helps evaluate strategy performance)
- Shows exact timestamps (for debugging/analysis)
- Clear urgency level helps you prioritize actions

---

## 5. ⚙️ System Health Report (8:00 AM)

**Current:** ❌ Generic system alerts

**Proposed:** ✅

```
⚙️ System Health Report — 2026-06-02

Daemon: RUNNING ✅
Heartbeat: HEALTHY (32s ago)
Last Activity: 2026-06-02 08:15:42

Recent Errors: 0
Positions Open: 2

All systems nominal. Ready for trading day.
```

**When Degraded:** ⚠️

```
⚙️ System Health Report — 2026-06-02

Daemon: RUNNING
Heartbeat: AGING (847s ago) ⚠️
Last Activity: 2026-06-02 07:28:15

Recent Errors: 1
Positions Open: 2

⚠️ Heartbeat aging - daemon may be hanging on long operation
Check: market_monitor logs for blocked subprocess
```

**Why better:**
- Proactive health status before problems arise
- Clear aging thresholds for heartbeat
- Actionable advice for degraded states
- Tells you what to check if issues detected

---

## 6. 🚨 System Alert (Noon Watchdog Check)

**Current:** ✅ Exists, but can be clearer

```
🚨 System Issues Detected (Noon Check)
❌ Daemon is stale - not responding
⚠️ 2 error(s) detected
```

**Proposed:** ✅ (Better)

```
🚨 CRITICAL: System Issues Detected — 2026-06-02 12:00:00

Issues:
❌ Daemon stale (no heartbeat for 1395s, expected every 60s)
⚠️ 2 pipeline error(s): broker_api timeout, exit_rules NameError

Last Known Activity: 2026-06-02 11:15:23

Immediate Actions:
1. Restart daemon: docker-compose restart tradingbot-daemon
2. Check logs: tail -f logs/daemon.log
3. Verify broker connection: test connectivity manually

Manual Entry/Exit Required: YES (positions may be unmonitored)
```

**Why better:**
- **CRITICAL** tag immediately grabs attention
- Explains WHAT went wrong and HOW LONG ago
- Shows last known state
- Provides specific recovery steps
- Tells you if manual intervention needed

---

## 7. 📈 Daily Summary (4:15 PM)

**Current:** ✅ Decent, but can be enhanced

```
Trading run complete
Approved: 3
Rejected: 19
Errors: 0
Exit alerts: 2

Open Positions:
  SPY: 2 contracts, P&L +$247.32, 2 DTE (Short Call Spread)
  QQQ: 1 contract, P&L -$18.50, 9 DTE (Iron Condor)
```

**Proposed:** ✅ (Better)

```
📈 Daily Summary — 2026-06-02

Trades Executed:
• Approved: 3 | Rejected: 19 | Errors: 0
• Win Rate: 100% (3/3 trades profitable)

Book Status:
• Open Positions: 2
• Net Delta: +0.45 | Net Theta: +18.50/day
• Unrealized P&L: +$228.82

Daily P&L:
• Closed Trades: +$156.00 (4 exits)
• Account Value: $50,384.82 (↑ 0.77% from start)

Open Positions:
1. SPY: 2 contracts, +$247.32 (+18.2%), 2 DTE → Monitor closely for expiry
2. QQQ: 1 contract, -$18.50 (-2.7%), 9 DTE → Comfortable hold

Tomorrow (2026-06-03):
• Regime Outlook: Continue neutral-vol bullish
• Market Status: Trading (FOMC decision at 2:00 PM)
• Action Items: Close SPY before expiry, evaluate QQQ roll
```

**Why better:**
- Shows performance metrics (win rate, account growth %)
- Delta/Theta exposure shows risk profile for next day
- Individual position analysis with recommendations
- Anticipates tomorrow's context
- Helps you assess day and plan ahead

---

## 8. ⚠️ Critical Error Alert (Immediate)

**Current:** ❌ Generic error messages

**Proposed:** ✅

```
⚠️ CRITICAL: Broker Connection Lost — 2026-06-02 09:15:33

Component: Broker API
Error: Connection timeout after 30s (max retries exceeded)

Impact:
❌ SPY trade entry FAILED (3 contracts, Short Call Spread 320/325)
⚠️ Exit scanning PAUSED (positions unmonitored)

Last Successful Request: 2026-06-02 09:14:52 (22s ago)

Recovery Steps:
1. Verify broker API status: https://status.moomoo.com
2. Check network connectivity: ping api.moomoo.com
3. Restart daemon: docker-compose restart tradingbot-daemon
4. If unresolved: Manual entry/exit required for open positions

Urgency: IMMEDIATE — 1 failed trade, positions unmonitored
```

**Why better:**
- Explains what failed and why
- Shows impact (which trades, which features down)
- Provides immediate recovery steps
- Tells you urgency level for action

---

## Summary Table

| Message Type | Trigger | Who | When | Critical? |
|---|---|---|---|---|
| 🌅 Premarket Scan | Scheduled | Auto | 8:00 AM | No |
| 📊 Market Scan | Full-scan complete | Auto | 9:45 AM | No |
| ✅ Trade Approved | Trade passes sizing | Auto | Real-time | No |
| 📤 Position Exit | Exit rules fire | Auto | Real-time | No |
| ⚙️ Health Report | Scheduled | Auto | 8:00 AM | No |
| 🚨 System Alert | Issues detected | Auto | Noon | YES |
| 📈 Daily Summary | Market close | Auto | 4:15 PM | No |
| ⚠️ Critical Error | Pipeline failure | Auto | Real-time | YES |

---

## Implementation Checklist

- [ ] Update `telegram_templates.yml` (created ✅)
- [ ] Modify `telegram_notify.py` to use YAML templates
- [ ] Update `market_monitor.py` to call correct notification functions
- [ ] Update `review_logs.ps1` to optionally post to Telegram
- [ ] Test all messages in staging
- [ ] Verify formatting on Telegram (max width, emoji rendering)
- [ ] Set message frequency (avoid spam, all critical alerts enabled)
- [ ] Add message history logging (for audits)
