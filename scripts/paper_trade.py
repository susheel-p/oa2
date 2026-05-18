"""oa2 automated paper trading runner.

Fully autonomous — no human intervention required. Run once per day
(or on a schedule) to scan all 22 tickers, log signals, check open
position exits, write a daily summary, and push everything to GitHub.

Usage:
    python scripts/paper_trade.py                  # full run, all flags on
    python scripts/paper_trade.py --dry-run        # scan only, no GitHub push
    python scripts/paper_trade.py --tickers SPY QQQ  # subset of tickers
    python scripts/paper_trade.py --account-size 100000

Environment variables (all default ON for paper trading):
    OA2_FLAG_DEBATERS=1
    OA2_FLAG_REGIME=1
    OA2_FLAG_CONSENSUS=1
    OA2_FLAG_DEALER=1
    OA2_FLAG_BANDIT=1
    OA2_FLAG_SIZING=1
    OA2_FLAG_EXIT=1
    GITHUB_TOKEN=<your token>   (optional — skip push if absent)
    OA2_ACCOUNT_SIZE=50000      (override account size)
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import sys
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Force all feature flags ON before any oa2 import ──────────────────────────
for _flag in (
    "OA2_FLAG_DEBATERS",
    "OA2_FLAG_REGIME",
    "OA2_FLAG_CONSENSUS",
    "OA2_FLAG_DEALER",
    "OA2_FLAG_BANDIT",
    "OA2_FLAG_SIZING",
    "OA2_FLAG_EXIT",
):
    os.environ.setdefault(_flag, "1")

# ── oa2 imports ───────────────────────────────────────────────────────────────
from oa2.execution.monitor import PositionMonitor
from oa2.graph.pipeline import run as pipeline_run
from oa2.sizing.limits import GreeksBook
from oa2.watchlist.builder import WATCHLIST

ET = ZoneInfo("America/New_York")
REPO = "susheel-p/oa2"
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


# =============================================================================
# Helpers
# =============================================================================

def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ET)


def _today_str() -> str:
    return _now_et().strftime("%Y-%m-%d")


def _ts() -> str:
    return _now_et().strftime("%Y-%m-%dT%H:%M:%S%z")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def _is_market_day() -> bool:
    """Return False on weekends. Does not check public holidays."""
    return _now_et().weekday() < 5


# =============================================================================
# GitHub push
# =============================================================================

def _github_push(files: dict[str, str], commit_msg: str) -> bool:
    """Push one or more files to GitHub via API.

    Args:
        files: {path_in_repo: file_content_string}
        commit_msg: commit message

    Returns True on success, False on any failure.
    """
    try:
        import urllib.request
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            _log("GITHUB_TOKEN not set — skipping push.")
            return False

        # Get current master HEAD
        def _api(method: str, path: str, body: dict | None = None) -> dict:
            url = f"https://api.github.com/repos/{REPO}/{path}"
            data = json.dumps(body).encode() if body else None
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())

        head = _api("GET", "git/ref/heads/master")["object"]["sha"]
        base_tree = _api("GET", f"git/commits/{head}")["tree"]["sha"]

        tree_items = []
        for repo_path, content in files.items():
            blob = _api("POST", "git/blobs", {
                "content": base64.b64encode(content.encode()).decode(),
                "encoding": "base64",
            })
            tree_items.append({
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })

        new_tree = _api("POST", "git/trees", {"base_tree": base_tree, "tree": tree_items})
        new_commit = _api("POST", "git/commits", {
            "message": commit_msg,
            "tree": new_tree["sha"],
            "parents": [head],
        })
        _api("PATCH", "git/refs/heads/master", {"sha": new_commit["sha"]})
        _log(f"Pushed to GitHub: {new_commit['sha'][:7]} — {commit_msg}")
        return True

    except Exception as exc:
        _log(f"GitHub push failed: {exc}")
        return False


# =============================================================================
# Single-ticker scan
# =============================================================================

def _scan_ticker(
    ticker: str,
    book: GreeksBook,
    monitor: PositionMonitor,
    account_size: float,
) -> dict:
    """Run the full pipeline for one ticker. Never raises — errors are caught."""
    start = time.time()
    result: dict = {
        "ticker": ticker,
        "ts": _ts(),
        "status": "error",
        "error": None,
        "decision": None,
        "sizing": None,
        "exit_alerts": [],
        "duration_ms": 0,
    }
    try:
        ctx = pipeline_run(
            ticker=ticker,
            account_size=account_size,
            book=book,
            monitor=monitor,
        )
        result["status"] = ctx.decision.get("status", "unknown") if ctx.decision else "unknown"
        result["decision"] = ctx.decision
        result["sizing"] = ctx.sizing
        result["exit_alerts"] = ctx.open_position_exits or []
        result["regime"] = (
            ctx.attribution.get("regime", {}) if ctx.attribution else {}
        )
        result["consensus"] = (
            ctx.attribution.get("consensus", {}) if ctx.attribution else {}
        )
    except Exception as exc:
        result["error"] = traceback.format_exc()
        _log(f"  ERROR scanning {ticker}: {exc}")
    finally:
        result["duration_ms"] = round((time.time() - start) * 1000)
    return result


# =============================================================================
# Daily summary
# =============================================================================

def _build_summary(results: list[dict], account_size: float, book: GreeksBook) -> dict:
    approved = [r for r in results if r.get("status") == "sized_approved"]
    rejected = [r for r in results if r.get("status") == "sized_rejected"]
    errors   = [r for r in results if r.get("status") == "error"]
    exit_alerts = [a for r in results for a in r.get("exit_alerts", [])]

    return {
        "date": _today_str(),
        "run_ts": _ts(),
        "account_size": account_size,
        "tickers_scanned": len(results),
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "error_count": len(errors),
        "exit_alert_count": len(exit_alerts),
        "approved_tickers": [r["ticker"] for r in approved],
        "exit_alerts": exit_alerts,
        "book_state": book.summary(),
        "errors": [{"ticker": r["ticker"], "error": r["error"]} for r in errors],
    }


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="oa2 automated paper trading runner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan all tickers but do not push to GitHub")
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Override ticker list (default: all 22)")
    parser.add_argument("--account-size", type=float,
                        default=float(os.environ.get("OA2_ACCOUNT_SIZE", "50000")),
                        help="Account size in dollars (default $50,000)")
    parser.add_argument("--skip-weekend", action="store_true",
                        help="Exit silently on weekends")
    args = parser.parse_args()

    # Weekend guard
    if args.skip_weekend and not _is_market_day():
        _log("Weekend — skipping run.")
        sys.exit(0)

    tickers = args.tickers if args.tickers else WATCHLIST
    account_size = args.account_size
    today = _today_str()

    _log("=" * 60)
    _log(f"oa2 paper trading run — {today}")
    _log(f"Tickers: {len(tickers)}  |  Account: ${account_size:,.0f}")
    _log(f"Dry run: {args.dry_run}")
    _log("=" * 60)

    # Shared book and monitor across all tickers
    book = GreeksBook(account_size=account_size)
    monitor = PositionMonitor()

    # ── Scan all tickers ──────────────────────────────────────────────────────
    results: list[dict] = []
    log_path = LOG_DIR / f"paper_trade_{today}.jsonl"

    with open(log_path, "a") as log_file:
        for ticker in tickers:
            _log(f"  Scanning {ticker} ...")
            result = _scan_ticker(ticker, book, monitor, account_size)
            results.append(result)

            # Write each result immediately — never lose a scan on crash
            log_file.write(json.dumps(result) + "\n")
            log_file.flush()

            status = result["status"]
            contracts = (result.get("sizing") or {}).get("contracts", 0)
            n_exits = len(result.get("exit_alerts", []))

            if status == "sized_approved":
                _log(f"    ✓ APPROVED — {contracts} contract(s)")
            elif status == "sized_rejected":
                reason = (result.get("sizing") or {}).get("reject_reason", "")
                _log(f"    ✗ REJECTED — {reason[:60]}")
            elif status == "error":
                _log(f"    ✗ ERROR")
            else:
                _log(f"    — {status}")

            if n_exits:
                _log(f"    ⚠ {n_exits} exit alert(s) for open positions")

    # ── Build and write daily summary ─────────────────────────────────────────
    summary = _build_summary(results, account_size, book)
    summary_path = LOG_DIR / f"summary_{today}.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    _log("-" * 60)
    _log(f"Run complete: {summary['approved_count']} approved, "
         f"{summary['rejected_count']} rejected, "
         f"{summary['error_count']} errors, "
         f"{summary['exit_alert_count']} exit alerts")
    _log(f"Logs: {log_path}")
    _log(f"Summary: {summary_path}")

    # ── Push to GitHub ────────────────────────────────────────────────────────
    if not args.dry_run:
        _log("Pushing logs to GitHub ...")
        files_to_push = {
            f"logs/paper_trade_{today}.jsonl": log_path.read_text(),
            f"logs/summary_{today}.json": summary_path.read_text(),
        }
        _github_push(
            files_to_push,
            commit_msg=f"paper trade {today}: "
                       f"{summary['approved_count']} approved, "
                       f"{summary['exit_alert_count']} exit alerts",
        )
    else:
        _log("Dry run — GitHub push skipped.")

    _log("=" * 60)

    # Exit non-zero if any ticker errored, so the caller/scheduler knows
    if summary["error_count"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
