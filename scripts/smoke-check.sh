#!/usr/bin/env bash
set -u

check_health() {
  local name="$1"
  local url="$2"

  printf "%-24s %s ... " "$name" "$url"
  if curl --fail --silent --show-error --max-time 3 "$url/health" >/tmp/nexus-smoke-health.json; then
    echo "ok"
  else
    echo "failed"
    return 1
  fi
}

status=0

check_health "shared-nexus-api" "${SHARED_NEXUS_API_BASE_URL:-http://localhost:8000}" || status=1
check_health "m1-telemetry" "${M1_TELEMETRY_BASE_URL:-http://localhost:8001}" || status=1
check_health "m2-anomaly" "${M2_ANOMALY_BASE_URL:-http://localhost:8002}" || status=1
check_health "m3-rca" "${M3_RCA_BASE_URL:-http://localhost:8003}" || status=1
check_health "m4-decision" "${M4_DECISION_BASE_URL:-http://localhost:8004}" || status=1
check_health "m5-memory" "${M5_MEMORY_BASE_URL:-http://localhost:8005}" || status=1
check_health "m6-finops" "${M6_FINOPS_BASE_URL:-http://localhost:8006}" || status=1

printf "%-24s %s ... " "prometheus" "${PROMETHEUS_BASE_URL:-http://localhost:9090}"
if curl --fail --silent --show-error --max-time 3 "${PROMETHEUS_BASE_URL:-http://localhost:9090}/-/ready" >/dev/null; then
  echo "ok"
else
  echo "failed"
  status=1
fi

exit "$status"

