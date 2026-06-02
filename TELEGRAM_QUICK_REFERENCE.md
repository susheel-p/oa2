# Telegram Messages — Quick Reference Guide

Fast lookup for message types, triggers, and examples.

---

## Message Type Quick Lookup

### 🌅 Premarket Market Scan
**When:** 8:00 AM (before market open)  
**Who:** market_monitor.py scheduler  
**Function:** `notify_premarket_scan(summary)`

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

**Output:**
```
🌅 Premarket Market Scan — 2026-06-02
Scanned: 22 | Bull: 15 | Neutral: 7

Regime: Neutral-Vol Bullish (R5)
VIX: 16.2
Premarket Movers: SPY +0.3% | QQQ +0.5%

Status: Ready for 9:00 AM full-scan
```

---

### 📊 Market Scan
**When:** ~9:45 AM (after full-scan)  
**Who:** market_monitor.py  
**Function:** `notify_scan_summary(summary)`

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
    'approved_trades': [
        {'ticker': 'SPY', 'direction': 'BULLISH', 'structure': 'Short Call Spread 320/325', 'dte': 2, 'contracts': 2},
        {'ticker': 'QQQ', 'direction': 'BEARISH', 'structure': 'Iron Condor 395/400/410/415', 'dte': 14, 'contracts': 1},
    ]
})
```

**Output:**
```
📊 Market Scan — 2026-06-02
Scanned: 22 | Bull: 15 | Bear: 0 | Neutral: 7

Approved Trades: 3
Rejected: 19

Account Size: $50,000

Top Approved:
  • SPY: Short Call Spread 320/325 2 DTE, 2 contracts
  • QQQ: Iron Condor 395/400/410/415 14 DTE, 1 contract
```

---

### ✅ Trade Approved
**When:** Real-time (when trade passes sizing)  
**Who:** paper_trade.py  
**Function:** `notify_trade(ticker, result, fills=None)`

```python
result = {
    'decision': {
        'direction': 'BULLISH',
        'p_bull': 0.725,
        'regime_id': 5,
        'recommended_expiry': '2026-06-04'
    },
    'sizing': {
        'contracts': 2,
        'kelly_fraction': 0.152,
        'max_dollars_at_risk': 814.00
    },
    'structure_pick': {
        'structure': 'Short Call Spread',
        'short_strike': 320,
        'long_strike': 325,
        'expiry': '2026-06-04'
    },
    'entry_premium': 0.93  # per contract
}

telegram_notify.notify_trade(ticker='SPY', result=result)
```

**Output:**
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

---

### 📤 Position Exit
**When:** Real-time (when exit rules fire)  
**Who:** exit.py / monitor.py  
**Function:** `notify_exit(ticker, alert)`

```python
alert = {
    'ticker': 'SPY',
    'trade_id': 'trade_20260602_001_spy',
    'reason': '50% profit target hit',
    'urgency': 'EXECUTE',
    'current_pnl': 247.32,
    'current_dte': 2
}

telegram_notify.notify_exit(ticker='SPY', alert=alert)
```

**Output:**
```
📤 Position EXIT: SPY

Structure: Short Call Spread
Exit Reason: 50% profit target hit
Urgency: EXECUTE

P&L: +$247.32
Current DTE: 2
```

---

### ⚙️ System Health Report
**When:** 8:00 AM (before market open)  
**Who:** market_monitor.py scheduler  
**Function:** `notify_system_health(...)`

```python
telegram_notify.notify_system_health(
    daemon_status='RUNNING',
    signals_generated=22,
    heartbeat_age_seconds=32,
    error_count=0,
    position_count=2
)
```

**Output (Healthy):**
```
⚙️ System Health Report — 2026-06-02

Daemon: RUNNING ✅
Heartbeat: HEALTHY (32s ago)
Signals Generated: 22
Recent Errors: 0
Positions Open: 2

✅ All systems nominal. Ready for trading.
```

**Output (Degraded):**
```
⚙️ System Health Report — 2026-06-02

Daemon: RUNNING
Heartbeat: AGING (847s ago) ⚠️
Signals Generated: 22
Recent Errors: 1
Positions Open: 2

