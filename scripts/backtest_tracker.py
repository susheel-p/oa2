"""Backtest results tracker and comparison tool.

Collects all backtest results and generates a comparison table showing
the evolution of the system across different configurations.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Any


def load_backtest_results(results_dir: Path = None) -> list[dict]:
    """Load all backtest JSON results from the results directory."""
    if results_dir is None:
        results_dir = Path.home() / ".tradingbot" / "backtest"

    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}", file=sys.stderr)
        return []

    results = []
    for result_file in sorted(results_dir.glob("results_*.json")):
        try:
            with open(result_file) as f:
                data = json.load(f)
                results.append({
                    "timestamp": result_file.stem.replace("results_", ""),
                    "file": result_file.name,
                    **data.get("metrics", {})
                })
        except Exception as e:
            print(f"Warning: failed to load {result_file}: {e}", file=sys.stderr)

    return results


def format_timestamp(ts_str: str) -> str:
    """Format timestamp from filename (YYYYMMDD_HHMMSS) to readable format."""
    try:
        dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return ts_str


def generate_comparison_table(results: list[dict]) -> str:
    """Generate a markdown comparison table of all backtest runs."""
    if not results:
        return "No backtest results found."

    # Build table header
    lines = [
        "# Backtest Results Comparison",
        "",
        "| # | Timestamp | Months | Sharpe | Baseline | Improvement | Consensus | Income | Direction | Volatility | Status |",
        "|---|-----------|--------|--------|----------|-------------|-----------|--------|-----------|------------|--------|",
    ]

    # Add rows
    for i, result in enumerate(results, 1):
        timestamp = format_timestamp(result.get("timestamp", ""))
        months = result.get("months_back", "?")
        v2_sharpe = result.get("v2_sharpe", 0)
        baseline_sharpe = result.get("baseline_sharpe", 0)
        improvement = result.get("sharpe_improvement", 0)
        consensus_acc = result.get("consensus_accuracy", 0)

        # F2 validation metrics
        f2 = result.get("f2_validation", {})
        income_acc = f2.get("income_trade_quality_accuracy", f2.get("income_accuracy_in_high_iv", 0))
        directional_acc = f2.get("directional_accuracy_in_trending", 0)
        volatility_acc = f2.get("volatility_trade_quality_accuracy", 0)

        # Determine status
        cutover_ready = result.get("cutover_ready", False)
        status = "[READY]" if cutover_ready else "[In Progress]"

        lines.append(
            f"| {i} | {timestamp} | {months} | {v2_sharpe:+.2f} | {baseline_sharpe:+.2f} | {improvement:+.2f} | "
            f"{consensus_acc:.1%} | {income_acc:.1%} | {directional_acc:.1%} | {volatility_acc:.1%} | {status} |"
        )

    return "\n".join(lines)


def generate_evolution_summary(results: list[dict]) -> str:
    """Generate a summary of system evolution across runs."""
    if len(results) < 2:
        return "Need at least 2 results for comparison."

    first = results[0]
    last = results[-1]

    first_sharpe = first.get("v2_sharpe", 0)
    last_sharpe = last.get("v2_sharpe", 0)
    baseline_sharpe = last.get("baseline_sharpe", 0)

    sharpe_improvement = last_sharpe - first_sharpe
    baseline_ratio = last_sharpe / baseline_sharpe if baseline_sharpe != 0 else 0

    summary = f"""
## System Evolution Summary

**Total runs:** {len(results)}

**First run (baseline):**
- Sharpe: {first_sharpe:+.3f}
- Consensus accuracy: {first.get('consensus_accuracy', 0):.1%}

**Latest run (current):**
- Sharpe: {last_sharpe:+.3f}
- Consensus accuracy: {last.get('consensus_accuracy', 0):.1%}
- Paper cutover: {"[READY]" if last.get('cutover_ready') else "[Not ready]"}

**Improvement:**
- Sharpe delta: {sharpe_improvement:+.3f} ({sharpe_improvement/abs(first_sharpe)*100:+.0f}%)
- vs Baseline: {baseline_ratio:.1f}x better ({(baseline_ratio-1)*100:+.0f}%)

**Phase F2 Validation (latest):**
- Income (trade_quality): {last.get('f2_validation', {}).get('income_trade_quality_accuracy', 0):.1%}
  {'[PASS]' if last.get('f2_validation', {}).get('income_passes') else '[FAIL]'}
- Directional (trending): {last.get('f2_validation', {}).get('directional_accuracy_in_trending', 0):.1%}
  {'[PASS]' if last.get('f2_validation', {}).get('directional_passes') else '[FAIL]'}
- Volatility (trade_quality): {last.get('f2_validation', {}).get('volatility_trade_quality_accuracy', 0):.1%}
  {'[PASS]' if last.get('f2_validation', {}).get('volatility_passes') else '[FAIL]'}
"""
    return summary.strip()


def save_comparison_report(results: list[dict], output_file: Path = None) -> Path:
    """Save comparison table and summary to a markdown file."""
    if output_file is None:
        results_dir = Path.home() / ".tradingbot" / "backtest"
        output_file = results_dir / "COMPARISON.md"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    content = f"""# OA2 Backtest Results Tracker

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{generate_comparison_table(results)}

{generate_evolution_summary(results)}

## Configuration Notes

- **Baseline:** EMA-20 > EMA-50 and VIX < 25 → BULLISH; EMA-20 < EMA-50 or VIX > 30 → BEARISH
- **Sharpe calculation:** Annualized, using daily returns signed by consensus direction
- **Period:** 12 months of historical daily data (SPY, QQQ, IWM, DIA)
- **Total trading days evaluated:** ~820 days per run

## Debater Meanings

- **Consensus Accuracy:** Direction hit rate (BULLISH/BEARISH only, skips NEUTRAL)
- **Income:** Trade-quality accuracy for premium-selling recommendations (%)
- **Direction:** Directional accuracy in trending regimes (%)
- **Volatility:** Trade-quality accuracy for vega-alignment recommendations (%)
"""

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    return output_file


def main():
    """Load results and generate comparison."""
    results = load_backtest_results()

    if not results:
        print("No backtest results found.", file=sys.stderr)
        sys.exit(1)

    print(generate_comparison_table(results))
    print()
    print(generate_evolution_summary(results))
    print()

    output_path = save_comparison_report(results)
    print(f"Comparison report saved to: {output_path}")


if __name__ == "__main__":
    main()
