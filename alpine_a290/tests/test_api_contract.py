"""Contract test against the REAL renault-api response models.

The poller reads specific attribute names + helper methods off renault-api's models
(`batteryLevel`, `batteryAutonomy`, `totalMileage`, `gpsLatitude`, `get_plug_status()`, …).
Those are pinned to `renault-api==0.5.12`, but a deliberate bump could rename a field or
change a helper and silently break the matching sensor — the rest of the suite uses synthetic
stubs that wouldn't notice. This loads a representative API response through the **library's
own schemas** and asserts every field/method the poller depends on, so such a change fails CI
instead of shipping a dead sensor. If this breaks after a renault-api bump, update the poller
(and this contract) together. See CLAUDE.md "Do not bump renault-api casually".
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
    import charge
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
