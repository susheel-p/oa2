# oa2 — Claude Code context

## What this project is

Greenfield v2 of `OptionsAgents` (sibling dir `../OptionsAgents/`). v1 is the
production paper-trading system; v2 is being built fresh to fix structural
weaknesses without dual-code-path refactoring.

## Current status (Phases A–F complete)

Phases 0-5 shipped: 5 debaters + dealer agent + regime classifier + GLS consensus engine
+ Thompson bandit. Pipeline runs end-to-end with all flags enabled. 381 tests passing.

Phase A complete: flow debater honest abstention, bandit warm-start script,
EWMA correlation matrix wired into consensus engine.

Phase B complete: Kelly sizing engine (kelly.py, limits.py, cvar.py).
Phase C complete: exit engine (exit.py, monitor.py, roll.py).
Phase D complete: regime enhancements (session overlay, crisis leads, cross-asset, GEX walls).
Phase E complete: flow adapter registry (yfinance/moomoo/tradier/options_whale/unusual_whales).
Phase F complete: backtesting harness + A/B comparison vs v1 baseline.

## Original phase roadmap (completed)

| Phase | Goal | Status |
|---|---|---|
| 0 | Scaffold + data adapters + 22-ticker watchlist | ✅ |
| 1 | Port 5 debaters (directional/income/volatility/flow/sentiment) | ✅ |
| 2 | Regime classifier — 8 buckets (vol × trend) | ✅ |
| 3 | Consensus engine v1 — GLS aggregator + EWMA covariance | ✅ |
| 4 | Regime-indexed Thompson bandit | ✅ |
| 5 | Dealer Positioning context agent + 6th debater | ✅ |

## Context Loading Strategy

**For any chat, load CLAUDE.md first.** Then:
1. **Know your task** — pick it from "Quick Task Lookup" below
2. **Load minimal file set** — don't load entire `oa2/` directory
3. **Check for tests** — `tests/test_*.py` validates assumptions
4. **Use `get_observations()` in mem-search** — did we solve this before?

New session without context? Load:
```
CLAUDE.md (this file)
oa2/core/schemas.py (all data contracts)
oa2/graph/pipeline.py (entry point)
scripts/smoke_test.py (health check)
```

Debugging a specific failure? Load that component + `tests/test_*.py` for that layer.

## Production roadmap (current work)

These phases must complete before paper-trade cutover. Order is risk-priority.

| Phase | Goal | Status | Gate |
|---|---|---|---|
| A | Honest debaters + live correlation + bandit warm-start | ✅ Complete | Required for signal integrity |
| B | Sizing engine (Kelly + book limits + CVaR) | ✅ Complete | Required before any live trading |
| C | Exit engine (position monitor + rules + roll logic) | ✅ Complete | Required for unattended running |
| D | Regime enhancement (session overlay + crisis leads + cross-asset) | ✅ Complete | Improves signal quality |
| E | Real flow data (sweep tape, real PCR) | ✅ Complete | Unlocks flow debater |
| F | Backtesting harness + A/B vs v1 | ✅ Complete | Gate for paper cutover |

### Phase A: Signal Integrity [COMPLETE]

**Files to load:** `oa2/debaters/flow.py`, `oa2/consensus/engine.py`, `oa2/consensus/covariance.py`, `scripts/bandit_warmstart.py`, `oa2/core/schemas.py`, `tests/test_debaters.py`, `tests/test_consensus.py`

A1 — Flow debater: abstain (conviction=0.0) when no real sweep data. Requires
`flow_data["data_quality"] == "real"`. File: `oa2/debaters/flow.py`.

A2 — Bandit warm-start: 6-month yfinance replay, scores next-day hits per
(debater, regime), saves Beta posteriors. File: `scripts/bandit_warmstart.py`.

A3 — EWMA correlation matrix: `oa2/consensus/covariance.py` (λ=0.94, min 20 obs),
wired into `oa2/consensus/engine.py` via `OA2_FLAG_EWMA_CORR` (default on).

### Phase B: Sizing Engine [COMPLETE]

**Files to load:** `oa2/sizing/kelly.py`, `oa2/sizing/limits.py`, `oa2/sizing/cvar.py`, `oa2/core/schemas.py`, `tests/test_sizing.py`, `oa2/graph/pipeline.py`

B1 — Fractional Kelly (with DTE-aware scaling): `oa2/sizing/kelly.py`
B2 — Book-level Greeks hard caps: `oa2/sizing/limits.py`
B3 — CVaR 5-scenario stress check: `oa2/sizing/cvar.py`

### Phase C: Exit Engine [COMPLETE]

**Files to load:** `oa2/execution/exit.py`, `oa2/execution/monitor.py`, `oa2/execution/roll.py`, `oa2/core/schemas.py`, `tests/test_execution.py`, `oa2/graph/pipeline.py`

