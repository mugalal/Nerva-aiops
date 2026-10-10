"""
Loads M2's package for the tests.

M1's service is also a package called `app`. If two test files both did
`import app`, Python would silently reuse whichever loaded first. So M2's
package is loaded from its path under the unique name `m2_app`. The service
code uses relative imports, so it doesn't care what it's called.

Usage in a test file:
    import m2_loader
    baseline = m2_loader.module("baseline")
"""

import importlib
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_DIR = REPO_ROOT / "services" / "anomaly-engine" / "app"


def _load():
    if "m2_app" in sys.modules:
        return sys.modules["m2_app"]
    spec = importlib.util.spec_from_file_location(
        "m2_app",
        APP_DIR / "__init__.py",
        submodule_search_locations=[str(APP_DIR)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["m2_app"] = package
    spec.loader.exec_module(package)
    return package


_load()


def module(name: str):
    """Import a submodule of M2's package, e.g. module("evaluation.scenarios")."""
    return importlib.import_module(f"m2_app.{name}")
