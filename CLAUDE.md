# CLAUDE.md

Home Assistant **add-on** for the **Alpine A290** EV. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.alpine_a290_*` / `binary_sensor.*`
/ `button.*` / `number.*` entities over **MQTT auto-discovery** — no shell scripts, no
`venv`, no `secrets.yaml`. Credentials are entered on the add-on's Configuration page.

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
source for what each car exposes. The A290 forbids several endpoints (charge-start,
charge-mode, pressure); the add-on probes `supports_endpoint()` at startup and only
publishes what's available.

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

Ruff config (`ruff.toml`): line-length 120, target py311, `select = E,F,W,B,I`,
`ignore = E501,B008`.

## Before recommending a merge: build the container locally

This add-on ships as a container image that HA Supervisor pulls **by tag** (`config.yaml`
`version`), so the production platform can't be live-verified until *after* a release is
tagged and published. A version-bump / runtime PR is therefore **not** considered verified
by CI alone — build and boot the image locally and observe the changed behaviour first:

```sh
docker buildx build --platform linux/amd64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19 \
  -t a290-local alpine_a290
# then run with a stub /data/options.json and curl http://localhost:<port>/healthz, check logs, etc.
```

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
