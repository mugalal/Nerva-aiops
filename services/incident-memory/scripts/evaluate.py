"""Produce a reproducible source/citation evaluation using explicitly mock records."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.copilot import answer, QUESTIONS, INSUFFICIENT
from app.fixtures import demo_records
from app.models import StoreRequest, CopilotRequest
from app.storage import Repository


def evaluate():
    rows = []
    with TemporaryDirectory() as directory:
        repo = Repository("sqlite:///" + str(Path(directory) / "evaluation.db"))
        repo.initialize()
        for payload in demo_records():
            repo.save(StoreRequest.model_validate(payload))
        cases = [(q, "INC-001", "INC-DEMO-HISTORY" if q.startswith("Have") else "INC-001", "ok") for q in QUESTIONS]
        cases += [("What is the root cause?", "INC-DEMO-TRAFFIC", "INC-DEMO-TRAFFIC", "ok"),
                  ("What changed before the incident?", "INC-DEMO-TRAFFIC", None, "insufficient_evidence"),
                  ("Invent financial savings", "INC-001", None, "unsupported_question")]
        for question, incident_id, expected_source, expected_status in cases:
            result = answer(repo, CopilotRequest(question=question, incident_id=incident_id, source="mock"))
            grounded = True
            for citation in result["citations"]:
                record = repo.get(citation["incident_id"], citation["source"])
                current = record
                for part in citation["field"].split("."):
                    current = current[part]
                grounded &= current == citation["value"]
            actual = [c["incident_id"] for c in result["citations"]]
            passed = grounded and result["status"] == expected_status and (expected_source in actual if expected_source else not actual)
            rows.append({"question": question, "expected_source": expected_source, "retrieved_sources": sorted(set(actual)),
                         "status": result["status"], "answer_grounded": grounded, "unsupported_claim": not grounded,
                         "passed": passed, "source": "mock"})
    return rows


if __name__ == "__main__":
    rows = evaluate()
    print(json.dumps(rows, indent=2))
    raise SystemExit(0 if all(row["passed"] for row in rows) else 1)
