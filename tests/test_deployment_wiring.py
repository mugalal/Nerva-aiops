"""Every deployment path carries the configuration the code requires.

The b1c19ac audit found the ingest token, async dispatch and the service
registry wired into code but into no Dockerfile, Compose file or manifest.
These checks keep that from regressing.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def manifest_env(path, container):
    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not document or document.get("kind") not in {"Deployment", "StatefulSet"}:
            continue
        for item in document["spec"]["template"]["spec"]["containers"]:
            if item["name"] == container:
                return {entry["name"]: entry for entry in item.get("env", [])}, item
    raise AssertionError(f"{container} not found in {path}")


def compose_env(service):
    compose = yaml.safe_load((ROOT / "observability/docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"][service]["environment"]


@pytest.mark.parametrize("service", ["shared-nexus-api", "anomaly-engine"])
def test_ingest_token_reaches_m2_and_m4_everywhere(service):
    env, _ = manifest_env(ROOT / f"services/{service}/k8s/{service}.yaml", service)
    assert env["NEXUS_INGEST_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "nexus-runtime", "key": "NEXUS_INGEST_TOKEN"}
    assert compose_env(service)["NEXUS_INGEST_TOKEN"].startswith("${NEXUS_INGEST_TOKEN:?")


def test_m2_async_dispatch_is_configured_in_every_deployment():
    env, _ = manifest_env(ROOT / "services/anomaly-engine/k8s/anomaly-engine.yaml", "anomaly-engine")
    assert env["M2_ASYNC_DISPATCH"]["value"] == "true"
    assert compose_env("anomaly-engine")["M2_ASYNC_DISPATCH"] == "true"


@pytest.mark.parametrize("service,manifest", [("shared-nexus-api", "shared-nexus-api.yaml"),
                                              ("finops-engine", "deployment.yaml")])
def test_registry_is_packaged_and_explicitly_configured(service, manifest):
    dockerfile = (ROOT / f"services/{service}/Dockerfile").read_text(encoding="utf-8")
    assert "COPY config" in dockerfile and "NEXUS_SERVICES_CONFIG=/app/config/services.yaml" in dockerfile
    env, _ = manifest_env(ROOT / f"services/{service}/k8s/{manifest}", service)
    assert env["NEXUS_SERVICES_CONFIG"]["value"] == "/app/config/services.yaml"
    assert compose_env(service)["NEXUS_SERVICES_CONFIG"] == "/app/config/services.yaml"
    assert "pyyaml" in (ROOT / f"services/{service}/requirements.txt").read_text(encoding="utf-8").lower()


def test_operator_boundary_is_configured_for_the_dashboard():
    env, _ = manifest_env(ROOT / "services/incident-memory/k8s/incident-memory.yaml", "incident-memory")
    assert env["M5_UI_OPERATOR_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "M5_UI_OPERATOR_TOKEN"
    assert compose_env("incident-memory")["M5_UI_OPERATOR_TOKEN"].startswith("${M5_UI_OPERATOR_TOKEN:?")
    for path in ("services/incident-memory/k8s/incident-memory.yaml", "observability/docker-compose.yml"):
        assert "M5_UI_TRUSTED_NETWORK: \"true\"" not in (ROOT / path).read_text(encoding="utf-8")


def test_diagnosis_waits_for_a_developed_fault_reading():
    env, _ = manifest_env(ROOT / "services/shared-nexus-api/k8s/shared-nexus-api.yaml", "shared-nexus-api")
    assert float(env["M4_AUTO_BUILD_DELAY_SECONDS"]["value"]) >= 60
    assert float(compose_env("shared-nexus-api")["M4_AUTO_BUILD_DELAY_SECONDS"]) >= 60


def test_m4_liveness_has_no_dependency_io():
    _, container = manifest_env(ROOT / "services/shared-nexus-api/k8s/shared-nexus-api.yaml", "shared-nexus-api")
    assert container["livenessProbe"]["httpGet"]["path"] == "/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"


def test_no_secret_values_are_committed_in_manifests():
    for path in ROOT.glob("services/*/k8s/*.yaml"):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if document and document.get("kind") in {"Deployment", "StatefulSet"}:
                for container in document["spec"]["template"]["spec"]["containers"]:
                    for entry in container.get("env", []):
                        if any(word in entry["name"] for word in ("TOKEN", "PASSWORD", "DATABASE_URL", "API_KEY")):
                            assert "value" not in entry, f"{path}: {entry['name']} must come from a Secret"


def test_every_reader_of_m4_has_a_credential_when_reads_are_authenticated():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    m4 = (root / "services/shared-nexus-api/k8s/shared-nexus-api.yaml").read_text(encoding="utf-8")
    assert 'name: NEXUS_REQUIRE_READ_AUTH\n              value: "true"' in m4
    assert "key: NEXUS_READ_TOKEN" in m4 and "key: NEXUS_AUDIT_HMAC_KEY" in m4
    # M3 reads incidents and anomalies with its own read-only token; M5 with its service token.
    assert "key: NEXUS_READ_TOKEN" in (root / "services/root-cause-analysis/k8s/root-cause-analysis.yaml").read_text(encoding="utf-8")
    assert "M5_SHARED_API_TOKEN" in (root / "services/incident-memory/k8s/incident-memory.yaml").read_text(encoding="utf-8")
    # The demo tooling reads M4 from inside the M4 pod with that pod's token.
    assert "NEXUS_READ_TOKEN" in (root / "scripts/demo/NervaDemo.psm1").read_text(encoding="utf-8")
    assert "read_headers()" in (root / "scripts/live-drill.py").read_text(encoding="utf-8")
