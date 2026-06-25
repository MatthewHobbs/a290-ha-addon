# Changelog

## 1.3.0

- Remove the dead **Start Charging** tile from the standard dashboard — charge-start is
  forbidden on the A290, so the tile pointed at an entity that's never published.
- Add README badges (CI, version, license, Home Assistant add-on, architectures, and a
  one-click "add repository" button).
- Raise unit-test coverage from ~54% to **96%** (`poll_once`, command dispatch, endpoint
  detection, MQTT wiring, the health server, account resolution, the `main()` loop, and the
  deploy WebSocket client) and bump the CI coverage gate to **90%**.

## 1.2.3

- Complete the CI fix: the HA add-on linter flags the `watchdog` config option as obsolete,
  so liveness is now a native Dockerfile **HEALTHCHECK** hitting the same `/healthz` endpoint
  (the Supervisor marks the container unhealthy and restarts it if a deadlocked event loop
  stops answering). Same behaviour, modern mechanism.

## 1.2.2

- Fix CI (was red since the dashboard bundle / health-server changes): yamllint now ignores
  the bundled upstream dashboard YAML under `dashboards/`, and the watchdog health server's
  `0.0.0.0` bind is marked `# nosec B104` (intentional — it's on HA's internal network only,
  no exposed port). No runtime change.

## 1.2.1

- README: add an **Alpine A290 API support** table (which Renault endpoints work / are
  forbidden on the A290) and document the latest functionality.
- Trim verbose code comments/docstrings across `main.py`, `deploy.py`, `catalog.py`. No
  behaviour change.

## 1.2.0

- **`deploy_dashboard: both`** — deploy *both* dashboards in one go. The standard dashboard
  lands at `dashboard_url_path` and the bubble one at `<dashboard_url_path>-bubble`, each
  create-once. `none`/`standard`/`bubble` behave exactly as before.
- README rewritten for the merged repo: a proper **Requirements** section listing the
  add-ons and the exact HACS frontend cards to install **first** (card-mod + Mushroom on
  both, Button Card + Browser Mod for the standard dashboard's tiles/pop-ups, Bubble Card
  for the bubble dashboard), plus install steps and what the add-on provides.

## 1.1.0

- **The dashboard now ships inside the add-on** — the separate `a290-dashboard-view` repo is
  no longer a dependency. The two front-end YAMLs are bundled into the image and read
  locally at deploy time (no network fetch for the layout); their images load via jsDelivr
  CDN from this same repo. One project, one place to maintain. Deploy behaviour is otherwise
  unchanged (still create-once, still optional via `deploy_dashboard`). The old dashboard
  repo is archived.

## 1.0.0

First stable release. Functionally identical to 0.25.0 — the commit history was reset at
this point (an early commit had contained secrets), so 1.0.0 is the new baseline. All
features from the 0.x line are present; the entries below are retained as documentation.

## 0.25.0

- Add a **`debug_dump`** option (default off, ported from the R5 add-on). When enabled, each
  poll logs the decoded response of every readable API endpoint (`get_details`,
  `get_battery_status`, `get_charge_schedule`, …) to help diagnose what the A290 exposes —
  with your VIN, account id, username and identifier/contact fields (registration number,
  TCU code, email, name, phone, …) redacted. This is the *safe* diagnostic path: the
  `renault-api` library's own DEBUG logging leaks access tokens (which v0.23.0 clamps),
  whereas this dumps the data with secrets masked. Verbose — turn it off once captured.

## 0.24.0

Polish from the architecture review (P2 tier). No behaviour change for the happy path.

- **Crash-safe state writes:** `save_state` now writes to a temp file and `os.replace`s it,
  so a kill mid-write can't truncate `state.json` to garbage (which `load_state` would
  silently treat as empty, wiping all charge history). State is also persisted on the
  poll-failure path so plug baselines / session progress survive a restart.
- **Unify the plug-state representation:** plug-suspect detection now derives its int code
  from the already-decoded `PlugState` enum (single source of truth, JSON-safe) instead of
  separately re-reading the raw attribute — removing a latent enum-vs-int footgun.
