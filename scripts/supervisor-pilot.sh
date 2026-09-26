#!/usr/bin/env bash
# Install, configure and probe the add-on under a real stable-channel
# Supervisor and Core, inside the official add-on devcontainer (ADR 0002).
# Adapted from polygonal-zones' pilot (its ADR 0001), which solved the image,
# floor-Core and AppArmor problems this relies on.
#
# One script for CI (.github/workflows/supervisor.yml) and for a local run:
#
#   scripts/supervisor-pilot.sh all              # every phase, then tear down
#   CORE_VERSION=minimum scripts/supervisor-pilot.sh all
#
# Phases can also be run one at a time against a running devcontainer
# (build, up, versions, sideload, egress, broker, install, provenance, probe,
# diagnostics, down). Needs docker with privileged containers, git and jq.
#
# Environment:
#   CORE_VERSION   "stable" (default), "minimum" (config.yaml's homeassistant:)
#                  or an exact Core version to pin.
#   PILOT_NAME     devcontainer name, and prefix of its volumes (a290-supervisor).
#   PILOT_WORKDIR  scratch for the image tar and state (a mktemp dir). Set it
#                  when running phases one at a time, so they share state.
#   PILOT_KEEP=1   `all` leaves the devcontainer running for inspection.
#   PILOT_BREAK    deliberately break the run, to show the probes can fail:
#                  "start"    installs with an empty username, so the add-on
#                             exits at start ("Missing required setting");
#                  "apparmor" drops apparmor.txt from the add-on copy, so the
#                             Supervisor confines it with its default profile;
#                  "dashboard" points dashboard_url_path at a reserved Core
#                             path, which deploy.py refuses, so no dashboard
#                             is deployed and the add-on logs the skip.
#   PILOT_APPARMOR_CHECK=skip  local runs only (Docker Desktop's kernel has no
#                  AppArmor); refused under GitHub Actions.
#   PILOT_EXPECT_SUPERVISOR / PILOT_EXPECT_CORE / PILOT_EXPECT_NODE /
#   PILOT_EXPECT_DASHBOARD  override what is expected; only for showing a
#                  check can fail.

set -euo pipefail

# Renovate keeps these current (customManagers in .github/renovate.json),
# matching the quoted "name:tag@digest"; the tag must exist upstream.
DEVCONTAINER_IMAGE="ghcr.io/home-assistant/devcontainer:6-apps@sha256:4e2d6efd9ac472c27f5cc522672ea9bbfdf35a897266ff4b1ac1f21ee611a4a9"
REGISTRY_IMAGE="docker.io/library/registry:3@sha256:852b3e4d378c426dda6b318fe9d9bfe8e92a0eccb9926671ec3d3ea17a196696"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON_DIR="alpine_a290"
SLUG="local_alpine_a290"
ADDON_CONTAINER="app_${SLUG}"
# The Supervisor renames the profile to the add-on's slug when it loads it.
PROFILE="$SLUG"
BROKER_SLUG="core_mosquitto"
BROKER_CONTAINER="app_${BROKER_SLUG}"
NAME="${PILOT_NAME:-a290-supervisor}"
CORE_VERSION="${CORE_VERSION:-stable}"
BREAK="${PILOT_BREAK:-}"
WORKDIR="${PILOT_WORKDIR:-}"
# Only a directory this run created is removed on exit; a caller's PILOT_WORKDIR is theirs.
# Every command gets the trap, not only `all`: a lone `down` or `versions` made a mktemp dir
# it never used again. `diagnostics` and PILOT_KEEP=1 disown it below to keep the output.
WORKDIR_OWNED=0
if [[ -z "$WORKDIR" ]]; then
  WORKDIR="$(mktemp -d)"
  WORKDIR_OWNED=1
fi
mkdir -p "$WORKDIR"
cleanup_workdir() {
  ((WORKDIR_OWNED)) && rm -rf "$WORKDIR"
  return 0
}
trap 'cleanup_workdir' EXIT
# The registry lives on the devcontainer's loopback: Docker allows plain HTTP
# to 127.0.0.1 without any daemon configuration, and nothing leaves the job.
LOCAL_REGISTRY="127.0.0.1:5000"
REGISTRY_TAG="a290-pilot-registry:local"
PROVENANCE_LABEL="io.github.matthewhobbs.a290.pilot-build"
# Supervisor const.py: add-ons get addresses from the range, and the mask is
# the whole hassio network (Supervisor, DNS, Core's gateway, the broker).
HASSIO_ADDON_RANGE="172.30.33.0/24"
HASSIO_NETWORK="172.30.32.0/23"
HASSIO_NETWORK6="fd0c:ac1e:2100::/48"
EGRESS_COMMENT="a290-pilot-no-egress"
# Credentials are fake and every route off the hassio network is refused
# (egress phase), so Renault is never reached; the same stubs as CLAUDE.md.
STUB_USERNAME="stub@example.invalid"
STUB_PASSWORD="stub-not-a-real-credential"
STUB_ACCOUNT_ID="0000000000"
STUB_VIN="VF1STUBVIN0000000"
# The discovery node the add-on publishes under (catalog.NODE).
EXPECT_NODE="${PILOT_EXPECT_NODE:-alpine_a290}"
BROKER_USER="pilot"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() {
  printf '%s FAIL %s\n' "$(date -u +%H:%M:%S)" "$*" >&2
  exit 1
}

dc() { docker exec "$NAME" "$@"; }
ha_cli() { docker exec "$NAME" ha "$@"; }

# `ha --raw-json` exits 0 even when the result is an error, so only the
# result field can say whether the call worked.
ha_ok() {
  local out
  out="$(ha_cli "$@" --raw-json)" || true
  echo "$out"
  jq -e '.result == "ok"' <<<"$out" >/dev/null
}

