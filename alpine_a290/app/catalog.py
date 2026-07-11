"""Entity catalog for the Alpine A290 — the declarative per-model tables (sensors, binary
sensors, icons, optional endpoints, control buttons). Object_ids are prefixed "a290_"; the
discovery value_template strips that prefix."""

# The per-model object_id prefix. Every object_id below starts with it, and main.py strips it
# (obj[len(OBJ_PREFIX):]) to derive the MQTT value_template key / command suffix. This is the
# ONE place the prefix is defined — the r5 twin sets "r5_" here and nothing else in the shared
# poll/publish loop changes (previously it was a hard-coded `obj[5:]` magic number at 5 sites).
OBJ_PREFIX = "a290_"

# The per-model environment-variable prefix the add-on's options are exported under (run.sh
# exports A290_USERNAME, A290_VIN, …). main.py injects it into the shared core as
# `config.ENV_PREFIX` so the redaction net reads this model's option names; the r5 twin sets
# "R5_" here. The ONE place the env prefix is defined.
ENV_PREFIX = "A290_"

# Per-model MQTT identity, injected into the shared core via mqtt.configure(catalog). NODE is the
# HA discovery node + topic root; DEVICE is the HA device block (its name drives the entity_id
# slug — HA ignores object_id); MQTT_KEEPALIVE is the broker keepalive. DIST_UNIT_OBJS names the
# sensors whose unit follows the locale (mi/km) instead of a fixed one. The r5 twin sets its own.
NODE = "alpine_a290"
DEVICE = {"identifiers": [NODE], "name": "Alpine A290", "manufacturer": "Alpine", "model": "A290"}
MQTT_KEEPALIVE = 60
DIST_UNIT_OBJS = ("a290_range", "a290_mileage")

SENSORS = {
    "a290_battery_level":        ("Battery Level", "battery", "%", "measurement"),
    "a290_range":                ("Range", "distance", "km", "measurement"),
    "a290_battery_temperature":  ("Battery Temperature", "temperature", "°C", "measurement"),
    "a290_charging_power":       ("Charging Power", "power", "kW", "measurement"),
    "a290_charging_remaining":   ("Charging Time Remaining", "duration", "min", "measurement"),
    "a290_available_energy":     ("Available Energy", "energy_storage", "kWh", "measurement"),
    "a290_plug_status":          ("Plug Status", None, None, None),
    "a290_charging_status":      ("Charging Status", None, None, None),
    "a290_charging_flap":        ("Charging Flap", None, None, None),
    "a290_drive_side":           ("Drive Side", None, None, None),
    "a290_mileage":              ("Mileage", "distance", "km", "total_increasing"),
    "a290_preconditioning_temperature": ("Preconditioning Temperature", "temperature", "°C", None),
    "a290_hvac_last_activity":   ("HVAC Last Activity", "timestamp", None, None),
    "a290_gps_last_activity":    ("GPS Last Activity", "timestamp", None, None),
    "a290_external_temperature": ("Outside Temperature", "temperature", "°C", "measurement"),
    "a290_hvac_status":          ("HVAC Status", None, None, None),
    "a290_hvac_soc_threshold":   ("HVAC SoC Threshold", "battery", "%", None),
    "a290_charge_mode":          ("Charge Mode", None, None, None),
    "a290_charge_schedule_mode": ("Charge Schedule Mode", None, None, None),
    "a290_scheduled_charge_start": ("Scheduled Charge Start", None, None, None),
    "a290_scheduled_charge_duration": ("Scheduled Charge Duration", "duration", "min", None),
    "a290_climate_schedule_mode": ("Climate Schedule Mode", None, None, None),
    "a290_climate_ready_time":   ("Climate Ready Time", None, None, None),
    "a290_tyre_pressure_fl":     ("Tyre Pressure Front Left", None, None, "measurement"),
    "a290_tyre_pressure_fr":     ("Tyre Pressure Front Right", None, None, "measurement"),
    "a290_tyre_pressure_rl":     ("Tyre Pressure Rear Left", None, None, "measurement"),
    "a290_tyre_pressure_rr":     ("Tyre Pressure Rear Right", None, None, "measurement"),
    "a290_last_updated":         ("Last Updated", "timestamp", None, None),
    "a290_last_charge_start":          ("Last Charge Start", "timestamp", None, None),
    "a290_last_charge_end":            ("Last Charge End", "timestamp", None, None),
    "a290_last_charge_start_soc":      ("Last Charge Start SoC", "battery", "%", None),
    "a290_last_charge_end_soc":        ("Last Charge End SoC", "battery", "%", None),
    "a290_last_charge_start_energy":   ("Last Charge Start Energy", "energy", "kWh", None),
    "a290_last_charge_end_energy":     ("Last Charge End Energy", "energy", "kWh", None),
    "a290_last_charge_recovered_pct":  ("Last Charge SoC Recovered", None, "%", None),
    "a290_last_charge_recovered_kwh":  ("Last Charge Energy Recovered", "energy", "kWh", None),
    "a290_last_charge_duration_min":   ("Last Charge Duration", "duration", "min", None),
    "a290_last_charge_average_power":  ("Last Charge Average Power", "power", "kW", None),
    "a290_last_charge_type":           ("Last Charge Type", None, None, None),
}
BINARY_SENSORS = {
    "a290_charging":              ("Charging", "battery_charging"),
    "a290_heated_steering_wheel": ("Heated Steering Wheel", None),
    "a290_heated_seat_driver":    ("Heated Seat Driver", None),
    "a290_heated_seat_passenger": ("Heated Seat Passenger", None),
    "a290_plug_suspect":          ("Plug State Suspect", "problem"),
    "a290_api_auth_failure":      ("API Auth Failure", "problem"),
    "a290_data_stale":            ("Data Stale", "problem"),
}

