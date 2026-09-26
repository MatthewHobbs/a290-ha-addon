# CLAUDE.md

Home Assistant **add-on** for the **Alpine A290** EV. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.alpine_a290_*` / `binary_sensor.*`
/ `button.*` / `number.*` entities over **MQTT auto-discovery** — no shell scripts, no
`venv`, no `secrets.yaml`. Credentials are entered on the add-on's Configuration page.

**Data class: Public. Audience: others** (`.data-class`, a machine-local marker the global git
excludes keep out of the repo; owner-approved 2026-09-24). Public means either provider may review it; others install it, so runtime changes get the container
boot below. Global rules (review, trunk/merge policy, Conventional Commits, HA cadence) live in
`~/.claude/CLAUDE.md`; this file is A290-specifics only.

A sibling repo, **`MatthewHobbs/r5-ha-addon`**, is the Renault 5 port of the same code.
**Keep the two in lockstep** — most feature/fix work here should be mirrored there
(adjusting for per-model API differences), and vice-versa.

**The parity check enforces that** ([ADR 0001](docs/adr/0001-r5-inherits-from-a290.md), stage A;
decisions live in `docs/adr/`). `scripts/parity_check.py` rewrites r5's names onto a290's
(`scripts/parity/map.tsv`), diffs the trees, and fails on any difference that
`scripts/parity/expected.tsv` does not list with a category, an exact count and a reason, and on
any entry whose difference is gone. Run `just parity` (clones r5's main, or
`PARITY_TWIN=../r5-ha-addon just parity` to use a git checkout; tracked files and modes are
compared, and a non-git tree is refused unless the script gets `--walk`); CI runs it as
`Parity with r5`. When
your change creates a difference, port it or add a `pending` entry naming its Target; when you
fix one, delete its entry. Never raise a count without reading the lines it printed. The script
is shared verbatim with r5; the map and list are each repo's own copy.

## Layout

```
alpine_a290/                 the add-on (this is what HA installs)
  app/
    main.py                  asyncio poller, MQTT discovery, controls, charge-limit
                             numbers, debug_dump, health endpoint (/healthz)
    catalog.py               entity tables — SENSORS / BINARY_SENSORS / BUTTONS / NUMBERS,
                             endpoint constants, RETIRED_* cleanup lists
    deploy.py                optional dashboard auto-deploy via the HA core API
    requirements.txt         pinned deps (see "Dependencies")
  tests/                     pytest — conftest.py, test_main.py, test_runtime.py, test_deploy.py
  config.yaml                add-on manifest: version, options + schema
  Dockerfile                 alpine base, HEALTHCHECK, root user (# nosec B104 for 0.0.0.0 bind)
  run.sh                     bashio entrypoint (reads /data/options.json)
  dashboards/                front-end.txt (standard) + front-end-bubble.txt (Bubble Card) + assets
  DOCS.md / CHANGELOG.md     the add-on's HA docs page + changelog
ui-tests/                    containerized HA + Playwright responsive/overflow gate
docs/                        dashboards-on-mobile.md + screenshots (user docs)
ruff.toml / repository.yaml / README.md / LICENSE
```

## Dependencies

`alpine_a290/app/requirements.txt` — all pinned, keep them pinned:
`renault-api==0.5.13`, `paho-mqtt==2.1.0`, `PyYAML==6.0.3`. Pinned in BOTH
`requirements.in` (the source) and `requirements.txt` (hash-locked) — bump both, or the next
regeneration silently reverts the one you missed.

**Do not bump `renault-api` casually.** Per-model endpoint support is hard-coded in the
library at `renault_api/kamereon/models.py` → `_VEHICLE_ENDPOINTS` (A290 is model
`A5E1AE`, R5 is `R5E1VE`). That map — not the readthedocs pages — is the authoritative
source for what each car exposes. The A290 forbids several endpoints (charge-mode, pressure,
charge-stop); charge-start became available in renault-api 0.5.13 (KCM "via-settings", model
`A5E1AE`) — it starts a charge by clearing the car's **own** scheduled programs, so it only acts
when the car's built-in timer is used and is a **no-op under external scheduling (Octopus
Intelligent)** — confirmed on a real A290 (car stayed "Waiting to Charge"; no car-side programs to
clear). The button is still published; docs steer Octopus users to Bump Charge / the physical
timer. The add-on probes `supports_endpoint()` at startup and only publishes what's available.

**The pinned 0.5.13 still carries two wrong entries for `A5E1AE`**, which is why the add-on
carries its own guards rather than trusting the table: `actions/refresh-location` is absent (it
works on the car — verified, response in 12s; DOCS.md and README.md still say it may be
refused (403) on purpose, because one verified car is not every account or region), and
`hvac-settings` is declared while the server answers `502000` to every call, which is what the
v1.23.1 circuit breaker exists for. Both are filed upstream as
[#2254](https://github.com/hacf-fr/renault-api/pull/2254) and
[#2255](https://github.com/hacf-fr/renault-api/pull/2255); they will not appear in a release
before 0.5.14, so do not expect a bump to remove those guards until then.

## Local checks — run the FULL suite before pushing

CI (`.github/workflows/ci.yaml`) has four jobs: **lint, test, security, build**. Run all of
them locally before pushing — not just ruff + pytest. macOS vs Linux behaviour differs
(the UI gate has caught Linux-only font truncations a local macOS run missed), so a green
local partial run is not a green CI.

```sh
# lint
ruff check alpine_a290/app
yamllint -c .yamllint alpine_a290 repository.yaml
hadolint -c .hadolint.yaml alpine_a290/Dockerfile
shellcheck alpine_a290/run.sh

# test (coverage gate is 95%)
python3 -m pytest alpine_a290/tests -q --cov=alpine_a290/app --cov-report=term-missing --cov-fail-under=95

# security
bandit -r alpine_a290/app -ll
pip-audit -r alpine_a290/app/requirements.txt
trivy fs --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed .
```

The `ui-tests/` gate (its own `ui-tests.yaml` workflow, path-filtered to dashboards +
ui-tests) is run with `ui-tests/run.sh` — it boots a throwaway HA container, seeds entities,
and uses Playwright across ~10 phone viewports to fail on any text truncation or
`hui-error-card`. Run it whenever you touch `alpine_a290/dashboards/` or `ui-tests/`.

The harness **pins its render inputs** in `ui-tests/run.sh` so the gate is deterministic: the
HA image and the mushroom, button-card, card-mod and Bubble Card versions. A floating
`:stable`/`@latest`/`@master` previously drifted under the gate and made it flake (marginal
sub-pixel truncations on one high-DPR device that vanished on the next run). Hand-bumped pins
went stale instead (2026.7.1 while users ran 2026.9.x), so **Renovate tracks all five** and
proposes them as **one grouped PR** ("UI gate render inputs", stable HA releases and `vX.Y.Z`
card tags only), bumped together; the gate on that PR confirms the combination before it is
pinned. Keep them identical to the r5 twin's `ui-tests/run.sh` and Renovate rules (lockstep).
The gate runs **two legs**: that stable pin, and the declared minimum (`homeassistant:` in
`alpine_a290/config.yaml`, currently 2026.8.1), read from the manifest so the test cannot drift
from the claim. The stable leg warns (does not fail) while its pin lags current stable; the fix is
merging Renovate's render-inputs PR. `scripts/ha_minimum_check.py` (CI Lint job, `just ha-min`)
fails if the minimum is missing or newer than the `.1` of the month before current stable, read
live from `version.home-assistant.io/stable.json`, or is not a published release on PyPI (exact
spelling, no pre-releases); an unreachable PyPI fails the step too. The script is shared verbatim
with r5, so change it in both repos together.

Ruff config (`ruff.toml`): line-length 120, target py314, `select = E,F,W,B,I`,
`ignore = E501,B008`.

## Before recommending a merge: build and boot the container locally

Per the global container rule — this add-on's image is pulled by tag (`config.yaml` `version`),
so build and boot it locally and observe the changed behaviour before merge on any runtime PR.

Two things make a naive run fail, both worth knowing before you spend time debugging them:

- **`bashio::config` reads the Supervisor API, not `/data/options.json`.** Mounting a stub
  options file achieves nothing: `/run.sh` gets empty config and the add-on correctly exits
  with `Missing required setting`. Bypass `run.sh` with `--entrypoint python3` and pass the
  `A290_*` env vars directly.
- **The poller needs an MQTT broker** or it dies on `ConnectionRefusedError` before it ever
  serves `/healthz`.

There are no test credentials to use. `renault-api` has no sandbox and authenticates only
against production Gigya/Kamereon, so blackhole the three real hosts and the boot test never
reaches Renault. The verification signal is identical; only the poll error text changes.
The S3 host is easy to miss — `get_api_keys()` hits it before login even starts.

```sh
docker buildx build --platform linux/amd64 -t a290-local alpine_a290

docker network create a290v
printf 'listener 1883 0.0.0.0\nallow_anonymous true\n' > /tmp/mosq.conf
docker run -d --name a290-mqtt --network a290v \
  -v /tmp/mosq.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto:2

docker run -d --name a290-verify --network a290v -p 8099:8099 \
  --add-host accounts.eu1.gigya.com:127.0.0.1 \
  --add-host api-wired-prod-1-euw1.wrd-aws.com:127.0.0.1 \
  --add-host renault-wrd-prod-1-euw1-myrapp-one.s3-eu-west-1.amazonaws.com:127.0.0.1 \
  -e A290_USERNAME=stub@example.invalid -e A290_PASSWORD=stub-not-a-real-credential \
  -e A290_ACCOUNT_ID=0000000000 -e A290_VIN=VF1STUBVIN0000000 \
  -e A290_LOCALE=en_GB -e A290_POLL_INTERVAL=300 -e A290_BATTERY_CAPACITY_KWH=52 \
  -e A290_STALE_HOURS=6 -e A290_PUBLISH_LOCATION=true -e A290_GPS_PRECISION=4 \
  -e A290_PRECONDITION_TEMPERATURE=20 \
  -e A290_LOG_LEVEL=debug -e A290_DEBUG_DUMP=false \
  -e A290_DEPLOY_DASHBOARD=none -e A290_REDEPLOY_DASHBOARD=false \
  -e MQTT_HOST=a290-mqtt -e MQTT_PORT=1883 -e MQTT_USER= -e MQTT_PASS= \
  --entrypoint python3 a290-local -u /app/main.py

sleep 8
curl -s http://127.0.0.1:8099/healthz; echo
docker logs a290-verify 2>&1 | tail -20

docker rm -f a290-verify a290-mqtt; docker network rm a290v
```

Expect `/healthz` to return `ok`, plus `MQTT connected — subscribed to commands, discovery
(re)published` and a `Published discovery: N sensors …` line. One `Poll failed … Cannot connect
to host accounts.eu1.gigya.com` is expected and correct: it is the proof no traffic left the
machine.

Exceptions (CI is enough): docs-only, CI-YAML-only, or test-only changes.

## Supervisor pilot (ADR 0002)

`.github/workflows/supervisor.yml` runs `scripts/supervisor-pilot.sh`. The script installs this
add-on under a real **stable-channel Supervisor** in the official add-on devcontainer, on amd64 and
aarch64. It runs two Core legs: current stable, and the `homeassistant:` minimum read from
`config.yaml`. It runs nightly, on demand, and on PRs that touch the pilot or what the Supervisor
runs. It is **not a required check** yet (ADR row 5). It checks that versions match `stable.json`,
that the running image is this checkout's build (provenance label), `/healthz`, the image
HEALTHCHECK, the retained MQTT discovery and availability topics, that Renault was tried and
refused, and that the dashboard the default options deploy (`deploy_dashboard: standard` at
`dashboard_url_path`) exists in Core with views, read back through Core's Lovelace WebSocket API
from inside the add-on container (the Supervisor's Core proxy admits only an add-on token), with
no `Dashboard auto-deploy skipped` in the log. It also checks AppArmor enforcement: the container
runs under `local_alpine_a290 (enforce)` and the kernel logged no denial. Adapted from
polygonal-zones' pilot; the traps it already solved:

- **The Supervisor always pulls** an add-on whose `config.yaml` names an `image:`, and never uses
  a local one. The copy in `apps/local` therefore points at a registry on the devcontainer's
  loopback, and a per-run provenance label proves the running container is this build.
- **A floor Core can't be reached with `ha core update --version`**: the older Core refuses the
  newer `.storage`. The version is seeded into `/mnt/supervisor/homeassistant.json` before the
  Supervisor first starts.
- **AppArmor is enforced only on the GitHub runner** (kernel 6.17, the devcontainer's parser
  4.1.0), not under Docker Desktop, which has no AppArmor. Locally, set
  `PILOT_APPARMOR_CHECK=skip`; the script refuses that under Actions.
- **Renault must never be reached**, and `--add-host` can't be given to a container the
  Supervisor creates. So an iptables `DOCKER-USER` rule refuses everything from the add-on range
  (`172.30.33.0/24`) to anywhere off the hassio network. The broker shares that range, so the
  rule counts per source address. The probe requires a refusal from **this add-on's own
  address** since just before it started, or it proves nothing.
- **The broker is the Mosquitto add-on**, not a service container. `services: mqtt:need` and
  `run.sh` take the broker from the Supervisor's service registry, which only a providing
  add-on fills.

Local run (about 2 minutes warm): `PILOT_APPARMOR_CHECK=skip scripts/supervisor-pilot.sh all`, with
`CORE_VERSION=minimum` for the floor. `PILOT_BREAK=start|apparmor|dashboard` breaks a run on
purpose.

## Release / versioning

Any user-facing change bumps **`alpine_a290/config.yaml` `version`** and adds a
**`alpine_a290/CHANGELOG.md`** entry (Supervisor keys the update on the version). When
mirroring to `r5-ha-addon`, bump **`renault_5/config.yaml`** `version` and its
**`renault_5/CHANGELOG.md`**; r5 has no version literal in code (`main.py` reads `R5_VERSION`,
which its release workflow sets from `config.yaml` via the Dockerfile's `BUILD_VERSION`).
Feature branches are **squash-merged** to `main` and deleted once merged.

## Gotchas

- **MQTT entity ids.** HA ignores the discovery `object_id`; the real `entity_id` is
  `slug(device name + " " + friendly name)`. Derive ids (e.g. for dashboards/tests) from
  the *names*, not from `object_id`.
- **Secrets never get logged.** The credentials (My Alpine username/password, VIN,
  account_id, GPS) are sensitive. `debug_dump: true` logs decoded API responses but routes
  everything through `_debug_redact` first; never add a logging path that bypasses it, and
  never lift the `renault_api` logger clamp in `main.py` (`setup_logging`). At DEBUG, renault-api
  0.5.13 logs full, unredacted Kamereon request and response bodies (VIN, account ids, unrounded
  GPS) and the app's API-key config — not access tokens, whose JWT is a header it never logs.
  The clamp keeps those records from ever being created; `debug_dump` is the redacted route.
- **Dashboards live in the add-on.** The old `a290-dashboard-view` repo is archived; all
  dashboard work happens in `alpine_a290/dashboards/`. Typography is intentionally uniform
  across tabs (no per-screen font/size changes); overflow is handled by `white-space:normal`
  clean-word-break wrapping, not by shrinking text.