# Supervisor REST API from inside hassio_cli, the one container that already
# holds a SUPERVISOR_TOKEN. A body goes in on stdin, never into the command.
supervisor_post() {
  local path="$1" body="$2"
  printf '%s' "$body" | docker exec -i "$NAME" docker exec -i hassio_cli sh -c "
    curl -sS --fail-with-body --max-time 60 -X POST 'http://supervisor$path' \
      -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" \
      -H 'Content-Type: application/json' --data-binary @-"
}

supervisor_get() {
  docker exec "$NAME" docker exec hassio_cli sh -c "
    curl -sS --fail-with-body --max-time 60 'http://supervisor$1' \
      -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\""
}

# poll DESCRIPTION TIMEOUT_S INTERVAL_S COMMAND... (the exit code decides)
poll() {
  local description="$1" timeout_s="$2" interval_s="$3" start
  shift 3
  start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if ((SECONDS - start >= timeout_s)); then
      log "TIMEOUT after ${timeout_s}s: $description" >&2
      return 1
    fi
    sleep "$interval_s"
  done
  log "OK (${description}, $((SECONDS - start))s)"
}

docker_arch() {
  case "$(docker version --format '{{.Server.Arch}}')" in
    amd64 | x86_64) echo amd64 ;;
    arm64 | aarch64) echo aarch64 ;;
    *) fail "unsupported docker server arch" ;;
  esac
}

config_value() {
  sed -n "s/^$1: *\"\{0,1\}\([^\"]*\)\"\{0,1\} *\$/\1/p" "$REPO_ROOT/$ADDON_DIR/config.yaml"
}

# "minimum" is read from the manifest, as ui-tests.yaml does, so the floor
# leg cannot drift from what the add-on claims.
resolved_core_version() {
  local v
  case "$CORE_VERSION" in
    minimum)
      v="$(config_value homeassistant)"
      [[ "$v" =~ ^[0-9]{4}\.[0-9]+\.[0-9]+$ ]] || fail "no usable homeassistant: minimum in config.yaml ('$v')"
      echo "$v"
      ;;
    *) echo "$CORE_VERSION" ;;
  esac
}

# The revision under test: HEAD, marked dirty if the add-on has local edits,
# so an uncommitted change can never pass as the committed one.
checkout_revision() {
  local rev
  rev="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain -- "$ADDON_DIR")" ]]; then
    rev="${rev}-dirty"
  fi
  echo "$rev"
}

# --- build: the image that would ship, built the way release.yaml builds it -
cmd_build() {
  local arch platform version tag nonce provenance
  arch="$(docker_arch)"
  platform="linux/$([[ $arch == aarch64 ]] && echo arm64 || echo amd64)"
  version="$(config_value version)"
  [[ -n "$version" ]] || fail "could not read version: from config.yaml"
  tag="${LOCAL_REGISTRY}/alpine_a290:${version}"
  # Revision plus a per-build nonce: a published image built from this very
  # commit still cannot carry it.
  nonce="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$(date +%s)-$RANDOM"
  provenance="$(checkout_revision)/${nonce}"

  # The Dockerfile pins its own FROM, so, as in release.yaml, the only build
  # argument is the version.
  log "Building $tag for $platform"
  docker buildx build \
    --platform "$platform" \
    --build-arg "BUILD_VERSION=$version" \
    --label "io.hass.type=addon" \
    --label "io.hass.arch=$arch" \
    --label "io.hass.version=$version" \
    --label "${PROVENANCE_LABEL}=${provenance}" \
    --tag "$tag" \
    --load \
    "$REPO_ROOT/$ADDON_DIR"

  docker pull --quiet "$REGISTRY_IMAGE" >/dev/null
  docker tag "$REGISTRY_IMAGE" "$REGISTRY_TAG"
  docker save "$tag" "$REGISTRY_TAG" -o "$WORKDIR/images.tar"
  {
    echo "PILOT_TAG=$tag"
    echo "PILOT_PROVENANCE=$provenance"
  } >"$WORKDIR/build.env"
  log "OK built $tag, provenance label ${provenance}"
}

# --- up: devcontainer, add-on copy, Supervisor, Core -------------------------
cmd_up() {
  if docker container inspect "$NAME" >/dev/null 2>&1; then
    fail "container $NAME already exists; run '$0 down' first"
  fi
  log "Starting devcontainer $DEVCONTAINER_IMAGE"
  # SUPERVISOR_CHANNEL explicitly: the devcontainer defaults to dev and its
  # template to beta. --privileged: it runs systemd and its own dockerd.
  docker run -d --name "$NAME" --privileged \
    -e SUPERVISOR_CHANNEL=stable \
    -v "${NAME}-docker:/var/lib/docker" \
    -v "${NAME}-containerd:/var/lib/containerd" \
    -v "${NAME}-mnt:/mnt/supervisor" \
    --tmpfs /tmp \
    "$DEVCONTAINER_IMAGE" >/dev/null

  # supervisor_run has no readiness check of its own and dies under set -e
  # if dockerd is not up yet.
  poll "inner Docker daemon ready" 120 2 dc docker info

  cmd_copy_addon

  dc bash -c 'curl -sS --fail --max-time 30 https://version.home-assistant.io/stable.json' \
    >"$WORKDIR/stable.json" || fail "could not fetch stable.json"
  log "stable.json: $(jq -c '{supervisor, homeassistant}' "$WORKDIR/stable.json")"
  # The parser that compiles the add-on's profile decides which policy
  # encoding the kernel enforces, so parser and kernel are the run's identity
  # as much as the Supervisor and Core versions are.
  log "kernel: $(dc uname -r)"
  log "devcontainer apparmor_parser: $(dc apparmor_parser --version 2>&1 | head -1)"
  log "AppArmor enabled: $(dc cat /sys/module/apparmor/parameters/enabled 2>&1 || true)"
  seed_core_version

  log "Starting Supervisor (channel stable)"
  docker exec -d "$NAME" bash -c 'supervisor_run > /var/log/supervisor_run.log 2>&1'
  poll "hassio_supervisor container running" 180 3 \
    bash -c "[[ \"\$(docker exec '$NAME' docker inspect -f '{{.State.Running}}' hassio_supervisor)\" == true ]]"
  cmd_wait_core
}

