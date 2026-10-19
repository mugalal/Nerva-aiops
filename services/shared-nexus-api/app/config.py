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




def load_recovery_seconds_per_replica(value=None):
    """Extra recovery time per replica a scale action adds (each pod must start and warm up)."""
    raw = os.getenv("RECOVERY_SECONDS_PER_ADDED_REPLICA", "15") if value is None else value
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("RECOVERY_SECONDS_PER_ADDED_REPLICA must be a finite number in [0, 300]") from exc
    if not math.isfinite(seconds) or not 0 <= seconds <= 300:
        raise ValueError("RECOVERY_SECONDS_PER_ADDED_REPLICA must be a finite number in [0, 300]")
    return seconds


RECOVERY_SECONDS_PER_ADDED_REPLICA = load_recovery_seconds_per_replica()


def recovery_timeout_seconds(added_replicas=0):
    """The recovery deadline grows with the size of a scale-out, capped at one hour."""
    added = max(0, int(added_replicas or 0))
    return min(3600.0, RECOVERY_TIMEOUT_SECONDS + added * RECOVERY_SECONDS_PER_ADDED_REPLICA)
