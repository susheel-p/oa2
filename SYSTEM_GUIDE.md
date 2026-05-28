# tradingbot — System Guide

**What this is, how it works, and how to run it.** Written so anyone can understand.

---

## What Does This System Do?

Every morning at 9:35 AM, this system wakes up, looks at 22 stocks and ETFs, and decides whether to place options trades on any of them. During the day, it watches all open positions and closes them when they hit profit targets or stop losses. At the end of the day, it learns from what worked and what didn't.

It runs **unattended** — you set it up once and it runs on its own. You get Telegram alerts if something goes wrong.

---

## The 22 Stocks It Watches

The system always trades the same 22 names. No others.

| Category | Tickers |
|----------|---------|
| Index ETFs | SPY, QQQ, IWM, DIA |
| Macro ETFs | TLT, GLD, SLV, USO |
| Sector ETFs | XLK, XLE, XLF, XLV, XLY, XLI |
| Mega-caps | AAPL, MSFT, AMZN, GOOGL, NVDA, TSLA, AMD, META |

It never trades the day before or after a mega-cap earnings report.

---

## How a Trade Gets Approved

Before any options trade is placed, it goes through 9 checks. Think of it as 9 doors the trade must pass through. If it fails any door, it's rejected.

```
Market data arrives
      │
      ▼
[Door 1] Market Regime — What kind of market is this right now?
         (calm/volatile? trending/sideways? crisis?)
      │
      ▼
[Door 2] 6 Debaters — What does each signal source say?
         • Directional: RSI, MACD, trend analysis
         • Income: is IV high enough to sell premium?
         • Volatility: is vol expanding or contracting?
         • Flow: what are big players doing with options?
         • Sentiment: what do news and social media say?
         • Dealer: where are the gamma walls and max pain?
      │
      ▼
[Door 3] Consensus — Do enough debaters agree?
         Combines all 6 votes using a math formula (GLS).
         Produces p_bull = probability the trade wins.
         If p_bull is too close to 50/50, rejected here.
      │
      ▼
[Door 4] Structure Picker — What options structure to use?
         Looks at real option chains to find a spread with
         good risk/reward. If no liquid spread exists, rejected.
      │
      ▼
[Door 5] Kelly Sizing — How many contracts?
         Uses Kelly formula to size the position.
         Smaller if: DTE is short, regime is uncertain,
                     we haven't seen enough trades yet.
      │
      ▼
[Door 6] Greeks Limits — Does this break the book?
         Checks the total portfolio delta, vega, theta.
         If adding this trade would make the book too risky, rejected.
      │
      ▼
[Door 7] CVaR Stress Test — What if the market gaps?
         Simulates 5 bad scenarios (e.g. stock drops 5%, VIX spikes).
         If any scenario blows past the loss limit, rejected.
      │
      ▼
[Door 8] Learning Check — Is this ticker blacklisted?
         Checks past performance. If this ticker has lost
         money consistently (< 43% hit rate), blocked.
      │
      ▼
[Door 9] Broker Submission — Place the order
         Sends legs to moomoo via OpenD.
         Monitors for fills.
```

On a typical day, 22 tickers are scanned and 0-5 trades are approved.

---

## How a Trade Gets Closed

The system checks every open position every few minutes and closes it if any of these happen:

| Rule | What It Does |
|------|-------------|
| **Profit target** | If the trade is up 50% of max possible profit → close it. Don't get greedy. |
| **Stop loss** | If the trade has lost the maximum amount we were willing to lose → close it now. |
| **DTE emergency** | If the option expires in fewer than 5 days → close it. Expiring options get dangerous. |
| **Time stop** | If the trade has been open too long with no resolution → close it. |
| **Hard EOD** | At 3:55 PM every day → close any intraday positions. Never hold through the close. |
| **Regime flip** | If the market regime changes AND the trade direction now conflicts → close it. |

---

## What the System Learns

After market close, the system compares what it predicted with what actually happened. It tracks:
- Which tickers it gets right most often
- Which market regimes it performs well in
- Which debaters (signals) are most accurate

