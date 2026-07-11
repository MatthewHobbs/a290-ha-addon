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
from datetime import datetime, timedelta, timezone

import aiohttp
import config
import deploy
import paho.mqtt.client as mqtt
from aiohttp import web
from catalog import (
    ACTION_BUTTONS,
    BINARY_SENSORS,
    CHARGES_ENDPOINT,
    DEFAULT_DISABLED_SENSORS,
    ICONS,
    NUMBERS,
    OBJ_PREFIX,
    OPTIONAL_ENDPOINTS,
    RETIRED_SENSORS,
    SENSORS,
    SOC_ENDPOINT,
)
from config import _opt_flag, _RedactingFilter, cfg, redact
from debug import maybe_dump_api
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.renault_client import RenaultClient
from util import _num, iso, now_ts

LOG = logging.getLogger("alpine_a290")

DISCOVERY_PREFIX = "homeassistant"
NODE = "alpine_a290"
STATE_TOPIC = f"{NODE}/state"
ATTR_TOPIC = f"{NODE}/location/attributes"
TRACKER_STATE_TOPIC = f"{NODE}/location/state"
AVAIL_TOPIC = f"{NODE}/availability"
CMD_PREFIX = f"{NODE}/cmd/"
# Number of leading chars to strip off an object_id to get the MQTT value_template key /
# command suffix (e.g. "a290_battery_level" -> "battery_level"). Derived from the catalog's
# per-model OBJ_PREFIX, so the r5 twin needs no change here — previously a bare `obj[5:]`.
_P = len(OBJ_PREFIX)
STATE_FILE = os.environ.get("A290_STATE_FILE", "/data/state.json")

# Publish the car's GPS as a device_tracker + retained location topics? Default on. When off,
# no location is fetched or published and any previously-retained GPS topics are cleared, so a
# privacy-minded user can run the add-on with no location footprint on the broker at all.
PUBLISH_LOCATION = _opt_flag("A290_PUBLISH_LOCATION", True)
# The location-refresh action's endpoint. When location publishing is off we also suppress this
# button + its MQTT command, so an opted-out install can't trigger a location refresh at all.
REFRESH_LOCATION_EP = "actions/refresh-location"
# Decimal places the published GPS is rounded to before it goes on the retained MQTT topic
# (privacy — coarsens an otherwise full-precision home location). 4 dp ≈ 11 m. Default 4.
# Tolerate the option being absent on an upgraded install (bashio can export "" or "null").
_GPS_P = os.environ.get("A290_GPS_PRECISION", "4").strip()
GPS_PRECISION = max(1, min(6, int(_GPS_P))) if _GPS_P.isdigit() else 4

DEVICE = {"identifiers": [NODE], "name": "Alpine A290", "manufacturer": "Alpine", "model": "A290"}
VERSION = os.environ.get("A290_VERSION", "dev")

_LOOP = None

HOME_POWER_MAX_KW = 7.4
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


def _on_message(client, userdata, msg):
    if _LOOP is not None and msg.topic.startswith(CMD_PREFIX):
        cmd = msg.topic[len(CMD_PREFIX):]
        payload = msg.payload.decode(errors="replace") if msg.payload else ""
        LOG.info("Received command: %s %s", cmd, payload)
        asyncio.run_coroutine_threadsafe(run_command(cmd, payload), _LOOP)


_MQTT_CTX = {"supported": None, "dist_unit": None}


def _on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe(f"{CMD_PREFIX}#")
    if _MQTT_CTX["supported"] is not None:
        publish_discovery(client, _MQTT_CTX["supported"], _MQTT_CTX["dist_unit"])
    client.publish(AVAIL_TOPIC, "online", retain=True)
    LOG.info("MQTT connected (rc=%s) — subscribed to commands, discovery (re)published", reason_code)


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        LOG.warning("MQTT disconnected (%s) — reconnecting", reason_code)


