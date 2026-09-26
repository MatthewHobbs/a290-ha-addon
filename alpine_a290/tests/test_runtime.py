"""Runtime/async coverage: poll_once, command dispatch, detection, MQTT wiring, health
server, account resolution, and one happy + one failing iteration of main()."""
import ast
import asyncio
import json
import logging
import types
from pathlib import Path

import main
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_mqtt import charge, config, debug, mqtt


def ns(**kw):
    return types.SimpleNamespace(**kw)


class FakeBattery:
    def __init__(self, power=0.0, status=ChargeState.NOT_IN_CHARGE, plug=PlugState.PLUGGED):
        self.batteryLevel = 60
        self.batteryAutonomy = 200
        self.batteryTemperature = 18
        self.chargingInstantaneousPower = power
        self.chargingRemainingTime = None
        self.batteryAvailableEnergy = 30.0
        self.plugStatus = plug.value
        self.timestamp = "2026-01-01T00:00:00Z"
        self._status, self._plug = status, plug

    def get_plug_status(self):
        return self._plug

    def get_charging_status(self):
        return self._status


class FakeVehicle:
    async def get_battery_status(self):
        return FakeBattery()

    async def get_cockpit(self):
        return ns(totalMileage=12345)

    async def get_hvac_status(self):
        return ns(externalTemperature=12, hvacStatus="off", socThreshold=20, lastUpdateTime="t")

    async def get_charge_schedule(self):
        return {"preconditioningTemperature": 21, "preconditioningHeatedStrgWheel": True,
                "preconditioningHeatedLeftSeat": False, "preconditioningHeatedRightSeat": True,
                "chargeModeRq": "scheduled_charge", "chargeTimeStart": "0230", "chargeDuration": 360}

    async def get_hvac_settings(self):
        day = ns(readyAtTime="T07:15Z")
        sched = ns(activated=True, monday=day, tuesday=None, wednesday=None,
                   thursday=None, friday=None, saturday=None, sunday=None)
        return ns(mode="scheduled", schedules=[sched])

    async def get_battery_soc(self):
        return ns(socTarget=80, socMin=20)

    async def get_tyre_pressure(self):
        return ns(flPressure=2.4, frPressure=2.4, rlPressure=2.3, rrPressure=2.3)

    async def get_charge_mode(self):
        return ns(chargeMode="always")

    async def get_location(self):
        return ns(gpsLatitude=51.512345, gpsLongitude=-0.123456, lastUpdateTime="t")  # synthetic-coords: 6 dp load-bearing, rounding asserted below

    async def get_details(self):
        return ns(raw_data={"vin": "SECRET", "batteryLevel": 60})

    async def start_horn(self):
        self.horned = True

    async def start_lights(self):
        pass

    async def set_ac_start(self, temp):
        self.ac_temp = temp

    async def set_ac_stop(self):
        pass

    async def set_charge_start(self):
        pass

    async def refresh_location(self):
        pass

    async def get_charges(self, start, end):
        return ns(raw_data={"charges": [{
            "chargeStartDate": "2026-06-21T00:00:00+00:00",
            "chargeEndDate": "2026-06-21T03:00:00+00:00",
            "chargeStartBatteryLevel": 35, "chargeEndBatteryLevel": 80,
            "chargeBatteryLevelRecovered": 45, "chargeEnergyRecovered": 23.4,
        }]})


class FakeVSession:
    def __init__(self, vehicle, locale="en_GB"):
        self._v, self.locale, self.invalidated = vehicle, locale, False

    async def vehicle(self):
        return self._v

    async def invalidate(self):
        self.invalidated = True

    async def close(self):
        pass


