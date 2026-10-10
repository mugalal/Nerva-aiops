# Coverage Ledger

Documented commit: `3cd0423` (2026-10-10 12:34 +03:00, local merge of M5 into `mugalal/integrate-m2`, **not yet pushed**), full hash `3cd04239175a204fc84df2aeabd79a67f8c09857`, tree `64ae68e`

Previous pin: `5a6858b` (2026-10-09). Superseded on 2026-10-10 when the Phase 0 redo was requested.

RESUME FROM: Phase 1 (`01_foundations.md`). Phase 0 (redo on `3cd0423`) is complete pending Galal's tier review.


> Scope rule: this ledger lists every file **tracked by git at the documented commit**, minus `docs/learning/` (rule 19). Untracked local files are listed in `00_inventory.md` §4, not here.


## Completeness counters

| Item | Found (Phase 0) | Documented | Status | How it was counted |
|---|---|---|---|---|
| Source files (tracked at commit) | 405 | 0 | ⬜ | 303 code/config/docs/data files + 102 evidence data files |
| Functions & methods | 1421 | 0 | ⬜ | 1421 total = 554 in application/script code + 867 in test code (AST count, nested functions included) |
| Classes | 254 | 0 | ⬜ | 200 in application/script code + 54 in test code |
| Config keys | 294 | 0 | ⬜ | leaf keys in Prometheus x2, Loki, Alloy x2, Grafana x2, kind, M5 compose (35), M5 CI workflow (41), M5 .env.example (22) + M1Settings (31) and RuntimeSettings (12) fields |
| Env vars | 127 | 0 | ⬜ | unique names read by code, set in manifests/compose/Dockerfiles/.env.example, or read by scripts |
| Hardcoded module constants | 154 | 0 | ⬜ | UPPER_CASE module-level names in non-test Python (thresholds, rates, weights, allow-lists, metric objects) |
| HTTP endpoints | 63 | 0 | ⬜ | 63 production routes + 4 test-only routes |
| PromQL / LogQL / SQL queries | 47 | 0 | ⬜ | 23 PromQL (17 M1 code + 6 Grafana) + 3 LogQL (1 M1 code + 2 smoke scripts) + 21 SQL (13 shared DDL + 2 shared templates + 1 M6 health probe + 5 in M5 storage) |
| Regexes | 17 | 0 | ⬜ | 11 in Python + 6 in Prometheus/Alloy relabel rules |
| Kubernetes resources | 37 | 0 | ⬜ | objects across 9 tracked manifest files (kind Cluster config not counted; the untracked M5 manifest is not counted) |
| Prometheus metrics NEXUS exposes | 11 | 0 | ⬜ | 6 in payment-service + 5 in M1; M2, M3, M4, M5, M6 expose none |
| ML models / artifacts | 3 | 0 | ⬜ | 2 detector classes (IsolationForestDetector, ZScoreDetector) + 1 frozen artifact (reference.json); runtime uses the z-score one |
| LLM call sites / prompts | 1 | 0 | ⬜ | M5 chat.py: one OpenAI-compatible chat call with a system prompt, tools and a JSON-schema answer |
| Tests | 588 | 0 | ⬜ | 584 Python test functions/methods in 54 files + 4 JavaScript tests in ui/copilot/storage.test.mjs |
| Dependencies | 47 | 0 | ⬜ | 35 Python requirement lines (12 unique packages) + 9 container images + 2 GitHub Actions + 1 downloaded binary (kubectl) |

The full lists behind each "Found" number are in `00_inventory.md` §9, so later phases can tick them off one by one.


## Files