# Where Core really answers. 2026.9.3 under the Supervisor serves port 80 and
# 307s 8123 there; 2026.7.1 serves only 8123 (polygonal-zones, both observed).
core_url() {
  local url
  for url in http://127.0.0.1:8123 http://127.0.0.1; do
    if dc curl -sf "$url/api/onboarding" | jq -e 'type == "array"' >/dev/null 2>&1; then
      echo "$url"
      return
    fi
  done
  return 1
}

core_ready() {
  ha_cli core info --raw-json | jq -e '.data.version != "landingpage"' && core_url
}

# The landing page that precedes Core answers /api/onboarding with a 302,
# which curl -f counts as success, so readiness needs Core's JSON step list
# and a Core version that is not "landingpage".
cmd_wait_core() {
  poll "Home Assistant Core (not the landing page) answering" 1200 5 core_ready
}

# Only git-tracked files, as a user's clone has them, into apps/local.
cmd_copy_addon() {
  local dest="/mnt/supervisor/apps/local"
  dc mkdir -p "$dest"
  git -C "$REPO_ROOT" ls-files -z -- "$ADDON_DIR" |
    (cd "$REPO_ROOT" && tar --null -T - -cf -) |
    docker exec -i "$NAME" tar -xf - -C "$dest"
  # The Supervisor always pulls when config.yaml names an image and never
  # uses a local one, so the checkout's build is served from a registry and
  # named here. Only the copy is changed.
  dc sed -i "s|^image: .*|image: \"${LOCAL_REGISTRY}/alpine_a290\"|" \
    "$dest/$ADDON_DIR/config.yaml"
  if [[ "$BREAK" == apparmor ]]; then
    dc rm -f "$dest/$ADDON_DIR/apparmor.txt"
    log "PILOT_BREAK=apparmor: apparmor.txt removed from the add-on copy"
  fi
  log "Add-on copy: $(dc grep -E '^(version|image|homeassistant):' "$dest/$ADDON_DIR/config.yaml" | tr '\n' ' ')"
  log "Add-on copy's profile starts: $(dc head -n1 "$dest/$ADDON_DIR/apparmor.txt" 2>&1 || true)"
}

# --- versions: running Supervisor/Core against stable.json -------------------
cmd_versions() {
  local want_sup want_core machine got_sup got_core channel sup_img core_img rc=0
  [[ -s "$WORKDIR/stable.json" ]] || fail "no stable.json in $WORKDIR (run 'up' first)"
  machine="$(ha_cli info --raw-json | jq -er .data.machine)" || fail "could not read machine"
  want_sup="${PILOT_EXPECT_SUPERVISOR:-$(jq -er .supervisor "$WORKDIR/stable.json")}" ||
    fail "stable.json has no supervisor version"
  if [[ "$CORE_VERSION" == stable ]]; then
    want_core="${PILOT_EXPECT_CORE:-$(jq -er --arg m "$machine" '.homeassistant[$m] // .homeassistant.default' "$WORKDIR/stable.json")}" ||
      fail "stable.json has no Core version for $machine"
  else
    want_core="${PILOT_EXPECT_CORE:-$(resolved_core_version)}"
  fi
  got_sup="$(ha_cli supervisor info --raw-json | jq -er .data.version)" || fail "could not read Supervisor version"
  channel="$(ha_cli supervisor info --raw-json | jq -er .data.channel)" || fail "could not read channel"
  got_core="$(ha_cli core info --raw-json | jq -er .data.version)" || fail "could not read Core version"
  # The API and the image actually running are checked separately.
  sup_img="$(dc docker inspect -f '{{.Config.Image}}' hassio_supervisor)"
  core_img="$(dc docker inspect -f '{{.Config.Image}}' homeassistant)"

  log "machine=$machine channel=$channel core_leg=$CORE_VERSION"
  log "Supervisor expected=$want_sup observed=$got_sup image=$sup_img"
  log "Core       expected=$want_core observed=$got_core image=$core_img"
  [[ "$channel" == stable ]] || { log "FAIL channel is $channel, not stable"; rc=1; }
  [[ "$got_sup" == "$want_sup" && "$sup_img" == *":$want_sup" ]] ||
    { log "FAIL Supervisor does not match $want_sup"; rc=1; }
  [[ "$got_core" == "$want_core" && "$core_img" == *":$want_core" ]] ||
    { log "FAIL Core does not match $want_core"; rc=1; }
  ((rc == 0)) || fail "version check"
  log "OK versions match"
}

# --- the floor leg -----------------------------------------------------------
# `ha core update --version` cannot pin a floor: the older Core refuses the
# .storage the newer one already wrote, and the Supervisor rolls back to
# stable while the CLI still reports ok. Instead the Supervisor is told the
# version before its first start, and with no Core container it installs
# that version directly (polygonal-zones ADR 0001 row 11). The layout of
# homeassistant.json is internal; the version check is what catches a change.
seed_core_version() {
  local v
  [[ "$CORE_VERSION" == stable ]] && return
  v="$(resolved_core_version)"
  log "Seeding Core $v before the Supervisor's first start"
  jq -n --arg v "$v" '{version: $v}' |
    docker exec -i "$NAME" tee /mnt/supervisor/homeassistant.json >/dev/null
}

# --- sideload: serve the checkout's image to the inner daemon ----------------
cmd_sideload() {
  # shellcheck source=/dev/null
  source "$WORKDIR/build.env"
  # /var/tmp: /tmp is a tmpfs that docker cp does not reach.
  docker cp "$WORKDIR/images.tar" "$NAME:/var/tmp/images.tar"
  dc docker load -i /var/tmp/images.tar
  dc rm -f /var/tmp/images.tar
  # After supervisor_run, which removes every container on the inner daemon.
  if ! dc docker inspect a290-pilot-registry >/dev/null 2>&1; then
    dc docker run -d --restart=always --name a290-pilot-registry \
      -p "${LOCAL_REGISTRY}:5000" "$REGISTRY_TAG" >/dev/null
  fi
  poll "loopback registry ready" 60 2 dc curl -sf "http://${LOCAL_REGISTRY}/v2/"
  dc docker push --quiet "$PILOT_TAG"
  # Only the registry may supply it, so drop the loaded copy.
  dc docker rmi "$PILOT_TAG" >/dev/null
  log "OK $PILOT_TAG served from the devcontainer's loopback registry"
}

