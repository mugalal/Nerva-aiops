"""
Checks that the Pydantic models in shared/contracts match the frozen
contracts in /contracts and the mocks in /mocks exactly.

These tests read the repo's real JSON files, not copies, so if a contract
or mock changes without the models changing (or the other way round), a
test here fails.

Run from the repo root:   python -m pytest tests/contract -v
"""

import copy
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.contracts import (  # noqa: E402
    CONTRACT_MODELS,
    MOCK_MODELS,
    AnomalyEvent,
    Incident,
)

CONTRACTS_DIR = REPO_ROOT / "contracts"
MOCKS_DIR = REPO_ROOT / "mocks"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Every contract and mock file is covered, and every model accepts them
# ---------------------------------------------------------------------------

def test_every_contract_file_has_a_model():
    on_disk = {p.name for p in CONTRACTS_DIR.glob("*.json")}
    assert on_disk == set(CONTRACT_MODELS), (
        "contracts/ and CONTRACT_MODELS disagree. "
        f"Only on disk: {sorted(on_disk - set(CONTRACT_MODELS))}. "
        f"Only in CONTRACT_MODELS: {sorted(set(CONTRACT_MODELS) - on_disk)}."
    )


def test_every_mock_file_has_a_model():
    on_disk = {p.name for p in MOCKS_DIR.glob("*.json")}
    assert on_disk == set(MOCK_MODELS), (
        "mocks/ and MOCK_MODELS disagree. "
        f"Only on disk: {sorted(on_disk - set(MOCK_MODELS))}. "
        f"Only in MOCK_MODELS: {sorted(set(MOCK_MODELS) - on_disk)}."
    )


@pytest.mark.parametrize("filename", sorted(CONTRACT_MODELS))
def test_model_accepts_frozen_contract(filename):
    CONTRACT_MODELS[filename].model_validate(_load(CONTRACTS_DIR / filename))


@pytest.mark.parametrize("filename", sorted(MOCK_MODELS))
def test_model_accepts_mock(filename):
    MOCK_MODELS[filename].model_validate(_load(MOCKS_DIR / filename))


@pytest.mark.parametrize("filename", sorted(CONTRACT_MODELS))
def test_contract_round_trips_unchanged(filename):
    """Validate then dump: the result must equal the original JSON.

    Catches a model that silently drops, renames or reshapes a field.
    """
    original = _load(CONTRACTS_DIR / filename)
    model = CONTRACT_MODELS[filename].model_validate(original)
    assert model.model_dump(mode="json") == original


# ---------------------------------------------------------------------------
# "Contract wins, no extra fields": strictness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", sorted(CONTRACT_MODELS))
def test_extra_top_level_field_rejected(filename):
    data = _load(CONTRACTS_DIR / filename)
    data["unexpected_field"] = 1
    with pytest.raises(ValidationError):
        CONTRACT_MODELS[filename].model_validate(data)


@pytest.mark.parametrize("filename", sorted(CONTRACT_MODELS))
def test_every_required_field_is_enforced(filename):
    original = _load(CONTRACTS_DIR / filename)
    for key in original:
        data = copy.deepcopy(original)
        del data[key]
        with pytest.raises(ValidationError):
            CONTRACT_MODELS[filename].model_validate(data)


def test_extra_field_inside_nested_object_rejected():
    # The Day-1 mismatch: service_health / labels were added to the metrics
    # mock but are not in the contract. Nested extras must fail too.
    data = _load(CONTRACTS_DIR / "telemetry_snapshot.json")
    data["metrics"]["service_health"] = "ok"
    with pytest.raises(ValidationError):
        CONTRACT_MODELS["telemetry_snapshot.json"].model_validate(data)


def test_anomaly_features_are_exactly_the_four_frozen_ones():
    data = _load(CONTRACTS_DIR / "anomaly_event.json")
    assert set(data["features"]) == {
        "request_rate",
        "latency_p95_ms",
        "http_5xx_rate",
        "cpu",
    }
    # An 8-feature event (e.g. with memory) must not be accepted until the
    # contract is versioned.
    data["features"]["memory"] = 0.61
    with pytest.raises(ValidationError):
        AnomalyEvent.model_validate(data)


# ---------------------------------------------------------------------------
# Value rules
# ---------------------------------------------------------------------------

def test_anomaly_score_must_be_between_0_and_1():
    data = _load(CONTRACTS_DIR / "anomaly_event.json")
    for bad in (-0.1, 1.5):
        data["score"] = bad
        with pytest.raises(ValidationError):
            AnomalyEvent.model_validate(data)


def test_incident_status_must_be_a_state_machine_state():
    data = _load(CONTRACTS_DIR / "incident.json")
    data["status"] = "NOT_A_REAL_STATE"
    with pytest.raises(ValidationError):
        Incident.model_validate(data)


@pytest.mark.parametrize(
    "state",
    [
        "DETECTED", "CORRELATING", "DIAGNOSING", "DIAGNOSED",
        "ACTION_PROPOSED", "AWAITING_APPROVAL", "EXECUTING", "VALIDATING",
        "RESOLVED", "FAILED_REMEDIATION", "ESCALATED",
    ],
)
def test_all_eleven_incident_states_accepted(state):
    data = _load(CONTRACTS_DIR / "incident.json")
    data["status"] = state
    assert Incident.model_validate(data).status == state
