"""Runtime/async coverage: poll_once, command dispatch, detection, MQTT wiring, health
server, account resolution, and one happy + one failing iteration of main()."""
import asyncio
import types

import main
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState


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
                "preconditioningHeatedLeftSeat": False, "preconditioningHeatedRightSeat": True}

    async def get_battery_soc(self):
        return ns(socTarget=80, socMin=20)

    async def get_tyre_pressure(self):
        return ns(flPressure=2.4, frPressure=2.4, rlPressure=2.3, rrPressure=2.3)

    async def get_charge_mode(self):
        return ns(chargeMode="always")

    async def get_location(self):
        return ns(gpsLatitude=51.5, gpsLongitude=-0.1, lastUpdateTime="t")

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
    assert attrs["latitude"] == 51.5


class FlakyVehicle(FakeVehicle):
    async def get_cockpit(self):
        raise RuntimeError("cockpit down")

    async def get_hvac_status(self):
        raise RuntimeError("hvac down")

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


def test_setup_logging_clamps_library_loggers(monkeypatch):
    import logging
    monkeypatch.setenv("A290_LOG_LEVEL", "debug")
    main.setup_logging()
    assert logging.getLogger("renault_api").getEffectiveLevel() >= logging.INFO


def test_poll_once_debug_dump_branch(monkeypatch):
    monkeypatch.setenv("A290_DEBUG_DUMP", "true")
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    asyncio.run(main.poll_once(FakeVSession(FakeVehicle()), {}, 52.0, set(), "km"))


# --------------------------------------------------------------------------- #
# dump_api
# --------------------------------------------------------------------------- #
def test_dump_api_runs_and_redacts(monkeypatch):
    monkeypatch.setenv("A290_VIN", "SECRET")
    asyncio.run(main.dump_api(FakeVehicle()))   # exercises the loop, raw_data + str fallbacks


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
# MQTT wiring
# --------------------------------------------------------------------------- #
def test_mqtt_connect(monkeypatch):
    seen = {}

    class FakeClient:
        def username_pw_set(self, u, p):
            seen["auth"] = (u, p)

        def will_set(self, *a, **k):
            seen["will"] = True

        def connect(self, host, port, keepalive):
            seen["connect"] = (host, port)

        def loop_start(self):
            seen["loop"] = True

    monkeypatch.setattr(main.mqtt, "Client", lambda *a, **k: FakeClient())
    monkeypatch.setenv("MQTT_HOST", "broker")
    monkeypatch.setenv("MQTT_USER", "u")
    monkeypatch.setenv("MQTT_PASS", "p")
    main.mqtt_connect()
    assert seen["connect"][0] == "broker" and seen["loop"] and seen["auth"] == ("u", "p")


def test_on_connect_resubscribes_and_publishes():
    class C:
        def __init__(self):
            self.subs, self.pub = [], {}

        def subscribe(self, t):
            self.subs.append(t)

        def publish(self, t, p, retain=False):
            self.pub[t] = p

    main._MQTT_CTX["supported"], main._MQTT_CTX["dist_unit"] = set(main.OPTIONAL_ENDPOINTS), "km"
    c = C()
    main._on_connect(c, None, None, 0)
    assert f"{main.CMD_PREFIX}#" in c.subs
    assert any("/sensor/alpine_a290/" in t for t in c.pub)
    assert c.pub[main.AVAIL_TOPIC] == "online"


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


def test_dump_api_records_per_endpoint_errors():
    class V:
        async def get_details(self):
            raise RuntimeError("forbidden")

    asyncio.run(main.dump_api(V()))   # exercises the per-method except branch


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
    monkeypatch.setattr(main, "mqtt_connect", lambda: FakeClient())
    monkeypatch.setattr(main, "publish_discovery", lambda *a, **k: None)
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


def test_main_exits_on_missing_config(monkeypatch):
    for k in ("A290_USERNAME", "A290_PASSWORD", "A290_VIN", "MQTT_HOST"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(main.main())