- **Extract the entity catalog** (`SENSORS`/`BINARY_SENSORS`/`ICONS`/`OPTIONAL_ENDPOINTS`/
  `ACTION_BUTTONS`/`RETIRED_SENSORS`) into `catalog.py` — the declarative per-model surface,
  kept out of the polling/MQTT engine. No entity change.
- **CI supply chain:** SHA-pin every third-party GitHub Action (was tag-pinned); add a
  modest `--cov-fail-under=50` coverage gate.
- **Tests:** cover the `_on_message` command dispatch (known → routed, unknown → ignored)
  and the `detect_supported` startup-login-failure path (degrades to read-only, invalidates).

## 0.23.0

Hardening from the architecture review (P1 tier).

- **Bound login storms during outages.** A failed poll no longer re-authenticates every
  cycle — the cached login is dropped only on an auth-class error (or every 3rd consecutive
  failure, in case the session is wedged). Polls also back off exponentially while failing
  (capped at 30 min), so a Renault outage can't hammer Gigya at full cadence.
- **No token leak at debug.** `renault-api` logs full Kamereon request/response bodies
  (incl. the bearer JWT, VIN, GPS) at DEBUG. The library loggers are now clamped to an INFO
  floor, so enabling the add-on's `debug`/`trace` level can't write a live token to the log.
- **Pin the dashboard source to an immutable commit** instead of `@main`, so a compromised
  repo or poisoned CDN cache can't change the Lovelace config we push with the Supervisor
  token. Bump `DASHBOARD_REF` deliberately when the dashboard repo updates.
- **Watchdog.** A tiny `/healthz` server backs a Supervisor `watchdog`, so a deadlocked
  event loop now triggers an automatic container restart instead of going unnoticed.
- **Timeout on button-press commands** too (`run_command` uses the same 60 s API timeout).
- **Single-source the version** from `config.yaml` via the builder's `BUILD_VERSION` arg
  (exposed as `A290_VERSION`) — no more hand-syncing a constant in `main.py`.
- **Tests:** add `test_deploy.py` covering the CDN rewrite and the create-once/redeploy
  branches of the dashboard deployer.

## 0.22.0

Reliability fixes from the architecture review (P0 tier).

- **Time-bound every Renault API call.** `aiohttp`'s default is no timeout, so a hung
  Kamereon socket could stall the single poll loop forever (the add-on stays "online" but
  publishes nothing). The cached session now uses `ClientTimeout(total=60, connect=10)`,
  and each poll is additionally wrapped in `asyncio.wait_for(interval-10)` so a cycle can
  never outlast its interval — a hang now surfaces as a normal failed poll (`data_stale`).
- **Recover MQTT after a broker restart.** Added an `on_connect` handler that re-subscribes
  to the command topic and re-publishes discovery + availability on every (re)connect. paho
  doesn't replay subscriptions and a restarted Mosquitto can drop retained discovery, so
  previously a broker bounce silently killed the control buttons and could blank entities.
- **Test:** assert the discovery `value_template` prefix-strip contract for `BINARY_SENSORS`
  too (was only checked for `SENSORS` — the same bug class that once broke Last Charge).

## 0.21.0

- Reuse one logged-in API session across polls instead of re-authenticating every cycle.
  The poller previously did a full Gigya login on **every** poll (~288 logins/day at the
  default 300 s interval, plus one per endpoint-support probe at startup), which risks
  Renault-side throttling. A new `VehicleSession` logs in once and reuses the websession +
  vehicle; `renault-api` refreshes its own access tokens as they expire. After any failed
  poll the cached login is dropped (`invalidate()`) so the next cycle re-authenticates —
  recovering from an expired token or a dropped socket. Button presses keep their own
  short-lived login, so there's no shared-session concurrency. No entity or config change.

## 0.20.2

- Pin `paho-mqtt==2.1.0` and `PyYAML==6.0.3` (were `>=`), completing the reproducible-build
  goal from 0.17.0 — an unpinned rebuild could otherwise pull a new major silently.
