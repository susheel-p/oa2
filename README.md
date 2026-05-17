# oa2 — OptionsAgents v2

Adaptive, regime-aware ensemble trading system for options on a 22-name universe
(indices, macro ETFs, sector ETFs, mega-caps). Greenfield rewrite of v1 to fix
structural weaknesses with a 9-layer architecture.

## What's different from v1

- **Universe trimmed** to 22 names: 4 indices + 4 macro ETFs + 6 sector ETFs + 8 mega-caps.
- **Dealer Positioning Agent** computes GEX/DEX/gamma flip/walls for SPY/QQQ/IWM.
- **Correlation-aware consensus engine** (GLS / Mahalanobis) replaces static weighted sum.
- **Regime-indexed Thompson bandit** learns per-(debater, regime) reliability.
- **EWMA live correlations** replace hardcoded GLS weights as opinion log accumulates.
- **Honest abstention** — flow debater returns conviction=0 when no real tape data available.
- **Plain Python pipeline** — no LangGraph; simpler and easier to debug at this scale.
- **No Fisher agent**, no single-stock catalyst fishing, no Alpaca (deferred).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the 9-layer design and gap analysis.
See [ROADMAP.md](ROADMAP.md) for the full production plan.

## Status

**Phase A complete. Phase B (sizing engine) in progress.** 141 tests passing.
NOT yet production-ready — sizing and exit engines required before any trading.

### Completed phases

| Phase | Description | Status |
|---|---|---|
| 0 | Scaffold + 22-ticker watchlist + data adapters | ✅ |
| 1 | 5 debaters (directional/income/volatility/flow/sentiment) | ✅ |
| 2 | Regime classifier (8 buckets vol × trend) | ✅ |
| 3 | Consensus engine v1 (GLS aggregation) | ✅ |
| 4 | Regime-indexed Thompson bandit | ✅ |
| 5 | Dealer positioning agent + 6th debater (GEX) | ✅ |
| A | Signal integrity: honest flow + live EWMA correlation + bandit warm-start | ✅ |

### Production phases (current work)

| Phase | Description | Status | Blocks |
|---|---|---|---|
| B | Sizing engine: Kelly + book limits + CVaR + DTE-aware | 🔨 In progress | Paper trading |
| C | Exit engine: position monitor + rules + roll logic | Pending | Unattended run |
| D | Regime enhancement: session overlay + leading crisis + cross-asset | Pending | Signal quality |
| E | Real flow data: vendor selection + sweep tape integration | Pending | Flow debater edge |
| F | Backtesting harness + A/B vs v1 | Ongoing | Paper cutover gate |

### Production blockers remaining

1. **Sizing engine missing** (`oa2/sizing/` — Phase B in progress) — no position sizing = undefined risk
2. **Exit engine missing** — open positions have no automated exit logic

### Phase A completed (signal integrity)

- Flow debater honest abstention: requires `flow_data["data_quality"]="real"` — no more fake PCR
- EWMA covariance tracker: `oa2/consensus/covariance.py` replaces hardcoded correlations
- Bandit warm-start: `scripts/bandit_warmstart.py` — populate posteriors from 6-month history

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env  # fill in credentials
python scripts/smoke_test.py
```

Run with all flags enabled (development):
```bash
export OA2_FLAG_DEBATERS=1
export OA2_FLAG_REGIME=1
export OA2_FLAG_CONSENSUS=1
export OA2_FLAG_DEALER=1
python -c "from oa2.graph.pipeline import run; ctx = run('SPY'); print(ctx.decision)"
```

Warm-start bandit from history (run once before paper trading):
```bash
python scripts/bandit_warmstart.py --months 6
```

Run tests:
```bash
pytest tests/ -v
# 141 passed
```

## Layout

```
oa2/
  core/          schemas, config, feature flags
  dataflows/     market data + sentiment adapters (ported from v1)
  watchlist/     22-ticker universe
  regime/        regime classifier (Phase 2) + session overlay (Phase D)
  context_agents/dealer, macro, event_risk, exec_quality (Phase 5)
  debaters/      directional, income, volatility, flow, sentiment, dealer (Phase 1+5)
  consensus/     GLS aggregator + covariance (EWMA, Phase A3) + calibration
  performance/   Thompson bandit + debater logger (Phase 4+A)
  dealer/        GEX computation + gamma flip/walls (Phase 5+D)
  sizing/        Kelly (B1/B4) + book limits (B2) + CVaR (B3) — Phase B
  portfolio/     book-level greeks + limits (Phase B)
  execution/     exit engine + roll logic + moomoo executor (Phase C — not yet built)
  graph/         pipeline.py — plain Python orchestration
scripts/
  smoke_test.py           Phase 0 validation
  bandit_warmstart.py     Phase A2 — populate bandit from 6-month history [complete]
  backtest.py             Phase F — historical replay (not yet built)
```

## Key architectural references

| Question | File |
|---|---|
| Architecture + gap analysis | `ARCHITECTURE.md` |
| Full production roadmap | `ROADMAP.md` |
| Phase 1 summary (debaters) | `PHASE1_SUMMARY.md` |
| Phase 2+3 summary (regime + consensus) | `PHASE2_3_SUMMARY.md` |
| Phase 4+5 summary (bandit + dealer) | `PHASE4_5_SUMMARY.md` |
| Schemas | `oa2/core/schemas.py` |
| Feature flags | `oa2/core/feature_flags.py` |
| Pipeline entry point | `oa2/graph/pipeline.py` |
| EWMA correlation tracker | `oa2/consensus/covariance.py` |
| Kelly sizing engine | `oa2/sizing/kelly.py` |
| Greek hard caps | `oa2/sizing/limits.py` |
| CVaR stress check | `oa2/sizing/cvar.py` |
