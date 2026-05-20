# RAG Learning Loop — Full Implementation Plan

**Date:** 2026-05-18  
**Goal:** Daily auto-learning system that correlates past decisions with outcomes and feeds insights back into the trading pipeline.

**Decisions (confirmed):**
- ✅ **Outcome definition: C** — track both direction-hit AND P&L proxy; weight by P&L for sizing decisions
- ✅ **RAG aggression: A (soft)** — knowledge base *informs* sizing (multiplier), humans review changes before acting
- ✅ **Mapping all phases first**, then build

---

## High-Level Data Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  9:35 AM     paper_trade.py --full-scan                               │
│  Entry       │                                                        │
│              ├─► Pipeline runs (debaters → consensus → sizing)        │
│              ├─► [NEW] RAG context injected from knowledge_base.json  │
│              │   - per-ticker accuracy multiplier                     │
│              │   - per-regime accuracy multiplier                     │
│              │   - per-debater historical conviction calibration      │
│              └─► logs/paper_trade_<date>.jsonl                        │
│                                                                       │
│  4:30 PM     [NEW] scripts/eod_outcomes.py                            │
│  Outcomes    │                                                        │
│              ├─► Fetch today's close prices (yfinance)                │
│              ├─► For each APPROVED trade from today:                  │
│              │     - direction_hit = (close > entry_price)            │
│              │     - pnl_proxy = simulate spread P&L from close       │
│              │     - magnitude = pnl_proxy / max_loss (signed)        │
│              └─► append outcomes_history.jsonl                        │
│                                                                       │
│  Nightly     [NEW] scripts/daily_learn.py                             │
│  Learning    │                                                        │
│              ├─► Read outcomes_history.jsonl (last 60d window)        │
│              ├─► Compute rolling stats:                               │
│              │     - per-ticker: hit_rate, avg_pnl_pct, n_trades      │
│              │     - per-regime: hit_rate, avg_pnl_pct                │
│              │     - per-debater: conviction-bucket vs hit-rate       │
│              │     - per-structure: avg_pnl_pct                       │
│              ├─► Detect pattern shifts (week-over-week drift)         │
│              ├─► Write knowledge_base.json (atomic)                   │
│              └─► Write reports/<date>_insights.md                     │
│                                                                       │
│  9:35 AM+1   paper_trade.py reads updated knowledge_base.json         │
│  (loop)      [Cycle repeats with learned multipliers]                 │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Knowledge Base Schema (`~/.tradingbot/knowledge_base.json`)

```json
{
  "schema_version": 1,
  "last_updated": "2026-05-18T22:00:00-04:00",
  "window_days": 60,
  "n_outcomes": 1450,
  "tickers": {
    "SPY": {
      "n_trades": 67,
      "hit_rate": 0.522,
      "avg_pnl_pct": 0.018,
      "win_rate_dollar_weighted": 0.541,
      "rolling_30d_hit_rate": 0.510,
      "trend": "stable",
      "conviction_multiplier": 0.97
    },
    "XLE": {
      "n_trades": 65,
      "hit_rate": 0.585,
      "avg_pnl_pct": 0.042,
      "win_rate_dollar_weighted": 0.612,
      "rolling_30d_hit_rate": 0.601,
      "trend": "improving",
      "conviction_multiplier": 1.10
    },
    "SLV": {
      "n_trades": 60,
      "hit_rate": 0.367,
      "avg_pnl_pct": -0.038,
      "win_rate_dollar_weighted": 0.318,
      "rolling_30d_hit_rate": 0.345,
      "trend": "declining",
      "conviction_multiplier": 0.0,
      "blacklisted": true,
      "blacklist_reason": "hit_rate < 0.43 AND dollar-weighted < 0.45"
    }
  },
  "regimes": {
    "vol_compression_mean_revert": {
      "n_trades": 49,
      "hit_rate": 0.408,
      "avg_pnl_pct": -0.022,
      "conviction_multiplier": 0.5,
      "blocked": true
    },
    "normal_neutral": {
      "n_trades": 129,
      "hit_rate": 0.558,
      "avg_pnl_pct": 0.031,
      "conviction_multiplier": 1.10
    }
  },
  "debaters": {
    "directional": {
      "n_votes": 1451,
      "hit_rate_when_voting": 0.517,
      "by_conviction_bucket": {
        "0.40-0.50": {"n": 320, "hit_rate": 0.49},
        "0.50-0.60": {"n": 580, "hit_rate": 0.53},
        "0.60-0.75": {"n": 551, "hit_rate": 0.51}
      },
      "calibration_score": 0.92
    }
  },
  "structures": {
    "vertical_call_spread": {
      "n_trades": 482,
      "hit_rate": 0.541,
      "avg_pnl_pct": 0.029,
      "median_max_loss": 285.0
    }
  },
  "patterns": {
    "high_conviction_in_mean_revert": {
      "description": "p_bull > 0.65 in mean-reverting regime",
      "n_observed": 87,
      "hit_rate": 0.391,
      "recommendation": "block or reduce sizing 50%"
    },
    "bullish_xle_morning_session": {
      "description": "BULLISH XLE during OPEN/MORNING session",
      "n_observed": 28,
      "hit_rate": 0.643,
      "recommendation": "boost conviction 15%"
    }
  }
}
```

