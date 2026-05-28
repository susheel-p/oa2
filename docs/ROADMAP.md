# tradingbot Production Roadmap

All phases complete. System is in paper trading validation.

---

## Current Status

| Phase | Goal | Status |
|-------|------|--------|
| 0 | Scaffold + data adapters + 22-ticker watchlist | Done |
| 1 | 5 debaters (directional/income/vol/flow/sentiment) | Done |
| 2 | 8-bucket regime classifier (vol × trend) | Done |
| 3 | GLS consensus engine + EWMA covariance | Done |
| 4 | Thompson bandit (regime-indexed) | Done |
| 5 | Dealer positioning agent (6th debater) | Done |
| A | Honest debaters + live correlation + bandit warm-start | Done |
| B | Kelly sizing engine + Greeks caps + CVaR stress | Done |
| C | Exit engine + position monitor + roll logic | Done |
| D | Regime enhancements (session, crisis leads, GEX walls) | Done |
| E | Real flow data (pluggable adapter registry) | Done |
| F | Backtesting harness + A/B vs v1 baseline | Done |

**Next:** 2-week paper trading validation → live cutover. See [PAPER_TRADING.md](PAPER_TRADING.md).

---

## Phase A — Signal Integrity [DONE]

**What it fixed:** Debaters were fabricating signals when data was unavailable.

- **A1 — Flow debater honest abstention** — `conviction=0.0` when no real tape data (`data_quality != "real"`). File: `tradingbot/debaters/flow.py`
- **A2 — Bandit warm-start** — 6-month yfinance replay seeds Thompson Beta posteriors before going live. File: `scripts/bandit_warmstart.py`
- **A3 — EWMA correlation** — Live λ=0.94 rolling correlation matrix in consensus engine (min 20 obs, falls back to fixed). Files: `tradingbot/consensus/covariance.py`, `tradingbot/consensus/engine.py`

---

## Phase B — Sizing Engine [DONE]

**What it does:** No trade executes without passing three independent sizing gates.

- **B1 — Fractional Kelly** — quarter-Kelly by default; DTE-aware scaling (50% at DTE 0-2, full at DTE 7-21). File: `tradingbot/sizing/kelly.py`
- **B2 — Greeks hard caps** — book-level delta/vega/theta/single-name concentration limits. File: `tradingbot/sizing/limits.py`
- **B3 — CVaR stress** — 5 scenarios (±3%, ±5% move, VIX +10, VIX +20). Rejects or reduces size if any breach. File: `tradingbot/sizing/cvar.py`

---

## Phase C — Exit Engine [DONE]

**What it does:** Every open position is monitored and closed by rule — no unattended exposure.

Exit rules (evaluated in order):
1. Stop loss: loss >= max_loss → close immediately
2. Profit target: gain >= 50% of max_profit → close (configurable via env vars)
3. DTE emergency: DTE < threshold on short leg → close to avoid assignment
4. Time stop: position held past time limit → evaluate close
5. Hard EOD: 3:55 PM ET → force close all intraday positions
6. Regime flip + direction conflict → forced close (not just review flag)

Files: `tradingbot/execution/exit.py`, `tradingbot/execution/monitor.py`, `tradingbot/execution/roll.py`

Exit thresholds are configurable via environment variables (see `.env.example`).

---

## Phase D — Regime Enhancement [DONE]

**What it added:** Better intraday context and earlier crisis detection.

- **D1 — Session overlay** — OPEN/MORNING/MIDDAY/AFTERNOON/POWER_HOUR intraday tags; debater weights adjust by session. File: `tradingbot/regime/session.py`
- **D2 — Leading crisis signal** — VIX3M/VIX < 1.05 AND VVIX > 110 triggers early CRISIS flag (not lagging VIX > 35). File: `tradingbot/regime/classifier.py`
- **D3 — Cross-asset context** — TLT, HYG, DXY as regime inputs. File: `tradingbot/regime/classifier.py`
- **D4 — GEX walls** — call wall, put wall, max pain from option chain OI. File: `tradingbot/dealer/gex.py`

---

## Phase E — Real Flow Data [DONE]

**What it added:** Pluggable adapter registry so real sweep/PCR data replaces synthetic chain-derived estimates.

Adapters: yfinance (default), moomoo, tradier, options_whale, unusual_whales.  
The latter three require API credentials — system falls back to yfinance without them.  
File: `tradingbot/dataflows/flow_adapter.py`

---

## Phase F — Backtesting Harness [DONE]

**What it added:** Validated system against 12 months of historical data before paper trading.

- **F1** — Daily OHLCV + EOD options replay via yfinance. File: `scripts/backtest.py`
- **F2** — Per-debater accuracy by regime. See `docs/BACKTEST_LEARNINGS.md` for results.
- **F3** — A/B vs v1: v2 Sharpe >= v1 on 90-day window. Flag: `TRADINGBOT_FLAG_AB_V1`
- **F4** — Paper cutover gate: parity confirmed.

---

## Post-Phase F: Signal Quality (Planned)

See `docs/IMPROVEMENT_PLAN.md` for details.

| Item | Goal | Status |
|------|------|--------|
| P1 — Directional debater | RSI/MACD with session weighting | Planned |
| P2 — Sentiment rewrite | IV-skew + earnings calendar | Planned |
| P3 — Calibrator refit | Platt scaling refresh from live outcomes | Planned |
| P4 — Income/vol debaters | Tuning for current IV environment | Deferred |

---

## May 2026 Bug Fixes

All post-launch fixes are documented in [INCIDENTS.md](INCIDENTS.md).

| Date | Fix | Commit |
|------|-----|--------|
| May 28 | DTE wiring through structure picker | `a929a7f` |
| May 27 | Exit engine env-var configuration | staged |
| May 27 | Scan hang (moomoo timeout) | `fcb02d9` |
| May 22 | Structure picker guard gate | `1388df6` |
| May 22 | Intelligent expiry selection | `0b48476` |
| May 22 | Daemon recovery + Telegram alerts | `b145aa5` |
