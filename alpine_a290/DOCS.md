# Alpine A290 add-on

Polls your Alpine A290 through the Renault/Kamereon API and publishes the data to
Home Assistant via MQTT auto-discovery.

## Before you start — install these first

The add-on itself only needs the **Mosquitto broker** add-on (its MQTT connection is
auto-discovered). But if you use one of the **bundled dashboards** (`deploy_dashboard`),
you must **first install the frontend cards via HACS** — otherwise the dashboard renders
as *"Custom element doesn't exist"* with broken tiles:

| Install via HACS → Frontend | Needed for |
| --- | --- |
| **card-mod** + **Mushroom** | both dashboards |
| **Button Card** + **Browser Mod** | the **standard** dashboard (tiles + pop-ups) |
| **Bubble Card** | the **bubble** dashboard only |

Install Mosquitto and the cards above **before** enabling `deploy_dashboard`, so the
dashboard renders correctly the first time. (The car's location uses Home Assistant's
built-in `map` card — no map plugin or API key needed.)

## Configuration

| Option | Description |
| --- | --- |
| `username` | Your My Alpine app email. |
| `password` | Your My Alpine app password. |
| `account_id` | Your Kamereon account id. **Optional** — leave blank to auto-discover it. |
| `vin` | Your vehicle VIN (uppercase). |
| `locale` | Pick from the dropdown (e.g. `en_GB`, `fr_FR`, `de_DE`). This sets the API region **and** the drive side — `en_GB`/`en_IE` ⇒ RHD, otherwise LHD (used for seat mapping). The country is derived from this, so there is no separate country option. |
| `poll_interval` | Seconds between polls (60–3600, default 300). |
| `battery_capacity_kwh` | `52` or `40`. Must be set — the API reports capacity as 0; used to derive charge-session energy. |
| `stale_hours` | Mark data stale after this many hours without a successful poll (default 6). |
| `precondition_temperature` | Target cabin temperature (°C, 16–27, default 20) used by the **Start Climate** button. |
| `log_level` | `info` normally; `debug` for troubleshooting. |
| `debug_dump` | `false` by default. When `true`, every poll logs the decoded data from all readable API endpoints — with your VIN, account id, username and contact/identifier fields redacted — to help diagnose what your car does/doesn't expose. Turn off again once captured (it's verbose). Prefer this over `log_level: debug` for API diagnostics: the library's own debug logging would expose access tokens. |
| `deploy_dashboard` | `none` (default), `standard`, `bubble`, or `both`. Auto-installs that dashboard — see below. `both` installs the standard dashboard at your `dashboard_url_path` and the bubble one with a `-bubble` suffix (e.g. `alpine-a290` and `alpine-a290-bubble`). |
| `dashboard_url_path` | URL slug for the deployed dashboard (default `alpine-a290`; with `both`, the bubble one is suffixed `-bubble`). |
| `redeploy_dashboard` | `true` re-pushes the dashboard config on next start (to pick up an update). Default `false` so your edits are never overwritten. |

## Dashboard auto-deploy (optional)

Set `deploy_dashboard` to `standard`, `bubble`, or `both` and the add-on will install the
dashboard(s) for you — **no raw-editor paste, no `configuration.yaml` edits, and nothing
to copy into `/config/www`**. On start it:

1. Reads the chosen dashboard YAML **bundled in the add-on** and rewrites its image
   references to the **jsDelivr CDN** (served from this add-on's own repo).
2. Registers the **Zen Dots** Google font as a Lovelace resource.
3. Creates the dashboard (at `dashboard_url_path`) via the Home Assistant API and pushes
   its config.

It is **create-once** — if the dashboard already exists it is left untouched (your edits
are safe). To pull in an updated layout, set `redeploy_dashboard: true` and restart once.

**Still required (these can't be automated):** install the frontend cards via HACS —
**card-mod** and **Mushroom** (both dashboards), **Button Card** and **Browser Mod**
(the standard dashboard's tiles and pop-ups), and **Bubble Card** (the bubble dashboard).
The optional **pretty-location** and **test-mode** features are a small manual package
under [`dashboards/`](dashboards/) (`Packages/`, `Templates/`, `Helpers/`).

### Kamereon account id

Leave `account_id` blank and the add-on auto-discovers your MyRenault/Kamereon
account on login. Only set it if you have multiple accounts and need to pin a
specific one.

## Status panel

The add-on adds a **read-only "Alpine A290" panel to the Home Assistant sidebar** (via
ingress). It shows the latest poll at a glance — battery, range, charging, plug, climate,
charge limits and diagnostics — without needing a dashboard. It is **read-only** (it never
changes anything), **auth-gated by Home Assistant**, and stores no credentials or precise
location. The bundled dashboards remain the richer view; the panel is the quick glance.

## Requirements

- The **Mosquitto broker** add-on (the MQTT connection is auto-discovered).

## Entities

Published via MQTT discovery under the **Alpine A290** device, e.g.
`sensor.alpine_a290_battery_level`, `…_range`, `…_plug_status`,
`…_charging_status`, `…_charging_power`, `…_mileage`.

### Control buttons

The add-on publishes a button for each remote action the car supports (it probes
`supports_endpoint()` at startup and only ships the buttons that are available, so you
won't see a control your A290 rejects):

| Button | Action |
| --- | --- |
| **Sound Horn** | Briefly sounds the horn. |
| **Flash Lights** | Flashes the headlights. |
| **Start Climate** | Starts preconditioning to `precondition_temperature`. |
| **Stop Climate** | Stops preconditioning. |
| **Refresh Location** | Forces a fresh GPS fix. *(May report forbidden on the A290 — see note.)* |

> Remote **charge-start is forbidden** on the A290 by Renault, so no Start Charging button
> is shown. **Refresh Location** isn't explicitly documented for the A290 in `renault-api`;
> it falls back to the default endpoint and may return *forbidden* when pressed — harmless
> if so, but it may simply not work.
