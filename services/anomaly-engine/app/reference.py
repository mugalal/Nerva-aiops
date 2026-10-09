"""
The frozen reference (M2 runbook, Days 5 and 10: "freeze model/config").

A reference is everything the ruler needs to score a reading without any
training at start-up:

    for each service, the average and usual wobble of each of the 8 features,
    and how many healthy readings they came from
    the alert line: how many wobbles from normal counts as an alert
    where it came from (which captures, how clean the separation was)

It is small, plain JSON, and meant to be committed, so the config the team is
running is visible and the same for everyone. It is produced by
`python -m app.realdata train` and loaded by the service at start-up.

A reference only describes services it was trained on. For any other service
the pipeline falls back to the threshold alarm, which needs no history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .features import FEATURE_ORDER
from .model import ZScoreDetector

FORMAT_VERSION = 1
DETECTOR_NAME = "z_score_per_service"


@dataclass(frozen=True)
class AlertLine:
    wobbles: float                          # this far from normal (or further) is an alert
    normal_max_wobbles: float | None        # the noisiest normal reading seen when choosing it
    fault_min_wobbles: float | None         # the quietest settled fault reading seen

    @property
    def score(self) -> float:
        """The same line on the detector's 0..1 score scale."""
        return self.wobbles / (self.wobbles + ZScoreDetector.SQUASH)

    @property
    def margin(self) -> float | None:
        """How many times further the quietest fault was than the noisiest normal."""
        if not self.normal_max_wobbles or self.fault_min_wobbles is None:
            return None
        return self.fault_min_wobbles / self.normal_max_wobbles


@dataclass(frozen=True)
class Reference:
    detector: ZScoreDetector
    alert: AlertLine
    trained_on: tuple[str, ...]
    created: str

    @property
    def services(self) -> list[str]:
        return self.detector.services


def save_reference(path: str | Path, reference: Reference) -> None:
    body = {
        "format": FORMAT_VERSION,
        "detector": DETECTOR_NAME,
        "created": reference.created,
        "trained_on": list(reference.trained_on),
        "feature_order": list(FEATURE_ORDER),
        "alert": {
            "wobbles": reference.alert.wobbles,
            "normal_max_wobbles": reference.alert.normal_max_wobbles,
            "fault_min_wobbles": reference.alert.fault_min_wobbles,
        },
        **reference.detector.to_dict(),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, indent=2) + "\n")


def load_reference(path: str | Path) -> Reference:
    body = json.loads(Path(path).read_text())
    if body.get("format") != FORMAT_VERSION:
        raise ValueError(f"{path}: unsupported reference format {body.get('format')!r}")
    if body.get("detector") != DETECTOR_NAME:
        raise ValueError(f"{path}: unknown detector {body.get('detector')!r}")
    if body.get("feature_order") != list(FEATURE_ORDER):
        # The reference stores numbers by position. If the feature order has
        # changed since it was made, every number would be matched to the
        # wrong feature, so refuse rather than score nonsense.
        raise ValueError(f"{path}: made for a different feature order; train it again")
    alert = body["alert"]
    return Reference(
        detector=ZScoreDetector.from_dict(body),
        alert=AlertLine(
            wobbles=float(alert["wobbles"]),
            normal_max_wobbles=alert.get("normal_max_wobbles"),
            fault_min_wobbles=alert.get("fault_min_wobbles"),
        ),
        trained_on=tuple(body.get("trained_on", [])),
        created=body.get("created", ""),
    )


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
