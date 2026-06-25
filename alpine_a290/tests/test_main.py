"""Unit tests for the Alpine A290 poller.

Focus on the pure logic that has bitten us before or would silently break a
dashboard tile: the discovery-template/data-key contract, charge-session maths,
plug-suspect detection, enum decoding and unit conversion.
"""
import json

import main
import pytest
from renault_api.kamereon.enums import ChargeState


class Battery:
    """Stand-in for renault-api's battery-status object (attr access + one method)."""

    def __init__(self, soc, power=0.0, energy=None, status=None):
        self.batteryLevel = soc
        self.chargingInstantaneousPower = power
        self.batteryAvailableEnergy = energy
        self._status = status

    def get_charging_status(self):
        return self._status


class StubClient:
    """Captures MQTT publishes so we can assert on discovery payloads."""

    def __init__(self):
        self.pub = {}

    def publish(self, topic, payload, retain=False):
        self.pub[topic] = payload


# --------------------------------------------------------------------------- #
# unit conversion / coercion helpers
# --------------------------------------------------------------------------- #
def test_num_rounds_and_tolerates_garbage():
    assert main._num("12.345") == 12.35
    assert main._num(None) is None
    assert main._num("not-a-number") is None


def test_dist_respects_locale_unit():
    assert main._dist(100, "km") == 100
    assert main._dist(100, "mi") == 62.1   # 100 km -> 62.1 mi
    assert main._dist(None, "mi") is None


@pytest.mark.parametrize("truthy", [True, "true", "True", "on", "ON", 1, "1"])
def test_bool_on_truthy(truthy):
    assert main._bool_on(truthy) == "on"


@pytest.mark.parametrize("falsy", [False, "false", None, 0, "0", "off"])
def test_bool_on_falsy(falsy):
    assert main._bool_on(falsy) == "off"


# --------------------------------------------------------------------------- #
# enum decoding
# --------------------------------------------------------------------------- #
def test_enum_label_known_member():
    assert main._enum_label(ChargeState.CHARGE_IN_PROGRESS,
                            main.CHARGE_STATUS_LABELS, 1.0) == "Charging"


def test_enum_label_unmapped_member_is_prettified():
    labels = {}  # force the name-prettify fallback
    assert main._enum_label(ChargeState.CHARGE_IN_PROGRESS, labels, 1.0) == "Charge In Progress"


def test_enum_label_none_uses_raw():
    assert main._enum_label(None, {}, None) == "Unknown"
    assert main._enum_label(None, {}, 0.2) == "Unknown (0.2)"


# --------------------------------------------------------------------------- #
# preconditioning payload search
# --------------------------------------------------------------------------- #
def test_find_precond_locates_nested_block():
    payload = {"data": {"attributes": {"ev": {"preconditioningTemperature": 21}}}}
    assert main._find_precond(payload) == {"preconditioningTemperature": 21}


def test_find_precond_returns_empty_when_absent_or_too_deep():
    assert main._find_precond({"foo": "bar"}) == {}
    assert main._find_precond("not a dict") == {}
    # deeper than the depth guard (4) -> empty
    deep = {"data": {"data": {"data": {"data": {"data": {"preconditioningX": 1}}}}}}
    assert main._find_precond(deep) == {}


# --------------------------------------------------------------------------- #
# is_charging — status OR power-fallback
# --------------------------------------------------------------------------- #
def test_is_charging_by_status():
    assert main.is_charging(Battery(50, power=0.0, status=ChargeState.CHARGE_IN_PROGRESS)) is True


def test_is_charging_by_power_fallback():
    assert main.is_charging(Battery(50, power=6.0, status=ChargeState.NOT_IN_CHARGE)) is True


def test_not_charging():
    assert main.is_charging(Battery(50, power=0.0, status=ChargeState.NOT_IN_CHARGE)) is False


# --------------------------------------------------------------------------- #
# charge-session tracking
# --------------------------------------------------------------------------- #
def test_charge_session_lifecycle(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}

    # start
    main.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    assert state["session_active"] is True

    # mid-session power sample
    main.update_charge_session(state, Battery(60, 7.0, 30.0), 52.0, charging=True)

    # end, 30 minutes later
    clock["t"] = 1000.0 + 1800
    lc = main.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)

    assert state["session_active"] is False
    assert lc["last_charge_duration_min"] == 30
    assert lc["last_charge_recovered_pct"] == 40          # 80 - 40
    assert lc["last_charge_recovered_kwh"] == 20.0        # 40 - 20
    assert lc["last_charge_average_power"] == 7.0
    assert lc["last_charge_type"] == "Home"               # avg <= HOME_POWER_MAX_KW


