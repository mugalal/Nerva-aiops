# Demo ownership — ANNOUNCED Day 1 (gap fix)

Gap: every scenario needs a demo `payment-service` (healthy v1 / faulty v2)
on Kubernetes, but no runbook owned it.

Decision (team lead, Day 1):
- **M1 builds `apps/demo-microservices/payment-service/`** (this folder).
  Reason: M1 Day 2 "instrument real services" needs it anyway.
  Deliverables: app with `/health` + `/pay`, v1/v2 fault switch,
  Dockerfile, healthy + faulty k8s manifests.
- **M4 sets up the local Kubernetes cluster** (`infrastructure/kind-cluster.yaml`,
  kind or minikube + kubectl). Reason: M4 must roll back and scale on it.
  Deliverables: working cluster, `kubectl apply -f apps/demo-microservices/k8s/`,
  verified ROLLBACK + SCALE on the demo.

Acceptance: `payment-service:v1` healthy → deploy `:v2` → degradation visible
→ rollback restores health. Required for Day-5 and Day-7 gates.
