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
from renault_api.exceptions import NotAuthenticatedException
from renault_api.gigya.exceptions import PendingTwoFactorAuthenticationException
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.kamereon.exceptions import InvalidInputException, UnauthorizedException
from renault_mqtt import config, debug, mqtt


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


class BatteryUnauthorizedVehicle(FakeVehicle):
    async def get_battery_status(self):
        raise UnauthorizedException("err.func.wired.unauthorized", "Not authorized")


class CockpitUnauthorizedVehicle(FakeVehicle):
    async def get_cockpit(self):
        raise UnauthorizedException("err.func.wired.unauthorized", "Not authorized")


def test_unauthorized_on_the_battery_request_escapes_poll_once():
    # The battery request is the one poll_once does not guard, so Kamereon's unauthorized reply on
    # it reaches the main loop's classifier (the two tests above). This pins that half of the path.
    with pytest.raises(UnauthorizedException):
        asyncio.run(main.poll_once(FakeVSession(BatteryUnauthorizedVehicle()), {}, 52.0, set(), "km"))


def test_unauthorized_on_an_optional_request_is_swallowed_by_poll_once():
    # Each optional request catches its own errors, so the same reply is logged and the poll
    # carries on: it never reaches the classifier and the sensor stays off.
    data, _ = asyncio.run(
        main.poll_once(FakeVSession(CockpitUnauthorizedVehicle()), {}, 52.0, set(), "km"))
    assert data["battery_level"] == 60
    assert "mileage" not in data


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


def test_poll_once_debug_dump_is_redacted_and_one_shot(monkeypatch, caplog):
    monkeypatch.setitem(debug._DEBUG_STATE, "dumped", False)
    monkeypatch.setenv("A290_DEBUG_DUMP", "true")
    monkeypatch.setenv("A290_VIN", "VF1DEBUGVIN0001")
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    probes = []

    class V(FakeVehicle):
        async def get_details(self):
            probes.append("details")
            return ns(raw_data={"vin": "PRIVATEVIN", "batteryLevel": 60,
                                "diagnostic": "vehicle VF1DEBUGVIN0001"})

    with caplog.at_level(logging.WARNING, logger=debug.LOG.name):
        for _ in range(2):
            asyncio.run(main.poll_once(FakeVSession(V()), {}, 52.0, set(), "km"))
    dumps = [r for r in caplog.records if r.name == debug.LOG.name and "API DEBUG DUMP" in r.getMessage()]
    assert probes == ["details"]
    assert len(dumps) == 1
    payload = json.loads(dumps[0].getMessage().split("\n", 1)[1])
    assert payload["get_details"] == {"vin": "***", "batteryLevel": 60, "diagnostic": "vehicle ***"}
    assert "PRIVATEVIN" not in caplog.text and "VF1DEBUGVIN0001" not in caplog.text


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


def test_the_debounce_clock_is_monotonic(monkeypatch):
    monkeypatch.setattr(main.time, "monotonic", lambda: 42.0)
    assert main._debounce_now() == 42.0


class _Horn:
    def __init__(self, fail=False):
        self.fail, self.honks = fail, 0

    async def start_horn(self):
        self.honks += 1
        if self.fail:
            raise RuntimeError("action failed at the car")


def _bounded(coro):
    """Run a scenario that parks logins on an event, failing instead of hanging if a regression
    leaves a command waiting on a login nobody releases."""
    return asyncio.run(asyncio.wait_for(coro, 2))


def test_climate_stop_waits_for_the_earlier_start_login(monkeypatch):
    async def scenario():
        entered, release, submitted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        logins, actions = [], []

        class V(FakeVehicle):
            async def set_ac_start(self, temp):
                actions.append("start")

            async def set_ac_stop(self):
                actions.append("stop")

        async def login(ws, loc):
            name = asyncio.current_task().get_name()
            logins.append(name)
            if name == "start":
                entered.set()
                await release.wait()
            return V()

        async def stop():
            submitted.set()
            await main.run_command("climate_stop")

        _fake_client_session(monkeypatch)
        monkeypatch.setattr(main, "_login_vehicle", login)
        tasks = [asyncio.create_task(main.run_command("climate_start"), name="start")]
        try:
            await entered.wait()
            tasks.append(asyncio.create_task(stop(), name="stop"))
            await submitted.wait()
            blocked_logins, blocked_actions = list(logins), list(actions)
            release.set()
            await asyncio.gather(*tasks)
            assert (blocked_logins, blocked_actions, actions) == (["start"], [], ["start", "stop"]), \
                "Stop overtook the blocked Start login"
            assert logins == ["start", "stop"]
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    _bounded(scenario())


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