# --- egress: nothing an add-on sends may leave the hassio network ------------
# The add-on logs in to Renault at start. The credentials are fake, but the
# rule is that Renault is never reached at all. CLAUDE.md's boot test does it
# with --add-host, which a Supervisor-created container cannot be given, so
# instead every forwarded packet from the add-on range to anywhere outside
# the hassio network is refused. That is stricter than blackholing three
# hosts: it covers any host renault-api adds later. DNS still resolves (the
# hassio DNS plugin forwards the lookup), but no connection can be opened.
# The Supervisor, Core and the inner dockerd's image pulls are not in the
# add-on range, so they are unaffected.
#
# The broker is in the same range, so a range-wide counter cannot say whose
# connection was refused. The chain therefore counts per source address
# first, one rule per address in the range, each jumping to an empty chain so
# `iptables -L` always has a target column to parse, then refuses.
EGRESS_CHAIN="A290-PILOT-EGRESS"
EGRESS_SEEN="A290-PILOT-SEEN"

# Captured, then matched: `producer | grep -q` under pipefail fails when grep
# exits on an early match and the producer takes SIGPIPE.
egress_rules_present() {
  local rules
  rules="$(dc iptables -S DOCKER-USER)" || return 1
  grep -q -- "$EGRESS_COMMENT" <<<"$rules"
}

egress_ruleset() {
  local i
  echo "*filter"
  echo ":$EGRESS_CHAIN - [0:0]"
  echo ":$EGRESS_SEEN - [0:0]"
  for i in $(seq 0 254); do
    echo "-A $EGRESS_CHAIN -s ${HASSIO_ADDON_RANGE%.*}.$i/32 -p tcp -j $EGRESS_SEEN"
  done
  echo "-A $EGRESS_CHAIN -p tcp -j REJECT --reject-with tcp-reset"
  echo "-A $EGRESS_CHAIN -j REJECT --reject-with icmp-port-unreachable"
  echo "-I DOCKER-USER 1 -s $HASSIO_ADDON_RANGE ! -d $HASSIO_NETWORK -m comment --comment $EGRESS_COMMENT -j $EGRESS_CHAIN"
  echo "COMMIT"
}

cmd_egress() {
  local rules6
  dc iptables -S DOCKER-USER >/dev/null || fail "no DOCKER-USER chain on the inner daemon"
  if ! egress_rules_present; then
    egress_ruleset | docker exec -i "$NAME" iptables-restore --noflush ||
      fail "could not install the egress rules"
  fi
  if [[ "$(dc docker network inspect hassio -f '{{.EnableIPv6}}')" == true ]]; then
    rules6="$(dc ip6tables -S DOCKER-USER)" || fail "hassio has IPv6 but there is no ip6tables DOCKER-USER chain"
    if ! grep -q -- "$EGRESS_COMMENT" <<<"$rules6"; then
      dc ip6tables -I DOCKER-USER 1 -s "$HASSIO_NETWORK6" ! -d "$HASSIO_NETWORK6" \
        -m comment --comment "$EGRESS_COMMENT" -j REJECT
    fi
  fi
  egress_rules_present || fail "egress rules did not install"
  [[ "$(egress_counts | wc -l | tr -d ' ')" == 255 ]] || fail "expected 255 per-address counting rules"
  log "OK add-on egress off the hassio network is refused, counted per source address:"
  dc iptables -L DOCKER-USER -v -n -x | sed 's/^/    /'
}

# "address packets" for every per-address counting rule.
egress_counts() {
  local listing
  listing="$(dc iptables -L "$EGRESS_CHAIN" -v -n -x)" || return 1
  awk -v seen="$EGRESS_SEEN" '$3 == seen { sub(/\/32$/, "", $8); print $8, $1 }' <<<"$listing"
}

# count_for COUNTS ADDRESS: that address's packets; fails if it has no rule.
count_for() {
  awk -v ip="$2" '$1 == ip { print $2; found = 1 } END { exit !found }' <<<"$1"
}

# egress_delta BEFORE AFTER ADDRESS: packets refused from ADDRESS in between.
egress_delta() {
  local before after
  before="$(count_for "$1" "$3")" || return 1
  after="$(count_for "$2" "$3")" || return 1
  echo $((after - before))
}

# Every address whose count moved, for the log.
egress_movers() {
  awk 'NR == FNR { b[$1] = $2; next } $2 != b[$1] { print "    " $1 " +" ($2 - b[$1]) }' \
    <(printf '%s\n' "$1") <(printf '%s\n' "$2")
}

# --- broker: the Mosquitto add-on, as users run it ---------------------------
# The add-on declares `services: mqtt:need` and run.sh takes the broker from
# the Supervisor's service registry, which only an add-on providing mqtt can
# populate. A plain broker container would never reach run.sh, so the broker
# is the official Mosquitto add-on. A pilot login is added for the probe.
discover() {
  ha_cli store reload >/dev/null 2>&1 || true
  # The API still says "addons" (Supervisor 2026.09.2); allow for the rename.
  ha_cli store apps --raw-json |
    jq -e --arg s "$1" '(.data.apps // .data.addons)[] | select(.slug == $s)'
}

started() {
  ha_cli apps info "$1" --raw-json | jq -e '.data.state == "started"'
}

mqtt_service_available() {
  supervisor_get /services | jq -e '.data.services[] | select(.slug == "mqtt") | .available == true'
}

broker_password() {
  [[ -s "$WORKDIR/broker.pw" ]] || { od -An -N16 -tx1 /dev/urandom | tr -d ' \n' >"$WORKDIR/broker.pw"; }
  cat "$WORKDIR/broker.pw"
}

