"""Runtime/async coverage: poll_once, command dispatch, detection, MQTT wiring, health
server, account resolution, and one happy + one failing iteration of main()."""
import asyncio
import types

from renault_ha_core import charge
from renault_ha_core import debug
import main
import mqtt
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_ha_core import config


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
        return ns(gpsLatitude=51.512345, gpsLongitude=-0.123456, lastUpdateTime="t")

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
        main.poll_once(FakeVSession(FakeVehicle()), {}, 52.0, {"pressure", "charge-mode"}, "km"))
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
    assert attrs["latitude"] == 51.5123 and attrs["longitude"] == -0.1235   # rounded to 4 dp
    assert attrs["gps_accuracy"] == 11                                       # ~11 m at 4 dp


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


def test_setup_logging_clamps_library_loggers(monkeypatch):
    import logging
    monkeypatch.setenv("A290_LOG_LEVEL", "debug")
    main.setup_logging()
    assert logging.getLogger("renault_api").getEffectiveLevel() >= logging.INFO


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
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)

    class Client:
        async def get_person(self):
            return ns(accounts=[ns(accountType="OTHER", accountId="x"),
                                ns(accountType="MYRENAULT", accountId="acct-2")])

    assert asyncio.run(main.resolve_account(Client())) == "acct-2"
    # the discovered id is captured so redact() can mask it in later error URLs
    assert config._DISCOVERED_ACCOUNT_ID == "acct-2"
    assert "acct-2" not in config.redact("boom /accounts/acct-2/vehicles/V/x")


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
    assert supported == set(main.OPTIONAL_ENDPOINTS)   # data defaults kept, no actions added


def test_resolve_account_raises_without_myrenault(monkeypatch):
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)

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
        def publish(self, *a, **k):
            pass

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
    monkeypatch.setattr(mqtt, "mqtt_connect", lambda: FakeClient())
    monkeypatch.setattr(mqtt, "publish_discovery", lambda *a, **k: None)
    monkeypatch.setattr(main, "start_health_server", fake_health)
    monkeypatch.setattr(main.deploy, "run_deploy", _acoro)
    monkeypatch.setattr(main, "poll_once", poll)
    monkeypatch.setattr(main.asyncio, "Event", _OneShotEvent)


async def _acoro(*a, **k):
    return None


def test_main_one_successful_iteration(monkeypatch):
    async def poll(vs, state, cap, sup, du):
        return ({"battery_level": 50, "plug_status": "Connected", "charging": "off",
                 "plug_suspect": "off"}, {"latitude": 1, "longitude": 2})

    _wire_main(monkeypatch, poll)
    asyncio.run(main.main())


def test_main_handles_failing_poll(monkeypatch):
    async def poll(vs, state, cap, sup, du):
        raise RuntimeError("403 forbidden")   # exercises the except/backoff branch

    _wire_main(monkeypatch, poll)
    asyncio.run(main.main())


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
    called = {"n": 0}

    async def fake_login(ws, locale):
        called["n"] += 1
        return object()

    monkeypatch.setattr(main, "_login_vehicle", fake_login)
    (cmd,) = tuple(main.LOCATION_CMDS)
    asyncio.run(main.run_command(cmd))
    assert called["n"] == 0            # rejected before any login/dispatch
