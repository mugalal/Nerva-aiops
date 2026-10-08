"""Generic persistence helpers shared by all modules.

Table and column names are checked against TABLES (a whitelist), so they are safe
to put in SQL text. Values always go through %s placeholders.
"""
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .connection import connect

TABLES = {
    "incidents": ["incident_id", "started_at", "status", "severity", "payload"],
    "anomalies": ["anomaly_id", "incident_id", "service", "occurred_at", "payload"],
    "rca_results": ["incident_id", "root_cause", "confidence", "payload"],
    "decision_proposals": ["incident_id", "recommended_action", "payload"],
    "approvals": ["incident_id", "approved", "decided_at", "payload"],
    "actions": ["action_id", "incident_id", "action", "status", "started_at",
                "completed_at", "payload"],
    "recovery_results": ["incident_id", "recovered", "recovery_time_seconds", "payload"],
    "deployment_events": ["event_id", "service", "occurred_at", "payload"],
    "incident_memory": ["incident_id", "payload"],
    "finops_recommendations": [
        "kind", "service", "incident_id", "status", "reason",
        "current_replicas", "current_cpu_request_m", "current_memory_request_mb",
        "recommended_replicas", "recommended_cpu_request_m", "recommended_memory_request_mb",
        "estimated_monthly_saving_pct", "reliability_risk", "assumptions",
        "window_start", "window_end", "options",
    ],
    "timeline_events": ["incident_id", "event_type", "occurred_at", "payload"],
    "experiment_runs": ["run_id", "scenario", "incident_id", "injection_time",
                        "detection_time", "rca_correct", "action_time",
                        "recovery_time", "cost_slo_effect"],
}


def _check(table: str, columns) -> None:
    if table not in TABLES:
        raise ValueError(f"unknown table: {table}")
    bad = [c for c in columns if c not in TABLES[table]]
    if bad:
        raise ValueError(f"unknown columns for {table}: {bad}")


def _adapt(value):
    return Jsonb(value) if isinstance(value, (dict, list)) else value


def insert_record(table: str, values: dict) -> None:
    _check(table, values)
    cols = list(values)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join(['%s'] * len(cols))})")
    with connect() as conn:
        conn.execute(sql, [_adapt(values[c]) for c in cols])


def get_records(table: str, **filters) -> list[dict]:
    _check(table, filters)
    where = " AND ".join(f"{c} = %s" for c in filters) or "TRUE"
    sql = f"SELECT * FROM {table} WHERE {where} ORDER BY created_at"
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, list(filters.values()))
        return cur.fetchall()