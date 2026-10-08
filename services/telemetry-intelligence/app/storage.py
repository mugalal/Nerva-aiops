import hashlib
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import RLock
import time
from typing import Callable, TypeVar

from .errors import BaselineNotFound, EvidenceNotFound
from .models import HealthyBaseline, IncidentEvidence, RecoveryEvidenceRecord


_T = TypeVar("_T")
_storage_io_lock = RLock()


def _retry_permission_error(operation: Callable[[], _T]) -> _T:
    # Windows can briefly deny opens/replaces while another reader or atomic
    # writer holds the destination. Keep retries bounded; persistent errors fail.
    with _storage_io_lock:
        for attempt in range(8):
            try:
                return operation()
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.001 * (2 ** attempt), 0.032))
    raise AssertionError("unreachable")


def _safe_name(value: str) -> str:
    if not value:
        raise ValueError("storage key cannot be empty")
    # Sanitizing punctuation to '_' aliases otherwise distinct incident IDs.
    # A fixed-length digest also avoids path traversal and Windows device names.
    return "key-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _legacy_name(value: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return None
    stem = value.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[0-9]", stem):
        return None
    return f"{value}.json"


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
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, indent=2, default=str)
            _retry_permission_error(lambda: temporary.replace(path))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_path(directory: Path, key: str) -> Path | None:
        path = directory / f"{_safe_name(key)}.json"
        if _retry_permission_error(path.exists):
            return path
        legacy_name = _legacy_name(key)
        if legacy_name is not None:
            legacy_path = directory / legacy_name
            if _retry_permission_error(legacy_path.exists):
                return legacy_path
        return None

    def save_baseline(self, baseline: HealthyBaseline) -> None:
        path = self.baseline_dir / f"{_safe_name(baseline.service)}.json"
        self._write(path, baseline.model_dump(mode="json"))

    def get_baseline(self, service: str) -> HealthyBaseline:
        path = self._read_path(self.baseline_dir, service)
        if path is None:
            raise BaselineNotFound(service)
        baseline = HealthyBaseline.model_validate_json(
            _retry_permission_error(lambda: path.read_text(encoding="utf-8"))
        )
        if baseline.service != service:
            raise BaselineNotFound(service)
        return baseline

    def save_evidence(self, evidence: IncidentEvidence) -> None:
        path = self.incident_dir / f"{_safe_name(evidence.incident_id)}.json"
        self._write(path, evidence.model_dump(mode="json"))

    def get_evidence(self, incident_id: str) -> IncidentEvidence:
        path = self._read_path(self.incident_dir, incident_id)
        if path is None:
            raise EvidenceNotFound(incident_id)
        evidence = IncidentEvidence.model_validate_json(
            _retry_permission_error(lambda: path.read_text(encoding="utf-8"))
        )
        if evidence.incident_id != incident_id:
            raise EvidenceNotFound(incident_id)
        return evidence

    def save_recovery(self, record: RecoveryEvidenceRecord) -> None:
        path = self.recovery_dir / f"{_safe_name(record.incident_id)}.json"
        self._write(path, record.model_dump(mode="json"))

    def get_recovery(self, incident_id: str) -> RecoveryEvidenceRecord:
        path = self._read_path(self.recovery_dir, incident_id)
        if path is None:
            raise EvidenceNotFound(incident_id)
        record = RecoveryEvidenceRecord.model_validate_json(
            _retry_permission_error(lambda: path.read_text(encoding="utf-8"))
        )
        if record.incident_id != incident_id or record.result.incident_id != incident_id:
            raise EvidenceNotFound(incident_id)
        return record
