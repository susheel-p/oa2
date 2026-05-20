---
name: tradingbot-paper-trade
description: Operates the tradingbot automated paper trading system. Use when the user asks to run paper trading, start the daily scan, check paper trade results, review signals, push trade logs to GitHub, or manage the tradingbot paper trading pipeline.
---

# tradingbot Paper Trading Skill

## What this skill does

Runs `scripts/paper_trade.py` — the fully automated daily paper trading runner that:
1. Scans all 22 tickers through the full 9-layer pipeline
2. Logs every signal and decision to `logs/paper_trade_YYYY-MM-DD.jsonl`
3. Checks all open positions for exit alerts
4. Writes a daily summary to `logs/summary_YYYY-MM-DD.json`
5. Pushes logs + summary to GitHub automatically

No human intervention required once started.

## Project context

- **Entry point:** `scripts/paper_trade.py`
- **Pipeline:** `tradingbot/graph/pipeline.py` — `run(ticker, account_size, book, monitor)`
- **Watchlist:** `tradingbot/watchlist/builder.py` — 22 tickers (WATCHLIST constant)
- **Logs:** `logs/` directory — JSONL per ticker scan, JSON daily summary
- **GitHub push:** Uses `GITHUB_TOKEN` env var; pushes via GitHub API (not git)
- **Feature flags:** All set to `1` automatically by the script before imports
- **Default account size:** $50,000 (override with `--account-size` or `TRADINGBOT_ACCOUNT_SIZE` env var)

## How to run

### Full automated daily run
```bash
python scripts/paper_trade.py
```

### Dry run (scan only, no GitHub push)
```bash
python scripts/paper_trade.py --dry-run
```

### Subset of tickers (for testing)
```bash
python scripts/paper_trade.py --tickers SPY QQQ IWM --dry-run
```

### With custom account size
```bash
python scripts/paper_trade.py --account-size 100000
```

### Skip on weekends automatically
```bash
python scripts/paper_trade.py --skip-weekend
```

## Checking results

### View today's summary
```bash
python -c "
import json, datetime
from zoneinfo import ZoneInfo
today = datetime.datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
print(json.dumps(json.load(open(f'logs/summary_{today}.json')), indent=2))
"
```

### View approved trades from today's scan
```bash
python -c "
import json, datetime
from zoneinfo import ZoneInfo
today = datetime.datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
for line in open(f'logs/paper_trade_{today}.jsonl'):
    r = json.loads(line)
    if r['status'] == 'sized_approved':
        print(r['ticker'], r['decision'])
"
```

### View exit alerts
```bash
python -c "
import json, datetime
from zoneinfo import ZoneInfo
today = datetime.datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
data = json.load(open(f'logs/summary_{today}.json'))
for alert in data['exit_alerts']:
    print(alert)
"
```

## Decision statuses

| Status | Meaning |
|---|---|
| `sized_approved` | All 3 gates passed — trade signal generated |
| `sized_rejected` | Kelly / Greeks / CVaR gate blocked the trade |
| `full_pipeline` | Consensus computed but sizing not enabled |
| `debaters_only` | Debaters ran but no consensus |
| `scaffold_only` | Feature flags off |
| `error` | Exception during scan — check `error` field in JSONL |

## Pushing logs manually

If the automatic push failed (no GITHUB_TOKEN, network issue), push manually:
```bash
# The script already wrote the files locally — just re-run with GITHUB_TOKEN set
GITHUB_TOKEN=<token> python scripts/paper_trade.py --dry-run  # re-scan to get fresh logs
# OR directly push existing log files via the GitHub API helper in the script
```

## Before first run (one-time setup)

```bash
# 1. Warm-start the bandit from 6 months of history
python scripts/bandit_warmstart.py --months 6

# 2. Run a dry-run first to verify everything works
python scripts/paper_trade.py --dry-run

# 3. Confirm 0 errors in the output, then enable live push
python scripts/paper_trade.py
```

## Workflow configuration

The Replit workflow `Start application` is configured to run:
```
python scripts/paper_trade.py --skip-weekend
```

Restart the workflow to trigger a fresh scan. The workflow runs once and exits (not a long-running server).

## Log file format

`logs/paper_trade_YYYY-MM-DD.jsonl` — one JSON object per line, one line per ticker:
```json
{
  "ticker": "SPY",
  "ts": "2026-05-18T10:30:00-0400",
  "status": "sized_approved",
  "error": null,
  "decision": { "status": "sized_approved", "contracts": 3, ... },
  "sizing": { "approved": true, "contracts": 3, "kelly": {...}, "cvar": {...} },
  "exit_alerts": [],
  "regime": { "regime_id": 5, "vol_state": "VOL_EXPANSION", ... },
  "consensus": { "direction": "BULLISH", "p_bull": 0.72, "n_eff": 3.8 },
  "duration_ms": 142
}
```

`logs/summary_YYYY-MM-DD.json` — daily roll-up:
```json
{
  "date": "2026-05-18",
  "tickers_scanned": 22,
  "approved_count": 3,
  "rejected_count": 18,
  "error_count": 1,
  "exit_alert_count": 0,
  "approved_tickers": ["SPY", "QQQ", "NVDA"],
  "book_state": { "net_delta": 45.0, "net_vega": -120.0, ... }
}
```

## Known constraints

- Position monitor is in-memory only — restarting the process loses open position state. For multi-day tracking, persist `monitor` to disk or rebuild from broker API (Phase E wiring).
- Market data is currently stub-based (yfinance delayed). Real-time data requires setting `TRADINGBOT_FLOW_SOURCE` to a paid adapter.
- Earnings blackout for mega-caps is enforced at the debater level — the pipeline will reject trades within 2 days of earnings automatically.
