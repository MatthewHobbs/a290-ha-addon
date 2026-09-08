#!/usr/bin/env bash
# Repo override for `audit.sh scan`.
#
# Why this exists: the default driver looks for requirements.txt at the repo root and,
# not finding it, audits the AMBIENT python environment instead — which says nothing
# about this repo. This repo's hash-locked pins are at alpine_a290/app/requirements.txt.
# CI's security job (.github/workflows/ci.yaml) runs bandit + pip-audit + trivy; mirror
# that here so a local audit and CI agree.
set -uo pipefail
REQ="alpine_a290/app/requirements.txt"

run() {  # run <name> <command...>
  local name="$1"; shift
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "SCAN ${name}: SKIP (not installed)"
    return
  fi
  "$@"; echo "SCAN ${name}: RAN (exit $?)"
}

run gitleaks   gitleaks detect --no-banner --redact
run bandit     bandit -r alpine_a290/app -ll
run pip-audit  pip-audit -r "$REQ"
run semgrep    semgrep --config=auto --error alpine_a290/app scripts .github/scripts
run trivy      trivy fs --scanners vuln,misconfig,secret \
                 --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed .
