import json
from pathlib import Path

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_finops_context.json"

def get_finops_context():
    with open(MOCK_FILE, "r", encoding="utf-8") as file:
        return json.load(file)