- Add a `pytest` unit-test suite (`alpine_a290/tests/`) and a CI **Tests** job. Covers the
  discovery-template/data-key contract (the class of bug that broke the Last Charge tiles),
  charge-session maths, plug stuck-detection, enum decoding, unit conversion, and the
  action-button/command-map consistency. No runtime change.

## 0.20.1

- Change the default `precondition_temperature` from 21 to 20 °C. Existing installs that
  already set the option keep their value; only the unset default changes.

## 0.20.0

- Add control buttons for every A290-supported action endpoint, each gated on
  `supports_endpoint()` (auto-hidden + retained config cleared if the platform forbids it):
  - **Sound Horn** (`mdi:bullhorn`) → `start_horn()`
  - **Flash Lights** (`mdi:car-light-high`) → `start_lights()`
  - **Start Climate** (`mdi:air-conditioner`) → `set_ac_start(precondition_temperature)`
  - **Stop Climate** (`mdi:fan-off`) → `set_ac_stop()`
  - **Refresh Location** (`mdi:crosshairs-gps`) → `refresh_location()` — note: this endpoint
    isn't explicitly mapped for the A290 and falls back to the library default, so it may
    return *forbidden* on press; it's harmless (read-only) if it does.
- New `precondition_temperature` add-on option (16–27 °C, default 21) — the target cabin
  temperature used by **Start Climate**.
- MQTT command handling is now generic: a single `alpine_a290/cmd/#` subscription dispatches
  each button press to its `renault-api` action via `COMMAND_ACTIONS`. The charge-start
  button stays gated/cleared (still forbidden on the A290).

## 0.19.0

- Remove the **Start Charging** button on the A290. Remote charge-start is forbidden at
  the Renault API level for this model (`A5E1AE`): every route `renault-api` offers —
  `actions/charge-start`, the KCM `charge/start` and `charge/pause-resume` endpoints, and
  the R5's `ev/settings` disable-programs trick — returns "access is forbidden" (verified
  against `renault-api==0.5.12` and the library's current `main`). The button was shipped
  unconditionally but `set_charge_start()` raised `EndpointNotAvailableError` before any
  request, so pressing it silently did nothing. It's now gated on
  `supports_endpoint("actions/charge-start")`: not published on the A290 (and any retained
  button from an older install is cleared), and it would reappear automatically if Renault
  ever lifts the restriction. The R5 E-Tech, which *does* allow start via `ev/settings`,
  keeps its button in the R5 port.
- Audited every other `renault-api` call against the A290's per-model endpoint map: all
  read endpoints resolve to supported endpoints, and the two forbidden ones (`pressure` /
  TPMS and `charge-mode`) remain correctly gated, so no perpetually-empty entities ship.

## 0.18.0

