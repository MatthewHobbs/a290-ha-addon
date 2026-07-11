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
# authoritative Last Charge via the charges endpoint
# --------------------------------------------------------------------------- #
_CHARGE_ITEM = {
    "chargeStartDate": "2026-06-20T22:00:00+00:00",
    "chargeEndDate": "2026-06-21T02:00:00+00:00",   # 4 h later
    "chargeStartBatteryLevel": 30, "chargeEndBatteryLevel": 80,
    "chargeBatteryLevelRecovered": 50, "chargeEnergyRecovered": 26.0,
    "chargeStartInstantaneousPower": 7.0,
}


def test_parse_charge_session_picks_latest_and_computes():
    older = {**_CHARGE_ITEM, "chargeEndDate": "2026-06-10T02:00:00+00:00"}
    lc = main._parse_charge_session([older, _CHARGE_ITEM], 52.0)
    # picked the most recent by end date
    assert lc["last_charge_end"] == "2026-06-21T02:00:00+00:00"
    assert lc["last_charge_start_soc"] == 30 and lc["last_charge_end_soc"] == 80
    assert lc["last_charge_recovered_pct"] == 50
    assert lc["last_charge_recovered_kwh"] == 26.0
    assert lc["last_charge_duration_min"] == 240          # from timestamps, not chargeDuration
    assert lc["last_charge_average_power"] == 6.5         # 26 kWh / 4 h
    assert lc["last_charge_type"] == "Home"               # 6.5 kW <= HOME_POWER_MAX_KW
    # produces exactly the Last Charge sensor keys (same contract as the inferred path)
    expected = {obj[len("a290_"):] for obj in main.SENSORS if "last_charge" in obj}
    assert set(lc) == expected


def test_parse_charge_session_empty_and_incomplete():
    assert main._parse_charge_session([], 52.0) == {}
    assert main._parse_charge_session(None, 52.0) == {}
    # a session still in progress (no end date) is ignored
    assert main._parse_charge_session([{"chargeStartDate": "2026-06-21T22:00:00+00:00"}], 52.0) == {}


def test_parse_charge_session_derives_missing_energy_from_soc():
    item = {"chargeStartDate": "2026-06-21T00:00:00+00:00",
            "chargeEndDate": "2026-06-21T01:00:00+00:00",
            "chargeStartBatteryLevel": 20, "chargeEndBatteryLevel": 40}
    lc = main._parse_charge_session([item], 50.0)
    assert lc["last_charge_recovered_pct"] == 20          # 40 - 20
    assert lc["last_charge_recovered_kwh"] == 10.0        # 20% of 50 kWh


def test_prefer_real_charge_matches_same_session_within_tolerance():
    real = {"last_charge_end": "2026-06-21T02:00:00+00:00"}
    assert main._prefer_real_charge(real, {}) is True        # nothing inferred yet -> use endpoint
    assert main._prefer_real_charge({}, real) is False       # no endpoint data -> keep inferred
    # endpoint's actual stop is a few minutes BEFORE the inferred (observed) stop -> same
    # session, authoritative record still wins (the bug codex caught: strict >= rejected this)
    live_observed_later = {"last_charge_end": "2026-06-21T02:05:00+00:00"}   # +5 min
    assert main._prefer_real_charge(real, live_observed_later) is True
    # a live session ending materially later (hours) is a fresh charge not yet posted -> keep it
    live_fresh = {"last_charge_end": "2026-06-21T06:00:00+00:00"}            # +4 h
    assert main._prefer_real_charge(real, live_fresh) is False
    # an unparseable endpoint date never displaces a live session
    assert main._prefer_real_charge({"last_charge_end": "garbage"}, live_fresh) is False


def test_due_for_charges_throttle(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 10_000.0)
    assert main._due_for_charges({}) is True                              # never fetched
    assert main._due_for_charges({"charges_last_fetch": 10_000.0}) is False
    assert main._due_for_charges({"charges_last_fetch": 0.0}) is True     # stale
    assert main._due_for_charges({"charges_last_fetch": 10_000.0,
                                  "charges_dirty": True}) is True          # session just ended


def test_prefer_real_charge_boundary_and_unparseable_live():
    real = {"last_charge_end": "2026-06-21T02:00:00+00:00"}
    re_ep = main._epoch(real["last_charge_end"])
    # exactly on the tolerance boundary (live ends CHARGE_MATCH_TOLERANCE_SEC after real) -> same session
    at_boundary = {"last_charge_end": main.iso(re_ep + main.CHARGE_MATCH_TOLERANCE_SEC)}
    assert main._prefer_real_charge(real, at_boundary) is True
    # one second past the boundary -> a materially-later fresh session, keep the inferred one
    past = {"last_charge_end": main.iso(re_ep + main.CHARGE_MATCH_TOLERANCE_SEC + 1)}
    assert main._prefer_real_charge(real, past) is False
    # an unparseable INFERRED end can't out-date the endpoint -> endpoint wins (the le-is-None branch)
    assert main._prefer_real_charge(real, {"last_charge_end": "garbage"}) is True