cmd_broker() {
  local options
  poll "$BROKER_SLUG in the official store" 300 5 discover "$BROKER_SLUG"
  log "Installing $BROKER_SLUG"
  ha_ok store apps install "$BROKER_SLUG" || fail "install of $BROKER_SLUG failed"
  options="$(ha_cli apps info "$BROKER_SLUG" --raw-json |
    jq -ce --arg u "$BROKER_USER" --arg p "$(broker_password)" \
      '{options: (.data.options + {logins: [{username: $u, password: $p}]})}')" ||
    fail "could not read $BROKER_SLUG options"
  supervisor_post "/addons/$BROKER_SLUG/options" "$options" >/dev/null || fail "Supervisor rejected the broker options"
  ha_ok apps start "$BROKER_SLUG" || fail "start of $BROKER_SLUG failed"
  poll "$BROKER_SLUG started" 180 3 started "$BROKER_SLUG" || fail "$BROKER_SLUG did not start"
  # run.sh reads the broker once, at start: it must be registered first.
  poll "mqtt service registered with the Supervisor" 120 3 mqtt_service_available ||
    fail "the broker never registered the mqtt service"
  ha_cli apps info "$BROKER_SLUG" --raw-json | jq -c '.data | {version, state}'
}

# --- install: discover, install, configure, start ----------------------------
cmd_install() {
  local options username="$STUB_USERNAME" extra='{}'
  egress_rules_present || fail "refusing to start the add-on without the egress rules (run 'egress')"
  if [[ "$BREAK" == start ]]; then
    username=""
    log "PILOT_BREAK=start: installing with an empty username"
  fi
  if [[ "$BREAK" == dashboard ]]; then
    # In deploy.py's RESERVED_URL_PATHS: the Supervisor's schema accepts it
    # (str), the add-on refuses to deploy over a built-in panel and logs the
    # skip, and Core never gets a dashboard at that path.
    extra='{"dashboard_url_path": "developer-tools"}'
    log "PILOT_BREAK=dashboard: installing with dashboard_url_path=developer-tools"
  fi
  # Local add-on discovery is known to be fragile (supervisor#3976).
  poll "$SLUG discovered in the local store" 180 5 discover "$SLUG"
  log "Installing $SLUG"
  ha_ok store apps install "$SLUG" || fail "install of $SLUG failed"
  log "Setting stub options through the Supervisor API"
  # The Supervisor validates the whole set, so change keys of the current one.
  # deploy_dashboard and dashboard_url_path stay at config.yaml's defaults:
  # the dashboard probe reads them back and requires that deploy to have
  # happened, so a user's first start is what the pilot tests.
  options="$(ha_cli apps info "$SLUG" --raw-json |
    jq -ce --arg u "$username" --arg p "$STUB_PASSWORD" --arg a "$STUB_ACCOUNT_ID" --arg v "$STUB_VIN" --argjson x "$extra" \
      '{options: (.data.options + {username: $u, password: $p, account_id: $a, vin: $v, log_level: "debug"} + $x)}')" ||
    fail "could not read the add-on's current options"
  supervisor_post "/addons/$SLUG/options" "$options" >/dev/null || fail "Supervisor rejected the options"
  # The baseline the egress check diffs against: taken immediately before the
  # start, so only what happens after it can count.
  local baseline
  baseline="$(egress_counts)" || fail "could not read the egress counters"
  [[ "$(wc -l <<<"$baseline" | tr -d ' ')" == 255 ]] || fail "the egress counters are incomplete"
  printf '%s\n' "$baseline" >"$WORKDIR/egress-baseline.txt"
  log "Starting $SLUG"
  ha_ok apps start "$SLUG" || fail "start of $SLUG failed"
  poll "Supervisor reports $SLUG started" 180 3 started "$SLUG" || fail "$SLUG did not start"
  ha_cli apps info "$SLUG" --raw-json | jq -c '.data | {version, state, ingress, apparmor, boot}'
}