def mqtt_connect():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="alpine_a290_addon")
    if cfg("MQTT_USER"):
        client.username_pw_set(cfg("MQTT_USER"), cfg("MQTT_PASS"))
    client.will_set(AVAIL_TOPIC, "offline", retain=True)
    client.on_message = _on_message
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=120)   # bounded backoff on broker drop
    LOG.info("Connecting to MQTT %s:%s", cfg("MQTT_HOST"), cfg("MQTT_PORT", "1883"))
    client.connect(cfg("MQTT_HOST"), int(cfg("MQTT_PORT", "1883") or "1883"), keepalive=60)
    client.loop_start()
    return client


def publish_discovery(client, supported_eps, dist_unit):
    skip = {obj for ep, objs in OPTIONAL_ENDPOINTS.items()
            if ep not in supported_eps for obj in objs}
    for obj in set(skip) | set(RETIRED_SENSORS):
        client.publish(f"{DISCOVERY_PREFIX}/sensor/{NODE}/{obj}/config", "", retain=True)
    published = 0
    for obj, (name, dev_class, unit, state_class) in SENSORS.items():
        if obj in skip:
            continue
        published += 1
        if obj in ("a290_range", "a290_mileage"):
            unit = dist_unit
            if dist_unit == "mi":
                dev_class = None  # else HA (metric) re-converts our miles back to km
        conf = {"name": name, "object_id": obj, "unique_id": obj,
                "state_topic": STATE_TOPIC, "value_template": "{{ value_json.%s }}" % obj[_P:],
                "availability_topic": AVAIL_TOPIC, "device": DEVICE}
        if dev_class:
            conf["device_class"] = dev_class
        if unit:
            conf["unit_of_measurement"] = unit
        if state_class:
            conf["state_class"] = state_class
        if obj in ICONS:
            conf["icon"] = ICONS[obj]
        if obj in DEFAULT_DISABLED_SENSORS:
            conf["enabled_by_default"] = False
        client.publish(f"{DISCOVERY_PREFIX}/sensor/{NODE}/{obj}/config", json.dumps(conf), retain=True)
    for obj, (name, dev_class) in BINARY_SENSORS.items():
        conf = {"name": name, "object_id": obj, "unique_id": obj,
                "state_topic": STATE_TOPIC, "value_template": "{{ value_json.%s }}" % obj[_P:],
                "payload_on": "on", "payload_off": "off",
                "availability_topic": AVAIL_TOPIC, "device": DEVICE}
        if dev_class:
            conf["device_class"] = dev_class
        if obj in ICONS:
            conf["icon"] = ICONS[obj]
        client.publish(f"{DISCOVERY_PREFIX}/binary_sensor/{NODE}/{obj}/config", json.dumps(conf), retain=True)
    tracker_topic = f"{DISCOVERY_PREFIX}/device_tracker/{NODE}/location/config"
    if PUBLISH_LOCATION:
        tracker = {"name": "Location", "object_id": "a290_car_location", "unique_id": "a290_car_location",
                   "state_topic": TRACKER_STATE_TOPIC, "json_attributes_topic": ATTR_TOPIC,
                   "availability_topic": AVAIL_TOPIC, "source_type": "gps", "device": DEVICE}
        client.publish(tracker_topic, json.dumps(tracker), retain=True)
    else:
        # Location opt-out: remove the tracker entity and clear any GPS previously retained on
        # the broker so an earlier fix doesn't linger after the user turns location off.
        client.publish(tracker_topic, "", retain=True)
        client.publish(ATTR_TOPIC, "", retain=True)
        client.publish(TRACKER_STATE_TOPIC, "", retain=True)
    buttons = []
    for obj, (name, icon, ep) in ACTION_BUTTONS.items():
        short = obj[_P:]
        topic = f"{DISCOVERY_PREFIX}/button/{NODE}/{short}/config"
        # Suppress the location-refresh button too when the user has opted out of location.
        if ep in supported_eps and not (ep == REFRESH_LOCATION_EP and not PUBLISH_LOCATION):
            conf = {"name": name, "object_id": obj, "unique_id": obj,
                    "command_topic": f"{CMD_PREFIX}{short}", "availability_topic": AVAIL_TOPIC,
                    "icon": icon, "device": DEVICE}
            client.publish(topic, json.dumps(conf), retain=True)
            buttons.append(short)
        else:
            client.publish(topic, "", retain=True)
    numbers = []
    soc_ok = SOC_ENDPOINT in supported_eps
    for obj, (name, icon, mn, mx, step) in NUMBERS.items():
        short = obj[_P:]
        topic = f"{DISCOVERY_PREFIX}/number/{NODE}/{short}/config"
        if soc_ok:
            conf = {"name": name, "object_id": obj, "unique_id": obj,
                    "state_topic": STATE_TOPIC, "value_template": "{{ value_json.%s }}" % short,
                    "command_topic": f"{CMD_PREFIX}{short}", "availability_topic": AVAIL_TOPIC,
                    "min": mn, "max": mx, "step": step, "mode": "slider",
                    "unit_of_measurement": "%", "device_class": "battery",
                    "optimistic": True, "icon": icon, "device": DEVICE}
            client.publish(topic, json.dumps(conf), retain=True)
            numbers.append(short)
        else:
            client.publish(topic, "", retain=True)
    LOG.info("Published discovery: %d sensors (%d unsupported cleared), %d binary_sensors, "
             "location=%s, buttons=%s, numbers=%s",
             published, len(skip), len(BINARY_SENSORS),
             "on" if PUBLISH_LOCATION else "off (cleared)", buttons or "none", numbers or "none")


