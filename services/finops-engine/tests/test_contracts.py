import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import FinOpsContext

# tests/ -> finops-engine -> services -> repo root
ROOT = Path(__file__).resolve().parents[3]


def load(path):
    return json.loads((ROOT / path).read_text())


def test_mock_finops_context_fits_our_model():
    ctx = FinOpsContext(**load("mocks/mock_finops_context.json"))
    assert ctx.service == "payment-service"
    assert len(ctx.temporary_scale_options) == 2


def test_extra_field_is_rejected():
    data = load("mocks/mock_finops_context.json")
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        FinOpsContext(**data)