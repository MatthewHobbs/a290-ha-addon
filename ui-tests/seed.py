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
that must be visible inside them and, per card the pop-up renders, the texts it must show before
the gate scans it (popup_items, ADR 0003).

Usage: seed.py --base http://localhost:8123 --token <access_token> [--dashboards <dir>]
                [--pass <name>] [--manifest <manifest.json>]
       seed.py --list-passes
"""
import argparse
import asyncio
import functools
import html
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


def condition_entities(conditions):
    """Every entity a conditional card's `conditions` name, at any depth."""
    out = set()
    for c in conditions if isinstance(conditions, list) else [conditions]:
        if isinstance(c, dict):
            if c.get("entity"):
                out.add(c["entity"])
            out |= condition_entities(c.get("conditions", []))
    return out


def condition_holds(conditions, effective, where=""):
    """Whether Home Assistant would show a conditional card's content given `effective`, the
    complete seeded state map {entity: (state, attrs)}: a list is `and`; `condition: or`, `and`
    and `not` nest; `condition: state` (or the flat entity/state form) compares the entity's state
    with `state` (one or a list) or `state_not`. An entity the seed does not set is an error, not
    an inactive card: the pass's partial map once called both Charge Status cards inactive."""
    if isinstance(conditions, dict):
        conditions = [conditions]
    for c in conditions or []:
        if not isinstance(c, dict):
            raise SystemExit(f"{where}: a condition is not a mapping: {c!r}")
        kind = c.get("condition", "state")
        if kind in ("or", "and", "not"):
            inner = c.get("conditions", [])
            results = [condition_holds(i, effective, where) for i in inner]
            if kind == "or" and inner and not any(results):
                return False
            if kind == "and" and not all(results):
                return False
            if kind == "not" and any(results):
                return False
            continue
        if kind != "state":
            raise SystemExit(f"{where}: cannot evaluate a {kind!r} condition; extend seed.condition_holds")
        eid = c.get("entity")
        if eid not in effective:
            raise SystemExit(f"{where}: condition names {eid!r}, which the seed does not set")
        have = effective[eid][0]
        if "state" in c:
            want = c["state"]
            want = [str(w) for w in want] if isinstance(want, list) else [str(want)]
            if have not in want:
                return False
        elif "state_not" in c:
            want = c["state_not"]
            want = [str(w) for w in want] if isinstance(want, list) else [str(want)]
            if have in want:
                return False
        else:
            raise SystemExit(f"{where}: a state condition on {eid!r} names neither state nor state_not")
    return True


def expected_labels(views, states, effective):
    """(label, sensors, pop-up) for the text a pass's `states` must put on the page: the `name`
    of every conditional card on those sensors whose conditions hold (condition_holds, over the
    complete seeded map `effective`), and the selected branch of every IF_IS_STATE text template
    that reads one. `pop-up` is the hash of the Bubble pop-up the card sits in, or None on the
    page itself. A text template that reads one of these sensors' state in any other form cannot
    be asserted, so it is an error rather than a silent gap."""
    found = []

    def walk(node, key=None, popup=None):
        if isinstance(node, dict):
            if node.get("card_type") == "pop-up" and node.get("hash"):
                popup = node["hash"]
            conds = node.get("conditions") if node.get("type") == "conditional" else None
            if conds:
                named = condition_entities(conds)
                if named and named <= states.keys() and condition_holds(conds, effective, popup or "the page"):
                    name = (node.get("card") or {}).get("name")
                    if name:
                        found.append((name, named, popup))
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


# --- The completeness set: what every pop-up's cards must show before the gate scans it (ADR 0003).

# A Jinja text field that is exactly the raw state of one entity, which the collector can read.
STATES_OF = re.compile(r"^\{\{\s*states\(\s*'([^']+)'\s*\)\s*\}\}$")
# Where a card holds other cards, each an item of its own; the wrapper's own texts stop here.
CONTAINER_KEYS = ("cards", "card")
# What Home Assistant's frontend shows for an on/off state in `en`, by domain and device class
# (binary_sensor device classes have their own words). A state the table cannot name is an
# error, not a guess.
ON_OFF_DOMAINS = {"binary_sensor", "switch", "input_boolean", "light", "fan"}
BINARY_CLASS_TEXT = {None: {"on": "On", "off": "Off"}, "problem": {"on": "Problem", "off": "OK"}}
TRACKER_TEXT = {"home": "Home", "not_home": "Away"}


