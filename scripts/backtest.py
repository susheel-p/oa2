"""Historical backtest — Phase F1 + F2 + F3.

Replays N months of daily price data through the full oa2 pipeline
and measures per-debater accuracy, consensus Sharpe, and A/B comparison
against a simple directional baseline (v1 proxy).

Design:
    1. Batch-download all price history in one yfinance call per ticker.
    2. Replay day-by-day from the cached DataFrame — no further network calls.
    3. For each day: classify regime, compute HV-based IV proxy, run debaters.
    4. Check next-day close-to-close direction as outcome.
    5. Aggregate: accuracy by regime, confusion matrix, Sharpe, A/B table.

Output:
    JSON report → ~/.oa2/backtest/results_<timestamp>.json
    Human-readable summary → stdout

Usage:
    python scripts/backtest.py
    python scripts/backtest.py --months 6 --tickers SPY QQQ IWM DIA
    python scripts/backtest.py --months 3 --verbose
    python scripts/backtest.py --dry-run        # print config, exit

Phase F2 validation:
    Checks that income debater accuracy > 0.5 in high-IV regimes,
    directional debater accuracy > 0.5 in trending regimes.

Phase F3 A/B:
    Compares v2 consensus against a SimpleBaseline (EMA20 > EMA50 → BULLISH,
    else BEARISH). The gap between v2 Sharpe and baseline Sharpe is the
    paper-cutover gate metric.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DayResult:
    """Outcome of one debater-run day."""
    date: str
    ticker: str
    regime_id: int
    regime_label: str            # e.g., "VOL_EXP_TREND"
    consensus_direction: str     # BULLISH | BEARISH | NEUTRAL
    consensus_score: float       # normalized [0, 1]
    p_bull: float
    next_day_return: float       # close-to-close return
    outcome: str                 # BULLISH | BEARISH | NEUTRAL (actual)
    debater_opinions: dict[str, str]    # {debater: direction}
    debater_convictions: dict[str, float]
    # baseline (v1 proxy)
    baseline_direction: str = "NEUTRAL"
    iv_rank: float = 0.50


@dataclass
class BacktestMetrics:
    """Aggregated backtest results."""
    months_back: int
    tickers: list[str]
    total_days: int
    # Accuracy
    consensus_accuracy: float          # direction hit rate on non-neutral consensus days
    debater_accuracy: dict[str, float] # per debater
    # By regime
    accuracy_by_regime: dict[str, float]
    consensus_accuracy_by_regime: dict[str, float]
    # Sharpe
    v2_sharpe: float
    baseline_sharpe: float
    sharpe_improvement: float
    # Confusion: predicted vs actual (count)
    confusion: dict[str, dict[str, int]]
    # Phase F2 validation
    f2_validation: dict[str, Any]
    # Paper cutover gate
    cutover_ready: bool
    cutover_reason: str


# ---------------------------------------------------------------------------
# Regime label helpers
# ---------------------------------------------------------------------------

def _regime_label(vol_state: str, trend_state: str) -> str:
    vol_abbr = {
        "VOL_COMPRESSION": "VOL_COMP",
        "NORMAL": "NORMAL",
        "VOL_EXPANSION": "VOL_EXP",
        "CRISIS": "CRISIS",
    }
    trend_abbr = {
        "TRENDING": "TREND",
        "MEAN_REVERTING": "REVERT",
        "NEUTRAL_TREND": "NEUTRAL",
    }
    return f"{vol_abbr.get(vol_state, vol_state)}_{trend_abbr.get(trend_state, trend_state)}"


# ---------------------------------------------------------------------------
# Pure computation helpers (testable without network)
# ---------------------------------------------------------------------------

def compute_daily_context(
    ticker: str,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    date_str: str,
    vix_series: list[float],
    day_idx: int,
) -> dict[str, Any]:
    """Build a pipeline context dict from pre-fetched price arrays.

    Uses the prices available up to and including day_idx.
    All computations are pure Python — no network calls.

    Args:
        ticker: ticker symbol
        closes: list of daily close prices (oldest first)
        highs, lows, volumes: parallel arrays
        date_str: ISO date string for this day
        vix_series: VIX close prices parallel to closes
        day_idx: index into the arrays for "today"

    Returns:
        context dict ready for RegimeClassifier + debaters
    """
    if day_idx < 20:
        return {}

    price = closes[day_idx]
    prev_close = closes[day_idx - 1]
    high = highs[day_idx]
    low = lows[day_idx]
    vol = volumes[day_idx]
    vix = vix_series[day_idx] if day_idx < len(vix_series) else 20.0

    # VWAP proxy
    vwap = (high + low + price) / 3.0

    # EMAs (exponential moving averages on closes up to today)
    window = closes[:day_idx + 1]
    ema_20 = _ema(window, 20)
    ema_50 = _ema(window, 50) if day_idx >= 50 else ema_20

    # RSI-14
    rsi = _rsi(window, 14)

    # ATR-14
    atr = _atr(closes, highs, lows, day_idx, 14)

    # 20-day realized vol (annualized)
    log_rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(max(1, day_idx - 29), day_idx + 1)
        if closes[i - 1] > 0
    ]
    rv_30 = _std(log_rets) * math.sqrt(252) if len(log_rets) >= 5 else 0.20

    # IV rank from rolling 252-day HV range
    rv_window = []
    for i in range(max(30, day_idx - 251), day_idx + 1):
        if i < len(closes) and closes[i - 1] > 0:
            rets = [math.log(closes[j] / closes[j - 1]) for j in range(i - 29, i + 1) if j > 0 and closes[j - 1] > 0]
            if len(rets) >= 20:
                rv_window.append(_std(rets) * math.sqrt(252))

    if rv_window and max(rv_window) > min(rv_window) + 0.001:
        iv_rank = (rv_30 - min(rv_window)) / (max(rv_window) - min(rv_window))
        iv_rank = max(0.0, min(1.0, iv_rank))
    else:
        iv_rank = 0.50

    rv_iv_ratio = rv_30 / (rv_30 * 1.1) if rv_30 > 0 else 1.0  # simplified proxy

    # 20-day price slope
    prices_20d = closes[max(0, day_idx - 19): day_idx + 1]

    # Avg volume for unusual volume check
    avg_vol = sum(volumes[max(0, day_idx - 29): day_idx + 1]) / 30

    # MTF alignment: compare EMA_20 trend direction on daily vs ema_50 direction
    if day_idx >= 21:
        ema_20_yesterday = _ema(closes[:day_idx], 20)
        slope_20 = (ema_20 - ema_20_yesterday) / max(ema_20_yesterday, 0.01)
        mtf_alignment = slope_20 * 100  # scale for readability
    else:
        mtf_alignment = 0.0

    return {
        "ticker": ticker,
        "analysis_date": date_str,
        "current_price": price,
        "vwap": vwap,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "rsi": rsi,
        "atr": atr,
        "prior_close": prev_close,
        "volume": vol,
        "avg_volume": avg_vol,
        "prices_20d": prices_20d,
        "iv_rank": iv_rank,
        "vol_regime": {
            "iv_rank": iv_rank,
            "rv_iv_ratio": rv_iv_ratio,
            "vix": vix,
        },
        "realized_vol": rv_30,
        "setup": {
            "multi_timeframe_alignment": mtf_alignment,
        },
    }


def evaluate_direction(next_day_return: float, threshold: float = 0.001) -> str:
    """Classify a daily return into BULLISH / BEARISH / NEUTRAL."""
    if next_day_return > threshold:
        return "BULLISH"
    elif next_day_return < -threshold:
        return "BEARISH"
    return "NEUTRAL"


def compute_sharpe(daily_returns: list[float], annualization: float = math.sqrt(252)) -> float:
    """Compute annualized Sharpe ratio from a list of daily returns.

    Assumes risk-free rate = 0 (simple Sharpe).
    Returns 0.0 if insufficient data or zero volatility.
    """
    if len(daily_returns) < 5:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    std = _std(daily_returns)
    if std < 1e-9:
        return 0.0
    return (mean / std) * annualization


def compute_accuracy(results: list[DayResult], debater: str | None = None) -> float:
    """Compute direction hit rate.

    If debater is None, uses consensus_direction.
    Only counts days where predicted direction is non-neutral.
    """
    correct = 0
    total = 0
    for r in results:
        predicted = r.debater_opinions.get(debater) if debater else r.consensus_direction
        if predicted == "NEUTRAL":
            continue
        if r.outcome == "NEUTRAL":
            continue
        total += 1
        if predicted == r.outcome:
            correct += 1
    return correct / total if total > 0 else 0.0


def compute_accuracy_by_regime(results: list[DayResult], debater: str | None = None) -> dict[str, float]:
    """Compute direction accuracy grouped by regime label."""
    by_regime: dict[str, list[DayResult]] = {}
    for r in results:
        by_regime.setdefault(r.regime_label, []).append(r)
    return {label: compute_accuracy(days, debater) for label, days in by_regime.items()}


def confusion_matrix(results: list[DayResult]) -> dict[str, dict[str, int]]:
    """Compute predicted vs actual direction counts."""
    matrix: dict[str, dict[str, int]] = {}
    for r in results:
        pred = r.consensus_direction
        actual = r.outcome
        matrix.setdefault(pred, {})
        matrix[pred][actual] = matrix[pred].get(actual, 0) + 1
    return matrix


def f2_validation(results: list[DayResult]) -> dict[str, Any]:
    """Phase F2: validate that debater accuracy matches expected regime fit.

    Checks:
        - income debater accuracy > 0.5 in VOL_EXP and CRISIS regimes
        - directional debater accuracy > 0.5 in TREND regimes
    """
    high_iv_labels = {"VOL_EXP_TREND", "VOL_EXP_REVERT", "VOL_EXP_NEUTRAL", "CRISIS_TREND", "CRISIS_REVERT", "CRISIS_NEUTRAL"}
    trend_labels = {"VOL_COMP_TREND", "NORMAL_TREND", "VOL_EXP_TREND"}

    high_iv_days = [r for r in results if r.regime_label in high_iv_labels]
    trend_days = [r for r in results if r.regime_label in trend_labels]

    income_acc_high_iv = compute_accuracy(high_iv_days, "income")
    directional_acc_trend = compute_accuracy(trend_days, "directional")

    return {
        "income_accuracy_in_high_iv": income_acc_high_iv,
        "directional_accuracy_in_trending": directional_acc_trend,
        "income_passes": income_acc_high_iv >= 0.50,
        "directional_passes": directional_acc_trend >= 0.50,
        "high_iv_days": len(high_iv_days),
        "trend_days": len(trend_days),
    }


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _ema(values: list[float], span: int) -> float:
    """Compute EMA with given span, returning last value."""
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    """Compute RSI from close prices."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]
    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period
    if avg_loss < 1e-9:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _atr(closes: list[float], highs: list[float], lows: list[float], day_idx: int, period: int = 14) -> float:
    """Compute ATR at day_idx."""
    trs = []
    for i in range(max(1, day_idx - period + 1), day_idx + 1):
        if i >= len(closes):
            break
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        trs.append(max(hl, hc, lc))
    return sum(trs) / len(trs) if trs else 0.0


