# RAG Learning System

The trading bot learns from past trade outcomes and adapts position sizing and conviction over time. This document covers everything: architecture, daily workflow, decision log format, and troubleshooting.

---

## How It Works

Every day the system runs a feedback loop:

```
9:35 AM   paper_trade.py --full-scan
            Loads knowledge_base.json
            Applies learned multipliers to p_bull
            Scales Kelly by Thompson posterior confidence
            Logs all RAG decisions to logs/paper_trade_YYYY-MM-DD.jsonl

4:30 PM   scripts/daily_learn.py
            Reads today's outcomes (close prices via yfinance)
            Computes rolling stats per ticker, regime, debater
            Writes updated knowledge_base.json

Next day  Repeat with improved multipliers
```

**Key principle:** The knowledge base *informs* sizing (multiplier on conviction), it never forces a trade. Humans review changes before raising risk.

---

## Knowledge Base

Stored at `~/.tradingbot/knowledge_base.json`. Contains:

- **Ticker stats** — hit rate, dollar-weighted win rate, blacklist flag
- **Regime stats** — hit rate and multiplier per market regime
- **Debater calibration** — accuracy per debater per conviction bucket
- **Thompson posteriors** — Beta(α, β) per debater per regime (from backtest)

### Conviction Multipliers

```
ticker_mult  = clip(0.6 + (hit_rate - 0.43) × 4.0,  0.5, 1.30)
regime_mult  = clip(0.6 + (hit_rate - 0.45) × 4.0,  0.5, 1.30)
combined     = ticker_mult × regime_mult (clipped 0.4–1.5)

p_bull_adj = 0.5 + (p_bull_raw - 0.5) × combined
```

- **> 1.0** — ticker/regime performing well, boost conviction
- **< 1.0** — ticker/regime underperforming, reduce conviction
- **0.0** — blacklisted (hit_rate < 43% AND dollar_win < 45%), trade blocked

### Thompson Confidence Scaling

```
confidence = 1.0 / (1.0 + posterior_std)
scaled_kelly = kelly_f × confidence
```

- Beta(1,1) — no data, std=0.235, confidence=0.81 → size reduced 19%
- Beta(110,90) — 200 observations, std=0.035, confidence=0.97 → full size

---

## Setup

### One-time: seed the knowledge base

```bash
python scripts/backtest.py --months 12 --bandit
```

Computes Thompson posteriors per debater × regime and saves to `~/.tradingbot/knowledge_base.json`.

### Daily: update after market close

```bash
python scripts/daily_learn.py
```

### Every morning: KB loads automatically

```bash
python scripts/paper_trade.py --full-scan
# Prints at startup:
# RAG Learning: ENABLED
#   KB last updated: 2026-05-24
#   Tickers tracked: 22
#   Outcomes used: 1450
#   Thompson posteriors: 40
```

---

## Diagnostics

### Check current KB state

```bash
python scripts/rag_status.py                    # full report
python scripts/rag_status.py --ticker SPY       # one ticker
python scripts/rag_status.py --posteriors-only  # Thompson posteriors
```

### Measure RAG impact on recent trades

```bash
python scripts/rag_impact.py --days 7
python scripts/rag_impact.py --date 2026-05-24
```

### Disable RAG (testing)

```bash
export TRADINGBOT_FLAG_KB_ENABLED=0
python scripts/paper_trade.py --full-scan
```

---

## Decision Log Format

Every trade in `logs/paper_trade_YYYY-MM-DD.jsonl` includes a `rag_learning` section:

```json
{
  "ticker": "SPY",
  "rag_learning": {
    "enabled": true,
    "kb_metadata": {
      "last_updated": "2026-05-24T22:00:00Z",
      "window_days": 60,
      "n_outcomes_used": 1450,
      "ticker_stats": { "n_trades": 67, "hit_rate": 0.522, "blacklisted": false },
      "regime_stats": { "n_trades": 129, "hit_rate": 0.558 }
    },
    "conviction_multipliers": {
      "ticker_multiplier": 0.97,
      "regime_multiplier": 1.10,
      "combined_multiplier": 1.07
    },
    "p_bull_adjustment": {
      "p_bull_raw": 0.620,
      "p_bull_adjusted": 0.663
    }
  },
  "sizing": {
    "kelly": {
      "thompson_scaling": {
        "enabled": true,
        "aggregate": { "confidence": 0.926 }
      }
    }
  }
}
```

### Key field meanings

| Field | Meaning |
|-------|---------|
| `rag_learning.enabled` | KB loaded successfully |
| `ticker_multiplier` | How ticker accuracy adjusts conviction |
| `regime_multiplier` | How regime accuracy adjusts conviction |
| `p_bull_adjustment` | Before/after conviction shift |
| `thompson_scaling.confidence` | Posterior certainty → Kelly scaling |
| `blacklisted` | Trade blocked if true |

---

## Filtering Decisions via Logs

```bash
# All trades from a date
cat logs/paper_trade_2026-05-24.jsonl | jq '.rag_learning'

# Trades where RAG was active
cat logs/paper_trade_2026-05-24.jsonl | jq 'select(.rag_learning.enabled == true)'

# Multipliers applied
cat logs/paper_trade_2026-05-24.jsonl | jq '.rag_learning.conviction_multipliers'
```

---

## Blacklist Rules

| Condition | Effect |
|-----------|--------|
| ticker `hit_rate < 0.43` AND `dollar_win < 0.45` AND `n_trades >= 20` | Blacklisted — trade blocked |
| regime `hit_rate < 0.45` AND `n_trades >= 30` | Blocked — multiplier = 0 |

Borderline tickers get multiplier 0.5 (not 0) to keep collecting data and avoid feedback loops.

---

## Troubleshooting

**KB shows "last_updated" from days ago?**
Run `python scripts/daily_learn.py` to refresh.

**Thompson posteriors empty?**
Run `python scripts/backtest.py --months 12 --bandit` to warm-start.

**Multiplier stuck at 1.0?**
Ticker has fewer than 20 trades in the 60-day window. Needs more data.

**Paper trading shows "RAG: DISABLED"?**
KB file missing. Regenerate: `python scripts/backtest.py --bandit`

**Ticker blacklisted but you want to trade it?**
Wait until hit_rate recovers above 43% over 20+ trades. Do not manually override.

---

## Planned Improvements

| Phase | Feature | Status |
|-------|---------|--------|
| Current | Thompson posteriors → Kelly scaling | Done |
| Next | Dynamic debater weights from posteriors | Planned |
| Future | Pattern mining (regime × ticker combos) | Planned |
| Future | Multi-day momentum patterns | Planned |
