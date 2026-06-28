# Alpine A290 app

Logs in to your **My Alpine** account, reads your car's data (battery, charging, location,
climate) every few minutes, and publishes it to Home Assistant — you enter your login once
on the Configuration page, no files to edit.

Polls your Alpine A290 through the Renault/Kamereon API and publishes the data to
Home Assistant via MQTT auto-discovery.

## Before you start — install these first

The app **deploys the standard dashboard for you by default** (`deploy_dashboard: standard`),
so install its frontend cards via **HACS → Frontend** *before you start the app for the
first time* — otherwise the dashboard renders as *"Custom element doesn't exist"* with broken
tiles. You also need the **Mosquitto broker** app (the app's MQTT connection is
auto-discovered from it).

| Install via HACS → Frontend | Needed for |
| --- | --- |
| **card-mod** + **Mushroom** | both dashboards |
| **Button Card** | **both** dashboards |
| **Browser Mod** | pop-ups on the **standard** dashboard |
| **Bubble Card** | the **bubble** dashboard only |

Install Mosquitto and the cards above **before first start**, so the dashboard renders
correctly the first time. (The car's location uses Home Assistant's built-in `map` card — no
map plugin or API key needed.) Don't want a dashboard deployed? Set `deploy_dashboard: none`.

### Finding your VIN and account id

- **VIN** (required): the 17-character vehicle identification number — on your **My Alpine**
  app (vehicle details), your registration document (V5C), or the windscreen base. Enter it
  in **uppercase**.
- **account id** (optional): leave it **blank** and the app auto-discovers your
  My Alpine/Kamereon account on login. Only set it if you have multiple accounts and need to
  pin a specific one.

## Configuration

| Option | Description |
| --- | --- |
| `username` | Your My Alpine app email. |
| `password` | Your My Alpine app password. |
| `account_id` | Your Kamereon account id. **Optional** — leave blank to auto-discover it. |
| `vin` | Your vehicle VIN (uppercase). |
| `locale` | Pick from the dropdown (e.g. `en_GB`, `fr_FR`, `de_DE`). Sets the API region, the drive side (right-hand drive for `en_GB`/`en_IE`, left-hand drive otherwise — used for heated-seat mapping), and distance units (**miles** for `en_GB`, **km** otherwise). The country is derived from this, so there is no separate country option. |
| `poll_interval` | Seconds between polls (60–3600, default 300). |
| `battery_capacity_kwh` | `52` or `40`. Must be set — the API reports capacity as 0; used to derive charge-session energy. |
| `stale_hours` | Mark data stale after this many hours without a successful poll (default 6). |
| `gps_precision` | Decimal places the car's GPS is rounded to before publishing (1–6, default **4** ≈ 11 m). Coarsens the location on the retained MQTT topic for privacy; raise to 5–6 for a more precise map pin, lower to 2–3 for more privacy. |
| `precondition_temperature` | Target cabin temperature (°C, 16–27, default 20) used by the **Start Climate** button. |
| `log_level` | `info` normally; `debug` for troubleshooting. |
| `debug_dump` | `false` by default. When `true`, every poll logs the decoded data from all readable API endpoints — with your VIN, account id, username and contact/identifier fields redacted — to help diagnose what your car does/doesn't expose. Turn off again once captured (it's verbose). Prefer this over `log_level: debug` for API diagnostics: the library's own debug logging would expose access tokens. |
| `deploy_dashboard` | `standard` (default), `bubble`, `both`, or `none`. Auto-installs that dashboard — see below. `both` installs the standard dashboard at your `dashboard_url_path` and the bubble one with a `-bubble` suffix (e.g. `alpine-a290` and `alpine-a290-bubble`). Set `none` to skip dashboard deployment. |
| `dashboard_url_path` | URL slug for the deployed dashboard (default `alpine-a290`; with `both`, the bubble one is suffixed `-bubble`). |
| `redeploy_dashboard` | `true` re-pushes the dashboard config on next start (to pick up an update). Default `false` so your edits are never overwritten. |
| `charger_smart_charge` | *(optional)* entity id of your EV charger's **smart-charge** switch — see [Smart Charging](#smart-charging-card) below. |
| `charger_bump_charge` | *(optional)* entity id of your charger's **bump/boost-charge** switch. |
| `charger_target_soc` | *(optional)* entity id of your charger's **charge-target %** number. |
| `charger_target_time` | *(optional)* entity id of your charger's **target-time** (ready-by) control. |

