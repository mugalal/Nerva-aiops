CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ,
    status TEXT,
    severity TEXT,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id TEXT PRIMARY KEY,
    incident_id TEXT,
    service TEXT,
    occurred_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rca_results (
    id SERIAL PRIMARY KEY,
    incident_id TEXT,
    root_cause TEXT,
    confidence DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decision_proposals (
    id SERIAL PRIMARY KEY,
    incident_id TEXT,
    recommended_action TEXT,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    incident_id TEXT,
    approved BOOLEAN,
    decided_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    incident_id TEXT,
    action TEXT,
    status TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recovery_results (
    id SERIAL PRIMARY KEY,
    incident_id TEXT,
    recovered BOOLEAN,
    recovery_time_seconds DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deployment_events (
    event_id TEXT PRIMARY KEY,
    service TEXT,
    occurred_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incident_memory (
    id SERIAL PRIMARY KEY,
    incident_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finops_recommendations (
    id SERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    service TEXT NOT NULL,
    incident_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    current_replicas INTEGER,
    current_cpu_request_m INTEGER,
    current_memory_request_mb INTEGER,
    recommended_replicas INTEGER,
    recommended_cpu_request_m INTEGER,
    recommended_memory_request_mb INTEGER,
    estimated_monthly_saving_pct DOUBLE PRECISION,
    reliability_risk TEXT,
    assumptions JSONB,
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ,
    options JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_finops_service ON finops_recommendations (service, created_at);

CREATE TABLE IF NOT EXISTS timeline_events (
    id SERIAL PRIMARY KEY,
    incident_id TEXT,
    event_type TEXT,
    occurred_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    run_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    incident_id TEXT,
    injection_time TIMESTAMPTZ,
    detection_time TIMESTAMPTZ,
    rca_correct BOOLEAN,
    action_time TIMESTAMPTZ,
    recovery_time TIMESTAMPTZ,
    cost_slo_effect JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent projections of the authoritative incidents.payload workflow.
ALTER TABLE rca_results ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE decision_proposals ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE recovery_results ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE timeline_events ADD COLUMN IF NOT EXISTS event_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rca_event_key ON rca_results(event_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_proposal_event_key ON decision_proposals(event_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_event_key ON approvals(event_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_event_key ON recovery_results(event_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_timeline_event_key ON timeline_events(event_key);

CREATE TABLE IF NOT EXISTS memory_outbox (
    job_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    delivered_at TIMESTAMPTZ,
    last_error TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
