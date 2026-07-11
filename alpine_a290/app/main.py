"""Alpine A290 add-on — poll the Renault/Kamereon API and publish to HA via MQTT discovery.

A290/CMF-BEV (model A5E1AE) quirks: batteryCapacity is always 0 (we use the configured
capacity); chargingStatus is a float ChargeState decoded via the library enum;
chargingInstantaneousPower units are unreliable; batteryTemperature/internalTemperature are
often absent. Control buttons (ACTION_BUTTONS) are gated on supports_endpoint(); charge-start
is forbidden on this model, so it's never shipped.
"""
import asyncio
import inspect
import json
import logging
import os
import signal
import sys

import aiohttp
import catalog
import deploy
from aiohttp import web
from catalog import (
    ACTION_BUTTONS,
    ENV_PREFIX,
    NUMBERS,
    OBJ_PREFIX,
    OPTIONAL_ENDPOINTS,
    REFRESH_LOCATION_EP,
    SOC_ENDPOINT,
)
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.renault_client import RenaultClient
from renault_mqtt import config, mqtt
from renault_mqtt.charge import CHARGES_ENDPOINT, resolve_last_charge, update_charge_session
from renault_mqtt.config import _RedactingFilter, cfg, redact
from renault_mqtt.debug import maybe_dump_api
from renault_mqtt.parse import (
    _bool_on,
    _charge_schedule_fields,
    _dist,
    _enum_label,
    _find_precond,
    _hvac_schedule_fields,
    available_energy,
)
from renault_mqtt.util import _num, iso, now_ts

# Inject this model's env-var prefix into the shared core's redaction net before anything is
# logged, so config.redact / _config_secrets mask this add-on's configured VIN / account_id /
# username / password. Set at import time (the add-on's own suite asserts the wiring).
config.ENV_PREFIX = ENV_PREFIX
# Hand the shared MQTT seam this model's catalog + identity (NODE / DEVICE / topics / discovery
# tables). Must follow the ENV_PREFIX injection above — configure() reads PUBLISH_LOCATION under it.
mqtt.configure(catalog)

LOG = logging.getLogger("alpine_a290")

# Number of leading chars to strip off an object_id to get the MQTT value_template key /
# command suffix (e.g. "a290_battery_level" -> "battery_level"). Derived from the catalog's
# per-model OBJ_PREFIX, so the r5 twin needs no change here — previously a bare `obj[5:]`.
# (mqtt.py keeps its own copy for discovery; both derive from the same catalog constant.)
_P = len(OBJ_PREFIX)
STATE_FILE = os.environ.get("A290_STATE_FILE", "/data/state.json")

# Decimal places the published GPS is rounded to before it goes on the retained MQTT topic
# (privacy — coarsens an otherwise full-precision home location). 4 dp ≈ 11 m. Default 4.
# Tolerate the option being absent on an upgraded install (bashio can export "" or "null").
_GPS_P = os.environ.get("A290_GPS_PRECISION", "4").strip()
GPS_PRECISION = max(1, min(6, int(_GPS_P))) if _GPS_P.isdigit() else 4

VERSION = os.environ.get("A290_VERSION", "dev")

CHARGE_STATUS_LABELS = {
    ChargeState.NOT_IN_CHARGE: "Not Charging",
    ChargeState.WAITING_FOR_A_PLANNED_CHARGE: "Waiting (Planned)",
    ChargeState.CHARGE_ENDED: "Charge Ended",
    ChargeState.WAITING_FOR_CURRENT_CHARGE: "Waiting to Charge",
    ChargeState.ENERGY_FLAP_OPENED: "Flap Open",
    ChargeState.CHARGE_IN_PROGRESS: "Charging",
    ChargeState.CHARGE_ERROR: "Error",
    ChargeState.UNAVAILABLE: "Unavailable",
    ChargeState.V2G_CHARGING_WAITING: "V2G Waiting",
    ChargeState.V2L_CONNECTED: "V2L Connected",
    ChargeState.V2G_DISCHARGING: "V2G Discharging",
    ChargeState.V2G_CHARGING_NORMAL: "V2G Charging",
}
PLUG_STATUS_LABELS = {
    PlugState.UNPLUGGED: "Disconnected",
    PlugState.PLUGGED: "Connected",
    PlugState.PLUG_ERROR: "Plug Error",
    PlugState.PLUG_UNKNOWN: "Unknown",
}
RHD_LOCALES = {"en_gb", "en_ie"}
MILES_LOCALES = {"en_gb"}
PLUG_KM_DELTA = 1
PLUG_SOC_DROP = 2
PLUG_MIN_AGE = 600
PLUG_MAX_AGE = 12 * 3600


