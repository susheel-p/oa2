# oa2 Trading Reports — Comprehensive Daily Analysis

Three daily reports keep you informed of strategy execution and decision-making:

## 1. Premarket Report (8:30 AM ET)

**When:** Before market open, every trading day  
**Data Source:** Previous trading day's scan results + current premarket prices  
**Format:** Obsidian-compatible Markdown (`reports/YYYY-MM-DD-premarket.md`)

### What's Inside

#### Summary Section
- **Tickers scanned:** Total number of tickers evaluated
- **Approved for trading:** Tickers with signals strong enough to enter
- **Rejected:** Tickers that failed sizing gates (explain why in plain English)
- **Open positions:** Any positions still held from previous days

#### Open Positions
If you have positions from yesterday still running:
- Current underlying price
- Entry price (what you paid)
- Current P&L (profit or loss so far)
- How close to profit target or stop loss

#### Per-Ticker Setup (Approved Trades Only)

For each approved trade, you get:

**Direction + Conviction:**
```
SPY — BULLISH (p_bull=61%, score=64%)
Regime: normal volatility, neutral trend
Top Debaters: flow=34%, directional=30%, sentiment=7%
```

**Scenario Analysis Table:**
| Scenario | Price | Action |
|---|---|---|
| +2% move | $XXX | Entry → Profit target watch |
| +1% move | $XXX | Entry → Profit target watch |
| -1% move | $XXX | Hold or reassess |
| -2% move | $XXX | Hold or reassess |

**The Math:**
- Kelly Fraction: 15% (position sizing)
- Max Risk: $XXX (dollar loss if stopped out)
- Profit Target: $YYY (at 50% of max profit)

#### Rejected Trades

Each rejection explains why in plain English:
```
SPY rejected at kelly gate — Negative Kelly fraction (−0.15)
  → Expected value is negative (risk not worth reward)

QQQ rejected: NEUTRAL direction — no trade
  → System saw equal bull/bear signals, won't trade
```

### How to Use the Premarket Report

1. **Review at 8:30 AM** — before market opens
2. **Check Open Positions** — any existing trades near stops or targets?
3. **Understand Approved Trades** — why is the system bullish/bearish?
4. **Study Scenarios** — if market moves +2%, what happens?
5. **Link to Trade Docs** — [[2026-05-18-premarket]] links to detailed trade strategy notes

---

## 2. Postmarket Report (4:15 PM ET)

**When:** After market close, every trading day  
**Data Source:** Entry logs, exit alerts, final position snapshots  
**Format:** Obsidian-compatible Markdown (`reports/YYYY-MM-DD-postmarket.md`)

### What's Inside

#### Day Summary
- Scanned: 22 tickers
- Approved: 3 trades
- Rejected: 19
- Exit alerts: 2 exits triggered during day

#### Trades Entered

Table of all trades entered today:
| Ticker | Direction | Structure | Entry Price | Contracts | Max Risk |
|--------|-----------|-----------|-------------|-----------|----------|
| SPY | BULLISH | CALL SPREAD | $455.50 | 5 | $2,500 |
| QQQ | BEARISH | PUT SPREAD | $425.00 | 3 | $1,500 |

#### Exit Events

What happened to today's trades:
| Time | Ticker | Reason | P&L |
|------|--------|--------|-----|
| 13:45 | SPY | profit_target | +$1,200 |
| 15:20 | QQQ | stop_loss | -$1,500 |

**Exit Reasons:**
- `profit_target` — hit 50% of max profit target
- `stop_loss` — hit max loss limit
- `dte_expiry` — too close to expiration
- `regime_flip` — market regime changed, reduce exposure
- `eod_force` — 3:55 PM force-close all intraday positions

#### Watch List — Almost Traded

Tickers with good signals but rejected for sizing:
```
IWM (BULLISH, p_bull=58%): Negative Kelly fraction
  → Signal was positive but expected value negative (bad risk/reward)

TLT (BEARISH, p_bull=62%): Greek hard cap exceeded
  → Risk was too large for book limits
```

### How to Use the Postmarket Report

1. **Review at 4:15 PM** — after market close
2. **Check Day Summary** — how many trades? how many exits?
3. **Analyze Trades** — entry prices, sizing, max risk
4. **Study Exit Reasons** — did we hit targets? stops? expiry?
5. **Review Watch List** — what signals did we miss?
6. **Link to Entry Report** — [[2026-05-18-premarket]] shows what was planned

---

## 3. Trade Strategy Documentation

**When:** Generated at market open for each approved trade  
**Data Source:** Debater votes, Kelly math, regime state  
**Format:** Obsidian note per trade (`reports/trades/YYYY-MM-DD-TICKER-ID.md`)

### Trade Doc Template

```markdown
# Trade: SPY BULLISH — 2026-05-18

## What We Did
On May 18 at 9:35 AM, the system entered a bullish position on SPY 
at $455.50 (5 contracts). Structure: Call Spread.

We entered because 5 debaters agreed: Flow was strongest (34% weight).

## Why We Did It
- Direction: BULLISH (consensus score 64%, calibrated p_bull 61%)
- Market Regime: Normal volatility, neutral trend
- Debaters that voted: Flow (34%), Directional (30%), Sentiment (7%)
- Calibration: Raw probability 81% scaled to 61% by Platt scaling

## The Math
- Kelly Fraction: 15% of bankroll
- Edge (expected return): 54%
- Max Risk: $2,500 | Profit Target: $1,250 (at 50% of max)

## Scenario Analysis
| If underlying moves... | Result |
|---|---|
| +2% to $464 | Profit target zone |
| +1% to $460 | In profit, monitor |
| -1% to $451 | Near break-even |
| -2% to $446 | Approaching stop |

## How It Resolved
[Filled in at market close]
- Closed at 13:45 at +$1,200 (profit target hit)
```

