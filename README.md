# oa2 — OptionsAgents v2

Greenfield rewrite of OptionsAgents with an adaptive, regime-aware ensemble
architecture. v1 (`../OptionsAgents`) remains the production paper-trading
system until v2 demonstrates parity in shadow mode.

## What's different from v1

- **Universe trimmed** to 22 names: 4 indices + 4 macro ETFs + 6 sector ETFs + 8 mega-caps.
- **Dealer Positioning Agent** computes GEX/DEX/gamma flip/walls for SPY/QQQ/IWM.
- **Correlation-aware consensus engine** (GLS / Mahalanobis) replaces static weighted sum.
- **Regime-indexed Thompson bandit** learns per-(debater, regime) reliability.
- **Plain Python pipeline** — no LangGraph; simpler and easier to debug at this scale.
- **No Fisher agent**, no single-stock catalyst fishing, no Alpaca (deferred).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the 9-layer design.

## Status

**Phase 0 (scaffolding) — complete.** Pipeline imports cleanly, watchlist loads,
data adapters work. No decisions are made yet.

| Phase | Status |
|---|---|
| 0. Scaffold + 22-ticker watchlist + ported data adapters | ✅ |
| 1. Port 5 debaters with new base class | ⏳ |
| 2. Regime classifier (8 buckets) | ⏳ |
| 3. Consensus engine v1 (GLS) | ⏳ |
| 4. Regime-indexed bandit | ⏳ |
| 5. Dealer positioning agent | ⏳ |
| 6. A/B harness vs v1 | ⏳ |
| 7. Cutover to v2 paper trading | ⏳ |

## Quickstart

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[broker,dev]"
cp .env.example .env  # then fill in credentials
python -m scripts.smoke_test
```

## Layout

```
oa2/
  core/          schemas, config, feature flags
  dataflows/     market data + sentiment adapters (ported from v1)
  watchlist/     22-ticker universe
  regime/        regime classifier (Phase 2)
  context_agents/dealer, macro, event_risk, exec_quality (Phase 5)
  debaters/      directional, income, volatility, flow, sentiment, dealer (Phase 1)
  consensus/     GLS aggregator + covariance + calibration (Phase 3)
  learning/      bandit, journal, store (Phase 4)
  sizing/        Kelly + vol-target + CVaR (later)
  portfolio/     book-level greeks + limits (later)
  execution/     moomoo executor (later)
  graph/         pipeline.py — plain Python orchestration
```