def setup_logging():
    level = getattr(logging, cfg("A290_LOG_LEVEL", "info").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    # Attach the secret-redaction net to the root handler(s): every record (ours + the
    # library's, which propagates to root) is scrubbed before it's emitted.
    redactor = _RedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
    for noisy in ("renault_api", "renault_api.kamereon", "renault_api.gigya"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)
    except OSError as err:
        LOG.warning("Could not persist state: %s", err)


def charging_status_label(battery):
    return _enum_label(battery.get_charging_status(), CHARGE_STATUS_LABELS,
                       getattr(battery, "chargingStatus", None))


def is_charging(battery):
    power = _num(getattr(battery, "chargingInstantaneousPower", None)) or 0
    return battery.get_charging_status() == ChargeState.CHARGE_IN_PROGRESS or power > 0.1


async def _login_vehicle(websession, locale):
    client = RenaultClient(websession=websession, locale=locale)
    await client.session.login(cfg("A290_USERNAME"), cfg("A290_PASSWORD"))
    account = await client.get_api_account(await resolve_account(client))
    return await account.get_api_vehicle(cfg("A290_VIN"))


API_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


class VehicleSession:
    """One logged-in vehicle + aiohttp session, reused across polls instead of a fresh
    Gigya login each cycle (~288/day). renault-api refreshes its own tokens; invalidate()
    drops the cached login so the next call re-authenticates. Owned by the poll loop only —
    button presses keep their own short-lived login in run_command."""

    def __init__(self, locale):
        self.locale = locale
        self._websession = None
        self._vehicle = None

    async def vehicle(self):
        if self._vehicle is None:
            self._websession = aiohttp.ClientSession(timeout=API_TIMEOUT)
            try:
                self._vehicle = await _login_vehicle(self._websession, self.locale)
            except Exception:
                await self.invalidate()
                raise
            LOG.info("Logged in to the Renault API (session cached for reuse)")
        return self._vehicle

    async def invalidate(self):
        """Drop the cached login so the next vehicle() re-authenticates."""
        if self._websession is not None:
            try:
                await self._websession.close()
            except Exception:  # noqa: BLE001
                pass
        self._websession = None
        self._vehicle = None

    async def close(self):
        """Release the session at shutdown."""
        await self.invalidate()


async def _supports(vehicle, ep):
    """supports_endpoint() is async in renault-api 0.5.x; tolerate a sync return too."""
    res = vehicle.supports_endpoint(ep)
    return (await res) if inspect.isawaitable(res) else res


async def detect_supported(vsession):
    """Set of supported endpoint names. Data endpoints default to supported on a detection
    error (read-only, harmless if empty); action endpoints default to unsupported so a
    forbidden control is never shipped."""
    supported = set(OPTIONAL_ENDPOINTS)
    action_eps = {ep for _name, _icon, ep in ACTION_BUTTONS.values()}
    try:
        vehicle = await vsession.vehicle()
        for ep in list(OPTIONAL_ENDPOINTS):
            try:
                if not await _supports(vehicle, ep):
                    supported.discard(ep)
            except Exception as err:  # noqa: BLE001
                LOG.warning("supports_endpoint(%s) check failed: %s", ep, err)
        for ep in sorted(action_eps | {SOC_ENDPOINT, CHARGES_ENDPOINT}):
            try:
                if await _supports(vehicle, ep):
                    supported.add(ep)
            except Exception as err:  # noqa: BLE001
                LOG.warning("supports_endpoint(%s) check failed: %s", ep, err)
        LOG.info("Supported optional endpoints: %s", sorted(supported))
    except Exception as err:  # noqa: BLE001
        await vsession.invalidate()
        LOG.warning("Endpoint-support detection failed (publishing sensors, hiding action buttons): %s", err)
    return supported


def _precondition_temp():
    """Target cabin temperature (°C) for Start Climate — set on the Configuration page."""
    return float(cfg("A290_PRECONDITION_TEMPERATURE", "20") or "20")


COMMAND_ACTIONS = {
    "charge_start":     lambda v: v.set_charge_start(),
    "horn":             lambda v: v.start_horn(),
    "lights":           lambda v: v.start_lights(),
    "climate_start":    lambda v: v.set_ac_start(_precondition_temp()),
    "climate_stop":     lambda v: v.set_ac_stop(),
    "refresh_location": lambda v: v.refresh_location(),
}

# Command suffixes that trigger a location refresh — rejected when location publishing is off
# (the button is also cleared in publish_discovery), so an opted-out install can't refresh.
LOCATION_CMDS = {obj[_P:] for obj, (_n, _i, ep) in ACTION_BUTTONS.items() if ep == REFRESH_LOCATION_EP}

# Command suffixes (topic tail) that map to writable numbers rather than button actions.
NUMBER_CMDS = {obj[_P:] for obj in NUMBERS}

_soc_lock = None
_soc_lock_loop = None


def _soc_lock_get():
    """One lock per event loop, serialising charge-limit writes so two quick slider moves
    can't interleave a read-modify-write and clobber each other. Re-created if the running
    loop changes (so per-test event loops don't trip cross-loop binding)."""
    global _soc_lock, _soc_lock_loop
    loop = asyncio.get_running_loop()
    if _soc_lock is None or _soc_lock_loop is not loop:
        _soc_lock, _soc_lock_loop = asyncio.Lock(), loop
    return _soc_lock


async def set_soc_level(which, payload):
    """Write a charge limit. set_battery_soc needs both min and target together, so the
    opposing slider's current value is read back first and re-sent unchanged. Serialised so
    adjusting both sliders in quick succession can't interleave and clobber a change."""
    try:
        value = int(float(payload))
    except (TypeError, ValueError):
        LOG.warning("Ignoring non-numeric %s value: %r", which, payload)
        return
    locale = cfg("A290_LOCALE", "en_GB")
    async with _soc_lock_get():
        try:
            async with aiohttp.ClientSession(timeout=API_TIMEOUT) as websession:
                vehicle = await _login_vehicle(websession, locale)
                soc = await vehicle.get_battery_soc()
                cur_min = getattr(soc, "socMin", None)
                cur_target = getattr(soc, "socTarget", None)
                new_min, new_target = (value, cur_target) if which == "soc_min" else (cur_min, value)
                if new_min is None or new_target is None:
                    LOG.error("Cannot set %s: current limits unavailable (min=%s, target=%s)",
                              which, cur_min, cur_target)
                    return
                await vehicle.set_battery_soc(min=int(new_min), target=int(new_target))
            LOG.info("Set charge limits: min=%s%%, target=%s%%", new_min, new_target)
        except Exception as err:  # noqa: BLE001
            LOG.error("Failed to set %s=%s: %s", which, value, redact(err))


async def run_command(cmd, payload=""):
    """Dispatch an MQTT command: a button press to its renault-api action, or a number set
    to set_battery_soc. Never fatal."""
    if cmd in NUMBER_CMDS:
        await set_soc_level(cmd, payload)
        return
    if cmd in LOCATION_CMDS and not mqtt.PUBLISH_LOCATION:
        LOG.info("Ignoring '%s' — location is disabled (publish_location: false)", cmd)
        return
    action = COMMAND_ACTIONS.get(cmd)
    if action is None:
        LOG.warning("Ignoring unknown command: %s", cmd)
        return
    locale = cfg("A290_LOCALE", "en_GB")
    try:
        async with aiohttp.ClientSession(timeout=API_TIMEOUT) as websession:
            vehicle = await _login_vehicle(websession, locale)
            await action(vehicle)
        LOG.info("Command '%s' sent", cmd)
    except Exception as err:  # noqa: BLE001
        LOG.error("Command '%s' failed: %s", cmd, redact(err))


def detect_plug_suspect(state, plug, mileage, soc, charging):
    """Connected-but-driven / Disconnected-but-charging detection. Returns 'on'/'off'."""
    prev = state.get("plug_prev")
    if plug == 1 and (prev != 1 or "plug_base_ts" not in state):
        state.update(plug_base_mileage=mileage, plug_base_soc=soc, plug_base_ts=now_ts())
    state["plug_prev"] = plug

    drove = False
    bts, bkm, bsoc = state.get("plug_base_ts"), state.get("plug_base_mileage"), state.get("plug_base_soc")
    if bts and None not in (mileage, soc, bkm, bsoc):
        age = now_ts() - bts
        if PLUG_MIN_AGE <= age <= PLUG_MAX_AGE:
            drove = (mileage - bkm >= PLUG_KM_DELTA) and (bsoc - soc >= PLUG_SOC_DROP)
    stuck = (plug == 1 and drove) or (plug == 0 and charging)
    return "on" if stuck else "off"


async def resolve_account(client):
    account_id = cfg("A290_ACCOUNT_ID")
    if account_id:
        return account_id
    person = await client.get_person()
    for account in person.accounts:
        if account.accountType == "MYRENAULT":
            config._DISCOVERED_ACCOUNT_ID = account.accountId   # so redact() can mask it (URL embeds it)
            LOG.info("Auto-discovered MYRENAULT account")
            return account.accountId
    raise RuntimeError("No MYRENAULT account found and A290_ACCOUNT_ID not set")


async def poll_once(vsession, state, capacity_kwh, supported_eps, dist_unit):
    vehicle = await vsession.vehicle()
    locale = vsession.locale
    battery = await vehicle.get_battery_status()
    plug = battery.get_plug_status()
    charging = is_charging(battery)
    data = {
        "battery_level": battery.batteryLevel,
        "range": _dist(battery.batteryAutonomy, dist_unit),
        "battery_temperature": battery.batteryTemperature,
        "charging_power": _num(getattr(battery, "chargingInstantaneousPower", None)),
        "charging_remaining": getattr(battery, "chargingRemainingTime", None),
        "available_energy": available_energy(getattr(battery, "batteryAvailableEnergy", None), battery.batteryLevel, capacity_kwh),
        "plug_status": _enum_label(plug, PLUG_STATUS_LABELS, getattr(battery, "plugStatus", None)),
        "charging_flap": "Open: Plugged In" if plug == PlugState.PLUGGED else "Closed",
        "charging_status": "Charging" if charging else charging_status_label(battery),
        "last_updated": getattr(battery, "timestamp", None) or iso(now_ts()),
        "drive_side": "RHD" if locale.lower() in RHD_LOCALES else "LHD",
    }
    mileage = None
    try:
        mileage = getattr(await vehicle.get_cockpit(), "totalMileage", None)
        data["mileage"] = _dist(mileage, dist_unit)
    except Exception as err:  # noqa: BLE001
        LOG.warning("cockpit unavailable: %s", err)
    try:
        hvac = await vehicle.get_hvac_status()
        data["external_temperature"] = getattr(hvac, "externalTemperature", None)
        data["hvac_status"] = str(getattr(hvac, "hvacStatus", ""))
        data["hvac_soc_threshold"] = getattr(hvac, "socThreshold", None)
        data["hvac_last_activity"] = getattr(hvac, "lastUpdateTime", None)
    except Exception as err:  # noqa: BLE001
        LOG.warning("hvac unavailable: %s", err)
    try:
        sched = await vehicle.get_charge_schedule()
        p = _find_precond(sched)
        data["preconditioning_temperature"] = p.get("preconditioningTemperature")
        data["heated_steering_wheel"] = _bool_on(p.get("preconditioningHeatedStrgWheel"))
        left = p.get("preconditioningHeatedLeftSeat")
        right = p.get("preconditioningHeatedRightSeat")
        rhd = locale.lower() in RHD_LOCALES
        data["heated_seat_driver"] = _bool_on(right if rhd else left)
        data["heated_seat_passenger"] = _bool_on(left if rhd else right)
        data.update(_charge_schedule_fields(p))
    except Exception as err:  # noqa: BLE001
        LOG.warning("ev/settings unavailable: %s", err)
    try:
        data.update(_hvac_schedule_fields(await vehicle.get_hvac_settings()))
    except Exception as err:  # noqa: BLE001
        LOG.warning("hvac-settings unavailable: %s", err)
    try:
        soc_lvl = await vehicle.get_battery_soc()
        data["soc_target"] = getattr(soc_lvl, "socTarget", None)
        data["soc_min"] = getattr(soc_lvl, "socMin", None)
    except Exception as err:  # noqa: BLE001
        LOG.warning("battery_soc unavailable: %s", err)
    if "pressure" in supported_eps:
        try:
            tp = await vehicle.get_tyre_pressure()
            data["tyre_pressure_fl"] = getattr(tp, "flPressure", None)
            data["tyre_pressure_fr"] = getattr(tp, "frPressure", None)
            data["tyre_pressure_rl"] = getattr(tp, "rlPressure", None)
            data["tyre_pressure_rr"] = getattr(tp, "rrPressure", None)
        except Exception as err:  # noqa: BLE001
            LOG.warning("tyre_pressure unavailable: %s", err)
    if "charge-mode" in supported_eps:
        try:
            cm = await vehicle.get_charge_mode()
            data["charge_mode"] = str(getattr(cm, "chargeMode", "") or "")
        except Exception as err:  # noqa: BLE001
            LOG.warning("charge_mode unavailable: %s", err)

    location_attrs = None
    if mqtt.PUBLISH_LOCATION:   # skipped entirely when the user opts out of location publishing
        try:
            loc = await vehicle.get_location()
            data["gps_last_activity"] = getattr(loc, "lastUpdateTime", None)
            lat, lon = getattr(loc, "gpsLatitude", None), getattr(loc, "gpsLongitude", None)
            if lat is not None and lon is not None:
                location_attrs = {"latitude": round(lat, GPS_PRECISION),
                                  "longitude": round(lon, GPS_PRECISION),
                                  "gps_accuracy": max(10, round(111_000 / 10 ** GPS_PRECISION)),
                                  "last_update": getattr(loc, "lastUpdateTime", None)}
        except Exception as err:  # noqa: BLE001
            LOG.warning("location unavailable: %s", err)

    live_lc = update_charge_session(state, battery, capacity_kwh, charging)
    data.update(await resolve_last_charge(vehicle, state, supported_eps, capacity_kwh, live_lc))
    data["charging"] = "on" if charging else "off"
    plug_code = plug.value if plug is not None else None
    data["plug_suspect"] = detect_plug_suspect(state, plug_code, mileage,
                                                battery.batteryLevel, charging)
    await maybe_dump_api(vehicle)
    return data, location_attrs


HEALTH_PORT = 8099

# Latest poll snapshot for the read-only ingress status panel. Deliberately excludes raw
# GPS (lat/lon are published separately to MQTT, not stored here) and any credential.
_LATEST = {"version": VERSION, "ok": False, "last_poll": None, "supported": [],
           "dist_unit": "km", "data": {}}

_PANEL_FILE = os.path.join(os.path.dirname(__file__), "panel.html")


async def _panel_page(_req):
    """Serve the self-contained, read-only ingress status panel."""
    if os.path.isfile(_PANEL_FILE):
        return web.FileResponse(_PANEL_FILE)
    return web.Response(text="Status panel unavailable", content_type="text/plain")


async def _panel_state(_req):
    """JSON the panel polls — latest car state + diagnostics; no credentials, no raw GPS."""
    return web.json_response(_LATEST)


async def start_health_server():
    """/healthz (backs the Dockerfile HEALTHCHECK) plus the read-only ingress status panel
    (GET / and GET /api/state) on the poll loop. A deadlocked loop can't answer /healthz,
    so the container is marked unhealthy and restarted."""
    app = web.Application()
    app.router.add_get("/healthz", lambda _req: web.Response(text="ok"))
    app.router.add_get("/", _panel_page)
    app.router.add_get("/api/state", _panel_state)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEALTH_PORT).start()  # nosec B104
    LOG.info("Health endpoint + status panel listening on :%d", HEALTH_PORT)
    return runner


