# Alpine A290 Dashboard — Installation

> **Back up Home Assistant before you start** (Settings → System → Backups). These
> steps add a dashboard (and, for the manual route, packages and helpers) to your config.
>
> **Charging control:** the app publishes a **Start Charging** button (renault-api 0.5.13) that
> starts an immediate charge by clearing the car's **built-in** charge timer. If your charging is
> scheduled externally (e.g. **Octopus Intelligent**) it's a **no-op** — use your charger/tariff's
> "charge now" (e.g. Octopus **Bump Charge**) or the car's physical timer button instead. Remote
> charge-*stop* stays unavailable — stop at the charger. The dashboards include no charge tile.

This dashboard is the **frontend only**. All car data is provided by the
**[Alpine A290 app](https://github.com/MatthewHobbs/a290-ha-addon)** — a proper
Home Assistant app that polls the Renault/Kamereon API and publishes
`sensor.alpine_a290_*` entities over MQTT auto-discovery. Credentials are entered once on the Configuration page, nothing to edit by hand.

**The app can also install this dashboard for you.** That's the recommended route
below — it copies nothing into `/config/www`, edits no `configuration.yaml`, and needs no
raw-editor paste. The [manual install](#manual-install-advanced) is kept as a fallback.

---

## Recommended install (app auto-deploy)

### 1. Install the dependencies first

Install these **before** the app, so a deployed dashboard renders straight away (a
dashboard deployed before its cards exist shows a wall of "custom element doesn't exist"):

- **Mosquitto broker** — Settings → Apps → App Store. The app auto-discovers it.
- **[HACS](https://hacs.xyz)** → Frontend, then these cards:
  - **card-mod**, **Mushroom**, **Button Card**, **Browser Mod**
  - **Bubble Card** — only if you want the Bubble dashboard

The location map uses Home Assistant's built-in `map` card, so there's no map plugin or
API key to install.

### 2. Install + configure the app (the data layer)

1. **Settings → Apps → App Store → ⋮ (top-right) → Repositories**, and add:
   ```
   https://github.com/MatthewHobbs/a290-ha-addon
   ```
2. Install the **Alpine A290** app. Open its **Configuration** tab and set:
   | Option | Value |
   | --- | --- |
   | `username` / `password` | Your **My Alpine** app login. |
   | `vin` | Your vehicle VIN (uppercase). |
   | `account_id` | Leave **blank** to auto-discover (set only if you have several accounts). |
   | `locale` | e.g. `en_GB`. Sets the API region, drive side (RHD for `en_GB`/`en_IE`) and units (**miles for `en_GB`**, km otherwise). |
   | `battery_capacity_kwh` | `52` or `40`. |
   | `poll_interval` | Seconds between polls (default 300). |
3. **Start** the app. Within a minute you should see `sensor.alpine_a290_battery_level`,
   `…_range`, `…_plug_status`, `device_tracker.alpine_a290_location`, etc.
   (Settings → Devices & Services → Entities, filter "alpine".)

See the app's [DOCS](https://github.com/MatthewHobbs/a290-ha-addon/blob/main/alpine_a290/DOCS.md)
for the full entity list.

### 3. Turn on dashboard auto-deploy

Back in the app's **Configuration** tab, set:

| Option | Value |
| --- | --- |
| `deploy_dashboard` | `standard` (closest to the original), `bubble`, or `both`. |
| `dashboard_url_path` | URL slug for the dashboard (default `alpine-a290`; with `both`, the bubble one is suffixed `-bubble`). |

**Restart** the app. On start it reads the dashboard YAML **bundled in the app**,
rewrites its images to the **jsDelivr CDN** (served from this repo), registers the **Zen
Dots** Google font as a Lovelace resource, and creates the dashboard via the HA API — then
it appears in your sidebar.

It is **create-once**: if the dashboard already exists it's left untouched (your edits are
safe). To pull in a later layout update, set `redeploy_dashboard: true` and restart once.

### 4. Control buttons + optional extras

- **Control buttons:** the app publishes these **natively over MQTT** — no Home Assistant `renault` integration needed: `button.alpine_a290_sound_horn`, `…_flash_lights`,
  `…_start_climate`, `…_stop_climate`, `…_refresh_location`, `…_start_charging`. Each is gated
  on what the platform supports. Charge-start (renault-api 0.5.13) starts a charge only by
  clearing the car's **built-in** timer — it's a **no-op** under external scheduling like
  Octopus Intelligent (use the tariff's "charge now" / the car's physical timer instead);
  charge-*stop* stays unavailable. The bundled dashboards include no charge tile.
- **Pretty location** and **test mode** are not auto-deployed — they're a small package
  you merge manually. See [Optional extras](#optional-extras).

---

## Manual install (advanced)

Use this if you'd rather host the assets yourself (no CDN dependency) or want full local
control. Do steps 1–2 of the recommended route first (app + HACS cards), then:

### 3. Copy the assets to `/config/www/`

Using the **File Editor** app or **Samba**:

1. Create `/config/www/backgrounds/` and copy everything from this repo's `Images/`
   subfolders into it (the dashboards reference them as `/local/backgrounds/…`).
2. Copy `CSS/zen-dots.css` to `/config/www/zen-dots.css`.
3. Copy `Fonts/ZenDots-Regular.ttf` to `/config/www/` (the CSS references it).

### 4. Wire up resources (and any extras)

The [`config-entries.yaml`](config-entries.yaml) here shows the blocks to merge into your
`configuration.yaml` (adjust paths to where you place the files):

```yaml
lovelace:
  resources:
    - url: /local/zen-dots.css
      type: css

homeassistant:
  packages: !include_dir_named packages

template: !include_dir_merge_list templates   # pretty-location sensor
```

Copy the repo folders accordingly:

- `Packages/` → `/config/packages/` — includes the self-contained **`a290_test.yaml`** test-mode
  package (its own helpers, display sensors, timers and driver — nothing else to wire up).
- `Templates/` → `/config/templates/` — the pretty-location sensor.

Check **Developer Tools → YAML → Check Configuration**, then **Restart**.

### 5. Import the dashboard

1. **Settings → Dashboards → + Add Dashboard → New dashboard from scratch.**
2. Open it, then **⋮ → Edit Dashboard → ⋮ → Raw configuration editor**.
3. Paste the entire contents of [`front-end.txt`](front-end.txt) (standard)
   or [`front-end-bubble.txt`](front-end-bubble.txt) (Bubble), and **Save**.

---

## Optional extras

Whichever route you used, these are a small manual package (the auto-deploy doesn't
install them):

- **Pretty location:** `sensor.alpine_pretty_location` shows "Driveway" / "Home" /
  town. Merge `Templates/a290_pretty_location.yaml` as in
  [manual step 4](#4-wire-up-resources-and-any-extras), create a `zone.driveway` for the
  driveway state, and optionally the [`places`](https://github.com/custom-components/places)
  integration for town names.
- **Test mode:** merge the single self-contained **`Packages/a290_test.yaml`** (everything is
  prefixed `a290_test_*`, so it never clashes with the R5 add-on's test mode). Then either toggle
  `input_boolean.a290_test_mode` to preview the charge panels, or press the **Run Test Charge**
  button for a scripted ~5-minute demo that fills the Last-Charge tiles and auto-resets.

---

## Troubleshooting

- **No `alpine_a290` entities?** Check the app log (Settings → Apps → Alpine A290
  → Log). A `403` usually means wrong credentials/locale. Confirm Mosquitto is running.
- **Entities show but the dashboard is blank/red:** a HACS card is missing — recheck
  the prerequisites and hard-refresh the browser. If you auto-deployed before installing
  the cards, install them and set `redeploy_dashboard: true` for one restart.
- **Map empty:** confirm `device_tracker.alpine_a290_location` has a location (the
  app must have polled at least once with GPS data).
