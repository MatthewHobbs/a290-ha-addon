# Alpine A290 Dashboard — Installation

> **Back up Home Assistant before you start** (Settings → System → Backups). These
> steps add a dashboard (and, for the manual route, packages and helpers) to your config.
>
> **Start/Stop charging:** charge **start** works via the add-on; charge **stop** is
> not exposed by the Renault API, so the Stop-Charging tile is inert.

This dashboard is the **frontend only**. All car data is provided by the
**[Alpine A290 add-on](https://github.com/MatthewHobbs/a290-ha-addon)** — a proper
Home Assistant add-on that polls the Renault/Kamereon API and publishes
`sensor.alpine_a290_*` entities over MQTT auto-discovery. No `venv`, shell scripts or
`secrets.yaml` editing.

**The add-on can also install this dashboard for you.** That's the recommended route
below — it copies nothing into `/config/www`, edits no `configuration.yaml`, and needs no
raw-editor paste. The [manual install](#manual-install-advanced) is kept as a fallback.

---

## Recommended install (add-on auto-deploy)

### 1. Install + configure the add-on (the data layer)

1. **Settings → Add-ons → Add-on Store → ⋮ (top-right) → Repositories**, and add:
   ```
   https://github.com/MatthewHobbs/a290-ha-addon
   ```
2. Install **Mosquitto broker** (if you haven't already) — the add-on auto-discovers it.
3. Install the **Alpine A290** add-on. Open its **Configuration** tab and set:
   | Option | Value |
   | --- | --- |
   | `username` / `password` | Your **My Alpine** app login. |
   | `vin` | Your vehicle VIN (uppercase). |
   | `account_id` | Leave **blank** to auto-discover (set only if you have several accounts). |
   | `locale` | e.g. `en_GB`. Sets the API region, drive side (RHD for `en_GB`/`en_IE`) and units (**miles for `en_GB`**, km otherwise). |
   | `battery_capacity_kwh` | `52` or `40`. |
   | `poll_interval` | Seconds between polls (default 300). |
4. **Start** the add-on. Within a minute you should see `sensor.alpine_a290_battery_level`,
   `…_range`, `…_plug_status`, `device_tracker.alpine_a290_location`, etc.
   (Settings → Devices & Services → Entities, filter "alpine".)

See the add-on's [DOCS](https://github.com/MatthewHobbs/a290-ha-addon/blob/main/alpine_a290/DOCS.md)
for the full entity list.

### 2. Install the frontend cards (HACS)

**Do this before enabling auto-deploy** — a dashboard deployed before its cards exist
renders as a wall of "custom element doesn't exist" errors. Install via HACS → Frontend:

- **card-mod**, **Mushroom Cards**, **Button Card**, **Browser Mod**
- **Bubble Card** — only if you want the Bubble dashboard

The location map uses Home Assistant's built-in `map` card, so there's no map plugin or
API key to install.

### 3. Turn on dashboard auto-deploy

Back in the add-on's **Configuration** tab, set:

| Option | Value |
| --- | --- |
| `deploy_dashboard` | `standard` (closest to the original) or `bubble`. |
| `dashboard_url_path` | URL slug for the dashboard (default `alpine-a290`). |

**Restart** the add-on. On start it fetches the chosen dashboard from this repo, rewrites
its images to the **jsDelivr CDN**, registers the **Zen Dots** Google font as a Lovelace
resource, and creates the dashboard via the HA API — then it appears in your sidebar.

It is **create-once**: if the dashboard already exists it's left untouched (your edits are
safe). To pull in a later layout update, set `redeploy_dashboard: true` and restart once.

### 4. Control buttons + optional extras

- **Control buttons (flash lights / horn / HVAC / charge):** the dashboards drive the
  official **Renault integration**'s button entities (`button.*_flash_lights`,
  `*_sound_horn`, `*_start_charging`, `*_start_air_conditioner`) — install/enable that
  integration for those tiles to work. (Charge **stop** and HVAC **stop** aren't exposed
  by the API, so those tiles are inert.)
- **Pretty location** and **test mode** are not auto-deployed — they're a small package
  you merge manually. See [Optional extras](#optional-extras).

---

## Manual install (advanced)

Use this if you'd rather host the assets yourself (no CDN dependency) or want full local
control. Do steps 1–2 of the recommended route first (add-on + HACS cards), then:

### 3. Copy the assets to `/config/www/`

Using the **File Editor** add-on or **Samba**:

1. Create `/config/www/backgrounds/` and copy everything from this repo's `Images/`
   subfolders into it (the dashboards reference them as `/local/backgrounds/…`).
2. Copy `CSS/zen-dots.css` to `/config/www/zen-dots.css`.
3. Copy `Fonts/ZenDots-Regular.ttf` to `/config/www/` (the CSS references it).

### 4. Wire up resources (and any extras)

This repo's [`YAML/config-entries.yaml`](YAML/config-entries.yaml) shows the blocks to
merge into your `configuration.yaml` (adjust paths to where you place the files):

```yaml
lovelace:
  resources:
    - url: /local/zen-dots.css
      type: css

homeassistant:
  packages: !include_dir_named packages

template:       !include_dir_merge_list templates   # pretty-location + test-panel timers
input_datetime: !include helpers/input_datetime.yaml
input_number:   !include helpers/input_number.yaml
input_text:     !include helpers/input_text.yaml
```

Copy the repo folders accordingly:

- `Packages/` → `/config/packages/`
- `Templates/` → `/config/templates/`
- `Helpers/` → `/config/helpers/`

Check **Developer Tools → YAML → Check Configuration**, then **Restart**.

### 5. Import the dashboard

1. **Settings → Dashboards → + Add Dashboard → New dashboard from scratch.**
2. Open it, then **⋮ → Edit Dashboard → ⋮ → Raw configuration editor**.
3. Paste the entire contents of [`YAML/front-end.txt`](YAML/front-end.txt) (standard)
   or [`YAML/front-end-bubble.txt`](YAML/front-end-bubble.txt) (Bubble), and **Save**.

---

## Optional extras

Whichever route you used, these are a small manual package (the auto-deploy doesn't
install them):

- **Pretty location:** `sensor.alpine_pretty_location` shows "Driveway" / "Home" /
  town. Merge the `Packages/`, `Templates/` and `Helpers/` folders as in
  [manual step 4](#4-wire-up-resources-and-any-extras), create a `zone.driveway` for the
  driveway state, and optionally the [`places`](https://github.com/custom-components/places)
  integration for town names.
- **Test mode:** toggle `input_boolean.a290_test_mode` to preview the charge panels.

---

## Troubleshooting

- **No `alpine_a290` entities?** Check the add-on log (Settings → Add-ons → Alpine A290
  → Log). A `403` usually means wrong credentials/locale. Confirm Mosquitto is running.
- **Entities show but the dashboard is blank/red:** a HACS card is missing — recheck
  the prerequisites and hard-refresh the browser. If you auto-deployed before installing
  the cards, install them and set `redeploy_dashboard: true` for one restart.
- **Map empty:** confirm `device_tracker.alpine_a290_location` has a location (the
  add-on must have polled at least once with GPS data).
