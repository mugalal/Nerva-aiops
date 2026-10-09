"""
Isolation Forest detector (M2 runbook, Day 4).

Learns what "normal" looks like from healthy telemetry only, then scores how
unusual a new reading is. It never sees a fault while training.

How Isolation Forest works, briefly: it builds many random trees that keep
asking random yes/no questions about the features ("is latency above 310?").
A reading that can be cut off from the rest in only a few questions is
unusual; an ordinary reading takes many. The average number of questions
becomes a score.

Scores here run from 0 to 1, higher = more unusual. (scikit-learn returns the
negative of this from `score_samples`; this module flips the sign so the
number can go straight into AnomalyEvent.score, which the contract keeps in
0..1.) Around 0.4 to 0.5 is typical for a normal reading.

One model per service by default, so each service is compared with its own
normal. `per_service=False` pools every service into a single model; that
exists to test whether the per-service reference is what makes the
difference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
from sklearn.ensemble import IsolationForest

from .features import FEATURE_ORDER

# Key the single pooled model is stored under when per_service=False.
POOLED_KEY = "*"

# A model trained on fewer healthy readings than this would just be noise.
MIN_TRAINING_SAMPLES = 50

# Features that are RECORDED but never SCORED by the ruler. The replica count is
# changed on purpose by the system's own remediation (M4 scales a service from 1
# to 10 copies to cure a traffic spike). A reference learned while there was
# always 1 copy would otherwise call the cure a fault, and keep calling it one
# for as long as the extra copies run. They still appear in the evidence log.
DEFAULT_CONTEXT_FEATURES = ("replica_count",)


def context_columns(names: Sequence[str] = DEFAULT_CONTEXT_FEATURES) -> tuple[int, ...]:
    """Column positions of the named features in the 8-feature vector."""
    unknown = [n for n in names if n not in FEATURE_ORDER]
    if unknown:
        raise ValueError(f"not features of the standard vector: {unknown}")
    return tuple(FEATURE_ORDER.index(n) for n in names)


class UnknownServiceError(KeyError):
    """There is no model for this service, because it has no healthy history."""


class IsolationForestDetector:
    def __init__(
        self,
        per_service: bool = True,
        n_estimators: int = 200,
        max_samples: int = 256,
        random_state: int = 0,
        min_samples: int = MIN_TRAINING_SAMPLES,
    ) -> None:
        self.per_service = per_service
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.random_state = random_state
        self.min_samples = min_samples
        self._models: dict[str, IsolationForest] = {}

    # -- training ---------------------------------------------------------

    def _new_model(self, n_available: int) -> IsolationForest:
        # Each tree is grown on a random sample. Asking for more readings than
        # exist (a service with little history) just makes scikit-learn warn.
        return IsolationForest(
            n_estimators=self.n_estimators,
            max_samples=min(self.max_samples, n_available),
            random_state=self.random_state,
        )

    def fit(self, healthy: Mapping[str, Sequence[Sequence[float]]]) -> "IsolationForestDetector":
        """Train on healthy readings: {service: rows of 8 feature values}.

        Raises ValueError if any service has too little history.
        """
        if not healthy:
            raise ValueError("no training data given")

        matrices = {service: np.asarray(rows, dtype=float) for service, rows in healthy.items()}
        for service, matrix in matrices.items():
            if len(matrix) < self.min_samples:
                raise ValueError(
                    f"not enough healthy history for {service!r}: "
                    f"{len(matrix)} readings, need at least {self.min_samples}"
                )

        self._models = {}
        if self.per_service:
            for service, matrix in matrices.items():
                self._models[service] = self._new_model(len(matrix)).fit(matrix)
        else:
            pooled = np.vstack(list(matrices.values()))
            self._models[POOLED_KEY] = self._new_model(len(pooled)).fit(pooled)
        return self

    # -- scoring ----------------------------------------------------------

    @property
    def services(self) -> list[str]:
        return sorted(self._models)

    def _model_for(self, service: str) -> IsolationForest:
        key = service if self.per_service else POOLED_KEY
        try:
            return self._models[key]
        except KeyError:
            raise UnknownServiceError(
                f"no model for service {service!r}; it needs healthy history first"
            ) from None

    def score_many(self, service: str, rows: Sequence[Sequence[float]]) -> np.ndarray:
        """Score several readings of one service. Returns values in 0..1.

        A reading with any missing or non-finite value scores 0.0: missing
        data must never become a confident anomaly (runbook, Day 12).
        """
        model = self._model_for(service)
        matrix = np.asarray(rows, dtype=float).reshape(len(rows), -1)
        usable = np.isfinite(matrix).all(axis=1)

        scores = np.zeros(len(matrix))
        if usable.any():
            scores[usable] = np.clip(-model.score_samples(matrix[usable]), 0.0, 1.0)
        return scores

    def score(self, service: str, vector: Sequence[float]) -> float:
        """Score one reading (its 8 features, in features.FEATURE_ORDER)."""
        return float(self.score_many(service, [vector])[0])


class ZScoreDetector:
    """A plain statistical reference per service. Not machine learning.

    For each service it records the average and the usual wobble (standard
    deviation) of every feature in healthy history. A new reading is scored by
    its worst feature: how many "usual wobbles" away from that service's
    average it is. 3 means it is three wobbles from normal.

    It exists as a control. If it does as well as the forest, then "knowing
    each service's normal" is what helps, not the forest.

    The replica count is the exception to "every feature counts": it is
    changed on purpose by remediation, so it is not scored (see
    DEFAULT_CONTEXT_FEATURES).

    A feature that never varied in healthy history (an error rate that is
    always exactly 0) has a wobble of 0, which would
    make any change infinitely large. It is given a small minimum wobble
    instead, so it is still measured: if it moves, that counts as a big
    change. (An earlier version skipped such features, which would have made
    it blind to errors on a service that never errors when healthy.)
    """

    # Squashes a distance in wobbles d to the 0..1 range as d / (d + SQUASH),
    # so the score can go straight into AnomalyEvent.score. Distance 5 maps to
    # 0.5, distance 10 to about 0.67.
    SQUASH = 5.0

    # The smallest wobble ever used: 1% of the feature's average, and never
    # below 0.001 (so a ratio that is always 0 still has a scale).
    MIN_RELATIVE_SPREAD = 0.01
    MIN_ABSOLUTE_SPREAD = 0.001

    def __init__(
        self,
        min_samples: int = MIN_TRAINING_SAMPLES,
        ignore_columns: Sequence[int] | None = None,
    ) -> None:
        """`ignore_columns` are columns that are never scored (see
        DEFAULT_CONTEXT_FEATURES). None means: for the standard 8-feature
        vector, ignore the default context features; for any other width,
        ignore nothing. Pass `()` to score every column."""
        self.min_samples = min_samples
        self.ignore_columns = None if ignore_columns is None else tuple(ignore_columns)
        self._stats: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._counts: dict[str, int] = {}

    def _ignored(self, width: int) -> tuple[int, ...]:
        if self.ignore_columns is not None:
            return self.ignore_columns
        return context_columns() if width == len(FEATURE_ORDER) else ()

    def fit(self, healthy: Mapping[str, Sequence[Sequence[float]]]) -> "ZScoreDetector":
        if not healthy:
            raise ValueError("no training data given")
        stats = {}
        counts = {}
        for service, rows in healthy.items():
            matrix = np.asarray(rows, dtype=float)
            if len(matrix) < self.min_samples:
                raise ValueError(
                    f"not enough healthy history for {service!r}: "
                    f"{len(matrix)} readings, need at least {self.min_samples}"
                )
            mean = matrix.mean(axis=0)
            spread = np.maximum(
                matrix.std(axis=0, ddof=1),
                np.maximum(self.MIN_RELATIVE_SPREAD * np.abs(mean), self.MIN_ABSOLUTE_SPREAD),
            )
            stats[service] = (mean, spread)
            counts[service] = len(matrix)
        self._stats = stats
        self._counts = counts
        return self

    @property
    def services(self) -> list[str]:
        return sorted(self._stats)

    # -- saving and loading the reference ---------------------------------

    def to_dict(self) -> dict:
        """The whole reference as plain JSON-able data: per service, the
        average and the usual wobble of each feature, and how many readings it
        came from. Small enough to commit."""
        return {
            "services": {
                service: {
                    "n": self._counts.get(service, 0),
                    "mean": [float(x) for x in mean],
                    "spread": [float(x) for x in spread],
                }
                for service, (mean, spread) in self._stats.items()
            }
        }

    @classmethod
    def from_dict(cls, data: dict, ignore_columns: Sequence[int] | None = None) -> "ZScoreDetector":
        detector = cls(ignore_columns=ignore_columns)
        for service, entry in data["services"].items():
            detector._stats[service] = (
                np.asarray(entry["mean"], dtype=float),
                np.asarray(entry["spread"], dtype=float),
            )
            detector._counts[service] = int(entry.get("n", 0))
        return detector

    def explain(self, service: str, vector: Sequence[float]) -> list[float]:
        """How many usual wobbles each feature is from normal, in feature
        order. Missing or non-finite readings give zeros."""
        return [float(x) for x in self._per_feature(service, [vector])[0]]

    def _per_feature(self, service: str, rows: Sequence[Sequence[float]]) -> np.ndarray:
        try:
            mean, spread = self._stats[service]
        except KeyError:
            raise UnknownServiceError(
                f"no reference for service {service!r}; it needs healthy history first"
            ) from None
        matrix = np.asarray(rows, dtype=float).reshape(len(rows), -1)
        usable = np.isfinite(matrix).all(axis=1)
        out = np.zeros(matrix.shape)
        if usable.any():
            out[usable] = np.abs(matrix[usable] - mean) / spread
            for column in self._ignored(matrix.shape[1]):
                out[:, column] = 0.0          # recorded elsewhere, never scored
        return out

    def distance_many(self, service: str, rows: Sequence[Sequence[float]]) -> np.ndarray:
        """Distance from normal, in usual wobbles (0 or more): the worst
        feature. Rows with a missing or non-finite value get 0.0, never a
        confident anomaly."""
        return self._per_feature(service, rows).max(axis=1)

    def score_many(self, service: str, rows: Sequence[Sequence[float]]) -> np.ndarray:
        distance = self.distance_many(service, rows)
        return distance / (distance + self.SQUASH)

    def score(self, service: str, vector: Sequence[float]) -> float:
        return float(self.score_many(service, [vector])[0])

    @classmethod
    def wobbles_of(cls, score: float) -> float:
        """Turn a 0..1 score back into a distance in usual wobbles."""
        return cls.SQUASH * score / (1.0 - score) if score < 1.0 else float("inf")
