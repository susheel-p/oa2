# oa2 Market Monitor Daemon — Fully Automated Trading

## What is the Daemon?

The market monitor daemon is a background service that **automatically runs your entire trading system** on schedule, 24/7. Once started, it needs no manual intervention.

### Daily Automation Schedule

```
8:30 AM   → Premarket Report (today's trading plan from yesterday's signals)
9:35 AM   → Full-Scan (run debaters, consensus, sizing, save positions)
Every 1 min (9:30 AM–4:00 PM) → Exit-Only (monitor positions, check exits)
4:15 PM   → Postmarket Report (day's results and analysis)
Midnight  → Reset daily flags for next day
```

All times are in Eastern Time (ET).

---

## Quick Start

### Test Mode (before auto-starting)

```bash
# Single cycle test (runs once and exits)
python scripts/market_monitor.py --once

# Test with dry-run (no file writes)
python scripts/market_monitor.py --dry-run

# View daemon log in real-time
tail -f logs/daemon.log  # (Linux/Mac)
powershell -Command "Get-Content 'logs/daemon.log' -Tail 20 -Wait"  # (Windows)
```

### Setup Auto-Start

```bash
# Print setup instructions for your OS
python scripts/market_monitor.py --setup

# Follow the instructions specific to your system:
#   - Windows: Add to Task Scheduler
#   - Linux/Mac: Add to crontab
```

### Manual Daemon (no auto-start)

```bash
# Start daemon in foreground (logs to console + logs/daemon.log)
python scripts/market_monitor.py

# Keep running until you stop it (Ctrl+C)
```

---

## Setup Instructions

### Windows Task Scheduler

1. **Open Task Scheduler**
   - Press `Win+R`
   - Type `taskschd.msc`
   - Click "Create Basic Task..." on the right

2. **General Tab**
   - Name: `oa2-market-monitor`
   - Description: `Automated trading system for oa2`
   - Check: "Run whether user is logged in or not"

3. **Trigger Tab**
   - Click "New..." → Select "At startup"
   - Click OK

4. **Action Tab**
   - Click "New..." → Select "Start a program"
   - Program: `python`
   - Arguments: `C:\path\to\oa2-new\scripts\market_monitor.py`
   - Start in: `C:\path\to\oa2-new`
   - Click OK

5. **Finish**
   - Click "Finish"
   - The task is now scheduled to start at system boot

6. **Test It**
   - Restart your computer
   - Check: `powershell -Command "Get-Content 'logs/daemon.log' -Tail 30 -Wait"`
   - You should see "Market monitor started" at 8:30 AM

**Verify Daemon is Running:**
```powershell
# Check if daemon is running
tasklist | findstr python

# View live logs
powershell -Command "Get-Content 'logs/daemon.log' -Tail 20 -Wait"

# Kill daemon (if needed)
taskkill /pid <PID> /f
```

---

### Linux / Mac (Cron)

1. **Open crontab**
   ```bash
   crontab -e
   ```

2. **Add this line** (all one line):
   ```bash
   @reboot cd /path/to/oa2-new && nohup python scripts/market_monitor.py > logs/daemon.log 2>&1 &
   ```

3. **Save and exit**
   - Vim: `:wq`
   - Nano: `Ctrl+X`, then `Y`, then Enter

4. **Test It**
   - Restart your computer
   - Check: `tail -f logs/daemon.log`
   - You should see "Market monitor started" at 8:30 AM

**Verify Daemon is Running:**
```bash
# Check if daemon is running
ps aux | grep market_monitor

# View live logs
tail -f logs/daemon.log

# Kill daemon (if needed)
pkill -f "python.*market_monitor.py"
```

---

## What Happens Each Day

### 8:30 AM ET — Premarket Report

Before market open, the daemon generates a report showing:
- Yesterday's trading signals (debater votes, consensus, Kelly sizing)
- Current premarket prices
- Entry plans for today's open
- Scenario analysis: +/-2% and +/-1% moves
- Rejected trades (with reasons in plain English)

**File:** `reports/YYYY-MM-DD/premarket.md`

**Use case:** Review at 8:30 AM to know what the system plans to do when the market opens.

