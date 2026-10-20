"""In-cluster live drill helper (stdlib only). Stream it into a pod:

    kubectl -n nexus-demo exec -i <pod> -- python - <command> [args] < scripts/live-drill.py

Credentials come only from the pod's own environment (M4: NEXUS_APPROVAL_TOKEN;
M5: M5_UI_OPERATOR_TOKEN) and are never printed. Every command prints JSON.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

M1 = "http://telemetry-intelligence:8001"
M2 = "http://anomaly-engine:8002"
M4 = "http://shared-nexus-api:8004"
M5 = "http://incident-memory:8005"


def call(method, url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw.decode(errors="replace")[:300]
        return exc.code, payload, dict(exc.headers)


def read_headers():
    """M4 requires a token on reads; use this pod's own credential (never printed)."""
    token = (os.environ.get("NEXUS_READ_TOKEN") or os.environ.get("NEXUS_INGEST_TOKEN")
             or os.environ.get("M5_SHARED_API_TOKEN"))
    return {"Authorization": "Bearer " + token} if token else {}


def now():
    return datetime.now(timezone.utc)


def operator():
    return {"Authorization": "Bearer " + os.environ["NEXUS_APPROVAL_TOKEN"], "X-Nexus-Approver": "hardening-drill-operator"}


def out(value):
    print(json.dumps(value, indent=1, default=str))


def main(command, *args):
    if command == "snapshot":
        out(call("GET", M1 + "/internal/telemetry/snapshot?service=payment-service")[:2])
    elif command == "baseline":
        end = now() - timedelta(seconds=15)
        out(call("POST", M1 + "/internal/baselines/measure", {"service": "payment-service",
            "start": (end - timedelta(seconds=75)).isoformat(), "end": end.isoformat(), "step_seconds": 15})[:2])
    elif command == "window":
        query = urllib.parse.urlencode({"service": "payment-service", "start": args[0], "end": args[1], "step_seconds": 15})
        out(call("GET", M1 + "/internal/telemetry/window?" + query, timeout=120)[1])
    elif command == "status":
        incidents = call("GET", M4 + "/api/incidents/", headers=read_headers())[1]
        out({"m4": [(i["incident_id"], i["status"], i["anomaly_ids"][-1:] if i["anomaly_ids"] else []) for i in incidents][-6:],
             "m2": [(c["incident_id"], c["status"]) for c in call("GET", M2 + "/internal/correlation/incidents?limit=5")[1]],
             "m2_health": call("GET", M2 + "/health")[1].get("dispatch")})
    elif command == "context":
        out(call("GET", M4 + f"/api/incidents/{args[0]}/context", headers=read_headers())[1])
    elif command == "m2detail":
        out(call("GET", M2 + f"/internal/correlation/incidents/{args[0]}")[1])
    elif command == "experiment":
        incident, run_id, scenario, injected, evidence = args
        out(call("POST", M4 + f"/api/incidents/{incident}/experiment", {"run_id": run_id, "scenario": scenario,
            "injection_time": injected, "injection_evidence": evidence}, operator())[:2])
    elif command == "link-latest":               # genuine latest M2 alert -> M4 ingest; run in the M4 pod
        event = call("GET", M2 + f"/internal/anomalies?incident_id={args[0]}&pick=latest")[1]
        ingest = {"Authorization": "Bearer " + os.environ["NEXUS_INGEST_TOKEN"], "X-Nexus-Sender": "operator-link-latest"}
        out({"event": event, "linked": call("POST", M4 + f"/internal/anomalies?incident_id={args[0]}", event, ingest)[:2]})
    elif command == "build":                     # operator-triggered (re-)diagnosis; run in the M4 pod
        out(call("POST", M4 + f"/internal/decisions/build/{args[0]}", None, operator(), timeout=120)[:2])
    elif command == "approve-without-digest":   # M4 must refuse (428); run in the M4 pod
        out(call("POST", M4 + f"/api/incidents/{args[0]}/approve", None, operator())[:2])
    elif command == "ui-approve":                # run in the M5 pod
        status, bundle, _ = call("GET", "http://127.0.0.1:8005/internal/ui/incidents/" + args[0])
        token = {"X-Nexus-Operator-Token": os.environ["M5_UI_OPERATOR_TOKEN"]}
        refused = call("POST", f"http://127.0.0.1:8005/internal/ui/incidents/{args[0]}/approval",
                       {"decision": "approve", "proposal_digest": bundle["proposal_digest"]}, timeout=30)[:2]
        decided = call("POST", f"http://127.0.0.1:8005/internal/ui/incidents/{args[0]}/approval",
                       {"decision": "approve", "proposal_digest": bundle["proposal_digest"]}, token, timeout=300)[:2]
        out({"digest": bundle["proposal_digest"], "proposal_state": bundle["proposal_state"],
             "without_operator_token": refused, "approved": decided, "approved_at": now().isoformat()})
    elif command == "memory":                    # run in the M5 pod
        out(call("GET", f"http://127.0.0.1:8005/internal/memory/{args[0]}?source={args[1]}")[:2])
    elif command == "copilot":                   # run in the M5 pod
        out(call("POST", "http://127.0.0.1:8005/internal/copilot/query",
                 {"incident_id": args[0], "source": args[1], "question": args[2]})[:2])
    elif command == "health":
        out({name: call("GET", url)[:2] for name, url in (("m4_ready", M4 + "/ready"), ("m4_live", M4 + "/live"))})
    else:
        raise SystemExit("unknown command " + command)


if __name__ == "__main__":
    main(*sys.argv[1:])
