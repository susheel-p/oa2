"""Bandit warm-start — populate Thompson bandit posteriors from historical data.

Phase A2: Rather than starting with flat Beta(1,1) priors (which take 6-12
months of paper trading to become useful), this script replays 6 months of
daily OHLCV data through the debater ensemble and scores each debater's
directional call against the next-day close.

Usage:
    python scripts/bandit_warmstart.py [--months 6] [--dry-run] [--verbose]

What it does:
  1. Fetches daily OHLCV for all 22 watchlist tickers via yfinance (EOD data).
  2. For each trading day, constructs a minimal context dict with available
     price/vol signals (VWAP estimated from OHLC, EMA approximated, IV from
     yfinance options where available).
  3. Runs each debater's debate() against that context.
  4. Classifies the next-day close as BULLISH (up > 0.1%) or BEARISH (down > 0.1%).
  5. A debater's call is a "hit" if its direction matches next-day direction
     (NEUTRAL is never counted as a hit — it abstains from the binary bet).
  6. Updates BanditEngine Beta posteriors per (debater, regime) arm.
  7. Saves posteriors to ~/.oa2/bandit/posteriors.json.

Limitations:
  - Flow debater will abstain throughout (no real tape data in history).
  - Sentiment debater will use neutral baseline (no historical sentiment).
  - Regime classification uses daily VIX + IV rank where available, otherwise
    falls back to price-slope-only regime.
  - This produces a warm (not perfect) prior. Posteriors will update as live
    paper trading accumulates real resolved trades.

Output:
  Prints per-debater, per-regime hit rates and final Beta(alpha, beta) parameters.
  Saves posteriors.json for the bandit to load on next startup.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm-start Thompson bandit from historical data.")
    parser.add_argument("--months", type=int, default=6, help="Months of history to replay (default: 6)")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not save posteriors")
    parser.add_argument("--verbose", action="store_true", help="Print per-ticker, per-day details")
    parser.add_argument("--tickers", nargs="+", help="Limit to specific tickers (default: all 22)")
    return parser.parse_args()


def _compute_ema(prices: list[float], period: int) -> float:
    """Simple EMA of last N prices."""
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def _compute_rsi(closes: list[float], period: int = 14) -> float:
    """RSI from list of closing prices."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _build_context(
    ticker: str,
    row_idx: int,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    iv_rank: float = 0.50,
    vix: float = 20.0,
) -> dict:
    """Build a minimal debate context from OHLCV history."""
    price = closes[row_idx]
    prior_close = closes[row_idx - 1] if row_idx > 0 else price

    # VWAP approximation from OHLC (daily: (H+L+C)/3)
    vwap = (highs[row_idx] + lows[row_idx] + closes[row_idx]) / 3

    # ATR (14-day)
    atr_window = min(14, row_idx)
    recent_highs = highs[max(0, row_idx - atr_window): row_idx + 1]
    recent_lows = lows[max(0, row_idx - atr_window): row_idx + 1]
    atr = sum(h - l for h, l in zip(recent_highs, recent_lows)) / max(len(recent_highs), 1)

    # EMAs
    recent_closes = closes[max(0, row_idx - 50): row_idx + 1]
    ema_20 = _compute_ema(recent_closes, 20)
    ema_50 = _compute_ema(recent_closes, 50)

    # RSI
    rsi = _compute_rsi(closes[max(0, row_idx - 30): row_idx + 1])

    # 20d price slope for regime
    start_idx = max(0, row_idx - 20)
    prices_20d = closes[start_idx: row_idx + 1]

    return {
        "ticker": ticker,
        "current_price": price,
        "prior_close": prior_close,
        "vwap": vwap,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "rsi": rsi,
        "atr": atr,
        "market_vix": vix,
        "vvix": 80.0,
        "vol_regime": {
            "iv_rank": iv_rank,
            "rv_iv_ratio": 1.0,
            "vix": vix,
            "skew_extreme": False,
            "term_upward": True,
        },
        "prices_20d": prices_20d,
        "flow_data": {},  # no real flow data in history — flow debater abstains
        "sentiment_snapshot": None,  # no historical sentiment
    }


def _classify_regime(context: dict) -> int:
    """Quick regime classification for warm-start."""
    from oa2.regime.classifier import RegimeClassifier
    classifier = RegimeClassifier()
    result = classifier.classify(context)
    return result.regime_id


