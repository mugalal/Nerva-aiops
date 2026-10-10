# FinOps engine

Run from this directory with `uvicorn app.main:app --port 8006`.
`M1_TELEMETRY_BASE_URL` points to the real telemetry service.

`GET /internal/finops/context?service=payment-service` and the corresponding
body-free POST return the unchanged frozen FinOps context. M6 reads
`GET /internal/evidence/preview` from M1 and requires `provider_mode=real`,
matching service identities, an explicit version, fresh telemetry, finite
nonnegative metrics, and a positive integer replica count. The default allowed
snapshot and evidence resource-collection age is 60 seconds, configurable with `FINOPS_M1_MAX_AGE_SECONDS`
(positive, at most 300 seconds).

When Kubernetes is available, M6 uses M1's verified per-pod `resource_config`:
`cpu_request_m`, `cpu_limit_m`, `memory_request_mb`, `memory_limit_mb`, and
`memory_unit=MiB`. CPU quantities are millicores; the legacy memory fields
ending in `_mb` use MiB. An unready rollout, disagreeing release versions, or
unverified Kubernetes resources prevents recommendations. The desired and ready
Kubernetes replica counts must both agree with the telemetry count. M6 rejects
resource configuration without its matching Kubernetes context.

When Kubernetes is unavailable, configure all four values explicitly:

```text
FINOPS_CPU_REQUEST_M=100
FINOPS_CPU_LIMIT_M=1000
FINOPS_MEMORY_REQUEST_MB=128
FINOPS_MEMORY_LIMIT_MB=512
```

These example values describe the Docker demo's 1000m CPU limit and explicitly
assumed requests of 100m / 128Mi. Set them to the actual deployment values.
There is no implicit 500m CPU limit. M6 logs whether resources came from
Kubernetes or explicit environment configuration. Limits and requests must be
positive and finite, and a request cannot exceed its limit. A malformed real
resource configuration is rejected instead of replaced with environment values.

The conversion is `percent of request = fraction of limit × limit ÷ request × 100`.
It uses exact resource quantities. Frozen integer request fields and cost inputs
round fractional resource reservations upward to the next millicore or MiB.
The context's observed percentage is capped at 100 to preserve its public
contract, while option risk uses the actual uncapped percentage. For example,
400% actual usage cannot become a low-risk doubling recommendation. M4 applies
its policy, replica cap, approval, and execution checks to the returned options.
If existing options offer no LOW risk, M6 also computes targets for projected
CPU utilization at most 70% and 50%. It retains the actual risk of each option
and removes duplicate replica counts. Targets above M4's limit remain above
that limit; M4 escalates when no permitted safe option exists.

`FINOPS_SCALE_DURATION_MINUTES` defaults to 30 and accepts 1–1440 minutes.
The `service` query is optional; its default is `FINOPS_DEFAULT_SERVICE` or
`payment-service`. Invalid query names produce 422. Missing configuration,
stale or malformed evidence, mock providers, and unavailable M1 produce 503
with no fabricated context.

`POST /internal/finops/recommend-live` requires explicit `cpu_limit_m` and
`memory_limit_mb` values alongside the existing current resources and window.
Both limits use the same units as the requests. There are no implicit limit
defaults. Historical utilization is converted from M1's window using these
explicit limits. M6 also fetches a fresh current M1 evidence preview and requires
the measured release to match the current deployment. Supplied requests, limits
and replicas must exactly match that preview's verified resources (or all four
explicit environment values when Kubernetes is unavailable). M1 reports fleet
averages, so one hot pod is not visible. Resource configuration must remain
unchanged throughout the measured release; M1's window does not include
historical resource configurations.

The historical response must match the requested service, start/end instants and
step, identify one nonempty release, and contain every reading on M1's inclusive
query grid. Timestamps must be timezone-aware, unique, ordered and in range;
missing, sparse, repeated, misaligned, mixed-release or wrong-service readings
return `INSUFFICIENT_EVIDENCE`, keep current resources and report zero savings.
M1 itself rejects windows crossing a release, including metric lookback.
The sizing window uses the actual first and last readings, never the requested
duration. For example, a 60-minute query with a 299-second step has only 59m48s
of observed coverage and cannot satisfy the minimum 60-minute sizing window.
Successful measured summaries include the release, unique sample count and
actual observation boundaries in the returned assumptions.

Cost estimates remain abstract cost units for reserved resources, with memory
charged per GiB (1024 MiB). They are not provider prices or real-money savings.

`k8s/deployment.yaml` deploys M6 and its port-8006 Service in `nexus-demo`.
It reads actual resource configuration from M1; it supplies no resource
fallback values and requires no Kubernetes service-account token. Build/load
`nexus/finops-engine:dev` into the cluster before applying the manifest.

Run verification with `python -m pytest tests -q`. The live database check is
skipped when `DATABASE_URL` is absent. Persistence remains best effort and is
not required for the context endpoint to respond.

`GET /health` reports the process health and a degraded body status when a
configured database is unreachable. `GET /ready` returns HTTP 503 in that case
so Kubernetes can withhold traffic until the database recovers. Without a
configured database, M6's calculation-only mode remains ready. M1 evidence is
validated when requests are made; readiness probes do not fetch costly history
or evidence previews.