def _std(values: list[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# Simple baseline (v1 proxy for F3)
# ---------------------------------------------------------------------------

def baseline_direction(context: dict[str, Any]) -> str:
    """Simple EMA-crossover baseline representing v1-style logic.

    Rules:
        EMA20 > EMA50 AND VIX < 25 → BULLISH
        EMA20 < EMA50 OR  VIX > 30 → BEARISH
        Otherwise → NEUTRAL
    """
    ema_20 = context.get("ema_20", 0.0)
    ema_50 = context.get("ema_50", 0.0)
    vix = context.get("vol_regime", {}).get("vix", 20.0)

    if ema_20 > ema_50 and vix < 25:
        return "BULLISH"
    elif ema_20 < ema_50 or vix > 30:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    tickers: list[str],
    months_back: int = 6,
    verbose: bool = False,
) -> tuple[list[DayResult], BacktestMetrics]:
    """Run the full backtest.

    Args:
        tickers: list of ticker symbols to backtest
        months_back: how many months of history to replay
        verbose: print per-day debug lines

    Returns:
        (results_list, metrics) tuple
    """
    try:
        import yfinance as yf
        import numpy as np
    except ImportError:
        print("ERROR: yfinance and numpy required. Run: pip install yfinance numpy", file=sys.stderr)
        sys.exit(1)

    from oa2.regime.classifier import RegimeClassifier
    from oa2.consensus.engine import ConsensusEngine
    from oa2.debaters.directional import DirectionalDebater
    from oa2.debaters.flow import FlowDebater
    from oa2.debaters.income import IncomeDebater
    from oa2.debaters.volatility import VolatilityDebater
    from oa2.debaters.sentiment import SentimentDebater
    from oa2.regime.state import VolState, TrendState

    classifier = RegimeClassifier()
    debaters = [
        DirectionalDebater(),
        IncomeDebater(),
        VolatilityDebater(),
        FlowDebater(),
        SentimentDebater(),
    ]

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=months_back * 31)

    print(f"[backtest] Downloading {len(tickers)} tickers × {months_back} months...")

    # Batch download
    price_data: dict[str, Any] = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                hist = t.history(start=start_date.isoformat(), end=end_date.isoformat(), auto_adjust=True)
            if hist.empty:
                print(f"  WARNING: no data for {ticker} — skipping")
                continue
            price_data[ticker] = hist
            print(f"  {ticker}: {len(hist)} days downloaded")
        except Exception as exc:
            print(f"  WARNING: failed to download {ticker}: {exc} — skipping")

    # Download VIX for regime classification
    vix_data = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            vix_hist = yf.Ticker("^VIX").history(
                start=start_date.isoformat(), end=end_date.isoformat(), auto_adjust=True
            )
        vix_data = {str(d.date()): float(v) for d, v in zip(vix_hist.index, vix_hist["Close"])}
    except Exception:
        pass

    all_results: list[DayResult] = []

    for ticker, hist in price_data.items():
        hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        closes = [float(v) for v in hist["Close"].tolist()]
        highs = [float(v) for v in hist["High"].tolist()]
        lows = [float(v) for v in hist["Low"].tolist()]
        volumes = [float(v) for v in hist["Volume"].tolist()]
        dates = [str(d.date()) for d in hist.index]

        # Align VIX to ticker dates
        vix_series = [vix_data.get(d, 20.0) for d in dates]

        for i in range(50, len(closes) - 1):   # need 50 days warmup; need +1 for outcome
            date_str = dates[i]
            ctx = compute_daily_context(
                ticker, closes, highs, lows, volumes, date_str, vix_series, i
            )
            if not ctx:
                continue

            # Classify regime
            try:
                regime_result = classifier.classify(ctx)
                regime_id = regime_result.regime_id
                regime_label = _regime_label(regime_result.vol_state.value, regime_result.trend_state.value)
            except Exception:
                regime_id = 5
                regime_label = "NORMAL_NEUTRAL"

            # Wire regime context into debater context
            ctx["vol_regime"]["iv_rank"] = ctx.get("iv_rank", 0.50)
            ctx["regime_id"] = regime_id
            ctx["rsi"] = ctx.get("rsi", 50.0)

            # Run debaters (flow and sentiment will abstain without real data)
            opinions = []
            debater_dirs = {}
            debater_convs = {}
            for debater in debaters:
                try:
                    opinion = debater.debate(ctx)
                    opinions.append(opinion)
                    debater_dirs[opinion.debater_name] = opinion.direction.value if hasattr(opinion.direction, "value") else str(opinion.direction)
                    debater_convs[opinion.debater_name] = opinion.conviction
                except Exception:
                    pass

            # Consensus
            try:
                engine = ConsensusEngine(regime=str(regime_id))
                consensus = engine.aggregate(opinions)
                consensus_dir = consensus.direction.value if hasattr(consensus.direction, "value") else str(consensus.direction)
                consensus_score = consensus.score
                p_bull = consensus.p_bull
            except Exception:
                consensus_dir = "NEUTRAL"
                consensus_score = 0.5
                p_bull = 0.5

            # Next-day outcome
            next_ret = (closes[i + 1] - closes[i]) / closes[i]
            outcome = evaluate_direction(next_ret)

            # Baseline
            bl_dir = baseline_direction(ctx)

            result = DayResult(
                date=date_str,
                ticker=ticker,
                regime_id=regime_id,
                regime_label=regime_label,
                consensus_direction=consensus_dir,
                consensus_score=consensus_score,
                p_bull=p_bull,
                next_day_return=next_ret,
                outcome=outcome,
                debater_opinions=debater_dirs,
                debater_convictions=debater_convs,
                baseline_direction=bl_dir,
                iv_rank=ctx.get("iv_rank", 0.50),
            )
            all_results.append(result)

            if verbose:
                print(f"  {date_str} {ticker}: regime={regime_label} consensus={consensus_dir} outcome={outcome} ret={next_ret:+.2%}")

    metrics = _compute_metrics(all_results, months_back, tickers)
    return all_results, metrics


