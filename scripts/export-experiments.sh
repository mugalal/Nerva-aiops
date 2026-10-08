#!/usr/bin/env bash
set -euo pipefail
# Usage: scripts/export-experiments.sh [output-dir]   (default: ./exports at repo root)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/services/shared-nexus-api/app"
python -m db.export_experiments --out-dir "${1:-$ROOT/exports}"