# CLAUDE.md

Home Assistant **add-on** for the **Alpine A290** EV. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.alpine_a290_*` / `binary_sensor.*`
/ `button.*` / `number.*` entities over **MQTT auto-discovery** — no shell scripts, no
`venv`, no `secrets.yaml`. Credentials are entered on the add-on's Configuration page.

**Tier 0.** Global rules (dual review, container-verify, trunk/merge policy, Conventional
Commits, HA cadence) live in `~/.claude/CLAUDE.md`; this file is A290-specifics only.

A sibling repo, **`MatthewHobbs/r5-ha-addon`**, is the Renault 5 port of the same code.
**Keep the two in lockstep** — most feature/fix work here should be mirrored there
(adjusting for per-model API differences), and vice-versa.

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
`renault-api==0.5.12`, `paho-mqtt==2.1.0`, `PyYAML==6.0.3`.

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

The harness **pins its render inputs** in `ui-tests/run.sh` so the gate is deterministic — HA
image `2026.7.1`, mushroom `v5.1.1`, button-card `v7.0.1`, card-mod `v4.2.1`. A floating
`:stable`/`@latest`/`@master` previously drifted under the gate and made it flake (marginal
sub-pixel truncations on one high-DPR device that vanished on the next run). Bump all four
**together and deliberately** when tracking upstream; the gate confirms the new combo renders
clean. Keep them identical to the r5 twin's `ui-tests/run.sh` (lockstep).

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

## Release / versioning

Any user-facing change bumps **`alpine_a290/config.yaml` `version`** and adds a
**`alpine_a290/CHANGELOG.md`** entry (Supervisor keys the update on the version). When
mirroring to `r5-ha-addon`, bump **`renault_5/config.yaml`** and the `VERSION` constant in
**`renault_5/app/main.py`** together. Feature branches are **squash-merged** to `main` and
deleted once merged.

## Gotchas

- **MQTT entity ids.** HA ignores the discovery `object_id`; the real `entity_id` is
  `slug(device name + " " + friendly name)`. Derive ids (e.g. for dashboards/tests) from
  the *names*, not from `object_id`.
- **Secrets never get logged.** The credentials (My Alpine username/password, VIN,
  account_id, GPS) are sensitive. `debug_dump: true` logs decoded API responses but routes
  everything through `_debug_redact` first; never add a logging path that bypasses it, and
  never use `log_level: debug` for diagnosis (the library prints access tokens at that
  level — `debug_dump` exists precisely to avoid that).
- **Dashboards live in the add-on.** The old `a290-dashboard-view` repo is archived; all
  dashboard work happens in `alpine_a290/dashboards/`. Typography is intentionally uniform
  across tabs (no per-screen font/size changes); overflow is handled by `white-space:normal`
  clean-word-break wrapping, not by shrinking text.
