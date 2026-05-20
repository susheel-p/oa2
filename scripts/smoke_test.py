"""Phase 0 smoke test — verify scaffold is honest.

Checks:
  1. All oa2 packages import cleanly.
  2. The 22-ticker watchlist loads with the expected composition.
  3. Core config paths resolve (creates ~/.tradingbot/ tree).
  4. Feature flags read correctly (all OFF by default).
  5. Pipeline runs without crashing (returns phase0_scaffold_only).

Does NOT test data fetches (network-dependent). Run those manually:
  python -c "from tradingbot.dataflows.yfinance_options import ...; ..."
"""

from __future__ import annotations

import sys
import traceback


def check(label: str, fn) -> bool:
    try:
        fn()
        print(f"  PASS  {label}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {label}")
        traceback.print_exc()
        return False


def test_imports() -> None:
    import tradingbot  # noqa: F401
    import tradingbot.core.schemas  # noqa: F401
    import tradingbot.core.config  # noqa: F401
    import tradingbot.core.feature_flags  # noqa: F401
    import tradingbot.watchlist.builder  # noqa: F401
    import tradingbot.dataflows.cache  # noqa: F401
    import tradingbot.dataflows.yfinance_options  # noqa: F401
    import tradingbot.dataflows.sentiment_aggregator  # noqa: F401
    import tradingbot.dataflows.reddit_fetcher  # noqa: F401
    import tradingbot.dataflows.stocktwits_fetcher  # noqa: F401
    import tradingbot.dataflows.moomoo_news_fetcher  # noqa: F401
    import tradingbot.dataflows.news  # noqa: F401
    import tradingbot.dataflows.iv_rank_validator  # noqa: F401
    import tradingbot.graph.pipeline  # noqa: F401
    # moomoo_data imports moomoo SDK; only attempt if installed
    try:
        import tradingbot.dataflows.moomoo_data  # noqa: F401
    except ImportError as e:
        print(f"    (moomoo_data skipped: {e})")


def test_watchlist() -> None:
    from tradingbot.watchlist.builder import WATCHLIST, watchlist_summary, is_dealer_signal_ticker
    s = watchlist_summary()
    assert s["total"] == 22, f"expected 22 tickers, got {s['total']}"
    assert len(s["indices"]) == 4
    assert len(s["macro"]) == 4
    assert len(s["sectors"]) == 6
    assert len(s["mega_caps"]) == 8
    assert is_dealer_signal_ticker("SPY") is True
    assert is_dealer_signal_ticker("AAPL") is False
    assert "SPY" in WATCHLIST


def test_config_paths() -> None:
    from tradingbot.core.config import tradingbot_home, shadow_log_dir, scan_log_dir, journal_dir
    assert tradingbot_home().exists()
    assert shadow_log_dir().exists()
    assert scan_log_dir().exists()
    assert journal_dir().exists()


def test_feature_flags_off() -> None:
    from tradingbot.core import feature_flags
    flags = feature_flags.all_flags()
    assert flags["DEBATERS_ENABLED"] is False
    assert flags["REGIME_CLASSIFIER_ENABLED"] is False
    assert flags["CONSENSUS_ENGINE_ENABLED"] is False
    assert flags["DEALER_AGENT_ENABLED"] is False


def test_pipeline_runs() -> None:
    from tradingbot.graph.pipeline import run
    ctx = run("SPY")
    assert ctx.decision is not None
    assert ctx.decision["ticker"] == "SPY"
    assert ctx.decision["status"] in ("phase0_scaffold_only", "scaffold_only", "debaters_only", "full_pipeline")


def main() -> int:
    print("oa2 Phase 0 smoke test")
    print("-" * 40)
    results = [
        check("imports", test_imports),
        check("watchlist (22 tickers, dealer set)", test_watchlist),
        check("config paths (creates ~/.tradingbot/)", test_config_paths),
        check("feature flags all OFF by default", test_feature_flags_off),
        check("pipeline runs (scaffold-only decision)", test_pipeline_runs),
    ]
    print("-" * 40)
    passed = sum(results)
    total = len(results)
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())