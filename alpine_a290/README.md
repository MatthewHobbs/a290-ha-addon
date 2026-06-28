# Alpine A290

A maintained Home Assistant app for the **Alpine A290** EV. It polls the
Renault/Kamereon API and publishes `sensor.alpine_a290_*` / `binary_sensor.*` / `button.*` /
`number.*` entities over **MQTT auto-discovery**. Credentials are entered once on the **Configuration** tab.

- **Native controls** — lights, horn, climate/preconditioning, refresh location — plus
  **writable charge-limit sliders** (Minimum / Charge-Target SoC). You do **not** need Home
  Assistant's `renault` integration. *(Remote charge-start is forbidden by Renault on the
  A290 — a platform limit, not a missing feature.)*
- **Ready-made dashboards** bundled in: set `deploy_dashboard` and the app installs a
  **standard** or **Bubble Card** dashboard for you (both phone-verified in CI).
- **Pre-built, Cosign-signed image** pulled by the Supervisor — no slow on-device build.

See **[DOCS.md](DOCS.md)** for the full option/entity reference and setup, and the
[repository README](https://github.com/MatthewHobbs/a290-ha-addon) for the **HACS frontend
cards you must install first** (card-mod, Mushroom, Button Card, Browser Mod, Bubble Card).
