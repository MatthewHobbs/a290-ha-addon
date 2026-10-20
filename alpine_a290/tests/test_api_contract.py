"""Contract test against the REAL renault-api response models.

The poller reads specific attribute names + helper methods off renault-api's models
(`batteryLevel`, `batteryAutonomy`, `totalMileage`, `gpsLatitude`, `get_plug_status()`, …).
Those are pinned to `renault-api==0.5.12`, but a deliberate bump could rename a field or
change a helper and silently break the matching sensor — the rest of the suite uses synthetic
stubs that wouldn't notice. This loads a representative API response through the **library's
own schemas** and asserts every field/method the poller depends on, so such a change fails CI
instead of shipping a dead sensor. If this breaks after a renault-api bump, update the poller
(and this contract) together. See CONTRIBUTING.md "Do not bump renault-api casually".
"""
import renault_api.kamereon.schemas as schemas
from renault_api.kamereon.enums import ChargeState, PlugState

# A representative Kamereon battery-status / cockpit / location payload (the shape the API
# returns for an A290), used only to exercise the real library schemas.
_BATTERY = {
    "timestamp": "2026-06-28T10:00:00Z", "batteryLevel": 80, "batteryTemperature": 20,
    "batteryAutonomy": 250, "batteryCapacity": 0, "batteryAvailableEnergy": 42,
    "plugStatus": 1, "chargingStatus": 1.0, "chargingRemainingTime": 30,
    "chargingInstantaneousPower": 7.4, "chargingRemainingTimeLastUpdateDateTime": None,
    "V2L_SystemStatusDisplay": 0,
}
_COCKPIT = {"totalMileage": 12345.6, "fuelAutonomy": None, "fuelQuantity": None}
_LOCATION = {"lastUpdateTime": "2026-06-28T09:00:00Z", "gpsLatitude": 51.5, "gpsLongitude": -0.1}
# get_charges returns an untyped model (raw_data only); the poller reads the per-session dicts
# straight off raw_data["charges"]. This pins both that the schema still surfaces raw_data and
# that _parse_charge_session reads the camelCase keys the Kamereon API actually returns.
_CHARGES = {"charges": [{
    "chargeStartDate": "2026-06-21T00:00:00+00:00", "chargeEndDate": "2026-06-21T03:00:00+00:00",
    "chargeStartBatteryLevel": 35, "chargeEndBatteryLevel": 80,
    "chargeBatteryLevelRecovered": 45, "chargeEnergyRecovered": 23.4,
    "chargeStartInstantaneousPower": 7.4,
}]}


def test_battery_status_model_contract():
    b = schemas.KamereonVehicleBatteryStatusDataSchema.load(_BATTERY)
    # every field the poller reads off the battery model:
    assert b.batteryLevel == 80
    assert b.batteryAutonomy == 250
    assert b.batteryTemperature == 20
    assert b.batteryAvailableEnergy == 42
    assert b.chargingInstantaneousPower == 7.4
    assert b.chargingRemainingTime == 30
    assert b.timestamp == "2026-06-28T10:00:00Z"
    # the decoded enums the poller keys plug/charge state on:
    assert b.get_plug_status() == PlugState.PLUGGED
    assert b.get_charging_status() == ChargeState.CHARGE_IN_PROGRESS


def test_cockpit_model_contract():
    c = schemas.KamereonVehicleCockpitDataSchema.load(_COCKPIT)
    assert c.totalMileage == 12345.6   # -> mileage sensor


def test_location_model_contract():
    loc = schemas.KamereonVehicleLocationDataSchema.load(_LOCATION)
    assert loc.gpsLatitude == 51.5 and loc.gpsLongitude == -0.1
    assert loc.lastUpdateTime == "2026-06-28T09:00:00Z"


def test_charges_model_contract():
    from renault_mqtt import charge
    charges = schemas.KamereonVehicleChargesDataSchema.load(_CHARGES)
    # renault-api exposes the charges list only via raw_data (the model itself is untyped)
    assert charges.raw_data["charges"][0]["chargeEndBatteryLevel"] == 80
    # and the poller turns that raw session into populated Last Charge fields
    lc = charge._parse_charge_session(charges.raw_data["charges"], 52.0)
    assert lc["last_charge_end_soc"] == 80
    assert lc["last_charge_recovered_pct"] == 45
    assert lc["last_charge_duration_min"] == 180   # 3 h from start/end timestamps