This is stored in a "knowledge base" (`~/.tradingbot/knowledge_base.json`). The next day, trades on tickers with strong track records get a confidence boost; tickers with poor records get reduced conviction or are blocked entirely.

---

## The Daily Schedule

| Time | What Happens |
|------|-------------|
| 9:35 AM | Full scan: runs all 22 tickers, places approved trades |
| Every 5 min | Exit check: reviews all open positions for close signals |
| 3:55 PM | Hard EOD: closes any remaining intraday positions |
| 4:15 PM | Postmarket report: summary of the day sent via Telegram |
| After 4:30 PM | Learning update: outcomes measured, knowledge base refreshed |

On weekends and holidays the system sleeps until the next market open.

---

## How to Start It

### Prerequisites

1. **OpenD** (moomoo's local broker server) must be running. Download from moomoo website.
2. **`.env` file** must have your moomoo credentials and Telegram bot token.
3. **Knowledge base** should be seeded from backtest.

```bash
# Seed the knowledge base (one-time, ~10 minutes)
python scripts/backtest.py --months 12 --bandit

# Check everything is ready
python scripts/smoke_test.py
```

### Run in shadow mode (signals only, no real orders)

```bash
python scripts/paper_trade.py --shadow
```

### Run in paper trading mode (real orders, fake money)

```bash
python scripts/paper_trade.py --full-scan
```

### Run the full daemon (automated, runs itself)

```bash
python scripts/market_monitor.py
```

### Run on Docker (recommended for production)

```bash
docker-compose up -d
```

---

## How to Check If It's Working

### Quick health check

```bash
python scripts/smoke_test.py
```

Should print 5 green checkmarks. If any fail, the daemon should not run.

### Check the knowledge base

```bash
python scripts/rag_status.py
```

Shows: last update time, how many tickers tracked, Thompson posteriors loaded.

### Watch today's signals live

```bash
tail -f logs/paper_trade_$(date +%Y-%m-%d).jsonl | python -m json.tool
```

### Check open positions

```bash
python scripts/get_positions.py
```

### Check the daemon heartbeat

```bash
cat logs/daemon_heartbeat.txt
```

The timestamp should be within the last 60 seconds during market hours.

---

## What the Telegram Alerts Mean

| Alert | What It Means |
|-------|--------------|
| "Daemon stale (no heartbeat for XXs)" | The daemon stopped responding. Check logs and restart. |
| "FULL-SCAN timeout" | The morning scan took too long (>30 min). Check if OpenD is running. |
| "FULL-SCAN failed" | The scan crashed. Check `logs/daemon.log` for the error. |
| "Open positions: N" | Normal daily report of what's currently open. |
| "Exit alert: [TICKER] stop loss" | A position hit its stop loss and was closed. |
| "Exit alert: [TICKER] profit target" | A position hit 50% profit and was closed. Good news. |

---

## What to Do When Things Go Wrong

### Daemon not running / no alerts received

```bash
# Check if process is running
ps aux | grep market_monitor

# Check logs for errors
tail -50 logs/daemon.log

# Restart
python scripts/market_monitor.py
```

### OpenD connection failed / scan hung

```bash
# Check if OpenD is reachable
python -c "import socket; s=socket.socket(); s.settimeout(3); print(s.connect_ex(('127.0.0.1', 11111)))"
# Should print 0 (connected)

# If not, restart OpenD (the moomoo desktop app)
```

### Knowledge base empty or stale

```bash
# Regenerate from 12-month backtest
python scripts/backtest.py --months 12 --bandit

# Then update with recent outcomes
python scripts/daily_learn.py
```

### No trades being approved

This is normal if the market is neutral (p_bull near 50%). But if it's happening every day:

```bash
# Run the recalibrate command
python scripts/fit_calibrator.py

# Check which gate is rejecting trades
tail -20 logs/paper_trade_$(date +%Y-%m-%d).jsonl | python -m json.tool | grep reject_gate
```

### Phantom positions in the monitor

If positions appear with $0 P&L and empty legs, they were never actually filled. Clean them:

```bash
# Delete the stale position file
rm logs/positions_$(date +%Y-%m-%d).json

# Restart exit monitoring
python scripts/paper_trade.py --exit-only
```

---

## Key Files Reference

| File | What It Does |
|------|-------------|
| `scripts/market_monitor.py` | The daemon — runs everything on schedule |
| `scripts/paper_trade.py` | Runs a single scan or exit check |
| `scripts/watchdog.py` | Health monitor — sends Telegram if daemon is stale |
| `scripts/smoke_test.py` | Quick 5-check health test |
| `scripts/rag_status.py` | Shows knowledge base status |
| `scripts/backtest.py` | Runs 12-month historical simulation |
| `tradingbot/graph/pipeline.py` | The brain — orchestrates all 9 layers |
| `.env` | Your credentials (never commit this file) |
| `logs/daemon.log` | Full daemon activity log |
| `logs/paper_trade_YYYY-MM-DD.jsonl` | Every signal and decision for the day |
| `~/.tradingbot/knowledge_base.json` | Learned performance stats |

---

## Important Limits Built Into the System

These are hard limits that cannot be overridden by any signal:

| Limit | Value | Why |
|-------|-------|-----|
| Portfolio delta | ±30% of account | Don't become a directional bet |
| Portfolio vega | ±$50 per 1% IV | Limit volatility exposure |
| Single ticker | ≤25% of vega | Don't get too concentrated |
| Stop loss | Configurable per trade | Never risk more than agreed |
| Daily EOD cutoff | 3:55 PM hard close | Never hold intraday positions overnight |
| DTE minimum entry | 14 days | Don't buy options that expire too soon |
| Mega-cap blackout | 2 days around earnings | Earnings are unpredictable |

---

## Frequently Asked Questions

**Q: Why did it approve zero trades today?**  
A: Most likely the market consensus was too close to 50/50 (no strong directional signal), or all options chains had no liquid spreads. Check `logs/paper_trade_$(date).jsonl` and look at `reject_gate` fields.

**Q: Why is it holding a position so long?**  
A: The exit rules haven't triggered yet — it hasn't hit 50% profit, hasn't hit stop loss, and DTE isn't critical. This is by design. The system exits on rules, not feelings.

**Q: Can I manually close a position?**  
A: Yes. Close it in moomoo directly. The system will detect the position is gone on the next exit check cycle and remove it from tracking.

**Q: The scan ran but nothing happened on moomoo. Why?**  
A: Most likely the scan was in shadow mode (`--shadow`), or no trades passed all 9 gates. Check the log: `tail logs/paper_trade_$(date).jsonl` and look for `"approved": false` and the `reject_gate` field.

**Q: How do I know if the system is profitable?**  
A: Check `scripts/rag_status.py` for win rates and `scripts/rag_impact.py --days 30` for a 30-day summary. For detailed backtest results, see `docs/BACKTEST_LEARNINGS.md`.

**Q: Can I add a new ticker?**  
A: The 22-ticker universe is fixed by design (see `tradingbot/watchlist/builder.py`). Adding tickers requires rebuilding the backtest and re-warming the bandit.

---

## Documentation Map

| I want to... | Read |
|-------------|------|
| Understand the system deeply | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Set up the daemon | [docs/DAEMON.md](docs/DAEMON.md) |
| Deploy with Docker | [DOCKER.md](DOCKER.md) |
| Run paper trading validation | [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md) |
| Understand the learning system | [docs/RAG.md](docs/RAG.md) |
| See all past bugs and fixes | [docs/INCIDENTS.md](docs/INCIDENTS.md) |
| See what's planned next | [docs/IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md) |
| Use the AI Q&A tool | [docs/AI_ANALYST.md](docs/AI_ANALYST.md) |
| Find any document | [docs/INDEX.md](docs/INDEX.md) |
