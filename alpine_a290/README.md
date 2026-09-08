# Alpine A290

A maintained Home Assistant app for the **Alpine A290** EV. It polls the
[Renault/Kamereon API](https://github.com/hacf-fr/renault-api) and publishes `sensor.alpine_a290_*` / `binary_sensor.*` / `button.*` /
`number.*` entities over **MQTT auto-discovery**. Credentials are entered once on the **Configuration** tab.

- **Native controls** — lights, horn, climate/preconditioning, start charging — plus
  **writable charge-limit sliders** (Minimum / Charge-Target SoC). You do **not** need Home
  Assistant's `renault` integration. *(Charge-start arrived with renault-api 0.5.13; it works
  by clearing the car's own timer, so it is a no-op under external scheduling like Octopus
  Intelligent. Remote charge-stop stays unavailable. **Refresh Location** is published only
  when `enable_refresh_location` is set — it is destructive on a parked car.)*
- **Location is opt-out** — `device_tracker.alpine_a290_location` publishes by default
  (coarsened per `gps_precision`); set `publish_location: false` to fetch none and clear any
  previously-retained GPS from the broker, for a zero location footprint.
- **Ready-made dashboards** bundled in: set `deploy_dashboard` and the app installs a
  **standard** or **Bubble Card** dashboard for you (both phone-verified in CI).
- **Pre-built multi-arch image** pulled by the Supervisor — no slow on-device build.

See **[DOCS.md](DOCS.md)** for the full option/entity reference and setup, and the
[repository README](https://github.com/MatthewHobbs/a290-ha-addon) for the **HACS frontend
cards you must install first** (card-mod, Mushroom, Button Card, Browser Mod, Bubble Card).