KM_TO_MI = 0.621371


def _mi(km):
    v = _num(km)
    return round(v * KM_TO_MI, 1) if v is not None else None


def _dist(km, unit):
    """Convert a km value to the locale unit ('mi' or 'km')."""
    return _mi(km) if unit == "mi" else _num(km)


def _bool_on(v):
    return "on" if v in (True, "true", "True", "on", "ON", 1, "1") else "off"


def _find_precond(obj, _depth=0):
    """Locate the dict holding preconditioning* fields in the ev/settings payload,
    regardless of how the kcm response nests it."""
    if not isinstance(obj, dict) or _depth > 4:
        return {}
    if any(k.startswith("preconditioning") for k in obj):
        return obj
    for key in ("attributes", "data", "ev"):
        found = _find_precond(obj.get(key), _depth + 1)
        if found:
            return found
    return {}


def _fmt_hhmm(v):
    """A bare 'HHMM' charge time (the KCM format) -> 'HH:MM'; anything else returned as-is."""
    if v is None:
        return None
    s = str(v).strip()
    return f"{s[:2]}:{s[2:]}" if s.isdigit() and len(s) == 4 else (s or None)


def _charge_schedule_fields(settings):
    """KCM ev/settings charge-schedule summary — the chargeModeRq / chargeTimeStart /
    chargeDuration siblings of the preconditioning* fields (field names per renault-api's KCM
    charge-schedule CLI). Absent fields -> None, so a car that doesn't populate them just shows
    the sensors as unavailable rather than erroring. No extra API call: reuses the poll's
    existing get_charge_schedule() payload."""
    mode = settings.get("chargeModeRq")
    return {
        "charge_schedule_mode": mode.replace("_", " ").title() if isinstance(mode, str) and mode else None,
        "scheduled_charge_start": _fmt_hhmm(settings.get("chargeTimeStart")),
        "scheduled_charge_duration": _num(settings.get("chargeDuration")),
    }


_HVAC_DAYS = (("monday", "Mon"), ("tuesday", "Tue"), ("wednesday", "Wed"), ("thursday", "Thu"),
              ("friday", "Fri"), ("saturday", "Sat"), ("sunday", "Sun"))


