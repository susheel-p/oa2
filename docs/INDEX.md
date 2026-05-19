# oa2 Documentation Index

Complete reference for oa2 trading system architecture, operations, and development.

## Getting Started

**New to oa2?** Start here:
1. Read [../README.md](../README.md) — What oa2 is and how to run it
2. Read [DAEMON.md](DAEMON.md) — How to set up and run the automated daemon
3. Read [ARCHITECTURE.md](ARCHITECTURE.md) — System design and components

## Core Documentation

### [DAEMON.md](DAEMON.md)
Complete guide to running the market monitor daemon and setting up Telegram health monitoring via the watchdog system.
- Daemon daily schedule (premarket → full-scan → exit-only → postmarket)
- Setup instructions for Windows Task Scheduler and Linux/Mac cron
- Daemon health watchdog (heartbeat monitoring + alerts)
- Troubleshooting common issues
- Performance & system requirements

### [ARCHITECTURE.md](ARCHITECTURE.md)
Design overview of the 9-layer system architecture.
- Component breakdown (debaters, consensus, sizing, exit engine)
- Data flow diagram
- Layer responsibilities and dependencies
- Design patterns and conventions

### [ROADMAP.md](ROADMAP.md)
Complete production roadmap with all phases and gates.
- Phases 0–5: Infrastructure and core debaters
- Phases A–F: Production readiness features
- Gate criteria and completion status
- Next phase requirements

## Analysis & Learning

### [BACKTEST_LEARNINGS.md](BACKTEST_LEARNINGS.md)
Key insights from 12-month backtest run and signal quality analysis.
- Debater performance by regime
- Directional vs income trade-offs
- Vol signal reliability
- Impact of quality gates and Kelly sizing

### [CRITICAL_ANALYSIS.md](CRITICAL_ANALYSIS.md)
Deep analysis of system gaps and opportunities identified before Phase A.
- Signal integrity issues
- Correlation dependencies
- Sizing constraints
- Production readiness gaps

### [RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md)
Knowledge base and RAG learning system design.
- Learning loop architecture (shadow trades → feedback → KB update)
- Knowledge base schema
- Integration points in pipeline
- Daily learner feedback workflow

## Implementation Plans

### [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md)
Detailed breakdown of planned improvements and their impact.
- Signal quality enhancements
- Sizing refinements
- Risk management upgrades
- Expected performance improvements

### [EXECUTION_SUMMARY.md](EXECUTION_SUMMARY.md)
Summary of major implementation milestones and execution status.
- Completed phases
- Key features delivered
- Test coverage
- Integration status

### [STRUCTURE_PICKER_PLAN.md](STRUCTURE_PICKER_PLAN.md)
Options structure selection and optimization strategy.
- Structure scoring framework
- Picker logic and constraints
- Greeks-aware selection
- Expiration and liquidity considerations

## Key Resources

### Configuration Files
- [../CLAUDE.md](../CLAUDE.md) — Claude Code working context (phases, conventions, file locations)
- [../.env.example](../.env.example) — Environment variables template
- [../README.md](../README.md) — Main project README

### Scripts
- `scripts/market_monitor.py` — Automated daemon
- `scripts/watchdog.py` — Daemon health monitor
- `scripts/paper_trade.py` — Full-scan and exit-only executor
- `scripts/report.py` — Premarket and postmarket report generation
- `scripts/backtest.py` — Historical backtesting
- `scripts/bandit_warmstart.py` — Bandit prior initialization

### Core Modules
- `oa2/graph/pipeline.py` — Main orchestration entry point
- `oa2/debaters/` — All 6 voting agents
- `oa2/consensus/` — GLS aggregator + EWMA correlation
- `oa2/sizing/` — Kelly engine + Greeks caps + CVaR
- `oa2/execution/` — Exit engine + position monitor + roll logic
- `oa2/dealer/` — GEX + walls + max pain computation

## Status Summary

**Current Phase:** Unsupervised paper trading validation (2-week shadow run)

**Completion:**
- ✅ Phases 0–5 (scaffold + core debaters)
- ✅ Phases A–F (production readiness)
- ✅ 381 tests passing
- ✅ 12-month backtest validated (+249.5% return)

**Ready for:**
- Supervised live trading
- Unsupervised validation run
- Production deployment

See [ROADMAP.md](ROADMAP.md) for detailed gate criteria.

---

*Last updated: May 19, 2026*
