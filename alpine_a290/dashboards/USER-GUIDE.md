# Alpine A290 Dashboard — User Guide

This guide explains what the dashboard shows. All data comes from the **Alpine A290 add-on**
over MQTT (`sensor.alpine_a290_*`) — there are no scripts, automations, RAW/CLI sensors, or
dependency on Home Assistant's `renault` integration. Two styles ship with the add-on: **standard**
(`front-end.txt`) and **Bubble** (`front-end-bubble.txt`); they show the same data with
different cards. See [INSTALLATION.md](INSTALLATION.md) to deploy one (or `both`).

> The Alpine A290 reports `batteryCapacity` as 0, so charge-energy figures are derived from
> the `battery_capacity_kwh` you set on the add-on. `chargingInstantaneousPower` units can be
> unreliable, and some fields (e.g. cabin temperature) the A290 simply doesn't expose.

## Standard dashboard

Three columns.

### Vehicle Status

A render of the car that **swaps to a charge indicator when a plug is connected** — showing
current SoC against the Min/Target SoC limits. Below it:

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
| HVAC Status | `…_hvac_status` | |
| HVAC SoC Threshold | `…_hvac_soc_threshold` | battery % below which HVAC can't start |

### Last Activity · Location · Presets

- **Last Activity** — timestamps of the last reported updates: `…_hvac_last_activity`,
  `…_gps_last_activity`, `…_last_updated`. Any lag is down to how often Renault polls the car.
- **Location** — Home Assistant's built-in `map` card, driven by
  `device_tracker.alpine_a290_location`. Updates around key-off / next drive.
- **Climate presets** (read from My Alpine / the car): Preconditioning Temperature
  (`…_preconditioning_temperature`), Heated Steering Wheel and Driver/Passenger Seats
  (`binary_sensor.alpine_a290_heated_*`).

### Remote Control · Last Charge

- **Remote Control** — native MQTT buttons published by the add-on (no Home Assistant `renault` integration):
  - `button.alpine_a290_sound_horn`, `…_flash_lights`, `…_start_climate`, `…_stop_climate`,
    and `…_refresh_location`.
  - **No charge buttons:** Renault forbids remote charge-start/stop on the A290, so neither
    dashboard includes a charge tile.
  - **Start Climate** preconditions to the add-on's `precondition_temperature` (default 20 °C);
    HVAC can lag if the car is asleep, and stop may be unreliable — both are Renault-side limits.
- **Charge limits** — writable sliders, set on the car via `set_battery_soc`:
  `number.alpine_a290_charge_target_soc` (target, 55–100 %) and
  `number.alpine_a290_minimum_soc` (minimum, 15–45 %). Shown only when the car supports the
  `soc-levels` endpoint (the A290 does). These replace the Minimum/Target charge-level numbers from Home Assistant's `renault` integration.
- **Last Charge** — captured by the add-on at the end of a session:
  Start/End time, Start/End SoC, Start/End Energy, SoC Recovered (%), Energy Recovered (kWh),
  Duration, Average Power, and **Type** — which is either **Home** or **Rapid/Public**
  (decided by whether the average power exceeds a home-charger threshold).

### Health

Three problem indicators: `binary_sensor.alpine_a290_api_auth_failure` (bad credentials /
locale), `…_data_stale` (no successful poll within `stale_hours`), and `…_plug_suspect`
(plug state disagrees with movement/charging — e.g. "Connected" but driven).

### Test mode (optional)

If you installed the optional test package (`input_boolean.a290_test_mode` +
`button.a290_test_charge_run`), you can simulate a 5-minute charge to preview the charge
panels without a real session. It's off by default and not needed for normal use.

## Bubble dashboard

The same entities and data, styled with **Bubble Card**. The bubble version does not include
the Start Charging tile.

---

Spot an error or a tile that doesn't match your car? Open an issue on the
[add-on repo](https://github.com/MatthewHobbs/a290-ha-addon).
