import json
from pathlib import Path

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_rca_response.json"

def get_rca_result():
    with open(MOCK_FILE, "r", encoding="utf-8") as file:
        return json.load(file)