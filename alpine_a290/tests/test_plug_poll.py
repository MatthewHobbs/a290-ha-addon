"""Real poll/adapter/core contracts; only Renault endpoint responses are synthetic."""
import asyncio
from types import SimpleNamespace

import main
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_mqtt import mqtt


class Vehicle:
    plug = PlugState.PLUGGED
    mileage = 1000
    soc = 50
    charging = False

    async def get_battery_status(self):
        return SimpleNamespace(
            batteryLevel=self.soc, batteryAutonomy=200, batteryTemperature=18,
            chargingInstantaneousPower=0, timestamp="2026-01-01T00:00:00Z",
            get_plug_status=lambda: self.plug,
            get_charging_status=lambda: (ChargeState.CHARGE_IN_PROGRESS if self.charging
                                         else ChargeState.NOT_IN_CHARGE))

    async def get_cockpit(self):
        return SimpleNamespace(totalMileage=self.mileage)


def poll(vehicle, state):
    async def get_vehicle():
        return vehicle

    session = SimpleNamespace(vehicle=get_vehicle, locale="en_GB")
    return asyncio.run(main.poll_once(session, state, 52, set(), "mi"))[0]


def test_real_poll_plug_baseline_clock_policy_and_persistence(monkeypatch, tmp_path):
    clock = [1000]
    monkeypatch.setattr(main, "now_ts", lambda: clock[0])
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    assert (main.PLUG_MIN_AGE, main.PLUG_MAX_AGE, main.PLUG_KM_DELTA, main.PLUG_SOC_DROP) == (600, 43200, 1, 2)
    vehicle, state = Vehicle(), {}
    assert poll(vehicle, state)["plug_suspect"] == "off"
    expected = dict(plug_prev="plugged", plug_base_ts=1000, plug_base_mileage=1000, plug_base_soc=50)
    assert {k: state[k] for k in expected} == expected
    main.save_state(state)
    state = main.load_state()
    vehicle.mileage, vehicle.soc = 1001, 48
    for age, verdict in [(599, "off"), (600, "on"), (43200, "on"), (43201, "off")]:
        clock[0] = 1000 + age
        data = poll(vehicle, state)
        assert data["plug_suspect"] == verdict
        assert data["plug_status"] == "Connected" and data["charging"] == "off"
        assert data["mileage"] == main._dist(1001, "mi")
        assert {k: state[k] for k in expected} == expected
    vehicle.mileage = None
    clock[0] = 1600
    assert poll(vehicle, state)["plug_suspect"] == "off"
    vehicle.mileage, vehicle.soc = 1002, None
    assert poll(vehicle, state)["plug_suspect"] == "off"


@pytest.mark.parametrize("plug,expected", [(PlugState.UNPLUGGED, "on"),
                                          (PlugState.PLUG_UNKNOWN, "off"),
                                          (PlugState.PLUG_ERROR, "off"), (None, "off")])
def test_real_poll_distinguishes_unplugged_from_unknown_and_rebases_on_reconnect(monkeypatch, plug, expected):
    clock = [1000]
    monkeypatch.setattr(main, "now_ts", lambda: clock[0])
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    vehicle, state = Vehicle(), {}
    assert poll(vehicle, state)["plug_suspect"] == "off"
    vehicle.plug, vehicle.charging = plug, True
    clock[0] = 1600
    data = poll(vehicle, state)
    assert data["plug_suspect"] == expected
    assert data["charging"] == "on"
    assert state["plug_prev"] == "unplugged" and state["plug_base_ts"] == 1000
    vehicle.plug, vehicle.charging, vehicle.mileage, vehicle.soc = PlugState.PLUGGED, False, 1010, 40
    clock[0] = 2200
    assert poll(vehicle, state)["plug_suspect"] == "off"
    assert state["plug_base_ts"] == 2200
    assert state["plug_base_mileage"] == 1010 and state["plug_base_soc"] == 40