# --- provenance: the running container came from this checkout ---------------
cmd_provenance() {
  local got image want_rev
  [[ -s "$WORKDIR/build.env" ]] || fail "no build.env in $WORKDIR (run 'build' first)"
  # shellcheck source=/dev/null
  source "$WORKDIR/build.env"
  want_rev="$(checkout_revision)"
  got="$(dc docker inspect -f "{{index .Config.Labels \"$PROVENANCE_LABEL\"}}" "$ADDON_CONTAINER")" ||
    fail "no container $ADDON_CONTAINER"
  image="$(dc docker inspect -f '{{.Config.Image}}' "$ADDON_CONTAINER")"
  log "Add-on container image=$image"
  log "Provenance expected=$PILOT_PROVENANCE observed=${got:-<none>} checkout=$want_rev"
  [[ "$got" == "$PILOT_PROVENANCE" ]] ||
    fail "the running add-on was not built by this run from the checkout under test"
  [[ "${got%%/*}" == "$want_rev" ]] || fail "the build's revision is not the checkout's ($want_rev)"
  [[ "$image" == "$PILOT_TAG" ]] || fail "the add-on runs $image, not $PILOT_TAG"
  log "OK the running add-on is this run's build of $want_rev"
}

# --- probe -------------------------------------------------------------------
addon_ip() {
  dc docker inspect -f '{{with index .NetworkSettings.Networks "hassio"}}{{.IPAddress}}{{end}}' "$ADDON_CONTAINER"
}

healthz_ok() {
  [[ "$(dc curl -sf --max-time 5 "http://$1:8099/healthz")" == ok ]]
}

docker_healthy() {
  [[ "$(dc docker inspect -f '{{.State.Health.Status}}' "$ADDON_CONTAINER")" == healthy ]]
}

addon_log() {
  dc docker logs "$ADDON_CONTAINER" 2>&1
}

# The container's whole log: `ha apps logs` returns only the tail, and at
# debug level the first poll scrolls out of it within minutes. Captured, then
# matched, for the same pipefail reason as egress_rules_present.
addon_log_has() {
  local out
  out="$(addon_log)" || return 1
  grep -q -- "$1" <<<"$out"
}

# Retained messages only: -W ends the read once the retained set is drained.
mqtt_read() {
  dc docker exec "$BROKER_CONTAINER" mosquitto_sub -h 127.0.0.1 -p 1883 \
    -u "$BROKER_USER" -P "$(broker_password)" --retained-only -W 5 -v "$@" 2>/dev/null || true
}

check_discovery() {
  local out count bad
  out="$(mqtt_read -t "homeassistant/+/${EXPECT_NODE}/+/config")"
  # "topic payload" per line; an empty payload is a cleared (unsupported) entity.
  count="$(awk 'NF >= 2' <<<"$out" | wc -l | tr -d ' ')"
  log "retained discovery configs under node '$EXPECT_NODE': $count"
  awk 'NF >= 2 { split($1, t, "/"); print t[2] }' <<<"$out" | sort | uniq -c | sed 's/^/    /'
  ((count > 0)) || fail "no MQTT discovery config published under homeassistant/+/${EXPECT_NODE}/"
  # Every config must name the add-on's device, or HA files it elsewhere.
  bad="$(awk 'NF >= 2 { sub(/^[^ ]+ /, ""); print }' <<<"$out" |
    jq -c --arg n "$EXPECT_NODE" 'select((.device.identifiers // []) | index($n) | not) | .unique_id' 2>&1 || echo parse-error)"
  [[ -z "$bad" ]] || fail "discovery configs without device identifier $EXPECT_NODE: $bad"
  log "OK $count discovery configs published, all on device $EXPECT_NODE"
}

check_availability() {
  local got
  got="$(mqtt_read -t "${EXPECT_NODE}/availability" | awk '{print $2}')"
  log "retained ${EXPECT_NODE}/availability: ${got:-<none>}"
  [[ "$got" == online ]] || fail "the poll loop never published availability online"
}

# No connection off the hassio network may have succeeded, and the refusal
# must have been exercised by THIS add-on since it started, or the check
# proves nothing: the broker shares the range, and counters outlive restarts.
check_egress() {
  local ip baseline after refused log_text
  poll "the first poll has failed (it cannot reach Renault)" 150 5 addon_log_has "Poll failed" ||
    fail "the add-on never logged a failed poll"
  ip="$(addon_ip)" || fail "no container $ADDON_CONTAINER"
  baseline="$(cat "$WORKDIR/egress-baseline.txt")" || fail "no egress baseline (run 'install')"
  after="$(egress_counts)" || fail "could not read the egress counters"
  log "egress TCP packets refused since the add-on started, by source:"
  egress_movers "$baseline" "$after"
  refused="$(egress_delta "$baseline" "$after" "$ip")" || fail "no egress counter for the add-on's address $ip"
  log "refused from the add-on ($ip): $refused"
  ((refused > 0)) || fail "nothing from the add-on's own address was refused, so the check proves nothing"
  if addon_log_has "Published: "; then
    fail "the add-on logged a successful poll, so it reached Renault"
  fi
  log_text="$(addon_log)" || true
  grep -E "Poll failed|Cannot connect" <<<"$log_text" | head -3 | sed 's/^/    /' || true
  log "OK the add-on tried Renault and was refused before any connection opened"
}

# --- dashboard: Core holds the dashboard the options deploy -------------------
# The add-on deploys its dashboard through Core's WebSocket API at start, and
# deploy.py catches every error there and only logs "Dashboard auto-deploy
# skipped", so without this check a broken deploy leaves the pilot green.
# Core's Lovelace API is WebSocket-only, and the Supervisor's Core proxy admits
# only an add-on token with homeassistant_api (supervisor/api/proxy.py; the
# CLI's token gets 401), so the query runs inside the add-on container with
# its token and the aiohttp it ships. It asks Core, not the add-on's log, what
# dashboards exist and what each holds.
DASHBOARD_QUERY='
import asyncio, json, os, sys
import aiohttp

async def cmd(ws, n, **payload):
    payload["id"] = n
    await ws.send_json(payload)
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=15)
        if msg.get("id") == n and msg.get("type") == "result":
            return msg

async def main():
    out = {"dashboards": None, "configs": {}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://supervisor/core/websocket",
                                timeout=aiohttp.ClientTimeout(total=30)) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": os.environ["SUPERVISOR_TOKEN"]})
            if (await ws.receive_json()).get("type") != "auth_ok":
                raise SystemExit("Core WebSocket auth failed")
            res = await cmd(ws, 1, type="lovelace/dashboards/list")
            if not res.get("success"):
                raise SystemExit("lovelace/dashboards/list failed: %s" % res.get("error"))
            out["dashboards"] = [d.get("url_path") for d in res.get("result") or []]
            for i, url_path in enumerate(sys.argv[1:], start=2):
                res = await cmd(ws, i, type="lovelace/config", url_path=url_path, force=False)
                if res.get("success"):
                    cfg = res.get("result") or {}
                    out["configs"][url_path] = {"title": cfg.get("title"),
                                                "views": len(cfg.get("views") or [])}
                else:
                    out["configs"][url_path] = {"error": (res.get("error") or {}).get("code")}
    print(json.dumps(out))

asyncio.run(main())
'

# lovelace_query URL_PATH...: {"dashboards": [url_path...], "configs": {url_path: {...}}}
lovelace_query() {
  printf '%s' "$DASHBOARD_QUERY" |
    docker exec -i "$NAME" docker exec -i "$ADDON_CONTAINER" python3 - "$@"
}

# The url_paths the add-on's options, as the Supervisor holds them, deploy to
# (deploy.py's _deploy_targets). The pilot leaves both options at config.yaml's
# defaults, so this is what a user's first start deploys.
dashboard_targets() {
  local options style url_path
  options="$(ha_cli apps info "$SLUG" --raw-json | jq -ce '.data.options')" ||
    fail "could not read the add-on's options"
  style="$(jq -r '.deploy_dashboard // "none"' <<<"$options")"
  url_path="$(jq -r '.dashboard_url_path // ""' <<<"$options")"
  # stderr: stdout is the list the caller captures.
  log "options: deploy_dashboard=$style dashboard_url_path=${url_path:-<none>}" >&2
  [[ -n "$url_path" ]] || fail "the add-on's options name no dashboard_url_path"
  case "$style" in
    standard | bubble) echo "$url_path" ;;
    both) printf '%s\n' "$url_path" "${url_path}-bubble" ;;
    *) fail "the add-on's options deploy no dashboard (deploy_dashboard=$style), so the deploy would go untested" ;;
  esac
}