| # | Path | Module | Tier | Status | Doc | Notes |
|---|---|---|---|---|---|---|
| 1 | observability/alloy/config.alloy | M1 | 2 | ⬜ | 05_files/observability/alloy/config.alloy.md |  |
| 2 | observability/grafana/dashboards/nexus-m1-overview.json | M1 | 1 | ⬜ | 05_files/observability/grafana/dashboards/nexus-m1-overview.json.md |  |
| 3 | observability/grafana/provisioning/dashboards/dashboards.yml | M1 | 3 | ⬜ | 05_files/observability/grafana/provisioning/dashboards/dashboards.yml.md |  |
| 4 | observability/grafana/provisioning/datasources/datasources.yml | M1 | 3 | ⬜ | 05_files/observability/grafana/provisioning/datasources/datasources.yml.md |  |
| 5 | observability/k8s/alloy.yaml | M1 | 1 | ⬜ | 05_files/observability/k8s/alloy.yaml.md |  |
| 6 | observability/k8s/loki.yaml | M1 | 2 | ⬜ | 05_files/observability/k8s/loki.yaml.md |  |
| 7 | observability/k8s/prometheus.yaml | M1 | 1 | ⬜ | 05_files/observability/k8s/prometheus.yaml.md |  |
| 8 | observability/prometheus/prometheus.yml | M1 | 1 | ⬜ | 05_files/observability/prometheus/prometheus.yml.md |  |
| 9 | services/telemetry-intelligence/Dockerfile | M1 | 2 | ⬜ | 05_files/services/telemetry-intelligence/Dockerfile.md |  |
| 10 | services/telemetry-intelligence/app/__init__.py | M1 | 3 | ⬜ | 05_files/services/telemetry-intelligence/app/__init__.py.md |  |
| 11 | services/telemetry-intelligence/app/baseline.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/baseline.py.md |  |
| 12 | services/telemetry-intelligence/app/config.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/config.py.md |  |
| 13 | services/telemetry-intelligence/app/errors.py | M1 | 3 | ⬜ | 05_files/services/telemetry-intelligence/app/errors.py.md |  |
| 14 | services/telemetry-intelligence/app/main.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/main.py.md |  |
| 15 | services/telemetry-intelligence/app/models.py | M1 | 2 | ⬜ | 05_files/services/telemetry-intelligence/app/models.py.md |  |
| 16 | services/telemetry-intelligence/app/providers/__init__.py | M1 | 3 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/__init__.py.md |  |
| 17 | services/telemetry-intelligence/app/providers/health.py | M1 | 2 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/health.py.md |  |
| 18 | services/telemetry-intelligence/app/providers/kubernetes.py | M1 | 2 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/kubernetes.py.md |  |
| 19 | services/telemetry-intelligence/app/providers/kubernetes_api.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/kubernetes_api.py.md |  |
| 20 | services/telemetry-intelligence/app/providers/loki.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/loki.py.md |  |
| 21 | services/telemetry-intelligence/app/providers/prometheus.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/prometheus.py.md |  |
| 22 | services/telemetry-intelligence/app/providers/resources.py | M1 | 2 | ⬜ | 05_files/services/telemetry-intelligence/app/providers/resources.py.md |  |
| 23 | services/telemetry-intelligence/app/service.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/service.py.md |  |
| 24 | services/telemetry-intelligence/app/storage.py | M1 | 2 | ⬜ | 05_files/services/telemetry-intelligence/app/storage.py.md |  |
| 25 | services/telemetry-intelligence/app/telemetry.py | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/app/telemetry.py.md |  |
| 26 | services/telemetry-intelligence/k8s/telemetry-intelligence.yaml | M1 | 1 | ⬜ | 05_files/services/telemetry-intelligence/k8s/telemetry-intelligence.yaml.md |  |
| 27 | services/telemetry-intelligence/requirements.txt | M1 | 3 | ⬜ | 05_files/services/telemetry-intelligence/requirements.txt.md |  |
| 28 | tests/m1/__init__.py | M1 | 3 | ⬜ | 05_files/tests/m1/__init__.py.md |  |
| 29 | tests/m1/support.py | M1 | 2 | ⬜ | 05_files/tests/m1/support.py.md |  |
| 30 | tests/m1/test_api.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_api.py.md |  |
| 31 | tests/m1/test_baseline_and_recovery.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_baseline_and_recovery.py.md |  |
| 32 | tests/m1/test_contracts.py | M1 | 3 | ⬜ | 05_files/tests/m1/test_contracts.py.md |  |
| 33 | tests/m1/test_evidence_preview.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_evidence_preview.py.md |  |
| 34 | tests/m1/test_kubernetes_provider.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_kubernetes_provider.py.md |  |
| 35 | tests/m1/test_payment_probe_concurrency.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_payment_probe_concurrency.py.md |  |
| 36 | tests/m1/test_prometheus_client_pool.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_prometheus_client_pool.py.md |  |
| 37 | tests/m1/test_prometheus_telemetry.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_prometheus_telemetry.py.md |  |
| 38 | tests/m1/test_storage.py | M1 | 2 | ⬜ | 05_files/tests/m1/test_storage.py.md |  |
| 39 | services/anomaly-engine/README.md | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/README.md.md |  |
| 40 | services/anomaly-engine/app/__init__.py | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/app/__init__.py.md |  |
| 41 | services/anomaly-engine/app/baseline.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/baseline.py.md |  |
| 42 | services/anomaly-engine/app/correlation.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/correlation.py.md |  |
| 43 | services/anomaly-engine/app/evaluation/__init__.py | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/app/evaluation/__init__.py.md |  |
| 44 | services/anomaly-engine/app/evaluation/compare.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/evaluation/compare.py.md |  |
| 45 | services/anomaly-engine/app/evaluation/metrics.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/evaluation/metrics.py.md |  |
| 46 | services/anomaly-engine/app/evaluation/run.py | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/app/evaluation/run.py.md |  |
| 47 | services/anomaly-engine/app/evaluation/scenarios.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/evaluation/scenarios.py.md |  |
| 48 | services/anomaly-engine/app/evaluation/tune.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/evaluation/tune.py.md |  |
| 49 | services/anomaly-engine/app/evidence.py | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/app/evidence.py.md |  |
| 50 | services/anomaly-engine/app/features.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/features.py.md |  |
| 51 | services/anomaly-engine/app/handoff.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/handoff.py.md |  |
| 52 | services/anomaly-engine/app/main.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/main.py.md |  |
| 53 | services/anomaly-engine/app/model.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/model.py.md |  |
| 54 | services/anomaly-engine/app/pipeline.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/pipeline.py.md |  |
| 55 | services/anomaly-engine/app/poller.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/poller.py.md |  |
| 56 | services/anomaly-engine/app/realdata/__init__.py | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/app/realdata/__init__.py.md |  |
| 57 | services/anomaly-engine/app/realdata/__main__.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/realdata/__main__.py.md |  |
| 58 | services/anomaly-engine/app/realdata/capture.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/realdata/capture.py.md |  |
| 59 | services/anomaly-engine/app/realdata/drill.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/realdata/drill.py.md |  |
| 60 | services/anomaly-engine/app/realdata/final.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/realdata/final.py.md |  |
| 61 | services/anomaly-engine/app/realdata/m1_client.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/realdata/m1_client.py.md |  |
| 62 | services/anomaly-engine/app/realdata/push.py | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/app/realdata/push.py.md |  |
| 63 | services/anomaly-engine/app/realdata/report.py | M2 | 2 | ⬜ | 05_files/services/anomaly-engine/app/realdata/report.py.md |  |
| 64 | services/anomaly-engine/app/realdata/train.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/realdata/train.py.md |  |
| 65 | services/anomaly-engine/app/reference.py | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/app/reference.py.md |  |
| 66 | services/anomaly-engine/capture1.json | M2 | ⏭️ | ⬜ | 05_files/services/anomaly-engine/capture1.json.md | data file: described, not walked through |
| 67 | services/anomaly-engine/capture2.json | M2 | ⏭️ | ⬜ | 05_files/services/anomaly-engine/capture2.json.md | data file: described, not walked through |
| 68 | services/anomaly-engine/capture3.json | M2 | ⏭️ | ⬜ | 05_files/services/anomaly-engine/capture3.json.md | data file: described, not walked through |
| 69 | services/anomaly-engine/data/.gitignore | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/data/.gitignore.md |  |
| 70 | services/anomaly-engine/reference/reference.json | M2 | 1 | ⬜ | 05_files/services/anomaly-engine/reference/reference.json.md |  |
| 71 | services/anomaly-engine/requirements.txt | M2 | 3 | ⬜ | 05_files/services/anomaly-engine/requirements.txt.md |  |
| 72 | services/anomaly-engine/results.csv | M2 | ⏭️ | ⬜ | 05_files/services/anomaly-engine/results.csv.md | data file: described, not walked through |
| 73 | tests/m2/conftest.py | M2 | 3 | ⬜ | 05_files/tests/m2/conftest.py.md |  |
| 74 | tests/m2/fake_shared_api.py | M2 | 2 | ⬜ | 05_files/tests/m2/fake_shared_api.py.md |  |
| 75 | tests/m2/m2_loader.py | M2 | 3 | ⬜ | 05_files/tests/m2/m2_loader.py.md |  |
| 76 | tests/m2/real_like.py | M2 | 3 | ⬜ | 05_files/tests/m2/real_like.py.md |  |
| 77 | tests/m2/serve.py | M2 | 3 | ⬜ | 05_files/tests/m2/serve.py.md |  |
| 78 | tests/m2/test_anomaly_engine.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_anomaly_engine.py.md |  |
| 79 | tests/m2/test_context_features.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_context_features.py.md |  |
| 80 | tests/m2/test_evaluation.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_evaluation.py.md |  |
| 81 | tests/m2/test_final_tools.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_final_tools.py.md |  |
| 82 | tests/m2/test_handoff.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_handoff.py.md |  |
| 83 | tests/m2/test_model.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_model.py.md |  |
| 84 | tests/m2/test_pipeline.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_pipeline.py.md |  |
| 85 | tests/m2/test_realdata.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_realdata.py.md |  |
| 86 | tests/m2/test_service_flow.py | M2 | 2 | ⬜ | 05_files/tests/m2/test_service_flow.py.md |  |
| 87 | services/root-cause-analysis/Dockerfile | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/Dockerfile.md |  |
| 88 | services/root-cause-analysis/app/__init__.py | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/app/__init__.py.md |  |
| 89 | services/root-cause-analysis/app/explanation.py | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/app/explanation.py.md | empty file |
| 90 | services/root-cause-analysis/app/main.py | M3 | 1 | ⬜ | 05_files/services/root-cause-analysis/app/main.py.md |  |
| 91 | services/root-cause-analysis/app/models.py | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/app/models.py.md |  |
| 92 | services/root-cause-analysis/app/providers/__init__.py | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/app/providers/__init__.py.md |  |
| 93 | services/root-cause-analysis/app/providers/base.py | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/app/providers/base.py.md |  |
| 94 | services/root-cause-analysis/app/providers/http.py | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/app/providers/http.py.md |  |
| 95 | services/root-cause-analysis/app/providers/mock.py | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/app/providers/mock.py.md |  |
| 96 | services/root-cause-analysis/app/scoring.py | M3 | 1 | ⬜ | 05_files/services/root-cause-analysis/app/scoring.py.md |  |
| 97 | services/root-cause-analysis/app/service.py | M3 | 1 | ⬜ | 05_files/services/root-cause-analysis/app/service.py.md |  |
| 98 | services/root-cause-analysis/k8s/root-cause-analysis.yaml | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/k8s/root-cause-analysis.yaml.md |  |
| 99 | services/root-cause-analysis/requirements-dev.txt | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/requirements-dev.txt.md |  |
| 100 | services/root-cause-analysis/requirements.txt | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/requirements.txt.md |  |
| 101 | services/root-cause-analysis/tests/test_api.py | M3 | 3 | ⬜ | 05_files/services/root-cause-analysis/tests/test_api.py.md |  |
| 102 | services/root-cause-analysis/tests/test_real_providers.py | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/tests/test_real_providers.py.md |  |
| 103 | services/root-cause-analysis/tests/test_scoring.py | M3 | 2 | ⬜ | 05_files/services/root-cause-analysis/tests/test_scoring.py.md |  |
| 104 | services/shared-nexus-api/Dockerfile | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/Dockerfile.md |  |
| 105 | services/shared-nexus-api/app/__init__.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/__init__.py.md |  |
| 106 | services/shared-nexus-api/app/api/anomalies.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/api/anomalies.py.md |  |
| 107 | services/shared-nexus-api/app/api/approvals.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/api/approvals.py.md |  |
| 108 | services/shared-nexus-api/app/api/decisions.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/api/decisions.py.md |  |
| 109 | services/shared-nexus-api/app/api/incidents.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/api/incidents.py.md |  |
| 110 | services/shared-nexus-api/app/api/recovery.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/api/recovery.py.md |  |
| 111 | services/shared-nexus-api/app/api/remediation.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/api/remediation.py.md |  |
| 112 | services/shared-nexus-api/app/config.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/config.py.md |  |
| 113 | services/shared-nexus-api/app/contracts.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/contracts.py.md |  |
| 114 | services/shared-nexus-api/app/decision_engine/engine.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/decision_engine/engine.py.md |  |
| 115 | services/shared-nexus-api/app/decision_engine/models.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/decision_engine/models.py.md |  |
| 116 | services/shared-nexus-api/app/main.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/main.py.md |  |
| 117 | services/shared-nexus-api/app/orchestration/__init__.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/orchestration/__init__.py.md |  |
| 118 | services/shared-nexus-api/app/orchestration/orchestrator.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/orchestration/orchestrator.py.md | has UNCOMMITTED local edits (see 00_inventory.md §4.1) |
| 119 | services/shared-nexus-api/app/providers/errors.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/providers/errors.py.md |  |
| 120 | services/shared-nexus-api/app/providers/evidence_provider.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/providers/evidence_provider.py.md |  |
| 121 | services/shared-nexus-api/app/providers/finops_provider.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/providers/finops_provider.py.md |  |
| 122 | services/shared-nexus-api/app/providers/rca_provider.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/providers/rca_provider.py.md |  |
| 123 | services/shared-nexus-api/app/providers/recovery_provider.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/providers/recovery_provider.py.md |  |
| 124 | services/shared-nexus-api/app/remediation/__init__.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/remediation/__init__.py.md |  |
| 125 | services/shared-nexus-api/app/remediation/audit.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/remediation/audit.py.md |  |
| 126 | services/shared-nexus-api/app/remediation/executor.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/remediation/executor.py.md |  |
| 127 | services/shared-nexus-api/app/remediation/guardrails.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/remediation/guardrails.py.md |  |
| 128 | services/shared-nexus-api/app/remediation/jenkins_executor.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/app/remediation/jenkins_executor.py.md | disabled at runtime (executor.py:L43) |
| 129 | services/shared-nexus-api/app/remediation/models.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/remediation/models.py.md |  |
| 130 | services/shared-nexus-api/app/state_machine/__init__.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/state_machine/__init__.py.md |  |
| 131 | services/shared-nexus-api/app/state_machine/machine.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/state_machine/machine.py.md |  |
| 132 | services/shared-nexus-api/app/state_machine/states.py | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/app/state_machine/states.py.md |  |
| 133 | services/shared-nexus-api/app/state_machine/test_manual.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/app/state_machine/test_manual.py.md | scratch file inside app/ |
| 134 | services/shared-nexus-api/install_kubectl.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/install_kubectl.py.md |  |
| 135 | services/shared-nexus-api/k8s/shared-nexus-api.yaml | M4 | 1 | ⬜ | 05_files/services/shared-nexus-api/k8s/shared-nexus-api.yaml.md |  |
| 136 | services/shared-nexus-api/pyproject.toml | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/pyproject.toml.md |  |
| 137 | services/shared-nexus-api/readme.md | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/readme.md.md |  |
| 138 | services/shared-nexus-api/requirements.txt | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/requirements.txt.md |  |
| 139 | services/shared-nexus-api/shared_nexus_providers/__init__.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/shared_nexus_providers/__init__.py.md | not imported anywhere |
| 140 | services/shared-nexus-api/shared_nexus_providers/providers/__init__.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/shared_nexus_providers/providers/__init__.py.md | not imported anywhere |
| 141 | services/shared-nexus-api/shared_nexus_providers/providers/base.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/shared_nexus_providers/providers/base.py.md | not imported anywhere |
| 142 | services/shared-nexus-api/shared_nexus_providers/providers/errors.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/shared_nexus_providers/providers/errors.py.md | not imported anywhere |
| 143 | services/shared-nexus-api/tests/conftest.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/conftest.py.md |  |
| 144 | services/shared-nexus-api/tests/test_decision_api.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_decision_api.py.md |  |
| 145 | services/shared-nexus-api/tests/test_decision_engine.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/tests/test_decision_engine.py.md |  |
| 146 | services/shared-nexus-api/tests/test_decision_models.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_decision_models.py.md |  |
| 147 | services/shared-nexus-api/tests/test_e2e_m4_flow.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_e2e_m4_flow.py.md |  |
| 148 | services/shared-nexus-api/tests/test_finops_provider.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_finops_provider.py.md |  |
| 149 | services/shared-nexus-api/tests/test_guardrails.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_guardrails.py.md |  |
| 150 | services/shared-nexus-api/tests/test_incidents_api.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/tests/test_incidents_api.py.md |  |
| 151 | services/shared-nexus-api/tests/test_jenkins_executor.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_jenkins_executor.py.md | disabled at runtime (executor.py:L43) |
| 152 | services/shared-nexus-api/tests/test_kubernetes_safety.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/tests/test_kubernetes_safety.py.md |  |
| 153 | services/shared-nexus-api/tests/test_orchestrator.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/tests/test_orchestrator.py.md |  |
| 154 | services/shared-nexus-api/tests/test_rca_provider.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_rca_provider.py.md |  |
| 155 | services/shared-nexus-api/tests/test_real_integration.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/tests/test_real_integration.py.md |  |
| 156 | services/shared-nexus-api/tests/test_recovery_api.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_recovery_api.py.md |  |
| 157 | services/shared-nexus-api/tests/test_remediation_api.py | M4 | 2 | ⬜ | 05_files/services/shared-nexus-api/tests/test_remediation_api.py.md |  |
| 158 | services/shared-nexus-api/tests/test_remediation_executor.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_remediation_executor.py.md |  |
| 159 | services/shared-nexus-api/tests/test_state_machine.py | M4 | 3 | ⬜ | 05_files/services/shared-nexus-api/tests/test_state_machine.py.md |  |
| 160 | .github/workflows/m5.yml | M5 | 2 | ⬜ | 05_files/.github/workflows/m5.yml.md | has UNCOMMITTED local edits (see 00_inventory.md §4.1) |
| 161 | services/incident-memory/.env.example | M5 | 3 | ⬜ | 05_files/services/incident-memory/.env.example.md |  |
| 162 | services/incident-memory/Dockerfile | M5 | 2 | ⬜ | 05_files/services/incident-memory/Dockerfile.md | has UNCOMMITTED local edits (see 00_inventory.md §4.1) |
| 163 | services/incident-memory/README.md | M5 | 3 | ⬜ | 05_files/services/incident-memory/README.md.md |  |
| 164 | services/incident-memory/app/__init__.py | M5 | 3 | ⬜ | 05_files/services/incident-memory/app/__init__.py.md |  |
| 165 | services/incident-memory/app/actions.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/app/actions.py.md |  |
| 166 | services/incident-memory/app/chat.py | M5 | 1 | ⬜ | 05_files/services/incident-memory/app/chat.py.md |  |
| 167 | services/incident-memory/app/copilot.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/app/copilot.py.md |  |
| 168 | services/incident-memory/app/fixtures.py | M5 | 3 | ⬜ | 05_files/services/incident-memory/app/fixtures.py.md |  |
| 169 | services/incident-memory/app/main.py | M5 | 1 | ⬜ | 05_files/services/incident-memory/app/main.py.md |  |
| 170 | services/incident-memory/app/models.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/app/models.py.md |  |
| 171 | services/incident-memory/app/providers.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/app/providers.py.md |  |
| 172 | services/incident-memory/app/retrieval.py | M5 | 1 | ⬜ | 05_files/services/incident-memory/app/retrieval.py.md |  |
| 173 | services/incident-memory/app/storage.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/app/storage.py.md |  |
| 174 | services/incident-memory/app/tools.py | M5 | 1 | ⬜ | 05_files/services/incident-memory/app/tools.py.md |  |
| 175 | services/incident-memory/compose.yml | M5 | 2 | ⬜ | 05_files/services/incident-memory/compose.yml.md |  |
| 176 | services/incident-memory/requirements.txt | M5 | 3 | ⬜ | 05_files/services/incident-memory/requirements.txt.md | has UNCOMMITTED local edits (see 00_inventory.md §4.1) |
| 177 | services/incident-memory/scripts/evaluate.py | M5 | 3 | ⬜ | 05_files/services/incident-memory/scripts/evaluate.py.md |  |
| 178 | services/incident-memory/scripts/seed_demo.py | M5 | 3 | ⬜ | 05_files/services/incident-memory/scripts/seed_demo.py.md |  |
| 179 | services/incident-memory/scripts/verify_docker.py | M5 | 3 | ⬜ | 05_files/services/incident-memory/scripts/verify_docker.py.md |  |
| 180 | services/incident-memory/tests/test_chat.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/tests/test_chat.py.md |  |
| 181 | services/incident-memory/tests/test_m5.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/tests/test_m5.py.md |  |
| 182 | services/incident-memory/tests/test_postgres.py | M5 | 3 | ⬜ | 05_files/services/incident-memory/tests/test_postgres.py.md |  |
| 183 | services/incident-memory/tests/test_tools.py | M5 | 2 | ⬜ | 05_files/services/incident-memory/tests/test_tools.py.md |  |
| 184 | ui/app.js | M5 | 2 | ⬜ | 05_files/ui/app.js.md |  |
| 185 | ui/components/render.js | M5 | 3 | ⬜ | 05_files/ui/components/render.js.md |  |
| 186 | ui/copilot/storage.mjs | M5 | 3 | ⬜ | 05_files/ui/copilot/storage.mjs.md |  |
| 187 | ui/copilot/storage.test.mjs | M5 | 3 | ⬜ | 05_files/ui/copilot/storage.test.mjs.md |  |
| 188 | ui/copilot/view.js | M5 | 3 | ⬜ | 05_files/ui/copilot/view.js.md |  |
| 189 | ui/incident/view.js | M5 | 3 | ⬜ | 05_files/ui/incident/view.js.md |  |
| 190 | ui/index.html | M5 | 3 | ⬜ | 05_files/ui/index.html.md |  |
| 191 | ui/overview/view.js | M5 | 3 | ⬜ | 05_files/ui/overview/view.js.md |  |
| 192 | ui/styles.css | M5 | 3 | ⬜ | 05_files/ui/styles.css.md |  |
| 193 | scripts/check-m6.sh | M6 | 3 | ⬜ | 05_files/scripts/check-m6.sh.md |  |
| 194 | services/finops-engine/Dockerfile | M6 | 2 | ⬜ | 05_files/services/finops-engine/Dockerfile.md |  |
| 195 | services/finops-engine/README.md | M6 | 3 | ⬜ | 05_files/services/finops-engine/README.md.md |  |
| 196 | services/finops-engine/app/__init__.py | M6 | 3 | ⬜ | 05_files/services/finops-engine/app/__init__.py.md |  |
| 197 | services/finops-engine/app/context_provider.py | M6 | 1 | ⬜ | 05_files/services/finops-engine/app/context_provider.py.md |  |
| 198 | services/finops-engine/app/cost_model.py | M6 | 1 | ⬜ | 05_files/services/finops-engine/app/cost_model.py.md |  |
| 199 | services/finops-engine/app/live_recommend.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/app/live_recommend.py.md |  |
| 200 | services/finops-engine/app/m1_client.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/app/m1_client.py.md |  |
| 201 | services/finops-engine/app/main.py | M6 | 1 | ⬜ | 05_files/services/finops-engine/app/main.py.md |  |
| 202 | services/finops-engine/app/models.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/app/models.py.md |  |
| 203 | services/finops-engine/app/persistence.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/app/persistence.py.md |  |
| 204 | services/finops-engine/app/resource_config.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/app/resource_config.py.md |  |
| 205 | services/finops-engine/app/rightsizing.py | M6 | 1 | ⬜ | 05_files/services/finops-engine/app/rightsizing.py.md |  |
| 206 | services/finops-engine/app/scale_options.py | M6 | 1 | ⬜ | 05_files/services/finops-engine/app/scale_options.py.md |  |
| 207 | services/finops-engine/k8s/deployment.yaml | M6 | 2 | ⬜ | 05_files/services/finops-engine/k8s/deployment.yaml.md |  |
| 208 | services/finops-engine/requirements.txt | M6 | 3 | ⬜ | 05_files/services/finops-engine/requirements.txt.md |  |
| 209 | services/finops-engine/tests/test_context.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/tests/test_context.py.md |  |
| 210 | services/finops-engine/tests/test_contracts.py | M6 | 3 | ⬜ | 05_files/services/finops-engine/tests/test_contracts.py.md |  |
| 211 | services/finops-engine/tests/test_db.py | M6 | 3 | ⬜ | 05_files/services/finops-engine/tests/test_db.py.md |  |
| 212 | services/finops-engine/tests/test_export.py | M6 | 3 | ⬜ | 05_files/services/finops-engine/tests/test_export.py.md |  |
| 213 | services/finops-engine/tests/test_health.py | M6 | 3 | ⬜ | 05_files/services/finops-engine/tests/test_health.py.md |  |
| 214 | services/finops-engine/tests/test_logic.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/tests/test_logic.py.md |  |
| 215 | services/finops-engine/tests/test_m1_client.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/tests/test_m1_client.py.md |  |
| 216 | services/finops-engine/tests/test_real_context.py | M6 | 2 | ⬜ | 05_files/services/finops-engine/tests/test_real_context.py.md |  |
| 217 | services/finops-engine/tests/test_requests.py | M6 | 3 | ⬜ | 05_files/services/finops-engine/tests/test_requests.py.md |  |
| 218 | contracts/README.md | Shared | 3 | ⬜ | 05_files/contracts/README.md.md |  |
| 219 | contracts/action_result.json | Shared | 3 | ⬜ | 05_files/contracts/action_result.json.md |  |
| 220 | contracts/anomaly_event.json | Shared | 3 | ⬜ | 05_files/contracts/anomaly_event.json.md |  |
| 221 | contracts/decision_proposal.json | Shared | 3 | ⬜ | 05_files/contracts/decision_proposal.json.md |  |
| 222 | contracts/deployment_event.json | Shared | 3 | ⬜ | 05_files/contracts/deployment_event.json.md |  |
| 223 | contracts/finops_context.json | Shared | 3 | ⬜ | 05_files/contracts/finops_context.json.md |  |
| 224 | contracts/finops_recommendation.json | Shared | 3 | ⬜ | 05_files/contracts/finops_recommendation.json.md |  |
| 225 | contracts/incident.json | Shared | 3 | ⬜ | 05_files/contracts/incident.json.md |  |
| 226 | contracts/incident_memory.json | Shared | 3 | ⬜ | 05_files/contracts/incident_memory.json.md |  |
| 227 | contracts/rca_result.json | Shared | 3 | ⬜ | 05_files/contracts/rca_result.json.md |  |
| 228 | contracts/recovery_result.json | Shared | 3 | ⬜ | 05_files/contracts/recovery_result.json.md |  |
| 229 | contracts/telemetry_snapshot.json | Shared | 3 | ⬜ | 05_files/contracts/telemetry_snapshot.json.md |  |
| 230 | infrastructure/postgres/docker-compose.yml | Shared | 2 | ⬜ | 05_files/infrastructure/postgres/docker-compose.yml.md |  |
| 231 | mocks/mock_action_result.json | Shared | 3 | ⬜ | 05_files/mocks/mock_action_result.json.md |  |
| 232 | mocks/mock_anomaly_event.json | Shared | 3 | ⬜ | 05_files/mocks/mock_anomaly_event.json.md |  |
| 233 | mocks/mock_decision.json | Shared | 3 | ⬜ | 05_files/mocks/mock_decision.json.md |  |
| 234 | mocks/mock_deployment_event.json | Shared | 3 | ⬜ | 05_files/mocks/mock_deployment_event.json.md |  |
| 235 | mocks/mock_finops_context.json | Shared | 3 | ⬜ | 05_files/mocks/mock_finops_context.json.md |  |
| 236 | mocks/mock_finops_recommendation.json | Shared | 3 | ⬜ | 05_files/mocks/mock_finops_recommendation.json.md |  |
| 237 | mocks/mock_incident.json | Shared | 3 | ⬜ | 05_files/mocks/mock_incident.json.md |  |
| 238 | mocks/mock_incident_memory.json | Shared | 3 | ⬜ | 05_files/mocks/mock_incident_memory.json.md |  |
| 239 | mocks/mock_metrics.json | Shared | 3 | ⬜ | 05_files/mocks/mock_metrics.json.md |  |
| 240 | mocks/mock_rca_response.json | Shared | 3 | ⬜ | 05_files/mocks/mock_rca_response.json.md |  |
| 241 | mocks/mock_recovery.json | Shared | 3 | ⬜ | 05_files/mocks/mock_recovery.json.md |  |
| 242 | scripts/export-experiments.sh | Shared | 3 | ⬜ | 05_files/scripts/export-experiments.sh.md |  |
| 243 | scripts/init-db.sh | Shared | 3 | ⬜ | 05_files/scripts/init-db.sh.md |  |
| 244 | services/shared-nexus-api/app/db/__init__.py | Shared | 3 | ⬜ | 05_files/services/shared-nexus-api/app/db/__init__.py.md |  |
| 245 | services/shared-nexus-api/app/db/config.py | Shared | 3 | ⬜ | 05_files/services/shared-nexus-api/app/db/config.py.md |  |
| 246 | services/shared-nexus-api/app/db/connection.py | Shared | 3 | ⬜ | 05_files/services/shared-nexus-api/app/db/connection.py.md |  |
| 247 | services/shared-nexus-api/app/db/export_experiments.py | Shared | 2 | ⬜ | 05_files/services/shared-nexus-api/app/db/export_experiments.py.md |  |
| 248 | services/shared-nexus-api/app/db/init_db.py | Shared | 3 | ⬜ | 05_files/services/shared-nexus-api/app/db/init_db.py.md |  |
| 249 | services/shared-nexus-api/app/db/repository.py | Shared | 2 | ⬜ | 05_files/services/shared-nexus-api/app/db/repository.py.md |  |
| 250 | services/shared-nexus-api/app/db/schema.sql | Shared | 2 | ⬜ | 05_files/services/shared-nexus-api/app/db/schema.sql.md |  |
| 251 | shared/config/__init__.py | Shared | 3 | ⬜ | 05_files/shared/config/__init__.py.md |  |
| 252 | shared/config/settings.py | Shared | 2 | ⬜ | 05_files/shared/config/settings.py.md |  |
| 253 | shared/contracts/__init__.py | Shared | 3 | ⬜ | 05_files/shared/contracts/__init__.py.md |  |
| 254 | shared/contracts/models.py | Shared | 2 | ⬜ | 05_files/shared/contracts/models.py.md |  |
| 255 | shared/logging/__init__.py | Shared | 3 | ⬜ | 05_files/shared/logging/__init__.py.md |  |
| 256 | shared/logging/json_logging.py | Shared | 2 | ⬜ | 05_files/shared/logging/json_logging.py.md |  |
| 257 | tests/contract/test_contract_models.py | Shared | 2 | ⬜ | 05_files/tests/contract/test_contract_models.py.md |  |
| 258 | apps/demo-microservices/README.md | Demo | 3 | ⬜ | 05_files/apps/demo-microservices/README.md.md |  |
| 259 | apps/demo-microservices/k8s/payment-service-v2-faulty.yaml | Demo | 2 | ⬜ | 05_files/apps/demo-microservices/k8s/payment-service-v2-faulty.yaml.md |  |
| 260 | apps/demo-microservices/k8s/payment-service.yaml | Demo | 2 | ⬜ | 05_files/apps/demo-microservices/k8s/payment-service.yaml.md |  |
| 261 | apps/demo-microservices/payment-service/Dockerfile | Demo | 2 | ⬜ | 05_files/apps/demo-microservices/payment-service/Dockerfile.md |  |
| 262 | apps/demo-microservices/payment-service/app.py | Demo | 1 | ⬜ | 05_files/apps/demo-microservices/payment-service/app.py.md |  |
| 263 | apps/demo-microservices/payment-service/requirements.txt | Demo | 3 | ⬜ | 05_files/apps/demo-microservices/payment-service/requirements.txt.md |  |
| 264 | observability/docker-compose.faulty.yml | Demo | 2 | ⬜ | 05_files/observability/docker-compose.faulty.yml.md |  |
| 265 | scripts/generate-traffic.ps1 | Demo | 2 | ⬜ | 05_files/scripts/generate-traffic.ps1.md |  |
| 266 | scripts/generate-traffic.sh | Demo | 2 | ⬜ | 05_files/scripts/generate-traffic.sh.md |  |
| 267 | scripts/k8s-traffic.py | Demo | 2 | ⬜ | 05_files/scripts/k8s-traffic.py.md |  |
| 268 | tests/test_k8s_traffic.py | Demo | 3 | ⬜ | 05_files/tests/test_k8s_traffic.py.md |  |
| 269 | infrastructure/README.md | Platform | 3 | ⬜ | 05_files/infrastructure/README.md.md |  |
| 270 | infrastructure/k8s/traffic-demo.yaml | Platform | 2 | ⬜ | 05_files/infrastructure/k8s/traffic-demo.yaml.md |  |
| 271 | infrastructure/kind-cluster.yaml | Platform | 2 | ⬜ | 05_files/infrastructure/kind-cluster.yaml.md |  |
| 272 | observability/docker-compose.yml | Platform | 1 | ⬜ | 05_files/observability/docker-compose.yml.md | has UNCOMMITTED local edits (see 00_inventory.md §4.1) |
| 273 | observability/k8s/namespace.yaml | Platform | 3 | ⬜ | 05_files/observability/k8s/namespace.yaml.md |  |
| 274 | scripts/run-integration-incident.ps1 | Integration | 1 | ⬜ | 05_files/scripts/run-integration-incident.ps1.md |  |
| 275 | tests/integration/README.md | Integration | 3 | ⬜ | 05_files/tests/integration/README.md.md |  |
| 276 | tests/integration/controlled_m1_server.py | Integration | 2 | ⬜ | 05_files/tests/integration/controlled_m1_server.py.md |  |
| 277 | tests/integration/test_m2_http_flow.py | Integration | 2 | ⬜ | 05_files/tests/integration/test_m2_http_flow.py.md |  |
| 278 | tests/integration/test_real_http_flow.py | Integration | 2 | ⬜ | 05_files/tests/integration/test_real_http_flow.py.md |  |
| 279 | tests/__init__.py | Tests | 3 | ⬜ | 05_files/tests/__init__.py.md |  |
| 280 | .dockerignore | Tooling | 3 | ⬜ | 05_files/.dockerignore.md |  |
| 281 | .github/pull_request_template.md | Tooling | 3 | ⬜ | 05_files/.github/pull_request_template.md.md |  |
| 282 | .gitignore | Tooling | 3 | ⬜ | 05_files/.gitignore.md |  |
| 283 | scripts/check-env.ps1 | Tooling | 3 | ⬜ | 05_files/scripts/check-env.ps1.md |  |
| 284 | scripts/check-env.sh | Tooling | 3 | ⬜ | 05_files/scripts/check-env.sh.md |  |
| 285 | scripts/smoke-check.ps1 | Tooling | 2 | ⬜ | 05_files/scripts/smoke-check.ps1.md |  |
| 286 | scripts/smoke-check.sh | Tooling | 2 | ⬜ | 05_files/scripts/smoke-check.sh.md |  |
| 287 | README.md | Docs | 3 | ⬜ | 05_files/README.md.md |  |
| 288 | docs/GITHUB_WORKFLOW.md | Docs | 3 | ⬜ | 05_files/docs/GITHUB_WORKFLOW.md.md |  |
| 289 | docs/KICKOFF.md | Docs | 3 | ⬜ | 05_files/docs/KICKOFF.md.md |  |
| 290 | docs/M1_FIX_VALIDATION_2026-10-08.md | Docs | 3 | ⬜ | 05_files/docs/M1_FIX_VALIDATION_2026-10-08.md.md |  |
| 291 | docs/M1_K8S_SCALING_DRILL.md | Docs | 3 | ⬜ | 05_files/docs/M1_K8S_SCALING_DRILL.md.md |  |
| 292 | docs/M1_REVIEW_2026-10-08.md | Docs | 3 | ⬜ | 05_files/docs/M1_REVIEW_2026-10-08.md.md |  |
| 293 | docs/M5_AI_CHAT.md | Docs | 3 | ⬜ | 05_files/docs/M5_AI_CHAT.md.md |  |
| 294 | docs/M5_COPILOT_TOOLS.md | Docs | 3 | ⬜ | 05_files/docs/M5_COPILOT_TOOLS.md.md |  |
| 295 | docs/M5_DEMO.md | Docs | 3 | ⬜ | 05_files/docs/M5_DEMO.md.md |  |
| 296 | docs/M5_EVALUATION.md | Docs | 3 | ⬜ | 05_files/docs/M5_EVALUATION.md.md |  |
| 297 | docs/M5_INTEGRATION.md | Docs | 3 | ⬜ | 05_files/docs/M5_INTEGRATION.md.md |  |
| 298 | docs/M5_POST_MERGE_PROMPT.md | Docs | 3 | ⬜ | 05_files/docs/M5_POST_MERGE_PROMPT.md.md |  |
| 299 | docs/M5_PR.md | Docs | 3 | ⬜ | 05_files/docs/M5_PR.md.md |  |
| 300 | docs/MEMBER_BRANCH_REVIEW_2026-10-08.md | Docs | 3 | ⬜ | 05_files/docs/MEMBER_BRANCH_REVIEW_2026-10-08.md.md |  |
| 301 | docs/REAL_SERVICE_INTEGRATION.md | Docs | 3 | ⬜ | 05_files/docs/REAL_SERVICE_INTEGRATION.md.md |  |
| 302 | docs/m6-contract-map.md | Docs | 3 | ⬜ | 05_files/docs/m6-contract-map.md.md |  |
| 303 | docs/runtime-conventions.md | Docs | 3 | ⬜ | 05_files/docs/runtime-conventions.md.md |  |
| 304 | docs/evidence/m1-k8s-20261009T113539Z/action-completed-at.txt | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 32 bytes; described, not walked through |
| 305 | docs/evidence/m1-k8s-20261009T113539Z/after-snapshot.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 447 bytes; described, not walked through |
| 306 | docs/evidence/m1-k8s-20261009T113539Z/baseline-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 63340 bytes; described, not walked through |
| 307 | docs/evidence/m1-k8s-20261009T113539Z/baseline.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1830 bytes; described, not walked through |
| 308 | docs/evidence/m1-k8s-20261009T113539Z/before-snapshot.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 435 bytes; described, not walked through |
| 309 | docs/evidence/m1-k8s-20261009T113539Z/deployment-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8802 bytes; described, not walked through |
| 310 | docs/evidence/m1-k8s-20261009T113539Z/deployment-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8802 bytes; described, not walked through |
| 311 | docs/evidence/m1-k8s-20261009T113539Z/health.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1399 bytes; described, not walked through |
| 312 | docs/evidence/m1-k8s-20261009T113539Z/incident-evidence.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 14369 bytes; described, not walked through |
| 313 | docs/evidence/m1-k8s-20261009T113539Z/incident-id.txt | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 34 bytes; described, not walked through |
| 314 | docs/evidence/m1-k8s-20261009T113539Z/payment-pods-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 40547 bytes; described, not walked through |
| 315 | docs/evidence/m1-k8s-20261009T113539Z/payment-pods-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 13600 bytes; described, not walked through |
| 316 | docs/evidence/m1-k8s-20261009T113539Z/per-pod-request-rate.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1647 bytes; described, not walked through |
| 317 | docs/evidence/m1-k8s-20261009T113539Z/recovery-evidence.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 5238 bytes; described, not walked through |
| 318 | docs/evidence/m1-k8s-20261009T113539Z/recovery-result.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 404 bytes; described, not walked through |
| 319 | docs/evidence/m1-k8s-20261009T113539Z/spike-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 27232 bytes; described, not walked through |
| 320 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/action.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 241 bytes; described, not walked through |
| 321 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/anomaly.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 341 bytes; described, not walked through |
| 322 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/decision.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 248 bytes; described, not walked through |
| 323 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/evidence.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8698 bytes; described, not walked through |
| 324 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 209 bytes; described, not walked through |
| 325 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/proposal.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 294 bytes; described, not walked through |
| 326 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/rca.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 347 bytes; described, not walked through |
| 327 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/recovery-incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 243 bytes; described, not walked through |
| 328 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/recovery-later.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 2431 bytes; described, not walked through |
| 329 | docs/evidence/real-integration-2026-10-09/INC-K8S-SCALE-20261009/recovery.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 2432 bytes; described, not walked through |
| 330 | docs/evidence/real-integration-2026-10-09/README.md | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 4845 bytes; described, not walked through |
| 331 | docs/evidence/real-integration-2026-10-09/baseline.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1142 bytes; described, not walked through |
| 332 | docs/evidence/real-integration-2026-10-09/rollback/action.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 246 bytes; described, not walked through |
| 333 | docs/evidence/real-integration-2026-10-09/rollback/anomaly.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 359 bytes; described, not walked through |
| 334 | docs/evidence/real-integration-2026-10-09/rollback/decision.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 143 bytes; described, not walked through |
| 335 | docs/evidence/real-integration-2026-10-09/rollback/evidence.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 6937 bytes; described, not walked through |
| 336 | docs/evidence/real-integration-2026-10-09/rollback/incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 211 bytes; described, not walked through |
| 337 | docs/evidence/real-integration-2026-10-09/rollback/proposal.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 300 bytes; described, not walked through |
| 338 | docs/evidence/real-integration-2026-10-09/rollback/recovery-incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 247 bytes; described, not walked through |
| 339 | docs/evidence/real-integration-2026-10-09/rollback/recovery.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 2460 bytes; described, not walked through |
| 340 | docs/evidence/real-integration-2026-10-09/rollback/traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 32390 bytes; described, not walked through |
| 341 | docs/evidence/real-integration-2026-10-09/scale-baseline-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 13260 bytes; described, not walked through |
| 342 | docs/evidence/real-integration-2026-10-09/scale-baseline.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1146 bytes; described, not walked through |
| 343 | docs/evidence/real-integration-2026-10-09/scale-finops-context.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 437 bytes; described, not walked through |
| 344 | docs/evidence/real-integration-2026-10-09/scale-pod-health.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 5263 bytes; described, not walked through |
| 345 | docs/evidence/real-integration-2026-10-09/scale-pod-request-rates.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1919 bytes; described, not walked through |
| 346 | docs/evidence/real-integration-2026-10-09/scale-preview-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8675 bytes; described, not walked through |
| 347 | docs/evidence/real-integration-2026-10-09/scale-preview-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8736 bytes; described, not walked through |
| 348 | docs/evidence/real-integration-2026-10-09/scale-traffic-before-action.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 650 bytes; described, not walked through |
| 349 | docs/evidence/real-integration-2026-10-09/scale-traffic-config.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 376 bytes; described, not walked through |
| 350 | docs/evidence/real-integration-2026-10-09/scale-traffic-identity-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 138 bytes; described, not walked through |
| 351 | docs/evidence/real-integration-2026-10-09/scale-traffic-identity.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 138 bytes; described, not walked through |
| 352 | docs/evidence/real-integration-2026-10-09/scale-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 35730 bytes; described, not walked through |
| 353 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/action.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 245 bytes; described, not walked through |
| 354 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/anomaly.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 357 bytes; described, not walked through |
| 355 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/audit-in-progress.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 5260 bytes; described, not walked through |
| 356 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/audit.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 12974 bytes; described, not walked through |
| 357 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/before-snapshot.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 312 bytes; described, not walked through |
| 358 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/decision.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 248 bytes; described, not walked through |
| 359 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/deployment-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8799 bytes; described, not walked through |
| 360 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/deployments-after-action.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 32811 bytes; described, not walked through |
| 361 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/evidence.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 11392 bytes; described, not walked through |
| 362 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 214 bytes; described, not walked through |
| 363 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/launcher-output.txt | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1428 bytes; described, not walked through |
| 364 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/m4-health.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 262 bytes; described, not walked through |
| 365 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/observed-anomaly.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 357 bytes; described, not walked through |
| 366 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/payment-pods-after-action.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 134858 bytes; described, not walked through |
| 367 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/payment-pods-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 13597 bytes; described, not walked through |
| 368 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/proposal.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 299 bytes; described, not walked through |
| 369 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/recovery-incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 253 bytes; described, not walked through |
| 370 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/recovery.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 2444 bytes; described, not walked through |
| 371 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/spike-pod-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8385 bytes; described, not walked through |
| 372 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-AUTO-20261009/spike-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 63125 bytes; described, not walked through |
| 373 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/action.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 250 bytes; described, not walked through |
| 374 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/anomaly.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 350 bytes; described, not walked through |
| 375 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/audit.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1425 bytes; described, not walked through |
| 376 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/decision.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 248 bytes; described, not walked through |
| 377 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/evidence.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 11449 bytes; described, not walked through |
| 378 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 218 bytes; described, not walked through |
| 379 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/proposal.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 303 bytes; described, not walked through |
| 380 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/recovery-incident.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 260 bytes; described, not walked through |
| 381 | docs/evidence/real-scale-recovery-2026-10-09/INC-K8S-SCALE-RECOVERY-20261009/recovery.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 2443 bytes; described, not walked through |
| 382 | docs/evidence/real-scale-recovery-2026-10-09/README.md | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 6128 bytes; described, not walked through |
| 383 | docs/evidence/real-scale-recovery-2026-10-09/baseline-traffic-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8906 bytes; described, not walked through |
| 384 | docs/evidence/real-scale-recovery-2026-10-09/baseline-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 20680 bytes; described, not walked through |
| 385 | docs/evidence/real-scale-recovery-2026-10-09/baseline.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1144 bytes; described, not walked through |
| 386 | docs/evidence/real-scale-recovery-2026-10-09/before-snapshot.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 301 bytes; described, not walked through |
| 387 | docs/evidence/real-scale-recovery-2026-10-09/deployment-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 6940 bytes; described, not walked through |
| 388 | docs/evidence/real-scale-recovery-2026-10-09/deployment-baseline.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8799 bytes; described, not walked through |
| 389 | docs/evidence/real-scale-recovery-2026-10-09/deployment-before-automated-run.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8799 bytes; described, not walked through |
| 390 | docs/evidence/real-scale-recovery-2026-10-09/deployment-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8799 bytes; described, not walked through |
| 391 | docs/evidence/real-scale-recovery-2026-10-09/dropped-log-records.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 200 bytes; described, not walked through |
| 392 | docs/evidence/real-scale-recovery-2026-10-09/environment-before-fixes.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 23313 bytes; described, not walked through |
| 393 | docs/evidence/real-scale-recovery-2026-10-09/integration-auto-spike-traffic-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8386 bytes; described, not walked through |
| 394 | docs/evidence/real-scale-recovery-2026-10-09/integration-recovery-baseline-traffic-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8400 bytes; described, not walked through |
| 395 | docs/evidence/real-scale-recovery-2026-10-09/integration-recovery-spike-traffic-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 8394 bytes; described, not walked through |
| 396 | docs/evidence/real-scale-recovery-2026-10-09/observed-anomaly.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 350 bytes; described, not walked through |
| 397 | docs/evidence/real-scale-recovery-2026-10-09/observer-client-creation-benchmark.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 253 bytes; described, not walked through |
| 398 | docs/evidence/real-scale-recovery-2026-10-09/payment-health-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 4123 bytes; described, not walked through |
| 399 | docs/evidence/real-scale-recovery-2026-10-09/payment-pods-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 93962 bytes; described, not walked through |
| 400 | docs/evidence/real-scale-recovery-2026-10-09/payment-pods-baseline.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 13597 bytes; described, not walked through |
| 401 | docs/evidence/real-scale-recovery-2026-10-09/per-pod-request-rate.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 1942 bytes; described, not walked through |
| 402 | docs/evidence/real-scale-recovery-2026-10-09/preview-before.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 11566 bytes; described, not walked through |
| 403 | docs/evidence/real-scale-recovery-2026-10-09/snapshot-history-consistency.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 686 bytes; described, not walked through |
| 404 | docs/evidence/real-scale-recovery-2026-10-09/spike-traffic-after.json | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 6673 bytes; described, not walked through |
| 405 | docs/evidence/real-scale-recovery-2026-10-09/spike-traffic.jsonl | Evidence | ⏭️ | ⏭️ | 06_reference/ (summary) | recorded run output, 67299 bytes; described, not walked through |

Status: ⬜ not started · 🟨 partial · ✅ done · ⏭️ skipped (reason required)


**Tier counts (proposed):** Tier 1: 48, Tier 2: 108, Tier 3: 143, ⏭️ data files outside evidence: 4, ⏭️ evidence data: 102.