ICONS = {
    "a290_plug_status":           "mdi:power-plug",
    "a290_charging_status":       "mdi:battery-charging",
    "a290_charging_flap":         "mdi:ev-plug-type2",
    "a290_drive_side":            "mdi:steering",
    "a290_hvac_status":           "mdi:fan",
    "a290_charge_mode":           "mdi:ev-station",
    "a290_charge_schedule_mode":  "mdi:calendar-clock",
    "a290_scheduled_charge_start": "mdi:clock-start",
    "a290_scheduled_charge_duration": "mdi:timer-outline",
    "a290_climate_schedule_mode": "mdi:fan-clock",
    "a290_climate_ready_time":    "mdi:clock-check-outline",
    "a290_last_charge_type":      "mdi:ev-station",
    "a290_heated_steering_wheel": "mdi:steering",
    "a290_heated_seat_driver":    "mdi:car-seat-heater",
    "a290_heated_seat_passenger": "mdi:car-seat-heater",
}

OPTIONAL_ENDPOINTS = {
    "charge-mode": ["a290_charge_mode"],
    "pressure": ["a290_tyre_pressure_fl", "a290_tyre_pressure_fr",
                 "a290_tyre_pressure_rl", "a290_tyre_pressure_rr"],
}

ACTION_BUTTONS = {
    "a290_charge_start":     ("Start Charging",   "mdi:ev-station",      "actions/charge-start"),
    "a290_horn":             ("Sound Horn",       "mdi:bullhorn",        "actions/horn-start"),
    "a290_lights":           ("Flash Lights",     "mdi:car-light-high",  "actions/lights-start"),
    "a290_climate_start":    ("Start Climate",    "mdi:air-conditioner", "actions/hvac-start"),
    "a290_climate_stop":     ("Stop Climate",     "mdi:fan-off",         "actions/hvac-stop"),
    "a290_refresh_location": ("Refresh Location", "mdi:crosshairs-gps",  "actions/refresh-location"),
}

# Writable charge-limit controls. State comes from the poll's soc-levels read (data keys
# match object_id[5:]); a press writes via set_battery_soc(). Gated on SOC_ENDPOINT support,
# so a model that rejects the write never ships the control. (name, icon, min, max, step)
SOC_ENDPOINT = "soc-levels"
# (CHARGES_ENDPOINT — the authoritative recent-charge-sessions endpoint — moved to
# renault_ha_core.charge with the reconciliation logic; it's identical across models. main.py
# imports it from there for the endpoint-support probe.)
# The refresh-location action endpoint. Names the ACTION_BUTTONS entry that triggers a GPS
# refresh; the poller gates both the discovery button and the command on it (and on the
# location opt-out), so it's shared between the control layer and the MQTT discovery layer.
REFRESH_LOCATION_EP = "actions/refresh-location"
NUMBERS = {
    "a290_soc_min":    ("Minimum SoC",       "mdi:battery-arrow-down", 15, 45,  5),
    "a290_soc_target": ("Charge Target SoC", "mdi:battery-arrow-up",   55, 100, 5),
}

# Cleared on every discovery publish so retired entities don't linger on existing installs.
# soc_min/soc_target moved from SENSORS to NUMBERS, so their old sensor configs are cleared.
RETIRED_SENSORS = ["a290_cabin_temperature", "a290_soc_target", "a290_soc_min"]

# Published but disabled in the entity registry by default — mapping artifacts with no
# user-meaningful state. drive_side is just RHD/LHD derived from locale (used internally for
# heated-seat mapping); it adds noise to the entity list. Users who want it can re-enable it.
DEFAULT_DISABLED_SENSORS = {"a290_drive_side"}
