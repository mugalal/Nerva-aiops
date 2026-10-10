#!/usr/bin/env bash
set -euo pipefail

base_url="${PAYMENT_SERVICE_BASE_URL:-http://localhost:8000}"
duration_seconds="${DURATION_SECONDS:-60}"
concurrency="${CONCURRENCY:-5}"
delay_seconds="${DELAY_SECONDS:-0.05}"
target="${base_url%/}/pay"
end_time=$(( $(date +%s) + duration_seconds ))
results_file="$(mktemp)"
trap 'rm -f "$results_file"' EXIT

worker() {
  while [ "$(date +%s)" -lt "$end_time" ]; do
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
      --request POST --header 'Content-Type: application/json' --data '{}' \
      --max-time 10 "$target" || printf '000')"
    printf '%s\n' "$status" >>"$results_file"
    sleep "$delay_seconds"
  done
}

printf 'Sending payment traffic to %s for %ss with %s workers...\n' \
  "$target" "$duration_seconds" "$concurrency"

for _ in $(seq 1 "$concurrency"); do
  worker &
done
wait

total="$(wc -l <"$results_file" | tr -d ' ')"
successful="$(awk '/^2[0-9][0-9]$/ {count++} END {print count+0}' "$results_file")"
server_errors="$(awk '/^5[0-9][0-9]$/ {count++} END {print count+0}' "$results_file")"
failed=$((total - successful))

printf 'Traffic complete: total=%s, successful=%s, failed=%s, server_5xx=%s\n' \
  "$total" "$successful" "$failed" "$server_errors"
