"""Knowledge base — rolling per-ticker / regime / debater accuracy.

Phase 2 of the RAG learning loop.

Stored at ~/.tradingbot/knowledge_base.json. Updated nightly by scripts/daily_learn.py.
Consumed at decision time by oa2/learning/rag_context.py (Phase 3).

Schema (version 1):
{
  "schema_version": 1,
  "last_updated": ISO datetime,
  "window_days": 60,
  "n_outcomes_used": int,
  "tickers":    {ticker: TickerStats},
  "regimes":    {regime_label: RegimeStats},
  "debaters":   {name: DebaterStats},
  "structures": {structure: StructureStats},
}

All multipliers are derived from rolling stats with safeguards:
  - Min n_trades >= MIN_OBS_FOR_MULT before any non-1.0 multiplier
  - Multipliers capped to [MIN_MULT, MAX_MULT]
  - Blacklist requires both hit_rate AND dollar_weighted_win_rate below thresholds
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# --- Tunables (safeguards against overfitting) -------------------------------

SCHEMA_VERSION = 1
DEFAULT_WINDOW_DAYS = 60
MIN_OBS_FOR_MULT = 20          # below this, multiplier defaults to 1.0
MIN_OBS_FOR_BLACKLIST = 30     # require more data before blacklisting

MIN_MULT = 0.50
MAX_MULT = 1.30

BLACKLIST_HIT_RATE = 0.43
BLACKLIST_DOLLAR_WEIGHTED = 0.45

REGIME_BLOCK_HIT_RATE = 0.45   # block regimes worse than this


# --- Per-dimension stats dataclasses -----------------------------------------

@dataclass
class TickerStats:
    n_trades: int = 0
    hits: int = 0
    total_pnl: float = 0.0
    sum_pnl_pct: float = 0.0
    n_dollar_winners: int = 0       # trades with pnl > 0
    rolling_30d_hits: int = 0
    rolling_30d_n: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_trades if self.n_trades else 0.5

    @property
    def avg_pnl_pct(self) -> float:
        return self.sum_pnl_pct / self.n_trades if self.n_trades else 0.0

    @property
    def dollar_weighted_win_rate(self) -> float:
        return self.n_dollar_winners / self.n_trades if self.n_trades else 0.5

    @property
    def rolling_30d_hit_rate(self) -> float:
        return self.rolling_30d_hits / self.rolling_30d_n if self.rolling_30d_n else 0.5


@dataclass
class RegimeStats:
    n_trades: int = 0
    hits: int = 0
    total_pnl: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_trades if self.n_trades else 0.5

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.n_trades if self.n_trades else 0.0


@dataclass
class DebaterStats:
    n_votes: int = 0
    hits: int = 0
    by_conviction: dict[str, dict[str, int]] = field(default_factory=dict)  # bucket -> {n,hits}

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_votes if self.n_votes else 0.5


@dataclass
class StructureStats:
    n_trades: int = 0
    hits: int = 0
    total_pnl: float = 0.0
    sum_max_loss: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_trades if self.n_trades else 0.5

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.n_trades if self.n_trades else 0.0


# --- Derived multipliers / blacklist -----------------------------------------

def _ticker_multiplier(stats: TickerStats) -> float:
    """Map ticker accuracy -> conviction multiplier in [MIN_MULT, MAX_MULT]."""
    if stats.n_trades < MIN_OBS_FOR_MULT:
        return 1.0
    # Linear: hit_rate 0.43 -> 0.60, 0.55 -> 1.08, 0.65 -> 1.48 (capped)
    mult = 0.6 + (stats.hit_rate - 0.43) * 4.0
    return max(MIN_MULT, min(MAX_MULT, mult))


def _ticker_blacklisted(stats: TickerStats) -> bool:
    if stats.n_trades < MIN_OBS_FOR_BLACKLIST:
        return False
    return (
        stats.hit_rate < BLACKLIST_HIT_RATE
        and stats.dollar_weighted_win_rate < BLACKLIST_DOLLAR_WEIGHTED
    )


def _regime_multiplier(stats: RegimeStats) -> float:
    if stats.n_trades < MIN_OBS_FOR_MULT:
        return 1.0
    if stats.hit_rate < REGIME_BLOCK_HIT_RATE:
        return 0.5
    mult = 0.6 + (stats.hit_rate - 0.45) * 4.0
    return max(MIN_MULT, min(MAX_MULT, mult))


def _regime_blocked(stats: RegimeStats) -> bool:
    if stats.n_trades < MIN_OBS_FOR_MULT:
        return False
    # Mean-reverting regimes specifically blocked when underperforming
    return False  # use multiplier instead of hard block in v1


# --- KB main type ------------------------------------------------------------

@dataclass
class BetaPosteriorData:
    """Serializable Beta(alpha, beta) posterior for Thompson sampling."""
    alpha: float = 1.0
    beta: float = 1.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def std(self) -> float:
        """Standard deviation of Beta distribution."""
        n = self.alpha + self.beta
        return ((self.alpha * self.beta) / (n * n * (n + 1))) ** 0.5


@dataclass
class KnowledgeBase:
    schema_version: int = SCHEMA_VERSION
    last_updated: str = ""
    window_days: int = DEFAULT_WINDOW_DAYS
    n_outcomes_used: int = 0
    tickers: dict[str, TickerStats] = field(default_factory=dict)
    regimes: dict[str, RegimeStats] = field(default_factory=dict)
    debaters: dict[str, DebaterStats] = field(default_factory=dict)
    structures: dict[str, StructureStats] = field(default_factory=dict)
    posteriors: dict[str, dict[int, BetaPosteriorData]] = field(default_factory=dict)  # {debater_name: {regime_id: BetaPosterior}}

    # --- Read API (for pipeline / RAG context) ---

    def ticker_multiplier(self, ticker: str) -> float:
        stats = self.tickers.get(ticker.upper())
        return _ticker_multiplier(stats) if stats else 1.0

    def is_blacklisted(self, ticker: str) -> bool:
        stats = self.tickers.get(ticker.upper())
        return _ticker_blacklisted(stats) if stats else False

    def regime_multiplier(self, regime_label: str) -> float:
        if not regime_label:
            return 1.0
        stats = self.regimes.get(regime_label.lower())
        return _regime_multiplier(stats) if stats else 1.0

    def get_posterior(self, debater_name: str, regime_id: int) -> BetaPosteriorData:
        """Get Thompson posterior for (debater, regime). Returns default if missing."""
        if debater_name not in self.posteriors:
            return BetaPosteriorData()
        return self.posteriors[debater_name].get(regime_id, BetaPosteriorData())

    def summary_rows(self) -> list[tuple[str, float, float, int]]:
        """Return (ticker, hit_rate, avg_pnl_pct, n_trades) sorted by hit_rate desc."""
        rows = [(t, s.hit_rate, s.avg_pnl_pct, s.n_trades) for t, s in self.tickers.items()]
        return sorted(rows, key=lambda r: -r[1])

    # --- Persistence ---

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "last_updated": self.last_updated,
            "window_days": self.window_days,
            "n_outcomes_used": self.n_outcomes_used,
            "tickers": {
                t: {**asdict(s),
                    "hit_rate": round(s.hit_rate, 4),
                    "avg_pnl_pct": round(s.avg_pnl_pct, 4),
                    "dollar_weighted_win_rate": round(s.dollar_weighted_win_rate, 4),
                    "rolling_30d_hit_rate": round(s.rolling_30d_hit_rate, 4),
                    "conviction_multiplier": round(_ticker_multiplier(s), 4),
                    "blacklisted": _ticker_blacklisted(s)}
                for t, s in self.tickers.items()
            },
            "regimes": {
                r: {**asdict(s),
                    "hit_rate": round(s.hit_rate, 4),
                    "avg_pnl": round(s.avg_pnl, 2),
                    "conviction_multiplier": round(_regime_multiplier(s), 4)}
                for r, s in self.regimes.items()
            },
            "debaters": {
                n: {**asdict(s), "hit_rate": round(s.hit_rate, 4)}
                for n, s in self.debaters.items()
            },
            "structures": {
                k: {**asdict(s),
                    "hit_rate": round(s.hit_rate, 4),
                    "avg_pnl": round(s.avg_pnl, 2)}
                for k, s in self.structures.items()
            },
            "posteriors": {
                debater: {
                    str(regime_id): {"alpha": round(post.alpha, 4), "beta": round(post.beta, 4), "mean": round(post.mean, 4)}
                    for regime_id, post in regimes.items()
                }
                for debater, regimes in self.posteriors.items()
            },
        }

    def save(self, path: Path) -> None:
        """Atomic write: write to .tmp then rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        # Atomic: write to temp file in same dir, then rename
        fd, tmp_path = tempfile.mkstemp(prefix=".kb_", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: Path) -> KnowledgeBase:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        kb = cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            last_updated=data.get("last_updated", ""),
            window_days=data.get("window_days", DEFAULT_WINDOW_DAYS),
            n_outcomes_used=data.get("n_outcomes_used", 0),
        )
        for t, s in (data.get("tickers") or {}).items():
            kb.tickers[t] = TickerStats(
                n_trades=s.get("n_trades", 0),
                hits=s.get("hits", 0),
                total_pnl=s.get("total_pnl", 0.0),
                sum_pnl_pct=s.get("sum_pnl_pct", 0.0),
                n_dollar_winners=s.get("n_dollar_winners", 0),
                rolling_30d_hits=s.get("rolling_30d_hits", 0),
                rolling_30d_n=s.get("rolling_30d_n", 0),
            )
        for r, s in (data.get("regimes") or {}).items():
            kb.regimes[r] = RegimeStats(
                n_trades=s.get("n_trades", 0),
                hits=s.get("hits", 0),
                total_pnl=s.get("total_pnl", 0.0),
            )
        for n, s in (data.get("debaters") or {}).items():
            kb.debaters[n] = DebaterStats(
                n_votes=s.get("n_votes", 0),
                hits=s.get("hits", 0),
                by_conviction=s.get("by_conviction", {}),
            )
        for k, s in (data.get("structures") or {}).items():
            kb.structures[k] = StructureStats(
                n_trades=s.get("n_trades", 0),
                hits=s.get("hits", 0),
                total_pnl=s.get("total_pnl", 0.0),
                sum_max_loss=s.get("sum_max_loss", 0.0),
            )
        for debater, regimes in (data.get("posteriors") or {}).items():
            kb.posteriors[debater] = {}
            for regime_id_str, post_data in regimes.items():
                regime_id = int(regime_id_str)
                kb.posteriors[debater][regime_id] = BetaPosteriorData(
                    alpha=post_data.get("alpha", 1.0),
                    beta=post_data.get("beta", 1.0),
                )
        return kb


