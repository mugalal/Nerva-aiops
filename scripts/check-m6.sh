#!/usr/bin/env bash
# Calls M6 the way M4 will, and checks the response has what M4 reads.
set -euo pipefail
URL="${M6_FINOPS_BASE_URL:-http://localhost:8006}"

curl -sf "$URL/health"; echo

curl -sf -X POST "$URL/internal/finops/scale-options" \
  -H "Content-Type: application/json" \
  -d '{"service":"payment-service","current_replicas":3,"cpu_request_m":100,
       "memory_request_mb":128,"observed_cpu_pct":72,"scale_duration_minutes":30}' \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
opts = d['temporary_scale_options']          # the key M4 reads
assert opts, 'no options: M4 would ESCALATE'
for o in opts:
    assert {'replicas', 'estimated_cost_delta', 'risk'} <= set(o)
print('OK: M4-compatible response with', len(opts), 'options')"