# --------------------------------------------------------------------------- #
# poll_once
# --------------------------------------------------------------------------- #
def test_poll_once_full(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    data, attrs = asyncio.run(
        main.poll_once(FakeVSession(FakeVehicle()), {}, 52.0,
                       {"pressure", "charge-mode", "hvac-settings"}, "km"))
    assert data["battery_level"] == 60
    assert data["mileage"] == 12345
    assert data["plug_status"] == "Connected"
    assert data["charging"] == "off"
    assert data["charge_mode"] == "always"
    assert data["tyre_pressure_fl"] == 2.4
    assert data["heated_seat_passenger"] == "off"   # left seat, LHD
    assert data["charge_schedule_mode"] == "Scheduled Charge"
    assert data["scheduled_charge_start"] == "02:30" and data["scheduled_charge_duration"] == 360
    assert data["climate_schedule_mode"] == "Scheduled" and data["climate_ready_time"] == "Mon 07:15"
    assert attrs["latitude"] == 51.5123 and attrs["longitude"] == -0.1235   # synthetic-coords: the fixture above, rounded to 4 dp
    assert attrs["gps_accuracy"] == 11                                       # ~11 m at 4 dp
    assert data["available_energy"] == 30.0                                  # reported by the car


def test_poll_once_produces_every_catalog_key(monkeypatch):
    """Every published sensor/binary_sensor reads value_json.<key>; a key poll_once never produces
    renders empty and HA shows unknown, with no error anywhere. Excluded, and only these: the
    last_charge_* keys (need a completed session, covered in test_main) and the keys main()
    stamps after poll_once returns, from its own clock and error path."""
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    monkeypatch.setattr(main, "_BREAKERS", {})
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    data, _ = asyncio.run(
        main.poll_once(FakeVSession(FakeVehicle()), {}, 52.0,
                       {"pressure", "charge-mode", "hvac-settings"}, "km"))
    stamped_by_main = {"last_successful_poll", "api_auth_failure", "data_stale", "poll_failing"}
    keys = {obj[len(main.OBJ_PREFIX):] for obj in (*main.catalog.SENSORS, *main.catalog.BINARY_SENSORS)}
    expected = {k for k in keys if not k.startswith("last_charge_")} - stamped_by_main
    assert expected - set(data) == set(), f"poll_once did not produce: {expected - set(data)}"
    # the keys the success log line reads
    assert {"battery_level", "plug_status", "charging", "plug_suspect"} <= set(data)


def test_poll_once_available_energy_falls_back_to_soc_estimate(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)

    class NoEnergyBattery(FakeBattery):
        def __init__(self, **kw):
            super().__init__(**kw)
            del self.batteryAvailableEnergy          # car doesn't report it (e.g. the R5)

    class V(FakeVehicle):
        async def get_battery_status(self):
            return NoEnergyBattery()

    data, _ = asyncio.run(main.poll_once(FakeVSession(V()), {}, 52.0, set(), "km"))
    assert data["available_energy"] == 31.2          # 60 % SoC × 52 kWh, not unknown


def test_poll_once_uses_charges_endpoint_when_supported(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    monkeypatch.setattr(charge, "now_ts", lambda: 1000.0)   # charges_last_fetch is stamped in charge's ns
    state = {}
    data, _ = asyncio.run(
        main.poll_once(FakeVSession(FakeVehicle()), state, 52.0, {"charges"}, "km"))
    # Last Charge comes from the authoritative charges endpoint, not the (empty) inference
    assert data["last_charge_end"] == "2026-06-21T03:00:00+00:00"
    assert data["last_charge_recovered_pct"] == 45
    assert data["last_charge_duration_min"] == 180          # 3 h from timestamps
    assert state["real_last_charge"]["last_charge_end_soc"] == 80
    assert state["charges_last_fetch"] == 1000.0            # throttle timestamp recorded


class ChargesFailVehicle(FakeVehicle):
    async def get_charges(self, start, end):
        raise RuntimeError("charges forbidden")


def test_poll_once_charges_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    # endpoint errors -> poll still succeeds, just no authoritative Last Charge
    data, _ = asyncio.run(
        main.poll_once(FakeVSession(ChargesFailVehicle()), {}, 52.0, {"charges"}, "km"))
    assert data["battery_level"] == 60
    assert "last_charge_end" not in data


class FlakyVehicle(FakeVehicle):
    async def get_cockpit(self):
        raise RuntimeError("cockpit down")

    async def get_hvac_status(self):
        raise RuntimeError("hvac down")

    async def get_hvac_settings(self):
        raise RuntimeError("hvac-settings down")

    async def get_charge_schedule(self):
        raise RuntimeError("settings down")

    async def get_battery_soc(self):
        raise RuntimeError("soc down")

    async def get_location(self):
        return ns(gpsLatitude=None, gpsLongitude=None, lastUpdateTime=None)


def test_poll_once_tolerates_endpoint_failures(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    data, attrs = asyncio.run(
        main.poll_once(FakeVSession(FlakyVehicle(), locale="fr_FR"), {}, 52.0, set(), "mi"))
    assert attrs is None                 # no GPS fix
    assert "mileage" not in data         # cockpit failed
    assert data["drive_side"] == "LHD"   # fr_FR is left-hand drive


class OptionalFailVehicle(FakeVehicle):
    async def get_tyre_pressure(self):
        raise RuntimeError("tpms down")

    async def get_charge_mode(self):
        raise RuntimeError("mode down")

    async def get_location(self):
        raise RuntimeError("gps down")


def test_poll_once_optional_endpoint_failures(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    data, attrs = asyncio.run(
        main.poll_once(FakeVSession(OptionalFailVehicle()), {}, 52.0,
                       {"pressure", "charge-mode"}, "km"))
    assert attrs is None                 # location fetch raised
    assert "tyre_pressure_fl" not in data
    assert "charge_mode" not in data


def test_poll_once_skips_location_when_publish_disabled(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)

    class NoLocVehicle(FakeVehicle):
        async def get_location(self):
            raise AssertionError("get_location must not be called when location is disabled")

    data, attrs = asyncio.run(
        main.poll_once(FakeVSession(NoLocVehicle()), {}, 52.0, set(), "km"))
    assert attrs is None                       # nothing published
    assert "gps_last_activity" not in data     # and no location-derived field set


# Every logger renault-api 0.5.13 creates. At DEBUG they log unredacted Kamereon bodies (VIN,
# account ids, unrounded GPS), which is what the clamp in setup_logging exists to stop.
_LIBRARY_LOGGERS = ("renault_api", "renault_api.gigya", "renault_api.kamereon", "renault_api.kamereon.models",
                    "renault_api.renault_session", "renault_api.renault_account", "renault_api.renault_client")


def test_setup_logging_keeps_library_debug_off_at_debug(monkeypatch):
    # pytest's root handlers make basicConfig a no-op and leave root at WARNING, which would
    # pass this with no clamp at all. Strip them so basicConfig really sets root to DEBUG.
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_levels = {name: logging.getLogger(name).level for name in _LIBRARY_LOGGERS}
    root.handlers.clear()
    try:
        monkeypatch.setenv("A290_LOG_LEVEL", "debug")
        main.setup_logging()
        assert root.level == logging.DEBUG                       # the precondition is real
        assert main.LOG.isEnabledFor(logging.DEBUG)              # our own debug still flows
        for name in _LIBRARY_LOGGERS:                            # the library's does not
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


def test_poll_once_debug_dump_branch(monkeypatch):
    debug._DEBUG_STATE["dumped"] = False
    monkeypatch.setenv("A290_DEBUG_DUMP", "true")
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    asyncio.run(main.poll_once(FakeVSession(FakeVehicle()), {}, 52.0, set(), "km"))


# --------------------------------------------------------------------------- #
# command dispatch
# --------------------------------------------------------------------------- #
def _fake_client_session(monkeypatch):
    class S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: S())


def test_run_command_dispatches(monkeypatch):
    v = FakeVehicle()
    _fake_client_session(monkeypatch)

    async def fake_login(ws, locale):
        return v

    monkeypatch.setattr(main, "_login_vehicle", fake_login)
    asyncio.run(main.run_command("horn"))
    assert v.horned is True


def test_run_command_unknown_is_ignored():
    asyncio.run(main.run_command("does-not-exist"))


def test_run_command_error_is_swallowed(monkeypatch):
    _fake_client_session(monkeypatch)

    async def boom(ws, locale):
        raise RuntimeError("login failed")

    monkeypatch.setattr(main, "_login_vehicle", boom)
    asyncio.run(main.run_command("horn"))


def test_run_command_debounces_repeat(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    main._last_command["horn"] = 998.0               # 2s ago, inside the 5s window
    called = {"login": 0}

    async def login(ws, loc):
        called["login"] += 1

    _fake_client_session(monkeypatch)
    monkeypatch.setattr(main, "_login_vehicle", login)
    asyncio.run(main.run_command("horn"))
    assert called["login"] == 0                       # suppressed, never logged in


def test_debounce_is_per_command_and_ends_after_the_window(monkeypatch):
    sent = []

    class V:
        async def start_horn(self):
            sent.append("horn")

        async def start_lights(self):
            sent.append("lights")

    _login_as(monkeypatch, V())
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    for t, cmd in ((1000.0, "horn"), (1004.9, "horn"), (1004.9, "lights"), (1005.0, "horn")):
        clock["t"] = t
        asyncio.run(main.run_command(cmd))
    assert sent == ["horn", "lights", "horn"]         # only the repeat inside 5s was dropped


class _Horn:
    def __init__(self, fail=False):
        self.fail, self.honks = fail, 0

    async def start_horn(self):
        self.honks += 1
        if self.fail:
            raise RuntimeError("action failed at the car")


def test_a_press_that_never_reached_the_car_does_not_block_the_retry(monkeypatch):
    """The login failed, so nothing was sent; the retry inside the window must go through."""
    v, logins = _Horn(), {"n": 0}

    async def login(ws, loc):
        logins["n"] += 1
        if logins["n"] == 1:
            raise RuntimeError("login failed")
        return v

    _fake_client_session(monkeypatch)
    monkeypatch.setattr(main, "_login_vehicle", login)
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    asyncio.run(main.run_command("horn"))
    clock["t"] = 1001.0                                # well inside the 5s window
    asyncio.run(main.run_command("horn"))
    assert (logins["n"], v.honks) == (2, 1)


def test_a_press_that_failed_during_the_action_still_debounces(monkeypatch):
    """Once the action has started the outcome is unknown, so a retry could send it twice."""
    v = _Horn(fail=True)
    _login_as(monkeypatch, v)
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    asyncio.run(main.run_command("horn"))
    clock["t"] = 1001.0
    asyncio.run(main.run_command("horn"))
    assert v.honks == 1


@pytest.mark.parametrize("login_fails", [False, True])
def test_simultaneous_presses_pass_the_debounce_once(monkeypatch, login_fails):
    """Two presses scheduled together: only one gets past the check. If that one then fails at
    login, the other has already been dropped, so nothing is sent; that is accepted, and the
    cleared stamp lets the next press through straight away."""
    v, logins = _Horn(), {"n": 0}

    async def login(ws, loc):
        logins["n"] += 1
        await asyncio.sleep(0)                         # the second press arrives mid-login
        if login_fails:
            raise RuntimeError("login failed")
        return v

    _fake_client_session(monkeypatch)
    monkeypatch.setattr(main, "_login_vehicle", login)
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)

    async def both():
        await asyncio.gather(main.run_command("horn"), main.run_command("horn"))

    asyncio.run(both())
    assert logins["n"] == 1
    assert v.honks == (0 if login_fails else 1)
    assert ("horn" in main._last_command) is not login_fails


def _bounded(coro):
    """Run a scenario that parks logins on an event, failing instead of hanging if a regression
    leaves a command waiting on a login nobody releases."""
    return asyncio.run(asyncio.wait_for(coro, 2))


def _blocking_login(monkeypatch, vehicle, fail=False):
    """Every login waits on the returned event, then succeeds (or raises if `fail`)."""
    gate, logins = asyncio.Event(), {"n": 0}

    async def login(ws, loc):
        logins["n"] += 1
        await gate.wait()
        if fail:
            raise RuntimeError("login timed out")
        return vehicle

    _fake_client_session(monkeypatch)
    monkeypatch.setattr(main, "_login_vehicle", login)
    return gate, logins


def test_a_repeat_after_the_window_is_ignored_while_the_first_is_still_being_sent(monkeypatch):
    """A login can take up to the 60s API timeout, so the 5s window alone let a second identical
    press through while the first was still logging in, and both reached the car."""
    v, clock = _Horn(), {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])

    async def scenario():
        gate, logins = _blocking_login(monkeypatch, v)
        first = asyncio.create_task(main.run_command("horn"))
        await asyncio.sleep(0)                         # first is now waiting in login
        clock["t"] = 1006.0                            # past the 5s window
        second = asyncio.create_task(main.run_command("horn"))
        await asyncio.sleep(0)
        gate.set()                                     # release every login, successfully
        await asyncio.gather(first, second)
        return logins["n"]

    assert _bounded(scenario()) == 1
    assert v.honks == 1


def test_a_failed_slow_login_frees_the_button_for_the_next_press(monkeypatch):
    """The repeat that arrived mid-login was dropped; once the login fails and nothing was sent,
    the next press goes through."""
    v, clock = _Horn(), {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])

    async def scenario():
        gate, logins = _blocking_login(monkeypatch, v, fail=True)
        first = asyncio.create_task(main.run_command("horn"))
        await asyncio.sleep(0)
        clock["t"] = 1006.0
        await main.run_command("horn")                 # in flight: ignored, no login
        gate.set()
        await first                                    # fails at login
        assert logins["n"] == 1
        monkeypatch.setattr(main, "_login_vehicle", lambda ws, loc: _acoro_value(v))
        clock["t"] = 1006.5
        await main.run_command("horn")

    _bounded(scenario())
    assert v.honks == 1
    assert "horn" not in main._in_flight


async def _acoro_value(value):
    return value


@pytest.mark.parametrize("stage", ["login", "action"])
def test_a_cancelled_command_does_not_wedge_the_button(monkeypatch, stage):
    """A cancelled task must clear the in-flight mark. Before dispatch nothing was sent, so the
    stamp goes too and the next press is sent at once; once the action has started the outcome is
    unknown, so the stamp stays and the 5s window applies."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])

    class Hangs(_Horn):
        async def start_horn(self):
            self.honks += 1
            if self.honks == 1 and stage == "action":
                await asyncio.Event().wait()

    v = Hangs()

    async def scenario():
        gate, _ = _blocking_login(monkeypatch, v)
        if stage == "action":
            gate.set()
        task = asyncio.create_task(main.run_command("horn"))
        for _ in range(3):
            await asyncio.sleep(0)                     # reach the login or the action
        assert "horn" in main._in_flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "horn" not in main._in_flight
        gate.set()
        before = v.honks
        clock["t"] = 1001.0
        await main.run_command("horn")                 # inside the 5s window
        return v.honks - before

    assert _bounded(scenario()) == (1 if stage == "login" else 0)


# --------------------------------------------------------------------------- #
# charge-limit numbers (set_battery_soc)
# --------------------------------------------------------------------------- #
class SocVehicle(FakeVehicle):
    def __init__(self):
        self.soc_set = None

    async def get_battery_soc(self):
        return ns(socTarget=80, socMin=20)

    async def set_battery_soc(self, *, min, target):
        self.soc_set = (min, target)


def _login_as(monkeypatch, vehicle):
    _fake_client_session(monkeypatch)

    async def fake_login(ws, locale):
        return vehicle

    monkeypatch.setattr(main, "_login_vehicle", fake_login)


def test_set_soc_target_sends_both_limits(monkeypatch):
    v = SocVehicle()
    _login_as(monkeypatch, v)
    asyncio.run(main.run_command("soc_target", "90"))
    assert v.soc_set == (20, 90)         # min unchanged, target updated


def test_set_soc_min_sends_both_limits(monkeypatch):
    v = SocVehicle()
    _login_as(monkeypatch, v)
    asyncio.run(main.run_command("soc_min", "30"))
    assert v.soc_set == (30, 80)         # target unchanged, min updated


def test_set_soc_ignores_non_numeric(monkeypatch):
    v = SocVehicle()
    _login_as(monkeypatch, v)
    asyncio.run(main.run_command("soc_target", "not-a-number"))
    assert v.soc_set is None             # never written


def test_set_soc_bails_when_opposing_limit_missing(monkeypatch):
    class NoLimits(SocVehicle):
        async def get_battery_soc(self):
            return ns(socTarget=None, socMin=None)

    v = NoLimits()
    _login_as(monkeypatch, v)
    asyncio.run(main.run_command("soc_target", "90"))
    assert v.soc_set is None             # bailed: current limits unavailable


def test_concurrent_soc_sets_do_not_clobber(monkeypatch):
    car = {"min": 20, "target": 80}

    class V:
        async def get_battery_soc(self):
            return ns(socMin=car["min"], socTarget=car["target"])

        async def set_battery_soc(self, *, min, target):
            await asyncio.sleep(0)               # yield — interleaves without the lock
            car["min"], car["target"] = min, target

    _fake_client_session(monkeypatch)

    async def fake_login(ws, locale):
        return V()

    monkeypatch.setattr(main, "_login_vehicle", fake_login)

    async def both():
        await asyncio.gather(main.run_command("soc_min", "30"),
                             main.run_command("soc_target", "90"))

    asyncio.run(both())
    assert car == {"min": 30, "target": 90}      # both survived -> writes serialised


def test_set_soc_error_is_swallowed(monkeypatch):
    _fake_client_session(monkeypatch)

    async def boom(ws, locale):
        raise RuntimeError("login failed")

    monkeypatch.setattr(main, "_login_vehicle", boom)
    asyncio.run(main.run_command("soc_target", "90"))   # no raise


def test_numbers_not_debounced(monkeypatch):
    # A number set must not be dropped by the button debounce window.
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    v = SocVehicle()
    _login_as(monkeypatch, v)
    asyncio.run(main.run_command("soc_target", "70"))
    asyncio.run(main.run_command("soc_target", "75"))   # immediate repeat still applies
    assert v.soc_set == (20, 75)


def test_detect_supported_adds_soc_levels(monkeypatch):
    class V:
        def supports_endpoint(self, ep):
            return ep == main.SOC_ENDPOINT

    supported = asyncio.run(main.detect_supported(FakeVSession(V())))
    assert main.SOC_ENDPOINT in supported


# --------------------------------------------------------------------------- #
# detect_supported (success path)
# --------------------------------------------------------------------------- #
def test_detect_supported_success(monkeypatch):
    class V:
        def __init__(self, s):
            self._s = s

        def supports_endpoint(self, ep):
            return ep in self._s

    vs = FakeVSession(V({"pressure", "actions/horn-start", "actions/hvac-start"}))
    supported = asyncio.run(main.detect_supported(vs))
    assert "pressure" in supported
    assert "charge-mode" not in supported          # not supported -> discarded
    assert "actions/horn-start" in supported
    assert "actions/charge-start" not in supported  # forbidden -> not added


# --------------------------------------------------------------------------- #
# health server + account resolution
# --------------------------------------------------------------------------- #
def test_health_server_serves_200():
    import aiohttp

    async def scenario():
        runner = await main.start_health_server()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{main.HEALTH_PORT}/healthz") as r:
                    assert r.status == 200 and (await r.text()) == "ok"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_status_panel_routes(monkeypatch):
    import aiohttp

    async def scenario():
        main._LATEST.update(ok=True, version="testver", supported=["x", "y"],
                            data={"battery_level": 42, "plug_status": "Plugged"})
        runner = await main.start_health_server()
        base = f"http://127.0.0.1:{main.HEALTH_PORT}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}/api/state") as r:        # JSON snapshot
                    assert r.status == 200
                    body = await r.json()
                    assert body["ok"] is True and body["version"] == "testver"
                    assert body["data"]["battery_level"] == 42
                async with s.get(f"{base}/") as r:                 # panel HTML
                    assert r.status == 200 and "Alpine A290" in (await r.text())
                monkeypatch.setattr(main, "_PANEL_FILE", "/no/such/panel.html")
                async with s.get(f"{base}/") as r:                 # graceful fallback
                    assert r.status == 200 and "unavailable" in (await r.text())
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_resolve_account_from_env(monkeypatch):
    monkeypatch.setenv("A290_ACCOUNT_ID", "acct-1")
    assert asyncio.run(main.resolve_account(object())) == "acct-1"


def test_resolve_account_autodiscovers(monkeypatch):
    # No VIN configured -> fall back to the MYRENAULT account (legacy safety net).
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("A290_VIN", raising=False)

    class Client:
        async def get_person(self):
            return ns(accounts=[ns(accountType="OTHER", accountId="x"),
                                ns(accountType="MYRENAULT", accountId="acct-2")])

    assert asyncio.run(main.resolve_account(Client())) == "acct-2"
    # the discovered id is captured so redact() can mask it in later error URLs
    assert config._DISCOVERED_ACCOUNT_ID == "acct-2"
    assert "acct-2" not in config.redact("boom /accounts/acct-2/vehicles/V/x")


def test_resolve_account_matches_vin_garage(monkeypatch):
    # The A290 lives under MYALPINE, not MYRENAULT — pick the account whose garage holds the VIN,
    # not the one whose type happens to be MYRENAULT.
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("A290_VIN", "vf1stubvin0000000")  # lower-case: match must be case-insensitive
    garages = {"acct-renault": [], "acct-alpine": [ns(vin="VF1STUBVIN0000000")]}

    class Account:
        def __init__(self, aid):
            self.aid = aid

        async def get_vehicles(self):
            return ns(vehicleLinks=garages[self.aid])

    class Client:
        async def get_person(self):
            return ns(accounts=[ns(accountType="MYRENAULT", accountId="acct-renault"),
                                ns(accountType="MYALPINE", accountId="acct-alpine")])

        async def get_api_account(self, aid):
            return Account(aid)

    assert asyncio.run(main.resolve_account(Client())) == "acct-alpine"
    assert config._DISCOVERED_ACCOUNT_ID == "acct-alpine"


def test_resolve_account_skips_failing_account(monkeypatch):
    # A garage lookup that errors on one account must not abort discovery — keep scanning.
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("A290_VIN", "VF1STUBVIN0000000")

    class Account:
        def __init__(self, aid):
            self.aid = aid

        async def get_vehicles(self):
            if self.aid == "acct-bad":
                raise RuntimeError("kamereon 500")
            return ns(vehicleLinks=[ns(vin="VF1STUBVIN0000000")])

    class Client:
        async def get_person(self):
            return ns(accounts=[ns(accountType="SFDC", accountId="acct-bad"),
                                ns(accountType="MYALPINE", accountId="acct-alpine")])

        async def get_api_account(self, aid):
            return Account(aid)

    assert asyncio.run(main.resolve_account(Client())) == "acct-alpine"


def test_login_vehicle(monkeypatch):
    for k, v in {"A290_USERNAME": "u", "A290_PASSWORD": "p", "A290_VIN": "V",
                 "A290_ACCOUNT_ID": "acct"}.items():
        monkeypatch.setenv(k, v)

    class Session:
        async def login(self, u, p):
            pass

    class Account:
        async def get_api_vehicle(self, vin):
            return "VEHICLE"

    class Client:
        def __init__(self, **k):
            self.session = Session()

        async def get_api_account(self, aid):
            return Account()

    monkeypatch.setattr(main, "RenaultClient", lambda **k: Client())
    assert asyncio.run(main._login_vehicle(object(), "en_GB")) == "VEHICLE"


def test_save_state_swallows_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    main.save_state({"a": 1})   # logged + swallowed


def test_vehicle_session_invalidate_swallows_close_error():
    class BadSession:
        async def close(self):
            raise RuntimeError("close failed")

    async def scenario():
        vs = main.VehicleSession("en_GB")
        vs._websession = BadSession()
        await vs.invalidate()
        assert vs._websession is None

    asyncio.run(scenario())


def test_detect_supported_handles_probe_errors():
    class V:
        def supports_endpoint(self, ep):
            raise RuntimeError("probe boom")

    supported = asyncio.run(main.detect_supported(FakeVSession(V())))
    # Data endpoints stay optimistic on a detection failure EXCEPT the pessimistic ones
    # (hvac-settings), which answer 502000 on every poll when absent.
    assert supported == set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS   # data defaults kept, no actions added


def test_resolve_account_raises_without_myrenault(monkeypatch):
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("A290_VIN", raising=False)

    class Client:
        async def get_person(self):
            return ns(accounts=[ns(accountType="OTHER", accountId="x")])

    with pytest.raises(RuntimeError):
        asyncio.run(main.resolve_account(Client()))


# --------------------------------------------------------------------------- #
# main() — one happy + one failing iteration, driven by a one-shot stop event
# --------------------------------------------------------------------------- #
class _OneShotEvent:
    def __init__(self):
        self.n = 0

    def is_set(self):
        self.n += 1
        return self.n > 1          # False on the first check, True after one iteration

    def set(self):
        pass

    async def wait(self):
        raise asyncio.TimeoutError   # exercise the inter-poll backoff/timeout branch


def _wire_main(monkeypatch, poll):
    for k in ("A290_USERNAME", "A290_PASSWORD", "A290_VIN", "MQTT_HOST"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("A290_POLL_INTERVAL", "60")

    class FakeClient:
        """Records publishes. A no-op publish() makes the main-loop tests coverage-only: they
        prove main() runs without asserting anything about what it emits, which is how a wrong
        topic write survived here while the R5 twin's equivalent test caught it."""

        def __init__(self):
            self.pubs = []

        def publish(self, topic, payload="", retain=False):
            self.pubs.append((topic, payload))

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    class FakeVS:
        def __init__(self, locale):
            self.locale = locale

        async def invalidate(self):
            pass

        async def close(self):
            pass

    async def fake_detect(vs):
        return set()

    async def fake_health():
        return ns(cleanup=_acoro)

    monkeypatch.setattr(main, "VehicleSession", FakeVS)
    monkeypatch.setattr(main, "detect_supported", fake_detect)
    fc = FakeClient()
    monkeypatch.setattr(mqtt, "mqtt_connect", lambda: fc)
    monkeypatch.setattr(mqtt, "publish_discovery", lambda *a, **k: None)
    monkeypatch.setattr(main, "start_health_server", fake_health)
    monkeypatch.setattr(main.deploy, "run_deploy", _acoro)
    monkeypatch.setattr(main, "poll_once", poll)
    monkeypatch.setattr(main.asyncio, "Event", _OneShotEvent)
    return fc


async def _acoro(*a, **k):
    return None


def test_main_one_successful_iteration(monkeypatch):
    async def poll(vs, state, cap, sup, du):
        return ({"battery_level": 50, "plug_status": "Connected", "charging": "off",
                 "plug_suspect": "off"}, {"latitude": 1, "longitude": 2})

    fc = _wire_main(monkeypatch, poll)
    asyncio.run(main.main())

    topics = [t for t, _ in fc.pubs]
    assert mqtt.STATE_TOPIC in topics
    assert mqtt.ATTR_TOPIC in topics                  # location attributes published
    # ...and NOTHING on the tracker state topic. A state payload becomes the entity's
    # location_name, which outranks the lat/lon attributes above and would pin the tracker to
    # that value permanently -- the bug this add-on shipped until 1.21.0.
    assert mqtt.TRACKER_STATE_TOPIC not in topics
    assert (mqtt.AVAIL_TOPIC, "offline") in fc.pubs   # clean shutdown


def test_main_handles_failing_poll(monkeypatch):
    async def poll(vs, state, cap, sup, du):
        raise RuntimeError("403 forbidden")   # exercises the except/backoff branch

    fc = _wire_main(monkeypatch, poll)
    asyncio.run(main.main())
    published = [json.loads(p) for t, p in fc.pubs if t == mqtt.STATE_TOPIC]
    assert published and all(d["api_auth_failure"] == "on" for d in published)
    assert main._LATEST["data"]["api_auth_failure"] == "on"     # the status panel agrees


def test_main_redacts_secret_in_error_snapshot(monkeypatch):
    async def poll(vs, state, cap, sup, du):
        # an error carrying the VIN (as a real Kamereon URL error would)
        raise RuntimeError("403 at https://api/accounts/A/vehicles/SECRETVIN123/charges")

    _wire_main(monkeypatch, poll)
    monkeypatch.setenv("A290_VIN", "SECRETVIN123")
    asyncio.run(main.main())
    # the status-panel snapshot (served unauth to co-tenant containers) must not carry the VIN
    assert "SECRETVIN123" not in main._LATEST.get("error", "")
    assert "***" in main._LATEST.get("error", "")


def test_main_exits_on_missing_config(monkeypatch):
    for k in ("A290_USERNAME", "A290_PASSWORD", "A290_VIN", "MQTT_HOST"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(main.main())


class _StopAfterPolls:
    """Stands in for main()'s stop Event: the loop ends once `polls` reaches `limit`."""
    polls, limit = {"n": 0}, 0

    def is_set(self):
        return self.polls["n"] >= self.limit

    def set(self):
        pass

    async def wait(self):   # never awaited: the test's wait_for records its timeout instead
        pass


@pytest.mark.parametrize("interval,expected", [
    (3600, [3600, 3600, 3600, 3600]),        # a slow interval must not retry faster when failing
    (300, [300, 600, 1200, 1800, 1800]),     # a fast one still backs off to the 30-minute cap
])
def test_failure_backoff_never_drops_below_the_interval(monkeypatch, interval, expected):
    polls = {"n": 0}

    async def poll(vs, state, cap, sup, du):
        polls["n"] += 1
        raise RuntimeError("Kamereon unreachable")

    _wire_main(monkeypatch, poll)
    monkeypatch.setenv("A290_POLL_INTERVAL", str(interval))
    monkeypatch.setattr(_StopAfterPolls, "polls", polls)
    monkeypatch.setattr(_StopAfterPolls, "limit", len(expected))
    monkeypatch.setattr(main.asyncio, "Event", _StopAfterPolls)
    delays, real_wait_for = [], asyncio.wait_for

    async def fake_wait_for(aw, timeout):
        if getattr(aw, "__qualname__", "") == "_StopAfterPolls.wait":   # the inter-poll sleep
            delays.append(timeout)
            aw.close()
            raise asyncio.TimeoutError
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(main.asyncio, "wait_for", fake_wait_for)
    asyncio.run(main.main())
    assert delays == expected


# --------------------------------------------------------------------------- #
# every Renault API session is bounded, so a hung connection can't wedge a poll or command
# --------------------------------------------------------------------------- #
class _TimedSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def close(self):
        pass


class _TimedVehicle:
    async def start_horn(self):
        pass

    async def get_battery_soc(self):
        return ns(socMin=20, socTarget=80)

    async def set_battery_soc(self, min, target):
        pass


def _poll_session():
    asyncio.run(main.VehicleSession("en_GB").vehicle())


def _command_session():
    asyncio.run(main.run_command("horn"))


def _soc_session():
    asyncio.run(main.set_soc_level(sorted(main.NUMBER_CMDS)[0], "70"))


SESSION_PATHS = {"poll": _poll_session, "command": _command_session, "charge_limit": _soc_session}


@pytest.mark.parametrize("path", sorted(SESSION_PATHS))
def test_renault_session_is_created_with_the_api_timeout(monkeypatch, path):
    made = []

    def factory(*a, **k):
        made.append(k)
        return _TimedSession()

    async def login(ws, locale):
        return _TimedVehicle()

    monkeypatch.setattr(main.aiohttp, "ClientSession", factory)
    monkeypatch.setattr(main, "_login_vehicle", login)
    SESSION_PATHS[path]()
    assert len(made) == 1
    timeout = made[0].get("timeout")
    assert isinstance(timeout, main.aiohttp.ClientTimeout), made[0]
    assert (timeout.total, timeout.connect) == (60, 10)


def test_no_client_session_in_main_escapes_the_timeout_tests():
    """A new session in main.py fails here until it passes a timeout and gets a SESSION_PATHS entry."""
    tree = ast.parse(Path(main.__file__).read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "ClientSession"]
    assert len(calls) == len(SESSION_PATHS)
    assert all(any(kw.arg == "timeout" for kw in c.keywords) for c in calls)


# --------------------------------------------------------------------------- #
# Parity with the R5 twin: state round-trip, cached-login failure modes, and the
# sync/async supports_endpoint wrapper (these guard the same code paths R5 tests).
# --------------------------------------------------------------------------- #
def test_state_roundtrip(monkeypatch, tmp_path):
    f = tmp_path / "state.json"
    monkeypatch.setattr(main, "STATE_FILE", str(f))
    assert main.load_state() == {}            # missing file
    main.save_state({"a": 1})
    assert main.load_state() == {"a": 1}
    f.write_text("{not json")
    assert main.load_state() == {}            # corrupt file


def test_vehicle_session_invalidates_on_login_failure(monkeypatch):
    closed = {"n": 0}

    class FakeSession:
        async def close(self):
            closed["n"] += 1

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: FakeSession())

    async def boom(ws, loc):
        raise RuntimeError("login refused")

    monkeypatch.setattr(main, "_login_vehicle", boom)

    async def scenario():
        vs = main.VehicleSession("en_GB")
        with pytest.raises(RuntimeError):
            await vs.vehicle()
        assert closed["n"] == 1          # half-open session was closed
        assert vs._vehicle is None

    asyncio.run(scenario())


def test_invalidate_swallows_close_error():
    class BadSession:
        async def close(self):
            raise RuntimeError("already closed")

    async def scenario():
        vs = main.VehicleSession("en_GB")
        vs._websession = BadSession()
        await vs.invalidate()            # must not raise
        assert vs._websession is None

    asyncio.run(scenario())


def test_supports_handles_sync_and_async():
    class SyncV:
        def supports_endpoint(self, ep):
            return True

    class AsyncV:
        def supports_endpoint(self, ep):
            async def _a():
                return True
            return _a()

    async def scenario():
        assert await main._supports(SyncV(), "x") is True
        assert await main._supports(AsyncV(), "x") is True

    asyncio.run(scenario())


def test_run_command_rejects_refresh_location_when_location_disabled(monkeypatch):
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", True)
    called = {"n": 0}

    async def fake_login(ws, locale):
        called["n"] += 1
        return object()

    monkeypatch.setattr(main, "_login_vehicle", fake_login)
    (cmd,) = tuple(main.LOCATION_CMDS)
    asyncio.run(main.run_command(cmd))
    assert called["n"] == 0            # rejected before any login/dispatch


@pytest.mark.parametrize("publish_location,enable_refresh,dispatched", [
    (True,  True,  True),    # opted in -> the only combination that reaches the car
    (True,  False, False),   # the shipped default
    (False, True,  False),
    (False, False, False),
])
def test_run_command_gates_refresh_location_on_the_opt_in(
        monkeypatch, publish_location, enable_refresh, dispatched):
    """Withholding the discovery button is not enough on its own: the entity is pressable from
    voice, automations and any dashboard, all of which publish to the same command topic. So the
    command itself must be rejected, and rejected BEFORE the login - a refused press must not even
    authenticate, let alone reach actions/refresh-location."""
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", publish_location)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", enable_refresh)
    logins = {"n": 0}
    refreshed = {"n": 0}

    class Veh:
        async def refresh_location(self):
            refreshed["n"] += 1

    async def fake_login(ws, locale):
        logins["n"] += 1
        return Veh()

    monkeypatch.setattr(main, "_login_vehicle", fake_login)
    (cmd,) = tuple(main.LOCATION_CMDS)
    asyncio.run(main.run_command(cmd))

    assert refreshed["n"] == (1 if dispatched else 0)
    assert logins["n"] == (1 if dispatched else 0)