def default_kb_path() -> Path:
    """Default disk location for the knowledge base."""
    from tradingbot.core.config import tradingbot_home
    return tradingbot_home() / "knowledge_base.json"


# --- Builders --------------------------------------------------------------

def build_from_outcomes(outcome_records: list[dict], window_days: int = DEFAULT_WINDOW_DAYS) -> KnowledgeBase:
    """Build a fresh KB from a list of resolved-outcome dicts (from outcomes_history.jsonl).

    Outcomes outside the rolling window are skipped.
    """
    kb = KnowledgeBase(window_days=window_days)
    kb.last_updated = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    cutoff = (datetime.date.today() - datetime.timedelta(days=window_days)).isoformat()
    rolling_cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    n_used = 0
    for o in outcome_records:
        if o.get("decision_date", "") < cutoff:
            continue
        n_used += 1
        ticker = (o.get("ticker") or "").upper()
        hit = bool(o.get("direction_hit"))
        pnl = float(o.get("total_pnl_dollars", 0.0))
        pnl_pct = float(o.get("pnl_pct_of_max_loss", 0.0))
        regime = (o.get("regime_label") or "").lower()
        structure = (o.get("structure") or "").lower()

        # Ticker
        ts = kb.tickers.setdefault(ticker, TickerStats())
        ts.n_trades += 1
        ts.hits += int(hit)
        ts.total_pnl += pnl
        ts.sum_pnl_pct += pnl_pct
        if pnl > 0:
            ts.n_dollar_winners += 1
        if o.get("decision_date", "") >= rolling_cutoff:
            ts.rolling_30d_n += 1
            ts.rolling_30d_hits += int(hit)

        # Regime
        rs = kb.regimes.setdefault(regime, RegimeStats())
        rs.n_trades += 1
        rs.hits += int(hit)
        rs.total_pnl += pnl

        # Structure
        ss = kb.structures.setdefault(structure, StructureStats())
        ss.n_trades += 1
        ss.hits += int(hit)
        ss.total_pnl += pnl
        ss.sum_max_loss += float(o.get("max_loss", 0.0))

    kb.n_outcomes_used = n_used
    return kb