def _fmt_ready(t):
    """Normalise an HVAC readyAtTime ('T07:00Z' / '0700' / '07:00:00') to 'HH:MM'."""
    if t is None:
        return None
    s = str(t).strip().lstrip("T").rstrip("Z")
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return _fmt_hhmm(s)


def _hvac_schedule_fields(settings):
    """Summarise get_hvac_settings() into the climate mode + the active schedule's per-day
    ready times ('Mon 07:00, Fri 08:00'). Reads the typed HvacSettingsData defensively
    (getattr), so a stub / None / no active schedule just yields None values."""
    mode = getattr(settings, "mode", None)
    out = {
        "climate_schedule_mode": mode.replace("_", " ").title() if isinstance(mode, str) and mode else None,
        "climate_ready_time": None,
    }
    active = next((s for s in (getattr(settings, "schedules", None) or [])
                   if getattr(s, "activated", False)), None)
    if active is not None:
        parts = []
        for day, abbr in _HVAC_DAYS:
            ds = getattr(active, day, None)
            t = _fmt_ready(getattr(ds, "readyAtTime", None)) if ds is not None else None
            if t:
                parts.append(f"{abbr} {t}")
        out["climate_ready_time"] = ", ".join(parts) or None
    return out


def _enum_label(enum_val, labels, raw):
    """Friendly label for a decoded enum; fall back to a prettified name, then raw."""
    if enum_val is not None:
        return labels.get(enum_val, enum_val.name.replace("_", " ").title())
    return "Unknown" if raw is None else f"Unknown ({raw})"


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
    if cmd in LOCATION_CMDS and not PUBLISH_LOCATION:
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


def update_charge_session(state, battery, capacity_kwh, charging):
    soc = battery.batteryLevel
    power = _num(getattr(battery, "chargingInstantaneousPower", None)) or 0
    energy = _num(getattr(battery, "batteryAvailableEnergy", None))
    if energy is None and soc is not None:
        energy = round(soc / 100.0 * capacity_kwh, 2)

    if charging and not state.get("session_active"):
        LOG.info("Charge session START (soc=%s%%, power=%skW)", soc, power)
        state.update(session_active=True, start_ts=now_ts(), start_soc=soc,
                     start_energy=energy, start_power=power, pwr_accum=0.0, pwr_count=0)
    if charging and state.get("session_active") and power > 0:
        state["pwr_accum"] = state.get("pwr_accum", 0.0) + power
        state["pwr_count"] = state.get("pwr_count", 0) + 1
    if not charging and state.get("session_active"):
        start_ts = state.get("start_ts")
        dur = round((now_ts() - start_ts) / 60.0) if start_ts else None
        avg = round(state["pwr_accum"] / state["pwr_count"], 2) if state.get("pwr_count") else state.get("start_power")
        rec_pct = (soc - state["start_soc"]) if (soc is not None and state.get("start_soc") is not None) else None
        rec_kwh = round(energy - state["start_energy"], 2) if (energy is not None and state.get("start_energy") is not None) else None
        state["last_charge"] = {
            "last_charge_start": iso(start_ts),
            "last_charge_end": iso(now_ts()),
            "last_charge_start_soc": state.get("start_soc"),
            "last_charge_end_soc": soc,
            "last_charge_start_energy": round(state["start_energy"], 2) if state.get("start_energy") is not None else None,
            "last_charge_end_energy": round(energy, 2) if energy is not None else None,
            "last_charge_recovered_pct": rec_pct,
            "last_charge_recovered_kwh": rec_kwh,
            "last_charge_duration_min": dur,
            "last_charge_average_power": avg,
            "last_charge_type": "Rapid/Public" if (avg or 0) > HOME_POWER_MAX_KW else "Home",
        }
        LOG.info("Charge session END (dur=%smin, +%s%%, +%skWh, avg=%skW)", dur, rec_pct, rec_kwh, avg)
        state["session_active"] = False
        state["charges_dirty"] = True   # a session just ended -> refetch authoritative charges next poll
    return state.get("last_charge", {})


