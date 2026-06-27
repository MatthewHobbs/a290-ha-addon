#!/usr/bin/env python3
"""Seed a Home Assistant instance for the dashboard UI test.

No MQTT / add-on needed: every entity the dashboards reference is given a representative
state via the REST /api/states API (cards read hass.states regardless of the backing
integration), the custom-card Lovelace resources are registered, and the 'standard' and
'bubble' dashboards are created from the bundled YAML via the WebSocket API.

Usage: seed.py --base http://localhost:8123 --token <access_token> [--dashboards <dir>]
"""
import argparse
import asyncio
import os
import re
import sys

import aiohttp
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR_DEFAULT = os.path.join(HERE, "..", "alpine_a290", "dashboards")
DASHBOARDS = {"alpine-standard": "front-end.txt", "alpine-bubble": "front-end-bubble.txt"}

# Lovelace resources. card-mod first (it patches card rendering); mushroom/button-card are
# vendored single-file bundles served from /local/cards; bubble-card is chunked so it loads
# from jsDelivr (chunks co-locate there); Zen Dots is the dashboards' display font.
RESOURCES = [
    ("/local/cards/card-mod.js", "module"),
    ("/local/cards/mushroom.js", "module"),
    ("/local/cards/button-card.js", "module"),
    ("https://cdn.jsdelivr.net/gh/Clooos/Bubble-Card@main/dist/bubble-card.js", "module"),
    ("https://fonts.googleapis.com/css2?family=Zen+Dots&display=swap", "css"),
]

ENTITY_RE = re.compile(
    r"\b(sensor|binary_sensor|number|device_tracker|switch|button|climate|select|light|"
    r"cover|lock|fan|person|zone|sun|weather|input_boolean|input_number|input_text)"
    r"\.[a-z0-9_]+\b")