def test_set_soc_enforces_catalog_number_ranges(monkeypatch):
    v = SocVehicle()
    _login_as(monkeypatch, v)
    min_lo, min_hi, min_step = main.NUMBER_RANGES["soc_min"]
    tgt_lo, tgt_hi, tgt_step = main.NUMBER_RANGES["soc_target"]
    assert (min_lo, min_hi, min_step) == main.NUMBERS["a290_soc_min"][2:5]
    assert (tgt_lo, tgt_hi, tgt_step) == main.NUMBERS["a290_soc_target"][2:5]
    asyncio.run(main.run_command("soc_target", str(tgt_hi + 1)))
    asyncio.run(main.run_command("soc_min", str(min_lo + 1)))
    assert v.soc_set is None
    asyncio.run(main.run_command("soc_min", str(min_lo)))
    assert v.soc_set == (min_lo, 80)


def test_run_command_debounces_and_clears_stamp_when_login_fails(monkeypatch):
    v, logins = _Horn(), {"n": 0}
    gate = None

    async def login(ws, loc):
        logins["n"] += 1
        if gate is not None:
            await gate.wait()
        if logins["n"] == 1:
            raise RuntimeError("login failed")
        return v

    _fake_client_session(monkeypatch)
    monkeypatch.setattr(main, "_login_vehicle", login)
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "_debounce_now", lambda: clock["t"])

    async def in_flight_check():
        nonlocal gate
        gate = asyncio.Event()
        first = asyncio.create_task(main.run_command("horn"))
        await asyncio.sleep(0)
        assert "horn" in main._in_flight
        clock["t"] = 1006.0
        await main.run_command("horn")
        gate.set()
        await first
        assert "horn" not in main._in_flight

    _bounded(in_flight_check())
    gate = None
    clock["t"] = 1007.0
    asyncio.run(main.run_command("horn"))
    clock["t"] = 1008.0
    asyncio.run(main.run_command("horn"))
    assert (logins["n"], v.honks) == (2, 1)


def test_detect_supported_adds_soc_levels(monkeypatch):
    class V:
        async def supports_endpoint(self, ep):
            return ep == main.SOC_ENDPOINT

    supported = asyncio.run(main.detect_supported(FakeVSession(V())))
    assert main.SOC_ENDPOINT in supported


def test_detection_wires_catalog_defaults_probe_logging_and_pending_retry(caplog):
    calls, failed = [], []

    class V:
        async def supports_endpoint(self, ep):
            calls.append(ep)
            if len(failed) < len(main.SUPPORT_ENDPOINTS):
                failed.append(ep)
                raise RuntimeError("probe unavailable")
            return ep == main.SOC_ENDPOINT

    vs = FakeVSession(V())
    pending = set(main.SUPPORT_ENDPOINTS)
    with caplog.at_level(logging.INFO):
        supported = asyncio.run(main.detect_supported(vs, unresolved=pending))
        assert supported == set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS
        assert pending == main.SUPPORT_ENDPOINTS
        assert calls == sorted(main.SUPPORT_ENDPOINTS)
        assert not vs.invalidated
        assert asyncio.run(main.detect_supported(vs, supported, pending)) is supported
    assert supported == {main.SOC_ENDPOINT} and pending == set()
    assert calls == sorted(main.SUPPORT_ENDPOINTS) * 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"supports_endpoint({ep}) check failed: probe unavailable" for ep in sorted(main.SUPPORT_ENDPOINTS)]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == [
        f"Supported optional endpoints: {sorted(set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS)}",
        f"Supported optional endpoints: {[main.SOC_ENDPOINT]}",
    ]


@pytest.mark.parametrize("cancelled", [False, True])
def test_detection_outer_failure_adapters_preserve_sets_and_propagate_cancellation(monkeypatch, caplog, cancelled):
    error = asyncio.CancelledError() if cancelled else RuntimeError("synthetic outer failure")
    invalidated, redacted = [], []

    class VS:
        async def vehicle(self):
            raise error

        async def invalidate(self):
            invalidated.append(True)

    def redact(err):
        redacted.append(err)
        return "masked failure"

    monkeypatch.setattr(main, "redact", redact)
    supported, pending = {"known"}, set(main.SUPPORT_ENDPOINTS)
    if cancelled:
        with pytest.raises(asyncio.CancelledError) as caught:
            asyncio.run(main.detect_supported(VS(), supported, pending))
        assert caught.value is error
        assert invalidated == redacted == [] and caplog.records == []
    else:
        assert asyncio.run(main.detect_supported(VS(), supported, pending)) is supported
        assert invalidated == [True] and redacted == [error]
        assert [r.getMessage() for r in caplog.records] == [
            "Endpoint-support detection failed (keeping prior support): masked failure",
        ]
    assert supported == {"known"} and pending == main.SUPPORT_ENDPOINTS