---

## Phase Breakdown

### Phase 1 — Foundation (Tonight, ~2.5 hrs)

#### 1.1 `tradingbot/learning/outcomes.py` — Outcome resolver
**Purpose:** Convert decision logs into outcomes (direction + P&L).

```python
@dataclass
class TradeOutcome:
    trade_id: str
    ticker: str
    decision_date: str
    direction: str          # BULLISH / BEARISH
    structure: str
    entry_price: float
    next_day_close: float
    # Outcomes:
    direction_hit: bool          # close moved in predicted direction
    pnl_proxy_dollars: float     # simulated spread P&L
    pnl_pct_of_max_loss: float   # signed: +1.0 = max win, -1.0 = max loss
    max_profit: float
    max_loss: float
    p_bull_at_entry: float
    regime_at_entry: str
    debater_convictions: dict[str, float]

def resolve_outcomes(decision_log_path: Path, close_prices: dict) -> list[TradeOutcome]
def simulate_spread_pnl(direction, long_strike, short_strike, entry_close, next_close) -> float
```

#### 1.2 `scripts/eod_outcomes.py` — EOD CLI
**Purpose:** Run after market close to resolve today's APPROVED trades.

```bash
python scripts/eod_outcomes.py                  # today's outcomes
python scripts/eod_outcomes.py --date 2026-05-18   # backfill specific date
python scripts/eod_outcomes.py --backfill        # process all log files
```

Outputs:
- `~/.tradingbot/outcomes/outcomes_<date>.jsonl` (one line per outcome)
- `~/.tradingbot/outcomes/outcomes_history.jsonl` (rolling append for analysis)

#### 1.3 One-shot backfill
Process all existing `logs/paper_trade_*.jsonl` files → populate `outcomes_history.jsonl`.

**Validation gate:** Print summary `N trades resolved, X% hit rate, $Y total P&L proxy`.

---

### Phase 2 — Knowledge Base + Learner (Tomorrow Morning, ~3 hrs)

#### 2.1 `tradingbot/learning/knowledge_base.py` — KB writer/reader
**Purpose:** Read/write `~/.tradingbot/knowledge_base.json` with atomic writes.

```python
@dataclass
class KnowledgeBase:
    last_updated: str
    window_days: int
    tickers: dict[str, TickerStats]
    regimes: dict[str, RegimeStats]
    debaters: dict[str, DebaterStats]
    structures: dict[str, StructureStats]
    patterns: list[Pattern]

    @classmethod
    def load(cls, path: Path) -> KnowledgeBase
    def save(cls, path: Path) -> None
    def get_ticker_multiplier(ticker: str) -> float   # used by pipeline
    def get_regime_multiplier(regime: str) -> float
    def is_blacklisted(ticker: str) -> bool
```

