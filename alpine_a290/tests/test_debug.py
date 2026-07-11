"""Tests for the debug seam: the one-shot `debug_dump` diagnostic and its payload redaction
(`debug_enabled` / `_debug_redact` / `_dump_one` / `dump_api` / `maybe_dump_api`). The
redaction here is the structural key/value scrubber for decoded API payloads — its job is to
never leak identifiers/secrets into the WARNING dump."""
import asyncio
import types

import debug


def ns(**kw):
    return types.SimpleNamespace(**kw)


class _DumpVehicle:
    """Minimal vehicle exercising dump_api's three raw-payload shapes: a plain dict, a
    raw_data-bearing object, and an object with neither (the str() fallback)."""

    async def get_details(self):
        return {"vin": "SECRET", "batteryLevel": 80}          # dict branch

    async def get_battery_status(self):
        return ns(raw_data={"batteryLevel": 60})              # raw_data branch

    async def get_cockpit(self):
        return "plain-repr-object"                            # neither -> str() fallback


# --------------------------------------------------------------------------- #
# debug flag + payload redaction
# --------------------------------------------------------------------------- #
def test_debug_enabled_reads_flag(monkeypatch):
    monkeypatch.setenv("A290_DEBUG_DUMP", "true")
    assert debug.debug_enabled() is True
    monkeypatch.setenv("A290_DEBUG_DUMP", "false")
    assert debug.debug_enabled() is False
    monkeypatch.delenv("A290_DEBUG_DUMP", raising=False)
    assert debug.debug_enabled() is False


def test_debug_redact_masks_ids_and_secret_values_but_keeps_telemetry():
    out = debug._debug_redact(
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


def test_debug_redact_masks_lifecycle_privacy_buildspec_and_token_keys():
    """Quasi-identifying lifecycle/privacy fields, the build-spec `assets` block, and
    token-ish field names are masked by key regardless of value type/shape."""
    out = debug._debug_redact(
        {
            "deliveryDate": "2024-03-01",
            "firstRegistrationDate": "2024-03-15",
            "vehicleId": 1234567,
            "privacyMode": "off",
            "privacyModeUpdateDate": "2024-04-01",
            "svtFlag": False,
            "svtBlockFlag": False,
            "batteryCode": "BC-XYZ",
            "assets": [{"renditions": [{"url": "https://3dv.renault.com/VCD/abc"}]}],
            "accessToken": "ey.real.token",
            "refreshToken": "ey.refresh",
            "gigyaCookieValue": "cookie",
            "batteryLevel": 80,             # telemetry — must survive
        },
        secrets=[],
    )
    for key in ("deliveryDate", "firstRegistrationDate", "vehicleId", "privacyMode",
                "privacyModeUpdateDate", "svtFlag", "svtBlockFlag", "batteryCode",
                "assets", "accessToken", "refreshToken", "gigyaCookieValue"):
        assert out[key] == "***", key
    assert out["batteryLevel"] == 80


def test_debug_redact_masks_gps_and_numeric_secrets():
    out = debug._debug_redact(
        {"gpsLatitude": 51.5, "gpsLongitude": -0.1, "accountId": "abc", "batteryLevel": 80},
        ["12345"])
    assert out["gpsLatitude"] == "***" and out["gpsLongitude"] == "***"   # masked by key
    assert out["accountId"] == "***"            # masked by key
    assert out["batteryLevel"] == 80            # telemetry kept
    assert debug._debug_redact({"ref": 12345}, ["12345"])["ref"] == "***"  # numeric secret value


# --------------------------------------------------------------------------- #
# _dump_one / dump_api / maybe_dump_api
# --------------------------------------------------------------------------- #
def test_dump_one_parses_and_redacts_list_results():
    out = {}

    async def call():
        return [ns(raw_data={"latitude": 51.5, "energy": 10})]   # charges -> list of sessions

    asyncio.run(debug._dump_one(out, "get_charges", call, ["x"]))
    assert out["get_charges"] == [{"latitude": "***", "energy": 10}]   # parsed, GPS masked


def test_dump_api_runs_and_redacts(monkeypatch):
    monkeypatch.setenv("A290_VIN", "SECRET")
    asyncio.run(debug.dump_api(_DumpVehicle()))   # exercises the loop, raw_data + str fallbacks


def test_dump_api_probes_ranged_endpoints(monkeypatch):
    monkeypatch.setenv("A290_VIN", "SECRET")
    captured = {}

    class V(_DumpVehicle):
        async def get_car_adapter(self):
            return ns(raw_data={"vin": "SECRET", "battery": {"capacity": 52}})

        async def get_charges(self, start, end):
            captured["window"] = (start, end)
            return ns(raw_data={"charges": [{"chargeEnergyRecovered": 10}]})

        async def get_charge_history(self, start, end, period):
            raise RuntimeError("forbidden")          # exercises the error branch

    asyncio.run(debug.dump_api(V()))
    start, end = captured["window"]                  # charges probed with a ~30-day window
    assert (end - start).days == debug._DEBUG_RANGE_DAYS


def test_dump_api_records_per_endpoint_errors():
    class V:
        async def get_details(self):
            raise RuntimeError("forbidden")

    asyncio.run(debug.dump_api(V()))   # exercises the per-method except branch


def test_maybe_dump_api_runs_once_per_restart(monkeypatch):
    debug._DEBUG_STATE["dumped"] = False
    monkeypatch.setenv("A290_DEBUG_DUMP", "true")
    calls = {"n": 0}

    async def fake_dump(v):
        calls["n"] += 1

    monkeypatch.setattr(debug, "dump_api", fake_dump)
    asyncio.run(debug.maybe_dump_api(object()))
    asyncio.run(debug.maybe_dump_api(object()))   # already dumped -> skipped
    assert calls["n"] == 1
