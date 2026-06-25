# Alpine A290 Home Assistant Add-on

[![CI](https://github.com/MatthewHobbs/a290-ha-addon/actions/workflows/ci.yaml/badge.svg)](https://github.com/MatthewHobbs/a290-ha-addon/actions/workflows/ci.yaml)

A custom Home Assistant **add-on repository** that polls your Alpine A290 (via the
Renault/Kamereon API) and publishes its data into Home Assistant using **MQTT
auto-discovery** — so the entities just appear, with no shell scripts, no Python
`venv`, and no `secrets.yaml` editing.

All configuration (credentials, account id, VIN, locale, poll interval) is entered
on the add-on's **Configuration** page.

## Installation

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ (top-right) → Repositories**.
2. Add this repository URL:
   ```
   https://github.com/MatthewHobbs/a290-ha-addon
   ```
3. Install the **Alpine A290** add-on, open its **Configuration** tab, fill in your
   My Alpine username/password, Kamereon account id, VIN and locale, then **Start** it.
4. Requires the **Mosquitto broker** add-on (the connection is picked up
   automatically).

Your Alpine A290 entities (`sensor.alpine_a290_*`) will appear automatically via MQTT.

> This add-on provides the **data layer**. The dashboards live in the companion
> repo: [a290-dashboard-view](https://github.com/MatthewHobbs/a290-dashboard-view).

## Credits

Part of the standalone **Alpine A290** project (the
[dashboards](https://github.com/MatthewHobbs/a290-dashboard-view)), which originally
started from [renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)
by [Topolino65](https://github.com/Topolino65) — credit retained. Developed
independently; no changes are submitted upstream. MIT licensed.
