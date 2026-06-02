# Telegram Integration Guide — Updated Messaging System

This guide shows how to use the new Telegram notification system with improved templates and meaningful context.

---

## Quick Start

### 1. Template System
All message templates are defined in `scripts/telegram_templates.yml`. This centralizes messaging and ensures consistency.

### 2. Updated Functions in `telegram_notify.py`

| Function | Purpose | Trigger | When |
|---|---|---|---|
| `notify_premarket_scan(summary)` | 🌅 Premarket scan | 8:00 AM | Before market open |
| `notify_scan_summary(summary)` | 📊 Daily market scan | After full-scan | ~9:45 AM |
| `notify_trade(ticker, result, fills)` | ✅ Trade approved | Real-time | When trade passes sizing |
| `notify_exit(ticker, alert)` | 📤 Position exit | Real-time | When exit rules fire |
| `notify_system_health(...)` | ⚙️ Health check | 8:00 AM | Before market |
| `notify_system_issues(...)` | 🚨 Critical alert | Noon check | If issues detected |
| `notify_summary(summary)` | 📈 Daily summary | 4:15 PM | After market close |
| `notify_critical_error(...)` | ⚠️ Error alert | Real-time | Critical failures |

---

## Integration Examples

### Market Monitor (market_monitor.py)

#### 8:00 AM Premarket Scan

```python
from scripts import telegram_notify

# After premarket scan completes
premarket_summary = {
    'date': '2026-06-02',
    'tickers_scanned': 22,
    'bullish_count': 15,
    'neutral_count': 7,
    'regime_summary': 'Neutral-Vol Bullish (R5)',
    'vix_level': '16.2',
    'premarket_movers': 'SPY +0.3% | QQQ +0.5% | GLD -0.2%'
}

telegram_notify.notify_premarket_scan(premarket_summary)
```

#### 9:45 AM Market Scan Summary

```python
# After full-scan pipeline completes
scan_summary = {
    'date': '2026-06-02',
    'tickers_scanned': 22,
    'bullish_count': 15,
    'bearish_count': 0,
    'neutral_count': 7,
    'approved_count': 3,
    'rejected_count': 19,
    'account_size': 50000,
    'approved_trades': [
        {
            'ticker': 'SPY',
            'direction': 'BULLISH',
            'structure': 'Short Call Spread 320/325',
            'dte': 2,
            'contracts': 2
        },
        # ... more trades
    ]
}

telegram_notify.notify_scan_summary(scan_summary)
```

#### 8:00 AM System Health Report

```python
# Before market opens, health check
telegram_notify.notify_system_health(
    daemon_status='RUNNING',
    signals_generated=22,
    heartbeat_age_seconds=32,
    error_count=0,
    position_count=2
)
```

#### Noon Watchdog Alert (if issues)

```python
# At noon, if daemon is stale
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

#### 4:15 PM Daily Summary

```python
# After market close
daily_summary = {
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
        },
        # ... more positions
    ]
}

telegram_notify.notify_summary(daily_summary)
```

---

### Trade Execution (paper_trade.py)

#### Trade Approved

```python
# When trade passes sizing gate
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

telegram_notify.notify_trade(ticker='SPY', result=result, fills=None)
```

---

### Exit Events (exit.py / monitor.py)

#### Position Exit Alert

```python
# When exit rules fire
alert = {
    'ticker': 'QQQ',
    'trade_id': 'trade_20260602_001_qqq',
    'reason': '50% profit target hit',
    'urgency': 'EXECUTE',
    'current_pnl': 125.50,
    'current_dte': 9
}

telegram_notify.notify_exit(ticker='QQQ', alert=alert)
```

---

### System Errors (error handlers)

#### Critical Error

```python
# When broker API fails
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

---

## PowerShell Integration (review_logs.ps1)

### Optional: Post Telegram Summary After Manual Review

```powershell
# At end of review_logs.ps1, after analysis:

$telegramMsg = @"
📊 Market Scan — $Date
Scanned: $($tickerRecords.Count) | Bull: $bullishCount | Bear: $bearishCount | Neutral: $neutralCount

Approved Trades: $($approved.Count)
Rejected: $($tickerRecords.Count - $approved.Count)

Account Size: `$$([Math]::Round($summary.account_size))
"@

# Option 1: Call Python script to send
python scripts\telegram_notify.py $telegramMsg

