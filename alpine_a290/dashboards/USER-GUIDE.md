# Alpine A290 Dashboard — User Guide

This guide explains what the dashboard shows. All data comes from the **Alpine A290 app**
over MQTT (`sensor.alpine_a290_*`), with no dependency on Home Assistant's `renault` integration. Two styles ship with the app: **standard**
(`front-end.txt`) and **Bubble** (`front-end-bubble.txt`); they show the same data with
different cards. See [INSTALLATION.md](INSTALLATION.md) to deploy one (or `both`).

> The Alpine A290 reports `batteryCapacity` as 0, so charge-energy figures are derived from
> the `battery_capacity_kwh` you set on the app. `chargingInstantaneousPower` units can be
> unreliable, and some fields (e.g. cabin temperature) the A290 simply doesn't expose.

## Standard dashboard

Three columns.

### Vehicle Status

A render of the car that **swaps to a charge indicator when a plug is connected** — showing
current State of Charge (SoC — how full the battery is, as a %) against the Min/Target SoC limits. Below it:

| Tile | Entity | Notes |
| --- | --- | --- |
| Battery Level | `sensor.alpine_a290_battery_level` | % |
| Available Energy | `…_available_energy` | kWh |
| Range | `…_range` | miles for `en_GB`, km otherwise |
| Mileage | `…_mileage` | odometer |
| Plug Status | `…_plug_status` | Connected / Disconnected |
| Charging Status | `…_charging_status` | Charging / Not Charging / Charge Ended / Waiting… / Flap Open / Error |
| Charging Power | `…_charging_power` | kW |
| Charging Time Remaining | `…_charging_time_remaining` | from the API |
| Charging Flap | `…_charging_flap` | Closed / "Open: Plugged In" |
| HVAC Status | `…_hvac_status` | HVAC = the car's climate (heating / air-con) |
| HVAC SoC Threshold | `…_hvac_soc_threshold` | battery % below which HVAC can't start |

### Last Activity · Location · Presets

- **Last Activity** — timestamps of the last reported updates: `…_hvac_last_activity`,
  `…_gps_last_activity`, `…_last_updated`.

  **These stop moving when the car is parked, and that is normal.** The car commits its data at
  **power-off** and then sleeps; polling a sleeping car returns the same trip-end values.
  Measured on a real A290 after a drive ending 12:03 UTC: battery-status 12:03:20, location
  12:02:41, climate 11:46 — all three still unchanged eight hours later, still parked.

  So **Mileage** and **Location** only move after a completed journey, and battery/range/plug
  values only refresh while the car is awake (briefly after power-on, and during a charge).
  Nothing here updates again until the car is driven.
- **Location** — Home Assistant's built-in `map` card, driven by
  `device_tracker.alpine_a290_location`. Updates when the car is powered off at the end of a
  journey, and not again until the next one.
- **Climate presets** (read from My Alpine / the car): Preconditioning Temperature
  (`…_preconditioning_temperature`), Heated Steering Wheel and Driver/Passenger Seats
  (`binary_sensor.alpine_a290_heated_*`).

### Remote Control · Last Charge

- **Remote Control** — native MQTT buttons published by the app (no Home Assistant `renault` integration):
  - `button.alpine_a290_sound_horn`, `…_flash_lights`, `…_start_climate`, `…_stop_climate`,
    and `…_refresh_location`.
  - **No charge buttons:** Renault forbids remote charge-start/stop on the A290, so neither
    dashboard includes a charge tile.
  - **Start Climate** preconditions to the app's `precondition_temperature` (default 20 °C);
    HVAC can lag if the car is asleep, and stop may be unreliable — both are Renault-side limits.
- **Charge limits** — writable sliders, set on the car via `set_battery_soc`:
  `number.alpine_a290_charge_target_soc` (target, 55–100 %) and
  `number.alpine_a290_minimum_soc` (minimum, 15–45 %). Shown only when the car supports the
  `soc-levels` endpoint (the A290 does). These replace the Minimum/Target charge-level numbers from Home Assistant's `renault` integration.
- **Last Charge** — captured by the app at the end of a session:
  Start/End time, Start/End SoC, Start/End Energy, SoC Recovered (%), Energy Recovered (kWh),
  Duration, Average Power, and **Type** — which is either **Home** or **Rapid/Public**
  (decided by whether the average power exceeds a home-charger threshold).

### Health

Four problem indicators: `binary_sensor.alpine_a290_api_auth_failure` (bad credentials /
locale), `…_data_stale`, `…_poll_failing`, and `…_plug_state_suspect` (plug state disagrees
with movement/charging — e.g. "Connected" but driven).

`…_data_stale` and `…_poll_failing` answer two different questions, and the difference matters:

- **Data Stale** — the *car* has not reported for longer than `stale_hours`. The readings on
  screen are real but old. **A car parked overnight will show this on, and that is expected** —
  the car commits data at power-off and then sleeps, so the readings genuinely are older than
  your threshold. Paired with **Poll Failing** off, it reads as "all working, car simply parked".
- **Poll Failing** — the *add-on* has not reached Renault for longer than `stale_hours`.

They are independent. A car parked in an underground car park goes Data Stale while polling
stays perfectly healthy — that is the common case, and it is the one that matters, because the
battery percentage on the dashboard is then hours or days out of date. `…_last_updated` shows
when the car last reported; `…_last_successful_poll` shows when the add-on last got through.

### Test mode (optional)

If you installed the optional test package (`input_boolean.a290_test_mode` +
`button.a290_test_charge_run`), you can simulate a 5-minute charge to preview the charge
panels without a real session. It's off by default and not needed for normal use.

## Bubble dashboard

The same entities and data, styled with **Bubble Card**. The bubble version does not include
the Start Charging tile.

---

Spot an error or a tile that doesn't match your car? Open an issue on the
[app repo](https://github.com/MatthewHobbs/a290-ha-addon).
