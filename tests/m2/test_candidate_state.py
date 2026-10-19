"""
M2's incident candidates and handoff progress survive a restart.

Before, a restart forgot an incident whose creation or anomaly links had not
reached the shared API yet. Now they are saved after every change and a new
process finishes the handoff.

Run from the repo root:   python -m pytest tests/m2/test_candidate_state.py -v
"""

import json

import m2_loader
import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from test_handoff import ago, bad, feed, posts, reference, shared, shared_server  # noqa: E402,F401

pipeline_mod = m2_loader.module("pipeline")
handoff_mod = m2_loader.module("handoff")
correlation_mod = m2_loader.module("correlation")
state_mod = m2_loader.module("state")


def make(reference, store, url, correlator=None):
    return pipeline_mod.Pipeline(reference, handoff=handoff_mod.SharedApiHandoff(url, timeout=2),
                                 handoff_retry_s=0, state=store, correlator=correlator)


def test_candidates_round_trip_exactly(reference, shared, tmp_path):
    store = state_mod.CandidateStore(tmp_path / "candidates.json")
    pipe = make(reference, store, shared.url)
    feed(pipe, 5)
    restored = correlation_mod.Correlator()
    assert store.load(restored) == 1
    before, after = pipe.correlator.candidates(None, 10)[0], restored.candidates(None, 10)[0]
    assert state_mod.candidate_to_dict(after) == state_mod.candidate_to_dict(before)
    assert after.handoff.incident_created and after.handoff.in_flight is False


def test_a_restart_finishes_an_undelivered_handoff(reference, shared, tmp_path):
    store = state_mod.CandidateStore(tmp_path / "candidates.json")
    down = make(reference, store, "http://127.0.0.1:1")      # the shared API is unreachable
    feed(down, 3)
    assert posts(shared, "/api/incidents/") == []
    saved = json.loads((tmp_path / "candidates.json").read_text(encoding="utf-8"))
    assert saved["candidates"][0]["handoff"]["incident_created"] is False

    # A new process: restore, then the poller's retry sweep delivers.
    correlator = correlation_mod.Correlator()
    store.load(correlator)
    restarted = make(reference, store, shared.url, correlator=correlator)
    assert restarted.retry_pending() == 1
    [incident] = list(shared.state.incidents)
    assert incident["incident_id"] == correlator.candidates(None, 1)[0].incident_id
    assert len(posts(shared, "/internal/anomalies")) >= 1


def test_a_restored_open_candidate_keeps_correlating(reference, shared, tmp_path):
    store = state_mod.CandidateStore(tmp_path / "candidates.json")
    feed(make(reference, store, shared.url), 3, start_ago=200)
    correlator = correlation_mod.Correlator()
    store.load(correlator)
    restarted = make(reference, store, shared.url, correlator=correlator)
    restarted.process(bad(ago(140), seed=9))                   # within the correlation gap
    assert len(restarted.incidents()) == 1


def test_a_corrupt_state_file_is_set_aside_not_fatal(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text("{not json", encoding="utf-8")
    store = state_mod.CandidateStore(path)
    assert store.load(correlation_mod.Correlator()) == 0
    assert not path.exists() and (tmp_path / "candidates.json.corrupt").exists()
    assert state_mod.CandidateStore(None).load(correlation_mod.Correlator()) == 0