def _compute_metrics(results: list[DayResult], months_back: int, tickers: list[str]) -> BacktestMetrics:
    """Aggregate DayResult list into BacktestMetrics."""
    if not results:
        return BacktestMetrics(
            months_back=months_back, tickers=tickers, total_days=0,
            consensus_accuracy=0.0, debater_accuracy={}, accuracy_by_regime={},
            consensus_accuracy_by_regime={}, v2_sharpe=0.0, baseline_sharpe=0.0,
            sharpe_improvement=0.0, confusion={}, f2_validation={},
            cutover_ready=False, cutover_reason="No data",
        )

    debater_names = list({k for r in results for k in r.debater_opinions})

    # Sharpe: trade $1 when consensus direction is non-neutral, sign × return
    def _signed_returns(get_dir):
        rets = []
        for r in results:
            d = get_dir(r)
            if d == "BULLISH":
                rets.append(r.next_day_return)
            elif d == "BEARISH":
                rets.append(-r.next_day_return)
        return rets

    v2_rets = _signed_returns(lambda r: r.consensus_direction)
    bl_rets = _signed_returns(lambda r: r.baseline_direction)

    v2_sharpe = compute_sharpe(v2_rets)
    bl_sharpe = compute_sharpe(bl_rets)

    f2 = f2_validation(results)

    acc_by_regime = compute_accuracy_by_regime(results)
    consensus_acc = compute_accuracy(results)

    debater_acc = {d: compute_accuracy(results, d) for d in debater_names}

    cutover_ready = (
        v2_sharpe >= bl_sharpe and
        f2.get("income_passes", False) and
        f2.get("directional_passes", False)
    )
    if cutover_ready:
        cutover_reason = f"v2 Sharpe ({v2_sharpe:.2f}) >= baseline ({bl_sharpe:.2f}); F2 checks pass."
    else:
        reasons = []
        if v2_sharpe < bl_sharpe:
            reasons.append(f"v2 Sharpe ({v2_sharpe:.2f}) < baseline ({bl_sharpe:.2f})")
        if not f2.get("income_passes"):
            reasons.append(f"income debater accuracy in high-IV regimes ({f2.get('income_accuracy_in_high_iv', 0):.2%}) < 50%")
        if not f2.get("directional_passes"):
            reasons.append(f"directional debater accuracy in trending ({f2.get('directional_accuracy_in_trending', 0):.2%}) < 50%")
        cutover_reason = "; ".join(reasons) or "Unknown"

    return BacktestMetrics(
        months_back=months_back,
        tickers=tickers,
        total_days=len(results),
        consensus_accuracy=consensus_acc,
        debater_accuracy=debater_acc,
        accuracy_by_regime=acc_by_regime,
        consensus_accuracy_by_regime=compute_accuracy_by_regime(results, None),
        v2_sharpe=v2_sharpe,
        baseline_sharpe=bl_sharpe,
        sharpe_improvement=v2_sharpe - bl_sharpe,
        confusion=confusion_matrix(results),
        f2_validation=f2,
        cutover_ready=cutover_ready,
        cutover_reason=cutover_reason,
    )


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def print_report(metrics: BacktestMetrics) -> None:
    """Print human-readable summary to stdout."""
    print("\n" + "=" * 60)
    print("OA2 BACKTEST REPORT")
    print("=" * 60)
    print(f"Period: {metrics.months_back} months | Tickers: {', '.join(metrics.tickers)}")
    print(f"Total trading days evaluated: {metrics.total_days}")
    print()
    print(f"Consensus accuracy (non-neutral only): {metrics.consensus_accuracy:.1%}")
    print()
    print("Debater accuracy:")
    for name, acc in sorted(metrics.debater_accuracy.items()):
        bar = "#" * int(acc * 20)
        print(f"  {name:<15} {acc:.1%}  |{bar:<20}|")
    print()
    print("Accuracy by regime:")
    for label, acc in sorted(metrics.accuracy_by_regime.items()):
        print(f"  {label:<25} {acc:.1%}")
    print()
    print(f"v2  Sharpe: {metrics.v2_sharpe:+.3f}")
    print(f"v1  Sharpe: {metrics.baseline_sharpe:+.3f}  (simple EMA-crossover baseline)")
    print(f"Improvement: {metrics.sharpe_improvement:+.3f}")
    print()
    print("F2 Validation:")
    f2 = metrics.f2_validation
    def _tick(v): return "PASS" if v else "FAIL"
    print(f"  income accuracy in high-IV: {f2.get('income_accuracy_in_high_iv', 0):.1%} [{_tick(f2.get('income_passes'))}]")
    print(f"  directional in trending:    {f2.get('directional_accuracy_in_trending', 0):.1%} [{_tick(f2.get('directional_passes'))}]")
    print()
    print(f"Paper cutover gate: {'READY' if metrics.cutover_ready else 'NOT READY'}")
    print(f"  {metrics.cutover_reason}")
    print()
    print("Confusion matrix (predicted → actual):")
    for pred, actuals in sorted(metrics.confusion.items()):
        for actual, count in sorted(actuals.items()):
            print(f"  {pred:<10} → {actual:<10} : {count}")
    print("=" * 60)


