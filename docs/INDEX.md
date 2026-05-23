# tradingbot Documentation Index

Complete reference for tradingbot architecture, operations, and development.

## Getting Started

New to tradingbot? Start here:
1. [../README.md](../README.md) — What tradingbot is and how to run it
2. [DAEMON.md](DAEMON.md) — Set up and run the automated daemon
3. [ARCHITECTURE.md](ARCHITECTURE.md) — System design and 9-layer overview
4. [../DOCKER.md](../DOCKER.md) — Docker deployment and volume setup

---

## Operations

### [DAEMON.md](DAEMON.md)
Running the market monitor daemon and Telegram health monitoring.
- Daily schedule: premarket → full-scan → exit-only → postmarket
- Setup for Windows / Linux / Mac
- Heartbeat watchdog and Telegram alerts
- Troubleshooting and system requirements

### [../DOCKER.md](../DOCKER.md)
Docker deployment guide (volumes, schedule, backups, troubleshooting).

### [DEPLOY.md](DEPLOY.md)
`deploy.ps1` reference — build image, deploy container, validate logs, run scan, generate report.
- All parameters and flags
- Step-by-step workflow
- Colour-coded output guide
- Common workflows and troubleshooting

### [../REPORTS.md](../REPORTS.md)
Trading report system — premarket, postmarket, daily insights, Obsidian integration.

---

## Architecture & Design

### [ARCHITECTURE.md](ARCHITECTURE.md)
9-layer system design: debaters → regime → consensus → sizing → execution.
- Component breakdown and data flow
- Layer responsibilities and dependencies
- Design patterns and conventions

### [ROADMAP.md](ROADMAP.md)
Complete production roadmap — Phases 0–F with gate criteria and completion status.

### [STRUCTURE_PICKER_PLAN.md](STRUCTURE_PICKER_PLAN.md)
Options structure selection module design (not yet implemented).
- Structure scoring framework, Greeks-aware selection
- Expiration and liquidity considerations

### [RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md)
Daily learning loop and knowledge base design (not yet implemented).
- Shadow trades → outcome feedback → KB update
- Integration points in pipeline

---

## Analysis & Signal Quality

### [BACKTEST_LEARNINGS.md](BACKTEST_LEARNINGS.md)
Key insights from the 12-month backtest.
- Debater performance by regime
- Ticker quality spread, p_bull bimodal distribution
- Impact of quality gates and Kelly sizing

### [CRITICAL_ANALYSIS.md](CRITICAL_ANALYSIS.md)
Deep analysis of GLS weights, Brier score, Kelly sensitivity, and sentiment redesign rationale.

### [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md)
Phased signal quality improvement plan (P1–P4) with implementation code, time estimates,
decision trees, and rollback plan.
- P1: Directional debater (RSI/MACD/ATR + session weighting)
- P2: Sentiment rewrite (IV-skew + earnings calendar)
- P3: Calibrator refit
- P4: Income + volatility debaters (deferred)

---

## Incident & Implementation Records

### [DAEMON_FIXES.md](DAEMON_FIXES.md)
May 21–22 daemon recovery: heartbeat file, Telegram alerts, market hours sleep logic.

### [PRODUCTION_ISSUES_ANALYSIS.md](PRODUCTION_ISSUES_ANALYSIS.md)
May 22 critical bug post-mortem: `structure_pick` guard gate, `max_loss=0.0` spurious exits,
validation fixes and deploy verification.

### [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
Intelligent expiry selection feature (3-phase) — completed May 22.
Multi-expiration chain fetching, flow-driven `_recommend_expiry()`, pipeline wiring.

---

## Root Files (not in docs/)

| File | Purpose |
|---|---|
| [../README.md](../README.md) | Project overview, quickstart, layout |
| [../CLAUDE.md](../CLAUDE.md) | Claude Code working context — phases, conventions, file map |
| [../DOCKER.md](../DOCKER.md) | Docker ops guide |
| [../REPORTS.md](../REPORTS.md) | Report system documentation |
| [../.env.example](../.env.example) | Environment variables template |

---

## Key Scripts

| Script | Purpose |
|---|---|
| `scripts/market_monitor.py` | Daemon scheduler (scan, exit, reports) |
| `scripts/paper_trade.py` | Full-scan and exit-only executor |
| `scripts/report.py` | Premarket / postmarket report generator |
| `scripts/watchdog.py` | Daemon health monitor + Telegram alerts |
| `scripts/backtest.py` | Historical backtesting harness |
| `scripts/bandit_warmstart.py` | Bandit prior initialization from replay |
| `scripts/daily_learn.py` | Daily outcome feedback and learning loop |
| `deploy.ps1` | One-command build → deploy → validate → report |

## Core Modules

| Module | Purpose |
|---|---|
| `tradingbot/graph/pipeline.py` | Main orchestration entry point |
| `tradingbot/debaters/` | 6 voting agents (directional/income/vol/flow/sentiment/dealer) |
| `tradingbot/consensus/` | GLS aggregator + EWMA correlation matrix |
| `tradingbot/sizing/` | Kelly engine + Greeks caps + CVaR stress |
| `tradingbot/execution/` | Exit engine + position monitor + roll logic |
| `tradingbot/regime/` | 8-bucket regime classifier + session overlay |
| `tradingbot/dataflows/` | Market data, flow adapters, moomoo integration |

---

## Status

**All Phases A–F complete.** System in unsupervised paper trading validation.

| Milestone | Status |
|---|---|
| Phases 0–5 (scaffold + core debaters) | ✅ |
| Phases A–F (production readiness) | ✅ |
| 381 tests passing | ✅ |
| 12-month backtest validated | ✅ |
| Docker daemon deployed | ✅ |
| Spurious exit bug fixed (May 22) | ✅ |
| Intelligent expiry selection (May 22) | ✅ |
| Signal quality improvement (P1–P3) | Planned |

*Last updated: May 22, 2026*
