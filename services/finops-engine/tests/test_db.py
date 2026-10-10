import os

import pytest

pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"),
                                reason="DATABASE_URL not set")

from app import persistence  # noqa: E402
from app.models import RecommendRequest  # noqa: E402
from app.rightsizing import recommend  # noqa: E402


def test_init_save_and_read_back():
    persistence._ensure_path()
    from db.init_db import init_db
    from db.repository import get_records

    init_db()
    req = RecommendRequest(**{
        "service": "db-test-service",
        "current": {"replicas": 3, "cpu_request_m": 100, "memory_request_mb": 128},
        "observed": {"avg_cpu_pct": 40, "peak_cpu_pct": 55,
                     "avg_memory_pct": 50, "peak_memory_pct": 60},
        "window": {"start": "2026-10-07T08:00:00Z", "end": "2026-10-07T10:00:00Z"},
    })
    resp = recommend(req)
    assert persistence.save_recommendation(req, resp) is True
    rows = get_records("finops_recommendations", service="db-test-service")
    assert rows and rows[-1]["status"] == resp.status