# --------------------------------------------------------------------------- #
# health server + account resolution
# --------------------------------------------------------------------------- #
def _bound_port(runner):
    # HEALTH_PORT is patched to 0, so the OS picks a free port; read back the one it chose.
    return runner.addresses[0][1]


def test_health_server_serves_200(monkeypatch):
    import aiohttp

    monkeypatch.setattr(main, "HEALTH_PORT", 0)

    async def scenario():
        runner = await main.start_health_server()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{_bound_port(runner)}/healthz") as r:
                    assert r.status == 200 and (await r.text()) == "ok"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_status_panel_routes(monkeypatch):
    import aiohttp

    monkeypatch.setattr(main, "HEALTH_PORT", 0)

    async def scenario():
        main._LATEST.update(ok=True, version="testver", supported=["x", "y"],
                            data={"battery_level": 42, "plug_status": "Plugged"})
        runner = await main.start_health_server()
        base = f"http://127.0.0.1:{_bound_port(runner)}"
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
            assert (u, p) == ("u", "p")

    class Account:
        async def get_api_vehicle(self, vin):
            assert vin == "V"
            return "VEHICLE"

    class Client:
        def __init__(self, **k):
            assert k == {"websession": websession, "locale": "en_GB"}
            self.session = Session()

        async def get_api_account(self, aid):
            assert aid == "acct"
            return Account()

    websession = object()
    monkeypatch.setattr(main, "RenaultClient", Client)
    assert asyncio.run(main._login_vehicle(websession, "en_GB")) == "VEHICLE"


def test_save_state_swallows_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    main.save_state({"a": 1})   # logged + swallowed


def test_detect_supported_handles_probe_errors():
    class V:
        async def supports_endpoint(self, ep):
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


@pytest.fixture
def wire_main(monkeypatch, tmp_path):
    """Main-loop scenarios use real persistence in their own directory."""
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    return lambda poll: _wire_main(monkeypatch, poll)


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
            self.snapshots = self
            self.connected = True
            self.latest = None
            self.location = None
            self.closed = False

        def update(self, data, **kwargs):
            self.latest = json.dumps(data)
            if not mqtt.PUBLISH_LOCATION:
                self.location = ""
            elif "location" in kwargs:
                self.location = json.dumps(kwargs["location"]) if kwargs["location"] else ""
            self.replay()

        def replay(self):
            if self.connected and self.latest is not None and not self.closed:
                self.publish(mqtt.STATE_TOPIC, self.latest, retain=True)
                if self.location is not None:
                    self.publish(mqtt.ATTR_TOPIC, self.location, retain=True)
                self.publish(mqtt.AVAIL_TOPIC, "online", retain=True)

        def connect(self):
            self.connected = True
            mqtt.publish_discovery(self, mqtt._MQTT_CTX["supported"], mqtt._MQTT_CTX["dist_unit"])
            self.replay()

        async def close(self):
            self.closed = True
            self.publish(mqtt.AVAIL_TOPIC, "offline", retain=True)
            self.disconnect()
            self.loop_stop()

        def publish(self, topic, payload="", retain=False):
            self.pubs.append((topic, payload, retain))

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

    async def fake_detect(vs, *, unresolved):
        unresolved.clear()
        return set()

    async def fake_health(health_state=None):
        return ns(cleanup=_acoro)

    monkeypatch.setattr(main, "VehicleSession", FakeVS)
    monkeypatch.setattr(main, "detect_supported", fake_detect)
    fc = FakeClient()
    def connect(*, snapshots=False):
        assert snapshots, "The app must opt into startup snapshot delivery"
        return fc

    monkeypatch.setattr(mqtt, "mqtt_connect", connect)
    monkeypatch.setattr(mqtt, "publish_discovery", lambda *a, **k: None)
    monkeypatch.setattr(main, "start_health_server", fake_health)
    monkeypatch.setattr(main.deploy, "run_deploy", _acoro)
    monkeypatch.setattr(main, "poll_once", poll)
    monkeypatch.setattr(main.asyncio, "Event", _OneShotEvent)
    return fc


async def _acoro(*a, **k):
    return None