## Smart Charging card

If you control charging through a smart-charging integration — e.g. **[Octopus Energy /
Intelligent Octopus](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)**, Ohme,
Zappi, Wallbox — you can show those controls on the deployed dashboard next to the car's
data. Set the `charger_*` options above to your charger's entity ids and the dashboard gains
a **"Smart Charging"** card (a built-in `entities` card — no extra HACS card needed). Leave
them blank (the default) and no card is added; each blank one is skipped, so you can map just
the controls you have.

For example, with the Octopus Intelligent entities from your account:

```yaml
charger_smart_charge: switch.octopus_energy_<account>_intelligent_smart_charge
charger_bump_charge:  switch.octopus_energy_<account>_intelligent_bump_charge
charger_target_soc:   number.octopus_energy_<account>_intelligent_charge_target
charger_target_time:  select.octopus_energy_<account>_intelligent_target_time
```

(Find the exact ids in **Developer Tools → States**.) The card is read-write — toggling a
switch or changing the target there controls your charger directly. It's added when the
dashboard is deployed, so set `redeploy_dashboard: true` (and restart once) if you add the
entities after the dashboard already exists.

## Dashboard auto-deploy (optional)

Set `deploy_dashboard` to `standard`, `bubble`, or `both` and the app will install the
dashboard(s) for you — **no raw-editor paste, no `configuration.yaml` edits, and nothing
to copy into `/config/www`**. On start it:

1. Reads the chosen dashboard YAML **bundled in the app** and rewrites its image
   references to the **jsDelivr CDN** (served from this app's own repo).
2. Registers the **Zen Dots** Google font as a Lovelace resource.
3. Creates the dashboard (at `dashboard_url_path`) via the Home Assistant API and pushes
   its config.

It is **create-once** — if the dashboard already exists it is left untouched (your edits
are safe). To pull in an updated layout, set `redeploy_dashboard: true` and restart once.

**Still required (these can't be automated):** install the frontend cards via HACS —
**card-mod** and **Mushroom** (both dashboards), **Button Card** (both dashboards),
**Browser Mod** (the standard dashboard's pop-ups), and **Bubble Card** (the bubble
dashboard). The optional **pretty-location** and **test-mode** features are a small manual
package under [dashboards/](https://github.com/MatthewHobbs/a290-ha-addon/tree/main/alpine_a290/dashboards/)
(`Packages/`, `Templates/`, `Helpers/`).

### Kamereon account id

Leave `account_id` blank and the app auto-discovers your My Alpine/Kamereon
account on login. Only set it if you have multiple accounts and need to pin a
specific one.

## Status panel

The app adds a **read-only "Alpine A290" panel to the Home Assistant sidebar**. It shows
the latest poll at a glance — battery, range, charging, plug, climate,
charge limits and diagnostics — without needing a dashboard. It is **read-only** (it never
changes anything), **auth-gated by Home Assistant**, and stores no credentials or precise
location. The bundled dashboards remain the richer view; the panel is the quick glance.

## Requirements

- The **Mosquitto broker** app (the MQTT connection is auto-discovered).

## Entities

Published via MQTT discovery under the **Alpine A290** device, e.g.
`sensor.alpine_a290_battery_level`, `…_range`, `…_plug_status`,
`…_charging_status`, `…_charging_power`, `…_mileage`.

A car that has a charge schedule programmed also exposes it (read-only, from the same settings
used for preconditioning — not a control): `…_charge_schedule_mode`,
`…_scheduled_charge_start`, `…_scheduled_charge_duration`. The programmed **climate**
(preconditioning) schedule is exposed too: `…_climate_schedule_mode` and `…_climate_ready_time`
(the days + times the cabin is set to be ready, e.g. `Mon 07:00, Fri 08:30`). These show
*unavailable* when no schedule is set.

### Control buttons

The app publishes a button for each remote action the car supports (it probes
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

### Last Charge

The **Last Charge** sensors (`…_last_charge_start`, `…_end`, `…_soc_recovered`,
`…_energy_recovered`, `…_duration`, `…_average_power`, `…_type`, …) report your most recent
completed charge. When the car exposes Renault's recent-charges history, the app reads that
**authoritative per-session record** and uses it directly. If the history isn't available yet
for a just-finished session, the app falls back to figures it works out live from the battery
polls, then replaces them with the official record once Renault posts it — so the tiles are
always populated and settle on the real numbers. No configuration is needed; it's automatic.
