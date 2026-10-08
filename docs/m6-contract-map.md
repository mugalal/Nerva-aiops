# M6 Contract Map (FinOps & Resource Optimization Intelligence)

Service: `services/finops-engine`, port **8006** (`M6_FINOPS_BASE_URL`).
M6 advises only. M4 decides. M6 never executes or applies anything.
The frozen contracts (`contracts/finops_context.json`, `contracts/finops_recommendation.json`)
are unchanged. Their example values (1000m, 2048MB, 28%, 0.30, 0.75) are illustrations, not
results of any formula, and M6 does not reuse them.

## 1. Endpoints

| Method | Path | Consumer | Purpose |
|---|---|---|---|
| GET | `/health` | all modules, `scripts/smoke-check.sh` | liveness and readiness |
| POST | `/internal/finops/scale-options` | M4 (decision engine) | cost/risk of temporary scale-up options |
| GET / POST | `/internal/finops/context` | M4 (decision engine) | same output as scale-options, no request body (the path M4's provider already points at) |
| POST | `/internal/finops/recommend` | shared core / UI | rightsizing from utilization the caller supplies |
| POST | `/internal/finops/recommend-live` | shared core / UI | rightsizing; M6 fetches the window from M1 itself |
| GET | `/internal/finops/assumptions` | UI, reviewers | cost-model assumptions |

## 2. GET /health

Response: `{service, status, version, environment}`.
`status`: `ok`, or `degraded` when `DATABASE_URL` is set but the database is unreachable.

## 3. POST /internal/finops/scale-options (for M4)

Request (`ScaleOptionsRequest`, unknown fields are rejected with 422):

| Field | Unit / rule |
|---|---|
| `service` | text |
| `incident_id` | optional text |
| `current_replicas` | integer >= 1 |
| `cpu_request_m` | millicores > 0 |
| `memory_request_mb` | MB > 0 |
| `observed_cpu_pct` | 0-100, percent of request |
| `scale_duration_minutes` | minutes > 0 |
| `candidate_replicas` | optional list; default is `current+1` and `current x 2` |

Response: exactly the frozen **FinOps Context** shape, no extra fields:
`service, current_replicas, current_cpu_request_m, observed_cpu_pct, temporary_scale_options[{replicas, estimated_cost_delta, risk}]`.

- `estimated_cost_delta` is in cost units (CU) for `scale_duration_minutes`.
- `risk` is `LOW`, `MEDIUM` or `HIGH`, from projected CPU after scaling:
  `observed_cpu_pct x current_replicas / new_replicas` (assumes load spreads evenly);
  <= 50 LOW, <= 75 MEDIUM, otherwise HIGH.
- An empty `temporary_scale_options` means "no safe option". M4 then escalates.
- A cheaper option can carry higher risk. M6 supplies options, M4 decides, because M4 also
  knows the RCA, policy and approval rules.

Example call M4 can copy:

```bash
curl -X POST http://localhost:8006/internal/finops/scale-options \
  -H "Content-Type: application/json" \
  -d '{"service":"payment-service","current_replicas":3,"cpu_request_m":100,
       "memory_request_mb":128,"observed_cpu_pct":72,"scale_duration_minutes":30}'
```

Result for that input: 4 replicas -> delta 0.0656 CU, risk MEDIUM; 6 replicas -> delta 0.1969 CU, risk LOW.

### 3b. GET or POST /internal/finops/context (no body)

This is the path M4's provider (`M6_FINOPS_URL`) already points at, so their stub can call M6 as written.
Optional query parameter: `?service=payment-service` (default from `FINOPS_DEFAULT_SERVICE`).

- Real data: M6 reads **replica count and CPU** from M1's `GET /internal/telemetry/snapshot`.
- Defaults (documented assumptions, set by environment variables, not hidden in code):

| Variable | Default | Meaning |
|---|---|---|
| `FINOPS_DEFAULT_SERVICE` | `payment-service` | service used when none is given |
| `FINOPS_CPU_REQUEST_M` | 100 | CPU request per pod |
| `FINOPS_MEMORY_REQUEST_MB` | 128 | memory request per pod |
| `FINOPS_CPU_LIMIT_M` | 500 | CPU limit per pod, used to convert M1's fraction |
| `FINOPS_SCALE_DURATION_MINUTES` | 30 | duration the temporary cost is computed for |

- Response: the frozen `finops_context` shape, same as section 3.
- If M1 is unavailable or reports no running replicas, the route returns **HTTP 503**, never invented numbers.
  M4 should treat an error as "no FinOps context" and escalate.
- Caveat: observed CPU is correct only if the defaults match the real requests and limits of the deployment.
  Set the variables to the real values before the demo.

## 4. POST /internal/finops/recommend (rightsizing)

Request (`RecommendRequest`): `service`, `current {replicas, cpu_request_m, memory_request_mb}`,
`observed {avg_cpu_pct, peak_cpu_pct, avg_memory_pct, peak_memory_pct}` (percent of the **request**,
peak >= average), `window {start, end, sample_count?}` (end after start), `traffic_pattern?`.

`/recommend-live` takes `service`, `current`, `cpu_limit_m` (default 500), `memory_limit_mb`
(default 512), `window_start`, `window_end`, `step_seconds` (default 15), `traffic_pattern?`,
and reads `GET /internal/telemetry/window` from M1 (`M1_TELEMETRY_BASE_URL`, default `http://localhost:8001`).

Response (`RecommendResponse`):

```
{ status, reason, assumptions[], recommendation: <frozen FinOps Recommendation shape> }
```

`status` is one of:

| Status | When |
|---|---|
| `RECOMMENDED` | enough evidence and a safe smaller configuration exists |
| `NO_RECOMMENDATION` | peak >= 90%, or saving < 5% |
| `INSUFFICIENT_EVIDENCE` | window < 60 min, fewer than 10 samples, or M1 unavailable / no data |

When nothing is recommended, `recommendation.recommended` equals the **current** configuration and
`estimated_monthly_saving_pct` is `0.0`. This keeps the frozen shape valid (its `recommended` field
is required) and shows "no change". "Can't recommend" is an HTTP 200 result, not an error. HTTP 422 is
only for malformed input.

## 5. Rightsizing rules

1. Size from the **peak**, not the average: new request = current x peak / 65, rounded up
   (CPU steps of 10m, memory steps of 16 MB).
2. Floors: CPU >= 50m, memory >= 64 MB. Resources never increase. Replicas are never changed.
3. Risk from projected peak after the change: > 80 HIGH; > 65 or window < 360 min MEDIUM; otherwise LOW.
4. Nothing is applied automatically. Same input always gives the same output.

## 6. Cost model (cost units, not money)

- CPU: 1.0 CU per requested core-hour. Memory: 0.25 CU per requested GB-hour (1 GB = 1024 MB).
- We charge for **requested** (reserved) resources, not for usage.
- Cost per hour = replicas x (cpu_m / 1000 x 1.0 + memory_mb / 1024 x 0.25). Month = 730 h.
- Saving % = (current - proposed) / current x 100.
- Scale delta = (new cost per hour - current cost per hour) x minutes / 60.
- No provider pricing is used. The rates are placeholders, tunable in `app/cost_model.py`.

Worked example: 3 x (100m, 128 MB) = 3 x (0.1 + 0.03125) = 0.39375 CU per hour.

## 7. Units and limits (important)

- M1 reports `cpu` and `memory` as a fraction of the **limit**, summed across pods (fleet average).
- M6 converts: `percent of request = fraction x limit / request x 100`.
  Demo `payment-service`: request 100m, limit 500m, so 0.18 means 90m = **90% of the request**.
- Demo requests are 100m / 128Mi, limits 500m / 512Mi. `128Mi` is treated as 128 MB (documented simplification).
- Not confirmed: whether M1's `nexus_resource_limit_*` metrics equal the YAML limits.

## 8. Persistence

Table `finops_recommendations`: one row per call to `/recommend`, `/recommend-live` and
`/scale-options` (columns for configuration, saving, risk, status, reason; JSONB for assumptions
and options). Saving is best-effort: a database failure is logged and never breaks the response.
Shared DB code is in `services/shared-nexus-api/app/db/`; `scripts/init-db.sh` creates all 12 tables.
Experiment export: `scripts/export-experiments.sh` writes CSV and JSON.
MTTD = detection - injection. MTTR = recovery - detection. Total impact = recovery - injection.
Missing timestamps give empty values, never invented numbers.

## 9. Not specified by the project (M6's choices)

| Item | Our choice |
|---|---|
| Path and body M4 calls | Both work: `/internal/finops/context` (no body, matches M4's stub; defaults in section 3b) or `POST /internal/finops/scale-options` (explicit body, section 3). M4 still has to write the HTTP call, because its real provider currently raises `NotImplementedError`. |
| Default requests / limits for the no-body route | 100m / 128 MB requests, 500m limit, 30 min, set by environment variables |
| Status and reason fields | kept outside the frozen shapes, in the `/recommend` wrapper |
| Thresholds (60 min, 10 samples, 90%, 65%, 50m / 64 MB, 5%, 360 min, 50 / 75) | placeholders, tunable |
| MTTR definition | recovery minus detection |
| `/recommend-live` and `/internal/finops/assumptions` | M6 additions |

## 10. Tests and checks

`python -m pytest` in `services/finops-engine`: all tests pass; the database test is skipped unless `DATABASE_URL` is set.
`scripts/check-m6.sh` checks the response against what M4's decision engine reads.
Docker: `docker build -f services/finops-engine/Dockerfile -t nexus/finops-engine:dev .` from the repo root.