def _decimals(state, attrs):
    """Fraction digits HA's formatNumber keeps: none when the step and the state are integers,
    else exactly those the seeded string carries (getNumberFormatOptions, getDefaultFormatOptions)."""
    step = attrs.get("step")
    if step is not None and float(step).is_integer() and float(state).is_integer():
        return 0
    return len(state.split(".", 1)[1]) if "." in state else 0


def _format_date_time(iso):
    """HA's formatDateTime for `en` (September 27, 2026 at 5:12 AM), in the browser's zone: the
    frontend's time_zone preference defaults to `local`, and the browser runs on this host, so
    Python's local zone is the same one. Chromium may put a narrow no-break space before AM; the
    gate compares whitespace loosely, so a plain space is written here."""
    dt = datetime.fromisoformat(iso).astimezone()
    return f"{dt:%B} {dt.day}, {dt.year} at {dt.hour % 12 or 12}:{dt:%M} {'PM' if dt.hour >= 12 else 'AM'}"


_SHORT_DATE_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _short_date(iso):
    """The short date a button's `styles` override writes into `.bubble-state` (ADR 0004):
    Intl.DateTimeFormat('en-GB', {timeZone: hass.config.time_zone, day:'numeric', month:'numeric',
    hour:'2-digit', minute:'2-digit', hourCycle:'h23'}), reassembled as 'D Mon HH:MM'. Uses UTC,
    matching `_offpeak_window`: hass.config.time_zone is the harness's untouched default."""
    dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return f"{dt.day} {_SHORT_DATE_MONTHS[dt.month - 1]} {dt.hour:02d}:{dt.minute:02d}"


def state_text(eid, state, attrs):
    """The text Home Assistant's formatEntityState renders for a seeded state, in the gate's `en`
    locale (frontend computeStateDisplay): a number with a unit (no blank before `%` or `°`, one
    before any other unit, grouped thousands), a timestamp sensor as an absolute date-time, an
    on/off word for the on/off domains, and the raw state otherwise. Bubble's state line and
    button-card's show_state both go through it."""
    if state in ("unknown", "unavailable"):
        return state.capitalize()
    domain = eid.split(".", 1)[0]
    unit = attrs.get("unit_of_measurement")
    numeric = bool(unit or attrs.get("state_class")) or domain in ("counter", "input_number", "number")
    if numeric:
        try:
            value = float(state)
        except ValueError:
            return f"{state} {unit}" if unit else state
        text = f"{value:,.{_decimals(state, attrs)}f}"
        return text + (("" if unit in ("%", "°") else " ") + unit if unit else "")
    if domain == "sensor" and attrs.get("device_class") == "timestamp":
        return _format_date_time(state)
    if domain in ON_OFF_DOMAINS:
        table = BINARY_CLASS_TEXT.get(attrs.get("device_class") if domain == "binary_sensor" else None)
        if table is None or state not in table:
            raise SystemExit(f"{eid} {state!r} (device_class {attrs.get('device_class')!r}): extend "
                             "seed.BINARY_CLASS_TEXT with the word Home Assistant shows for it")
        return table[state]
    if domain == "device_tracker":
        return TRACKER_TEXT.get(state, state)
    return state


def effective_states(entities, problems, states, posted=None):
    """{entity: (state, attrs)} as seed_states posts them for this pass: KNOWN and the derived
    values through state_for, with the pass's problem sensors and toggles applied, and the
    states actually posted (`posted`) where the caller has them."""
    out = {}
    for eid in entities:
        st, attrs = state_for(eid, problems, states)
        if posted and eid in posted:
            st = posted[eid]
        out[eid] = (st, attrs)
    return out


def card_tag(card_type):
    """The element a card type renders as: `custom:x` is `<x>`, a core `y` is `<hui-y-card>`."""
    if not isinstance(card_type, str) or not card_type:
        raise SystemExit(f"a card has no type: {card_type!r}")
    return card_type[len("custom:"):] if card_type.startswith("custom:") else f"hui-{card_type.replace('_', '-')}-card"


def _pct(eff, eid):
    """A SoC the Charge Status badges render: the raw state with any % stripped, then `%`."""
    return str(eff[eid][0]).replace("%", "") + "%"


