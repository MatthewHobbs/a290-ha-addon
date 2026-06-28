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