# Option 2: Call Telegram API directly (if credentials in env)
$botToken = $env:TELEGRAM_BOT_TOKEN
$chatId = $env:TELEGRAM_CHAT_ID
if ($botToken -and $chatId) {
    $uri = "https://api.telegram.org/bot$botToken/sendMessage"
    Invoke-WebRequest -Uri $uri -Method Post -Body @{
        chat_id = $chatId
        text = $telegramMsg
    } | Out-Null
}
```

---

## Message Field Reference

### Premarket Scan Summary Fields
```python
{
    'date': 'YYYY-MM-DD',
    'tickers_scanned': int,
    'bullish_count': int,
    'neutral_count': int,
    'regime_summary': 'str (e.g., "Neutral-Vol Bullish (R5)")',
    'vix_level': 'float or str',
    'premarket_movers': 'str (e.g., "SPY +0.3% | QQQ +0.5%")'
}
```

### Scan Summary Fields
```python
{
    'date': 'YYYY-MM-DD',
    'tickers_scanned': int,
    'bullish_count': int,
    'bearish_count': int,
    'neutral_count': int,
    'approved_count': int,
    'rejected_count': int,
    'account_size': float,
    'approved_trades': [
        {
            'ticker': 'str',
            'direction': 'BULLISH|BEARISH',
            'structure': 'str',
            'dte': int,
            'contracts': int
        }
    ]
}
```

### Daily Summary Fields
```python
{
    'approved_count': int,
    'rejected_count': int,
    'error_count': int,
    'exit_alert_count': int,
    'account_size': float,
    'book_state': {
        'position_count': int,
        'net_delta': float,
        'net_theta': float
    },
    'open_positions': [
        {
            'ticker': 'str',
            'contracts': int,
            'current_pnl': float,
            'current_pnl_pct': float,
            'current_dte': int,
            'structure': 'str'
        }
    ]
}
```

---

## Formatting Conventions

### Currency
```python
f"${value:,.2f}"  # $1,234.56
```

### Percentages
```python
f"{value:.1%}"    # 72.5%
f"{value:.2%}"    # 72.50%
```

### Signed Numbers
```python
f"{value:+.2f}"   # +123.45 or -123.45
```

### Days to Expiration
```python
f"{dte} DTE ({date})"  # 2 DTE (2026-06-04)
```

---

## Testing

### Test Individual Functions

```bash
# Test premarket scan
python -c "
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
"

# Test trade approved
python -c "
from scripts.telegram_notify import notify_trade
result = {
    'decision': {'direction': 'BULLISH', 'p_bull': 0.725, 'regime_id': 5},
    'sizing': {'contracts': 2, 'kelly_fraction': 0.15},
    'structure_pick': {'structure': 'Call', 'expiry': '2026-06-04'}
}
notify_trade('SPY', result)
"
```

### Test via PowerShell

```powershell
python scripts/telegram_notify.py "Test message from oa2 system"
```

---

## Changelog

| Date | Change |
|---|---|
| 2026-06-02 | ✅ Created telegram_templates.yml with all message types |
| 2026-06-02 | ✅ Added notify_premarket_scan() function |
| 2026-06-02 | ✅ Added notify_scan_summary() function |
| 2026-06-02 | ✅ Enhanced notify_system_health() with detailed status |
| 2026-06-02 | ✅ Enhanced notify_system_issues() with actionable recovery steps |
| 2026-06-02 | ✅ Added notify_critical_error() for exception handling |
| 2026-06-02 | ✅ Improved format_trade() with premium, risk, Kelly display |

---

## Common Issues

### "Message too long" (Telegram limit 4096 chars)
Telegram has a 4096 character limit. Solutions:
1. Shorten detailed breakdowns (show top 3 trades instead of all)
2. Send multiple messages (one per section)
3. Use HTML/Markdown formatting to compress (not recommended)

### Emoji not rendering
Some emojis don't render on all devices. Safe emojis:
- ✅ (check)
- ❌ (X)
- ⚠️ (warning)
- 📊 (chart)
- 🚨 (alarm)

### Credentials missing
Silent no-op if `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` not in .env.
Check with:
```bash
echo $env:TELEGRAM_BOT_TOKEN  # PowerShell
echo $TELEGRAM_BOT_TOKEN       # Bash
```