def main() -> int:
    args = parse_args()

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        print("ERROR: yfinance and pandas are required. Run: pip install yfinance pandas")
        return 1

    from oa2.watchlist.builder import WATCHLIST
    from oa2.debaters.directional import DirectionalDebater
    from oa2.debaters.income import IncomeDebater
    from oa2.debaters.volatility import VolatilityDebater
    from oa2.debaters.flow import FlowDebater
    from oa2.debaters.sentiment import SentimentDebater
    from oa2.debaters.base import Direction
    from oa2.performance.bandit import BanditEngine
    from oa2.performance.storage import bandit_path

    debaters = {
        "directional": DirectionalDebater(),
        "income": IncomeDebater(),
        "volatility": VolatilityDebater(),
        "flow": FlowDebater(),
        "sentiment": SentimentDebater(),
    }

    tickers = args.tickers or list(WATCHLIST.keys())
    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.months * 31)

    print(f"Bandit warm-start: {args.months} months, {len(tickers)} tickers")
    print(f"Period: {start_date.date()} to {end_date.date()}")
    print("-" * 60)

    bandit = BanditEngine()

    # Track stats for reporting
    stats: dict[str, dict[str, list]] = {
        name: {"hits": [], "misses": [], "abstains": []}
        for name in debaters
    }

    for ticker in tickers:
        try:
            data = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=True)
        except Exception as e:
            print(f"  SKIP {ticker}: download failed ({e})")
            continue

        if data is None or len(data) < 22:
            print(f"  SKIP {ticker}: insufficient data ({len(data) if data is not None else 0} rows)")
            continue

        closes_raw = data["Close"]
        highs_raw = data["High"]
        lows_raw = data["Low"]

        if hasattr(closes_raw, "squeeze"):
            closes_raw = closes_raw.squeeze()
            highs_raw = highs_raw.squeeze()
            lows_raw = lows_raw.squeeze()

        closes = [float(x) for x in closes_raw.tolist() if x == x]  # drop NaN
        highs = [float(x) for x in highs_raw.tolist() if x == x]
        lows = [float(x) for x in lows_raw.tolist() if x == x]
        volumes = [0.0] * len(closes)

        n_days = 0
        for i in range(20, len(closes) - 1):
            context = _build_context(ticker, i, closes, highs, lows, volumes)
            regime_id = _classify_regime(context)

            # Next-day direction (the label)
            next_close = closes[i + 1]
            current_close = closes[i]
            pct_change = (next_close - current_close) / current_close
            if pct_change > 0.001:
                true_direction = Direction.BULLISH
            elif pct_change < -0.001:
                true_direction = Direction.BEARISH
            else:
                true_direction = Direction.NEUTRAL

            # Run each debater and score
            for name, debater in debaters.items():
                try:
                    opinion = debater.debate(context)
                except Exception:
                    stats[name]["abstains"].append(regime_id)
                    continue

                if opinion.conviction < 0.01 or opinion.direction == Direction.NEUTRAL:
                    stats[name]["abstains"].append(regime_id)
                    continue

                hit = opinion.direction == true_direction

                bandit.update(name, regime_id, hit=hit)

                if hit:
                    stats[name]["hits"].append(regime_id)
                else:
                    stats[name]["misses"].append(regime_id)

            n_days += 1
            if args.verbose and n_days % 20 == 0:
                print(f"  {ticker}: processed {n_days} days")

        print(f"  {ticker}: {n_days} trading days processed")

    # Report
    print()
    print("=" * 60)
    print("Per-debater warm-start results")
    print("=" * 60)
    for name in debaters:
        hits = len(stats[name]["hits"])
        misses = len(stats[name]["misses"])
        abstains = len(stats[name]["abstains"])
        total = hits + misses
        rate = hits / total if total > 0 else 0.0
        print(f"  {name:14s}: {hits:4d} hits / {misses:4d} misses / {abstains:4d} abstains  hit_rate={rate:.3f}")

    print()
    print("Bandit posteriors (alpha, beta) per arm:")
    print(f"  {'arm':30s}  alpha   beta   mean")
    for (name, regime_id), posterior in sorted(bandit._posteriors.items()):
        print(f"  {name+':regime_'+str(regime_id):30s}  {posterior.alpha:5.1f}  {posterior.beta:5.1f}  {posterior.mean():.3f}")

    if args.dry_run:
        print()
        print("DRY RUN — posteriors NOT saved.")
        return 0

    path = bandit_path()
    bandit.save(path)
    print()
    print(f"Posteriors saved to: {path}")
    print("Bandit will load these on next startup (BanditEngine.load()).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
