"""The registered services, their dependencies and their open incidents (the topology view).

State comes only from recorded incidents: a service with no open incident is reported as
"no open incident", never as "healthy"; M4 does not measure health itself.
"""
from fastapi import APIRouter

from app.api.incidents import TERMINAL_STATUSES, committed_incidents
from shared.registry import get_registry

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("")
def list_services():
    incidents = committed_incidents()
    services = []
    for spec in get_registry().services:
        mine = [item for item in incidents if spec.name in item.get("affected_services", [])]
        open_incidents = [{"incident_id": item["incident_id"], "status": item["status"],
                           "kind": item.get("_kind") or "incident"}
                          for item in mine if item.get("status") not in TERMINAL_STATUSES]
        blocked = [{"incident_id": item["incident_id"], **item["_blocked_by"]}
                   for item in mine if item.get("_blocked_by") and item.get("status") == "ESCALATED"]
        services.append({"name": spec.name, "depends_on": list(spec.depends_on), "tier": spec.tier,
                         "open_incidents": open_incidents,
                         "state": "incident_open" if any(i["kind"] != "rightsizing" for i in open_incidents)
                         else "no_open_incident",
                         "latest_dependency_block": blocked[-1] if blocked else None})
    return {"services": services}