def save_report(metrics: BacktestMetrics, results: list[DayResult]) -> Path:
    """Save JSON report to ~/.oa2/backtest/."""
    from dataclasses import asdict

    out_dir = Path.home() / ".oa2" / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"results_{ts}.json"

    # DayResult is not automatically serializable (dataclass)
    def _day_to_dict(r: DayResult) -> dict:
        return {
            "date": r.date, "ticker": r.ticker, "regime_id": r.regime_id,
            "regime_label": r.regime_label, "consensus_direction": r.consensus_direction,
            "consensus_score": r.consensus_score, "p_bull": r.p_bull,
            "next_day_return": r.next_day_return, "outcome": r.outcome,
            "debater_opinions": r.debater_opinions, "debater_convictions": r.debater_convictions,
            "baseline_direction": r.baseline_direction, "iv_rank": r.iv_rank,
        }

    report = {
        "generated": datetime.datetime.now().isoformat(),
        "metrics": asdict(metrics),
        "days": [_day_to_dict(r) for r in results],
    }

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    return out_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

DEFAULT_TICKERS = ["SPY", "QQQ", "IWM", "DIA"]


def main() -> None:
    parser = argparse.ArgumentParser(description="oa2 backtest harness (Phase F)")
    parser.add_argument("--months", type=int, default=6, help="Months of history to replay (default: 6)")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS, help="Tickers to backtest")
    parser.add_argument("--verbose", action="store_true", help="Print per-day output")
    parser.add_argument("--dry-run", action="store_true", help="Print config and exit")
    args = parser.parse_args()

    if args.dry_run:
        print(f"[dry-run] months={args.months}, tickers={args.tickers}")
        return

    results, metrics = run_backtest(
        tickers=args.tickers,
        months_back=args.months,
        verbose=args.verbose,
    )

    print_report(metrics)
    out_path = save_report(metrics, results)
    print(f"\nFull report saved → {out_path}")


if __name__ == "__main__":
    main()