# The typed HVAC / charge-mode models the poll loop reads. poll_once reads hvacStatus /
# externalTemperature / socThreshold / lastUpdateTime off get_hvac_status(), the chargeMode
# off get_charge_mode(), and _hvac_schedule_fields walks the typed HvacSettings.schedules ->
# per-day .readyAtTime. A renault-api bump renaming any of these would break a live sensor;
# the synthetic-stub tests elsewhere wouldn't notice, so pin them against the real schemas.
def test_hvac_status_model_contract():
    hv = schemas.KamereonVehicleHvacStatusDataSchema.load(
        {"externalTemperature": 12.5, "internalTemperature": 19.0, "hvacStatus": "off",
         "socThreshold": 30.0, "lastUpdateTime": "2026-06-28T09:00:00Z"})
    assert hv.externalTemperature == 12.5     # -> external_temperature sensor
    assert hv.hvacStatus == "off"             # -> hvac_status sensor
    assert hv.socThreshold == 30.0            # -> hvac_soc_threshold sensor
    assert hv.lastUpdateTime == "2026-06-28T09:00:00Z"


def test_charge_mode_model_contract():
    cm = schemas.KamereonVehicleChargeModeDataSchema.load({"chargeMode": "always"})
    assert cm.chargeMode == "always"          # -> charge_mode sensor


def test_hvac_settings_model_contract():
    import main
    settings = schemas.KamereonVehicleHvacSettingsDataSchema.load(
        {"mode": "scheduled", "schedules": [
            {"id": 1, "activated": False, "monday": {"readyAtTime": "T06:00Z"}},
            {"id": 2, "activated": True,
             "monday": {"readyAtTime": "T07:30Z"}, "friday": {"readyAtTime": "T08:15Z"}},
        ]})
    assert settings.mode == "scheduled"
    # the active schedule's typed per-day .readyAtTime is what the poller summarises:
    assert settings.schedules[1].activated is True
    assert settings.schedules[1].monday.readyAtTime == "T07:30Z"
    out = main._hvac_schedule_fields(settings)
    assert out["climate_schedule_mode"] == "Scheduled"
    assert out["climate_ready_time"] == "Mon 07:30, Fri 08:15"   # only the activated schedule


def test_soc_levels_model_contract():
    """The charge-limit chain, end to end: API field -> data key -> published entity_id.

    This is the one endpoint the contract test did NOT cover, and the gap showed. DOCS.md
    documented `number.alpine_a290_soc_min_target` / `_soc_max_target` — the R5's ids, mirrored
    across without porting. The add-on publishes neither, so anyone automating from the docs
    targeted entities that do not exist.

    The payload below is the REAL captured response from renault-api's own fixture
    (`tests/fixtures/kamereon/vehicle_kcm_data/ev-soc-levels.json`), not an invention, loaded
    through the library's own schema. `soc-levels` is declared for A5E1AE in the library's
    `_VEHICLE_ENDPOINTS`, so this endpoint genuinely exists for this model.
    """
    import re

    import catalog
    import main

    soc = schemas.KamereonVehicleBatterySocDataSchema.load(
        {"lastEnergyUpdateTimestamp": "2025-04-18T06:51:09Z", "socMin": 20, "socTarget": 80})
    # 1. The field names the poller reads off the library model.
    assert soc.socMin == 20
    assert soc.socTarget == 80

    # 2. The data keys the poller writes must equal object_id minus the catalog's prefix, or the
    #    MQTT value_template resolves to nothing and the slider renders unavailable.
    keys = {o[len(catalog.OBJ_PREFIX):] for o in catalog.NUMBERS}
    assert keys == {"soc_min", "soc_target"}, keys

    # 3. And the entity_ids those NAMES produce are the ones the docs must cite. HA ignores the
    #    discovery object_id and derives slug(device name + friendly name) — the whole reason the
    #    wrong ids went unnoticed.
    def slug(text):
        return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")

    device = catalog.DEVICE["name"]
    ids = {slug(f"{device} {meta[0]}") for meta in catalog.NUMBERS.values()}
    assert ids == {"alpine_a290_minimum_soc", "alpine_a290_charge_target_soc"}, ids

    # 4. The real captured values must sit inside the ranges the sliders advertise, or a genuine
    #    car state would be unsettable through the control the add-on ships.
    ranges = {o[len(catalog.OBJ_PREFIX):]: (m[2], m[3]) for o, m in catalog.NUMBERS.items()}
    assert ranges["soc_min"][0] <= soc.socMin <= ranges["soc_min"][1]
    assert ranges["soc_target"][0] <= soc.socTarget <= ranges["soc_target"][1]

    assert main.SOC_ENDPOINT == "soc-levels"


