# oa2 — OptionsAgents v2

Adaptive, regime-aware ensemble trading system for options on a 22-name universe
(indices, macro ETFs, sector ETFs, mega-caps). Greenfield rewrite of v1 to fix
structural weaknesses with a clean 9-layer architecture.

## What's different from v1

- **Universe trimmed** to 22 names: 4 indices + 4 macro ETFs + 6 sector ETFs + 8 mega-caps.
- **Dealer Positioning Agent** computes GEX / gamma flip / call wall / put wall / max pain for SPY/QQQ/IWM.
- **Correlation-aware consensus engine** (GLS / Mahalanobis) replaces static weighted sum.
- **Regime-indexed Thompson bandit** learns per-(debater, regime) reliability; warm-started from 6-month history replay.
- **EWMA live correlations** replace hardcoded GLS weights as opinion log accumulates.
- **Honest abstention** — flow debater returns conviction=0.0 when no real tape data available.
- **Full sizing engine** — fractional Kelly + book-level Greeks hard caps + CVaR 5-scenario stress check.
- **Exit engine** — position monitor, profit/stop/DTE/EOD/regime-flip rules, roll logic.
- **Plain Python pipeline** — no LangGraph; simpler and easier to debug at this scale.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the 9-layer design.
See [ROADMAP.md](ROADMAP.md) for the full production plan and gate status.

## Status

**All phases A–F complete. 381 tests passing.**
System is ready for supervised paper trading. Unsupervised cutover pending 2-week shadow validation.

### Phase completion

| Phase | Description | Status |
|---|---|---|
| 0 | Scaffold + 22-ticker watchlist + data adapters | ✅ |
| 1 | 5 debaters (directional / income / volatility / flow / sentiment) | ✅ |
| 2 | Regime classifier (8 buckets vol × trend) | ✅ |
| 3 | Consensus engine v1 (GLS aggregation + EWMA covariance) | ✅ |
| 4 | Regime-indexed Thompson bandit | ✅ |
| 5 | Dealer positioning agent + 6th debater (GEX + walls + max pain) | ✅ |
| A | Signal integrity: honest flow + live EWMA correlation + bandit warm-start | ✅ |
| B | Sizing engine: fractional Kelly + book Greeks limits + CVaR | ✅ |
| C | Exit engine: position monitor + rules + roll logic | ✅ |
| D | Regime enhancement: session overlay + leading crisis + cross-asset + GEX walls | ✅ |
| E | Real flow data: pluggable adapter registry (5 sources) | ✅ |
| F | Backtesting harness + A/B vs v1 baseline | ✅ |

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env  # fill in credentials
python scripts/smoke_test.py
# 5/5 passed
```

Run with all flags enabled:
```bash
export OA2_FLAG_DEBATERS=1
export OA2_FLAG_REGIME=1
export OA2_FLAG_CONSENSUS=1
export OA2_FLAG_DEALER=1
export OA2_FLAG_BANDIT=1
python -c "from oa2.graph.pipeline import run; ctx = run('SPY'); print(ctx.decision)"
```

Warm-start bandit from history (run once before paper trading):
```bash
python scripts/bandit_warmstart.py --months 6
```

Run historical backtest:
```bash
python scripts/backtest.py --months 6 --tickers SPY QQQ IWM
```

Run tests:
```bash
pytest tests/ -v
# 381 passed
```

## Layout

```
oa2/
  core/           schemas, config, feature flags
  dataflows/      market data + sentiment adapters + flow adapter registry
  watchlist/      22-ticker universe + dealer signal set + earnings blackout
  regime/         regime classifier (8-bucket vol × trend) + session overlay
  context_agents/ dealer, macro, event_risk, exec_quality
  debaters/       directional, income, volatility, flow, sentiment, dealer
  consensus/      GLS aggregator + EWMA covariance + isotonic calibration
  performance/    Thompson bandit + debater logger
  dealer/         GEX computation + gamma flip / call wall / put wall / max pain
  sizing/         kelly.py (B1/B4) + limits.py (B2) + cvar.py (B3)
  portfolio/      book-level Greeks + position limits
  execution/      exit engine + roll logic + position monitor + moomoo executor
  graph/          pipeline.py — plain Python orchestration
scripts/
  smoke_test.py           Phase 0 validation (5 checks)
  bandit_warmstart.py     populate bandit posteriors from 6-month history
  backtest.py             historical replay + A/B vs v1 baseline
```

## Key references

| Question | File |
|---|---|
| Architecture + design decisions | `ARCHITECTURE.md` |
| Full production roadmap + gates | `ROADMAP.md` |
| Claude/agent working context | `CLAUDE.md` |
| All Pydantic schemas | `oa2/core/schemas.py` |
| Feature flags | `oa2/core/feature_flags.py` |
| Pipeline entry point | `oa2/graph/pipeline.py` |
| EWMA correlation tracker | `oa2/consensus/covariance.py` |
| Kelly sizing engine | `oa2/sizing/kelly.py` |
| Greek hard caps | `oa2/sizing/limits.py` |
| CVaR stress check | `oa2/sizing/cvar.py` |
| Exit engine | `oa2/execution/exit.py` |
| Flow adapter registry | `oa2/dataflows/flow_adapter.py` |
