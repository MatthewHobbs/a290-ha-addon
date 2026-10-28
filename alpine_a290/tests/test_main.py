"""Unit tests for the Alpine A290 poller.

Focus on the pure logic that has bitten us before or would silently break a
dashboard tile: the Last Charge data-key contract, plug-suspect detection, enum
decoding, schedule summaries and unit conversion.
"""
import catalog
import main
import pytest
from renault_api.kamereon.enums import ChargeState
from renault_mqtt import charge


class Battery:
    """Stand-in for renault-api's battery-status object (attr access + one method)."""

    def __init__(self, soc, power=0.0, energy=None, status=None):
        self.batteryLevel = soc
        self.chargingInstantaneousPower = power
        self.batteryAvailableEnergy = energy
        self._status = status

    def get_charging_status(self):
        return self._status


# --------------------------------------------------------------------------- #
# unit conversion helpers
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# enum decoding
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# preconditioning payload search
# --------------------------------------------------------------------------- #


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
    expected = {obj[len("a290_"):] for obj in catalog.SENSORS
                if obj.endswith(("charge_schedule_mode", "scheduled_charge_start",
                                 "scheduled_charge_duration"))}
    assert set(out) == expected


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
    expected = {obj[len("a290_"):] for obj in catalog.SENSORS if obj.startswith("a290_climate_")}
    assert set(out) == expected


# --------------------------------------------------------------------------- #
# discovery contract — the class of bug that shipped broken Last Charge tiles
# --------------------------------------------------------------------------- #
def test_last_charge_data_keys_match_sensor_object_ids(monkeypatch):
    """Every Last Charge sensor's value_template key must be produced by the data dict."""
    clock = {"t": 0.0}
    monkeypatch.setattr(charge, "now_ts", lambda: clock["t"])
    state = {}
    charge.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = charge.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)

    produced = set(lc)
    expected = {obj[len("a290_"):] for obj in catalog.SENSORS if "last_charge" in obj}
    assert produced == expected
    assert not any(k.startswith("a290_") for k in produced)


# --------------------------------------------------------------------------- #
# control buttons
# --------------------------------------------------------------------------- #
def test_command_actions_cover_every_button():
    assert {obj[len("a290_"):] for obj in main.ACTION_BUTTONS} == set(main.COMMAND_ACTIONS)


def test_command_actions_call_real_renault_api_methods(monkeypatch):
    import asyncio

    from renault_api.renault_vehicle import RenaultVehicle

    monkeypatch.delenv("A290_PRECONDITION_TEMPERATURE", raising=False)

    class Probe:
        def __getattr__(self, name):
            object.__setattr__(self, "called", name)

            async def _noop(*a, **k):
                return None
            return _noop

        async def get_charge_schedule(self):
            object.__setattr__(self, "called", "get_charge_schedule")
            return {}

    for name, fn in main.COMMAND_ACTIONS.items():
        probe = Probe()
        asyncio.run(fn(probe))
        assert hasattr(RenaultVehicle, probe.called), f"{name} -> nonexistent {probe.called}"


def test_precondition_temp_unset_means_the_car_decides(monkeypatch):
    monkeypatch.delenv("A290_PRECONDITION_TEMPERATURE", raising=False)
    assert main._precondition_temp() is None


def test_precondition_temp_reads_the_option(monkeypatch):
    monkeypatch.setenv("A290_PRECONDITION_TEMPERATURE", "18")
    assert main._precondition_temp() == 18.0


def test_start_climate_sends_the_option_when_set(monkeypatch):
    import asyncio
    monkeypatch.setenv("A290_PRECONDITION_TEMPERATURE", "23")

    class V:
        sent = None

        async def get_charge_schedule(self):
            raise AssertionError("the car's schedule is not read when the option is set")

        async def set_ac_start(self, temp):
            type(self).sent = temp

    asyncio.run(main._hvac_start_action(V()))
    assert V.sent == 23.0