#### 2.2 `scripts/daily_learn.py` — Nightly aggregator
**Purpose:** Read outcomes history → compute KB stats → write reports.

Key computations:
- **Per-ticker:** hit_rate, dollar_weighted_win_rate, rolling_30d, trend (improving/stable/declining)
- **Per-regime:** hit_rate, avg_pnl_pct
- **Per-debater:** conviction-bucket hit rates (calibration check)
- **Pattern miner:** find combos with statistically significant hit-rate deviation (e.g., "high conviction in mean-revert" or "XLE morning session")

**Blacklist rules:**
- ticker `hit_rate < 0.43` AND `dollar_weighted_win_rate < 0.45` AND `n_trades >= 20` → blacklist
- regime `hit_rate < 0.45` AND `n_trades >= 30` → block (multiplier 0)

**Conviction multiplier formula:**
```python
mult = 0.6 + (hit_rate - 0.43) * 4.0   # 0.43->0.6, 0.55->1.08, 0.65->1.48
mult = clip(mult, 0.5, 1.30)
```

#### 2.3 Report template `reports/<date>_insights.md`
Auto-generated daily markdown with:
- Today's decisions summary (approved/rejected)
- Last 7-day rolling performance
- Top 5 / bottom 5 tickers
- New patterns detected this week
- Recommended actions (changes to consider)
- Confidence calibration chart

---

### Phase 3 — RAG Context Injection (Tomorrow Afternoon, ~1.5 hrs)

#### 3.1 `tradingbot/learning/rag_context.py` — Replace static blacklist
**Purpose:** Inject KB-derived multipliers into pipeline at decision time.

```python
def load_rag_context() -> RagContext:
    """Load knowledge_base.json once per scan."""

class RagContext:
    def ticker_multiplier(ticker: str) -> float
    def regime_multiplier(regime: str) -> float
    def is_blacklisted(ticker: str) -> bool
    def matched_patterns(context: dict) -> list[Pattern]
```

#### 3.2 Wire into `pipeline._run_sizing`
Replace current hardcoded `TICKER_BLACKLIST` lookup with KB-driven version. Quality gate becomes:
```python
rag = load_rag_context()
if rag.is_blacklisted(ticker):
    return {"approved": False, "reject_gate": "rag_blacklist", ...}

# Apply combined multiplier (ticker × regime × pattern)
final_mult = (
    rag.ticker_multiplier(ticker)
    * rag.regime_multiplier(regime_label)
    * rag.matched_pattern_multiplier(context)
)
p_bull_adj = 0.5 + (p_bull - 0.5) * final_mult
```

#### 3.3 Logging KB contribution
Every approved/rejected trade attribution gets a `rag_context` block showing which multipliers fired and why.

---

### Phase 4 — Reporting & Calibration UI (Day 3, ~2 hrs)

#### 4.1 `scripts/report_dashboard.py` — Multi-day rollup
Generate weekly + monthly summaries:
- `reports/weekly_2026-W21.md`
- `reports/monthly_2026-05.md`

#### 4.2 Brier-score tracking
Track calibration of our p_bull predictions over time. Auto-refit calibrator when Brier drift > 5%.

#### 4.3 Pattern explorer CLI
```bash
python scripts/explore_patterns.py --ticker XLE --regime normal_trending
# Shows historical decisions, outcomes, what worked
```

---

### Phase 5 — Automation (Day 4, ~1 hr)

#### 5.1 Cron / Task Scheduler entries
```
# Daily entry scan (already exists)
35 9   * * MON-FRI  python scripts/paper_trade.py --full-scan

# Intraday exit monitoring
*/5 9-16 * * MON-FRI  python scripts/paper_trade.py --exit-only

# EOD outcome resolution (after market close)
30 16  * * MON-FRI  python scripts/eod_outcomes.py

# Nightly learning aggregator
0  22  * * MON-FRI  python scripts/daily_learn.py
```

#### 5.2 Health monitoring
- Slack/email on: blacklist additions, pattern detections, KB drift > threshold
- Daily summary push to email

---

### Phase 6 — Advanced (Future, ~4-6 hrs)