def test_poll_once_skips_hvac_settings_when_the_car_does_not_advertise_it():
    """A5E1AE (A290) does not list hvac-settings among its supported endpoints, and calling it
    anyway returned errorCode 502000 on every poll - a warning every five minutes, forever.
    Gating it means no call and no log spam; the two climate-schedule fields are simply absent."""
    called = []

    class _V(FakeVehicle):
        async def get_hvac_settings(self):
            called.append(1)
            raise AssertionError("hvac-settings must not be called when unsupported")

    data, _ = asyncio.run(
        main.poll_once(FakeVSession(_V()), {}, 52.0, {"pressure", "charge-mode"}, "km"))
    assert not called
    assert "climate_schedule_mode" not in data and "climate_ready_time" not in data


def test_sentinel_fix_does_not_advance_gps_last_activity():
    """binary_sensor.a290_gps_stale is templated on sensor.*_gps_last_activity. Writing the
    sentinel's fresh timestamp would disarm the staleness guard at the moment the position became
    unknown — fixing the coordinates while silencing the sensor that reports the problem.
    Found by dual review 2026-09-06, after the first cut of the fix did exactly that."""
    class _V(FakeVehicle):
        async def get_location(self):
            return types.SimpleNamespace(gpsLatitude=91, gpsLongitude=181,
                                         lastUpdateTime="2026-09-06T17:37:31Z")

    data, loc_attrs = asyncio.run(
        main.poll_once(FakeVSession(_V()), {}, 52.0,
                       {"pressure", "charge-mode", "hvac-settings"}, "km"))
    assert loc_attrs is None                      # coordinates rejected
    assert "gps_last_activity" not in data        # and the guard is NOT disarmed