def _charge_status_badges(eff):
    """The button-card's three JavaScript custom_fields: the label each shows and the SoC as the
    card renders it (`${v}%` of the raw state). `Charging` replaces `Current SOC` while the
    charging sensor is on."""
    charging = eff["binary_sensor.alpine_a290_charging"][0] == "on"
    return {"custom_fields.min_badge": ["Min SOC", _pct(eff, "number.alpine_a290_minimum_soc")],
            "custom_fields.max_badge": ["Target SOC", _pct(eff, "number.alpine_a290_charge_target_soc")],
            "custom_fields.current_badge": ["Charging" if charging else "Current SOC",
                                            _pct(eff, "sensor.alpine_a290_battery_level")]}


def _offpeak_window(eff):
    """The off-peak badge's `secondary`: `Off-peak HH:MM–HH:MM` from the dispatching sensor's
    window attributes (current_* over next_*), as timestamp_custom('%H:%M', true) renders them in
    Home Assistant's own time zone, which the harness leaves at its UTC default."""
    _, attrs = eff[CHARGER_DEMO["A290_CHARGER_DISPATCHING"]]
    start = attrs.get("current_start") or attrs.get("next_start")
    end = attrs.get("current_end") or attrs.get("next_end")
    if not (start and end):
        return {"secondary": ["Schedule unavailable"]}

    def hhmm(iso):
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%H:%M")

    return {"secondary": [f"Off-peak {hhmm(start)}–{hhmm(end)}"]}


# Visible-text fields the collector cannot read (JavaScript `[[[ ]]]` or Jinja beyond IF_IS_STATE
# and STATES_OF), declared by hand: {(dashboard, pop-up hash, card type, card entity): fn}, fn
# taking the effective seeded map and returning {field path: [texts the field renders]}, the path
# dotted from the card (`secondary`, `custom_fields.min_badge`). A card with such a field and no
# declaration, a declaration no card matches, and two cards matching one are each a manifest-time
# error, so nothing is scanned on a guess and nothing goes stale quietly.
DECLARED = {
    ("alpine-bubble", "#alpine-charge", "custom:button-card", "sensor.alpine_a290_battery_level"): _charge_status_badges,
    ("alpine-bubble", "#alpine-charging", "custom:mushroom-template-card",
     CHARGER_DEMO["A290_CHARGER_DISPATCHING"]): _offpeak_window,
}


def _make_short_date(eid):
    """Bound to one entity, for a STATE_TEXT_OVERRIDE entry."""
    return lambda eff: _short_date(eff[eid][0])


# Bubble state buttons whose `styles` JS overwrites `.bubble-state`'s text after render (ADR
# 0004's short-date templates): keyed by (dashboard, pop-up hash, entity, card name), since Last
# Charge's Started and Date buttons share an entity and only their name tells them apart. `texts_of`
# detects the override itself (the styles string targets `.bubble-state` and sets `.textContent`)
# and requires a matching entry rather than falling back to state_text; a card overriding its state
# with no entry, and an entry matching no card, are each a manifest-time error, same guarantee as
# DECLARED.
STATE_TEXT_OVERRIDE = {
    ("alpine-bubble", "#alpine-activity", "sensor.alpine_a290_hvac_last_activity", "HVAC"):
        _make_short_date("sensor.alpine_a290_hvac_last_activity"),
    ("alpine-bubble", "#alpine-activity", "sensor.alpine_a290_last_updated", "Battery"):
        _make_short_date("sensor.alpine_a290_last_updated"),
    ("alpine-bubble", "#alpine-activity", "sensor.alpine_a290_gps_last_activity", "GPS"):
        _make_short_date("sensor.alpine_a290_gps_last_activity"),
    ("alpine-bubble", "#alpine-lastcharge", "sensor.alpine_a290_last_charge_start", "Started"):
        _make_short_date("sensor.alpine_a290_last_charge_start"),
    ("alpine-bubble", "#alpine-lastcharge", "sensor.alpine_a290_last_charge_end", "Ended"):
        _make_short_date("sensor.alpine_a290_last_charge_end"),
    ("alpine-bubble", "#alpine-lastcharge", "sensor.alpine_a290_last_charge_start", "Date"):
        _make_short_date("sensor.alpine_a290_last_charge_start"),
}