# --------------------------------------------------------------------------- #
# resolve_last_charge — the async orchestrator around the charges endpoint
# --------------------------------------------------------------------------- #
_A_SESSION = {"chargeStartDate": "2026-06-21T00:00:00+00:00",
              "chargeEndDate": "2026-06-21T03:00:00+00:00",
              "chargeStartBatteryLevel": 35, "chargeEndBatteryLevel": 80}


def _charges_vehicle(sessions, counter=None):
    import types as _t

    class V:
        async def get_charges(self, start, end):
            if counter is not None:
                counter["n"] += 1
            return _t.SimpleNamespace(raw_data={"charges": sessions})
    return V()


def test_resolve_last_charge_reads_caches_and_throttles(monkeypatch):
    import asyncio
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    calls = {"n": 0}
    vehicle = _charges_vehicle([_A_SESSION], calls)
    state = {}

    async def scenario():
        # first call: endpoint is due -> fetched, cached, and preferred over the empty inference
        out1 = await main.resolve_last_charge(vehicle, state, {"charges"}, 52.0, {})
        assert out1["last_charge_end"] == "2026-06-21T03:00:00+00:00"
        assert state["real_last_charge"]["last_charge_end_soc"] == 80
        assert state["charges_last_fetch"] == 1000.0 and state["charges_dirty"] is False
        # second call within the throttle window: NOT re-fetched, served from cache
        out2 = await main.resolve_last_charge(vehicle, state, {"charges"}, 52.0, {})
        assert out2["last_charge_end"] == "2026-06-21T03:00:00+00:00"
        assert calls["n"] == 1

    asyncio.run(scenario())


def test_resolve_last_charge_endpoint_error_keeps_prior_value(monkeypatch):
    import asyncio
    monkeypatch.setattr(main, "now_ts", lambda: 5000.0)

    class BoomVehicle:
        async def get_charges(self, start, end):
            raise RuntimeError("charges endpoint 500")

    # a previously-cached authoritative session must survive a transient endpoint failure
    state = {"real_last_charge": {"last_charge_end": "2026-06-20T10:00:00+00:00"}}

    async def scenario():
        out = await main.resolve_last_charge(BoomVehicle(), state, {"charges"}, 52.0, {})
        # no raise; cached value retained; still preferred over empty inference
        assert out["last_charge_end"] == "2026-06-20T10:00:00+00:00"
        assert state["real_last_charge"]["last_charge_end"] == "2026-06-20T10:00:00+00:00"

    asyncio.run(scenario())


def test_resolve_last_charge_skips_endpoint_when_unsupported():
    import asyncio

    async def scenario():
        # endpoint not supported -> never called, inferred value returned unchanged
        live = {"last_charge_end": "2026-06-19T09:00:00+00:00"}
        out = await main.resolve_last_charge(object(), {}, set(), 52.0, live)
        assert out is live

    asyncio.run(scenario())


def test_epoch_none_for_empty_or_non_string():
    assert main._epoch(None) is None
    assert main._epoch("") is None
    assert main._epoch(12345) is None
    assert main._epoch("not-a-date") is None




# --------------------------------------------------------------------------- #
# KCM charge-schedule summary (from the ev/settings payload we already fetch)
# --------------------------------------------------------------------------- #
def test_charge_schedule_fields_extracts_kcm_settings():
    # chargeModeRq / chargeTimeStart / chargeDuration sit alongside the preconditioning fields
    settings = {"preconditioningTemperature": 21, "chargeModeRq": "scheduled_charge",
                "chargeTimeStart": "0420", "chargeDuration": 480}
    out = main._charge_schedule_fields(settings)
    assert out["charge_schedule_mode"] == "Scheduled Charge"   # underscores -> title case
    assert out["scheduled_charge_start"] == "04:20"            # bare HHMM -> HH:MM
    assert out["scheduled_charge_duration"] == 480
    # keys line up with the catalog sensor object_ids (minus the a290_ prefix)
    expected = {obj[len("a290_"):] for obj in main.SENSORS
                if obj.endswith(("charge_schedule_mode", "scheduled_charge_start",
                                 "scheduled_charge_duration"))}
    assert set(out) == expected


def test_charge_schedule_fields_absent_is_none():
    out = main._charge_schedule_fields({"preconditioningTemperature": 21})
    assert out == {"charge_schedule_mode": None, "scheduled_charge_start": None,
                   "scheduled_charge_duration": None}


