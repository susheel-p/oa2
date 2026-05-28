# tradingbot Documentation Index

All documentation for the tradingbot system. Start here.

---

## New Here? Start With These

1. [SYSTEM_GUIDE.md](../SYSTEM_GUIDE.md) — Plain-English overview: what this does, how it works, how to run it
2. [README.md](../README.md) — Technical project overview and layout
3. [DAEMON.md](DAEMON.md) — How to set up and run the automated daemon
4. [ARCHITECTURE.md](ARCHITECTURE.md) — 9-layer system design

---

## Operations

### [DAEMON.md](DAEMON.md)
Running the market monitor daemon and health monitoring.
- Daily schedule: full-scan → exit-only → reports
- Setup for Windows, Linux, Mac
- Heartbeat watchdog and Telegram alerts
- Troubleshooting

### [DEPLOY.md](DEPLOY.md)
`deploy.ps1` reference — build Docker image, deploy container, validate logs, run scan.
- All parameters and flags
- Common workflows
- Troubleshooting

### [PAPER_TRADING.md](PAPER_TRADING.md)
2-week validation plan before going live.
- Week 1 shadow mode checklist
- Week 2 paper trading checklist
- Decision criteria and hard stops

### [../REPORTS.md](../REPORTS.md)
Trading report system — premarket, postmarket, daily insights, Obsidian integration.

### [../DOCKER.md](../DOCKER.md)
Docker deployment guide (volumes, schedule, backups).

---

## Architecture & Design

### [ARCHITECTURE.md](ARCHITECTURE.md)
9-layer system design: debaters → regime → consensus → sizing → execution.
- Component breakdown and data flow
- Design patterns and conventions

### [ROADMAP.md](ROADMAP.md)
Complete production roadmap — Phases 0–F with gate criteria and current status.

### [STRUCTURE_PICKER_PLAN.md](STRUCTURE_PICKER_PLAN.md)
Options structure selection design (Greeks-aware, expiry-aware selection from chain data).

---

## Learning & Signal Quality

### [RAG.md](RAG.md)
RAG learning system — knowledge base, conviction multipliers, Thompson posteriors, daily loop.
- Architecture and data flow
- Setup and daily workflow
- Decision log format and diagnostics

### [BACKTEST_LEARNINGS.md](BACKTEST_LEARNINGS.md)
Key insights from the 12-month backtest.
- Debater performance by regime
- Ticker quality, p_bull distribution, Kelly sizing impact

### [CRITICAL_ANALYSIS.md](CRITICAL_ANALYSIS.md)
Deep analysis of GLS weights, Brier score, Kelly sensitivity, sentiment redesign rationale.

### [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md)
Phased signal quality improvement plan (P1–P4).
- P1: Directional debater (RSI/MACD/ATR + session weighting)
- P2: Sentiment rewrite
- P3: Calibrator refit
- P4: Income + volatility (deferred)

### [AI_ANALYST.md](AI_ANALYST.md)
Local AI Q&A over trading logs — ask anything about past trades and signals.

---

## Incident Records

### [INCIDENTS.md](INCIDENTS.md)
Post-mortems and implementation notes (newest first).
- May 28: DTE wiring fix
- May 27: Exit engine configuration
- May 27: Scan hang fix
- May 22: Structure picker guard gate
- May 22: Intelligent expiry selection
- May 22: Daemon recovery (May 21 incident)

---

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/market_monitor.py` | Daemon scheduler (scan, exit, reports) |
| `scripts/paper_trade.py` | Full-scan and exit-only executor |
| `scripts/watchdog.py` | Daemon health monitor + Telegram alerts |
| `scripts/backtest.py` | Historical backtesting harness |
| `scripts/daily_learn.py` | Daily outcome feedback and KB update |
| `scripts/rag_status.py` | Knowledge base health check |
| `scripts/rag_impact.py` | Measure RAG effect on recent trades |
| `scripts/smoke_test.py` | Quick system health validation |
| `deploy.ps1` | One-command build → deploy → validate |

## Core Modules

| Module | Purpose |
|--------|---------|
| `tradingbot/graph/pipeline.py` | Main orchestration (9 layers) |
| `tradingbot/debaters/` | 6 voting agents |
| `tradingbot/consensus/` | GLS aggregator + EWMA correlation |
| `tradingbot/sizing/` | Kelly + Greeks caps + CVaR stress |
| `tradingbot/execution/` | Exit engine + monitor + roll logic |
| `tradingbot/regime/` | 8-bucket regime classifier |
| `tradingbot/dataflows/` | Market data, flow adapters, moomoo |
| `tradingbot/learning/` | Knowledge base + RAG context |

---

## Status

All Phases A–F complete. System in paper trading validation.

| Milestone | Status |
|-----------|--------|
| Phases 0–5 (scaffold + core debaters) | Done |
| Phases A–F (production readiness) | Done |
| 381 tests passing | Done |
| 12-month backtest validated | Done |
| Docker daemon deployed | Done |
| Scan hang fix (May 27) | Done |
| DTE wiring fix (May 28) | Done |
| Signal quality improvement (P1–P3) | Planned |
| Live trading cutover | Pending validation |

*Last updated: May 28, 2026*