def test_valid_fix_does_advance_gps_last_activity():
    class _V(FakeVehicle):
        async def get_location(self):
            return types.SimpleNamespace(gpsLatitude=51.5, gpsLongitude=-0.1,   # synthetic: valid, in-range, deliberately low-precision
                                         lastUpdateTime="2026-09-06T17:37:31Z")

    data, loc_attrs = asyncio.run(
        main.poll_once(FakeVSession(_V()), {}, 52.0,
                       {"pressure", "charge-mode", "hvac-settings"}, "km"))
    assert loc_attrs is not None
    assert data["gps_last_activity"] == "2026-09-06T17:37:31Z"


def test_detection_failure_leaves_hvac_settings_unsupported():
    """Optional data endpoints default to supported when detection cannot run. hvac-settings must
    not: it answers 502000 on every poll here, so an optimistic default turns one transient login
    failure into a warning every five minutes until restart."""
    class _Broken:
        async def vehicle(self):
            raise RuntimeError("login failed")
        async def invalidate(self):
            pass

    supported = asyncio.run(main.detect_supported(_Broken()))
    assert "hvac-settings" not in supported
    assert "pressure" in supported and "charge-mode" in supported   # others stay optimistic


def test_pessimistic_endpoint_is_restored_when_the_car_does_support_it():
    """hvac-settings starts outside the supported set, so the probe loop must be able to ADD it
    back — a discard-only loop would leave it dark forever on cars that do support it, defeating
    the self-healing the pessimistic default is supposed to preserve. Dual review, 2026-09-06."""
    class _Vs:
        async def vehicle(self):
            class _V:
                async def supports_endpoint(self, ep):
                    return True
            return _V()
        async def invalidate(self):
            pass

    supported = asyncio.run(main.detect_supported(_Vs()))
    assert "hvac-settings" in supported


