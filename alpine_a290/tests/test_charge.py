"""a290-side contract for the shared charge seam.

The reconciliation logic itself is tested in the renault-mqtt package. What stays a290's
responsibility is the *contract* between the shared charge output and this model's catalog: the
last_charge_* keys the core produces must be exactly the "last_charge" sensors a290 declares in
its catalog — otherwise a published field would have no home in discovery, or a declared sensor
would go permanently unpopulated.
"""
import catalog
from renault_mqtt import charge

_CHARGE_ITEM = {
    "chargeStartDate": "2026-06-20T22:00:00+00:00",
    "chargeEndDate": "2026-06-21T02:00:00+00:00",
    "chargeStartBatteryLevel": 30, "chargeEndBatteryLevel": 80,
    "chargeBatteryLevelRecovered": 50, "chargeEnergyRecovered": 26.0,
    "chargeStartInstantaneousPower": 7.0,
}


def test_core_charge_keys_match_the_a290_catalog_sensors():
    lc = charge._parse_charge_session([_CHARGE_ITEM], 52.0)
    expected = {obj[len(catalog.OBJ_PREFIX):] for obj in catalog.SENSORS if "last_charge" in obj}
    assert set(lc) == expected
