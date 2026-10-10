"""M1 observability and telemetry tests."""

import sys
from pathlib import Path

# Make the documented repository-root unittest command resolve the M1 app.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "telemetry-intelligence"))