def _main_cycles(monkeypatch, count, after_cycle):
    """Advance at main's inter-poll wait, after publications and real persistence."""
    class Stop:
        n = 0

        def is_set(self):
            return self.n >= count

        def set(self):
            self.n = count

        async def wait(self):
            await after_cycle(self.n)
            self.n += 1
            raise asyncio.TimeoutError

    with monkeypatch.context() as scoped:
        scoped.setattr(main.asyncio, "Event", Stop)
        _bounded(main.main())


@pytest.mark.parametrize("failure", ["login", "probe"])
def test_main_recovers_discovery_after_transient_detection_failure(monkeypatch, wire_main, failure):
    real_detect, real_discovery, real_poll = main.detect_supported, mqtt.publish_discovery, main.poll_once
    probes, logins = {}, []
    expected = {"actions/horn-start", "actions/lights-start", main.SOC_ENDPOINT}

    class V(FakeVehicle):
        async def supports_endpoint(self, ep):
            probes[ep] = probes.get(ep, 0) + 1
            if failure == "probe":
                if ep in {"actions/horn-start", main.SOC_ENDPOINT} and probes[ep] <= 2:
                    raise RuntimeError("temporary probe outage")
                if ep == "actions/lights-start" and probes[ep] > 1:
                    raise RuntimeError("known-positive probe unavailable")
            return ep in expected

    class VS(FakeVSession):
        def __init__(self, locale):
            super().__init__(V(), locale)

        async def vehicle(self):
            logins.append("login")
            if failure == "login" and len(logins) == 1:
                raise RuntimeError("temporary login outage")
            return await super().vehicle()

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "VehicleSession", VS)
    monkeypatch.setattr(main, "detect_supported", real_detect)
    monkeypatch.setattr(mqtt, "publish_discovery", real_discovery)
    fc.connected = False
    snapshots = []

    async def after_cycle(n):
        if n == 0:
            fc.connect()
            if failure == "probe":
                configs = {t: p for t, p, _ in fc.pubs if t.endswith("/config")}
                assert not configs[f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/horn/config"]
                assert configs[f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/lights/config"]
        snapshots.append((dict(mqtt._MQTT_CTX, supported=set(mqtt._MQTT_CTX["supported"])),
                          json.loads((await main._panel_state(None)).text)))

    _main_cycles(monkeypatch, 3, after_cycle)
    assert all(panel["ok"] and panel["data"]["battery_level"] == 60 for _, panel in snapshots)
    configs = {t: (p, retain) for t, p, retain in fc.pubs if t.endswith("/config")}
    for domain, commands in (("button", ("horn", "lights")), ("number", ("soc_min", "soc_target"))):
        for cmd in commands:
            payload, retain = configs[f"{mqtt.DISCOVERY_PREFIX}/{domain}/{mqtt.NODE}/{cmd}/config"]
            assert payload, f"{cmd} discovery was not restored after {failure} recovery"
            assert json.loads(payload)["command_topic"] == f"{mqtt.CMD_PREFIX}{cmd}"
            assert retain is True
    ctx, panel = snapshots[-1]
    assert expected <= ctx["supported"]
    assert expected <= set(panel["supported"])
    assert probes["actions/charge-start"] == 1, "Definite false must not be probed every poll"
    assert "actions/charge-start" not in ctx["supported"]
    if failure == "probe":
        assert probes[main.SOC_ENDPOINT] >= 3, "Unknown support must be retried"
        assert all("actions/lights-start" in ctx["supported"] for ctx, _ in snapshots)


def test_timed_out_support_retry_keeps_resolved_answers_and_successful_poll(monkeypatch, wire_main):
    real_detect, real_discovery, real_poll = main.detect_supported, mqtt.publish_discovery, main.poll_once
    event_type = asyncio.Event
    probes, cancelled = {}, []

    class V(FakeVehicle):
        async def supports_endpoint(self, ep):
            probes[ep] = probes.get(ep, 0) + 1
            if ep in {"actions/horn-start", main.SOC_ENDPOINT} and probes[ep] == 1:
                raise RuntimeError("temporary probe outage")
            if ep == main.SOC_ENDPOINT:
                try:
                    await event_type().wait()
                finally:
                    cancelled.append(ep)
            return ep == "actions/horn-start"

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "VehicleSession", lambda locale: FakeVSession(V(), locale))
    monkeypatch.setattr(main, "detect_supported", real_detect)
    monkeypatch.setattr(mqtt, "publish_discovery", real_discovery)
    monkeypatch.setattr(main, "API_TIMEOUT", main.aiohttp.ClientTimeout(total=0.01))
    snapshots = []

    async def after_cycle(n):
        snapshots.append(json.loads((await main._panel_state(None)).text))

    _main_cycles(monkeypatch, 2, after_cycle)
    assert cancelled == [main.SOC_ENDPOINT, main.SOC_ENDPOINT]
    assert probes["actions/charge-start"] == 1
    assert probes["actions/horn-start"] == 2
    assert all(panel["ok"] and panel["data"]["battery_level"] == 60 for panel in snapshots)
    assert all("actions/horn-start" in panel["supported"] for panel in snapshots)
    assert "actions/horn-start" in mqtt._MQTT_CTX["supported"]
    configs = {t: p for t, p, _ in fc.pubs if t.endswith("/config")}
    assert configs[f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/horn/config"]


def test_main_retains_last_good_snapshot_through_failure_and_recovery(monkeypatch, wire_main):
    from renault_mqtt import freshness

    real_poll = main.poll_once
    base = 1767225600.0  # 2026-01-01T00:00:00Z
    clock, cycle = {"t": base}, {"n": 0}

    class V(FakeVehicle):
        async def get_battery_status(self):
            if cycle["n"] in (1, 2):
                raise RuntimeError("temporary battery outage")
            battery = FakeBattery()
            battery.timestamp = main.iso(base - 1800)
            if cycle["n"] == 3:
                battery.batteryLevel, battery.timestamp = 75, main.iso(clock["t"])
            return battery

        async def get_location(self):
            loc = await super().get_location()
            loc.lastUpdateTime = main.iso(base - 600 if cycle["n"] == 0 else clock["t"])
            return loc

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "VehicleSession", lambda locale: FakeVSession(V(), locale))
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    monkeypatch.setattr(freshness, "now_ts", lambda: clock["t"])
    monkeypatch.setenv("A290_STALE_HOURS", "1")
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    snapshots = []

    async def after_cycle(n):
        payload, retained = [(p, r) for t, p, r in fc.pubs if t == mqtt.STATE_TOPIC][-1]
        snapshots.append((json.loads(payload), json.loads((await main._panel_state(None)).text), retained))
        cycle["n"] += 1
        clock["t"] = base + (1801, 3601, 3660, 3720)[n]

    _main_cycles(monkeypatch, 4, after_cycle)
    good = snapshots[0][0]
    assert good["battery_level"] == 60
    assert good["last_updated"] == main.iso(base - 1800)
    assert good["gps_last_activity"] == main.iso(base - 600)
    for n, (data, panel, retain) in enumerate(snapshots):
        assert retain is True
        assert data == panel["data"], f"MQTT and panel disagree on cycle {n}"
        assert panel["ok"] is (n in (0, 3))
        assert data["data_stale"] == ("on" if n in (1, 2) else "off")
        assert data["poll_failing"] == ("on" if n == 2 else "off")
        if n in (1, 2):
            for key, value in good.items():
                if key not in {"data_stale", "poll_failing", "api_auth_failure"}:
                    assert data[key] == value, f"Last-good {key} changed during failure"
    recovered = snapshots[-1][0]
    assert recovered["battery_level"] == 75
    assert recovered["last_updated"] == recovered["gps_last_activity"] == main.iso(base + 3660)
    assert recovered["last_successful_poll"] == main.iso(base + 3660)


def test_main_failure_before_first_success_does_not_invent_vehicle_data(monkeypatch, wire_main):
    main._LATEST["data"] = {"battery_level": 99, "gps_last_activity": "another vehicle's snapshot"}
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, RuntimeError("service unavailable"))
    assert len(published) == 1 and invalidated == 0
    data = published[0]
    assert data == main._LATEST["data"]
    assert data["poll_failing"] == "on" and data["api_auth_failure"] == "off"
    assert not data.get("last_successful_poll")
    assert {"battery_level", "last_updated", "gps_last_activity", "data_stale"}.isdisjoint(data)


