# ADR 0001 — What this add-on is, why, and how to work on it

- **Repo:** a290-ha-addon
- **Status:** Accepted (2026-10-25), replacing the earlier records. Retrospective: the rows
  describe the tree at `297b6b1`, with rows 4, 8, 10 and 15 updated on 2026-10-27. Rows 22, 23 and 25 also record policy the code cannot state —
  which checks are required, and that a release is cut on request from a tag I sign. **Working
  in this repository** carries the operating context, so this file alone is enough to start.
- **Context:** this repository is a Home Assistant add-on for the Alpine A290. It polls the
  Renault Kamereon API for one car and publishes what it finds to Home Assistant over MQTT.
  Others install it, so what it does with a credential, a location or a failure matters more
  than how much it exposes. A sibling repo, `r5-ha-addon`, is the Renault 5 port of the same
  code.
- **North star:** the add-on tells the truth about the car, or says it does not know. It never
  invents a fresh reading, never ships a control the car refuses, never lets a secret or a
  precise location leave the machine unmasked, and never installs a release that was not booted
  first.

## Decision

One add-on, one car, one poll loop, published as discovered MQTT entities, with every claim
about the car gated on what the car says it supports and every failure shown as a failure. The
runtime is small and its checks are many, because it runs unattended in other people's homes.

The table is the record, and it is kept current rather than appended to: a row says what is true
now. When a decision reverses, the row is rewritten and re-dated, and the change is explained in
the changelog like any other.

