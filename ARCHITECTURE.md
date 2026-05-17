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

## Regime taxonomy (v1 — 8 buckets)

```
regime_id = (vol_state, trend_state)
vol_state   ∈ {VOL_COMP, NORMAL, VOL_EXP, CRISIS}  (from existing VolRegime)
trend_state ∈ {TREND, MEAN_REVERT, NEUTRAL}         (20d return classifier)
```

### Regime Classifier Known Gaps (tracked, fix in Phase D)

- Crisis detection is backward-looking (VIX > 35). Leading signals needed: VIX3M/VIX ratio < 1.05 + VVIX > 110.
- 20-day slope is too slow for intraday regime. Session-state overlay needed (OPEN/MORNING/MIDDAY/POWER_HOUR).
- No cross-asset macro signals: TLT crash + DXY spike (risk-off), HYG/JNK spread widening (credit stress).
- Upgrade path: replace with HMM + BOCPD once ≥500 resolved trades exist.

## Consensus Engine Known Gaps (tracked, fix in Phase A)

- Correlation matrix is hardcoded (fixed pairs, not learned). Replace with EWMA over resolved trades.
- GLS correctness depends on accurate correlation estimates. Wrong correlations amplify duplicated signals.
- Fixed correlations were never empirically validated; they were initial assumptions.

## Debater Known Gaps (tracked, fix in Phase A + E)

### Flow Debater (highest priority)
- Currently derives PCR from chain delta field — this is a proxy of a proxy.
- Dark pool flags (dark_pool_bullish/bearish) are never populated from real data.
- Must return conviction=0.0 + NEUTRAL when no real sweep/flow data is available.
- Real data source needed: Unusual Whales API or Tradier streaming tape.

### Bandit Cold-Start Problem
- 48 Beta(1,1) priors require 30-50 resolved trades per arm to diverge.
- At 2-3 paper trades/day, adaptive weighting is dormant for 6-12 months.
- Fix: warm-start from 6-month historical replay before enabling bandit weights.

### Additive Conviction Scoring
- Directional and flow debaters sum independent-looking signals that share the same underlying price series.
- RSI oversold + price below VWAP + EMA20 < EMA50 are correlated — not 3 independent votes.
- Fix: group signals by data source, allow only one vote per group; cross-group convergence = real conviction.

## Missing Modules (production blockers)

### Sizing Engine (oa2/sizing/) — BLOCKER for paper trading
Required before any live or paper trading begins:
- Fractional Kelly per trade: f* = (edge / odds) × 0.5, where edge = consensus conviction × regime win rate
- Book-level hard caps: max net delta, max net vega, max net theta
- CVaR scenario check: 5 stress scenarios (SPY -3%, SPY -5%, VIX +10, VIX +20, correlation spike)
- DTE-aware sizing: smaller size for < 7 DTE short positions (gamma risk)

### Portfolio Orchestrator (oa2/portfolio/) — BLOCKER for paper trading
- Book-level Δ/Γ/Θ/Vega limits with running tallies
- Position concentration check: max % of book in single underlying
- Correlation matrix for position clustering

### Exit Engine (oa2/execution/exit.py) — BLOCKER for unattended running
- Position monitor: mark-to-market vs targets
- Rules: 50% of max profit → close short premium; stop loss hit → close; DTE < 2 on short → close
- Hard EOD cutoff: 3:55 PM ET force-close all intraday
- Regime flip → re-evaluate and reduce exposure
- Roll logic: evaluate roll vs close for near-expiration profitable positions

## Missing Edge Factors (ranked by production impact)

| Priority | Factor | Current State | Status |
|---|---|---|---|
| 1 | Real options flow / sweep tape | Fake (derived from delta) | Phase E |
| 2 | Sizing engine (Kelly + book limits) | Empty directory | Phase B |
| 3 | EWMA correlation matrix (live) | Hardcoded assumptions | Phase A |
| 4 | Exit signal engine | No module exists | Phase C |
| 5 | DTE-aware strategy routing | Not used anywhere | Phase B |
| 6 | Max pain / call wall levels from GEX | Not computed | Phase D |
| 7 | Intraday session regime overlay | 20-day slope only | Phase D |
| 8 | Bandit warm-start / hierarchical prior | Flat Beta(1,1) | Phase A |
| 9 | Cross-asset macro signals in regime | VIX + slope only | Phase D |
| 10 | Additive conviction (signal overlap) | Simple count model | Phase A |

## DTE Edge (fundamental options concept, not yet modeled)

Options edge is fundamentally a function of DTE:
- 0-2 DTE: gamma rent cheap relative to realized moves on catalyst days → long gamma favored
- 7-14 DTE: transition zone; theta accelerates, gamma still meaningful
- 21-45 DTE: sweet spot for defined-risk premium selling (iron condors, verticals)
- 60+ DTE: calendar/diagonal territory, vol term structure plays

No debater, router, or sizing rule currently uses DTE as a variable.
Fix in Phase B (sizing) and Phase C (exit).

## Max Pain / Call-Put Walls (not yet computed)

The GEX computation already has the data needed. Call walls (highest OI call strike) cap intraday upside.
Put walls provide magnetic support. Max pain (where most contracts expire worthless) is the pinning target.
These should populate Setup.resistance_level and Setup.support_level, currently set from EMA levels alone.
Fix in Phase D.

## Failure modes designed for

- Cold-start (no covariance history) → fall back to flat weights.
- Singular Σ → ridge-regularize with `λ=1e-3`.
- Missing debater (timeout) → marginalize out, recompute N_eff.
- Earnings within blackout → hard reject on mega-caps.
- Stale OI for dealer signal → dealer debater abstains, weight reflows to others.
- No real flow data → flow debater abstains (conviction=0.0), not fabricated.
- Bandit cold-start → warm-start posteriors from historical replay before enabling.
- Sizing breach → hard reject regardless of consensus score.
- DTE < 2 on short premium → force exit, override P&L targets.

## What is NOT in v2 (intentional)

- Fisher agent (single-stock catalyst scanner)
- Alpaca execution (moomoo only)
- crisis_trade_agent.py
- Lightweight setup check (revisit if needed)
- Streamlit dashboard (later port)
- LangGraph

## Production Readiness Assessment

As of Phases 1-5 complete: the system is an entry signal generator with educated but
partially synthetic opinions. It is NOT ready for live or unattended paper trading because:

1. No sizing engine — wrong size kills edge even with correct direction
2. No exit engine — options decay; winning trades become losses without exits
3. Flow debater emits fabricated signals — adds noise to consensus
4. Bandit cold-start — adaptive weighting is dormant for months without warm-start
5. GLS correlations are assumptions — consensus math depends on accurate Σ

Gate for paper trading cutover: Phase A + Phase B + Phase C complete, Phase F shows v2 >= v1.
