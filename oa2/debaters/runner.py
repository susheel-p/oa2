"""Debater orchestration — run all 5 debaters, collect opinions, log."""

from __future__ import annotations

from typing import Any

from oa2.debaters.base import DebaterOpinion
from oa2.debaters.directional import DirectionalDebater
from oa2.debaters.income import IncomeDebater
from oa2.debaters.volatility import VolatilityDebater
from oa2.debaters.flow import FlowDebater
from oa2.debaters.sentiment import SentimentDebater
from oa2.learning.debater_logger import log_opinion


class DebaterEnsemble:
    """Runs all 5 debaters on a shared context, returns opinions."""

    def __init__(self):
        self.debaters = {
            "directional": DirectionalDebater(),
            "income": IncomeDebater(),
            "volatility": VolatilityDebater(),
            "flow": FlowDebater(),
            "sentiment": SentimentDebater(),
        }

    def run(self, context: dict[str, Any], log_to_disk: bool = True) -> list[DebaterOpinion]:
        """Run all debaters on context, optionally log opinions.

        Args:
            context: dict with market data, regime, setup, strategy, etc.
            log_to_disk: if True, append each opinion to debater_logs/opinions.jsonl

        Returns:
            list of 5 DebaterOpinion objects (one per debater)
        """
        opinions = []
        ticker = context.get("ticker", "UNKNOWN")
        trade_id = context.get("trade_id")  # optional, filled in later

        for name, debater in self.debaters.items():
            opinion = debater.debate(context)
            opinions.append(opinion)

            if log_to_disk:
                try:
                    log_opinion(opinion, ticker, trade_id)
                except Exception as e:
                    # Silently skip logging failures — don't block the pipeline
                    pass

        return opinions

    def opinions_summary(self, opinions: list[DebaterOpinion]) -> dict[str, Any]:
        """Summarize opinions for debugging."""
        return {
            "count": len(opinions),
            "debaters": [
                {
                    "name": op.debater_name,
                    "direction": op.direction.value,
                    "conviction": op.conviction,
                }
                for op in opinions
            ],
        }