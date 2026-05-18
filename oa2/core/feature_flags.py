"""Feature flags for staged rollout of v2 components.

Each new component ships in shadow mode (compute but don't act) before
cutover. Flags are read at module import; restart to change.
"""

from __future__ import annotations

import os


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# Phase 1 — Debaters
DEBATERS_ENABLED = _flag("OA2_FLAG_DEBATERS", default=False)

# Phase 2 — Regime classifier
REGIME_CLASSIFIER_ENABLED = _flag("OA2_FLAG_REGIME", default=False)

# Phase 3 — Consensus engine
CONSENSUS_ENGINE_ENABLED = _flag("OA2_FLAG_CONSENSUS", default=False)
CONSENSUS_SHADOW_LOG = _flag("OA2_FLAG_CONSENSUS_SHADOW", default=True)

# Phase 4 — Thompson bandit
BANDIT_ENABLED = _flag("OA2_FLAG_BANDIT", default=False)
BANDIT_USE_POSTERIOR_MEAN = _flag("OA2_FLAG_BANDIT_MEAN", default=True)

# Phase 5 — Dealer agent
DEALER_AGENT_ENABLED = _flag("OA2_FLAG_DEALER", default=False)
DEALER_SHADOW_LOG = _flag("OA2_FLAG_DEALER_SHADOW", default=True)

# Phase 8 — Event risk agent
EVENT_RISK_AGENT_ENABLED = _flag("OA2_FLAG_EVENT_RISK", default=False)

# A/B comparison harness vs v1
AB_COMPARE_WITH_V1 = _flag("OA2_FLAG_AB_V1", default=False)

# Phase A — Signal integrity (production readiness)
# A1: Flow debater honest abstention — always enforced, no flag needed (code-level change)
# A2: Bandit warm-start — run scripts/bandit_warmstart.py manually; posteriors load automatically
# A3: EWMA correlation matrix — replaces hardcoded _fixed_correlation() in consensus engine
EWMA_CORR_ENABLED = _flag("OA2_FLAG_EWMA_CORR", default=True)

# Phase B — Sizing engine (oa2/sizing/: kelly.py, limits.py, cvar.py)
SIZING_ENGINE_ENABLED = _flag("OA2_FLAG_SIZING", default=False)

# Phase C — Exit engine (oa2/execution/: exit.py, monitor.py, roll.py)
EXIT_ENGINE_ENABLED = _flag("OA2_FLAG_EXIT", default=False)

# Phase D — Regime enhancements
SESSION_OVERLAY_ENABLED = _flag("OA2_FLAG_SESSION", default=False)
CROSS_ASSET_REGIME_ENABLED = _flag("OA2_FLAG_CROSS_ASSET", default=False)

# Phase E — Real flow data
# E1: vendor selection (manual decision — see ROADMAP.md Phase E)
# E2: flow adapter layer — always available; quality depends on vendor configured
FLOW_ADAPTER_SOURCE = os.getenv("OA2_FLOW_SOURCE", "yfinance")   # "yfinance" | "unusual_whales" | "tradier"
FLOW_ADAPTER_ENABLED = _flag("OA2_FLAG_FLOW_ADAPTER", default=False)
# E3: expiration-aware flow
EXPIRY_FLOW_ENABLED = _flag("OA2_FLAG_EXPIRY_FLOW", default=False)

# Phase F — Backtesting harness
# F1: historical replay (run via: python scripts/backtest.py)
# F2: bandit validation (included in backtest output)
# F3: A/B vs v1 baseline (AB_COMPARE_WITH_V1 flag above)
BACKTEST_ENABLED = _flag("OA2_FLAG_BACKTEST", default=False)


def all_flags() -> dict[str, bool]:
    return {
        # Phases 1-5
        "DEBATERS_ENABLED": DEBATERS_ENABLED,
        "REGIME_CLASSIFIER_ENABLED": REGIME_CLASSIFIER_ENABLED,
        "CONSENSUS_ENGINE_ENABLED": CONSENSUS_ENGINE_ENABLED,
        "CONSENSUS_SHADOW_LOG": CONSENSUS_SHADOW_LOG,
        "BANDIT_ENABLED": BANDIT_ENABLED,
        "BANDIT_USE_POSTERIOR_MEAN": BANDIT_USE_POSTERIOR_MEAN,
        "DEALER_AGENT_ENABLED": DEALER_AGENT_ENABLED,
        "DEALER_SHADOW_LOG": DEALER_SHADOW_LOG,
        "EVENT_RISK_AGENT_ENABLED": EVENT_RISK_AGENT_ENABLED,
        "AB_COMPARE_WITH_V1": AB_COMPARE_WITH_V1,
        # Phase A
        "EWMA_CORR_ENABLED": EWMA_CORR_ENABLED,
        # Phase B
        "SIZING_ENGINE_ENABLED": SIZING_ENGINE_ENABLED,
        # Phase C
        "EXIT_ENGINE_ENABLED": EXIT_ENGINE_ENABLED,
        # Phase D
        "SESSION_OVERLAY_ENABLED": SESSION_OVERLAY_ENABLED,
        "CROSS_ASSET_REGIME_ENABLED": CROSS_ASSET_REGIME_ENABLED,
        # Phase E
        "FLOW_ADAPTER_ENABLED": FLOW_ADAPTER_ENABLED,
        "EXPIRY_FLOW_ENABLED": EXPIRY_FLOW_ENABLED,
        # Phase F
        "BACKTEST_ENABLED": BACKTEST_ENABLED,
    }