# What the library declares for the A290 (model A5E1AE): every key, including the ones set to
# None. The add-on probes supports_endpoint() at startup and publishes only what this allows, and a
# key that DISAPPEARS falls back to renault-api's default map, which reads as supported (that is
# how hvac-settings was polled into 502000 errors). Dependabot proposes renault-api like any
# dependency; this fails CI on any change to the map until someone has looked at it.
_KCA = "/kca/car-adapter/v1/cars/{vin}"
A5E1AE_MAP = {
    "actions/charge-set-mode": None,
    "actions/charge-set-schedule": None,
    "actions/charge-start": ("/kcm/v1/vehicles/{vin}/ev/settings", "kcm-settings"),
    "actions/charge-stop": None,
    "actions/horn-start": (f"{_KCA}/actions/horn-lights", "default"),
    "actions/hvac-set-schedule": None,
    "actions/hvac-start": (f"{_KCA}/actions/hvac-start", "default"),
    "actions/hvac-stop": (f"{_KCA}/actions/hvac-start", "kca-stop"),
    "actions/lights-start": (f"{_KCA}/actions/horn-lights", "default"),
    "actions/refresh-location": (f"{_KCA}/actions/refresh-location", "default"),
    "alerts": None,
    "battery-status": ("/kca/car-adapter/v2/cars/{vin}/battery-status", "default"),
    "charge-history": None,
    "charge-mode": None,
    "charge-schedule": ("/kcm/v1/vehicles/{vin}/ev/settings", "kcm-settings"),
    "charges": (f"{_KCA}/charges", "default"),
    "charging-settings": None,
    "cockpit": (f"{_KCA}/cockpit", "default"),
    "hvac-history": None,
    "hvac-sessions": None,
    "hvac-settings": None,
    "hvac-status": (f"{_KCA}/hvac-status", "default"),
    "location": (f"{_KCA}/location", "default"),
    "lock-status": None,
    "notification-settings": None,
    "pressure": None,
    "res-state": None,
    "soc-levels": ("/kcm/v1/vehicles/{vin}/ev/soc-levels", "default"),
}


def test_a290_endpoint_map_is_the_reviewed_one():
    from renault_api.kamereon.models import _VEHICLE_ENDPOINTS
    declared = {ep: None if v is None else (v.endpoint, v.mode)
                for ep, v in _VEHICLE_ENDPOINTS["A5E1AE"].items()}
    changed = {ep: (A5E1AE_MAP.get(ep, "<absent>"), declared.get(ep, "<absent>"))
               for ep in A5E1AE_MAP.keys() | declared.keys() if A5E1AE_MAP.get(ep, 0) != declared.get(ep, 0)}
    assert not changed, f"A5E1AE endpoint map changed (reviewed, now): {changed}"


def test_requirements_in_pins_match_the_lock():
    """A bump that edits requirements.in but not the hash-locked requirements.txt (or the reverse)
    would pass every other gate while the image kept installing the old version."""
    import re
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app"
    pin = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\;]+)", re.M)
    norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()   # noqa: E731
    lock = {norm(n): v for n, v in pin.findall((app / "requirements.txt").read_text())}
    src = [ln.strip() for ln in (app / "requirements.in").read_text().splitlines()
           if ln.strip() and not ln.strip().startswith("#")]
    assert all(pin.match(ln) for ln in src), f"requirements.in must pin every package with ==: {src}"
    wanted = {norm(n): v for n, v in pin.findall("\n".join(src))}
    assert wanted, "requirements.in has no pins"
    assert {n: lock.get(n) for n in wanted} == wanted