### 9:35 AM ET — Full-Scan

The daemon runs the complete trading pipeline:
1. **Debaters vote** — directional, income, volatility, flow, sentiment, dealer
2. **Consensus engine** — aggregate votes with weights
3. **Sizing engine** — Kelly fraction, Greek hard caps, CVaR stress test
4. **Save positions** — approved trades saved to JSON
5. **Generate trade docs** — Obsidian strategy notes for each trade

**Trades are entered if they pass all gates:**
- Kelly fraction >= 0 (positive expected value)
- Greek caps not exceeded
- CVaR stress test passes
- Directional conviction high enough

### 9:30 AM–4:00 PM ET — Exit-Only (Every 1 Minute)

The daemon monitors open positions:
1. **Fetch current prices** — via moomoo or yfinance
2. **Mark-to-market** — update P&L and Greeks
3. **Check exit rules:**
   - Profit target hit (50% of max profit) → close
   - Stop loss hit (max loss exceeded) → close
   - Near expiration (DTE < 2 days) → close
   - Regime flip detected → reduce exposure
   - 3:55 PM → force-close all intraday positions

**Exits are logged to:** `logs/exit_alerts_YYYY-MM-DD.jsonl`

### 4:15 PM ET — Postmarket Report

After market close, the daemon generates a report showing:
- Trades entered today (entry prices, structure, contracts)
- Exit events (time, reason, P&L)
- Watch list (signals that almost traded but were rejected)
- Daily summary (scanned X, entered Y, exits Z)

**File:** `reports/YYYY-MM-DD/postmarket.md`

**Use case:** Review at end of day to understand what happened and why.

### Midnight — Daily Reset

All daily flags are reset:
- `premarket_done_today = False`
- `full_scan_done_today = False`
- `postmarket_done_today = False`

Ready for the next trading day.

---

## Daemon Logs

The daemon logs all activity to `logs/daemon.log`:

```
[2026-05-20T08:30:00-0400] Market monitor started
[2026-05-20T08:30:05-0400] [PREMARKET-REPORT] Starting ...
[2026-05-20T08:30:10-0400] [PREMARKET-REPORT] SUCCESS
[2026-05-20T09:35:00-0400] Full-scan trigger at 09:35:00 (target 09:35:00)
[2026-05-20T09:35:00-0400] [FULL-SCAN] Starting ...
[2026-05-20T09:35:15-0400] [FULL-SCAN] SUCCESS
[2026-05-20T09:35:15-0400] Full-scan flagged as done for today
[2026-05-20T09:36:00-0400] [EXIT-ONLY] Starting ...
[2026-05-20T09:36:05-0400] [EXIT-ONLY] SUCCESS
...
```

### View Logs

**Linux/Mac:**
```bash
# Last 50 lines
tail -50 logs/daemon.log

# Follow in real-time
tail -f logs/daemon.log

# Search for errors
grep ERROR logs/daemon.log
```

**Windows:**
```powershell
# Last 50 lines
Get-Content logs/daemon.log -Tail 50

# Follow in real-time (PowerShell 3.0+)
Get-Content logs/daemon.log -Tail 20 -Wait

# Search for errors
Select-String ERROR logs/daemon.log
```

---

## Daemon Health Monitoring (Watchdog)

The daemon runs silently in the background. If it crashes, hangs, or gets stuck, you might not notice until the market closes. The **daemon watchdog** monitors the daemon's heartbeat and sends you Telegram alerts if something goes wrong.

### How It Works

The daemon writes a fresh timestamp every time it completes a scheduling loop. The watchdog script checks this timestamp:

- **If fresh** (within 5 minutes) — daemon is healthy, no alert
- **If stale or missing** — daemon is down, watchdog sends a Telegram alert
- **Alert cap** — after 5 alerts, watchdog goes silent (to prevent spam)
- **Recovery** — when daemon comes back online, watchdog resets and starts fresh

### Setup Watchdog