def test_pessimistic_endpoint_stays_out_when_the_car_does_not_support_it():
    class _Vs:
        async def vehicle(self):
            class _V:
                async def supports_endpoint(self, ep):
                    return ep != "hvac-settings"
            return _V()
        async def invalidate(self):
            pass

    supported = asyncio.run(main.detect_supported(_Vs()))
    assert "hvac-settings" not in supported
    assert "pressure" in supported


def test_rejected_fix_carries_the_last_good_timestamp_forward():
    """`data` is published as a COMPLETE retained state document, so dropping the key renders the
    sensor empty; the stale guard's `t is not none` then short-circuits to "not stale" — the same
    disarm as advancing it, by the opposite route. Carrying the last usable fix time forward is
    the only behaviour that leaves the guard armed. Dual review, 2026-09-06."""
    class _V(FakeVehicle):
        async def get_location(self):
            return types.SimpleNamespace(gpsLatitude=91, gpsLongitude=181,
                                         lastUpdateTime="2026-09-06T17:37:31Z")

    st = {"gps_last_activity": "2026-09-02T11:43:14Z"}
    data, loc_attrs = asyncio.run(
        main.poll_once(FakeVSession(_V()), st, 52.0,
                       {"pressure", "charge-mode", "hvac-settings"}, "km"))
    assert loc_attrs is None
    assert data["gps_last_activity"] == "2026-09-02T11:43:14Z"   # old, not the sentinel's


