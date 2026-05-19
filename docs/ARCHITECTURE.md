# oa2 Architecture

## 9-Layer Adaptive Ensemble

```
L0  MARKET DATA & FEATURE FABRIC          (dataflows/)
L1  REGIME DETECTION SERVICE              (regime/)         — 8-bucket vol × trend + session overlay
L2  CONTEXT AGENTS (read-only, no LLM)    (context_agents/)
      • Dealer Positioning  (SPY/QQQ/IWM) — GEX, gamma flip, call/put walls, max pain
      • Macro Regime
      • Event Risk           (earnings blackout, CPI/FOMC)
      • Execution Quality    (spread, depth, fill prob)
L3  TIMEFRAME ROUTER                      (graph/pipeline.py)
L4  DEBATER ENSEMBLE                      (debaters/)
      Directional · Income · Volatility · Flow · Sentiment · Dealer
L5  CONSENSUS ENGINE                      (consensus/)
      GLS aggregator + EWMA covariance + isotonic calibration
L6  POSITION SIZING ENGINE                (sizing/)
      Fractional Kelly · DTE-aware scaling · CVaR cap · Greeks budget
L7  PORTFOLIO ORCHESTRATOR                (portfolio/)
      Book-level Δ/Γ/Θ/Vega limits · correlation matrix
L8  EXECUTION & LEARNING                  (execution/, learning/)
      moomoo executor · exit engine · trade journal · regime-indexed bandit
```

## Trade universe (locked, v2 launch)

| Group | Tickers | Dealer signal |
|---|---|---|
| Indices | SPY, QQQ, IWM, DIA | SPY/QQQ/IWM only |
| Macro ETFs | GLD, SLV, TLT, USO | cross-asset context signals |
| Sector ETFs | XLF, XLE, XLK, XLV, XLI, XLY | abstain |
| Mega-caps | NVDA, TSLA, AAPL, MSFT, AMZN, META, GOOGL, AMD | abstain (until paid OPRA data) |

22 total. Mega-caps enforce a 2-trading-day earnings blackout.
Macro ETFs (TLT, GLD, USO) serve double duty: tradeable + cross-asset regime inputs.

## Key design decisions

1. **Plain Python pipeline** instead of LangGraph. At single-account scale, a 50-line orchestrator beats a framework.
2. **Quant-only context agents.** No LLM in dealer/macro/event/exec — keeps latency and determinism.
3. **LLM debaters fire on event triggers**, not every tick. Cache outputs by `(regime, setup, ticker)`.
4. **Bandit indexed by `(debater, regime_bucket)`** — 6 debaters × 8 regimes = 48 Beta posteriors.
5. **Correlation-aware consensus** via GLS: `c = (1ᵀ Σ⁻¹ o) / N_eff`, `κ = Φ(|c|·√N_eff)`.
6. **Shadow-mode first** for every new component. Cutover only after parity demonstrated.
7. **Debaters must abstain honestly.** A debater with no real data returns conviction=0.0, not a fabricated estimate.
8. **Bandit warm-started from backtest.** Cold Beta(1,1) priors are useless for 6-12 months; historical replay populates posteriors before live trading.
9. **Sizing is a hard gate.** No trade executes without passing Kelly + Greeks caps + CVaR checks.
10. **Exit logic is mandatory.** Every approved trade gets exit conditions assigned at approval time.

## Regime taxonomy (8 buckets)

```
regime_id = (vol_state, trend_state)
vol_state   ∈ {VOL_COMP, NORMAL, VOL_EXP, CRISIS}  (from VolRegime)
trend_state ∈ {TREND, MEAN_REVERT, NEUTRAL}         (20d return classifier)
```

Regime thresholds:
- `VOL_COMPRESSION`: iv_rank < 0.35 — premium cheap, directional plays preferred
- `NORMAL`: iv_rank 0.35–0.65 — standard regime
- `VOL_EXPANSION`: iv_rank > 0.65 — premium selling window
- `CRISIS`: rv_iv_ratio > 1.20 OR vix > 35 — overrides vol state; reduces size
- Leading crisis signal: VIX3M/VIX < 1.05 AND VVIX > 110 → early CRISIS flag

Session overlay (D1): OPEN / MORNING / MIDDAY / AFTERNOON / POWER_HOUR adjusts
debater weights intraday — flow and GEX matter more at open; theta harvest in midday.

## Consensus engine

GLS aggregation with EWMA live covariance (λ=0.94, min 20 observations):
- Opinion vectorization: BULLISH×conv → +conv, BEARISH×conv → -conv, NEUTRAL → 0
- Precision matrix inverted from EWMA correlation tracker
- Effective sample size N_eff = (Σw)² / Σw² — reduced by correlated opinions
- p_bull = sigmoid(score × N_eff × 2.0) — calibrated probability for Kelly sizing