@pytest.mark.parametrize("first_failure", [False, True])
def test_main_delayed_connect_delivers_latest_poll_without_another_poll(monkeypatch, wire_main, first_failure):
    cycle = {"n": 0}
    location = {"latitude": 1, "longitude": 2}

    async def poll(*args):
        if first_failure or cycle["n"] == 1:
            raise RuntimeError("temporary outage")
        return {"battery_level": 50, "last_updated": "2026-01-01T00:00:00Z"}, location

    fc = wire_main(poll)
    fc.connected = False
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)

    async def after_cycle(n):
        assert not [p for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC]
        if n == 1:
            fc.connect()
            data = json.loads([p for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC][-1])
            assert data == main._LATEST["data"]
            assert main._LATEST["ok"] is False
            if first_failure:
                assert "battery_level" not in data and fc.location is None
            else:
                assert data["battery_level"] == 50
                assert data["last_updated"] == "2026-01-01T00:00:00Z"
                assert json.loads(fc.location) == location
        cycle["n"] += 1

    _main_cycles(monkeypatch, 2, after_cycle)
    assert fc.closed
    assert fc.pubs[-1] == (mqtt.AVAIL_TOPIC, "offline", True)


@pytest.mark.parametrize("location_enabled", [False, True])
def test_main_snapshot_location_no_fix_and_optout(monkeypatch, wire_main, location_enabled):
    real_poll = main.poll_once
    cycle = {"n": 0}

    class V(FakeVehicle):
        async def get_location(self):
            assert location_enabled, "Privacy opt-out must not fetch coordinates"
            if cycle["n"]:
                raise RuntimeError("no new fix")
            return await super().get_location()

    fc = wire_main(real_poll)
    fc.connected = False
    monkeypatch.setattr(main, "VehicleSession", lambda locale: FakeVSession(V(), locale))
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", location_enabled)
    fixes = []

    async def after_cycle(n):
        fixes.append(fc.location)
        if n == 1:
            fc.connect()
        cycle["n"] += 1

    _main_cycles(monkeypatch, 2, after_cycle)
    assert fixes[0] == fixes[1]
    attrs = [p for t, p, _ in fc.pubs if t == mqtt.ATTR_TOPIC]
    assert attrs == [fixes[0]]
    if location_enabled:
        assert set(json.loads(attrs[0])) >= {"latitude", "longitude"}
    else:
        assert attrs == [""]


