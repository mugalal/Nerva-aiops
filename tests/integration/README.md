# Controlled HTTP integration tests

These tests launch four separate loopback Uvicorn processes for M1, M3, M4,
and M6. M3/M4/M6 use real HTTP provider paths. M1 uses its production API,
baseline, evidence storage, and recovery code with explicit telemetry, logs,
deployment, resource, and payment-health fixtures. M4 uses a mock actuator.
No live Kubernetes, Jenkins, Prometheus, Loki, or payment workload is contacted.

Install the runtime requirements for the four services and pytest in an isolated
Python environment. From the repository root, run:

```powershell
python -m pytest tests/integration -q -p no:cacheprovider
```

The verified command for this Windows workspace uses its existing bundled
runtime and ignored dependency directories:

```powershell
Set-Location 'E:/AIOPS/final project'
$integrationPython = 'C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$env:PYTHONPATH = 'E:/AIOPS/final project/.review-branches/m1-deps;E:/AIOPS/final project/.review-branches/deps'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& $integrationPython -m pytest services/root-cause-analysis/tests -q -p no:cacheprovider
& $integrationPython -m pytest tests/integration -q -p no:cacheprovider
```

Both rollback and scale flows use a frozen incident and linked anomaly, diagnose
through M1's uncaptured preview, bind the observed service version, capture the
baseline before approval, and verify stored action timestamps. They assert an
immediate HTTP 202 with `Retry-After: 15`, then require M1's measured `recovered`
and `slo_restored` results before M4 returns `RESOLVED`. The scale case also
checks M6's CPU resource conversion and nine-replica low-risk recommendation.

Each flow needs approximately 15 seconds for its 5-second lookback plus
10-second healthy hold; baseline checks retain at least 60 seconds of controlled
historical samples. The two cases usually finish in about 40 seconds total.

Processes bind random localhost ports, write logs/evidence to pytest temporary
directories, launch hidden on Windows, and terminate only owned child processes.
Passing these tests proves controlled HTTP integration, not live scaling.
