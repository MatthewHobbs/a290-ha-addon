"""Entity catalog for the Alpine A290 — the declarative per-model tables (sensors, binary
sensors, icons, optional endpoints, control buttons). Object_ids are prefixed "a290_"; the
discovery value_template strips that prefix."""

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
    "a290_soc_target":           ("Charge Target SoC", "battery", "%", None),
    "a290_soc_min":              ("Minimum SoC", "battery", "%", None),
    "a290_charge_mode":          ("Charge Mode", None, None, None),
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

RETIRED_SENSORS = ["a290_cabin_temperature"]
