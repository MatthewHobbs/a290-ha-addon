"""Tests for the mqtt seam: HA MQTT-discovery publishing, the broker client wiring, and the
connect/message/disconnect callbacks. The discovery-template/data-key contract lives here — it's
the class of bug that has shipped broken dashboard tiles.

publish_discovery reads mqtt.PUBLISH_LOCATION and _MQTT_CTX in the mqtt namespace, so tests
patch/set those on the mqtt module. _on_message dispatches via the injected _COMMAND_HANDLER +
_LOOP (main wires real ones at startup)."""
import json

import catalog
from renault_ha_core import mqtt


class StubClient:
    """Captures MQTT publishes/subscribes so we can assert on discovery payloads."""

    def __init__(self):
        self.pub = {}
        self.subs = []

    def publish(self, topic, payload, retain=False):
        self.pub[topic] = payload

    def subscribe(self, topic):
        self.subs.append(topic)


# --------------------------------------------------------------------------- #
# discovery template / data-key contract
# --------------------------------------------------------------------------- #
def test_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    for obj in catalog.SENSORS:
        payload = c.pub.get(f"homeassistant/sensor/{mqtt.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj[len("a290_"):]


def test_binary_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    for obj in catalog.BINARY_SENSORS:
        payload = c.pub.get(f"homeassistant/binary_sensor/{mqtt.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj[len("a290_"):]


def test_distance_device_class_dropped_only_for_miles():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    for obj in ("a290_range", "a290_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"])
        assert conf.get("device_class") == "distance" and conf["unit_of_measurement"] == "km"

    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "mi")
    for obj in ("a290_range", "a290_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"])
        assert "device_class" not in conf and conf["unit_of_measurement"] == "mi"


def test_optional_sensors_cleared_when_unsupported():
    c = StubClient()
    mqtt.publish_discovery(c, set(), "km")   # nothing supported
    for obj in catalog.OPTIONAL_ENDPOINTS["pressure"]:
        assert c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"] == ""


def test_charge_start_button_cleared_others_published():
    c = StubClient()
    supported = {ep for _n, _i, ep in catalog.ACTION_BUTTONS.values()
                 if ep != "actions/charge-start"}
    mqtt.publish_discovery(c, supported, "mi")
    base = f"homeassistant/button/{mqtt.NODE}"
    assert c.pub[f"{base}/charge_start/config"] == ""              # forbidden -> cleared
    assert json.loads(c.pub[f"{base}/horn/config"])["name"] == "Sound Horn"
    assert json.loads(c.pub[f"{base}/climate_start/config"])["icon"] == "mdi:air-conditioner"


def test_numbers_published_when_soc_supported():
    c = StubClient()
    mqtt.publish_discovery(c, {catalog.SOC_ENDPOINT}, "mi")
    base = f"homeassistant/number/{mqtt.NODE}"
    for obj, (name, _icon, mn, mx, step) in catalog.NUMBERS.items():
        short = obj[len("a290_"):]
        conf = json.loads(c.pub[f"{base}/{short}/config"])
        assert conf["name"] == name
        assert conf["command_topic"] == f"{mqtt.CMD_PREFIX}{short}"
        assert conf["value_template"] == "{{ value_json.%s }}" % short
        assert (conf["min"], conf["max"], conf["step"]) == (mn, mx, step)
        assert conf["device_class"] == "battery" and conf["unit_of_measurement"] == "%"


def test_numbers_cleared_when_soc_unsupported():
    c = StubClient()
    mqtt.publish_discovery(c, set(), "mi")   # soc-levels not supported
    base = f"homeassistant/number/{mqtt.NODE}"
    for obj in catalog.NUMBERS:
        assert c.pub[f"{base}/{obj[len('a290_'):]}/config"] == ""


def test_retired_soc_sensors_are_cleared():
    c = StubClient()
    mqtt.publish_discovery(c, {catalog.SOC_ENDPOINT}, "mi")
    for obj in ("a290_soc_min", "a290_soc_target"):
        assert c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"] == ""


