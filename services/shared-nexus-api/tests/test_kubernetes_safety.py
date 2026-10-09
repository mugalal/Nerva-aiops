"""Kubernetes command doubles exercise preconditions without any live writes."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace

import pytest

from app.decision_engine.models import DecisionAction
from app.remediation import executor


def deployment(version="v2", replicas=1):
    return {"metadata": {"uid": "payment-uid", "resourceVersion": "10", "generation": 1,
                         "annotations": {"deployment.kubernetes.io/revision": "3"}},
            "spec": {"replicas": replicas,
                     "template": {"metadata": {"labels": {"version": version}},
                                  "spec": {"containers": [{"name": "payment", "image": "fixture:" + version,
                                                            "resources": {"requests": {"cpu": "100m"}}}]}}},
            "status": {"observedGeneration": 1, "replicas": replicas, "updatedReplicas": replicas,
                       "readyReplicas": replicas, "availableReplicas": replicas}}


def replica_set(version, revision, owner="payment-uid"):
    return {"metadata": {"annotations": {"deployment.kubernetes.io/revision": str(revision)},
                         "ownerReferences": [{"uid": owner, "kind": "Deployment", "controller": True}]},
            "spec": {"template": {"metadata": {"labels": {"version": version, "pod-template-hash": "fixture-hash"}},
                                   "spec": {"containers": [{"name": "payment", "image": "fixture:" + version}]}}}}


def completed(stdout="completed", returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="conflict" if returncode else "")


def bound_expected(captured=None):
    captured = captured or deployment()
    rollback = replica_set("v1", 1)["spec"]["template"]
    rollback["metadata"]["labels"].pop("pod-template-hash")
    return {"version": captured["spec"]["template"]["metadata"]["labels"]["version"],
            "replicas": captured["spec"]["replicas"], "uid": captured["metadata"]["uid"],
            "template_fingerprint": executor._template_fingerprint(captured["spec"]["template"]),
            "rollback_revision": 1, "rollback_template_fingerprint": executor._template_fingerprint(rollback)}


@pytest.fixture
def kube_commands(monkeypatch):
    monkeypatch.setattr(executor, "REMEDIATION_BACKEND", "kubernetes")
    monkeypatch.setattr(executor, "KUBECTL_PATH", "fixture-kubectl")
    monkeypatch.setattr(executor, "KUBECTL_CONTEXT", "")
    state = SimpleNamespace(deployment=deployment(), replica_sets={"items": [replica_set("v1", 1), replica_set("v0", 2)]},
                            calls=[], fail_mutation=False)

    def run(command, **kwargs):
        state.calls.append(command)
        if command[1:3] == ["get", "deployment/payment-service"]:
            return completed(json.dumps(state.deployment))
        if command[1:3] == ["get", "replicasets"]:
            return completed(json.dumps(state.replica_sets))
        if command[1] in {"scale", "patch"} and state.fail_mutation:
            return completed(returncode=1)
        return completed()

    monkeypatch.setattr(executor.subprocess, "run", run)
    return state


@pytest.mark.parametrize("version,replicas", [("v3", 1), ("v2", 3)])
def test_stale_scaling_proposal_performs_no_mutation(kube_commands, version, replicas):
    kube_commands.deployment = deployment(version, replicas)
    with pytest.raises(ValueError, match="changed since"):
        executor.execute_remediation(DecisionAction.SCALE, True, 2,
                                     expected_state=bound_expected())
    assert len(kube_commands.calls) == 1 and kube_commands.calls[0][1] == "get"


@pytest.mark.parametrize("change", ["uid", "image", "cpu_request"])
def test_same_version_and_replicas_do_not_hide_identity_or_template_drift(kube_commands, change):
    expected = bound_expected()
    if change == "uid":
        kube_commands.deployment["metadata"]["uid"] = "replacement-uid"
    elif change == "image":
        kube_commands.deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "other-image:v2"
    else:
        kube_commands.deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = "50m"
    with pytest.raises(ValueError, match="identity or template changed"):
        executor.execute_remediation(DecisionAction.SCALE, True, 3, expected_state=expected)
    assert len(kube_commands.calls) == 1 and kube_commands.calls[0][1] == "get"


def test_binding_pins_actual_identity_template_and_finops_cpu_request(kube_commands):
    bound = executor.bind_live_preconditions("payment-service", {"version": "v2", "replicas": 1}, cpu_request_m=100)
    assert bound["uid"] == "payment-uid" and bound["template_fingerprint"] and bound["cpu_request_m"] == 100
    kube_commands.deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = "0.05"
    with pytest.raises(ValueError, match="CPU request changed"):
        executor.bind_live_preconditions("payment-service", {"version": "v2", "replicas": 1}, cpu_request_m=100)
    assert all(command[1] == "get" for command in kube_commands.calls)


def test_binding_pins_rollback_revision_and_rejects_changed_history(kube_commands):
    expected = executor.bind_live_preconditions("payment-service", {"version": "v2", "replicas": 1}, rollback_target="v1")
    assert expected["rollback_revision"] == 1 and expected["rollback_template_fingerprint"]
    kube_commands.calls.clear()
    kube_commands.replica_sets["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "another-image:v1"
    with pytest.raises(ValueError, match="rollback revision changed"):
        executor.execute_remediation(DecisionAction.ROLLBACK, True, expected_state=expected, rollback_target="v1")
    assert all(command[1] == "get" for command in kube_commands.calls)


def test_scaling_is_conditional_and_never_downscales(kube_commands):
    executor.execute_remediation(DecisionAction.SCALE, True, 3, expected_state=bound_expected())
    command = kube_commands.calls[1]
    assert "--current-replicas=1" in command and "--resource-version=10" in command
    kube_commands.calls.clear()
    kube_commands.deployment = deployment(replicas=3)
    with pytest.raises(ValueError, match="must increase"):
        executor.execute_remediation(DecisionAction.SCALE, True, 3, expected_state=bound_expected(deployment(replicas=3)))
    assert all(command[1] == "get" for command in kube_commands.calls)


def test_missing_preconditions_and_incomplete_rollout_are_rejected(kube_commands):
    with pytest.raises(ValueError, match="requires captured"):
        executor.execute_remediation(DecisionAction.SCALE, True, 2)
    assert kube_commands.calls == []
    kube_commands.deployment["status"]["observedGeneration"] = 0
    with pytest.raises(ValueError, match="incomplete"):
        executor.execute_remediation(DecisionAction.SCALE, True, 2, expected_state=bound_expected())
    assert len(kube_commands.calls) == 1


def test_rollback_restores_matching_revision_with_atomic_resource_version_test(kube_commands):
    result = executor.execute_remediation(DecisionAction.ROLLBACK, True,
                                         expected_state=bound_expected(), rollback_target="v1")
    assert result.success and "revision 1" in result.message
    command = kube_commands.calls[2]
    assert command[1] == "patch" and "--type=json" in command
    patch = json.loads(command[command.index("--patch") + 1])
    assert patch[0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "10"}
    assert patch[1]["value"]["spec"]["containers"][0]["image"] == "fixture:v1"
    assert "pod-template-hash" not in patch[1]["value"]["metadata"]["labels"]
    assert kube_commands.calls[-1][1:3] == ["rollout", "status"]


def test_rollback_rejects_stale_source_and_foreign_revision(kube_commands):
    kube_commands.deployment = deployment("v3")
    with pytest.raises(ValueError, match="changed since"):
        executor.execute_remediation(DecisionAction.ROLLBACK, True,
                                     expected_state=bound_expected(), rollback_target="v1")
    kube_commands.calls.clear()
    kube_commands.deployment = deployment()
    kube_commands.replica_sets = {"items": [replica_set("v1", 1, owner="other-deployment")]}
    with pytest.raises(ValueError, match="No owned"):
        executor.execute_remediation(DecisionAction.ROLLBACK, True,
                                     expected_state=bound_expected(), rollback_target="v1")
    assert all(command[1] == "get" for command in kube_commands.calls)


@pytest.mark.parametrize("action,target", [(DecisionAction.SCALE, None), (DecisionAction.ROLLBACK, "v1")])
def test_mutation_conflict_is_never_reported_completed(kube_commands, action, target):
    kube_commands.fail_mutation = True
    with pytest.raises(ValueError, match="conflict"):
        executor.execute_remediation(action, True, replicas=3 if action == DecisionAction.SCALE else None,
                                     expected_state=bound_expected(), rollback_target=target)
    assert not any(command[1:3] == ["rollout", "status"] for command in kube_commands.calls)


def test_same_service_actions_serialize_and_second_stale_proposal_is_rejected(kube_commands, monkeypatch):
    first_rollout = Event()
    release_rollout = Event()
    second_live_read = Event()
    calls = []
    live = deployment()
    reads = 0

    def run(command, **kwargs):
        nonlocal reads
        calls.append(command)
        if command[1] == "get":
            reads += 1
            if reads == 2:
                second_live_read.set()
            return completed(json.dumps(live))
        if command[1] == "scale":
            assert "--current-replicas=1" in command
            live.update(deployment(replicas=3))
            return completed()
        first_rollout.set()
        assert release_rollout.wait(timeout=2)
        return completed()

    monkeypatch.setattr(executor.subprocess, "run", run)
    expected = bound_expected()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(executor.execute_remediation, DecisionAction.SCALE, True, 3, expected_state=expected)
        assert first_rollout.wait(timeout=2)
        second = pool.submit(executor.execute_remediation, DecisionAction.SCALE, True, 2, expected_state=expected)
        try:
            assert not second_live_read.wait(timeout=0.15)
        finally:
            release_rollout.set()
        assert first.result(timeout=2).success
        with pytest.raises(ValueError, match="changed since"):
            second.result(timeout=2)
    assert sum(command[1] == "scale" for command in calls) == 1
    assert live["spec"]["replicas"] == 3