def test_charge_session_energy_falls_back_to_soc_estimate(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    state = {}
    # batteryAvailableEnergy is None -> energy derived from soc * capacity
    main.update_charge_session(state, Battery(50, 7.0, None), 52.0, charging=True)
    assert state["start_energy"] == pytest.approx(26.0)   # 50% of 52 kWh


def test_rapid_charge_is_classified_public(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.update_charge_session(state, Battery(20, 50.0, 10.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = main.update_charge_session(state, Battery(60, 0.0, 31.0), 52.0, charging=False)
    assert lc["last_charge_type"] == "Rapid/Public"       # avg 50 kW > HOME_POWER_MAX_KW


# --------------------------------------------------------------------------- #
# plug stuck-detection
# --------------------------------------------------------------------------- #
def test_plug_suspect_disconnected_but_charging():
    # plug reported unplugged (0) while the car is actually charging -> suspect
    assert main.detect_plug_suspect({}, plug=0, mileage=1000, soc=50, charging=True) == "on"


def test_plug_suspect_connected_but_driven(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    # first sighting: plugged in -> baseline captured, not yet suspect
    assert main.detect_plug_suspect(state, plug=1, mileage=1000, soc=50, charging=False) == "off"
    # later: still "plugged" but driven 5 km and 4% SoC lost over 1h -> suspect
    clock["t"] = 1000.0 + 3600
    assert main.detect_plug_suspect(state, plug=1, mileage=1005, soc=46, charging=False) == "on"


def test_plug_suspect_quiet_when_genuinely_plugged(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.detect_plug_suspect(state, plug=1, mileage=1000, soc=50, charging=False)
    clock["t"] = 1000.0 + 3600
    # no movement, no SoC drop -> not suspect
    assert main.detect_plug_suspect(state, plug=1, mileage=1000, soc=50, charging=False) == "off"


# --------------------------------------------------------------------------- #
# discovery contract — the class of bug that shipped broken Last Charge tiles
# --------------------------------------------------------------------------- #
def test_last_charge_data_keys_match_sensor_object_ids(monkeypatch):
    """Every Last Charge sensor's value_template key must be produced by the data dict."""
    clock = {"t": 0.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = main.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)

    produced = set(lc)
    expected = {obj[len("a290_"):] for obj in main.SENSORS if "last_charge" in obj}
    assert produced == expected
    assert not any(k.startswith("a290_") for k in produced)


def test_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")
    for obj in main.SENSORS:
        payload = c.pub.get(f"homeassistant/sensor/{main.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj[len("a290_"):]


def test_binary_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")
    for obj in main.BINARY_SENSORS:
        payload = c.pub.get(f"homeassistant/binary_sensor/{main.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj[len("a290_"):]


def test_optional_sensors_cleared_when_unsupported():
    c = StubClient()
    main.publish_discovery(c, set(), "km")   # nothing supported
    for obj in main.OPTIONAL_ENDPOINTS["pressure"]:
        assert c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"] == ""


# --------------------------------------------------------------------------- #
# control buttons
# --------------------------------------------------------------------------- #
def test_command_actions_cover_every_button():
    assert {obj[len("a290_"):] for obj in main.ACTION_BUTTONS} == set(main.COMMAND_ACTIONS)


def test_command_actions_call_real_renault_api_methods():
    from renault_api.renault_vehicle import RenaultVehicle

    class Probe:
        def __getattr__(self, name):
            object.__setattr__(self, "called", name)
            return lambda *a, **k: None

    for name, fn in main.COMMAND_ACTIONS.items():
        probe = Probe()
        fn(probe)
        assert hasattr(RenaultVehicle, probe.called), f"{name} -> nonexistent {probe.called}"


def test_charge_start_button_cleared_others_published():
    c = StubClient()
    supported = {ep for _n, _i, ep in main.ACTION_BUTTONS.values()
                 if ep != "actions/charge-start"}
    main.publish_discovery(c, supported, "mi")
    base = f"homeassistant/button/{main.NODE}"
    assert c.pub[f"{base}/charge_start/config"] == ""              # forbidden -> cleared
    assert json.loads(c.pub[f"{base}/horn/config"])["name"] == "Sound Horn"
    assert json.loads(c.pub[f"{base}/climate_start/config"])["icon"] == "mdi:air-conditioner"


def test_precondition_temp_default(monkeypatch):
    monkeypatch.delenv("A290_PRECONDITION_TEMPERATURE", raising=False)
    assert main._precondition_temp() == 20.0


# --------------------------------------------------------------------------- #
# command dispatch + startup detection failure
# --------------------------------------------------------------------------- #
class _Msg:
    def __init__(self, topic):
        self.topic = topic


def test_on_message_dispatches_known_command(monkeypatch):
    recorded = []

    def fake_run_command(cmd):           # sync fake: records at call time
        recorded.append(cmd)
        async def _noop():
            return None
        return _noop()

    scheduled = []

    def fake_schedule(coro, loop):
        scheduled.append(coro)
        coro.close()                     # avoid 'never awaited' warning

    monkeypatch.setattr(main, "run_command", fake_run_command)
    monkeypatch.setattr(main, "_LOOP", object())
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe", fake_schedule)

    main._on_message(None, None, _Msg(f"{main.CMD_PREFIX}horn"))
    assert recorded == ["horn"]

    main._on_message(None, None, _Msg("unrelated/topic"))   # non-command -> ignored
    assert recorded == ["horn"]


def test_detect_supported_degrades_and_invalidates_on_login_failure(monkeypatch):
    import asyncio

    class FakeSession:
        async def close(self):
            pass

    async def boom(websession, locale):
        raise RuntimeError("login boom")

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: FakeSession())
    monkeypatch.setattr(main, "_login_vehicle", boom)

    async def scenario():
        vs = main.VehicleSession("en_GB")
        supported = await main.detect_supported(vs)
        # falls back to the read-only optional endpoints; no action buttons; session dropped
        assert supported == set(main.OPTIONAL_ENDPOINTS)
        assert not any(ep.startswith("actions/") for ep in supported)
        assert vs._vehicle is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# debug API dump — redaction must not leak identifiers/secrets
# --------------------------------------------------------------------------- #
def test_debug_enabled_reads_flag(monkeypatch):
    monkeypatch.setenv("A290_DEBUG_DUMP", "true")
    assert main.debug_enabled() is True
    monkeypatch.setenv("A290_DEBUG_DUMP", "false")
    assert main.debug_enabled() is False
    monkeypatch.delenv("A290_DEBUG_DUMP", raising=False)
    assert main.debug_enabled() is False


def test_debug_redact_masks_ids_and_secret_values_but_keeps_telemetry():
    out = main._debug_redact(
        {
            "vin": "VF1AAAA",
            "registrationNumber": "AB12CDE",       # key match is case-insensitive
            "batteryLevel": 80,                    # telemetry — must be kept
            "owner": {"email": "me@x.com", "firstName": "Matt"},
            "note": "vehicle VF1AAAA parked",      # secret value inside free text
            "items": [{"phoneNumber": "555"}],
        },
        secrets=["VF1AAAA", "acct-123"],
    )
    assert out["vin"] == "***"
    assert out["registrationNumber"] == "***"
    assert out["owner"]["email"] == "***"
    assert out["owner"]["firstName"] == "***"
    assert out["items"][0]["phoneNumber"] == "***"
    assert out["batteryLevel"] == 80
    assert out["note"] == "vehicle *** parked"


# --------------------------------------------------------------------------- #
# cached login session
# --------------------------------------------------------------------------- #
def test_vehicle_session_reuses_login_and_reauths_after_invalidate(monkeypatch):
    import asyncio

    calls = {"login": 0, "closed": 0}

    class FakeSession:
        async def close(self):
            calls["closed"] += 1

    async def fake_login(websession, locale):
        calls["login"] += 1
        return f"vehicle#{calls['login']}"

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: FakeSession())
    monkeypatch.setattr(main, "_login_vehicle", fake_login)

    async def scenario():
        vs = main.VehicleSession("en_GB")
        first = await vs.vehicle()
        again = await vs.vehicle()
        assert first is again            # cached — only one login
        assert calls["login"] == 1

        await vs.invalidate()            # drop the session
        assert calls["closed"] == 1
        assert await vs.vehicle() == "vehicle#2"   # re-authenticates
        assert calls["login"] == 2

        await vs.close()
        assert calls["closed"] == 2

    asyncio.run(scenario())
