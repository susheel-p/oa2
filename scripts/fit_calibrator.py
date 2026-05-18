"""Fit the p_bull calibrator from the latest backtest results.

Reads ~/.oa2/backtest/results_*.json (latest by mtime), extracts (p_bull, hit)
pairs from non-NEUTRAL days, and fits the Calibrator. Saves to the path
returned by `default_calibrator_path()`.

Usage:
    python scripts/fit_calibrator.py
    python scripts/fit_calibrator.py --results results_20260518.json --mode isotonic
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oa2.consensus.calibration import Calibrator, default_calibrator_path
from oa2.core.config import oa2_home


def load_latest_backtest() -> Path:
    btdir = oa2_home() / "backtest"
    files = sorted(btdir.glob("results_*.json"))
    if not files:
        raise FileNotFoundError(f"No backtest results found in {btdir}")
    return files[-1]


def extract_pairs(results: dict) -> tuple[list[float], list[int]]:
    """From a backtest result, extract (p_bull, hit) where hit is 1 if consensus direction matched outcome."""
    ps, hits = [], []
    for d in results.get("days", []):
        direction = d.get("consensus_direction")
        outcome = d.get("outcome")
        p_bull = d.get("p_bull")
        if direction in (None, "NEUTRAL") or outcome in (None, "NEUTRAL"):
            continue
        if p_bull is None:
            continue
        # For BEARISH calls, the relevant "edge" probability fed to Kelly was 1 - p_bull.
        # But for calibrating p_bull itself, we want to predict "underlying went BULLISH".
        hit_bullish = 1 if outcome == "BULLISH" else 0
        ps.append(float(p_bull))
        hits.append(hit_bullish)
    return ps, hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=str, default=None, help="Path to a specific backtest JSON")
    ap.add_argument("--mode", choices=["platt", "isotonic"], default="platt")
    ap.add_argument("--min-samples", type=int, default=50)
    args = ap.parse_args()

    path = Path(args.results) if args.results else load_latest_backtest()
    print(f"Reading backtest results: {path}")
    data = json.loads(path.read_text())

    ps, hits = extract_pairs(data)
    print(f"Extracted {len(ps)} (p_bull, hit) pairs from non-NEUTRAL days.")
    if len(ps) < args.min_samples:
        print(f"  Below min_samples={args.min_samples} threshold — calibrator will fit in identity mode.")

    cal = Calibrator(min_samples=args.min_samples, mode=args.mode)
    state = cal.fit(ps, hits)
    print(f"  mode={state.mode}")
    print(f"  n_samples={state.n_samples}")
    print(f"  brier_before={state.brier_before}  brier_after={state.brier_after}")
    if state.mode == "platt":
        print(f"  a={state.a:.4f}  b={state.b:.4f}")

    out = default_calibrator_path()
    cal.save(out)
    print(f"Saved calibrator -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
