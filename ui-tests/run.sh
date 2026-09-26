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
# Pinned (not :stable) so the render is deterministic — a floating tag made the truncation
# gate flake as HA/card versions moved under it. Renovate proposes this and the card pins below as
# one grouped PR (.github/renovate.json); keep the image:tag and URL shapes its regexes expect.
HA_IMAGE="${HA_IMAGE:-ghcr.io/home-assistant/home-assistant:2026.9.3}"
CONFIG="$(mktemp -d)"

cleanup() {
  docker rm -f ha-ui >/dev/null 2>&1 || true
  # HA writes .storage as root, so a plain rm fails on Linux CI — fall back to sudo.
  rm -rf "$CONFIG" 2>/dev/null || sudo rm -rf "$CONFIG" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Vendor custom cards into the HA www/ (same-origin, no CORS issues)"
mkdir -p "$CONFIG/www/cards"
# Pinned to fixed releases (not @latest / @master) so the rendered layout is reproducible; a
# floating card version shifting the shadow DOM is what tripped the marginal truncation gate.
# Renovate bumps these together with HA_IMAGE (one grouped PR).
curl -fsSL "https://github.com/piitaya/lovelace-mushroom/releases/download/v5.2.3/mushroom.js" -o "$CONFIG/www/cards/mushroom.js"
curl -fsSL "https://github.com/custom-cards/button-card/releases/download/v7.0.1/button-card.js" -o "$CONFIG/www/cards/button-card.js"
curl -fsSL "https://cdn.jsdelivr.net/gh/thomasloven/lovelace-card-mod@v4.2.1/card-mod.js" -o "$CONFIG/www/cards/card-mod.js"
# Bubble Card is a single self-contained bundle at pinned releases — vendor it too (was loaded
# live from jsDelivr @main, whose runtime fetch intermittently error-carded on the widest viewport).
curl -fsSL "https://cdn.jsdelivr.net/gh/Clooos/Bubble-Card@v3.4.1/dist/bubble-card.js" -o "$CONFIG/www/cards/bubble-card.js"

# Vendor the dashboards' background/render images so /local/backgrounds/<file> resolves
# (the live add-on rewrites these to the CDN via deploy._cdnify; the harness serves them
# locally instead, so the car render + the SOC gauge's picture-elements background appear).
echo "==> Vendor dashboard background images into the HA www/"
mkdir -p "$CONFIG/www/backgrounds"
find "$HERE/../alpine_a290/dashboards/Images" -type f \( -name '*.webp' -o -name '*.png' \) \
  -exec cp {} "$CONFIG/www/backgrounds/" \;
# card-mod loads as a frontend module, not a Lovelace resource. It styles a card only via prototype
# patches, so a card built before card-mod.js runs stays unstyled for that load; as a resource it
# lost that race on CI (mass false truncation). A module starts with the page instead of after the
# dashboard config: a head start, not a guarantee, so check_overflow.py still fails by name if
# card-mod never applies. (card-mod README: "Performance improvements".)
printf 'default_config:\nfrontend:\n  extra_module_url:\n    - /local/cards/card-mod.js\n' > "$CONFIG/configuration.yaml"

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
"$PY" "$HERE/seed.py" --base "$BASE" --token "$ACCESS" --manifest "$CONFIG/pass.json"

echo "==> Render + truncation check across the device matrix"
rc=0
"$PY" "$HERE/check_overflow.py" --base "$BASE" --tokens "$CONFIG/tokens.json" --expect "$CONFIG/pass.json" || rc=1

# The seed is a parked car on a working add-on with the charger dispatching, so the text the
# problem sensors and the demo toggles switch on (Not Polling, Auth Failure, Last Updated, Peak
# rate) never renders above. seed.py derives the passes that render it, each a state production can
# publish, and each re-checks the dashboards, and opens the pop-ups, it changes. They run even after
# a failure above, so one run reports every pass.
PASSES="$("$PY" "$HERE/seed.py" --list-passes)"
for pass in $PASSES; do
  echo "==> $pass pass: reseed the sensors it switches, re-check the dashboards that show them"
  "$PY" "$HERE/seed.py" --base "$BASE" --token "$ACCESS" --pass "$pass" --manifest "$CONFIG/pass-$pass.json"
  "$PY" "$HERE/check_overflow.py" --base "$BASE" --tokens "$CONFIG/tokens.json" \
    --pass-name "$pass" --expect "$CONFIG/pass-$pass.json" || rc=1
done
[ "$rc" -eq 0 ] || exit 1