| Feature | Description |
|---------|-------------|
| **Bandit integration** | KB feeds Thompson-sampling weights, fully adaptive allocation |
| **Multi-day patterns** | Detect 3-day momentum + sentiment-flip patterns |
| **Cross-asset RAG** | "When SPY is up 1%+ in morning, XLE has 62% hit rate" |
| **Counterfactual replay** | What would profit have been if KB had been live 30 days ago? |
| **Regime-shift alerts** | KB detects when current behavior diverges from historical baseline |

---

## File Layout

```
tradingbot/
  learning/
    __init__.py
    outcomes.py           # TradeOutcome dataclass + resolver
    knowledge_base.py     # KB schema + read/write
    rag_context.py        # Injects KB into pipeline
    patterns.py           # Pattern mining algorithms

scripts/
  eod_outcomes.py         # EOD outcome resolution (Phase 1)
  daily_learn.py          # Nightly aggregator (Phase 2)
  report_dashboard.py     # Multi-day rollups (Phase 4)
  explore_patterns.py     # Pattern explorer CLI (Phase 4)

reports/
  2026-05-18_insights.md  # Auto-generated daily reports
  weekly_2026-W21.md
  monthly_2026-05.md

tests/
  test_outcomes.py
  test_knowledge_base.py
  test_rag_context.py
  test_patterns.py

~/.tradingbot/
  knowledge_base.json     # Current KB state (single source of truth)
  outcomes/
    outcomes_2026-05-18.jsonl
    outcomes_history.jsonl
```

---

## Validation Gates (Per Phase)

| Phase | Gate | Pass Criteria |
|-------|------|---------------|
| 1 | Outcome resolver works | Backfill produces N>0 outcomes with sane hit_rate (40-60%) |
| 2 | KB matches backtest | KB ticker hit_rates within ±2 ppt of backtest analysis |
| 3 | RAG context wired | Live scan still produces 7-10 approvals; KB multipliers visible in logs |
| 4 | Reports auto-generate | Daily report markdown file created without manual edits |
| 5 | Cron runs reliably | 5 consecutive successful nightly runs without errors |
| 6 | Bandit improves | Sharpe with bandit weights > Sharpe with KB-only |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| KB overfits to recent noise | Require min `n_trades >= 20` before multiplier kicks in; use rolling 60-day window |
| Bad data corrupts KB | Atomic JSON writes (write to .tmp, rename); keep last 7 KB snapshots |
| Outcome resolution lag (T+1 settlement) | EOD job uses T close; full reconciliation T+1 morning |
| RAG causes feedback loop (blacklist → no trades → no learning) | Soft mode (multiplier 0.5 not 0) for borderline tickers to keep collecting data |
| Multiplier explosion (XLE goes to 2x conviction → outsized risk) | Cap multiplier at 1.30 |

---

## Tonight's Starting Point — Phase 1

**Immediate goal:** By tonight, have:
1. `outcomes.py` resolving today's APPROVED trades (SPY, QQQ, XLE, NVDA, AAPL, MSFT, AMZN, GOOGL, AMD)
2. `eod_outcomes.py` CLI working
3. Backfill into `outcomes_history.jsonl`
4. Print: "9 trades from 2026-05-18, predicted 9 BULLISH, X hit, $Y net P&L proxy"

**Tomorrow's Phase 2:** KB + daily_learn.py running → first auto-generated insights report.

---

## Implementation Order Confirmation

| Order | Phase | When |
|-------|-------|------|
| 1 | Phase 1: Foundation (outcomes resolver) | Tonight ~2.5 hr |
| 2 | Phase 2: KB + learner | Tomorrow morning ~3 hr |
| 3 | Phase 3: RAG context wiring | Tomorrow afternoon ~1.5 hr |
| 4 | Phase 4: Reporting + calibration | Day 3 ~2 hr |
| 5 | Phase 5: Automation (cron) | Day 4 ~1 hr |
| 6 | Phase 6: Advanced bandit integration | Future |

**Ready to start with Phase 1?**
