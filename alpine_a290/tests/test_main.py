"""Unit tests for the Alpine A290 poller.

Focus on the pure logic that has bitten us before or would silently break a
dashboard tile: the Last Charge data-key contract, plug-suspect detection, enum
decoding, schedule summaries and unit conversion.
"""
import catalog
from renault_ha_core import charge
import main
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


def test_precondition_temp_default(monkeypatch):
    monkeypatch.delenv("A290_PRECONDITION_TEMPERATURE", raising=False)
    assert main._precondition_temp() == 20.0


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