def test_valid_fix_records_the_timestamp_for_later_carry_forward():
    class _V(FakeVehicle):
        async def get_location(self):
            return types.SimpleNamespace(gpsLatitude=51.5, gpsLongitude=-0.1,   # synthetic: valid, in-range, deliberately low-precision
                                         lastUpdateTime="2026-09-06T17:37:31Z")

    st = {}
    asyncio.run(main.poll_once(FakeVSession(_V()), st, 52.0,
                               {"pressure", "charge-mode", "hvac-settings"}, "km"))
    assert st["gps_last_activity"] == "2026-09-06T17:37:31Z"


def test_rejected_fix_falls_back_to_the_last_published_timestamp():
    """state['gps_last_activity'] is introduced by this change, so the first poll after an upgrade
    has no persisted value. If that poll is a sentinel — the case on the vehicle that prompted this
    fix — the key would be omitted and the guard would go quiet. Fall back to the last value this
    process published. Dual review, 2026-09-06."""
    class _V(FakeVehicle):
        async def get_location(self):
            return types.SimpleNamespace(gpsLatitude=91, gpsLongitude=181,
                                         lastUpdateTime="2026-09-06T17:37:31Z")

    main._LATEST.update(data={"gps_last_activity": "2026-09-02T11:43:14Z"})
    try:
        data, loc_attrs = asyncio.run(
            main.poll_once(FakeVSession(_V()), {}, 52.0,
                           {"pressure", "charge-mode", "hvac-settings"}, "km"))
        assert loc_attrs is None
        assert data["gps_last_activity"] == "2026-09-02T11:43:14Z"
    finally:
        main._LATEST.update(data={})