### Field Explanations

- **p_bull**: Probability of bullish outcome (0.0-1.0), calibrated by Platt scaling
- **consensus_score**: How unified are the debaters (0.0-1.0)
- **Kelly Fraction**: Optimal position size (as % of bankroll)
- **Edge**: Expected winning probability minus loss probability
- **Max Risk**: Total dollar amount at stake if trade stops out
- **Profit Target**: Dollar amount we're aiming for (usually 50% of max profit)

---

## Obsidian Vault Setup

Drop the `reports/` folder into your Obsidian vault to get:

### Auto-linking (Relative Links)
- `[[premarket]]` — jumps to that day's premarket report (in same folder)
- `[[postmarket]]` — jumps to that day's postmarket report (in same folder)
- `[[../premarket]]` — from trades/ folder, jumps back to daily premarket
- `[[SPY-001]]` — within trades/, jumps to SPY trade doc

### Organization (Daily Folders)
```
Vault Root/
  reports/
    2026-05-18/                  ← one folder per trading day
      premarket.md               ← 8:30 AM (what we plan to do)
      postmarket.md              ← 4:15 PM (what actually happened)
      trades/
        SPY-001.md               ← full strategy for this trade
        QQQ-002.md
        ...
    2026-05-17/
      premarket.md
      postmarket.md
      trades/
        SPY-001.md
        ...
```

### Useful Obsidian Searches

**Find all BULLISH trades this week:**
```
path:reports/*/trades tag:BULLISH
```

**Find all profit_target exits this month:**
```
path:reports "profit_target"
```

**Find where you hit stop losses:**
```
path:reports "stop_loss"
```

**Track a specific ticker across time:**
```
path:reports tag:SPY
```

**View all premarket reports:**
```
file:premarket
```

**View all postmarket reports:**
```
file:postmarket
```

---

## CLI Usage

### Generate Reports Manually

```bash
# Generate today's premarket (uses yesterday's scan)
python scripts/report.py --premarket

# Generate premarket for a specific date (uses that day's scan)
python scripts/report.py --premarket --date 2026-05-18

# Generate today's postmarket
python scripts/report.py --postmarket

# Generate postmarket for a specific date
python scripts/report.py --postmarket --date 2026-05-18

# Generate strategy doc for a single trade
python scripts/report.py --trade-doc TRADE_001
```

### Automatic Scheduling

Reports run automatically via `market_monitor.py`:

```bash
# Run daemon (schedules premarket @ 8:30, postmarket @ 4:15)
python scripts/market_monitor.py

# Test mode (dry-run, no file writes)
python scripts/market_monitor.py --dry-run

# One cycle only (for testing)
python scripts/market_monitor.py --once
```

**Schedule in cron or Windows Task Scheduler:**
```bash
# Every trading day at 7:30 AM:
00 07 * * 1-5 cd /path/to/oa2-new && python scripts/market_monitor.py
```

---

## Data Sources

### Prices
- **Preferred:** moomoo API (real broker data)
- **Fallback:** yfinance (public data)

### Signal Data
- Scanner runs at **9:35 AM** (full-scan mode)
- Logs saved to `logs/paper_trade_YYYY-MM-DD.jsonl`
- Positions saved to `logs/positions_YYYY-MM-DD.json`
- Exit alerts logged to `logs/exit_alerts_YYYY-MM-DD.jsonl`

### Calibration
- Platt-scaled probabilities (account for historical accuracy)
- Brier score improvement tracked in backtest logs
- Warm-started Thompson bandit (6-month yfinance replay)

---

## Understanding Rejections

### Common Rejection Reasons

**"NEUTRAL direction — no trade"**  
System saw equal bullish/bearish signals (p_bull ≈ 50%). Won't trade a coin flip.

**"Negative Kelly fraction"**  
Expected value is negative. Math says risk > reward, so we skip.

**"Greeks hard cap exceeded"**  
Position would exceed book-level Greeks limits (delta, theta, vega). Protects against outsized Greeks.

**"Monte Carlo CVaR exceeds threshold"**  
5-scenario stress test shows unacceptable downside (e.g., >10% loss in 1 scenario).

---

## Tuning Report Generation

### Environment Variables

```bash
# Override reports output directory (default: reports/)
export REPORTS_DIR=/path/to/obsidian/vault/trading

# Override base directory for log lookup (default: parent of scripts/)
export TRADINGBOT_HOME=/custom/path
```

### Customize Prices
Edit `_fetch_price()` in `scripts/report.py` to add:
- Real broker API (Alpaca, Interactive Brokers)
- Delayed quotes (10-15 min lag)
- Pre/post-market hours

---

## Examples

### Morning (8:30 AM)
1. Wake up, open Obsidian vault
2. Read today's premarket report ([[2026-05-18-premarket]])
3. Review scenario tables — know what to expect if market moves ±2%
4. Check open positions from yesterday — any near stops or targets?
5. Click through to trade strategy docs ([[2026-05-18-SPY-001]]) to understand why each signal fired

### During Trading Day (9:30 AM - 4:00 PM)
- `market_monitor.py` runs every minute
- Exit checks happen automatically
- Positions log P&L in real-time
- Exit alerts appear in logs

### Evening (4:15 PM)
1. Market closes, postmarket report auto-generated
2. Review [[2026-05-18-postmarket]] for day's results
3. See which trades hit profit targets, which hit stops
4. Track daily P&L and exit reasons
5. Plan tomorrow based on today's learnings

---

## Support

For issues or feature requests:
- Check that `market_monitor.py` is running (logs show "Market monitor started")
- Verify `reports/` folder is writable
- Confirm moomoo/yfinance can fetch prices (check logs for errors)
- See `CLAUDE.md` for project structure