When EWMA tracker is cold (< 20 observations), falls back to `_fixed_correlation()`.
Feature flag: `OA2_FLAG_EWMA_CORR` (default on).

## Sizing engine (Phase B)

Three checks, all must pass before trade approval:

**B1/B4 — Fractional Kelly (DTE-aware)**
`f* = (edge × odds - (1 - edge)) / odds × kelly_fraction`
- `edge` = consensus p_bull (or 1 − p_bull for bearish)
- `kelly_fraction` = 0.25 (quarter-Kelly)
- DTE scaling: 0-2 DTE → 50% Kelly; 3-6 DTE → 75%; 7+ → 100%; 46+ → 75%

**B2 — Book-level Greeks hard caps**
- max_net_delta: ±0.30 of account
- max_net_vega: ±$50/1% IV move
- max_net_theta: no single-day theta > 2% of account
- max_single_underlying_pct: ≤25% of vega/delta in one name

**B3 — CVaR 5-scenario stress check**
1. Underlying −3% intraday
2. Underlying −5% intraday
3. VIX +10 points
4. VIX +20 points (crisis spike)
5. Correlation spike (all positions adversely correlated)

Any scenario breaching 5% of account → trade rejected or size reduced.

## Exit engine (Phase C)

Rules evaluated in priority order on every open position:

1. **Hard stop** — current loss ≥ max_loss_dollars → market order close
2. **Profit target** — current gain ≥ 50% of max_profit (short premium) → close
3. **DTE emergency** — DTE < 2 on any short leg → close (avoid assignment)
4. **Time stop** — position held > time_stop_days from entry → evaluate close
5. **Hard EOD** — 3:55 PM ET → force-close all intraday positions
6. **Regime flip** — regime ≠ entry regime → re-run consensus; if direction flips → close

Roll logic (C3): when short position is profitable (> 25% max profit), DTE < 14, regime
unchanged → evaluate rolling to next expiration vs closing.

## Flow adapter registry (Phase E)

Five adapters, all implement the same `FlowData` interface:
`yfinance` (free, delayed) | `moomoo` (built-in) | `tradier` | `options_whale` | `unusual_whales`

Selected via `OA2_FLOW_SOURCE` env var. Flow debater requires
`flow_data["data_quality"] == "real"` — abstains with conviction=0.0 otherwise.

## GEX / Dealer positioning (Phase 5 + D4)

Net GEX = Σ(call_gamma × call_OI − put_gamma × put_OI) × spot² × 100

- Positive GEX (dealers long gamma) → range-bound, dampen moves → NEUTRAL
- Negative GEX above gamma flip → dealers short gamma, hurt on rallies → BULLISH
- Negative GEX below gamma flip → dealers short gamma, hurt on drops → BEARISH

Extended outputs (D4):
- `call_wall` — highest OI call strike (magnetic resistance ceiling)
- `put_wall` — highest OI put strike (magnetic support floor)
- `max_pain` — strike minimising total aggregate option value (pinning target)

Dealer signal active only for SPY/QQQ/IWM (sufficient OI data).

## Failure modes designed for

- Cold-start (no covariance history) → fall back to flat weights
- Singular Σ → ridge-regularize with λ=1e-3
- Missing debater (timeout) → marginalize out, recompute N_eff
- Earnings within blackout → hard reject on mega-caps
- Stale OI for dealer signal → dealer abstains, weight reflows to others
- No real flow data → flow debater abstains (conviction=0.0)
- Bandit cold-start → warm-start posteriors from historical replay
- Sizing breach → hard reject regardless of consensus score
- DTE < 2 on short premium → force exit, override P&L targets
- moomoo unavailable → yfinance fallback (options chain + bars)

## What is NOT in v2 (intentional)

- Fisher agent (single-stock catalyst scanner)
- Alpaca execution (moomoo only)
- LangGraph
- Streamlit dashboard (later port)
- Single-stock OPRA data (mega-cap dealer signal deferred)

## Paper trading gate status

All hard requirements met for supervised paper trading:
- Phase A complete: honest debaters, live EWMA correlation, warm bandit
- Phase B complete: sizing engine passing all scenario checks
- Phase C complete: exit engine running on all open positions
- Phase F: v2 Sharpe ≥ v1 baseline on backtest window

Remaining gate: 2 weeks of shadow mode with no sizing/exit rule breaches before
enabling unsupervised paper trading.
