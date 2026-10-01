#!/usr/bin/env bash
# Day-1 laptop check (macOS/Linux/Git-Bash). Run: bash scripts/check-env.sh
fail=0
check() { # $1 name, $2 version-cmd, $3 hint
  if out=$(eval "$2" 2>&1); then echo "[OK] $1 : $(echo "$out" | head -n1)";
  else echo "[MISSING] $1 -- $3"; fail=1; fi
}
check "python 3.11+" "python3 --version" "Install Python 3.11"
check "git" "git --version" "Install git"
check "docker" "docker --version" "Install Docker Desktop"
check "kubectl" "kubectl version --client --short=true" "Demo machine (M4) only"
check "kind" "kind --version" "Demo machine (M4) only (or minikube)"
if [ "$fail" -ne 0 ]; then echo "RESULT: install the [MISSING] tools today (Day 1), not Day 4."; exit 1; fi
echo "RESULT: all required tools present."
