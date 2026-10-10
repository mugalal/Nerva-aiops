import sys
from pathlib import Path

# Ensure repo root is available in sys.path if running locally
_repo_root = Path(__file__).resolve().parents[3]
if _repo_root.exists() and (_repo_root / "shared").is_dir() and str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
