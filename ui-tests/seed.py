#!/usr/bin/env python3
"""Seed a Home Assistant instance for the dashboard UI test.

No MQTT / add-on needed: every entity the dashboards reference is given a representative
state via the REST /api/states API (cards read hass.states regardless of the backing
integration), the custom-card Lovelace resources are registered, and the 'standard' and
'bubble' dashboards are created from the bundled YAML via the WebSocket API.

The problem-class binary sensors the dashboards reference, and the demo entities in TOGGLES, are
rendered in several passes, each a combination production can publish (derive_passes): the normal
pass above, then every named pass listed by --list-passes. --pass <name> reseeds an already-seeded
instance for one of them. With --manifest, either writes the dashboards to check in that pass, the
card labels that must be visible on each, and the Bubble pop-ups to open on each with the labels
that must be visible inside them.

Usage: seed.py --base http://localhost:8123 --token <access_token> [--dashboards <dir>]
                [--pass <name>] [--manifest <manifest.json>]
       seed.py --list-passes
"""
import argparse
import asyncio
import functools
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import aiohttp
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR_DEFAULT = os.path.join(HERE, "..", "alpine_a290", "dashboards")
def _ago(**delta):
    """An ISO timestamp `delta` before now, for entities the frontend renders as relative time."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


DASHBOARDS = {"alpine-standard": "front-end.txt", "alpine-bubble": "front-end-bubble.txt"}
APP_DIR = os.path.join(HERE, "..", "alpine_a290", "app")

# Demo charger entities so the optional "Smart Charging" controls are rendered (and overflow-
# checked) by the harness. We set these into the environment and reuse the add-on's own
# deploy-time injection, so the harness exercises exactly what users get when they configure
# the charger_* options — the standard-dashboard card and the bubble pop-up "tab".
CHARGER_DEMO = {
    "A290_CHARGER_SMART_CHARGE": "switch.demo_intelligent_smart_charge",
    "A290_CHARGER_BUMP_CHARGE": "switch.demo_intelligent_bump_charge",
    "A290_CHARGER_TARGET_SOC": "number.demo_intelligent_charge_target",
    "A290_CHARGER_TARGET_TIME": "select.demo_intelligent_target_time",
    "A290_CHARGER_DISPATCHING": "binary_sensor.demo_intelligent_dispatching",
}

# Lovelace resources. card-mod is not one: run.sh loads it as a frontend module, ahead of the cards
# it patches (listing it here too would load it twice, which its README warns against). The cards
# are vendored single-file bundles served same-origin from /local/cards (see run.sh) so no card is
# fetched over the network at render time; Zen Dots (the display font) is the one remaining remote
# load.
RESOURCES = [
    ("/local/cards/mushroom.js", "module"),
    ("/local/cards/button-card.js", "module"),
    ("/local/cards/bubble-card.js", "module"),
    ("https://fonts.googleapis.com/css2?family=Zen+Dots&display=swap", "css"),
]

ENTITY_RE = re.compile(
    r"\b(sensor|binary_sensor|number|device_tracker|switch|button|climate|select|light|"
    r"cover|lock|fan|person|zone|sun|weather|input_boolean|input_number|input_text|"
    r"input_button|input_datetime)"
    r"\.[a-z0-9_]+\b")

# Representative states for the A290 entities — realistic lengths/values to reproduce the
# real layout (and thus any truncation). Anything referenced but not listed gets a sane
# per-domain default below.
KNOWN = {
    "sensor.alpine_a290_battery_level": ("80", {"unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_range": ("147.3", {"unit_of_measurement": "mi"}),
    "sensor.alpine_a290_mileage": ("12345", {"unit_of_measurement": "mi"}),
    "sensor.alpine_a290_charging_power": ("7.4", {"unit_of_measurement": "kW"}),
    "sensor.alpine_a290_charging_time_remaining": ("215", {"unit_of_measurement": "min"}),
    "sensor.alpine_a290_available_energy": ("41.6", {"unit_of_measurement": "kWh"}),
    "sensor.alpine_a290_battery_temperature": ("18", {"unit_of_measurement": "°C"}),
    # Ids are slug(device + entity NAME): object_id a290_external_temperature is "Outside Temperature".
    "sensor.alpine_a290_outside_temperature": ("12", {"unit_of_measurement": "°C"}),
    "sensor.alpine_a290_plug_status": ("Connected", {"icon": "mdi:power-plug"}),
    "sensor.alpine_a290_charging_status": ("Rapid/Public", {"icon": "mdi:battery-charging"}),
    "sensor.alpine_a290_charging_flap": ("Open: Plugged In", {"icon": "mdi:ev-plug-type2"}),
    "sensor.alpine_a290_drive_side": ("RHD", {"icon": "mdi:steering"}),
    "sensor.alpine_a290_hvac_status": ("Idle", {"icon": "mdi:fan"}),
    "sensor.alpine_a290_charge_mode": ("Scheduled", {"icon": "mdi:ev-station"}),
    # Writable numbers, not sensors: the dashboards read number.* (catalog.NUMBERS). Seeded as
    # sensor.* they were never referenced, and the numbers fell back to the generic "50".
    # device_class battery as the core publishes it, so the seeded control matches the real one.
    "number.alpine_a290_charge_target_soc": ("80", {"min": 55, "max": 100, "step": 5, "mode": "slider",
        "unit_of_measurement": "%", "device_class": "battery"}),
    "number.alpine_a290_minimum_soc": ("20", {"min": 15, "max": 45, "step": 5, "mode": "slider",
        "unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_hvac_soc_threshold": ("40", {"unit_of_measurement": "%", "device_class": "battery"}),
    "sensor.alpine_a290_preconditioning_temperature": ("20", {"unit_of_measurement": "°C"}),
    "sensor.alpine_a290_last_charge_type": ("Rapid/Public", {"icon": "mdi:ev-station"}),
    "sensor.alpine_a290_last_charge_average_power": ("48.2", {"unit_of_measurement": "kW"}),
    "sensor.alpine_a290_last_charge_duration": ("42", {"unit_of_measurement": "min"}),
    "sensor.alpine_a290_last_charge_soc_recovered": ("55", {"unit_of_measurement": "%"}),
    "sensor.alpine_a290_last_charge_energy_recovered": ("28.6", {"unit_of_measurement": "kWh"}),
    # Timestamps are seeded RELATIVE to the run, not as fixed dates. A device_class:timestamp
    # sensor is rendered by mushroom as relative text ("2 months ago"), so a hard-coded date
    # produces different PIXELS as the wall clock moves past each unit boundary — fine for an
    # overflow check, fatal for comparing a screenshot against a committed one. Anchoring to
    # now keeps the rendered text identical on every run ("3 hours ago" is always "3 hours
    # ago"). Offsets are xx:12, NOT xx:30: half-past is exactly the rounding boundary, so if the
    # frontend rounds to nearest rather than truncating, a few seconds of drift between
    # seeding and capture flips "3 hours ago" to "4 hours ago" and changes the pixels. xx:12
    # reads the same under either rule and leaves ~12 minutes of slack before any boundary.
    "sensor.alpine_a290_last_charge_start": (_ago(hours=14, minutes=12), {"device_class": "timestamp"}),
    "sensor.alpine_a290_last_charge_end": (_ago(hours=13, minutes=12), {"device_class": "timestamp"}),
    # Seeded ON so the render gate exercises the "Car Parked" tile — which is the state a
    # normally-parked car sits in, and the branch carrying the longer text. poll_failing off
    # alongside it is the pairing that means "working fine, car simply parked". The named passes
    # (derive_passes) render the branches this one hides.
    # last_updated's state is set per pass by DERIVED_AGE below, to match data_stale.
    "binary_sensor.alpine_a290_data_stale": ("on", {"device_class": "problem"}),
    "binary_sensor.alpine_a290_poll_failing": ("off", {"device_class": "problem"}),
    "sensor.alpine_a290_last_updated": (None, {"device_class": "timestamp"}),
    "sensor.alpine_a290_hvac_last_activity": (_ago(hours=5, minutes=12), {"device_class": "timestamp"}),
    "sensor.alpine_a290_gps_last_activity": (_ago(hours=4, minutes=12), {"device_class": "timestamp"}),
    # Demo Octopus Intelligent charger entities (Smart Charging card / bubble pop-up).
    "switch.demo_intelligent_smart_charge": ("on", {"icon": "mdi:ev-station"}),
    "switch.demo_intelligent_bump_charge": ("off", {"icon": "mdi:battery-plus-variant"}),
    "number.demo_intelligent_charge_target": ("90", {"min": 10, "max": 100, "step": 1,
        "mode": "slider", "unit_of_measurement": "%", "device_class": "battery"}),
    "select.demo_intelligent_target_time": ("06:30", {"icon": "mdi:clock-outline",
        "options": ["04:00", "04:30", "05:00", "05:30", "06:00", "06:30", "07:00", "07:30",
                    "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00"]}),
    "binary_sensor.demo_intelligent_dispatching": ("on", {"friendly_name": "Dispatching",
        "next_start": "2026-06-27T23:30:00+00:00", "next_end": "2026-06-28T05:30:00+00:00"}),
}
# Timestamps a problem sensor is computed from, so no pass seeds a pair production cannot publish:
# data_stale is on exactly when last_updated is older than the stale_hours option (36h default, 48h
# maximum). {timestamp: (problem sensor, option, {its state: age})}; every pass reseeds these.
DERIVED_AGE = {
    "sensor.alpine_a290_last_updated": ("binary_sensor.alpine_a290_data_stale", "stale_hours", {
        "on": {"days": 2, "hours": 3, "minutes": 12},  # 51h12m: stale under any stale_hours
        "off": {"hours": 3, "minutes": 12},
    }),
}
# States production cannot publish apart: {(sensor, state): {sensor: the state it forces}}.
# poll_failing is on only when the last successful poll is older than stale_hours, and the car
# timestamp data_stale ages was read by a poll, so it is at least as old (main.freshness_fields).
# Every pass is closed over these; a branch that closure keeps off the page gets a pass of its own.
IMPLIES = {
    ("binary_sensor.alpine_a290_poll_failing", "on"): {"binary_sensor.alpine_a290_data_stale": "on"},
}
# Demo entities with a second state production publishes that the KNOWN seed keeps off the page:
# {entity: that state}. Each gets a "<entity>_<state>" pass of its own (derive_passes), checked
# against the text its templates select for that state, and every other pass reseeds it as KNOWN.
# Octopus Intelligent's dispatching sensor is off outside a dispatch window, when the off-peak badge
# reads "Peak rate" on both dashboards; seeded on, that branch went unrendered for months.
TOGGLES = {
    "binary_sensor.demo_intelligent_dispatching": "off",
}
# A Bubble dashboard is nothing but pop-ups, and Bubble renders one only while it is open, so the
# gate opens each by hash (check_overflow.py). Fewer than this many found is a harness fault (the
# card_type/hash keys moved), not a dashboard with fewer pop-ups: lower it deliberately with one.
MIN_POPUPS = 8
# States for the template sensors the bundled helper packages define (dashboards/Templates), keyed
# by the template's `name`; its entity id is derived from that name below, as HA derives it, so a
# renamed template cannot leave a stale id here. The pretty location is the LOCATION tiles'
# sub-line: a zone name, a town, or the `places` sensor's formatted address. Seeded at that longest
# form, for the landmark the device_tracker default sits on, so the tiles' wrap is exercised; the
# per-domain "42" never could be clipped by any style.
TEMPLATE_DIR = os.path.join(HERE, "..", "alpine_a290", "dashboards", "Templates")
HELPER_STATES = {
    "Alpine Pretty Location": ("Trafalgar Square, Westminster, London", {"icon": "mdi:map-marker"}),
}
DEFAULTS = {
    "binary_sensor": ("off", {}),
    "number": ("50", {"min": 15, "max": 100, "step": 5, "mode": "slider", "unit_of_measurement": "%"}),
    "device_tracker": ("home", {"latitude": 51.5074, "longitude": -0.1278, "gps_accuracy": 8, "source_type": "gps"}),  # synthetic-coords: Trafalgar Square, a public landmark
    "switch": ("off", {}),
    "button": ("unknown", {}),
    # Test-mode helpers (a290_test_* package): default idle so the panels stay hidden and the
    # always-visible "Run Test Charge" button renders instead of a "not found" error card.
    "input_boolean": ("off", {}),
    "input_button": ("unknown", {}),
    "input_datetime": ("2026-06-27 09:00:00", {"has_date": True, "has_time": True}),
}


def _name_from_id(eid):
    obj = eid.split(".", 1)[1]
    return " ".join("A290" if w == "a290" else w.capitalize() for w in obj.split("_"))


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _catalog():
    sys.path.insert(0, os.path.abspath(APP_DIR))
    import catalog
    return catalog


def device_slug():
    return _slug(_catalog().DEVICE["name"])


def problem_sensors():
    """entity_id of every problem-class binary sensor the add-on publishes. Derived from the
    catalog the way HA derives it, slug(device name + entity name), so it follows a rename and
    the r5 twin's catalog without a list here to fall out of step."""
    return frozenset(f"binary_sensor.{device_slug()}_{_slug(name)}"
                     for name, dclass in _catalog().BINARY_SENSORS.values() if dclass == "problem")