def test_main_one_successful_iteration(wire_main):
    async def poll(vs, state, cap, sup, du):
        return ({"battery_level": 50, "plug_status": "Connected", "charging": "off",
                 "plug_suspect": "off"}, {"latitude": 1, "longitude": 2})

    fc = wire_main(poll)
    asyncio.run(main.main())

    topics = [t for t, _, _ in fc.pubs]
    assert mqtt.STATE_TOPIC in topics
    assert mqtt.ATTR_TOPIC in topics                  # location attributes published
    # ...and NOTHING on the tracker state topic. A state payload becomes the entity's
    # location_name, which outranks the lat/lon attributes above and would pin the tracker to
    # that value permanently -- the bug this add-on shipped until 1.21.0.
    assert mqtt.TRACKER_STATE_TOPIC not in topics
    assert [(p, retain) for t, p, retain in fc.pubs if t == mqtt.AVAIL_TOPIC] == [
        ("online", True), ("offline", True)]
    assert all(retain is True for t, _, retain in fc.pubs if t in (mqtt.STATE_TOPIC, mqtt.ATTR_TOPIC))


def test_main_handles_failing_poll(wire_main):
    async def poll(vs, state, cap, sup, du):
        raise RuntimeError("403 forbidden")   # exercises the except/backoff branch

    fc = wire_main(poll)
    asyncio.run(main.main())
    published = [json.loads(p) for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC]
    assert published and all(d["api_auth_failure"] == "on" for d in published)
    assert main._LATEST["data"]["api_auth_failure"] == "on"     # the status panel agrees
    assert all(retain is True for t, _, retain in fc.pubs if t == mqtt.STATE_TOPIC)


def _one_failed_poll(monkeypatch, wire_main, err):
    """main() through a single poll that raises `err`: the state documents it published, and how
    many times the Renault session was thrown away (invalidate) on that one failure."""
    async def poll(vs, state, cap, sup, du):
        raise err

    fc = wire_main(poll)
    seen = {"invalidated": 0}

    class CountingVS:
        def __init__(self, locale):
            self.locale = locale

        async def invalidate(self):
            seen["invalidated"] += 1

        async def close(self):
            pass

    monkeypatch.setattr(main, "VehicleSession", CountingVS)
    asyncio.run(main.main())
    return [json.loads(p) for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC], seen["invalidated"]


@pytest.mark.parametrize("err", [
    pytest.param(UnauthorizedException("err.func.wired.unauthorized", "Not authorized"), id="kamereon"),
    pytest.param(NotAuthenticatedException("Authentication expired."), id="expired-session"),
])
def test_kamereon_unauthorized_is_an_auth_failure(monkeypatch, wire_main, err):
    # str() is "('err.func.wired.unauthorized', 'Not authorized')" - none of the words the
    # keyword match looks for, so it read as an ordinary failure until 0.5.14 gave the reply a
    # class of its own. The gap predates that release.
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, err)
    assert published and all(d["api_auth_failure"] == "on" for d in published)
    assert main._LATEST["data"]["api_auth_failure"] == "on"
    assert invalidated == 1        # a rejected token is dropped at once, not on every third failure