⚠️ Heartbeat aging - daemon may be hanging
Check market_monitor logs for blocked operations
```

---

### 🚨 System Issues Alert
**When:** Noon (if issues detected)  
**Who:** watchdog.py  
**Function:** `notify_system_issues(...)`

```python
telegram_notify.notify_system_issues(
    is_stale=True,
    error_count=2,
    last_activity='2026-06-02 11:15:23',
    heartbeat_age_seconds=1395,
    errors_detail=[
        'broker_api: Connection timeout after 30s',
        'exit_rules: NameError - undefined variable'
    ]
)
```

**Output:**
```
🚨 CRITICAL: System Issues Detected — 2026-06-02 12:00:00

Issues:
  ❌ Daemon stale (no heartbeat for 1395s)
  ⚠️ 2 pipeline error(s) detected
     - broker_api: Connection timeout after 30s
     - exit_rules: NameError - undefined variable

Last Known Activity: 2026-06-02 11:15:23

Immediate Actions:
1. Restart daemon: docker-compose restart tradingbot-daemon
2. Check logs: tail -f logs/daemon.log
3. Verify broker connectivity

⚠️ Manual Entry/Exit Required: YES (positions may be unmonitored)
```

---

### 📈 Daily Summary
**When:** 4:15 PM (after market close)  
**Who:** market_monitor.py scheduler  
**Function:** `notify_summary(summary)`

```python
telegram_notify.notify_summary({
    'approved_count': 3,
    'rejected_count': 19,
    'error_count': 0,
    'exit_alert_count': 2,
    'account_size': 50000,
    'book_state': {
        'position_count': 2,
        'net_delta': 0.45,
        'net_theta': 18.50
    },
    'open_positions': [
        {
            'ticker': 'SPY',
            'contracts': 2,
            'current_pnl': 247.32,
            'current_pnl_pct': 0.182,
            'current_dte': 2,
            'structure': 'Short Call Spread'
        }
    ]
})
```

**Output:**
```
📈 Daily Summary

Trades Executed:
  Approved: 3 | Rejected: 19 | Errors: 0
  Exit alerts: 2

Account Size: $50,000

Book State:
  Open Positions: 2
  Net Delta: +0.45
  Net Theta: +18.50/day

Open Positions:
  • SPY: 2c, P&L +$247.32 (+18.2%), 2 DTE (Short Call Spread)
```

---

### ⚠️ Critical Error Alert
**When:** Real-time (critical failure)  
**Who:** Error handlers  
**Function:** `notify_critical_error(...)`

```python
telegram_notify.notify_critical_error(
    component='Broker API',
    error_msg='Connection timeout after 30s (max retries exceeded)',
    affected=[
        'SPY trade entry FAILED (3 contracts, Short Call Spread)',
        'Exit scanning PAUSED (positions unmonitored)'
    ],
    severity='CRITICAL',
    recovery_steps=[
        'Verify broker API status: https://status.moomoo.com',
        'Check network connectivity: ping api.moomoo.com',
        'Restart daemon: docker-compose restart tradingbot-daemon',
        'If unresolved: Manual entry/exit required'
    ]
)
```

**Output:**
```
⚠️ CRITICAL: Broker API Error — 2026-06-02 09:15:33

Error: Connection timeout after 30s (max retries exceeded)

Impact:
  • SPY trade entry FAILED (3 contracts, Short Call Spread)
  • Exit scanning PAUSED (positions unmonitored)

Recovery Steps:
1. Verify broker API status: https://status.moomoo.com
2. Check network connectivity: ping api.moomoo.com
3. Restart daemon: docker-compose restart tradingbot-daemon
4. If unresolved: Manual entry/exit required

Urgency: IMMEDIATE — Manual review required
```

---

## Field Reference by Message Type

### Premarket Scan
```
date               str (YYYY-MM-DD)
tickers_scanned    int
bullish_count      int
neutral_count      int
regime_summary     str (e.g., "Neutral-Vol Bullish (R5)")
vix_level          float or str
premarket_movers   str (e.g., "SPY +0.3% | QQQ +0.5%")
```

### Market Scan
```
date               str (YYYY-MM-DD)
tickers_scanned    int
bullish_count      int
bearish_count      int
neutral_count      int
approved_count     int
rejected_count     int
account_size       float
approved_trades    list[dict] with: ticker, direction, structure, dte, contracts
```

### Trade Approved
```
decision:
  direction        str (BULLISH|BEARISH)
  p_bull           float (0.0-1.0)
  regime_id        int
  recommended_expiry str (YYYY-MM-DD)

sizing:
  contracts        int
  kelly_fraction   float (0.0-1.0)
  max_dollars_at_risk float

