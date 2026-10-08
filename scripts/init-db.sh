#!/usr/bin/env bash
set -euo pipefail
# Needs DATABASE_URL set and the project venv active.
cd "$(dirname "$0")/../services/shared-nexus-api/app"
python -m db.init_db