1. **Verify Telegram is configured** in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```

2. **Add watchdog to Task Scheduler (Windows) or cron (Linux/Mac)**

   **Windows:**
   - Follow the same steps as daemon setup
   - Name the task: `oa2-watchdog`
   - Program: `python`
   - Arguments: `C:\path\to\oa2-new\scripts\watchdog.py`
   - Trigger: "Repeat task every 5 minutes, indefinitely" (under Advanced settings)

   **Linux/Mac:**
   ```bash
   crontab -e
   # Add this line:
   */5 * * * * cd /path/to/oa2-new && python scripts/watchdog.py
   ```

3. **Optional: Configure watchdog sensitivity** (in `.env`)
   ```
   WATCHDOG_STALE_SECONDS=300      # Alert if no heartbeat for 5 min (default)
   WATCHDOG_MAX_ALERTS=5           # Max alerts before silencing (default)
   ```

### Test Watchdog

```bash
# Test one-shot (check daemon health now)
python scripts/watchdog.py

# Test loop mode (checks every 5 min, useful for debugging)
python scripts/watchdog.py --loop
```

### What You'll See

**When daemon is healthy:**
```
[OK] Daemon recovered (heartbeat age 0s)
```

**When daemon is stale:**
```
[OK] Alert 1/5 sent: daemon stale (age 330s)
```
Telegram message arrives: "oa2 daemon stale (no update for 330s). Check: tail -f logs/daemon.log"

**When max alerts reached:**
```
[WARN] Daemon stale (age 600s) — max alerts (5) reached, silencing
```
No more alerts until daemon recovers (heartbeat becomes fresh).

### Watchdog State

Watchdog maintains state in `logs/watchdog_state.json`:
- Tracks how many alerts sent in current outage
- Tracks when the last alert was sent
- Resets when daemon recovers

You can delete this file anytime to reset the alert counter.

---

## Troubleshooting

### Daemon not starting at boot

**Windows:**
- Verify task exists: Open Task Scheduler → Search for "oa2-market-monitor"
- Check trigger: Right-click task → Properties → Trigger tab
- Verify action: Right-click task → Properties → Action tab
- Run test: Right-click task → Run

**Linux/Mac:**
- Verify crontab entry: `crontab -l | grep market_monitor`
- Manual test: `nohup python scripts/market_monitor.py > logs/daemon.log 2>&1 &`
- Check logs: `tail logs/daemon.log`

### Daemon crashes or hangs

**Check logs:**
```bash
tail -100 logs/daemon.log | grep ERROR
```

**Common issues:**
- `ModuleNotFoundError` — Missing dependencies (install with `pip install -r requirements.txt`)
- `moomoo connection failed` — OpenD not running (for broker API)
- `Cannot open log file` — Permission denied on logs/ folder

**Manual restart:**

Windows:
```powershell
taskkill /pid <PID> /f
python scripts/market_monitor.py --dry-run
```

Linux/Mac:
```bash
pkill -f "python.*market_monitor.py"
python scripts/market_monitor.py --dry-run
```

### Daemon running but trades not executing

**Check:**
1. Logs show "Full-scan SUCCESS" at 9:35 AM?
2. Positions saved? `ls logs/positions_YYYY-MM-DD.json`
3. Approved trades? Review `reports/YYYY-MM-DD/premarket.md`
4. Signal weak? Check debater weights and consensus scores

**Manual test:**
```bash
python scripts/paper_trade.py --full-scan --dry-run
python scripts/report.py --premarket --date YYYY-MM-DD
```

### Reports not generating

**Check:**
1. Logs show `[PREMARKET-REPORT] SUCCESS` and `[POSTMARKET-REPORT] SUCCESS`?
2. Reports folder exists? `ls reports/YYYY-MM-DD/`
3. Python can write to reports/? Check folder permissions

**Manual test:**
```bash
python scripts/report.py --premarket --date YYYY-MM-DD
python scripts/report.py --postmarket --date YYYY-MM-DD
```

---

## Performance & System Requirements

### CPU/Memory
- **Lightweight** — Each cycle uses <100MB RAM
- **CPU:** <5% during execution
- **Idle:** <1% between runs (sleeping)

### Network
- **Minimal** — Only fetches prices at scheduled times
- **Moomoo API** — Real-time, preferred source
- **Fallback** — yfinance (free, rate-limited)

### Disk
- **Logs:** ~1-2 MB per day
- **Reports:** ~100 KB per day
- **Positions:** ~10 KB per day
- **Total:** ~5-10 GB per year of trading

---

## Advanced: Manual Control

### Start daemon in foreground (for debugging)

```bash
python scripts/market_monitor.py
# Press Ctrl+C to stop
```

### Run single cycle (test)

```bash
python scripts/market_monitor.py --once
```

### Dry-run (no file writes)

```bash
python scripts/market_monitor.py --dry-run
```

### Check daemon status

```bash
# Linux/Mac
ps aux | grep market_monitor