# --------------------------------------------------------------------------- #
# location publishing (opt-out)
# --------------------------------------------------------------------------- #
def test_publish_discovery_location_enabled_vs_disabled(monkeypatch):
    tracker_topic = f"{mqtt.DISCOVERY_PREFIX}/device_tracker/{mqtt.NODE}/location/config"

    # enabled (default): a populated device_tracker config is published
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    assert '"source_type": "gps"' in c.pub[tracker_topic]

    # disabled: the tracker is cleared AND the retained GPS topics are wiped off the broker
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    assert c.pub[tracker_topic] == ""
    assert c.pub[mqtt.ATTR_TOPIC] == ""
    assert c.pub[mqtt.TRACKER_STATE_TOPIC] == ""


def test_refresh_location_button_cleared_when_location_disabled(monkeypatch):
    # the location-refresh command suffix, derived from the catalog action table
    (cmd,) = tuple(obj[len("a290_"):] for obj, (_n, _i, ep) in catalog.ACTION_BUTTONS.items()
                   if ep == catalog.REFRESH_LOCATION_EP)
    btn_topic = f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/{cmd}/config"
    eps = set(catalog.OPTIONAL_ENDPOINTS) | {catalog.REFRESH_LOCATION_EP}

    # location on: the refresh-location button is published
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    c = StubClient()
    mqtt.publish_discovery(c, eps, "km")
    assert "command_topic" in c.pub[btn_topic]

    # location off: the button is cleared even though the endpoint is supported
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    c = StubClient()
    mqtt.publish_discovery(c, eps, "km")
    assert c.pub[btn_topic] == ""


# --------------------------------------------------------------------------- #
# broker client + callbacks
# --------------------------------------------------------------------------- #
def test_mqtt_connect(monkeypatch):
    seen = {}

    class FakeClient:
        def username_pw_set(self, u, p):
            seen["auth"] = (u, p)

        def will_set(self, *a, **k):
            seen["will"] = True

        def reconnect_delay_set(self, **k):
            seen["delay"] = k

        def connect(self, host, port, keepalive):
            seen["connect"] = (host, port)

        def loop_start(self):
            seen["loop"] = True

    monkeypatch.setattr(mqtt.paho_mqtt, "Client", lambda *a, **k: FakeClient())
    monkeypatch.setenv("MQTT_HOST", "broker")
    monkeypatch.setenv("MQTT_USER", "u")
    monkeypatch.setenv("MQTT_PASS", "p")
    mqtt.mqtt_connect()
    assert seen["connect"][0] == "broker" and seen["loop"] and seen["auth"] == ("u", "p")
    assert seen["delay"] == {"min_delay": 1, "max_delay": 120}   # bounded reconnect backoff


def test_on_disconnect_logs_both_paths():
    mqtt._on_disconnect(None, None, None, 0)   # clean disconnect — no warning
    mqtt._on_disconnect(None, None, None, 1)   # unexpected — logs a reconnect warning


def test_on_connect_resubscribes_and_publishes():
    c = StubClient()
    mqtt._MQTT_CTX["supported"], mqtt._MQTT_CTX["dist_unit"] = set(catalog.OPTIONAL_ENDPOINTS), "km"
    mqtt._on_connect(c, None, None, 0)
    assert f"{mqtt.CMD_PREFIX}#" in c.subs
    assert any("/sensor/alpine_a290/" in t for t in c.pub)
    assert c.pub[mqtt.AVAIL_TOPIC] == "online"


class _Msg:
    def __init__(self, topic, payload=b""):
        self.topic = topic
        self.payload = payload


def test_on_message_dispatches_via_injected_handler(monkeypatch):
    recorded = []

    def fake_handler(cmd, payload=""):   # sync fake standing in for main.run_command
        recorded.append((cmd, payload))

        async def _noop():
            return None
        return _noop()

    scheduled = []

    def fake_schedule(coro, loop):
        scheduled.append(coro)
        coro.close()                     # avoid 'never awaited' warning

    monkeypatch.setattr(mqtt, "_COMMAND_HANDLER", fake_handler)
    monkeypatch.setattr(mqtt, "_LOOP", object())
    monkeypatch.setattr(mqtt.asyncio, "run_coroutine_threadsafe", fake_schedule)

    mqtt._on_message(None, None, _Msg(f"{mqtt.CMD_PREFIX}horn"))
    assert recorded == [("horn", "")]

    mqtt._on_message(None, None, _Msg(f"{mqtt.CMD_PREFIX}soc_target", b"80"))
    assert recorded[-1] == ("soc_target", "80")

    mqtt._on_message(None, None, _Msg("unrelated/topic"))   # non-command -> ignored
    assert len(recorded) == 2