def build_from_backtest(backtest_path: Path, window_days: int = DEFAULT_WINDOW_DAYS) -> KnowledgeBase:
    """Bootstrap a KB from a backtest results JSON.

    The backtest doesn't compute P&L per-trade, so structures stats and
    pnl-related fields stay at zero; hit_rates and counts are populated.
    """
    data = json.loads(backtest_path.read_text())
    kb = KnowledgeBase(window_days=window_days)
    kb.last_updated = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    rolling_cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    n_used = 0
    for day in data.get("days", []):
        if day.get("consensus_direction") == "NEUTRAL":
            continue
        if day.get("outcome") == "NEUTRAL":
            continue
        n_used += 1
        ticker = (day.get("ticker") or "").upper()
        hit = day.get("outcome") == day.get("consensus_direction")
        regime = (day.get("regime_label") or "").lower()

        ts = kb.tickers.setdefault(ticker, TickerStats())
        ts.n_trades += 1
        ts.hits += int(hit)
        if day.get("date", "") >= rolling_cutoff:
            ts.rolling_30d_n += 1
            ts.rolling_30d_hits += int(hit)

        rs = kb.regimes.setdefault(regime, RegimeStats())
        rs.n_trades += 1
        rs.hits += int(hit)

    kb.n_outcomes_used = n_used
    return kb