def test_two_factor_account_is_an_auth_failure(monkeypatch, wire_main):
    # Recognised by type: its text "(403101, ...)" matched the keyword list only by the accident of
    # containing "403". The detail string here deliberately avoids every keyword.
    err = PendingTwoFactorAuthenticationException(403101, "pending verification", reg_token="r")
    err.args = ("pending verification",)
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, err)
    assert published and all(d["api_auth_failure"] == "on" for d in published)
    assert main._LATEST["data"]["api_auth_failure"] == "on"
    assert invalidated == 1


def test_other_kamereon_errors_are_not_auth_failures(monkeypatch, wire_main):
    # The type check must not widen into "any Kamereon error": a bad request is not a credentials
    # problem, the sensor stays off, and the session survives the first failure.
    err = InvalidInputException("err.func.wired.invalid-body-format", "bad body")
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, err)
    assert published and all(d["api_auth_failure"] == "off" for d in published)
    assert invalidated == 0


def test_main_redacts_secret_in_error_snapshot(monkeypatch, wire_main):
    async def poll(vs, state, cap, sup, du):
        # an error carrying the VIN (as a real Kamereon URL error would)
        raise RuntimeError("403 at https://api/accounts/A/vehicles/SECRETVIN123/charges")

    wire_main(poll)
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
def test_failure_backoff_never_drops_below_the_interval(monkeypatch, wire_main, interval, expected):
    polls = {"n": 0}

    async def poll(vs, state, cap, sup, du):
        polls["n"] += 1
        raise RuntimeError("Kamereon unreachable")

    wire_main(poll)
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
    asyncio.run(main.set_soc_level("soc_min", "30"))


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


def test_supports_awaits_the_pinned_api():
    """supports_endpoint() is a coroutine in the pinned renault-api, so its result is awaited."""
    class V:
        async def supports_endpoint(self, ep):
            return ep == "supported"

    async def scenario():
        assert await main._supports(V(), "supported") is True
        assert await main._supports(V(), "other") is False

    asyncio.run(scenario())


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


def test_pessimistic_endpoint_is_restored_when_the_car_does_support_it():
    """hvac-settings starts outside the supported set, so the probe loop must be able to ADD it
    back — a discard-only loop would leave it dark forever on cars that do support it, defeating
    the self-healing the pessimistic default is supposed to preserve. Found in review."""
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


def test_rejected_fix_falls_back_to_the_last_published_timestamp():
    """state['gps_last_activity'] is introduced by this change, so the first poll after an upgrade
    has no persisted value. If that poll is a sentinel — the case on the vehicle that prompted this
    fix — the key would be omitted and the guard would go quiet. Fall back to the last value this
    process published. Found in review."""
    class _V(FakeVehicle):
        async def get_location(self):
            return types.SimpleNamespace(gpsLatitude=91, gpsLongitude=181,
                                         lastUpdateTime="2026-01-01T12:00:00Z")

    main._LATEST.update(data={"gps_last_activity": "2026-09-02T11:43:14Z"})
    try:
        data, loc_attrs = asyncio.run(
            main.poll_once(FakeVSession(_V()), {}, 52.0,
                           {"pressure", "charge-mode", "hvac-settings"}, "km"))
        assert loc_attrs is None
        assert data["gps_last_activity"] == "2026-09-02T11:43:14Z"
    finally:
        main._LATEST.update(data={})