def option_default(option):
    with open(os.path.join(APP_DIR, "..", "config.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["options"][option]


@functools.cache
def helper_states():
    """{entity_id: (state, attrs)} for HELPER_STATES, each id slug(name) of a template sensor found
    in dashboards/Templates; a name no template defines is an error, not a silent "42"."""
    found = {}
    for fname in sorted(os.listdir(TEMPLATE_DIR)):
        if not fname.endswith(".yaml"):
            continue
        with open(os.path.join(TEMPLATE_DIR, fname), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        blocks = doc if isinstance(doc, list) else (doc or {}).get("template", [])
        for block in blocks:
            for dom in ("sensor", "binary_sensor"):
                for ent in block.get(dom, []):
                    if ent.get("name") in HELPER_STATES:
                        found[f"{dom}.{_slug(ent['name'])}"] = HELPER_STATES[ent["name"]]
    if len(found) != len(HELPER_STATES):
        raise SystemExit(f"HELPER_STATES names a template sensor no file in {TEMPLATE_DIR} defines: "
                         f"{sorted(HELPER_STATES)} vs {sorted(found)}")
    return found


def state_for(eid, problems=frozenset(), states=None):
    """The seeded state. A pass's `states` {problem sensor: state} win over KNOWN/DEFAULTS, and a
    DERIVED_AGE timestamp takes the age its sensor's state in that pass needs."""
    if eid in KNOWN:
        st, attrs = KNOWN[eid]
    elif eid in (helpers := helper_states()):
        st, attrs = helpers[eid]
    else:
        st, attrs = DEFAULTS.get(eid.split(".")[0], ("42", {}))
    attrs = dict(attrs)
    attrs.setdefault("friendly_name", _name_from_id(eid))
    if states and eid in states:
        st = states[eid]
    if eid in DERIVED_AGE:
        src, _, ages = DERIVED_AGE[eid]
        st = _ago(**ages[state_for(src, problems, states)[0]])
    if eid in problems:
        attrs.setdefault("device_class", "problem")  # as published: "Problem"/"OK", not "On"/"Off"
    return st, attrs


def unreachable(states, ages=None):
    """Why production could not publish these together: `states` {problem sensor: state}, `ages`
    {DERIVED_AGE timestamp: hours old}. Empty when it could."""
    why = [f"{a} {sa} forces {b} {sb}, seeded {states[b]}"
           for (a, sa), then in IMPLIES.items() if states.get(a) == sa
           for b, sb in then.items() if states.get(b, sb) != sb]
    for ts, hours in (ages or {}).items():
        src, option, _ = DERIVED_AGE[ts]
        limit = option_default(option)
        if src in states and (hours > limit) != (states[src] == "on"):
            why.append(f"{ts} {hours:.1f}h old with {src} {states[src]} ({option} default {limit})")
    return why


def close(states):
    """`states` with every IMPLIES consequence applied, until none changes anything."""
    out = dict(states)
    for _ in range(len(out) + 1):
        forced = {b: sb for (a, sa), then in IMPLIES.items() if out.get(a) == sa
                  for b, sb in then.items() if b in out and out[b] != sb}
        if not forced:
            return out
        out.update(forced)
    raise SystemExit(f"IMPLIES never settles from {states}: two relations force opposite states")


def _invert(eid, state):
    if state not in ("on", "off"):
        raise SystemExit(f"{eid} is seeded {state!r}; a problem sensor's passes need 'on' or 'off'")
    return "off" if state == "on" else "on"


def derive_passes(referenced, toggles=TOGGLES):
    """[(name, {sensor: state})] over the problem sensors the dashboards reference and `toggles`:
    the normal pass (name "", the KNOWN/DEFAULTS states), "alarm" (every problem sensor inverted,
    then closed over IMPLIES), a "<sensor>_<state>" pass for each problem branch that closure kept
    off the page, and one for each toggle's other state. So every branch of every referenced sensor
    renders in some pass, and every pass is a state production can publish; a KNOWN seed or a
    branch that cannot be is an error, not a skipped render. Every pass carries every toggle, at
    KNOWN unless it is the toggle's own, so each reseed puts the previous pass's state back."""
    normal = {eid: state_for(eid)[0] for eid in sorted(referenced)}
    fixed = {eid: state_for(eid)[0] for eid in sorted(toggles)}
    passes = [("", {**fixed, **normal}),
              ("alarm", {**fixed, **close({eid: _invert(eid, st) for eid, st in normal.items()})})]
    prefix = device_slug() + "_"

    def pass_name(eid, state):
        return f"{eid.split('.', 1)[1].removeprefix(prefix)}_{state}"

    for eid, st in normal.items():
        want = _invert(eid, st)
        if any(p[eid] == want for _, p in passes):
            continue
        p = close({**normal, eid: want})
        if p[eid] != want:
            raise SystemExit(f"no pass production can publish renders {eid} {want}: IMPLIES forces it back")
        passes.append((pass_name(eid, want), {**fixed, **p}))
    for eid, want in sorted(toggles.items()):
        if fixed[eid] == want:
            raise SystemExit(f"TOGGLES gives {eid} its KNOWN state {want!r}; it must name the other one")
        passes.append((pass_name(eid, want), {**fixed, **normal, eid: want}))
    for name, p in passes:
        if why := unreachable(p):
            raise SystemExit(f"{name or 'normal'} pass seeds what production cannot publish: {why}")
    return passes


# Card fields rendered as visible text, and the one template form whose branch text can be read
# statically: literal text around {% if is_state('<id>','<state>') %}A{% else %}B{% endif %}.
# icon/icon_color/card_mod templates key on the same sensors but render no text, so they are not
# labels.
TEXT_KEYS = {"primary", "secondary", "name", "label", "title", "heading", "content"}
IF_IS_STATE = re.compile(
    r"^([^{}]*)\{%-?\s*if\s+is_state\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)\s*-?%\}([^{}]*)"
    r"\{%-?\s*else\s*-?%\}([^{}]*)\{%-?\s*endif\s*-?%\}([^{}]*)$")
# Actions run on a tap: whatever they name is not on the page, so never an expected label.
ACTION_KEYS = {"tap_action", "hold_action", "double_tap_action"}


def _reads_state(template, eid):
    """Whether a Jinja template's text can follow `eid`'s STATE. One that only reads its
    attributes (state_attr) renders the same in every pass, so no pass can assert it."""
    quoted = re.escape(eid)
    return re.search(rf"is_state\(\s*'{quoted}'|states\(\s*'{quoted}'|states\.{quoted}\b", template)


def expected_labels(views, states):
    """(label, sensors, pop-up) for the text a pass's `states` must put on the page: the `name`
    of every conditional card whose conditions they all meet, and the selected branch of every
    IF_IS_STATE text template that reads one. `pop-up` is the hash of the Bubble pop-up the card
    sits in, or None on the page itself. A text template that reads one of these sensors' state
    in any other form cannot be asserted, so it is an error rather than a silent gap."""
    found = []

    def walk(node, key=None, popup=None):
        if isinstance(node, dict):
            if node.get("card_type") == "pop-up" and node.get("hash"):
                popup = node["hash"]
            conds = node.get("conditions") if node.get("type") == "conditional" else None
            if conds and all(isinstance(c, dict) and c.get("entity") in states
                             and str(c.get("state")) == states[c["entity"]] for c in conds):
                name = (node.get("card") or {}).get("name")
                if name:
                    found.append((name, {c["entity"] for c in conds}, popup))
            for k, v in node.items():
                if k not in ACTION_KEYS:
                    walk(v, k, popup)
        elif isinstance(node, list):
            for v in node:
                walk(v, key, popup)
        elif isinstance(node, str) and key in TEXT_KEYS and "{%" in node:
            keyed = [eid for eid in states if _reads_state(node, eid)]
            if not keyed:
                return
            m = IF_IS_STATE.match(node)
            if not m or m.group(2) not in states:
                raise SystemExit(f"cannot assert the {key!r} template on {keyed}; "
                                 f"extend seed.expected_labels for it: {node[:160]!r}")
            before, eid, st, if_text, else_text, after = m.groups()
            text = before + (if_text if states[eid] == st else else_text) + after
            found.append((text.strip(), {eid}, popup))

    walk(views)
    return [(label, eids, popup) for label, eids, popup in found if label]


def popups_in(views):
    """[(hash, name, node)] of every Bubble pop-up a dashboard defines, in file order. Stops on a
    pop-up without a hash or a name (the gate opens it by the one and proves it open by the
    other), on two sharing either, on a navigate action that targets no pop-up, and on a Bubble
    dashboard defining fewer than MIN_POPUPS: each is a way the pop-up loop could go quiet."""
    popups, targets, bubble = [], [], False

    def walk(node):
        nonlocal bubble
        if isinstance(node, dict):
            if node.get("type") == "custom:bubble-card":
                bubble = True
            if node.get("card_type") == "pop-up":
                if not node.get("hash") or not node.get("name"):
                    raise SystemExit("a pop-up without a hash or a name cannot be opened and proved open: "
                                     f"{ {k: node.get(k) for k in ('hash', 'name')} }")
                popups.append((node["hash"], node["name"], node))
            if node.get("action") == "navigate" and str(node.get("navigation_path", "")).startswith("#"):
                targets.append(node["navigation_path"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(views)
    hashes = [h for h, _, _ in popups]
    names = [n for _, n, _ in popups]
    if len(set(hashes)) != len(hashes) or len(set(names)) != len(names):
        raise SystemExit(f"pop-ups share a hash or a name: {sorted((h, n) for h, n, _ in popups)}")
    if dead := sorted(set(targets) - set(hashes)):
        raise SystemExit(f"navigate actions target pop-ups the dashboard does not define: {dead}")
    if bubble and len(popups) < MIN_POPUPS:
        raise SystemExit(f"a Bubble dashboard defines {len(popups)} pop-ups, fewer than MIN_POPUPS "
                         f"{MIN_POPUPS}: the harness is not finding them")
    return popups


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


async def seed_states(session, base, token, entities, problems=frozenset(), states=None):
    """POST each state; returns {entity_id: the state sent}, for callers that read it back."""
    headers = {"Authorization": f"Bearer {token}"}
    posted = {}
    for eid in entities:
        st, attrs = state_for(eid, problems, states)
        posted[eid] = st
        async with session.post(f"{base}/api/states/{eid}", headers=headers,
                                json={"state": st, "attributes": attrs}) as r:
            if r.status not in (200, 201):
                print(f"  ! {eid}: HTTP {r.status}", file=sys.stderr)
    print(f"  seeded {len(entities)} entity states")
    return posted


async def verify_pass(session, args, where, posted, states):
    """Read back every state the pass depends on and stop unless HA holds what was seeded and
    production could publish it. A failed POST only logs, and a named pass follows another on the
    same instance, so this is what shows the page renders this pass and nothing left over."""
    headers = {"Authorization": f"Bearer {args.token}"}
    held, ages = {}, {}
    for eid in sorted(set(states) | {e for e in posted if e in DERIVED_AGE}):
        async with session.get(f"{args.base}/api/states/{eid}", headers=headers) as r:
            got = (await r.json()).get("state") if r.status == 200 else f"HTTP {r.status}"
        want = posted[eid] if eid in posted else states[eid]
        if got != want:
            raise SystemExit(f"{where} pass: {eid} is {got!r} in HA, this pass needs {want!r}")
        if eid in DERIVED_AGE:
            ages[eid] = (datetime.now(timezone.utc) - datetime.fromisoformat(got)).total_seconds() / 3600
            print(f"  {eid} -> {ages[eid]:.1f}h old")
        else:
            held[eid] = got
            print(f"  {eid} -> {got}")
    if why := unreachable(held, ages):
        raise SystemExit(f"{where} pass: HA holds states production cannot publish together: {why}")


def write_manifest(path, built, name, states, normal):
    """{dashboard: {"labels": [...], "popups": [{"hash", "name", "labels"}]}} for one pass: the
    labels that must be visible on the page, and the Bubble pop-ups to open with the labels that
    must be visible inside each. A named pass re-checks only the dashboards, and opens only the
    pop-ups, that reference a state it changed from the normal pass; the normal pass checks every
    dashboard and opens every pop-up."""
    changed = {eid for eid, st in states.items() if st != normal[eid]}
    changed |= {ts for ts, (src, _, _) in DERIVED_AGE.items() if src in changed}
    where = name or "normal"
    manifest = {}
    for url_path, views in built.items():
        refs = set(extract_entities([yaml.safe_dump(views)]))
        if name and not refs & changed:
            continue
        popups = [(h, n, node) for h, n, node in popups_in(views)
                  if not name or set(extract_entities([yaml.safe_dump(node)])) & changed]
        opened = {h for h, _, _ in popups}
        # A label inside a pop-up this pass does not open is not on the page; the pass that opens
        # it (the pop-up references a changed state, so some pass does) asserts it.
        pairs = [(label, eids, popup) for label, eids, popup in expected_labels(views, states)
                 if popup is None or popup in opened]
        # The labels are read from the same file a regression would edit: hard-code a switching
        # tile and its expected branch vanishes with it. What survives is the sensor still being
        # referenced (its icon/colour templates) with no text left switching on it; fail on that.
        # Every referenced sensor changes in some pass (derive_passes), so each is checked once.
        silent = (refs & changed & states.keys()) - {eid for _, eids, _ in pairs for eid in eids}
        if silent:
            raise SystemExit(f"{where} pass: {url_path} references {sorted(silent)} but no text on it "
                             "switches on them in a form the gate can assert (a conditional card's "
                             "name, or an IF_IS_STATE text template), so this pass cannot fail "
                             "for them")

        labels_in = {}
        for label, _, popup in pairs:
            labels_in.setdefault(popup, {})[label] = None   # a dict: unique, in order
        manifest[url_path] = {"labels": list(labels_in.get(None, {})),
                              "popups": [{"hash": h, "name": n, "labels": list(labels_in.get(h, {}))}
                                         for h, n, _ in popups]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"  {where} pass checks {manifest}")


def load_views(dash_dir, fname):
    with open(os.path.join(dash_dir, fname), encoding="utf-8") as fh:
        views = yaml.safe_load(fh.read())
    if not isinstance(views, list):
        raise ValueError(f"{fname} did not parse to a list of views")
    return views


def inject_smart_charging(url_path, views):
    """Apply the add-on's own deploy-time Smart Charging injection to a parsed dashboard, so
    the harness renders the optional charger controls exactly as a configured user gets them:
    an entities card on the standard dashboard, a pop-up 'tab' on the bubble dashboard."""
    if not views or not isinstance(views[0], dict):
        return
    sys.path.insert(0, os.path.abspath(APP_DIR))
    os.environ.update(CHARGER_DEMO)
    import deploy
    if "bubble" in url_path:
        deploy._inject_bubble_charging(views[0])
    elif (cards := deploy._charger_cards()):
        deploy._add_cards(views[0], cards)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--token")
    ap.add_argument("--dashboards", default=DASH_DIR_DEFAULT)
    ap.add_argument("--list-passes", action="store_true", help="print the named passes, one per line")
    ap.add_argument("--pass", dest="pass_name", default="", metavar="NAME",
                    help="reseed an already-seeded instance for this named pass")
    ap.add_argument("--manifest", help="write the pass's {dashboard: [expected labels]} here")
    args = ap.parse_args()

    # Build each dashboard's views with the Smart Charging injection applied, then extract the
    # entities to seed from the *injected* YAML (so the demo charger entities get states too).
    built = {}
    for url_path, fname in DASHBOARDS.items():
        views = load_views(args.dashboards, fname)
        inject_smart_charging(url_path, views)
        built[url_path] = views
    entities = extract_entities([yaml.safe_dump(v) for v in built.values()])
    problems = problem_sensors()
    # A rename in the catalog would otherwise make a relation match nothing and pass silently.
    declared = ({a for a, _ in IMPLIES} | {b for then in IMPLIES.values() for b in then}
                | {src for src, _, _ in DERIVED_AGE.values()})
    if declared - problems:
        raise SystemExit(f"IMPLIES/DERIVED_AGE name {sorted(declared - problems)}, which the catalog "
                         f"does not publish as problem sensors {sorted(problems)}")
    referenced = [eid for eid in entities if eid in problems]
    if not referenced:
        # No named pass would render anything and each would pass: the catalog-to-id derivation or
        # the dashboards changed, and the gate must say so rather than go quietly blind.
        raise SystemExit(f"no dashboard references any problem sensor {sorted(problems)}")
    # A toggle's pass renders its other branch; one nothing references, or that is a problem
    # sensor (whose passes derive_passes already makes), would pass having shown nothing new.
    if bad := sorted(eid for eid in TOGGLES if eid not in entities or eid in problems):
        raise SystemExit(f"TOGGLES name {bad}: not referenced by any dashboard, or a problem sensor")
    passes = derive_passes(referenced)
    if args.list_passes:
        print("\n".join(name for name, _ in passes if name))
        return
    if not args.token:
        ap.error("--token is required to seed")
    by_name = dict(passes)
    if args.pass_name not in by_name:
        raise SystemExit(f"unknown pass {args.pass_name!r}; derived: {[n for n, _ in passes if n]}")
    states, where = by_name[args.pass_name], args.pass_name or "normal"

    async with aiohttp.ClientSession() as session:
        if args.pass_name:
            print(f"Seeding the {where} pass…")
            # Every problem sensor and derived timestamp, not only those this pass changes: the
            # previous pass's states are still in HA.
            reseed = sorted(states) + sorted(ts for ts in DERIVED_AGE if ts in entities)
            posted = await seed_states(session, args.base, args.token, reseed, problems, states)
            await verify_pass(session, args, where, posted, states)
            if args.manifest:
                write_manifest(args.manifest, built, args.pass_name, states, by_name[""])
            return
        print("Seeding entity states…")
        posted = await seed_states(session, args.base, args.token, entities, problems, states)
        await verify_pass(session, args, where, posted, states)
        if args.manifest:
            write_manifest(args.manifest, built, "", states, states)

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
            for url_path in DASHBOARDS:
                if url_path not in existing_dash:
                    await api.cmd(type="lovelace/dashboards/create", url_path=url_path,
                                  title=url_path, icon="mdi:car-sports", mode="storage",
                                  show_in_sidebar=True, require_admin=False)
                views = built[url_path]
                await api.cmd(type="lovelace/config/save", url_path=url_path,
                              config={"title": url_path, "views": views})
                print(f"  deployed dashboard '{url_path}' ({len(views)} views)")
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