# addon_log_deploy_settled URL_PATH...: the deploy has reached an outcome for
# every expected dashboard. deploy.py deploys 'both' targets one after the
# other and logs each on its own, so the first "Deployed" line is not the end:
# querying Core then would miss the second dashboard still being saved. A skip
# or error line ends the whole deploy, whichever target it was on.
addon_log_deploy_settled() {
  local out target
  out="$(addon_log)" || return 1
  if grep -qE "Dashboard auto-deploy skipped|skipping dashboard deploy|deploy_dashboard=.*not recognised" <<<"$out"; then
    return 0
  fi
  for target; do
    grep -qE "Deployed '[a-z]+' dashboard to '$target'|Dashboard '$target' already exists" <<<"$out" || return 1
  done
}

check_dashboard() {
  local targets target result dashboards views rc=0 log_text
  targets="$(dashboard_targets)" || exit 1
  if [[ -n "${PILOT_EXPECT_DASHBOARD:-}" ]]; then
    targets="$PILOT_EXPECT_DASHBOARD"
    log "PILOT_EXPECT_DASHBOARD overrides the expected dashboard: $targets"
  fi
  # The deploy runs once at start, before the poll loop; whichever way it
  # went, the add-on logs it. Not just the skip: a deploy that hangs would
  # never log at all, and the timeout is what catches that.
  # shellcheck disable=SC2086  # one url_path per word, by construction
  poll "the add-on has logged an outcome for every expected dashboard" 120 3 addon_log_deploy_settled $targets ||
    fail "the add-on never logged a dashboard deploy outcome for every expected dashboard"
  log_text="$(addon_log)" || fail "could not read the add-on's log"
  grep -E "Dashboard|dashboard" <<<"$log_text" | grep -vE "^.* DEBUG " | head -5 | sed 's/^/    /' || true
  if grep -q "Dashboard auto-deploy skipped" <<<"$log_text"; then
    log "FAIL the add-on logged 'Dashboard auto-deploy skipped'"
    rc=1
  fi
  # shellcheck disable=SC2086  # one url_path per word, by construction
  result="$(lovelace_query $targets)" || fail "could not query Core's Lovelace API from the add-on container"
  dashboards="$(jq -r '.dashboards | join(" ")' <<<"$result")" || fail "unparseable Lovelace query result: $result"
  log "Core's dashboards: ${dashboards:-<none>}"
  while read -r target; do
    if ! jq -e --arg t "$target" '.dashboards | index($t)' <<<"$result" >/dev/null; then
      log "FAIL dashboard '$target' is not in Core's dashboard list"
      rc=1
      continue
    fi
    views="$(jq -r --arg t "$target" '.configs[$t].views // "none (" + (.configs[$t].error // "no config") + ")"' <<<"$result")"
    log "dashboard '$target': views=$views title=$(jq -r --arg t "$target" '.configs[$t].title // "<none>"' <<<"$result")"
    if ! jq -e --arg t "$target" '.configs[$t].views > 0' <<<"$result" >/dev/null; then
      log "FAIL dashboard '$target' has no views in Core"
      rc=1
    fi
  done <<<"$targets"
  ((rc == 0)) || fail "dashboard check"
  log "OK the add-on deployed its dashboard through Core: ${targets//$'\n'/ }"
}

proc_label() {
  dc cat "/proc/$1/attr/apparmor/current" 2>/dev/null || dc cat "/proc/$1/attr/current"
}

# Enforced, not merely loaded: the kernel has the profile in enforce mode, the
# container was started with it, and the running processes carry it.
check_apparmor() {
  local want="$PROFILE (enforce)" container_profile pid label denials kernel_log rc
  if [[ "${PILOT_APPARMOR_CHECK:-}" == skip ]]; then
    [[ -z "${GITHUB_ACTIONS:-}" ]] || fail "PILOT_APPARMOR_CHECK=skip is for local runs only"
    log "WARNING AppArmor check skipped (PILOT_APPARMOR_CHECK=skip); this run proves nothing about confinement"
    return
  fi
  [[ "$(dc cat /sys/module/apparmor/parameters/enabled 2>&1)" == Y ]] ||
    fail "AppArmor is not enabled on this kernel, so nothing here is confined"
  container_profile="$(dc docker inspect -f '{{.AppArmorProfile}}' "$ADDON_CONTAINER")"
  log "container AppArmorProfile=${container_profile:-<none>}"
  [[ "$container_profile" == "$PROFILE" ]] ||
    fail "the Supervisor started the add-on under '${container_profile:-<none>}', not its own profile $PROFILE"
  dc grep -Fx "$want" /sys/kernel/security/apparmor/profiles >/dev/null ||
    fail "profile $PROFILE is not loaded in enforce mode: $(dc grep -F "$PROFILE" /sys/kernel/security/apparmor/profiles 2>&1 || echo 'not loaded')"
  # Every process in the container, s6 and the poller alike.
  while read -r pid; do
    label="$(proc_label "$pid")"
    [[ "$label" == "$want" ]] || fail "pid $pid runs as '$label', not '$want'"
  done < <(dc docker top "$ADDON_CONTAINER" -eo pid | tail -n +2)
  log "OK every process in $ADDON_CONTAINER runs under '$want'"
  # A log that cannot be read, or is empty, would pass as "no denial": only a
  # search that ran and matched nothing may.
  kernel_log="$(dc dmesg 2>&1)" ||
    fail "could not read the kernel log, so a denial would go unseen: ${kernel_log:0:200}"
  [[ -n "$kernel_log" ]] || fail "the kernel log is empty, so a denial would go unseen"
  denials="$(grep -E "apparmor=\"DENIED\".*profile=\"$PROFILE\"" <<<"$kernel_log")" && rc=0 || rc=$?
  ((rc <= 1)) || fail "could not search the kernel log (grep exit $rc)"
  if ((rc == 0)); then
    head -20 <<<"$denials" | sed 's/^/    /' >&2
    fail "the kernel denied the add-on under its profile"
  fi
  log "OK no AppArmor denial for $PROFILE in the kernel log"
}

