"""MQTT integration seam for the Alpine A290 add-on: the broker client, its connect/message/
disconnect callbacks, and Home Assistant MQTT-discovery publishing. Owns the topic topology
(state/attributes/availability/command topics), the HA device block, and the location-publish
policy.

This is a leaf of the integration layer: it imports config/catalog only — never main. The one
edge that would otherwise point back at main (an inbound command must run main's async
run_command on the event loop) is inverted via injection: main sets `mqtt._LOOP` and
`mqtt._COMMAND_HANDLER` at startup, and `_on_message` calls the injected handler. That keeps
the dependency one-directional (main -> mqtt) with no cycle.
"""
import asyncio
import json
import logging

import paho.mqtt.client as paho_mqtt
from catalog import (
    ACTION_BUTTONS,
    BINARY_SENSORS,
    DEFAULT_DISABLED_SENSORS,
    ICONS,
    NUMBERS,
    OBJ_PREFIX,
    OPTIONAL_ENDPOINTS,
    REFRESH_LOCATION_EP,
    RETIRED_SENSORS,
    SENSORS,
    SOC_ENDPOINT,
)
from renault_ha_core.config import _opt_flag, cfg

LOG = logging.getLogger("alpine_a290")

DISCOVERY_PREFIX = "homeassistant"
NODE = "alpine_a290"
STATE_TOPIC = f"{NODE}/state"
ATTR_TOPIC = f"{NODE}/location/attributes"
TRACKER_STATE_TOPIC = f"{NODE}/location/state"
AVAIL_TOPIC = f"{NODE}/availability"
CMD_PREFIX = f"{NODE}/cmd/"
_P = len(OBJ_PREFIX)
DEVICE = {"identifiers": [NODE], "name": "Alpine A290", "manufacturer": "Alpine", "model": "A290"}

# Location publishing is opt-out (default on). Gates the device_tracker discovery entity, the
# refresh-location button, and (in main) the poll-time GPS read + the refresh command. Single
# source of truth — main reads it as mqtt.PUBLISH_LOCATION.
PUBLISH_LOCATION = _opt_flag("A290_PUBLISH_LOCATION", True)

# Injected by main at startup (see module docstring). _LOOP is the running event loop an inbound
# command is scheduled onto; _COMMAND_HANDLER is main's async run_command(cmd, payload).
_LOOP = None
_COMMAND_HANDLER = None

_MQTT_CTX = {"supported": None, "dist_unit": None}


def _on_message(client, userdata, msg):
    if _LOOP is not None and _COMMAND_HANDLER is not None and msg.topic.startswith(CMD_PREFIX):
        cmd = msg.topic[len(CMD_PREFIX):]
        payload = msg.payload.decode(errors="replace") if msg.payload else ""
        LOG.info("Received command: %s %s", cmd, payload)
        asyncio.run_coroutine_threadsafe(_COMMAND_HANDLER(cmd, payload), _LOOP)


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
    client = paho_mqtt.Client(paho_mqtt.CallbackAPIVersion.VERSION2, client_id="alpine_a290_addon")
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
