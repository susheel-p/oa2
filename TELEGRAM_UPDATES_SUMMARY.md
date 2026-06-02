# Telegram Notification System — Updates Summary

## What Changed

### 1. **Message Terminology** ✅
Changed "Premarket signals" → "Market Scan" (and related variants)

**Before:**
```
Premarket signals — 2026-06-02
Scanned: 22 | Bull: 15 | Bear: 0 | Neutral: 7
Bullish:
```

**After:**
```
🌅 Premarket Market Scan — 2026-06-02
Scanned: 22 | Bull: 15 | Neutral: 7

Regime: Neutral-Vol Bullish (R5)
VIX: 16.2
Premarket Movers: SPY +0.3% | QQQ +0.5%

Status: Ready for 9:00 AM full-scan
```

---

### 2. **Added Meaningful Context** ✅

Each message now includes:
- **Date/Time context** (when the message was sent)
- **Market conditions** (regime, VIX, volatility)
- **Account status** (size, P&L, positions)
- **Actionable insights** (next steps, urgency levels)
- **Risk/reward metrics** (Kelly, max risk, premium)

---

### 3. **New Functions in telegram_notify.py** ✅

```python
notify_premarket_scan()    # 🌅 8:00 AM premarket scan
notify_scan_summary()      # 📊 9:45 AM full-scan summary (NEW)
notify_system_health()     # ⚙️ 8:00 AM health report (ENHANCED)
notify_system_issues()     # 🚨 Noon watchdog alert (ENHANCED)
notify_critical_error()    # ⚠️ Real-time error alerts (NEW)
```

---

### 4. **Template System** ✅

Created `scripts/telegram_templates.yml` with:
- 8 message template types
- Field descriptions for each
- Real-world examples
- Formatting guidelines
- Emoji usage recommendations

---

## Files Created

### 1. `scripts/telegram_templates.yml` (275 lines)
Centralized message templates with examples and field references.

**Usage:**
```python
import yaml
with open('scripts/telegram_templates.yml') as f:
    templates = yaml.safe_load(f)
template = templates['templates']['scan_summary']['template']
```

### 2. `TELEGRAM_MESSAGE_DRAFTS.md` (320 lines)
Visual comparison of **before/after** for each message type with explanations of why improvements matter.

**Sections:**
- 🌅 Premarket Scan
- 📊 Daily Market Scan
- ✅ Trade Approved
- 📤 Position Exit
- ⚙️ System Health
- 🚨 System Alert
- 📈 Daily Summary
- ⚠️ Critical Error

### 3. `TELEGRAM_INTEGRATION.md` (400+ lines)
Complete integration guide for developers showing:
- How to call each function
- Expected data structures
- Code examples
- Field reference
- Formatting conventions
- Testing procedures

### 4. `TELEGRAM_UPDATES_SUMMARY.md` (this file)
Overview of all changes and new capabilities.

---

## Message Types & Triggers

### Daily Schedule

| Time | Message | Type | Purpose |
|---|---|---|---|
| 8:00 AM | 🌅 Premarket Scan | Auto | Check regime before market open |
| 8:00 AM | ⚙️ System Health | Auto | Verify daemon is ready |
| 9:45 AM | 📊 Market Scan | Auto | Summary of full-scan results |
| Real-time | ✅ Trade Approved | Auto | Each approved trade |
| Real-time | 📤 Position Exit | Auto | Each position closed |
| Real-time | ⚠️ Critical Error | Auto | If errors detected |
| Noon | 🚨 System Alert | Auto | If daemon stale or issues |
| 4:15 PM | 📈 Daily Summary | Auto | End-of-day report |

### Ad-hoc Messages
- Trade approvals (whenever triggered)
- Exit alerts (when rules fire)
- Error alerts (critical failures)

---

## Enhanced Existing Functions

### `notify_system_health()` — Before → After

**Before:**
```
✅ System Health Report (8am)

Daemon: running
Signals generated: 22
Heartbeat age: 32s
```

**After:**
```
⚙️ System Health Report — 2026-06-02

Daemon: RUNNING ✅
Heartbeat: HEALTHY (32s ago)
Last Activity: 2026-06-02 08:15:42

Recent Errors: 0
Positions Open: 2

✅ All systems nominal. Ready for trading.
```

**Improvements:**
- Clear status emoji and labels
- Explicit health indicators
- Account for degradation (aging heartbeat warning)
- Actionable message when issues detected

---

### `notify_system_issues()` — Before → After

**Before:**
```
🚨 System Issues Detected (Noon Check)
❌ Daemon is stale - not responding
⚠️ 2 error(s) detected
```

**After:**
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

**Improvements:**
- Specific error details and duration
- Last known state for context
- Step-by-step recovery procedure
- Clear impact statement

---

### `format_trade()` — Before → After

**Before:**
```
Trade approved: SPY BULLISH
Structure: Short Call Spread  320/325  exp 2026-06-04
Size: 2 contract(s)  Kelly=15.20%
p_bull = 0.725
```

**After:**
```
✅ Trade Approved: SPY BULLISH

Structure: Short Call Spread 320/325
Expiry: 2 DTE (2026-06-04)
Size: 2 contracts

Entry Premium: $186.00
Max Risk: $814.00
Kelly: 15.2%

Conviction: p_bull=72.5%
Regime: R5
```

**Improvements:**
- Premium received (helps understand value)
- Max risk at stake (critical)
- DTE + calendar (helps prioritization)
- Better visual hierarchy
- Context-rich conviction display

