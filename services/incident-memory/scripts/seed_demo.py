"""Explicit opt-in mock seed. Never imports mock incidents into real storage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.fixtures import demo_records
from app.models import StoreRequest
from app.storage import Repository

if __name__ == "__main__":
    repo = Repository()
    repo.initialize()
    for payload in demo_records():
        record, status = repo.save(StoreRequest.model_validate(payload))
        print(record["memory"]["incident_id"], status, "[mock]")
