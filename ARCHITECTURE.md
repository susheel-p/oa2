# oa2 Architecture

## 9-Layer Adaptive Ensemble

```
L0  MARKET DATA & FEATURE FABRIC          (dataflows/)
L1  REGIME DETECTION SERVICE              (regime/)         — 8-bucket vol × trend
L2  CONTEXT AGENTS (read-only, no LLM)    (context_agents/)
      • Dealer Positioning  (SPY/QQQ/IWM)
      • Macro Regime
      • Event Risk           (earnings blackout, CPI/FOMC)
      • Execution Quality    (spread, depth, fill prob)
L3  TIMEFRAME ROUTER                      (graph/pipeline.py)
L4  DEBATER ENSEMBLE                      (debaters/)
      Directional · Income · Volatility · Flow · Sentiment · Dealer
L5  CONSENSUS ENGINE                      (consensus/)
      GLS aggregator + EWMA covariance + isotonic calibration
L6  POSITION SIZING ENGINE                (sizing/)
      Fractional Kelly · vol-target · CVaR cap · greeks budget
L7  PORTFOLIO ORCHESTRATOR                (portfolio/)
      Book-level Δ/Γ/Θ/Vega limits · correlation matrix
L8  EXECUTION & LEARNING                  (execution/, learning/)
      moomoo executor · trade journal · regime-indexed bandit
```

## Trade universe (locked, v2 launch)

| Group | Tickers | Dealer signal |
|---|---|---|
| Indices | SPY, QQQ, IWM, DIA | SPY/QQQ/IWM only |
| Macro ETFs | GLD, SLV, TLT, USO | abstain |
| Sector ETFs | XLF, XLE, XLK, XLV, XLI, XLY | abstain |
| Mega-caps | NVDA, TSLA, AAPL, MSFT, AMZN, META, GOOGL, AMD | abstain (until paid OPRA data) |

22 total. Mega-caps enforce a 2-trading-day earnings blackout.

## Key design decisions

1. **Plain Python pipeline** instead of LangGraph. At single-account scale, a 50-line orchestrator beats a framework.
2. **Quant-only context agents.** No LLM in dealer/macro/event/exec — keeps latency and determinism.
3. **LLM debaters fire on event triggers**, not every tick. Cache outputs by `(regime, setup, ticker)`.
4. **Bandit indexed by `(debater, regime_bucket)`** — 6 debaters × 8 regimes = 48 Beta posteriors.
5. **Correlation-aware consensus** via GLS: `c = (1ᵀ Σ⁻¹ o) / N_eff`, `κ = Φ(|c|·√N_eff)`.
6. **Shadow-mode first** for every new component. Cutover only after parity demonstrated.

## Regime taxonomy (v1 — 8 buckets)

```
regime_id = (vol_state, trend_state)
vol_state   ∈ {VOL_COMP, NORMAL, VOL_EXP, CRISIS}  (from existing VolRegime)
trend_state ∈ {TREND, MEAN_REVERT}                  (new — 20d return classifier)
```

Upgrade path: replace with HMM + BOCPD once ≥500 resolved trades exist.

## Failure modes designed for

- Cold-start (no covariance history) → fall back to flat weights.
- Singular Σ → ridge-regularize with `λ=1e-3`.
- Missing debater (timeout) → marginalize out, recompute N_eff.
- Earnings within blackout → hard reject on mega-caps.
- Stale OI for dealer signal → dealer debater abstains, weight reflows to others.

## What is NOT in v2 (intentional)

- Fisher agent (single-stock catalyst scanner)
- Alpaca execution (moomoo only)
- crisis_trade_agent.py
- Lightweight setup check (revisit if needed)
- Streamlit dashboard (later port)
- LangGraph