def test_hvac_breaker_real_poll_trip_retry_recovery_and_bounded_logging(caplog):
    """Real poll_once wires BREAKER_TRIP, BREAKER_RETRY_EVERY, _hvac_due, breaker state,
    bounded warning/recovery logs, and _hvac_schedule_fields across pre-trip reset, trip,
    silent skipped polls, silent failed retry probe, and retry probe recovery."""
    assert (main.BREAKER_TRIP, main.BREAKER_RETRY_EVERY) == (3, 12)
    calls = []
    broken = {"v": True}

    class _V(FakeVehicle):
        async def get_hvac_settings(self):
            calls.append(1)
            if broken["v"]:
                raise RuntimeError("('err.tech.vcps.ev.hvac-settings.error', '502000')")
            day = types.SimpleNamespace(readyAtTime="T07:15Z")
            sched = types.SimpleNamespace(
                activated=True, monday=day, tuesday=None, wednesday=None,
                thursday=None, friday=None, saturday=None, sunday=None,
            )
            return types.SimpleNamespace(mode="scheduled", schedules=[sched])

    eps = {"pressure", "charge-mode", "hvac-settings"}
    # Pre-trip failure then immediate recovery resets _BREAKER_FAILS without tripping.
    asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
    assert main._BREAKER_FAILS["hvac-settings"] == 1
    assert not main._BREAKERS.get("hvac-settings")
    broken["v"] = False
    data, _ = asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
    assert main._BREAKER_FAILS["hvac-settings"] == 0
    assert not main._BREAKERS.get("hvac-settings")
    assert data["climate_schedule_mode"] == "Scheduled"
    assert data["climate_ready_time"] == "Mon 07:15"

    # Trip after BREAKER_TRIP consecutive failures; failed periodic probe stays tripped and silent;
    # subsequent periodic probe recovers, logs INFO, and clears breaker state.
    calls.clear()
    caplog.clear()
    broken["v"] = True
    with caplog.at_level(logging.INFO):
        for _ in range(main.BREAKER_TRIP):
            data, _ = asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
            assert "climate_schedule_mode" not in data and "climate_ready_time" not in data
        assert len(calls) == main.BREAKER_TRIP
        assert main._BREAKERS.get("hvac-settings") is True
        assert main._BREAKER_FAILS["hvac-settings"] == main.BREAKER_TRIP

        # Skipped for BREAKER_RETRY_EVERY - 1 polls while tripped.
        for expected_skip in range(1, main.BREAKER_RETRY_EVERY):
            asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
            assert len(calls) == main.BREAKER_TRIP
            assert main._BREAKER_SKIPS["hvac-settings"] == expected_skip

        # Failed retry probe on the BREAKER_RETRY_EVERY-th poll stays tripped and silent.
        asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == main.BREAKER_TRIP + 1
        assert main._BREAKERS.get("hvac-settings") is True
        assert main._BREAKER_SKIPS["hvac-settings"] == 0

        # Another BREAKER_RETRY_EVERY - 1 skipped polls, then recovery probe succeeds.
        for _ in range(main.BREAKER_RETRY_EVERY - 1):
            asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == main.BREAKER_TRIP + 1
        broken["v"] = False
        data, _ = asyncio.run(main.poll_once(FakeVSession(_V()), {}, 52.0, eps, "km"))

    assert len(calls) == main.BREAKER_TRIP + 2
    assert "hvac-settings" not in main._BREAKERS
    assert main._BREAKER_FAILS["hvac-settings"] == 0
    assert main._BREAKER_SKIPS["hvac-settings"] == 0
    assert data["climate_schedule_mode"] == "Scheduled"
    assert data["climate_ready_time"] == "Mon 07:15"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "hvac-settings" in r.getMessage()]
    assert len(warnings) == main.BREAKER_TRIP
    assert "pausing it" in warnings[-1]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "hvac-settings" in r.getMessage()]
    assert infos == ["hvac-settings is answering again - re-enabled."]


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


def test_main_publishes_the_car_timestamp_verdict_not_the_poll_verdict(wire_main):
    """Wiring through main() with a stub poll: an old payload must publish
    data_stale on. This is the assertion that would have caught the original defect."""
    old = main.iso(main.now_ts() - 68 * 3600)

    async def poll(vs, state, cap, sup, du):
        return ({"battery_level": 55, "last_updated": old}, None)

    fc = wire_main(poll)
    asyncio.run(main.main())

    published = [json.loads(p) for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC]
    assert published, "no state document published"
    assert published[0]["data_stale"] == "on"
    assert published[0]["poll_failing"] == "off"
    assert published[0]["last_successful_poll"]


def test_main_failure_path_publishes_both_signals(wire_main):
    async def poll(vs, state, cap, sup, du):
        raise RuntimeError("boom")

    fc = wire_main(poll)
    asyncio.run(main.main())

    published = [json.loads(p) for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC]
    assert published[0]["poll_failing"] == "on"      # never succeeded in this process
    assert "data_stale" not in published[0]          # and no car timestamp to judge
    assert published[0]["api_auth_failure"] == "off"  # not every failure is a credentials one


# --------------------------------------------------------------------------- #
# state file shape and a cancelled login
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("document", ["[]", '"text"', "3", "null", "{}"])
def test_load_state_ignores_a_document_of_the_wrong_shape(tmp_path, monkeypatch, document):
    """The poll's own error path calls .get() on this, so a list would raise inside the
    handler and take the loop down."""
    path = tmp_path / "state.json"
    path.write_text(document)
    monkeypatch.setattr(main, "STATE_FILE", str(path))
    state = main.load_state()
    assert state == {}
    assert isinstance(state, dict)
