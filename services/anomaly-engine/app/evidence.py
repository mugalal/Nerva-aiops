"""
Feature evidence (M2 runbook, Day 9: "persist anomaly score/features with
incident for M5 memory and final evaluation").

One JSON line per alert, appended as it happens. Each line carries everything
someone would need to understand why M2 alerted: the incident it joined, the
score, ALL eight features (not only the four the AnomalyEvent contract carries),
how far each was from normal, the signals involved, and any deployment context.

A line is only ever added, never changed, so the file is a faithful log that
M5 (memory) can read later and the final evaluation can replay.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path


class EvidenceStore:
    def __init__(self, path: str | Path | None) -> None:
        """`path=None` keeps nothing on disk (evidence still reaches the
        incident candidates held in memory)."""
        self.path = None if path is None else Path(path)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def append(self, record: dict) -> None:
        if self.path is None:
            return
        line = json.dumps(record, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read(self) -> list[dict]:
        if self.path is None or not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