| # | Decision | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | The add-on is an MQTT publisher, not an integration. It polls on an asyncio loop and publishes retained state to one topic, with Home Assistant discovery for the entities | a290 | Done | `renault_mqtt.runtime` (`run_main`); `alpine_a290/app/main.py` (`main`, `poll_once`); `services: mqtt:need` in `config.yaml`; broker details come from the Supervisor in `run.sh` |
| 2 | Entities are declared as tables, per model. Names, units, device classes, icons, optional endpoints and controls live in `catalog.py`; the object-id prefix and environment prefix are defined there and nowhere else, so the Renault 5 port changes the catalogue and not the loop | a290 | Done | `alpine_a290/app/catalog.py` (`OBJ_PREFIX`, `ENV_PREFIX`, `NODE`, `SENSORS`, `BINARY_SENSORS`, `NUMBERS`, `ACTION_BUTTONS`); `main.py` hands it to `mqtt.configure(catalog)` |
| 3 | The poll/publish plumbing is a shared core, pinned to a commit. `renault_mqtt` (MQTT seam, charge-session reconciliation, parsing, redaction, debug dump) is installed from `MatthewHobbs/renault-mqtt` at `CORE_REF`, not vendored, and the add-on injects its model's prefix into it | a290 | Done | `Dockerfile` (`CORE_REF`, the `pip install … renault-mqtt @ git+…`); `main.py` (`config.ENV_PREFIX = ENV_PREFIX`) |
| 4 | Capability checks distinguish unknown from unsupported. Core resolves pending probes incrementally; the app owns endpoints, defaults, acquisition, invalidation and logging. Failed probes alone are retried after successful polls, within a timeout, updating discovery and reconnect/panel support. Definite false is not retried and errors preserve prior positives. Unknown data defaults to supported, except `hvac-settings`; unknown controls stay hidden | a290 | Done | `renault_mqtt.support` (`resolve_support`); `main.py` (`detect_supported`, `_supports`, `SUPPORT_ENDPOINTS`, `main`); `catalog.py` (`OPTIONAL_ENDPOINTS`, `PESSIMISTIC_ENDPOINTS`, `DATA_GATED_SENSORS`) |
| 5 | A failing optional endpoint is paused, not retried every poll. Core owns the `hvac-settings` advertisement gate, trip counter, retry cadence and recovery reset; the app owns thresholds, breaker state and bounded logs. After three consecutive failures it is switched off, probed again every twelve polls, and re-enabled if it recovers | a290 | Done | `renault_mqtt.hvac` (`hvac_due`, `core.poll_hvac_settings`); `renault_mqtt.poll` (`poll_vehicle`); `main.py` (`BREAKER_TRIP`, `BREAKER_RETRY_EVERY`, `_hvac_due`, `poll_once`) |
| 6 | Two freshness questions are two entities. `data_stale` asks whether the car has reported within `stale_hours`, from the battery-status timestamp; `poll_failing` asks whether this add-on can reach Kamereon. A successful poll never asserts the car's data is fresh, and the car's own timestamp is never replaced by ours | a290 | Done | `renault_mqtt.poll` (`poll_vehicle`); `main.py` (`freshness_fields` imported from `renault_mqtt.freshness`, `poll_once`); `catalog.py` (`a290_last_updated`, `a290_last_successful_poll`, `a290_data_stale`, `a290_poll_failing`) |
| 7 | An unknown value stays unknown. A location the API marks unusable (sentinel or out-of-range coordinates) is rejected and the last good fix is carried forward, so the stale-location guard stays armed. A key with no value is omitted and renders as `unknown` instead of a made-up `off` | a290 | Done | `renault_mqtt.poll` (`poll_vehicle`); `main.py` (`poll_once`, `freshness_fields`) |
| 8 | Controls are conservative. Core owns per-command debounce, in-flight cancellation cleanup, climate/charge-limit lock separation, preconditioning temperature fallback and SoC slider validation; the app supplies short-lived bounded sessions, login, command actions and slider tables. Climate start/stop share a lock through login and action; charge-limit writes use a separate lock, reading the opposing slider first. Other controls remain independent. Cancellation releases the lock; only a dispatched command keeps its debounce stamp | a290 | Done | `renault_mqtt.commands` (`COMMAND_DEBOUNCE_S`, `command_lock_get`, `dispatch_command`, `hvac_start_action`, `precondition_temp`, `set_soc_level`); `main.py` (`run_command`, `COMMAND_DEBOUNCE_S`, `set_soc_level`, `_command_lock_get`, `_hvac_start_action`, `_precondition_temp`); `catalog.py` (`NUMBERS`: minimum 15 to 45, target 55 to 100, step 5) |
| 9 | A control that can hurt is off until asked for. Refresh Location withholds itself unless `enable_refresh_location` is set, because on a parked car it replaces the recorded position with no fix. Start Charging is published but documented as a no-op under external scheduling | a290 | Done | `config.yaml` (`enable_refresh_location: false`); `main.py` (`run_command`, the module docstring); `catalog.py` (`ACTION_BUTTONS`) |
| 10 | The add-on logs in once and keeps the session. Core owns the single-poll-owner lifecycle, account resolution, auth-failure classification and backoff loop; the app supplies transport, login, locale, exception types and logging. A rejected or expired login, recognised by exception type, drops it immediately and turns on API Auth Failure; other errors drop it every third failure. Polls are bounded by a timeout under the interval; failures back off exponentially up to the larger of the interval and thirty minutes | a290 | Done | `renault_mqtt.session` (`VehicleSession`); `renault_mqtt.runtime` (`resolve_account`, `core.is_auth_failure`, `run_main`); `main.py` (`VehicleSession`, `_login_vehicle`, `resolve_account`, `API_TIMEOUT`, `_AUTH_EXCEPTIONS`, `main`); `catalog.py` (`a290_api_auth_failure`) |
| 11 | A secret never reaches a log. Records are scrubbed by a redaction filter on every handler; the `renault_api` loggers are held at INFO, because at DEBUG the library logs whole request and response bodies; `debug_dump` is the one route for decoded responses and goes through redaction; dashboard-deploy errors are masked as well | a290 | Done | `main.py` (`setup_logging`); `deploy.py` (`_redact`); `core/config.py` (`core._RedactingFilter`, `core.redact`), reached through `main.py` |
| 12 | Location is opt-out and coarsened. `publish_location` can disable it; the published position is rounded to `gps_precision` places (default 4, about 11 m) before it reaches the retained topic; the tracker has attributes and no state topic, so Home Assistant resolves home and zones from the coordinates | a290 | Done | `config.yaml` (`publish_location`, `gps_precision`); `renault_mqtt.runtime` (`run_main`); `main.py` (`GPS_PRECISION`, `main`) |
| 13 | State that must survive a restart is a small file. Charge-session reconciliation, the plug baseline, the last good GPS time and the last car timestamp are kept in `/data/state.json`, written after every poll. Core owns plug baseline/evidence logic; the app owns Renault enum interpretation, clock, thresholds and persistence | a290 | Done | `renault_mqtt.plug` (`detect_plug_suspect`); `main.py` (`STATE_FILE`, `load_state`, `save_state`, `detect_plug_suspect`) |
| 14 | An entity that is retired is cleaned up, and one that adds noise is hidden. Discovery clears `RETIRED_SENSORS` on every publish so old installs lose them; `drive_side` is published disabled by default | a290 | Done | `catalog.py` (`RETIRED_SENSORS`, `DEFAULT_DISABLED_SENSORS`) |
| 15 | `/healthz` backs the image `HEALTHCHECK`; a read-only ingress panel and retained MQTT share the same vehicle snapshot. Failed polls keep this process's last good readings and original timestamps with updated diagnostics; recovery replaces them. No readings are invented before success. The panel excludes credentials and raw coordinates; the event loop serves both health and panel | a290 | Done | `renault_mqtt.runtime` (`HEALTH_PORT`, `panel_page`, `panel_state`, `start_health_server`, `run_main`); `main.py` (`main`, `start_health_server`, `_panel_page`, `_panel_state`, `_LATEST`); `Dockerfile` (`HEALTHCHECK`); `config.yaml` (`ingress: true`); `app/panel.html` |
| 16 | A dashboard is offered, never imposed. On request the add-on writes a Lovelace dashboard (standard, Bubble Card, or both) through the Supervisor's Core WebSocket; it creates once and leaves an existing dashboard alone unless `redeploy_dashboard` is set; it can never stop the poller; images are served from jsDelivr pinned to the release tag. The dashboards are text files shipped in the image | a290 | Done | `deploy.py` (`run_deploy`, `_deploy_one`, `CDN`, `_REF`); `config.yaml` (`deploy_dashboard`, `dashboard_url_path`, `redeploy_dashboard`, `homeassistant_api: true`); `alpine_a290/dashboards/` |
| 17 | Octopus Intelligent and similar smart charging are mapped, not reimplemented. Five optional `charger_*` options name existing Home Assistant entities, and a blank slot is skipped; the add-on adds a charging pop-up and cards that point at them | a290 | Done | `deploy.py` (`_CHARGER_ENTITIES`, `_charger_cards`, `_charger_popup`, `_inject_bubble_charging`); `config.yaml` (`charger_*`) |
| 18 | The image is small, pinned and confined. The base is the Home Assistant base image by digest; Python dependencies are hash-locked and installed with `--require-hashes`; `tempio` is removed because scanners flag it and nothing uses it; an AppArmor profile denies mount, umount, pivot_root and ptrace and grants no raw packet socket; the health bind carries a justified `nosec` | a290 | Done | `Dockerfile`; `app/requirements.in` and `requirements.txt`; `apparmor.txt`; `main.py` (`start_health_server`) |
| 19 | The Renault library is ranged and its contract is tested. `renault-api` is `>=0.6.1,<0.7` in the source, exactly locked in `requirements.txt`, and a test fails unless the lock satisfies the source. It was pinned only while its releases were wrong about this car; with the fixes upstream it takes an ordinary Dependabot PR. A test loads representative Kamereon payloads through the library's own schemas and asserts every field and helper the poller reads | a290 | Done | `alpine_a290/app/requirements.in`; `alpine_a290/tests/test_api_contract.py` (`test_requirements_in_specifiers_are_satisfied_by_the_lock`) |
| 20 | Every pull request runs the same gates locally and remotely. Lint (ruff, yamllint, hadolint, shellcheck), the unit suite with coverage reported rather than gated by a percentage, Bandit, pip-audit, Trivy, a repo check that refuses committed VINs, coordinates, personal emails and home paths, a 50-word cap on comment blocks, and a check that the declared minimum Home Assistant version is real and allowed. `just ci` runs the local subset | a290 | Done | `.github/workflows/ci.yaml`; `justfile`; `scripts/pii_check.py`, `scripts/check_comment_length.py`, `scripts/ha_minimum_check.py` |
| 21 | Documentation is held to the code by a check. A change to the options or the published entities fails unless the options table or the entity lists change with it, a change to the version fails unless it is exactly a release, and every citation in this table is checked against the tree | a290 | Done | `scripts/docs_sync_check.py`, `scripts/adr_evidence_check.py`; the `Docs sync` job in `ci.yaml` |
| 22 | The dashboards are rendered in a real Home Assistant on seven phone sizes, and a truncated label or an error card fails the run. Its inputs (the Home Assistant image and four custom-card bundles) are pinned and bumped together, and it always reports so it can be required | a290 | Done | `ui-tests/run.sh`, `seed.py`, `check_overflow.py`, `devices.json`; `.github/workflows/ui-tests.yaml` |
| 23 | The add-on is installed under a real stable Supervisor before it ships. A pilot builds or pulls the image, installs it from a local registry, and checks the versions, the image's provenance, `/healthz`, the retained discovery and availability topics, that Renault was tried and refused, the dashboard in Core, and AppArmor enforcement. It runs on both architectures at current stable and at the declared minimum Core, and the network guard means Renault is never reached | a290 | Done | `scripts/supervisor-pilot.sh`; `.github/workflows/supervisor.yml`; the `pilot` job in `release.yaml` |
| 24 | The twin is derived and checked. `r5-ha-addon` is held to this repo by a script that rewrites its names onto this repo's, diffs the trees and fails on any difference that is not listed with a category, an exact count and a reason | a290 | Done | `scripts/parity_check.py`; `scripts/parity/map.tsv`, `expected.tsv`; the `parity` job in `ci.yaml` |
| 25 | A release is requested, then published from a tag I sign. Ordinary changes add a changelog entry under `## Unreleased` and leave `version` alone; a release PR changes only `version` and the heading; I then push a signed `v<version>` tag, and the tag run checks it (annotated, verified, on `main`, matches `config.yaml`, above every earlier tag), pushes the per-arch images, boots them under the Supervisor, waits for CI and the UI gate, publishes the manifest, attests its provenance and creates the Release | a290 | Done | `scripts/prepare_release.py`; `scripts/check-version-order.sh`; the `justfile` `release` and `tag` recipes; `.github/workflows/release.yaml`. The tag run itself has not yet been exercised on a real release |
| 26 | Dependabot is the only dependency bot. It covers the runtime Python, the Actions and the Dockerfile's images; what no manifest describes (the core commit, the UI gate's inputs, the pilot's digests, inline tool pins) is bumped by hand | a290 | Done | `.github/dependabot.yml` |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context

