#!/usr/bin/env bash
set -u

m1_only=false
skip_grafana=false
require_kubernetes=false
for argument in "$@"; do
  case "$argument" in
    --m1-only) m1_only=true ;;
    --skip-grafana) skip_grafana=true ;;
    --require-kubernetes) require_kubernetes=true ;;
    *) echo "Usage: $0 [--m1-only] [--skip-grafana] [--require-kubernetes]" >&2; exit 2 ;;
  esac
done

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  python_bin=python3
elif command -v python >/dev/null 2>&1; then
  python_bin=python
else
  echo "Python 3 is required to validate smoke-check JSON responses." >&2
  exit 2
fi

response_file=$(mktemp)
trap 'rm -f -- "$response_file"' EXIT
export NEXUS_SMOKE_REQUIRE_KUBERNETES="$require_kubernetes"

validate_json() {
  "$python_bin" - "$1" "$response_file" <<'PY'
import datetime
import json
import math
import os
import sys

try:
    with open(sys.argv[2], encoding="utf-8") as response:
        payload = json.load(response)
    kind = sys.argv[1]
    if kind in {"health", "m1-health"}:
        if payload.get("status") not in {"ok", "degraded"}:
            raise ValueError(f"Unexpected readiness status: {payload.get('status')}")
        if kind == "m1-health":
            if payload.get("provider_mode") != "real":
                raise ValueError("M1 must use real providers for this smoke check")
            for name in ("telemetry_provider", "telemetry_data", "loki", "payment_service"):
                dependency = payload.get("dependencies", {}).get(name, {})
                if dependency.get("status") != "ok":
                    raise ValueError(f"{name} is not ready: {dependency.get('detail')}")
            if os.environ["NEXUS_SMOKE_REQUIRE_KUBERNETES"] == "true":
                dependency = payload.get("dependencies", {}).get("kubernetes", {})
                if dependency.get("status") != "ok":
                    raise ValueError(f"Kubernetes evidence is not ready: {dependency.get('detail')}")
        print(payload["status"])
    elif kind == "snapshot":
        if payload.get("service") != "payment-service" or not payload.get("version"):
            raise ValueError("Snapshot has no valid payment-service identity")
        metrics = payload["metrics"]
        fields = {"request_rate", "latency_p95_ms", "http_5xx_rate", "cpu", "memory", "replica_count"}
        if set(metrics) != fields:
            raise ValueError("Snapshot metrics do not match the frozen contract")
        for name in fields:
            value = metrics[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Snapshot has an invalid {name}")
        if metrics["http_5xx_rate"] > 1 or not isinstance(metrics["replica_count"], int) or metrics["replica_count"] < 1:
            raise ValueError("Snapshot has an invalid error ratio or no running replicas")
        if metrics["request_rate"] <= 0 or metrics["latency_p95_ms"] <= 0:
            raise ValueError("No recent /pay traffic. Generate traffic for at least 90 seconds and retry while traffic is running")
        timestamp = datetime.datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - timestamp).total_seconds()
        if age > float(os.environ.get("M1_STALE_AFTER_SECONDS", "60")) or age < -5:
            raise ValueError(f"Snapshot timestamp is stale or in the future (age={age:.1f} seconds)")
        print(f"ok ({metrics['request_rate']:.2f} req/s, {metrics['latency_p95_ms']:.2f} ms P95, version {payload['version']})")
    elif kind == "logs":
        if payload.get("status") != "success" or payload.get("data", {}).get("resultType") != "streams":
            raise ValueError("Loki did not return a successful log query")
        streams = payload["data"]["result"]
        if any(stream.get("stream", {}).get("service_name") != "payment-service" for stream in streams):
            raise ValueError("Loki returned logs for another service")
        count = sum(len(stream.get("values", [])) for stream in streams)
        if count == 0:
            raise ValueError("No /pay logs in the last 2 minutes. Generate payment traffic and check Alloy collection")
        print(f"ok ({count} recent payment request logs)")
    elif kind == "grafana":
        if payload.get("database") != "ok":
            raise ValueError("Grafana database is not ready")
        print("ok")
except (KeyError, TypeError, ValueError, OSError) as error:
    print(f"failed: {error}")
    sys.exit(1)
PY
}

check_json() {
  local name="$1"
  local url="$2"
  local kind="$3"

  printf "%-24s %s ... " "$name" "$url"
  if curl --fail --silent --show-error --max-time 15 "$url" -o "$response_file"; then
    validate_json "$kind"
  else
    echo "failed (check service availability and keep /pay traffic running)"
    return 1
  fi
}

check_ready() {
  local name="$1"
  local url="$2"

  printf "%-24s %s ... " "$name" "$url"
  if curl --fail --silent --show-error --max-time 3 "$url" >/dev/null; then
    echo "ok"
  else
    echo "failed"
    return 1
  fi
}

status=0
m1_url="${M1_TELEMETRY_BASE_URL:-http://localhost:8001}"
loki_url="${LOKI_BASE_URL:-http://localhost:3100}"

if [[ "$m1_only" == false ]]; then
  check_json "shared-nexus-api" "${SHARED_NEXUS_API_BASE_URL:-http://localhost:8000}/health" health || status=1
fi
check_json "m1-telemetry" "${m1_url%/}/health" m1-health || status=1
if [[ "$m1_only" == false ]]; then
  check_json "m2-anomaly" "${M2_ANOMALY_BASE_URL:-http://localhost:8002}/health" health || status=1
  check_json "m3-rca" "${M3_RCA_BASE_URL:-http://localhost:8003}/health" health || status=1
  check_json "m4-decision" "${M4_DECISION_BASE_URL:-http://localhost:8004}/health" health || status=1
  check_json "m5-memory" "${M5_MEMORY_BASE_URL:-http://localhost:8005}/health" health || status=1
  check_json "m6-finops" "${M6_FINOPS_BASE_URL:-http://localhost:8006}/health" health || status=1
fi
check_ready prometheus "${PROMETHEUS_BASE_URL:-http://localhost:9090}/-/ready" || status=1
if [[ "$m1_only" == true ]]; then
  check_ready loki "${loki_url%/}/ready" || status=1
  if [[ "$skip_grafana" == false ]]; then
    check_json grafana "${GRAFANA_BASE_URL:-http://localhost:3000}/api/health" grafana || status=1
  fi
fi
check_json "payment snapshot" "${m1_url%/}/internal/telemetry/snapshot?service=payment-service" snapshot || status=1

end_ns=$("$python_bin" -c 'import time; print(time.time_ns())')
start_ns=$((end_ns - 120000000000))
printf "%-24s %s ... " "payment logs" "$loki_url"
if curl --fail --silent --show-error --max-time 15 --get "${loki_url%/}/loki/api/v1/query_range" \
  --data-urlencode 'query={service_name="payment-service"} | json | route="/pay" | __error__=""' \
  --data-urlencode "start=$start_ns" --data-urlencode "end=$end_ns" \
  --data-urlencode limit=20 --data-urlencode direction=backward -o "$response_file"; then
  validate_json logs || status=1
else
  echo "failed"
  status=1
fi

exit "$status"