---

## New Functions

### `notify_premarket_scan(summary: dict) -> bool`
Sends 8:00 AM premarket market scan with regime, VIX, and movers.

**Example:**
```python
telegram_notify.notify_premarket_scan({
    'date': '2026-06-02',
    'tickers_scanned': 22,
    'bullish_count': 15,
    'neutral_count': 7,
    'regime_summary': 'Neutral-Vol Bullish (R5)',
    'vix_level': '16.2',
    'premarket_movers': 'SPY +0.3% | QQQ +0.5%'
})
```

### `notify_scan_summary(summary: dict) -> bool`
Sends 9:45 AM market scan summary after full-scan completes.

**Example:**
```python
telegram_notify.notify_scan_summary({
    'date': '2026-06-02',
    'tickers_scanned': 22,
    'bullish_count': 15,
    'bearish_count': 0,
    'neutral_count': 7,
    'approved_count': 3,
    'rejected_count': 19,
    'account_size': 50000,
    'approved_trades': [...]  # List of top approved trades
})
```

### `notify_critical_error(component, error_msg, affected, severity, recovery_steps) -> bool`
Sends real-time critical error alerts with recovery guidance.

**Example:**
```python
telegram_notify.notify_critical_error(
    component='Broker API',
    error_msg='Connection timeout after 30s',
    affected=['SPY trade entry FAILED', 'Exit scanning PAUSED'],
    severity='CRITICAL',
    recovery_steps=[
        'Check broker status',
        'Verify network',
        'Restart daemon'
    ]
)
```

---

## Integration Checklist

### Phase 1: Update Functions (DONE ✅)
- [x] Updated `format_trade()` with premium, risk, Kelly display
- [x] Enhanced `notify_system_health()` with detailed status
- [x] Enhanced `notify_system_issues()` with recovery steps
- [x] Added `notify_premarket_scan()` function
- [x] Added `notify_scan_summary()` function
- [x] Added `notify_critical_error()` function

### Phase 2: Integration Points (TODO)
- [ ] Update `market_monitor.py` to call `notify_premarket_scan()` at 8:00 AM
- [ ] Update `market_monitor.py` to call `notify_scan_summary()` after full-scan
- [ ] Update `paper_trade.py` to use enhanced `notify_trade()` function
- [ ] Update exit handler to use enhanced `notify_exit()` function
- [ ] Update watchdog.py to use enhanced `notify_system_issues()`
- [ ] Update error handlers to use `notify_critical_error()`
- [ ] Optional: Update `review_logs.ps1` to post Telegram summary

### Phase 3: Testing (TODO)
- [ ] Test each function with sample data
- [ ] Verify message formatting on Telegram
- [ ] Check for character limit violations (4096 max)
- [ ] Verify emoji rendering
- [ ] Test in production with real signals

---

## Key Improvements Summary

| Aspect | Before | After | Benefit |
|---|---|---|---|
| **Terminology** | "Premarket signals" | "Market Scan" | Clear, professional terminology |
| **Context** | Minimal | Full (regime, VIX, movers) | Understand market conditions |
| **Trade Details** | Structure only | Structure + premium + risk + Kelly | Make informed decisions |
| **Status Reporting** | Generic counts | Detailed health + actionable alerts | Proactive monitoring |
| **Error Messages** | Basic error | Detailed + recovery steps | Faster resolution |
| **Consistency** | Varied format | Unified templates | Professional appearance |
| **Visual Hierarchy** | Flat list | Structured sections | Easier scanning |

---

## Next Steps

1. **Review message examples** in `TELEGRAM_MESSAGE_DRAFTS.md`
2. **Integrate functions** into `market_monitor.py` (see `TELEGRAM_INTEGRATION.md` for examples)
3. **Update data structures** passed to notify functions to include new fields
4. **Test with sample data** before going live
5. **Monitor first day** to ensure messages are clear and actionable

---

## File Locations

```
oa2-new/
  ├── scripts/
  │   ├── telegram_notify.py         ← UPDATED with new functions
  │   ├── telegram_templates.yml     ← NEW: Template system
  │   └── market_monitor.py          ← TODO: Integrate calls
  ├── TELEGRAM_MESSAGE_DRAFTS.md     ← NEW: Before/after examples
  ├── TELEGRAM_INTEGRATION.md        ← NEW: Developer guide
  └── TELEGRAM_UPDATES_SUMMARY.md    ← NEW: This file
```

---

## Questions?

Reference files:
- **How to structure data?** → `TELEGRAM_INTEGRATION.md` (field reference section)
- **What should message look like?** → `TELEGRAM_MESSAGE_DRAFTS.md` (visual examples)
- **How to call functions?** → `TELEGRAM_INTEGRATION.md` (code examples)
- **What templates exist?** → `scripts/telegram_templates.yml`

---

## Example: Complete Daily Flow

```
8:00 AM → notify_premarket_scan() 
        → notify_system_health()
        
9:45 AM → notify_scan_summary()
        
9:47 AM → notify_trade('SPY', result1)    # Trade 1
        → notify_trade('QQQ', result2)    # Trade 2
        → notify_trade('IWM', result3)    # Trade 3
        
2:15 PM → notify_exit('SPY', alert)       # Position closed at 50% profit
        
4:15 PM → notify_summary(daily_summary)   # End-of-day report
```

Each message is **clear, actionable, and contextual**.
