"""
Keeps every M2 test independent of the machine it runs on.

The service reads its settings from environment variables, including the path
of a frozen reference. Without this, a reference.json committed in the repo (or
a variable left set in someone's terminal) would silently change what
unrelated tests are checking. Tests that need a setting turn it on themselves.
"""

import pytest

_M2_SETTINGS = (
    "M2_POLL_ENABLED", "M2_POLL_SERVICES", "M2_POLL_INTERVAL_S", "M2_STALE_AFTER_S",
    "M2_CORRELATION_GAP_S", "M1_TELEMETRY_BASE_URL",
)


@pytest.fixture(autouse=True)
def isolated_m2_environment(monkeypatch):
    monkeypatch.setenv("M2_REFERENCE_PATH", "none")
    monkeypatch.setenv("M2_EVIDENCE_PATH", "none")
    for name in _M2_SETTINGS:
        monkeypatch.delenv(name, raising=False)
