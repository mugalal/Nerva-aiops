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

# Key the single pooled model is stored under when per_service=False.
POOLED_KEY = "*"

# A model trained on fewer healthy readings than this would just be noise.
MIN_TRAINING_SAMPLES = 50


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

    A feature that never varied in healthy history (an error rate that is
    always exactly 0, a fixed replica count) has a wobble of 0, which would
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

    def __init__(self, min_samples: int = MIN_TRAINING_SAMPLES) -> None:
        self.min_samples = min_samples
        self._stats: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def fit(self, healthy: Mapping[str, Sequence[Sequence[float]]]) -> "ZScoreDetector":
        if not healthy:
            raise ValueError("no training data given")
        stats = {}
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
        self._stats = stats
        return self

    @property
    def services(self) -> list[str]:
        return sorted(self._stats)

    def distance_many(self, service: str, rows: Sequence[Sequence[float]]) -> np.ndarray:
        """Distance from normal, in usual wobbles (0 or more). Rows with a
        missing or non-finite value get 0.0, never a confident anomaly."""
        try:
            mean, spread = self._stats[service]
        except KeyError:
            raise UnknownServiceError(
                f"no reference for service {service!r}; it needs healthy history first"
            ) from None

        matrix = np.asarray(rows, dtype=float).reshape(len(rows), -1)
        usable = np.isfinite(matrix).all(axis=1)

        distance = np.zeros(len(matrix))
        if usable.any():
            distance[usable] = (np.abs(matrix[usable] - mean) / spread).max(axis=1)
        return distance

    def score_many(self, service: str, rows: Sequence[Sequence[float]]) -> np.ndarray:
        distance = self.distance_many(service, rows)
        return distance / (distance + self.SQUASH)

    def score(self, service: str, vector: Sequence[float]) -> float:
        return float(self.score_many(service, [vector])[0])

    @classmethod
    def wobbles_of(cls, score: float) -> float:
        """Turn a 0..1 score back into a distance in usual wobbles."""
        return cls.SQUASH * score / (1.0 - score) if score < 1.0 else float("inf")
