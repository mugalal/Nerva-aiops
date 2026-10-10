import math
import os


def load_recovery_timeout_seconds(value=None):
    raw = os.getenv("RECOVERY_TIMEOUT_SECONDS", "360") if value is None else value
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("RECOVERY_TIMEOUT_SECONDS must be a finite number in (0, 3600]") from exc
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError("RECOVERY_TIMEOUT_SECONDS must be a finite number in (0, 3600]")
    return seconds


RECOVERY_TIMEOUT_SECONDS = load_recovery_timeout_seconds()


def load_proposal_ttl_seconds(value=None):
    raw = os.getenv("M4_PROPOSAL_TTL_SECONDS", "300") if value is None else value
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("M4_PROPOSAL_TTL_SECONDS must be a finite number in (0, 86400]") from exc
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError("M4_PROPOSAL_TTL_SECONDS must be a finite number in (0, 86400]")
    return seconds


PROPOSAL_TTL_SECONDS = load_proposal_ttl_seconds()

