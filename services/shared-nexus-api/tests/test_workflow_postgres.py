"""Optional actual PostgreSQL gate; requires a disposable verification database."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import pytest


def test_real_postgres_process_restart_lifecycle_and_export():
    url = os.getenv("M4_TEST_DATABASE_URL")
    if not url:
        pytest.skip("M4_TEST_DATABASE_URL is not set")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    schema = "m4_verification_" + uuid4().hex
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    env = os.environ.copy()
    env.update(DATABASE_URL=make_conninfo(url, options="-csearch_path=" + schema),
               M4_STATE_BACKEND="postgres", M5_MEMORY_BASE_URL="http://127.0.0.1:1")
    # A script under tests/ does not inherit pytest's service-root import path.
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    command = [sys.executable, str(Path(__file__).with_name("check_postgres_workflow.py"))]
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["restart_in_separate_process"] and evidence["ambiguous_intent_not_repeated"]
    assert evidence["actuator"] == "mock" and evidence["injection_time_invented"] is False
    assert all(count > 0 for count in evidence["normalized_rows"].values())