def _strip_markup(text):
    """The visible text of an HTML string custom field: tags out, entities decoded, one space."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", text)).split())


def _shows_state(card):
    """Whether the card shows its entity's state without a text field naming it: Bubble's state
    button (unless show_state is off), anything with show_state, and mushroom's entity card,
    whose secondary_info defaults to the state."""
    ctype = card.get("type")
    if ctype == "custom:bubble-card":
        return bool(card.get("show_state", card.get("button_type") == "state"))
    if ctype == "custom:mushroom-entity-card":
        return card.get("secondary_info", "state") == "state"
    return bool(card.get("show_state"))


def popup_items(url_path, popup, effective, declared=DECLARED):
    """[(card tag, [texts])] for one pop-up, one item per card its `cards:` render in dashboard
    order, never the pop-up's own name (its header). Each item's texts are every static string
    under a TEXT_KEYS key, the branch an IF_IS_STATE template selects, the raw state a STATES_OF
    template reads, the visible text of every plain-string button-card custom field, the text
    the entity's seeded state renders as where the card shows state (state_text), and whatever a
    DECLARED entry gives for the fields nothing here can read. A conditional card contributes its
    inner card only while condition_holds, and never itself: an inactive hui-conditional-card has
    no box. Stacks are items too (they render), each of their cards another. ACTION_KEYS subtrees
    are skipped. Returns (items, declarations used)."""
    items, used = [], set()
    where = f"{url_path} pop-up {popup.get('hash')}"

    def texts_of(card, path):
        out = []
        ctype = card.get("type")
        key = (url_path, popup.get("hash"), ctype, card.get("entity"))
        decl = declared.get(key)
        given = {}
        if decl:
            if key in used:
                raise SystemExit(f"{where}: two cards match the declaration {key}; key them apart")
            used.add(key)
            given = decl(effective)
            if not isinstance(given, dict):
                raise SystemExit(f"{where}: the declaration {key} must return {{field: [texts]}}")
        unread, consumed = [], set()

        def walk(node, k=None, at=()):
            if isinstance(node, dict):
                for kk, v in node.items():
                    if kk in ACTION_KEYS or kk in CONTAINER_KEYS or kk == "custom_fields":
                        continue
                    walk(v, kk, at + (kk,))
            elif isinstance(node, list):
                for v in node:
                    walk(v, k, at)
            elif isinstance(node, str) and k in TEXT_KEYS:
                field = ".".join(at)
                if field in given:
                    consumed.add(field)
                    out.extend(given[field])
                elif "[[[" in node:
                    unread.append(field)
                elif "{{" in node or "{%" in node:
                    if m := IF_IS_STATE.match(node):
                        before, eid, st, if_text, else_text, after = m.groups()
                        if eid not in effective:
                            raise SystemExit(f"{where}: {field!r} reads {eid!r}, which the seed does not set")
                        text = before + (if_text if effective[eid][0] == st else else_text) + after
                        if text.strip():
                            out.append(text.strip())
                    elif m := STATES_OF.match(node):
                        if m.group(1) not in effective:
                            raise SystemExit(f"{where}: {field!r} reads {m.group(1)!r}, which the seed does not set")
                        out.append(str(effective[m.group(1)][0]))
                    else:
                        unread.append(field)
                elif node.strip():
                    out.append(node.strip())

        walk(card)
        for name, value in (card.get("custom_fields") or {}).items():
            field = f"custom_fields.{name}"
            if field in given:
                consumed.add(field)
                out.extend(given[field])
            elif not isinstance(value, str):
                unread.append(field)
            elif "[[[" in value or "{{" in value or "{%" in value:
                unread.append(field)
            elif text := _strip_markup(value):
                out.append(text)
        if "state_content" in card:
            unread.append("state_content")
        if unread:
            raise SystemExit(f"{where}: card {path} ({ctype}) has visible-text fields the collector cannot "
                             f"read and no DECLARED entry covers: {unread}; declare what they render")
        if stale := sorted(set(given) - consumed):
            raise SystemExit(f"{where}: the declaration {key} names fields card {path} does not have: {stale}")
        if _shows_state(card):
            eid = card.get("entity")
            if eid not in effective:
                raise SystemExit(f"{where}: card {path} ({ctype}) shows the state of {eid!r}, which the seed "
                                 "does not set")
            styles = card.get("styles")
            if isinstance(styles, str) and ".bubble-state')" in styles and ".textContent=" in styles:
                okey = (url_path, popup.get("hash"), eid, card.get("name"))
                fn = STATE_TEXT_OVERRIDE.get(okey)
                if fn is None:
                    raise SystemExit(f"{where}: card {path} ({ctype}) overrides its state display in "
                                     f"styles and no STATE_TEXT_OVERRIDE entry covers {okey}; declare "
                                     "what it renders")
                out.append(fn(effective))
                used.add(("state", *okey))
            else:
                out.append(state_text(eid, *effective[eid]))
        return out

    def walk_card(card, path):
        if not isinstance(card, dict):
            raise SystemExit(f"{where}: {path} is not a card mapping: {card!r}")
        ctype = card.get("type")
        if ctype == "conditional":
            if condition_holds(card.get("conditions", []), effective, f"{where} card {path}"):
                walk_card(card.get("card"), f"{path}.card")
            return
        items.append((card_tag(ctype), texts_of(card, path)))
        for i, sub in enumerate(card.get("cards") or []):
            walk_card(sub, f"{path}.cards[{i}]")
        if isinstance(card.get("card"), dict):
            walk_card(card["card"], f"{path}.card")

    for i, card in enumerate(popup.get("cards") or []):
        walk_card(card, f"cards[{i}]")
    if not items:
        raise SystemExit(f"{where}: no cards found; the walk missed the pop-up")
    return items, used


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


def write_manifest(path, built, name, states, normal, posted=None):
    """{dashboard: {"labels": [...], "popups": [{"hash", "name", "labels", "cards"}]}} for one
    pass: the labels that must be visible on the page, and the Bubble pop-ups to open, each with
    the labels that must be visible inside it and the per-card items (popup_items) the gate
    waits for before scanning it. A named pass re-checks only the dashboards, and opens only the
    pop-ups, that reference a state it changed from the normal pass; the normal pass checks every
    dashboard and opens every pop-up. Every pop-up's items are collected whether or not this
    pass opens it, so a DECLARED entry that matches no card fails every pass."""
    changed = {eid for eid, st in states.items() if st != normal[eid]}
    changed |= {ts for ts, (src, _, _) in DERIVED_AGE.items() if src in changed}
    where = name or "normal"
    entities = extract_entities([yaml.safe_dump(v) for v in built.values()])
    effective = effective_states(entities, problem_sensors(), states, posted)
    if unknown := sorted({k for k in DECLARED if k[0] not in built}):
        raise SystemExit(f"DECLARED names dashboards that are not built: {unknown}")
    if unknown := sorted({k for k in STATE_TEXT_OVERRIDE if k[0] not in built}):
        raise SystemExit(f"STATE_TEXT_OVERRIDE names dashboards that are not built: {unknown}")
    manifest = {}
    for url_path, views in built.items():
        refs = set(extract_entities([yaml.safe_dump(views)]))
        every = popups_in(views)
        items, used = {}, set()
        for h, _, node in every:
            items[h], seen = popup_items(url_path, node, effective, DECLARED)
            used |= seen
        if stale := sorted(k for k in DECLARED if k[0] == url_path and k not in used):
            raise SystemExit(f"{where} pass: DECLARED entries match no card in {url_path}: {stale}")
        if stale := sorted(k for k in STATE_TEXT_OVERRIDE if k[0] == url_path and ("state", *k) not in used):
            raise SystemExit(f"{where} pass: STATE_TEXT_OVERRIDE entries match no card in {url_path}: {stale}")
        if name and not refs & changed:
            continue
        popups = [(h, n, node) for h, n, node in every
                  if not name or set(extract_entities([yaml.safe_dump(node)])) & changed]
        opened = {h for h, _, _ in popups}
        # A label inside a pop-up this pass does not open is not on the page; the pass that opens
        # it (the pop-up references a changed state, so some pass does) asserts it.
        pairs = [(label, eids, popup) for label, eids, popup in expected_labels(views, states, effective)
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
                              "popups": [{"hash": h, "name": n, "labels": list(labels_in.get(h, {})),
                                          "cards": [[tag, texts] for tag, texts in items[h]]}
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
            # Every entity, not only those this pass changes: each pass runs as its own process,
            # so a plain KNOWN `_ago()` age (an entity no pass ever names, e.g. the Activity
            # buttons' last-activity sensors) is recomputed fresh here relative to THIS process's
            # own clock. Posting only the diff left such an entity's absolute value exactly as an
            # earlier pass's process happened to compute it, drifting a few minutes behind what
            # this pass's own manifest predicts for it whenever an unrelated sibling reopens its
            # pop-up — a mismatch this scan cannot tell apart from the button never having painted.
            posted = await seed_states(session, args.base, args.token, entities, problems, states)
            await verify_pass(session, args, where, posted, states)
            if args.manifest:
                write_manifest(args.manifest, built, args.pass_name, states, by_name[""], posted)
            return
        print("Seeding entity states…")
        posted = await seed_states(session, args.base, args.token, entities, problems, states)
        await verify_pass(session, args, where, posted, states)
        if args.manifest:
            write_manifest(args.manifest, built, "", states, states, posted)

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