- Fix the 11 **Last Charge** sensors, which never populated (always unavailable). MQTT
  discovery builds each sensor's `value_template` as `value_json.<object_id minus the
  "a290_" prefix>` (e.g. `value_json.last_charge_start`), but the charge-session dict was
  written with the *prefixed* keys (`a290_last_charge_start`, …) — so the published JSON
  key never matched the template and every Last Charge tile read unavailable. The dict
  now uses the unprefixed keys (`last_charge_start`, `last_charge_end`, `…_start_soc`,
  `…_end_soc`, `…_start_energy`, `…_end_energy`, `…_recovered_pct`, `…_recovered_kwh`,
  `…_duration_min`, `…_average_power`, `…_type`), consistent with the main data dict.
  On an existing install the tiles populate after the next charge session completes (a
  persisted pre-0.18.0 `last_charge` in `/data/state.json` keeps the old keys until then).

## 0.17.0

- Pin `renault-api==0.5.12` for reproducible builds. The library's per-model endpoint
  maps and method behaviour change between releases, so an unpinned `>=` could silently
  alter behaviour on a rebuild. Bumps are now deliberate and re-verified.

## 0.16.0

- Single source of truth for "charging": `is_charging()` is computed once per poll and
  passed into charge-session tracking, and the **Charging Status** text now reads
  "Charging" whenever that's true (incl. the power-based fallback) — so it can no longer
  contradict the `binary_sensor.alpine_a290_charging` state. Precise sub-states
  (Waiting/Charge Ended/Flap Open/…) still show when not actively charging.
- Minor: decode the plug state once per poll (was decoded twice — label + charging flap).

## 0.15.0

- **Charging Status** now decodes the full `ChargeState` enum via the library's
  `battery.get_charging_status()`. Previously only `0.0`/`1.0`/`-1.0` were mapped, so
  real sub-states (e.g. `0.2` charge-ended, `0.4` energy-flap-opened) leaked to the tile
  as raw floats. Existing labels ("Charging"/"Not Charging"/"Error") are unchanged, so
  the dashboards still match.
- **Plug Status** uses `battery.get_plug_status()`; the `PLUG_UNKNOWN` sentinel
  (`-2147483648`) now reads "Unknown" instead of the raw number.

## 0.14.0

- Removed the **Cabin Temperature** sensor (`sensor.alpine_a290_cabin_temperature`).
  The renault-api HVAC endpoint never returns `internalTemperature` for the A290, so
  the entity was perpetually unavailable. Its retained MQTT discovery config is now
  cleared on startup, so upgraded installs drop the dead entity automatically. Outside
  Temperature and HVAC status are unaffected.

## 0.13.0

- Reverted the 0.12 attempt to set the map marker via the device_tracker's MQTT
  `entity_picture` — Home Assistant **blocks** `entity_picture`/`icon` from MQTT
  attributes, so it never applied. The car-coloured marker is instead set in the
  dashboard via a `homeassistant: customize:` entity_picture (see the dashboard repo).

## 0.12.0

- (No-op, superseded by 0.13.0) Attempted to publish the map marker as the
  device_tracker `entity_picture`; HA strips that attribute over MQTT.

## 0.11.0

- Gave the text/status sensors proper icons in MQTT discovery (plug status,
  charging status, charging flap, HVAC status, drive side, last-charge type, heated
  seats/steering wheel). They previously fell back to HA's generic `mdi:eye` on cards
  that don't set their own icon.

## 0.10.0

- **Optional dashboard auto-deploy.** Set `deploy_dashboard` to `standard` or `bubble`
  and the add-on fetches that dashboard, rewrites its images to the **jsDelivr CDN**
  (nothing to copy into `/config/www`), registers the **Zen Dots** Google font, and
  creates the dashboard via Home Assistant's API — no raw-editor paste, no
  `configuration.yaml` edits. Create-once (your edits are never overwritten); set
  `redeploy_dashboard` to refresh it after an update. Needs `homeassistant_api: true`
  (already enabled). The HACS frontend cards still install via HACS; pretty-location
  and test-mode remain an optional package.

## 0.9.0

- Added entities so the dashboard can repoint fully onto the add-on (no template layer):
  - `charging_flap` (Open: Plugged In / Closed), derived from plug status.
  - `last_charge_start_energy` / `last_charge_end_energy` (kWh), from start/end SoC × capacity.

## 0.8.0

- Distance is now locale-aware: range + mileage are shown in **miles for the UK**
  (en_GB) and **km everywhere else** (incl. Ireland), with the matching unit. Replaces
  the separate _mi entities. (Plug stuck-detection still computes in km internally.)

## 0.7.0

- Added ev/settings-derived entities: preconditioning temperature, heated steering
  wheel, and heated seats (driver/passenger, resolved from `drive_side`).
- Added miles variants (`range_mi`, `mileage_mi`) and activity timestamps
  (`hvac_last_activity`, `gps_last_activity`). Part of full dashboard coverage.

## 0.6.4

- Unsupported endpoints' discovery is now actively cleared on startup, so entities
  published by an earlier version (e.g. charge_mode / tyre pressure on A5E1AE) are
  removed rather than lingering as empty.
- Version is logged from a single source; tidied the discovery log count.

## 0.6.3

- `battery_capacity_kwh` is now a dropdown (52 or 40 kWh) instead of free entry.
  Capacity must be configured because the API's `batteryCapacity` is always 0 on
  these cars; it's used to derive charge-session energy.

## 0.6.2

- Added `sensor.alpine_a290_drive_side` (RHD/LHD), derived from the locale
  (en_GB / en_IE = RHD, otherwise LHD). The dashboard uses this to map the API's
  left/right seats to driver/passenger automatically — no more manual LHD/RHD editing.

## 0.6.1

- `locale` is now a dropdown of the 29 supported MyRenault/MyAlpine locales
  (bg_BG … sv_SE) instead of free text.
- Removed the redundant `country` option — the full locale (e.g. `en_GB`) already
  determines the region/country, so it was unused.

## 0.6.0

- Added Alpine branding (add-on logo + icon).

- Optional endpoints are now gated on `vehicle.supports_endpoint()`: on cars that
  don't expose `charge-mode` or `pressure` (e.g. the A290 / model A5E1AE), those
  entities are no longer published, so there are no perpetually-empty sensors.

## 0.5.0

- **Charge control:** a "Start Charging" button (`button.a290_charge_start`). On these
  cars charge-start goes via the KCM `ev/settings` route (the library's
  `set_charge_start()` disables all schedule programs to force instant charge); the
  legacy KCA action 403s, so it is not used. STOP is not exposed by the platform, so no
  stop button is provided.
- **Plug stuck-detection:** `binary_sensor.a290_plug_suspect` — flags
  Connected-but-driven (mileage/SoC moved since the plug baseline) and
  Disconnected-but-charging, mirroring the original template heuristic.

## 0.4.0

- Full CMF-BEV read endpoint coverage. Added:
  - KCM `ev/soc-level` → Charge Target SoC + Minimum SoC.
  - HVAC → cabin (internal) temperature and SoC threshold (was: outside temp only).
  - Tyre pressure (TPMS) for all four corners.
  - Charge mode.
- `chargingStatus` mapped to a readable value (Charging / Not Charging / Error).
- Platform caveats handled: `batteryCapacity` (always 0) ignored in favour of the
  configured `battery_capacity_kwh`; absent `batteryTemperature` tolerated.
- Note: charge **control** is not wired yet — the legacy KCA charge actions return 403
  on R5 E-Tech / A290; control must use the KCM `ev/settings` POST trick. Future feature.

## 0.3.0

- **Resilient charge-session tracking** (persisted to `/data`, survives restarts):
  detects charge start/end and records last charge start/end times, start/end SoC,
  SoC + energy recovered, duration, and average power; classifies Home vs Rapid/Public.
- **Health binary sensors:** `binary_sensor.a290_charging`, `…_api_auth_failure`,
  `…_data_stale` — derived from the add-on's own poll success (no CLI-output parsing).
- New options: `battery_capacity_kwh` (default 52) and `stale_hours` (default 6).

## 0.2.0

- Added endpoints: HVAC status (outside temperature, HVAC state) and location.
- Added a `device_tracker` (GPS) for the car's position — drives the map card.
- More battery fields: available energy.
- **Account-id auto-discovery** — leave `account_id` blank and the add-on finds your
  MyRenault/Kamereon account automatically.
- Fresh login each poll cycle (re-auth resilience); any single endpoint failing no
  longer stops the others.
- Entities published with stable `sensor.a290_*` / `device_tracker.a290_car_location`
  ids for the dashboard to bind to.

## 0.1.0

- Initial release (data layer).
- Logs in to the Renault/Kamereon API via `renault-api` using credentials from the
  add-on Configuration page.
- Polls battery status and cockpit; publishes core sensors (battery level, range,
  battery temperature, charging power, charging time remaining, plug status,
  charging status, mileage, last updated) via MQTT auto-discovery.
- MQTT broker connection auto-discovered from the Mosquitto add-on.
- Keeps running through API/auth hiccups and marks itself unavailable on failure.

### Planned

- Remaining endpoints (location, HVAC, charges history, EV settings, SoC levels).
- Port the resilient charge-session tracking, plug/stuck detection and auto-reauth.
- Kamereon account-id auto-discovery.