# Windows
tasklist | findstr python
```

### Stop daemon

```bash
# Linux/Mac
pkill -f "python.*market_monitor.py"

# Windows
taskkill /im python.exe
# or specifically:
taskkill /pid <PID> /f
```

---

## What Gets Logged

### Daily Log Files Generated

**Trading:**
- `logs/paper_trade_YYYY-MM-DD.jsonl` — full scan results (consensus, sizing, rejections)
- `logs/summary_YYYY-MM-DD.json` — daily summary (counts, errors)
- `logs/positions_YYYY-MM-DD.json` — open positions at end of scan
- `logs/exit_alerts_YYYY-MM-DD.jsonl` — all exit events during the day

**Reports:**
- `reports/YYYY-MM-DD/premarket.md` — morning plan
- `reports/YYYY-MM-DD/postmarket.md` — evening analysis
- `reports/YYYY-MM-DD/trades/*.md` — per-trade strategy docs

**Daemon:**
- `logs/daemon.log` — daemon status, errors, execution times

---

## FAQ

**Q: Can I stop the daemon and start it manually?**
A: Yes. Stop the daemon, then run `python scripts/market_monitor.py` or `python scripts/market_monitor.py --once` for a test cycle.

**Q: What if my computer crashes?**
A: The daemon will restart automatically when your computer reboots (it's scheduled to start at system boot).

**Q: Can I change the schedule times?**
A: Yes. Edit `scripts/market_monitor.py` and modify the hour/minute checks in `_schedule_loop()`. Then restart the daemon.

**Q: Does the daemon work on WSL (Windows Subsystem for Linux)?**
A: Yes. Use the Linux cron instructions in WSL's crontab.

**Q: Can I run multiple daemons on different machines?**
A: Yes, but they'll step on each other's positions file. Add hostname to paths (e.g., `positions_HOSTNAME_YYYY-MM-DD.json`) to isolate them.

**Q: What if moomoo API is down?**
A: The daemon automatically falls back to yfinance. Trades will still execute, just with slightly delayed prices.

---

## Monitoring in Production

### Daily health checks

1. **Check daemon is running:**
   ```bash
   ps aux | grep market_monitor  # Linux/Mac
   tasklist | findstr python     # Windows
   ```

2. **Review premarket report at 8:30 AM:**
   - Open Obsidian vault
   - View `reports/YYYY-MM-DD/premarket.md`
   - Confirm system plan matches market conditions

3. **Review postmarket report at 4:15 PM:**
   - View `reports/YYYY-MM-DD/postmarket.md`
   - Check P&L and exit reasons
   - Note any anomalies

4. **Monitor logs weekly:**
   ```bash
   grep ERROR logs/daemon.log | tail -20
   ```

### Weekly backups

```bash
# Backup logs and reports
tar -czf backups/oa2_trading_$(date +%Y%m%d).tar.gz logs/ reports/
```

---

## Shutdown & Maintenance

### Temporary stop (will restart on reboot)

```bash
# Linux/Mac
pkill -f "python.*market_monitor.py"

# Windows
taskkill /im python.exe
```

### Permanent disable

**Windows Task Scheduler:**
- Right-click task → Disable

**Linux crontab:**
- `crontab -e` → Comment out or remove the line → Save

---

## Summary

Once daemon is running:
- **No manual intervention needed** — everything runs on schedule
- **Full audit trail** — every trade logged with reasoning
- **Automatic reports** — premarket and postmarket insights
- **Error resilience** — crashes detected and logged
- **Continuous monitoring** — positions checked every minute

Your trading system is now **fully automated**.
