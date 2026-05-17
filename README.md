# oa2 — OptionsAgents v2

Adaptive, regime-aware ensemble trading system for options on a 22-name universe
(indices, macro ETFs, sector ETFs, mega-caps). Greenfield rewrite of v1 to fix
structural weaknesses with a 9-layer architecture.

## What's different from v1

- **Universe trimmed** to 22 names: 4 indices + 4 macro ETFs + 6 sector ETFs + 8 mega-caps.
- **Dealer Positioning Agent** computes GEX/DEX/gamma flip/walls for SPY/QQQ/IWM.
- **Correlation-aware consensus engine** (GLS / Mahalanobis) replaces static weighted sum.
- **Regime-indexed Thompson bandit** learns per-(debater, regime) reliability.
- **Plain Python pipeline** — no LangGraph; simpler and easier to debug at this scale.
- **No Fisher agent**, no single-stock catalyst fishing, no Alpaca (deferred).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the 9-layer design and gap analysis.
See [ROADMAP.md](ROADMAP.md) for the full production plan.

## Status

**Phases 0-5 complete.** Pipeline runs end-to-end. 92 tests passing.
NOT yet production-ready — see blockers below.

### Completed phases

| Phase | Description | Status |
|---|---|---|
| 0 | Scaffold + 22-ticker watchlist + data adapters | ✅ |
| 1 | 5 debaters (directional/income/volatility/flow/sentiment) | ✅ |
| 2 | Regime classifier (8 buckets vol × trend) | ✅ |
| 3 | Consensus engine v1 (GLS aggregation) | ✅ |
| 4 | Regime-indexed Thompson bandit | ✅ |
| 5 | Dealer positioning agent + 6th debater (GEX) | ✅ |

### Production phases (current work)

| Phase | Description | Status | Blocks |
|---|---|---|---|
| A | Signal integrity: honest flow, live correlation, bandit warm-start | 🔨 | Everything |
| B | Sizing engine: Kelly + book limits + CVaR + DTE-aware | Pending | Paper trading |
| C | Exit engine: position monitor + rules + roll logic | Pending | Unattended run |
| D | Regime enhancement: session overlay + leading crisis + cross-asset | Pending | Signal quality |
| E | Real flow data: vendor selection + sweep tape integration | Pending | Flow debater edge |
| F | Backtesting harness + A/B vs v1 | Ongoing | Paper cutover gate |

### Production blockers (must fix before paper trading)

1. **Sizing engine missing** (`oa2/sizing/` empty) — no position sizing = undefined risk
2. **Exit engine missing** — open positions have no automated exit logic
3. **Flow debater fabricates data** — derives PCR from chain delta; abstention fix needed
4. **Bandit cold-start** — flat priors; warm-start from historical replay required
5. **GLS correlations hardcoded** — never empirically validated; EWMA update required

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

Run tests:
```bash
pytest tests/ -v
# 92 passed
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
  consensus/     GLS aggregator + covariance + calibration (Phase 3+A)
  performance/   Thompson bandit + debater logger (Phase 4+A)
  dealer/        GEX computation + gamma flip/walls (Phase 5+D)
  sizing/        Kelly + vol-target + CVaR (Phase B — not yet built)
  portfolio/     book-level greeks + limits (Phase B — not yet built)
  execution/     exit engine + roll logic + moomoo executor (Phase C — not yet built)
  graph/         pipeline.py — plain Python orchestration
scripts/
  smoke_test.py         Phase 0 validation
  bandit_warmstart.py   Phase A2 — populate bandit from history (not yet built)
  backtest.py           Phase F — historical replay (not yet built)
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
