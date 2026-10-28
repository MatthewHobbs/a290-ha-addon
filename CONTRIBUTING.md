# Contributing

Home Assistant **add-on** for the **Alpine A290** EV. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.alpine_a290_*` / `binary_sensor.*`
/ `button.*` / `number.*` entities over **MQTT auto-discovery**. There are no shell scripts, no
`venv` and no `secrets.yaml`. Credentials are entered on the add-on's Configuration page.

Others install this add-on, so a runtime change gets the container boot below before it merges.

A sibling repo, **`MatthewHobbs/r5-ha-addon`**, is the Renault 5 port of the same code.
**Keep the two in lockstep.** Most feature and fix work here should be mirrored there
(adjusting for per-model API differences), and the other way round.

**The parity check enforces that** (decisions live in `docs/adr/`). `scripts/parity_check.py`
rewrites r5's names onto a290's (`scripts/parity/map.tsv`), diffs the trees, and fails on any
difference that
`scripts/parity/expected.tsv` does not list with a category, an exact count and a reason, and on
any entry whose difference is gone. Run `just parity` (clones r5's main, or
`PARITY_TWIN=../r5-ha-addon just parity` to use a git checkout; tracked files and modes are
compared, and a non-git tree is refused unless the script gets `--walk`). CI runs it as
`Parity with r5`. When your change creates a difference, port it or add a `pending` entry naming
its Target. When you fix one, delete its entry. Never raise a count without reading the lines it
printed. The script is shared verbatim with r5; the map and list are each repo's own copy.

## Layout

```
alpine_a290/                 the add-on (this is what HA installs)
  app/
    main.py                  asyncio poller, MQTT discovery, controls, charge-limit
                             numbers, debug_dump, health endpoint (/healthz)
    catalog.py               entity tables: SENSORS / BINARY_SENSORS / BUTTONS / NUMBERS,
                             endpoint constants, RETIRED_* cleanup lists
    deploy.py                optional dashboard auto-deploy via the HA core API
    panel.html               the read-only ingress status panel
    requirements.in/.txt     pinned deps (see "Dependencies")
  tests/                     pytest: runtime, MQTT, charge, config, deploy, dashboard
                             conformance and the renault-api contract
  config.yaml                add-on manifest: version, options + schema
  Dockerfile                 alpine base, HEALTHCHECK, root user (# nosec B104 for 0.0.0.0 bind)
  run.sh                     bashio entrypoint (options via bashio::config)
  apparmor.txt               the add-on's AppArmor profile
  dashboards/                front-end.txt (standard) + front-end-bubble.txt (Bubble Card), assets,
                             Packages/ (optional test mode), Templates/, INSTALLATION.md, USER-GUIDE.md
  DOCS.md / CHANGELOG.md     the add-on's HA docs page + changelog
ui-tests/                    containerized HA + Playwright responsive/overflow gate
docs/                        dashboards-on-mobile.md + screenshots (user docs)
ruff.toml / repository.yaml / README.md / LICENSE
```

## Dependencies

`alpine_a290/app/requirements.txt` pins the Python dependencies and keeps them pinned:
`renault-api`, `paho-mqtt` and `PyYAML`, at the versions in those two files. Each is pinned in BOTH
`requirements.in` (the source) and `requirements.txt` (hash-locked). Bump both, or the next
regeneration silently reverts the one you missed.

**The shared core.** Most of the runtime (MQTT, discovery, parsing, redaction, the debug dump)
lives in `renault-mqtt`, shared with the r5 add-on. The Dockerfile pins it by full commit SHA
(`ARG CORE_REF`, with the release tag in the comment above it) and installs it with
`--no-build-isolation`. No bot follows that pin: bump it by hand when a core release lands, like
any runtime change, with the container boot.

**CI tools.** The workflows and the justfile pin pytest, pytest-cov, bandit, pip-audit, playwright,
ruff and Trivy's version, and nothing bumps them: Dependabot reads `uses:` action pins and
manifests, not versions quoted inside a workflow step or a justfile recipe. Raise them by hand,
together, when a release matters — a major one separately. Workflow steps that need runtime
packages install the hash-locked `requirements.txt`, never a hand pin, so runtime Python stays
Dependabot's alone. ruff's version lives in `ruff.toml` (`required-version`), which `just lint`
runs, and moves with its action's pin. The workflows' Python stays on 3.14, the add-on runtime's;
when the base image moves, move `python-version` with it.

**`renault-api` follows upstream.** Dependabot proposes it weekly in a group of its own, so each
bump is its own PR. Review each bump against `renault_api/kamereon/models.py`
`_VEHICLE_ENDPOINTS` (the A290 is model `A5E1AE`, the R5 is `R5E1VE`), which
`tests/test_api_contract.py` pins, and boot the container. That map, not the readthedocs pages,
is the authoritative source for what each car exposes.

What is known about the A290 endpoints:

- The A290 forbids several endpoints (charge-mode, pressure, charge-stop).
- Charge-start became available in renault-api 0.5.13 (KCM "via-settings", model `A5E1AE`). It
  starts a charge by clearing the car's **own** scheduled programs, so it only acts when the car's
  built-in timer is used. It is a **no-op under external scheduling (Octopus Intelligent)**,
  confirmed on a real A290: the car stayed "Waiting to Charge" because there were no car-side
  programs to clear. The button is still published, and the docs steer Octopus users to Bump
  Charge or the physical timer.
- The add-on probes `supports_endpoint()` at startup and only publishes what is available.
- 0.5.14 corrected two `A5E1AE` entries that 0.5.13 got wrong (upstream
  [#2254](https://github.com/hacf-fr/renault-api/pull/2254) and
  [#2255](https://github.com/hacf-fr/renault-api/pull/2255)): `actions/refresh-location` is now
  declared (it works on the car, response in 12s), and `hvac-settings` is now `None`.
- The add-on's guards stay regardless. DOCS.md and README.md still say refresh-location may be
  refused (403) on purpose, because one verified car is not every account or region. The v1.23.1
  circuit breaker guards the *server's* `502000`, which no library release fixes. Confirm on the
  car before removing it.

## Local checks: run the full suite before pushing

CI (`.github/workflows/ci.yaml`) has seven jobs: **lint, test, security, build, docs-sync,
parity and comments** — the last three compare this repo against its twin and `DOCS.md`, check
that every file and symbol [the decision record](docs/adr/0001-what-this-add-on-is-why-and-how.md) cites
still exists (`just adr`), and hold code comments under 50 words. Run all of them locally before
pushing, not just ruff and pytest. macOS and Linux behave differently (the UI gate has caught
Linux-only font truncations a local macOS run missed), so a green local partial run is not a green
CI. `just ci` runs the static gates.

```sh
# lint — `just lint` runs exactly this, with the pinned ruff
ruff check alpine_a290/app
yamllint -c .yamllint alpine_a290 repository.yaml
hadolint -c .hadolint.yaml alpine_a290/Dockerfile
shellcheck alpine_a290/run.sh

# test (failures block CI; coverage is reporting-only)
python3 -m pytest alpine_a290/tests -q --cov=alpine_a290/app --cov-report=term-missing

# security
bandit -r alpine_a290/app -ll
pip-audit -r alpine_a290/app/requirements.txt
trivy fs --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed .
```

`scripts/pii_check.py` fails on personal data in any tracked file (coordinates, VINs, emails,
home-directory paths). Keep test data synthetic.

## UI gate

The `ui-tests/` gate (its own `ui-tests.yaml` workflow, path-filtered to dashboards and
ui-tests) is run with `ui-tests/run.sh`. It boots a throwaway HA container, seeds entities, and
uses Playwright across seven phone viewports to fail on any text truncation or
`hui-error-card`. Run it whenever you touch `alpine_a290/dashboards/` or `ui-tests/`.

The harness **pins its render inputs** in `ui-tests/run.sh` so the gate is deterministic: the HA
image and the mushroom, button-card, card-mod and Bubble Card versions. A floating
`:stable`/`@latest`/`@master` previously drifted under the gate and made it flake (marginal
sub-pixel truncations on one high-DPR device that vanished on the next run). Hand-bumped pins
went stale instead (2026.7.1 while users ran 2026.9.x), so the gate warns when the stable pin falls
behind current stable, and **all five move together or not at all**. Bump them in one change, with
a run of the gate confirming the combination; never one at a time, and keep them identical to the
r5 twin's `ui-tests/run.sh` (lockstep).

The gate has **two legs**: that stable pin, and the declared minimum (`homeassistant:` in
`alpine_a290/config.yaml`, currently 2026.8.1), read from the manifest so the test cannot drift
from the claim. The minimum leg renders only for a release PR, a change to `version:` or
`homeassistant:`, or a manual dispatch; otherwise it reports success with every step skipped.
The stable leg warns (does not fail) while its pin lags current stable; the fix
is bumping the render inputs in `ui-tests/run.sh`. `scripts/ha_minimum_check.py` (CI Lint job,
`just ha-min`) fails if the minimum is missing or newer than the `.1` of the month before
current stable, read live from `version.home-assistant.io/stable.json`, or is not a published
release on PyPI (exact spelling, no pre-releases). An unreachable PyPI fails the step too. The
script is shared verbatim with r5, so change it in both repos together.

Ruff config (`ruff.toml`): line-length 120, target py314, `select = E,F,W,B,I`,
`ignore = E501,B008`.

## Before merging a runtime change: build and boot the container locally

This add-on's image is pulled by tag (`config.yaml` `version`), so build and boot it locally and
observe the changed behaviour before merging any runtime PR.

Two things make a naive run mislead or fail:

- **`bashio::config` reads the Supervisor API, not `/data/options.json`.** Mounting a stub
  options file achieves nothing: `/run.sh` gets empty config and the add-on correctly exits
  with `Missing required setting`. Bypass `run.sh` with `--entrypoint python3` and pass the
  `A290_*` env vars directly.
- **The poller needs an MQTT broker.** With none, the core retries in the background and
  `/healthz` still answers `ok`, and `Published discovery` is logged either way. Only the
  `MQTT connected` line below proves the broker.

There are no test credentials. `renault-api` has no sandbox and authenticates only against
production Gigya/Kamereon, so blackhole the three real hosts and the boot test never reaches
Renault. The verification signal is identical; only the poll error text changes. The S3 host is
easy to miss: `get_api_keys()` hits it before login even starts.

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

## Supervisor pilot

`.github/workflows/supervisor.yml` runs `scripts/supervisor-pilot.sh`. The script installs this
add-on under a real **stable-channel Supervisor** in the official add-on devcontainer, on amd64
and aarch64. It runs two Core legs: current stable, and the `homeassistant:` minimum read from
`config.yaml`. Nightly it runs amd64 on Core stable only; on Wednesdays, on demand, and on every
PR it runs all four legs, which are **required checks**. The trigger is
unconditional and a `changes` job decides relevance, the way `ui-tests.yaml` does, so
a PR that touches neither the pilot nor the add-on's runtime files still reports all four in
seconds, with every step skipped, instead of leaving a required check pending. It checks that
versions match
`stable.json`, that the running image is this checkout's build (provenance label), `/healthz`,
the image HEALTHCHECK, the retained MQTT discovery and availability topics, that Renault was
tried and refused, and that the dashboard the default options deploy (`deploy_dashboard:
standard` at `dashboard_url_path`) exists in Core with views, read back through Core's Lovelace
WebSocket API from inside the add-on container (the Supervisor's Core proxy admits only an
add-on token), with no `Dashboard auto-deploy skipped` in the log. It also checks AppArmor
enforcement: the container runs under `local_alpine_a290 (enforce)` and the kernel logged no
denial. Adapted from polygonal-zones' pilot; the traps it already solved:

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

Local run (about 2 minutes warm): `PILOT_APPARMOR_CHECK=skip scripts/supervisor-pilot.sh all`,
with `CORE_VERSION=minimum` for the floor. `PILOT_BREAK=start|apparmor|dashboard` breaks a run on
purpose.

## Release / versioning

**Releases are batched and cut on request.**
A user-facing change adds its entry under `## Unreleased` in **`alpine_a290/CHANGELOG.md`** and
**leaves `alpine_a290/config.yaml` `version` alone**: the Supervisor keys updates on that version
and `release.yaml` publishes an image for any PR that moves it, so moving it is releasing. The
docs-sync check fails a PR that moves the version without being exactly a release, and no label
waives that.

Only when the maintainer asks for a release: `just release <version>` (read the entries it
prints, then choose the number: a new feature is a minor, otherwise a patch), commit the two
files it changes as their own PR titled `chore(release): <version>`, and let the existing gates
run. Merging that PR publishes nothing. `just tag <version>` first refuses unless the version order
is right, the release commit is on main, and every workflow on that exact merge commit has
passed; it then signs and pushes `v<version>`. That run scans each architecture image by digest
(HIGH/CRITICAL, failing the release), installs it under a real stable Supervisor on four legs,
waits for CI and the UI gate, attests the staged manifest, and only then publishes the version
tag users pull. Never put a version bump in a feature PR "to get it out". A tag run that fails after the per-arch push burns
that version, because a re-run re-scans the same bytes: fix the cause and cut the next patch. A
transient failure is recoverable — delete the `<arch>-<image>:<version>` and `staging-<version>`
image tags and re-run, which rebuilds and repushes. `.trivyignore` is read from the tagged commit,
so an exception has to land before the tag.

**What reaches users on merge, whatever the version.** Batching holds back the container image
only. The Supervisor reads the add-on's *files* straight from `main`, so a merge that changes
`config.yaml` (options, schema, `image`, `arch`, `slug`), `build.yaml`, `apparmor.txt`, `DOCS.md`
or `CHANGELOG.md` is visible to users immediately, while they still run the previous image. Such
a change must work with the currently released image. Adding an option the old image ignores is
fine. Removing or renaming one, changing `image`/`arch`/`slug`, or adding an `apparmor.txt` rule
the old image does not need, is not, and belongs in the release PR's company, never in an
ordinary merge. The docs-sync check refuses any `config.*` or `build.*` file other than
`alpine_a290/config.yaml`, because the Supervisor treats every one it finds as an add-on.

When mirroring to `r5-ha-addon`, the entry goes under `## Unreleased` in
**`renault_5/CHANGELOG.md`**. r5 is released separately, never ahead of this repo. r5 has no
version literal in code (`main.py` reads `R5_VERSION`, which its release workflow sets from
`config.yaml` via the Dockerfile's `BUILD_VERSION`). Feature branches are **squash-merged** to
`main` and deleted once merged.

## Gotchas

- **MQTT entity ids.** HA ignores the discovery `object_id`; the real `entity_id` is
  `slug(device name + " " + friendly name)`. Derive ids (for dashboards and tests) from the
  *names*, not from `object_id`.
- **Secrets never get logged.** The credentials (My Alpine username/password, VIN, account_id,
  GPS) are sensitive. `debug_dump: true` logs decoded API responses but masks them first (the core's
  `renault_mqtt.debug` key masking plus `renault_mqtt.config.redact`, also applied to every log
  line and traceback). Never add a logging path that bypasses it, and never lift the
  `renault_api` logger clamp in `main.py` (`setup_logging`). At DEBUG, renault-api (checked
  at 0.5.13) logs full, unredacted Kamereon request and response bodies
  (VIN, account ids, unrounded GPS) and
  the app's API-key config, but not access tokens, whose JWT is a header it never logs. The
  clamp keeps those records from ever being created; `debug_dump` is the redacted route.
- **Dashboards live in the add-on.** The old `a290-dashboard-view` repo is archived; all
  dashboard work happens in `alpine_a290/dashboards/`. Typography is intentionally uniform
  across tabs (no per-screen font/size changes). Overflow is handled by `white-space:normal`
  clean-word-break wrapping, not by shrinking text.