async def main():
    setup_logging()
    LOG.info("Alpine A290 add-on v%s starting", VERSION)
    for req in ("A290_USERNAME", "A290_PASSWORD", "A290_VIN", "MQTT_HOST"):
        if not cfg(req):
            LOG.error("Missing required setting: %s — set it on the add-on Configuration page.", req)
            sys.exit(1)

    locale = cfg("A290_LOCALE", "en_GB")
    dist_unit = "mi" if locale.lower() in MILES_LOCALES else "km"
    interval = int(cfg("A290_POLL_INTERVAL", "300") or "300")
    capacity = float(cfg("A290_BATTERY_CAPACITY_KWH", "52") or "52")
    stale_secs = int(cfg("A290_STALE_HOURS", "6") or "6") * 3600

    loop = asyncio.get_running_loop()
    # Inject the loop + command handler into the MQTT seam so its _on_message can schedule an
    # inbound command onto our loop without importing main (keeps the dependency one-directional).
    mqtt._LOOP = loop
    mqtt._COMMAND_HANDLER = run_command
    health = await start_health_server()
    state = load_state()
    vsession = VehicleSession(locale)
    supported = await detect_supported(vsession)
    mqtt._MQTT_CTX["supported"], mqtt._MQTT_CTX["dist_unit"] = supported, dist_unit
    _LATEST["supported"] = sorted(supported)
    _LATEST["dist_unit"] = dist_unit  # so the status panel can label range/mileage
    client = mqtt.mqtt_connect()
    mqtt.publish_discovery(client, supported, dist_unit)
    await deploy.run_deploy()

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    fails = 0
    max_backoff = max(interval, 1800)
    while not stop.is_set():
        try:
            data, location_attrs = await asyncio.wait_for(
                poll_once(vsession, state, capacity, supported, dist_unit),
                timeout=max(30, interval - 10))
            fails = 0
            state["last_success"] = now_ts()
            data["api_auth_failure"] = "off"
            data["data_stale"] = "off"
            client.publish(mqtt.STATE_TOPIC, json.dumps(data), retain=True)
            _LATEST.update(ok=True, last_poll=iso(now_ts()), data=data)
            if location_attrs:
                client.publish(mqtt.ATTR_TOPIC, json.dumps(location_attrs), retain=True)
                client.publish(mqtt.TRACKER_STATE_TOPIC, "online", retain=True)
            client.publish(mqtt.AVAIL_TOPIC, "online", retain=True)
            save_state(state)
            LOG.info("Published: %s%% battery, plug=%s, charging=%s, suspect=%s",
                     data.get("battery_level"), data.get("plug_status"),
                     data.get("charging"), data.get("plug_suspect"))
        except Exception as err:  # noqa: BLE001
            fails += 1
            LOG.error("Poll failed (%d in a row): %s", fails, redact(err))
            last_ok = state.get("last_success", 0)
            stale = (now_ts() - last_ok) > stale_secs if last_ok else True
            # Prefer the exception type (an HTTP 401/403 is unambiguous); fall back to the
            # message text for gigya/library errors that aren't raised as ClientResponseError.
            auth = (isinstance(err, aiohttp.ClientResponseError) and err.status in (401, 403)) or \
                any(s in str(err).lower() for s in ("login", "password", "credential", "401", "403"))
            if auth or fails % 3 == 0:
                await vsession.invalidate()
            client.publish(mqtt.STATE_TOPIC, json.dumps({
                "api_auth_failure": "on" if auth else "off",
                "data_stale": "on" if stale else "off",
            }), retain=True)
            client.publish(mqtt.AVAIL_TOPIC, "online", retain=True)
            _LATEST.update(ok=False, last_poll=iso(now_ts()), error=redact(err))
            _LATEST["data"].update(api_auth_failure="on" if auth else "off",
                                   data_stale="on" if stale else "off")
            save_state(state)
        wait = interval if fails == 0 else min(interval * 2 ** (fails - 1), max_backoff)
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass

    LOG.info("Shutting down")
    await vsession.close()
    await health.cleanup()
    client.publish(mqtt.AVAIL_TOPIC, "offline", retain=True)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