# --- Authoritative Last Charge via the charges endpoint -------------------------------
# update_charge_session() above *infers* the last session by watching live battery polls.
# When the car exposes the charges endpoint we instead read Renault's own per-session record
# (get_charges -> raw_data["charges"]) and prefer it. The inferred value stays as the fallback
# for cars/sessions the endpoint doesn't (yet) cover; newest-end wins so a just-finished
# session shows live immediately, then gets replaced by the authoritative record once posted.
CHARGES_LOOKBACK_DAYS = 14
CHARGES_REFRESH_SEC = 1800   # between charges reads; an ended session forces an immediate refetch
# Renault's authoritative chargeEndDate is the *actual* stop time; the inferred fallback's end
# is when this add-on first *observed* charging==false (up to a poll cycle + posting delay
# later). Treat an endpoint session ending within this window of the inferred one as the SAME
# session, so the authoritative record still wins; only a live session ending materially later
# (a fresh charge the history hasn't posted yet) keeps the inferred values.
CHARGE_MATCH_TOLERANCE_SEC = 3600


def _epoch(s):
    """ISO-8601 string -> epoch seconds, or None if unparseable. Tolerates a trailing 'Z'."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _parse_charge_session(charges, capacity_kwh):
    """Most recent *completed* session from a get_charges() payload, in the same
    last_charge_* shape update_charge_session() produces ({} when none usable). Duration is
    derived from the start/end timestamps (sidesteps the per-model seconds-vs-minutes quirk in
    chargeDuration); energy/power fall back to capacity-scaled SoC when the API omits kWh."""
    done = [c for c in (charges or []) if isinstance(c, dict) and c.get("chargeEndDate")]
    if not done:
        return {}
    c = max(done, key=lambda x: _epoch(x.get("chargeEndDate")) or 0.0)
    start_soc = _num(c.get("chargeStartBatteryLevel"))
    end_soc = _num(c.get("chargeEndBatteryLevel"))
    rec_pct = _num(c.get("chargeBatteryLevelRecovered"))
    if rec_pct is None and None not in (start_soc, end_soc):
        rec_pct = round(end_soc - start_soc, 2)
    rec_kwh = _num(c.get("chargeEnergyRecovered"))
    if rec_kwh is None and rec_pct is not None:
        rec_kwh = round(rec_pct / 100.0 * capacity_kwh, 2)
    s_ep, e_ep = _epoch(c.get("chargeStartDate")), _epoch(c.get("chargeEndDate"))
    dur = round((e_ep - s_ep) / 60.0) if (s_ep is not None and e_ep is not None and e_ep >= s_ep) else None
    avg = round(rec_kwh / (dur / 60.0), 2) if (rec_kwh and dur) else _num(c.get("chargeStartInstantaneousPower"))
    start_energy = round(start_soc / 100.0 * capacity_kwh, 2) if start_soc is not None else None
    end_energy = round(end_soc / 100.0 * capacity_kwh, 2) if end_soc is not None else None
    return {
        "last_charge_start": c.get("chargeStartDate"),
        "last_charge_end": c.get("chargeEndDate"),
        "last_charge_start_soc": start_soc,
        "last_charge_end_soc": end_soc,
        "last_charge_start_energy": start_energy,
        "last_charge_end_energy": end_energy,
        "last_charge_recovered_pct": rec_pct,
        "last_charge_recovered_kwh": round(rec_kwh, 2) if rec_kwh is not None else None,
        "last_charge_duration_min": dur,
        "last_charge_average_power": avg,
        "last_charge_type": "Rapid/Public" if (avg or 0) > HOME_POWER_MAX_KW else "Home",
    }


def _prefer_real_charge(real, live):
    """True when the authoritative endpoint session `real` should replace the inferred `live`.
    The endpoint wins unless the inferred session ends *materially* later than the endpoint's
    (more than CHARGE_MATCH_TOLERANCE_SEC) — i.e. a just-finished session the history hasn't
    posted yet. An unparseable endpoint date never displaces a live session."""
    if not real:
        return False
    if not live:
        return True
    re_, le = _epoch(real.get("last_charge_end")), _epoch(live.get("last_charge_end"))
    if re_ is None:
        return False
    if le is None:
        return True
    return le - re_ <= CHARGE_MATCH_TOLERANCE_SEC


def _due_for_charges(state):
    """Throttle charges reads: once per CHARGES_REFRESH_SEC, or immediately after a session end."""
    if state.get("charges_dirty"):
        return True
    last = state.get("charges_last_fetch")
    return last is None or (now_ts() - last) >= CHARGES_REFRESH_SEC


async def fetch_real_last_charge(vehicle, capacity_kwh):
    """Read recent charge sessions and return the latest completed one in last_charge_* shape."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=CHARGES_LOOKBACK_DAYS)
    end = now + timedelta(days=1)   # get_charges params are day-granular (%Y%m%d); query through
                                    # tomorrow so a session that ended *today* is in-window
    res = await vehicle.get_charges(start, end)
    raw = getattr(res, "raw_data", None) or {}
    return _parse_charge_session(raw.get("charges"), capacity_kwh)