structure_pick:
  structure        str
  short_strike     float
  long_strike      float
  expiry           str

entry_premium      float (per contract)
```

### Position Exit
```
ticker             str
trade_id           str
reason             str
urgency            str (IMMEDIATE|EXECUTE|EVALUATE)
current_pnl        float
current_dte        int
```

### System Health
```
daemon_status      str (RUNNING|STALE|STOPPED)
signals_generated  int
heartbeat_age_seconds int
error_count        int
position_count     int
```

### System Issues
```
is_stale           bool
error_count        int
last_activity      str (YYYY-MM-DD HH:MM:SS)
heartbeat_age_seconds int
errors_detail      list[str]
```

### Daily Summary
```
approved_count     int
rejected_count     int
error_count        int
exit_alert_count   int
account_size       float

book_state:
  position_count   int
  net_delta        float
  net_theta        float

open_positions:    list[dict] with ticker, contracts, current_pnl, current_pnl_pct, current_dte, structure
```

---

## Daily Message Schedule

```
Timeline          Message             Function
─────────────────────────────────────────────────────
08:00 AM          🌅 Premarket Scan   notify_premarket_scan()
08:00 AM          ⚙️ Health Report    notify_system_health()
09:45 AM          📊 Market Scan      notify_scan_summary()
─────────────────────────────────────────────────────
Real-time         ✅ Trade Approved   notify_trade()
Real-time         📤 Position Exit    notify_exit()
Real-time         ⚠️ Critical Error   notify_critical_error()
─────────────────────────────────────────────────────
Noon              🚨 System Issues    notify_system_issues() [if issues]
─────────────────────────────────────────────────────
16:15 PM          📈 Daily Summary    notify_summary()
```

---

## Common Value Formats

### Currency
```python
f"${value:,.2f}"  # $1,234.56
```

### Percentages
```python
f"{value:.1%}"    # 72.5%
f"{value:.2%}"    # 72.50%
```

### Signed Numbers (P&L)
```python
f"{value:+.2f}"   # +247.32 or -18.50
```

### DTE Display
```python
f"{dte} DTE ({date})"  # 2 DTE (2026-06-04)
```

### Regime
```python
f"R{regime_id}"   # R5 or R5 (Neutral-Vol Bullish)
```

---

## Error Severities

| Severity | Emoji | Meaning | Action |
|---|---|---|---|
| CRITICAL | 🚨 | System down, positions unmonitored | Immediate manual intervention |
| HIGH | ⚠️ | Major issue, degraded function | Check logs, restart if needed |
| MEDIUM | ⚠️ | Issue detected, trading continues | Monitor and resolve |
| LOW | ℹ️ | Minor issue, no impact | Log and note |

---

## Testing

### Quick Test
```bash
python scripts/telegram_notify.py "Test from oa2"
```

### Test Function with Data
```python
from scripts.telegram_notify import notify_premarket_scan

summary = {
    'date': '2026-06-02',
    'tickers_scanned': 22,
    'bullish_count': 15,
    'neutral_count': 7,
    'regime_summary': 'Neutral-Vol Bullish (R5)',
    'vix_level': '16.2',
    'premarket_movers': 'SPY +0.3%'
}

notify_premarket_scan(summary)
```

---

## Troubleshooting

### No message sent?
1. Check `.env` for `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
2. Verify credentials are valid
3. Check internet connectivity

### Message too long?
Max 4096 characters. Solutions:
- Show top 3 items instead of all
- Split into multiple messages
- Abbreviate structure names

### Emoji not rendering?
Use only standard emojis:
- ✅ ❌ ⚠️ 🚨 📊 📤 ⚙️ 🌅 📈 ℹ️

---

## Implementation Order

1. ✅ Update `telegram_notify.py` with new functions
2. → Update `market_monitor.py` to call premarket/scan/health functions
3. → Update `paper_trade.py` to call notify_trade()
4. → Update exit handlers to call notify_exit()
5. → Update watchdog.py to call notify_system_issues()
6. → Update error handlers to call notify_critical_error()
7. → Test each message type
8. → Go live

---

## Links

- **Message Examples:** `TELEGRAM_MESSAGE_DRAFTS.md`
- **Integration Guide:** `TELEGRAM_INTEGRATION.md`
- **Complete Summary:** `TELEGRAM_UPDATES_SUMMARY.md`
- **Templates File:** `scripts/telegram_templates.yml`
