"""Feature flags for staged rollout of v2 components.

Each new component ships in shadow mode (compute but don't act) before
cutover. Flags are read at module import; restart to change.

Phase 0 ships all flags OFF except imports — pipeline raises
NotImplementedError until Phase 1 ports debaters.
"""

from __future__ import annotations

import os


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# Phase 1
DEBATERS_ENABLED = _flag("OA2_FLAG_DEBATERS", default=False)

# Phase 2
REGIME_CLASSIFIER_ENABLED = _flag("OA2_FLAG_REGIME", default=False)

# Phase 3
CONSENSUS_ENGINE_ENABLED = _flag("OA2_FLAG_CONSENSUS", default=False)
CONSENSUS_SHADOW_LOG = _flag("OA2_FLAG_CONSENSUS_SHADOW", default=True)

# Phase 4
BANDIT_ENABLED = _flag("OA2_FLAG_BANDIT", default=False)
BANDIT_USE_POSTERIOR_MEAN = _flag("OA2_FLAG_BANDIT_MEAN", default=True)

# Phase 5
DEALER_AGENT_ENABLED = _flag("OA2_FLAG_DEALER", default=False)
DEALER_SHADOW_LOG = _flag("OA2_FLAG_DEALER_SHADOW", default=True)

# Phase 8
EVENT_RISK_AGENT_ENABLED = _flag("OA2_FLAG_EVENT_RISK", default=False)

# A/B comparison harness vs v1
AB_COMPARE_WITH_V1 = _flag("OA2_FLAG_AB_V1", default=False)


def all_flags() -> dict[str, bool]:
    return {
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
    }