def test_hvac_breaker_stops_calling_after_repeated_failures():
    """v1.23.0 gated hvac-settings on supports_endpoint(), which returns True on this model while
    every call answers 502000 — so the warning continued every five minutes. What the endpoint
    does is the only reliable signal. Bounded log spam, then silence."""
    main._BREAKERS.clear()
    main._BREAKER_FAILS.clear()
    calls = []

    class _V(FakeVehicle):
        async def get_hvac_settings(self):
            calls.append(1)
            raise RuntimeError("('err.tech.vcps.ev.hvac-settings.error', '502000')")

    eps = {"pressure", "charge-mode", "hvac-settings"}
    try:
        for _ in range(6):
            asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == main.BREAKER_TRIP, "must stop calling once the breaker trips"
        assert main._BREAKERS.get("hvac-settings") is True
    finally:
        main._BREAKERS.clear()
    main._BREAKER_FAILS.clear()


def test_hvac_breaker_resets_on_success():
    """A server-side fix must be picked up without intervention, so any success clears the count."""
    main._BREAKERS.clear()
    main._BREAKER_FAILS.clear()
    state = {"fail": True}

    class _V(FakeVehicle):
        async def get_hvac_settings(self):
            if state["fail"]:
                raise RuntimeError("502000")
            return types.SimpleNamespace(raw_data={})

    eps = {"pressure", "charge-mode", "hvac-settings"}
    try:
        for _ in range(main.BREAKER_TRIP - 1):
            asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert main._BREAKER_FAILS["hvac-settings"] == main.BREAKER_TRIP - 1
        state["fail"] = False
        asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert main._BREAKER_FAILS["hvac-settings"] == 0
        assert not main._BREAKERS.get("hvac-settings")
    finally:
        main._BREAKERS.clear()
    main._BREAKER_FAILS.clear()