cmd_probe() {
  local ip
  ip="$(addon_ip)" || fail "no container $ADDON_CONTAINER"
  log "add-on address on hassio: ${ip:-<none>}"
  # The egress rules only cover the add-on range, so this is their precondition.
  [[ "$ip" == 172.30.33.* ]] || fail "the add-on is not in $HASSIO_ADDON_RANGE, so the egress rules do not cover it"
  poll "/healthz answers ok" 90 3 healthz_ok "$ip" || fail "/healthz on $ip:8099 did not answer ok"
  poll "Docker HEALTHCHECK healthy" 180 5 docker_healthy ||
    fail "the image's HEALTHCHECK never reported healthy"
  check_discovery
  check_availability
  check_egress
  check_dashboard
  check_apparmor
  log "OK $SLUG runs under the stable Supervisor"
}

cmd_diagnostics() {
  local out="${1:-$WORKDIR/diagnostics}"
  # The default output lives in WORKDIR, so a run that owns it keeps it.
  [[ -n "${1:-}" ]] || WORKDIR_OWNED=0
  mkdir -p "$out"
  dc cat /var/log/supervisor_run.log >"$out/supervisor_run.log" 2>&1 || true
  dc docker ps -a >"$out/inner-docker-ps.txt" 2>&1 || true
  ha_cli supervisor logs >"$out/supervisor.log" 2>&1 || true
  ha_cli core logs >"$out/core.log" 2>&1 || true
  addon_log >"$out/addon.log" 2>&1 || ha_cli apps logs "$SLUG" >"$out/addon.log" 2>&1 || true
  ha_cli apps logs "$BROKER_SLUG" >"$out/broker.log" 2>&1 || true
  ha_cli resolution info --raw-json >"$out/resolution.json" 2>&1 || true
  { dc iptables -L DOCKER-USER -v -n -x; egress_counts | awk '$2 > 0'; } >"$out/egress.txt" 2>&1 || true
  # AppArmor is the runner host's kernel, not the devcontainer's: a denial for
  # the add-on's profile shows up in the host ring buffer with the operation
  # and address family that was refused, which the add-on's own log never says.
  {
    echo "kernel: $(dc uname -r 2>&1)"
    echo "parser: $(dc apparmor_parser --version 2>&1 | head -1)"
    echo "apparmor enabled: $(dc cat /sys/module/apparmor/parameters/enabled 2>&1)"
    echo "--- kernel network features"
    dc ls /sys/kernel/security/apparmor/features/ 2>&1
    dc ls /sys/kernel/security/apparmor/features/network 2>&1
    echo "--- loaded profiles matching the add-on"
    dc grep -F "$PROFILE" /sys/kernel/security/apparmor/profiles 2>&1
    echo "--- the add-on copy's profile header"
    dc head -n 3 "/mnt/supervisor/apps/local/$ADDON_DIR/apparmor.txt" 2>&1
    echo "--- dmesg apparmor lines"
    dc dmesg 2>&1 | grep -i apparmor
  } >"$out/apparmor.txt" 2>&1 || true
  log "Diagnostics in $out"
}

cmd_down() {
  docker rm -f -v "$NAME" >/dev/null 2>&1 || true
  docker volume rm "${NAME}-docker" "${NAME}-containerd" "${NAME}-mnt" >/dev/null 2>&1 || true
  docker image rm "$REGISTRY_TAG" >/dev/null 2>&1 || true
  log "Removed $NAME and its volumes"
}

cleanup_all() {
  cmd_down
  cleanup_workdir
}

cmd_all() {
  local t0=$SECONDS t phase timings=""
  export PILOT_WORKDIR="$WORKDIR"
  if [[ "${PILOT_KEEP:-0}" == 1 ]]; then
    WORKDIR_OWNED=0
    log "PILOT_KEEP=1: leaving $NAME running and its state in $WORKDIR"
  else
    trap 'cleanup_all' EXIT
  fi
  [[ -z "$BREAK" || "$BREAK" == start || "$BREAK" == apparmor || "$BREAK" == dashboard ]] ||
    fail "unknown PILOT_BREAK '$BREAK'"
  # Each phase in its own process: a function called on the left of || runs
  # with set -e disabled, which would let a failed step pass silently.
  for phase in build up versions sideload egress broker install provenance probe; do
    t=$SECONDS
    log "=== $phase"
    if ! bash "${BASH_SOURCE[0]}" "$phase"; then
      bash "${BASH_SOURCE[0]}" diagnostics || true
      fail "phase $phase (timings so far: ${timings})"
    fi
    timings+="$phase=$((SECONDS - t))s "
  done
  log "Timings: ${timings}total=$((SECONDS - t0))s"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    echo "Supervisor pilot ($(docker_arch), Core leg ${CORE_VERSION}): ${timings}total=$((SECONDS - t0))s" >>"$GITHUB_STEP_SUMMARY"
  fi
}

case "${1:-}" in
  build | up | wait-core | versions | sideload | egress | broker | install | provenance | probe | diagnostics | down | all | copy-addon)
    cmd="${1//-/_}"
    shift
    "cmd_$cmd" "$@"
    ;;
  *)
    echo "usage: $0 {all|build|up|wait-core|versions|sideload|egress|broker|install|provenance|probe|diagnostics|down}" >&2
    exit 2
    ;;
esac