C1 — Position monitor: mark-to-market vs targets on open positions.
C2 — Exit rules: 50% profit → close short; stop hit → close; DTE < 2 → close.
C3 — Hard EOD cutoff: 3:55 PM ET force-close all intraday positions.
C4 — Regime flip → reduce exposure, re-evaluate.
C5 — Roll logic: evaluate roll vs close for near-expiry profitable positions.

### Phase D: Regime Enhancement [COMPLETE]

**Files to load:** `oa2/regime/classifier.py`, `oa2/regime/session.py`, `oa2/core/schemas.py`, `oa2/graph/pipeline.py`, `tests/test_regime.py`, `oa2/dataflows/market_data.py`

D1 — Session overlay: OPEN / MORNING / MIDDAY / AFTERNOON / POWER_HOUR tags.
D2 — Early crisis signal: VIX3M/VIX ratio flattening + VVIX > 110 (leading, not lagging).
D3 — Cross-asset context: TLT, HYG, DXY as regime inputs (flight-to-safety vs risk-off).
D4 — Max pain / call-put walls: populate Setup.resistance_level / support_level from GEX data.
D5 — Additive conviction fix: group signals by data source; one vote per source group.

### Phase E: Real Flow Data [COMPLETE]

**Files to load:** `oa2/dataflows/flow_adapter.py`, `oa2/debaters/flow.py`, `oa2/core/schemas.py`, `tests/test_flow_adapter.py`, `tests/test_debaters.py`, `oa2/graph/pipeline.py`

E1 — Pluggable adapter registry: yfinance / moomoo / tradier / options_whale / unusual_whales.
E2 — FlowDebater wired to real sweeps: PCR from tape, not chain delta.
E3 — Dark pool fields populated from real prints when adapter supports it.

### Phase F: Backtesting Harness [COMPLETE]

**Files to load:** `scripts/backtest.py`, `scripts/backtest_analyzer.py`, `oa2/graph/pipeline.py`, `oa2/core/schemas.py`, `tests/test_backtest.py`, `.env` (for data paths)

F1 — Historical replay: 6-12 months of daily OHLCV + EOD options snapshots via yfinance.
F2 — Per-debater accuracy by regime: signal quality measured before live weighting.
F3 — A/B vs v1: OA2_FLAG_AB_V1 compares v2 consensus vs v1 decisions.
F4 — Paper cutover gate: v2 Sharpe >= v1 Sharpe on 90-day backtest window.

## Core decisions (locked, don't relitigate)

- Package name: `oa2`
- Pipeline: plain Python functions (no LangGraph)
- Broker: moomoo only (Alpaca deferred)
- Universe: 22 fixed tickers (see `oa2/watchlist/builder.py`)
- Dealer signal: only SPY/QQQ/IWM
- No Fisher agent, no single-stock catalyst scanning
- Mega-cap earnings blackout: 2 trading days, hard rule
- Same-repo .env on dev machine, but oa2 has its own .env in repo root

## New conventions added after gap analysis

- **Debaters must abstain honestly.** No fabricated signals. conviction=0.0 when data unavailable.
- **Sizing is a hard gate.** No trade executes without passing B1+B2+B3 checks.
- **Exit logic is mandatory.** Every approved trade gets a corresponding exit condition at approval time.
- **DTE is a first-class variable.** Every debater and sizer must account for expiration proximity.
- **Bandit warm-started.** Never go live with flat Beta(1,1) priors; replay history first.

## Working conventions

- **Port files from v1 unchanged when possible.** Don't rewrite for style.
- **No emojis in code or commits.**
- **Pydantic v2 schemas** with `ConfigDict(extra='forbid')`.
- **Async I/O, sync logic.** Don't make pure-compute functions async.
- **Feature flags in `oa2/core/feature_flags.py`** for any new component shipped in shadow mode.
- **No new directories.** Every future agent slots into one of the 9 existing layers.

## Where to find things

