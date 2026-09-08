#!/usr/bin/env bash
# Repo override for `audit.sh coverage`.
#
# The default driver finds no coverage tool for this repo because it looks for a
# root-level requirements.txt; this add-on's pins live in alpine_a290/app/. CI's real
# gate is the pytest run below (--cov-fail-under=95, see .github/workflows/ci.yaml).
#
# The app imports the shared core `renault-mqtt`, which is installed from the git SHA
# pinned as ARG CORE_REF in alpine_a290/Dockerfile. If that import is unavailable the
# suite cannot collect, so emit unknown rather than a misleading 0.
set -uo pipefail

if ! python3 -c "import renault_mqtt, aiohttp, paho.mqtt.client" 2>/dev/null; then
  echo "coverage: dependencies unavailable (need renault-mqtt + aiohttp + paho-mqtt);" \
       "install per .github/workflows/ci.yaml 'Install deps'" >&2
  echo "COVERAGE=unknown"
  exit 0
fi

pct="$(python3 -m pytest alpine_a290/tests -q \
        --cov=alpine_a290/app --cov-report=term-missing 2>/dev/null \
      | sed -n 's/^TOTAL.* \([0-9]\{1,3\}\)%$/\1/p' | tail -1)"

echo "COVERAGE=${pct:-unknown}"
