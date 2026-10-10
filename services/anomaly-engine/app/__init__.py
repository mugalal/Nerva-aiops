"""M2 anomaly-engine service.

`shared` lives at the repo root, so put the root on sys.path before any
module in this package imports from it. Doing it here (not in main.py) means
`uvicorn app.main:app`, the tests, and any script all resolve `shared` the
same way, whatever directory they are started from.
"""

import sys
from pathlib import Path

# app/__init__.py -> app -> anomaly-engine -> services -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