# Representative states for the A290 entities — realistic lengths/values to reproduce the
# real layout (and thus any truncation). Anything referenced but not listed gets a sane
# per-domain default below.
KNOWN = {
    "sensor.alpine_a290_battery_level": ("80", {"unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_range": ("147.3", {"unit_of_measurement": "mi"}),
    "sensor.alpine_a290_mileage": ("12345", {"unit_of_measurement": "mi"}),
    "sensor.alpine_a290_charging_power": ("7.4", {"unit_of_measurement": "kW"}),
    "sensor.alpine_a290_charging_remaining": ("215", {"unit_of_measurement": "min"}),
    "sensor.alpine_a290_charging_time_remaining": ("215", {"unit_of_measurement": "min"}),
    "sensor.alpine_a290_available_energy": ("41.6", {"unit_of_measurement": "kWh"}),
    "sensor.alpine_a290_battery_temperature": ("18", {"unit_of_measurement": "°C"}),
    "sensor.alpine_a290_external_temperature": ("12", {"unit_of_measurement": "°C"}),
    "sensor.alpine_a290_plug_status": ("Connected", {"icon": "mdi:power-plug"}),
    "sensor.alpine_a290_charging_status": ("Rapid/Public", {"icon": "mdi:battery-charging"}),
    "sensor.alpine_a290_charging_flap": ("Open: Plugged In", {"icon": "mdi:ev-plug-type2"}),
    "sensor.alpine_a290_drive_side": ("RHD", {"icon": "mdi:steering"}),
    "sensor.alpine_a290_hvac_status": ("Idle", {"icon": "mdi:fan"}),
    "sensor.alpine_a290_charge_mode": ("Scheduled", {"icon": "mdi:ev-station"}),
    "sensor.alpine_a290_charge_target_soc": ("80", {"unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_minimum_soc": ("20", {"unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_hvac_soc_threshold": ("40", {"unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_preconditioning_temperature": ("20", {"unit_of_measurement": "°C"}),
    "sensor.alpine_a290_last_charge_type": ("Rapid/Public", {"icon": "mdi:ev-station"}),
    "sensor.alpine_a290_last_charge_average_power": ("48.2", {"unit_of_measurement": "kW"}),
    "sensor.alpine_a290_last_charge_duration": ("42", {"unit_of_measurement": "min"}),
    "sensor.alpine_a290_last_charge_soc_recovered": ("55", {"unit_of_measurement": "%"}),
    "sensor.alpine_a290_last_charge_energy_recovered": ("28.6", {"unit_of_measurement": "kWh"}),
    "sensor.alpine_a290_last_charge_start": ("2026-06-26T18:04:00+00:00", {"device_class": "timestamp"}),
    "sensor.alpine_a290_last_charge_end": ("2026-06-26T18:46:00+00:00", {"device_class": "timestamp"}),
    "sensor.alpine_a290_last_updated": ("2026-06-27T09:15:00+00:00", {"device_class": "timestamp"}),
    "sensor.alpine_a290_hvac_last_activity": ("2026-06-27T08:50:00+00:00", {"device_class": "timestamp"}),
    "sensor.alpine_a290_gps_last_activity": ("2026-06-27T09:10:00+00:00", {"device_class": "timestamp"}),
}
DEFAULTS = {
    "binary_sensor": ("off", {}),
    "number": ("50", {"min": 15, "max": 100, "step": 5, "mode": "slider", "unit_of_measurement": "%"}),
    "device_tracker": ("home", {"latitude": 51.5074, "longitude": -0.1278, "gps_accuracy": 8, "source_type": "gps"}),
    "switch": ("off", {}),
    "button": ("unknown", {}),
}


def _name_from_id(eid):
    obj = eid.split(".", 1)[1]
    return " ".join("A290" if w == "a290" else w.capitalize() for w in obj.split("_"))


def state_for(eid):
    if eid in KNOWN:
        st, attrs = KNOWN[eid]
    else:
        st, attrs = DEFAULTS.get(eid.split(".")[0], ("42", {}))
    attrs = dict(attrs)
    attrs.setdefault("friendly_name", _name_from_id(eid))
    return st, attrs


def extract_entities(texts):
    ids = set()
    for t in texts:
        ids.update(m.group(0) for m in ENTITY_RE.finditer(t))
    # zone.home / sun.sun etc. are HA built-ins — don't override them
    return sorted(e for e in ids if not e.startswith(("zone.", "sun.", "person.")))


class WS:
    """Minimal HA WebSocket client (direct connection with a long-lived/access token)."""

    def __init__(self, ws, token):
        self._ws, self._token, self._id = ws, token, 0

    async def auth(self):
        await self._ws.receive_json()  # auth_required
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        if (await self._ws.receive_json()).get("type") != "auth_ok":
            raise RuntimeError("HA WebSocket auth failed")

    async def cmd(self, **payload):
        self._id += 1
        payload["id"] = self._id
        await self._ws.send_json(payload)
        while True:
            msg = await self._ws.receive_json()
            if msg.get("id") == self._id and msg.get("type") == "result":
                if not msg.get("success", False):
                    raise RuntimeError(f"{payload['type']} failed: {msg.get('error')}")
                return msg.get("result")


async def seed_states(session, base, token, entities):
    headers = {"Authorization": f"Bearer {token}"}
    for eid in entities:
        st, attrs = state_for(eid)
        async with session.post(f"{base}/api/states/{eid}", headers=headers,
                                json={"state": st, "attributes": attrs}) as r:
            if r.status not in (200, 201):
                print(f"  ! {eid}: HTTP {r.status}", file=sys.stderr)
    print(f"  seeded {len(entities)} entity states")


def load_views(dash_dir, fname):
    with open(os.path.join(dash_dir, fname), encoding="utf-8") as fh:
        views = yaml.safe_load(fh.read())
    if not isinstance(views, list):
        raise ValueError(f"{fname} did not parse to a list of views")
    return views


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--token", required=True)
    ap.add_argument("--dashboards", default=DASH_DIR_DEFAULT)
    args = ap.parse_args()

    dash_texts = [open(os.path.join(args.dashboards, f), encoding="utf-8").read()
                  for f in DASHBOARDS.values()]
    entities = extract_entities(dash_texts)

    async with aiohttp.ClientSession() as session:
        print("Seeding entity states…")
        await seed_states(session, args.base, args.token, entities)

        ws_url = args.base.replace("http", "ws", 1) + "/api/websocket"
        async with session.ws_connect(ws_url) as ws:
            api = WS(ws, args.token)
            await api.auth()

            existing_res = {r.get("url") for r in (await api.cmd(type="lovelace/resources") or [])}
            for url, rtype in RESOURCES:
                if url not in existing_res:
                    await api.cmd(type="lovelace/resources/create", url=url, res_type=rtype)
            print(f"  registered {len(RESOURCES)} resources")

            existing_dash = {d.get("url_path") for d in (await api.cmd(type="lovelace/dashboards/list") or [])}
            for url_path, fname in DASHBOARDS.items():
                if url_path not in existing_dash:
                    await api.cmd(type="lovelace/dashboards/create", url_path=url_path,
                                  title=url_path, icon="mdi:car-sports", mode="storage",
                                  show_in_sidebar=True, require_admin=False)
                views = load_views(args.dashboards, fname)
                await api.cmd(type="lovelace/config/save", url_path=url_path,
                              config={"title": url_path, "views": views})
                print(f"  deployed dashboard '{url_path}' ({len(views)} views)")
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
