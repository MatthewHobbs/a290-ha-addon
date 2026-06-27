#!/usr/bin/env bash
# Render the bundled A290 dashboards in a real Home Assistant across the mobile device
# matrix (ui-tests/devices.json) and fail on text truncation or broken cards. Used by CI
# and runnable locally. Needs: docker, curl, and a python with aiohttp + PyYAML + playwright
# (override the interpreter with PYTHON=/path/to/python; it must have chromium installed via
# `playwright install chromium`).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="http://localhost:8123"
CID="$BASE/"
PY="${PYTHON:-python3}"
HA_IMAGE="${HA_IMAGE:-ghcr.io/home-assistant/home-assistant:stable}"
CONFIG="$(mktemp -d)"

cleanup() { docker rm -f ha-ui >/dev/null 2>&1 || true; rm -rf "$CONFIG"; }
trap cleanup EXIT

echo "==> Vendor custom cards into the HA www/ (same-origin, no CORS issues)"
mkdir -p "$CONFIG/www/cards"
curl -fsSL "https://github.com/piitaya/lovelace-mushroom/releases/latest/download/mushroom.js" -o "$CONFIG/www/cards/mushroom.js"
curl -fsSL "https://github.com/custom-cards/button-card/releases/latest/download/button-card.js" -o "$CONFIG/www/cards/button-card.js"
curl -fsSL "https://cdn.jsdelivr.net/gh/thomasloven/lovelace-card-mod@master/card-mod.js" -o "$CONFIG/www/cards/card-mod.js"
printf 'default_config:\n' > "$CONFIG/configuration.yaml"

echo "==> Start Home Assistant ($HA_IMAGE)"
docker run -d --name ha-ui -p 8123:8123 -v "$CONFIG":/config "$HA_IMAGE" >/dev/null
echo -n "    waiting for HA onboarding API"
for _ in $(seq 1 90); do
  curl -sf -o /dev/null "$BASE/api/onboarding" && { echo " up"; break; }
  docker ps --filter name=ha-ui --format '{{.Names}}' | grep -q ha-ui || { echo " HA died:"; docker logs ha-ui 2>&1 | tail -25; exit 1; }
  echo -n "."; sleep 3
done

echo "==> Onboard (create owner) + obtain token"
RESP=$(curl -fsS -X POST "$BASE/api/onboarding/users" -H 'Content-Type: application/json' \
  -d "{\"client_id\":\"$CID\",\"name\":\"UI Test\",\"username\":\"uitest\",\"password\":\"uitestpass123\",\"language\":\"en\"}")
CODE=$(printf '%s' "$RESP" | "$PY" -c "import sys,json;print(json.load(sys.stdin)['auth_code'])")
curl -fsS -X POST "$BASE/auth/token" -d "client_id=$CID&grant_type=authorization_code&code=$CODE" > "$CONFIG/tokens.json"
ACCESS=$("$PY" -c "import json;print(json.load(open('$CONFIG/tokens.json'))['access_token'])")
for step in core_config analytics; do
  curl -fsS -X POST "$BASE/api/onboarding/$step" -H "Authorization: Bearer $ACCESS" -H 'Content-Type: application/json' -d '{}' -o /dev/null
done
curl -fsS -X POST "$BASE/api/onboarding/integration" -H "Authorization: Bearer $ACCESS" -H 'Content-Type: application/json' \
  -d "{\"client_id\":\"$CID\",\"redirect_uri\":\"$CID\"}" -o /dev/null

echo "==> Seed entity states + dashboards"
"$PY" "$HERE/seed.py" --base "$BASE" --token "$ACCESS"

echo "==> Render + truncation check across the device matrix"
"$PY" "$HERE/check_overflow.py" --base "$BASE" --tokens "$CONFIG/tokens.json"