def test_fmt_hhmm_only_reformats_bare_four_digits():
    assert main._fmt_hhmm("0700") == "07:00"
    assert main._fmt_hhmm("T07:00Z") == "T07:00Z"   # already formatted -> untouched
    assert main._fmt_hhmm(None) is None
    assert main._fmt_hhmm("") is None


# --------------------------------------------------------------------------- #
# HVAC preconditioning schedule (get_hvac_settings)
# --------------------------------------------------------------------------- #
class _Day:
    def __init__(self, ready):
        self.readyAtTime = ready


class _Sched:
    def __init__(self, activated, **days):
        self.activated = activated
        for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            setattr(self, d, days.get(d))


class _HvacSettings:
    def __init__(self, mode, schedules):
        self.mode, self.schedules = mode, schedules


def test_hvac_schedule_fields_active_schedule():
    settings = _HvacSettings("scheduled_value", [
        _Sched(False, monday=_Day("T06:00Z")),                       # inactive -> ignored
        _Sched(True, monday=_Day("T07:00Z"), friday=_Day("0830")),   # active
    ])
    out = main._hvac_schedule_fields(settings)
    assert out["climate_schedule_mode"] == "Scheduled Value"
    assert out["climate_ready_time"] == "Mon 07:00, Fri 08:30"       # only the active schedule
    expected = {obj[len("a290_"):] for obj in main.SENSORS if obj.startswith("a290_climate_")}
    assert set(out) == expected


def test_hvac_schedule_fields_none_and_no_active():
    assert main._hvac_schedule_fields(None) == {"climate_schedule_mode": None,
                                                "climate_ready_time": None}
    out = main._hvac_schedule_fields(_HvacSettings("none", [_Sched(False, monday=_Day("T06:00Z"))]))
    assert out["climate_schedule_mode"] == "None" and out["climate_ready_time"] is None


def test_fmt_ready_normalises_time_forms():
    assert main._fmt_ready("T07:00Z") == "07:00"
    assert main._fmt_ready("08:30:00") == "08:30"
    assert main._fmt_ready("0915") == "09:15"
    assert main._fmt_ready(None) is None


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


def test_distance_device_class_dropped_only_for_miles():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")
    for obj in ("a290_range", "a290_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"])
        assert conf.get("device_class") == "distance" and conf["unit_of_measurement"] == "km"

    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "mi")
    for obj in ("a290_range", "a290_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"])
        assert "device_class" not in conf and conf["unit_of_measurement"] == "mi"


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
# writable charge-limit numbers
# --------------------------------------------------------------------------- #
def test_numbers_published_when_soc_supported():
    c = StubClient()
    main.publish_discovery(c, {main.SOC_ENDPOINT}, "mi")
    base = f"homeassistant/number/{main.NODE}"
    for obj, (name, _icon, mn, mx, step) in main.NUMBERS.items():
        short = obj[len("a290_"):]
        conf = json.loads(c.pub[f"{base}/{short}/config"])
        assert conf["name"] == name
        assert conf["command_topic"] == f"{main.CMD_PREFIX}{short}"
        assert conf["value_template"] == "{{ value_json.%s }}" % short
        assert (conf["min"], conf["max"], conf["step"]) == (mn, mx, step)
        assert conf["device_class"] == "battery" and conf["unit_of_measurement"] == "%"


def test_numbers_cleared_when_soc_unsupported():
    c = StubClient()
    main.publish_discovery(c, set(), "mi")   # soc-levels not supported
    base = f"homeassistant/number/{main.NODE}"
    for obj in main.NUMBERS:
        assert c.pub[f"{base}/{obj[len('a290_'):]}/config"] == ""


def test_retired_soc_sensors_are_cleared():
    c = StubClient()
    main.publish_discovery(c, {main.SOC_ENDPOINT}, "mi")
    for obj in ("a290_soc_min", "a290_soc_target"):
        assert c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"] == ""


def test_number_cmds_match_numbers():
    assert main.NUMBER_CMDS == {obj[len("a290_"):] for obj in main.NUMBERS}


# --------------------------------------------------------------------------- #
# command dispatch + startup detection failure
# --------------------------------------------------------------------------- #
class _Msg:
    def __init__(self, topic, payload=b""):
        self.topic = topic
        self.payload = payload


def test_on_message_dispatches_known_command(monkeypatch):
    recorded = []

    def fake_run_command(cmd, payload=""):   # sync fake: records at call time
        recorded.append((cmd, payload))
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
    assert recorded == [("horn", "")]

    main._on_message(None, None, _Msg(f"{main.CMD_PREFIX}soc_target", b"80"))
    assert recorded[-1] == ("soc_target", "80")

    main._on_message(None, None, _Msg("unrelated/topic"))   # non-command -> ignored
    assert len(recorded) == 2


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