The A290 is one of two Renault-group cars reachable through the same Kamereon API, and the
library that talks to it, `renault-api`, keeps a hard-coded map of which endpoints each model
exposes. That map, not the library's documentation, decides what this car can do, which is why
rows 4 and 19 treat support as something to probe and pin and not something to assume. The A290
refuses several endpoints, accepts others only under conditions (Start Charging acts by clearing
the car's own timer, so it does nothing for someone whose charging is scheduled elsewhere), and
sometimes answers a location request with a value that means no fix. Most of the rows above
exist because of one of those three facts.

The add-on runs as root inside a container it does not control, holds a credential to a car and,
if allowed, that car's location. It also shares a repository that is public. That is why rows
11, 12, 18 and 20 are as strict as they are.

## Working in this repository

Enough to start without reading anything else. Where this section and the code disagree, the
code is right and this section is the bug.

**Where things are.** `alpine_a290/app/main.py` is the poll loop, MQTT discovery, the controls
and the health server; `catalog.py` holds the entity tables, endpoint constants and the retired
lists; `deploy.py` writes the dashboards; `core/config.py` is the pinned core's own. Tests sit
in `alpine_a290/tests/`, dashboards in `alpine_a290/dashboards/`, the render gate in
`ui-tests/`, the pilot in `scripts/supervisor-pilot.sh`. `alpine_a290/config.yaml` is the
manifest the Supervisor reads.

**Checks.** `just` is the entry point, and its recipes mirror the CI steps where they can. `just
venv` builds the virtualenv the test recipe uses — a stale one fails two MQTT tests that look
like code faults. `just ci` runs ruff, the comment cap, the decision-record citations, the PII
gate, the HA-floor bound, both self-test suites and the tests with coverage reporting. Test
failures block the change; a coverage percentage does not. What it
cannot run is remote only: Hadolint, the app linter, Bandit, pip-audit, Trivy, the multi-arch
build with a boot smoke, the render gate on seven phone sizes, and the Supervisor pilot. A green
`just ci` is therefore not a green PR: run `ui-tests/run.sh` for a dashboard change, and treat a
runtime change as needing the pilot.

**Dependencies.** Everything is pinned, Python with hashes. `renault-api` is the one dependency
that changes behaviour rather than hygiene: the per-model endpoint map inside it decides what
this car exposes, so a bump is reviewed against `tests/test_api_contract.py`, never waved
through. Dependabot is the only bot. The core (`renault-mqtt`) is a commit, `CORE_REF`, in
`alpine_a290/Dockerfile`; nothing bumps it automatically, and a fix there does not arrive until
that line moves.

**The pair.** `r5-ha-addon` mirrors this repo, and this repo leads. A change that is not
per-model lands here first with a `pending` row in `scripts/parity/expected.tsv` naming r5 as
the Target, then in r5, then a recount here. `scripts/parity_check.py` earns its keep by failing
three ways: an unlisted difference, a listed one that has gone away, and a count that no longer
matches. Never change a count without reading the lines the run printed. The four check scripts
are shared byte for byte, so editing one fails the twin's job until both copies agree;
`map.tsv`'s `mask` kind hides a version pin on both sides so a bump does not read as drift, and
a mask that stops matching is the signal a pin has moved.

**Releasing.** A change adds its entry under `## Unreleased` and leaves `version` alone. On
request, `just release <version>` turns that heading into the version and moves the manifest;
that PR is merged, then `just tag <version>` signs `v<version>` on the merge commit. The tag run
does the rest: it checks the tag, pushes the images, boots them under a stable Supervisor, waits
for CI and the render gate, and only then publishes the manifest and the Release. Tags are
immutable and signing is on by default, so `git tag` needs `-m` or it hangs, and a tag pushed
early burns the version.

**Traps, in the order they have cost time.** Home Assistant derives an entity's id from the
device and friendly names, not from the discovery `object_id`, so tests and dashboards must
derive ids the same way. `debug_dump` is the only route to decoded API responses and it goes
through redaction; never add a logging path that bypasses `_debug_redact` or lifts the
`renault_api` clamp, because the library logs whole bodies at DEBUG. Dashboard text wraps, it
never shrinks, and the injected style must cover `.content p` and `.content h2`. An absent
option is not a default: state it. Comment blocks stay under 50 words, and docstrings are
exempt. The PII gate pairs a latitude with a longitude across lines, which comments may separate
but blank lines may not. The docs-sync and record checks are diff-based or citation-based, so
they catch what they are pointed at and nothing else.

**Where the local state lives.** `HANDOVER.md`, `AGENTS.md` and `CLAUDE.md` are git-excluded, so
they are not here: the queue, the operator's notes and the long reference respectively.
`BACKLOG.md` is tracked and public, so it carries open work only. `docs/adr/` holds this record
and its index. The Supervisor reads this repo's files straight from `main`, so a merge that
changes `config.yaml`, `DOCS.md`, `apparmor.txt` or the changelog reaches users while they still
run the previous image.

## Consequences

- The twin has not taken the release flow of row 25: it still publishes from the bump PR, and
  its parity list carries that as `pending`.
- The runtime stays small on purpose. A new reading is a catalogue row and a poll line; a new
  control is a catalogue row and a gated command. Anything that needs a decision about the car's
  behaviour belongs here as a row first.
- The shared core is a pinned commit of another repository. A fix there does not reach this
  add-on until `CORE_REF` moves, and nothing bumps it automatically (row 26).
- Everything in the table is checked by something that can fail, except the tag run in row 25,
  which has not yet run on a real release.
- **Not established:** how this add-on behaves on a real A290 for the endpoints I have not seen
  refuse or accept, beyond what the tests and the changelog record; that the Supervisor on a
  user's host acts on the image `HEALTHCHECK`; and any behaviour of the shared core that the
  add-on's own tests do not exercise.