def test_start_climate_falls_back_to_the_cars_own_setting(monkeypatch):
    import asyncio
    monkeypatch.delenv("A290_PRECONDITION_TEMPERATURE", raising=False)

    class V:
        sent = None

        async def get_charge_schedule(self):
            return {"preconditioningTemperature": 19}

        async def set_ac_start(self, temp):
            type(self).sent = temp

    asyncio.run(main._hvac_start_action(V()))
    assert V.sent == 19.0


def test_start_climate_falls_back_to_21c_without_a_schedule(monkeypatch):
    import asyncio
    monkeypatch.delenv("A290_PRECONDITION_TEMPERATURE", raising=False)

    class V:
        sent = None

        async def get_charge_schedule(self):
            raise RuntimeError("api down")

        async def set_ac_start(self, temp):
            type(self).sent = temp

    asyncio.run(main._hvac_start_action(V()))
    assert V.sent == 21.0


# --------------------------------------------------------------------------- #
# writable charge-limit numbers
# --------------------------------------------------------------------------- #
def test_number_cmds_match_numbers():
    assert main.NUMBER_CMDS == {obj[len("a290_"):] for obj in main.NUMBERS}


# --------------------------------------------------------------------------- #
# startup detection failure
# --------------------------------------------------------------------------- #
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
            # Data endpoints stay optimistic on a detection failure EXCEPT the pessimistic
        # ones (hvac-settings), which answer 502000 on every poll when absent.
        assert supported == set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS
        assert not any(ep.startswith("actions/") for ep in supported)
        assert vs._vehicle is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# cached login session
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("first_attempt", ["success", "refused", "timeout"])
def test_vehicle_session_wires_transport_login_and_reauthenticates(monkeypatch, caplog, first_attempt):
    """Exercise the shared cache through the real app login at the Renault boundary."""
    import asyncio

    calls = {"login": 0, "closed": 0, "sessions": []}
    settings = {"A290_USERNAME": "driver@example.invalid", "A290_PASSWORD": "test-only",
                "A290_ACCOUNT_ID": "test-account", "A290_VIN": "test-vin"}
    vehicles = [object(), object()]
    failures = int(first_attempt != "success")

    class FakeSession:
        def __init__(self, *, timeout):
            assert timeout is main.API_TIMEOUT
            calls["sessions"].append(self)

        async def close(self):
            calls["closed"] += 1

    class Client:
        def __init__(self, *, websession, locale):
            assert websession is calls["sessions"][-1]
            assert locale == "fr_FR"
            self.session = self

        async def login(self, username, password):
            assert (username, password) == (settings["A290_USERNAME"], settings["A290_PASSWORD"])
            calls["login"] += 1
            if calls["login"] == 1:
                if first_attempt == "refused":
                    raise RuntimeError("login refused")
                if first_attempt == "timeout":
                    await asyncio.Event().wait()

        async def get_api_account(self, account_id):
            assert account_id == settings["A290_ACCOUNT_ID"]
            return self

        async def get_api_vehicle(self, vin):
            assert vin == settings["A290_VIN"]
            return vehicles[calls["login"] - 1 - failures]

    monkeypatch.setattr(main.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(main, "RenaultClient", Client)
    monkeypatch.setattr(main, "cfg", lambda key: settings[key])
    caplog.set_level("INFO", logger=main.LOG.name)

    async def scenario():
        vs = main.VehicleSession("fr_FR")
        assert calls["sessions"] == []
        if failures:
            error = RuntimeError if first_attempt == "refused" else TimeoutError
            with pytest.raises(error):
                await asyncio.wait_for(vs.vehicle(), timeout=0.01)
            assert calls["closed"] == 1
            assert not any("session cached for reuse" in r.message for r in caplog.records)
        first = await vs.vehicle()
        again = await vs.vehicle()
        assert first is again is vehicles[0]
        assert calls["login"] == 1 + failures

        await vs.invalidate()            # drop the session
        assert calls["closed"] == 1 + failures
        assert await vs.vehicle() is vehicles[1]
        assert calls["login"] == 2 + failures

        await vs.close()
        assert calls["closed"] == 2 + failures

    asyncio.run(scenario())
    assert sum("session cached for reuse" in r.message for r in caplog.records) == 2