| Question | File | Related Context |
|---|---|---|
| What tickers do we trade? | `oa2/watchlist/builder.py` | `oa2/core/schemas.py` (Ticker schema) |
| What's the entry point? | `oa2/graph/pipeline.py` | `oa2/core/config.py` (env setup) |
| All schemas? | `oa2/core/schemas.py` | `oa2/core/feature_flags.py` |
| Env / paths / OA2_HOME? | `oa2/core/config.py` | `.env.example`, `.env` |
| Smoke test? | `scripts/smoke_test.py` | `oa2/graph/pipeline.py` |
| Architecture + gap analysis? | `docs/ARCHITECTURE.md` | `CLAUDE.md` (this file) |
| Full production roadmap? | `docs/ROADMAP.md` | `docs/INDEX.md` |
| Documentation index? | `docs/INDEX.md` | markdown files in `docs/` |
| Kelly sizing engine? | `oa2/sizing/kelly.py` | `oa2/sizing/limits.py`, `oa2/sizing/cvar.py` |
| Greek hard caps? | `oa2/sizing/limits.py` | `oa2/core/schemas.py` (Greeks) |
| CVaR stress check? | `oa2/sizing/cvar.py` | `oa2/sizing/kelly.py` |
| Exit engine? | `oa2/execution/exit.py` | `oa2/execution/monitor.py`, `oa2/execution/roll.py` |
| Roll logic? | `oa2/execution/roll.py` | `oa2/execution/exit.py` |
| Position monitor? | `oa2/execution/monitor.py` | `oa2/core/schemas.py` (Position schema) |
| Flow adapter registry? | `oa2/dataflows/flow_adapter.py` | `oa2/debaters/flow.py` |
| Bandit warm-start? | `scripts/bandit_warmstart.py` | `oa2/bandit/thompson.py`, `oa2/core/schemas.py` |
| EWMA covariance? | `oa2/consensus/covariance.py` | `oa2/consensus/engine.py` |
| Backtest harness? | `scripts/backtest.py` | `scripts/bandit_warmstart.py`, `oa2/graph/pipeline.py` |
| Daemon setup? | `docs/DAEMON.md` | `scripts/market_monitor.py`, `.env.example` |
| Daemon health watchdog? | `scripts/watchdog.py` | `docs/DAEMON.md` |

## Quick Task Lookup (Token-Optimized File Sets)

Use these file sets to minimize context loading for common tasks:

### **🔧 Fixing bugs in existing debaters**
Load: `oa2/core/schemas.py` + `oa2/debaters/{directional,income,volatility,flow,sentiment}.py` + `tests/test_debaters.py`

### **📊 Tuning sizing/exit logic**
Load: `oa2/sizing/{kelly,limits,cvar}.py` + `oa2/execution/{exit,monitor,roll}.py` + `tests/test_sizing.py` + `tests/test_execution.py`

### **🔄 Adding new flow adapter or data source**
Load: `oa2/dataflows/flow_adapter.py` + `oa2/debaters/flow.py` + `oa2/core/schemas.py` (FlowData schema)

### **⚙️ Consensus engine improvements**
Load: `oa2/consensus/engine.py` + `oa2/consensus/covariance.py` + `oa2/core/schemas.py` (Setup, Signal schemas) + `tests/test_consensus.py`

### **🎰 Bandit or regime classifier work**
Load: `oa2/bandit/thompson.py` + `oa2/regime/classifier.py` + `oa2/core/schemas.py` (RegimeState, BetaPosterior) + `scripts/bandit_warmstart.py`

### **🚀 Running backtest or analyzing results**
Load: `scripts/backtest.py` + `scripts/backtest_analyzer.py` + `tests/test_backtest.py` + `.env` (for data paths)

### **⏰ Daemon or monitoring work**
Load: `scripts/market_monitor.py` + `scripts/watchdog.py` + `docs/DAEMON.md` + `.env.example` (config keys)

### **📈 Adding new signal debater (after Phase 1)**
Load: `oa2/debaters/{any}.py` (template) + `oa2/core/schemas.py` (Signal schema) + `oa2/graph/pipeline.py` (integration point) + `oa2/core/feature_flags.py`

## File Categories by Task

**Core (always load for any change):**
- `oa2/core/schemas.py` — all Pydantic schemas
- `oa2/core/config.py` — env vars, OA2_HOME
- `.env`, `.env.example` — configuration

**Debaters (signal generation):**
- `oa2/debaters/*.py` — all 5 debaters + flow adapter
- `oa2/dataflows/flow_adapter.py` — pluggable data sources

**Consensus & Bandit:**
- `oa2/consensus/engine.py` — signal aggregation
- `oa2/consensus/covariance.py` — EWMA correlation
- `oa2/bandit/thompson.py` — regime-indexed bandit

**Regime & Entry:**
- `oa2/regime/classifier.py` — 8-bucket regime bucketing
- `oa2/watchlist/builder.py` — ticker universe

**Sizing & Risk (Phase B-C):**
- `oa2/sizing/kelly.py` — fractional Kelly
- `oa2/sizing/limits.py` — Greek hard caps
- `oa2/sizing/cvar.py` — stress testing

**Execution & Exit (Phase C-E):**
- `oa2/execution/exit.py` — exit rules
- `oa2/execution/monitor.py` — position tracking
- `oa2/execution/roll.py` — expiry roll logic

**Integration & Running:**
- `oa2/graph/pipeline.py` — end-to-end flow
- `scripts/smoke_test.py` — quick validation
- `scripts/backtest.py` — historical testing
- `scripts/market_monitor.py` — live daemon
