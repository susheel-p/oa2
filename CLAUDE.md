# oa2 — Claude Code context

## What this project is

Greenfield v2 of `OptionsAgents` (sibling dir `../OptionsAgents/`). v1 is the
production paper-trading system; v2 is being built fresh to fix structural
weaknesses without dual-code-path refactoring.

## Phase 0 status

Scaffolding complete: directory tree, data adapters ported from v1, 22-ticker
watchlist, plain Python pipeline skeleton. No decisions are made yet —
`pipeline.run()` raises `NotImplementedError` until Phase 1 ports the debaters.

## Core decisions (locked, don't relitigate)

- Package name: `oa2`
- Pipeline: plain Python functions (no LangGraph)
- Broker: moomoo only (Alpaca deferred)
- Universe: 22 fixed tickers (see `oa2/watchlist/builder.py`)
- Dealer signal: only SPY/QQQ/IWM
- No Fisher agent, no single-stock catalyst scanning
- Mega-cap earnings blackout: 2 trading days, hard rule
- Same-repo .env on dev machine, but oa2 has its own .env in repo root

## Phase roadmap

| Phase | Goal |
|---|---|
| 0 ✅ | Scaffold + data adapters + 22-ticker watchlist |
| 1 | Port 5 debaters (directional/income/volatility/flow/sentiment) with new base class |
| 2 | Regime classifier — 8 buckets (vol × trend) |
| 3 | Consensus engine v1 — GLS aggregator + EWMA covariance |
| 4 | Regime-indexed Thompson bandit |
| 5 | Dealer Positioning context agent + 6th debater |
| 6 | A/B harness vs v1 on same scans |
| 7 | Paper-trade cutover when v2 ≥ v1 |
| 8 | Event Risk agent + sizing engine |
| 9 | (Later) Kafka / microservices topology |

## Working conventions

- **Port files from v1 unchanged when possible.** Don't rewrite for style.
- **No emojis in code or commits.**
- **Pydantic v2 schemas** with `ConfigDict(extra='forbid')`.
- **Async I/O, sync logic.** Don't make pure-compute functions async.
- **Feature flags in `oa2/core/feature_flags.py`** for any new component shipped in shadow mode.
- **No new directories.** Every future agent slots into one of the 9 existing layers.

## Where to find things

| Question | File |
|---|---|
| What tickers do we trade? | `oa2/watchlist/builder.py` |
| What's the entry point? | `oa2/graph/pipeline.py` |
| All schemas? | `oa2/core/schemas.py` |
| Env / paths / OA2_HOME? | `oa2/core/config.py` |
| Smoke test? | `scripts/smoke_test.py` |
| Architecture? | `ARCHITECTURE.md` |