async def resolve_last_charge(vehicle, state, supported_eps, capacity_kwh, live_lc):
    """Pick the Last Charge to publish: the authoritative charges-endpoint session when it's
    at least as recent as the inferred one, else the inferred session. Caches the endpoint
    result in state so it's only re-read on the throttle/after a session ends."""
    if CHARGES_ENDPOINT in supported_eps and _due_for_charges(state):
        state["charges_dirty"] = False
        state["charges_last_fetch"] = now_ts()
        try:
            real = await fetch_real_last_charge(vehicle, capacity_kwh)
            if real:
                state["real_last_charge"] = real
        except Exception as err:  # noqa: BLE001
            LOG.warning("charges endpoint unavailable: %s", err)
    real_lc = state.get("real_last_charge", {})
    return real_lc if _prefer_real_charge(real_lc, live_lc) else live_lc


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
        "available_energy": _num(getattr(battery, "batteryAvailableEnergy", None)),
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
    if PUBLISH_LOCATION:   # skipped entirely when the user opts out of location publishing
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
    global _LOOP
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

    _LOOP = asyncio.get_running_loop()
    health = await start_health_server()
    state = load_state()
    vsession = VehicleSession(locale)
    supported = await detect_supported(vsession)
    _MQTT_CTX["supported"], _MQTT_CTX["dist_unit"] = supported, dist_unit
    _LATEST["supported"] = sorted(supported)
    _LATEST["dist_unit"] = dist_unit  # so the status panel can label range/mileage
    client = mqtt_connect()
    publish_discovery(client, supported, dist_unit)
    await deploy.run_deploy()

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        _LOOP.add_signal_handler(sig, stop.set)

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
            client.publish(STATE_TOPIC, json.dumps(data), retain=True)
            _LATEST.update(ok=True, last_poll=iso(now_ts()), data=data)
            if location_attrs:
                client.publish(ATTR_TOPIC, json.dumps(location_attrs), retain=True)
                client.publish(TRACKER_STATE_TOPIC, "online", retain=True)
            client.publish(AVAIL_TOPIC, "online", retain=True)
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
            client.publish(STATE_TOPIC, json.dumps({
                "api_auth_failure": "on" if auth else "off",
                "data_stale": "on" if stale else "off",
            }), retain=True)
            client.publish(AVAIL_TOPIC, "online", retain=True)
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
    client.publish(AVAIL_TOPIC, "offline", retain=True)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
