"""p_bull calibration — Platt scaling with isotonic fallback.

The consensus engine produces `p_bull = sigmoid(consensus_score × N_eff × 2)`.
That's a hand-tuned squash, not a calibrated probability. Feeding an
uncalibrated probability into Kelly is the textbook way to oversize.

This module provides a Calibrator that learns a monotone transform
from raw `p_bull` to empirically calibrated probability using historical
(p_bull, hit) pairs from the debater_log + outcome data.

Default behaviour (identity):
    When fewer than `min_samples` observations exist, the calibrator is
    a no-op — `transform(p) == p`. Kelly sees the raw sigmoid output.
    This prevents shipping a calibrator with one trade of evidence.

Fit modes:
    "platt":    logistic regression — p_cal = sigmoid(a × p_raw + b).
                Fast, smooth, two parameters, robust to small samples.
    "isotonic": non-parametric monotone fit via pool-adjacent-violators.
                More flexible, requires more samples to be useful.

Default fit mode is Platt because the smooth output works well with
Kelly's sensitivity to small probability changes.

Persistence:
    save()/load() use JSON; small footprint, human-readable diagnostics.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


_MIN_SAMPLES_DEFAULT = 50
_PLATT_LR = 0.1
_PLATT_EPOCHS = 1000


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip01(p: float) -> float:
    return max(1e-6, min(1.0 - 1e-6, p))


@dataclass
class CalibratorState:
    """Fitted calibrator parameters + metadata."""
    mode: str = "identity"           # "identity" | "platt" | "isotonic"
    a: float = 1.0                   # Platt slope
    b: float = 0.0                   # Platt intercept
    isotonic_x: list[float] = field(default_factory=list)
    isotonic_y: list[float] = field(default_factory=list)
    n_samples: int = 0
    brier_before: float | None = None
    brier_after: float | None = None
    fit_timestamp: str | None = None


class Calibrator:
    """Calibrate raw consensus p_bull to empirical hit probability.

    Args:
        min_samples: minimum (p_raw, hit) pairs required to fit (default 50).
        mode: "platt" or "isotonic" — fit method when there is enough data.
    """

    def __init__(
        self,
        min_samples: int = _MIN_SAMPLES_DEFAULT,
        mode: Literal["platt", "isotonic"] = "platt",
    ):
        self.min_samples = min_samples
        self.preferred_mode = mode
        self.state = CalibratorState()

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def transform(self, p_raw: float) -> float:
        """Map raw p_bull → calibrated p_bull.

        Returns `p_raw` unchanged when the calibrator is in identity mode
        (insufficient training data, or no fit() has been called).
        """
        p_raw = _clip01(p_raw)

        if self.state.mode == "identity":
            return p_raw

        if self.state.mode == "platt":
            return _clip01(_sigmoid(self.state.a * p_raw + self.state.b))

        if self.state.mode == "isotonic" and self.state.isotonic_x:
            xs = self.state.isotonic_x
            ys = self.state.isotonic_y
            # Piecewise-linear interp; ranges are sorted by construction
            if p_raw <= xs[0]:
                return _clip01(ys[0])
            if p_raw >= xs[-1]:
                return _clip01(ys[-1])
            for i in range(len(xs) - 1):
                if xs[i] <= p_raw <= xs[i + 1]:
                    span = xs[i + 1] - xs[i]
                    if span <= 0:
                        return _clip01(ys[i])
                    frac = (p_raw - xs[i]) / span
                    return _clip01(ys[i] + frac * (ys[i + 1] - ys[i]))

        return p_raw

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, p_raws: list[float], hits: list[bool | int]) -> CalibratorState:
        """Fit the calibrator on observed (p_raw, hit) pairs.

        If there are fewer than `min_samples` samples, the calibrator
        remains in identity mode. Otherwise the chosen mode is fit and
        Brier score before/after is recorded for diagnostics.
        """
        import datetime as _dt
        n = min(len(p_raws), len(hits))
        if n < self.min_samples:
            self.state = CalibratorState(
                mode="identity",
                n_samples=n,
                fit_timestamp=_dt.datetime.utcnow().isoformat(),
            )
            return self.state

        ps = [_clip01(float(p_raws[i])) for i in range(n)]
        ys = [1.0 if hits[i] else 0.0 for i in range(n)]
        brier_before = self._brier(ps, ys)

        if self.preferred_mode == "platt":
            a, b = self._fit_platt(ps, ys)
            cal = [_sigmoid(a * p + b) for p in ps]
            self.state = CalibratorState(
                mode="platt",
                a=a, b=b,
                n_samples=n,
                brier_before=round(brier_before, 5),
                brier_after=round(self._brier(cal, ys), 5),
                fit_timestamp=_dt.datetime.utcnow().isoformat(),
            )
        else:
            xs, mono_ys = self._fit_isotonic(ps, ys)
            cal = [self._interp(p, xs, mono_ys) for p in ps]
            self.state = CalibratorState(
                mode="isotonic",
                isotonic_x=xs, isotonic_y=mono_ys,
                n_samples=n,
                brier_before=round(brier_before, 5),
                brier_after=round(self._brier(cal, ys), 5),
                fit_timestamp=_dt.datetime.utcnow().isoformat(),
            )
        return self.state

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _brier(ps: list[float], ys: list[float]) -> float:
        if not ps:
            return 0.0
        return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)

    @staticmethod
    def _fit_platt(ps: list[float], ys: list[float]) -> tuple[float, float]:
        """Logistic regression via batch gradient descent. Two parameters → fast."""
        a, b = 1.0, 0.0
        n = len(ps)
        for _ in range(_PLATT_EPOCHS):
            ga, gb = 0.0, 0.0
            for p, y in zip(ps, ys):
                z = a * p + b
                pred = _sigmoid(z)
                err = pred - y
                ga += err * p
                gb += err
            a -= _PLATT_LR * ga / n
            b -= _PLATT_LR * gb / n
        return a, b

    @staticmethod
    def _fit_isotonic(ps: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
        """Pool-adjacent-violators isotonic regression.

        Returns (sorted_x, monotone_y) suitable for piecewise-linear interp.
        """
        order = sorted(range(len(ps)), key=lambda i: ps[i])
        xs = [ps[i] for i in order]
        ws = [1.0] * len(order)
        vals = [ys[i] for i in order]

        # Pool adjacent violators
        i = 0
        while i < len(vals) - 1:
            if vals[i] <= vals[i + 1]:
                i += 1
                continue
            # Pool i and i+1
            total_w = ws[i] + ws[i + 1]
            pooled = (vals[i] * ws[i] + vals[i + 1] * ws[i + 1]) / total_w
            vals[i] = pooled
            ws[i] = total_w
            del vals[i + 1]
            del ws[i + 1]
            # The pooled x: use rightmost (so transform interpolates correctly)
            xs[i] = xs[i + 1]
            del xs[i + 1]
            if i > 0:
                i -= 1
        return xs, vals

    @staticmethod
    def _interp(p: float, xs: list[float], ys: list[float]) -> float:
        if not xs:
            return p
        if p <= xs[0]:
            return ys[0]
        if p >= xs[-1]:
            return ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= p <= xs[i + 1]:
                span = xs[i + 1] - xs[i]
                if span <= 0:
                    return ys[i]
                frac = (p - xs[i]) / span
                return ys[i] + frac * (ys[i + 1] - ys[i])
        return p

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": self.state.mode,
            "a": self.state.a,
            "b": self.state.b,
            "isotonic_x": self.state.isotonic_x,
            "isotonic_y": self.state.isotonic_y,
            "n_samples": self.state.n_samples,
            "brier_before": self.state.brier_before,
            "brier_after": self.state.brier_after,
            "fit_timestamp": self.state.fit_timestamp,
            "min_samples": self.min_samples,
            "preferred_mode": self.preferred_mode,
        }
        path.write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls, path: Path) -> Calibrator:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        cal = cls(
            min_samples=int(data.get("min_samples", _MIN_SAMPLES_DEFAULT)),
            mode=data.get("preferred_mode", "platt"),
        )
        cal.state = CalibratorState(
            mode=data.get("mode", "identity"),
            a=float(data.get("a", 1.0)),
            b=float(data.get("b", 0.0)),
            isotonic_x=list(data.get("isotonic_x", [])),
            isotonic_y=list(data.get("isotonic_y", [])),
            n_samples=int(data.get("n_samples", 0)),
            brier_before=data.get("brier_before"),
            brier_after=data.get("brier_after"),
            fit_timestamp=data.get("fit_timestamp"),
        )
        return cal


def default_calibrator_path() -> Path:
    """Default disk location for the persisted calibrator state."""
    from tradingbot.core.config import tradingbot_home
    return tradingbot_home() / "calibration" / "p_bull_calibrator.json"