def test_tripped_breaker_retries_and_re_enables_on_recovery():
    """A tripped breaker that never retried would make "re-enabled automatically" false: the reset
    lives inside the call it stops making, so recovery would need a restart nobody knows to do.
    Dual review, 2026-09-06."""
    main._BREAKERS.clear()
    main._BREAKER_FAILS.clear()
    main._BREAKER_SKIPS.clear()
    calls = []
    broken = {"v": True}

    class _V(FakeVehicle):
        async def get_hvac_settings(self):
            calls.append(1)
            if broken["v"]:
                raise RuntimeError("502000")
            return types.SimpleNamespace(raw_data={})

    eps = {"pressure", "charge-mode", "hvac-settings"}
    try:
        for _ in range(main.BREAKER_TRIP):
            asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert main._BREAKERS.get("hvac-settings") is True
        tripped_at = len(calls)

        # skipped while tripped ...
        for _ in range(main.BREAKER_RETRY_EVERY - 1):
            asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == tripped_at, "must not call while tripped"

        # ... then probes once, and recovery clears the breaker
        broken["v"] = False
        asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == tripped_at + 1
        assert "hvac-settings" not in main._BREAKERS
    finally:
        main._BREAKERS.clear()
        main._BREAKER_FAILS.clear()
        main._BREAKER_SKIPS.clear()


def test_failed_probe_stays_tripped_and_silent(caplog):
    """Spam must stay bounded at BREAKER_TRIP lines however long the outage runs."""
    main._BREAKERS.clear()
    main._BREAKER_FAILS.clear()
    main._BREAKER_SKIPS.clear()

    class _V(FakeVehicle):
        async def get_hvac_settings(self):
            raise RuntimeError("502000")

    eps = {"pressure", "charge-mode", "hvac-settings"}
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(main.BREAKER_TRIP + main.BREAKER_RETRY_EVERY + 2):
                asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert caplog.text.count("hvac-settings") <= main.BREAKER_TRIP
        assert main._BREAKERS.get("hvac-settings") is True
    finally:
        main._BREAKERS.clear()
        main._BREAKER_FAILS.clear()
        main._BREAKER_SKIPS.clear()


# --- data_stale measures the CAR, poll_failing measures the FEED (v1.24.0) -------------------
# Regression cover for the defect where data_stale was wired to poll success and forced "off"
# on every successful poll: a vehicle silent since 2026-09-04 was polled successfully every
# five minutes for 68 hours and published as healthy, showing 55% while the car was at 83%.

def _fresh(state, payload_iso, last_ok, hours=6):
    return main.freshness_fields(state, payload_iso, hours * 3600, last_ok)


def test_data_stale_fires_when_the_car_goes_quiet_though_every_poll_succeeds():
    """THE reported incident. Polls all succeed, so poll_failing is correctly off — and the
    68-hour-old payload must still raise data_stale. Before the fix this published "off"."""
    now = main.now_ts()
    out = _fresh({}, main.iso(now - 68 * 3600), last_ok=now)
    assert out["data_stale"] == "on"
    assert out["poll_failing"] == "off"


def test_data_stale_is_off_while_the_car_is_reporting():
    now = main.now_ts()
    assert _fresh({}, main.iso(now - 600), last_ok=now) == {"data_stale": "off",
                                                            "poll_failing": "off"}


def test_poll_failure_ages_the_last_known_car_timestamp():
    """No new payload, so the last car timestamp carries forward and keeps ageing. An outage
    can neither make stale data look fresh nor invent staleness."""
    now = main.now_ts()
    state = {"last_payload_ts": now - 8 * 3600}
    out = _fresh(state, None, last_ok=now - 8 * 3600)
    assert out == {"data_stale": "on", "poll_failing": "on"}


def test_a_transient_failure_raises_neither_signal():
    """One missed poll against a car that reported recently is not an alarm — poll_failing keeps
    the stale_hours tolerance data_stale used to apply on this path."""
    now = main.now_ts()
    out = _fresh({"last_payload_ts": now - 600}, None, last_ok=now - 300)
    assert out == {"data_stale": "off", "poll_failing": "off"}


def test_data_stale_is_omitted_until_a_car_timestamp_has_been_seen():
    """A fresh install whose first polls fail has no basis for the claim. The key is omitted so
    HA reads `unknown`; publishing "off" would restate the bug this replaces."""
    out = _fresh({}, None, last_ok=0)
    assert "data_stale" not in out
    assert out["poll_failing"] == "on"


def test_a_seen_car_timestamp_is_persisted_for_the_next_failure():
    now = main.now_ts()
    state = {}
    _fresh(state, main.iso(now - 600), last_ok=now)
    assert state["last_payload_ts"] == pytest.approx(now - 600, abs=2)


def test_last_updated_is_not_fabricated_when_the_payload_carries_no_timestamp():
    """`or iso(now_ts())` stamped a timestamp-less payload as arriving this instant, which made
    staleness permanently unfireable — the GPS-sentinel failure in another guise."""
    class _B(FakeBattery):
        def __init__(self):
            super().__init__()
            self.timestamp = None

    class _V(FakeVehicle):
        async def get_battery_status(self):
            return _B()

    data, _ = asyncio.run(
        main.poll_once(FakeVSession(_V()), {}, 52.0, {"pressure", "charge-mode"}, "km"))
    assert data["last_updated"] is None


def test_main_publishes_the_car_timestamp_verdict_not_the_poll_verdict(monkeypatch):
    """End-to-end through main(): a successful poll returning an old payload must publish
    data_stale on. This is the assertion that would have caught the original defect."""
    old = main.iso(main.now_ts() - 68 * 3600)

    async def poll(vs, state, cap, sup, du):
        return ({"battery_level": 55, "last_updated": old}, None)

    monkeypatch.setattr(main, "save_state", lambda s: None)
    monkeypatch.setattr(main, "load_state", dict)
    fc = _wire_main(monkeypatch, poll)
    asyncio.run(main.main())

    published = [json.loads(p) for t, p in fc.pubs if t == mqtt.STATE_TOPIC]
    assert published, "no state document published"
    assert published[0]["data_stale"] == "on"
    assert published[0]["poll_failing"] == "off"
    assert published[0]["last_successful_poll"]


def test_main_failure_path_publishes_both_signals(monkeypatch):
    async def poll(vs, state, cap, sup, du):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "save_state", lambda s: None)
    monkeypatch.setattr(main, "load_state", dict)
    fc = _wire_main(monkeypatch, poll)
    asyncio.run(main.main())

    published = [json.loads(p) for t, p in fc.pubs if t == mqtt.STATE_TOPIC]
    assert published[0]["poll_failing"] == "on"      # never succeeded in this process
    assert "data_stale" not in published[0]          # and no car timestamp to judge
    assert published[0]["api_auth_failure"] == "off"  # not every failure is a credentials one
