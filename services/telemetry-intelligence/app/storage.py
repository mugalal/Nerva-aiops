import json
from pathlib import Path
import re

from .errors import BaselineNotFound, EvidenceNotFound
from .models import HealthyBaseline, IncidentEvidence, RecoveryEvidenceRecord


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    if not safe:
        raise ValueError("storage key cannot be empty")
    return safe


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root
        self.baseline_dir = root / "baselines"
        self.incident_dir = root / "incidents"
        self.recovery_dir = root / "recoveries"
        for directory in (self.baseline_dir, self.incident_dir, self.recovery_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    def save_baseline(self, baseline: HealthyBaseline) -> None:
        path = self.baseline_dir / f"{_safe_name(baseline.service)}.json"
        self._write(path, baseline.model_dump(mode="json"))

    def get_baseline(self, service: str) -> HealthyBaseline:
        path = self.baseline_dir / f"{_safe_name(service)}.json"
        if not path.exists():
            raise BaselineNotFound(service)
        return HealthyBaseline.model_validate_json(path.read_text(encoding="utf-8"))

    def save_evidence(self, evidence: IncidentEvidence) -> None:
        path = self.incident_dir / f"{_safe_name(evidence.incident_id)}.json"
        self._write(path, evidence.model_dump(mode="json"))

    def get_evidence(self, incident_id: str) -> IncidentEvidence:
        path = self.incident_dir / f"{_safe_name(incident_id)}.json"
        if not path.exists():
            raise EvidenceNotFound(incident_id)
        return IncidentEvidence.model_validate_json(path.read_text(encoding="utf-8"))

    def save_recovery(self, record: RecoveryEvidenceRecord) -> None:
        path = self.recovery_dir / f"{_safe_name(record.incident_id)}.json"
        self._write(path, record.model_dump(mode="json"))

    def get_recovery(self, incident_id: str) -> RecoveryEvidenceRecord:
        path = self.recovery_dir / f"{_safe_name(incident_id)}.json"
        if not path.exists():
            raise EvidenceNotFound(incident_id)
        return RecoveryEvidenceRecord.model_validate_json(path.read_text(encoding="utf-8"))
