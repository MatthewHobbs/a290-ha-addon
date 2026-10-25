"""Conformance: the bundled dashboards and the UI-gate seed must reference entities this build
actually publishes.

A Lovelace card reading a non-existent entity renders a fallback, not an error, so a dashboard
can drift away from the catalog and every gate still passes: pytest never opens the ``.txt``
dashboards, and the Playwright gate seeds whatever ids the dashboards name and fails only on
truncation or ``hui-error-card``. The R5 twin shipped empty Min/Max SoC badges for weeks that
way (r5-ha-addon #50), and this is the port of the test that now guards it there (r5 #90).

ENTITY IDS COME FROM NAMES, NOT OBJECT_IDS. Home Assistant ignores the discovery ``object_id``
and derives ``entity_id = slug(device name + " " + entity name)``. On this model the two
diverge in the prefix as well as the tail: the object_id ``a290_external_temperature`` is
``sensor.alpine_a290_outside_temperature``. So references are matched on both prefixes, and an
object_id-shaped one (``sensor.a290_battery_level``) fails rather than being skipped. The ids
are taken from the discovery configs the shared core's real ``publish_discovery`` emits, which
also covers the core-published device_tracker without a hand-kept list. Since core v0.18.0
each config also pins that id as ``default_entity_id`` (core ADR 0001), so a device area or
rename cannot prefix it; the pin is checked against the same derivation.

Pure string/YAML/AST work plus one in-process discovery run: no HA, no browser, no network.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import catalog
import main  # noqa: F401  -- importing main runs mqtt.configure(catalog)
import pytest
import yaml
from renault_mqtt import mqtt

_REPO = Path(__file__).resolve().parents[2]
_DASH_DIR = _REPO / "alpine_a290" / "dashboards"
_DASHBOARDS = sorted(_DASH_DIR.glob("*.txt"))
# The optional helper packages the add-on ships for users to install (INSTALLATION.md). Ids they
# define are the only references allowed to be absent from discovery.
_HELPER_FILES = sorted([*(_DASH_DIR / "Packages").glob("*.yaml"), *(_DASH_DIR / "Templates").glob("*.yaml")])
_SEED = _REPO / "ui-tests" / "seed.py"


def _slug(text: str) -> str:
    """homeassistant.util.slugify for ASCII input (python-slugify 9: lowercase, every
    non-alphanumeric run -> one "_", trim). Apostrophes become a separator, not nothing:
    "Driver's Seat" -> driver_s_seat. HA transliterates non-ASCII first, which this does not;
    test_entity_names_keep_the_slug_valid keeps the inputs where the two agree."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


_DEVICE_SLUG = _slug(catalog.DEVICE["name"])  # "alpine_a290"
# Entity-id prefix, object_id prefix, and the brand word the helper templates use. Matching only
# the entity-id prefix would silently skip the commonest mistake: an id written as an object_id.
_PREFIXES = sorted({_DEVICE_SLUG + "_", catalog.OBJ_PREFIX, _DEVICE_SLUG.split("_")[0] + "_"})
# ANY domain, not a listed few: a fixed list made ``switch.alpine_a290_battery_level`` invisible
# while the >= 20 guard stayed satisfied. Legitimate domains are derived and checked in
# test_dashboard_references_use_a_domain_this_build_publishes; ``sensor2.`` must be caught too.
_REF = re.compile(r"\b([a-z0-9_]+)\.((?:" + "|".join(_PREFIXES) + r")[a-z0-9_]+)")
# Domains a dashboard may reference although neither discovery nor a helper package defines
# them, each with the reason. Empty today: the core's device_tracker comes out of discovery and
# the input_* helpers out of the packages, so nothing needs listing by hand.
_ALLOWED_DOMAINS: dict[str, str] = {}


class _Recorder:
    """Keeps the last payload per topic, i.e. what the broker would retain."""

    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic, payload, retain=False):
        self.retained[topic] = payload


def _name_derived_id(domain: str, conf: dict) -> str:
    """The id Home Assistant gives a new MQTT entity when nothing pins it: the domain plus
    slug(device name + " " + entity name)."""
    return f"{domain}.{_slug(conf['device']['name'] + ' ' + conf['name'])}"


@pytest.fixture
def configs(monkeypatch) -> dict[str, dict]:
    """Every discovery config this build can publish, keyed by its ``<domain>/<segment>``.

    Runs the core's real publish_discovery with every optional capability on (all endpoints
    supported, location and the opt-in refresh button enabled), so this is the most this build
    can ever publish; a reference outside it cannot resolve on any car.
    """
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", True)
    every_ep = (set(catalog.OPTIONAL_ENDPOINTS) | {catalog.SOC_ENDPOINT}
                | {ep for *_, ep in catalog.ACTION_BUTTONS.values()})
    rec = _Recorder()
    mqtt.publish_discovery(rec, every_ep, "km")
    found = {}
    for topic, payload in rec.retained.items():
        parts = topic.split("/")
        if parts[0] != mqtt.DISCOVERY_PREFIX or parts[-1] != "config" or not payload:
            continue          # state/attribute topics, and cleared (tombstoned) configs
        found[f"{parts[1]}/{parts[-2]}"] = json.loads(payload)
    return found


@pytest.fixture
def published(configs) -> set[str]:
    """Every entity_id this build can publish, as Home Assistant would name it from the names."""
    return {_name_derived_id(key.split("/")[0], conf) for key, conf in configs.items()}


def _helper_ids() -> set[str]:
    """Ids defined by the shipped helper packages: input_* helpers by key, template entities by
    slug(name) (they set no default_entity_id)."""
    ids = set()
    for path in _HELPER_FILES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        blocks = doc if isinstance(doc, list) else doc.get("template", [])
        if isinstance(doc, dict):
            ids |= {f"{dom}.{key}" for dom, items in doc.items() if dom.startswith("input_") for key in items}
        for block in blocks:
            for dom in ("sensor", "binary_sensor"):
                ids |= {f"{dom}.{_slug(ent['name'])}" for ent in block.get(dom, [])}
    return ids


def _dashboard_refs() -> set[tuple[str, str]]:
    """(source file, entity_id) for every add-on-prefixed entity the dashboards reference."""
    found = set()
    for path in _DASHBOARDS:
        for domain, rest in _REF.findall(path.read_text(encoding="utf-8")):
            found.add((path.name, f"{domain}.{rest}"))
    return found


def _seeded() -> set[str]:
    """entity_id for every prefixed entity the UI gate seeds by name.

    Parsed with ``ast`` rather than imported: seed.py needs aiohttp and a live HA to run.
    """
    tree = ast.parse(_SEED.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _REF.fullmatch(node.value)
    }


def _allowed(eid: str) -> bool:
    """A reference in a domain _ALLOWED_DOMAINS admits, so no check below expects to find it."""
    return eid.split(".", 1)[0] in _ALLOWED_DOMAINS


def _elsewhere(eid: str, published: set[str]) -> list[str]:
    """The same name published under another domain, if any."""
    name = eid.split(".", 1)[1]
    return sorted(p for p in published if p.split(".", 1)[1] == name and p != eid)


def test_inputs_are_not_empty() -> None:
    """Guard the guard: a glob or pattern that matches nothing would pass every test below."""
    assert {p.name for p in _DASHBOARDS} >= {"front-end.txt", "front-end-bubble.txt"}
    per_file = {name: 0 for name in (p.name for p in _DASHBOARDS)}
    for name, _ in _dashboard_refs():
        per_file[name] += 1
    assert all(n >= 20 for n in per_file.values()), per_file
    assert len(_seeded()) >= 20, sorted(_seeded())
    assert len(_helper_ids()) >= 20, sorted(_helper_ids())


def test_entity_ids_are_derived_from_names(published) -> None:
    """Guard the derivation: a slug or discovery change here would silently re-open the gap."""
    assert "sensor.alpine_a290_battery_level" in published          # name slug == object_id tail
    assert "device_tracker.alpine_a290_location" in published       # core-published, not in catalog
    assert "button.alpine_a290_refresh_location" in published       # opt-in, forced on above
    # object_id tail != name slug: only the name-derived form is a real id.
    assert "button.alpine_a290_start_charging" in published
    assert "button.alpine_a290_charge_start" not in published
    assert "sensor.alpine_a290_outside_temperature" in published
    assert "sensor.alpine_a290_external_temperature" not in published
    assert "sensor.a290_battery_level" not in published              # object_id is never an id
    # Retired sensors (tombstoned) are gone; the SoC limits live on as numbers.
    assert "number.alpine_a290_charge_target_soc" in published
    assert "number.alpine_a290_minimum_soc" in published
    for retired in ("charge_target_soc", "minimum_soc", "cabin_temperature"):
        assert f"sensor.alpine_a290_{retired}" not in published


def test_every_config_pins_the_name_derived_id(configs) -> None:
    """Every discovery config carries ``default_entity_id``, and it is the id Home Assistant
    already derives from the names (core ADR 0001). Without the pin a device with an area gets
    ``<area>_`` prefixed ids (r5 #83); with a different pin new installs get ids no dashboard
    uses. Both are silent, so the value is checked per config, not just its presence."""
    assert len(configs) >= 20, sorted(configs)
    wrong = sorted(
        f"{key}: default_entity_id={conf.get('default_entity_id')!r}, "
        f"HA derives {_name_derived_id(key.split('/')[0], conf)!r}"
        for key, conf in configs.items()
        if conf.get("default_entity_id") != _name_derived_id(key.split("/")[0], conf)
    )
    assert not wrong, "Discovery configs pin an id other than the name-derived one:\n  " + "\n  ".join(wrong)
    # The pin names the id under the domain the config is published on, never another.
    assert all(conf["default_entity_id"].split(".", 1)[0] == key.split("/")[0] for key, conf in configs.items())


def test_pinned_ids_are_exactly_the_published_ids(configs, published) -> None:
    """The set of pins is the set of name-derived ids: nothing pinned that is not published,
    nothing published without a pin. The dashboards are matched against ``published``, so this
    is what makes their references the pinned ids and not merely the name-derived ones."""
    pinned = {conf["default_entity_id"] for conf in configs.values()}
    assert pinned == published, (sorted(pinned - published), sorted(published - pinned))


def test_dashboard_entities_are_the_pinned_ids(configs) -> None:
    """Every add-on entity a dashboard references is exactly a ``default_entity_id`` this build
    sends, so an install with the device in an area, or a renamed device, still resolves them."""
    pinned = {conf["default_entity_id"] for conf in configs.values()}
    helpers = _helper_ids()
    unpinned = sorted(f"{src}: {eid}" for src, eid in _dashboard_refs() if eid not in pinned and eid not in helpers)
    assert not unpinned, "Dashboards reference ids no discovery config pins:\n  " + "\n  ".join(unpinned)


def test_entity_names_keep_the_slug_valid() -> None:
    """The derivation holds only for ASCII names (HA transliterates, _slug does not), and only
    while no entity name starts with the device name (HA's MQTT integration strips that prefix)."""
    names = [meta[0] for table in (catalog.SENSORS, catalog.BINARY_SENSORS, catalog.ACTION_BUTTONS,
                                    catalog.NUMBERS) for meta in table.values()]
    assert all(n.isascii() for n in [catalog.DEVICE["name"], *names]), [n for n in names if not n.isascii()]
    device = catalog.DEVICE["name"].lower()
    assert not [n for n in names if n.lower().startswith(device)]


def test_dashboard_entities_exist(published) -> None:
    """Every dashboard entity is one this build publishes, or one a shipped helper package defines."""
    helpers = _helper_ids()
    unknown = sorted(
        f"{src}: {eid}"
        for src, eid in _dashboard_refs()
        if eid not in published and eid not in helpers and not _elsewhere(eid, published) and not _allowed(eid)
    )
    assert not unknown, (
        "Dashboards reference entity ids this build does not publish. Home Assistant names "
        "entities slug(device name + entity NAME), so check the catalog's names, not its "
        "object_ids:\n  " + "\n  ".join(unknown)
    )


def test_dashboard_entities_use_the_domain_the_catalog_publishes(published) -> None:
    """The r5 #50 regression: right name, wrong domain, renders a silent fallback."""
    wrong = sorted(
        f"{src}: {eid} — published as {', '.join(_elsewhere(eid, published))}"
        for src, eid in _dashboard_refs()
        if eid not in published and _elsewhere(eid, published) and not _allowed(eid)
    )
    assert not wrong, "Dashboard entity domains disagree with what is published:\n  " + "\n  ".join(wrong)


def _domains(ids: set[str]) -> set[str]:
    return {eid.split(".", 1)[0] for eid in ids}


def test_dashboard_references_use_a_domain_this_build_publishes(published) -> None:
    """A prefixed reference in a domain nothing here defines (``switch.``, ``climate.``, ``select.``)
    is named, not skipped: such an id can resolve on no install, and the specific checks above
    only ever compare against ids that do exist."""
    known = _domains(published) | _domains(_helper_ids()) | set(_ALLOWED_DOMAINS)
    assert {"sensor", "binary_sensor", "number", "button", "device_tracker", "input_boolean"} <= known
    foreign = sorted(f"{src}: {eid}" for src, eid in _dashboard_refs() if eid.split(".", 1)[0] not in known)
    assert not foreign, (
        "Dashboards reference add-on entities in a domain neither discovery nor a helper package "
        f"defines (known: {', '.join(sorted(known))}). Fix the reference, or add the domain to "
        "_ALLOWED_DOMAINS with its reason:\n  " + "\n  ".join(foreign)
    )


def test_allowed_domain_admits_a_reference_every_check_would_otherwise_reject(published, monkeypatch) -> None:
    """The escape hatch the failure message points at has to work end to end: a listed domain
    passes the existence and wrong-domain checks too, not only the domain check, and the same
    reference fails all three without the listing."""
    name = next(iter(published)).split(".", 1)[1]
    # One name published under another domain (the wrong-domain check's case) and one nothing
    # publishes (the existence check's case): the two checks split the cases between them.
    refs = {("fixture", f"switch.{name}"), ("fixture", "switch.alpine_a290_nothing_publishes_this")}
    monkeypatch.setattr(sys.modules[__name__], "_dashboard_refs", lambda: refs)
    checks = (test_dashboard_entities_exist, test_dashboard_entities_use_the_domain_the_catalog_publishes,
              test_dashboard_references_use_a_domain_this_build_publishes)
    for check in checks:
        with pytest.raises(AssertionError):
            check(published)
    monkeypatch.setattr(sys.modules[__name__], "_ALLOWED_DOMAINS", {"switch": "fixture"})
    for check in checks:
        check(published)


def test_helper_packages_do_not_shadow_published_ids(published) -> None:
    """A helper id the add-on also publishes would collide on install and exempt nothing real."""
    assert not _helper_ids() & published, sorted(_helper_ids() & published)


def _doc_files() -> list[Path]:
    """The prose that tells people which entities to build automations on."""
    return sorted({*_REPO.glob("*/DOCS.md"), *_REPO.glob("*/README.md"), *_REPO.glob("*/dashboards/*.md"),
                   *_REPO.glob("docs/*.md"), _REPO / "README.md"})


# Docs name entities in prose: only the add-on's own device prefix is judged (a user's charger or
# the demo entities are not ours), and the domain is part of the id.
_DOC_REF = re.compile(
    r"\b(?:sensor|binary_sensor|button|number|device_tracker|switch|select)\." + re.escape(_DEVICE_SLUG) + r"_[a-z0-9_]*"
)


def _unknown_doc_ids(text: str, known: set[str]) -> list[str]:
    """Entity ids in ``text`` that are the add-on's own but not in ``known``. Skipped on purpose:
    wildcard or truncated forms in prose (``..._heated_*``, ``..._heated_…``) and the optional
    ``*_test_*`` package, which ships separately."""
    out = set()
    for m in _DOC_REF.finditer(text):
        eid = m.group(0)
        if eid.endswith("_") or text[m.end():m.end() + 1] == "*" or "_test_" in eid or eid in known:
            continue
        out.add(eid)
    return sorted(out)


def test_documented_entities_exist(published) -> None:
    """Every add-on entity id the docs name is one this build publishes (or a shipped helper
    defines). Docs that name a renamed or r5-only id leave an automation targeting nothing; the
    r5 ids ``number.alpine_a290_soc_min_target`` / ``..._soc_max_target`` once sat in DOCS.md."""
    known = published | _helper_ids()
    docs = _doc_files()
    assert {str(p.relative_to(_REPO)) for p in docs} >= {
        "README.md", "alpine_a290/README.md", "alpine_a290/DOCS.md", "docs/dashboards-on-mobile.md"}, docs
    cited = sum(len(_DOC_REF.findall(p.read_text(encoding="utf-8"))) for p in docs)
    assert cited >= 5, "the docs cite almost no entities: the pattern or the file list has gone stale"
    unknown = sorted(f"{p.relative_to(_REPO)}: {eid}"
                     for p in docs for eid in _unknown_doc_ids(p.read_text(encoding="utf-8"), known))
    assert not unknown, (
        "Docs name entity ids this build does not publish. Home Assistant names entities "
        "slug(device name + entity NAME), so check the catalog's names, not its object_ids:\n  "
        + "\n  ".join(unknown)
    )


def test_unknown_documented_entity_is_reported(published) -> None:
    """Self-check: the docs check can fail. A sample with one real id, one invented id, a
    wrong-domain id, and the forms it must leave alone."""
    real = "sensor.alpine_a290_battery_level"
    sample = (
        f"Use `{real}` and `number.alpine_a290_soc_min_target` in an automation.\n"
        "Not `sensor.alpine_a290_battery_levl`, nor `button.alpine_a290_battery_level`.\n"
        "Wildcards stay quiet: `sensor.alpine_a290_heated_*`, `sensor.alpine_a290_heated_…`,\n"
        "as do the test package `input_boolean.a290_test_mode`, `sensor.alpine_a290_test_panel`\n"
        "and a user's own `sensor.octopus_charger`.\n"
    )
    assert _unknown_doc_ids(sample, published | _helper_ids()) == [
        "button.alpine_a290_battery_level",
        "number.alpine_a290_soc_min_target",
        "sensor.alpine_a290_battery_levl",
    ]
    assert _unknown_doc_ids(f"`{real}`", published) == []


def test_ui_gate_seeds_what_the_add_on_publishes(published) -> None:
    """The seed must not invent entities, or the UI gate validates a fiction."""
    bad = []
    for eid in sorted(_seeded()):
        if eid in published:
            continue
        other = _elsewhere(eid, published)
        bad.append(f"{eid} — published as {', '.join(other)}" if other else f"{eid} — not published")
    assert not bad, (
        "ui-tests/seed.py seeds entities that differ from what the add-on publishes, so the "
        "UI gate renders against an entity set no real install has:\n  " + "\n  ".join(bad)
    )


# --- Visibility without the optional test package -------------------------------------------
# The frontend's checkStateCondition reads a missing entity as "unknown". A condition needing
# 'off' on a package-only entity therefore never matches on a default install (the package is
# optional), and the UI gate cannot see it because ui-tests/seed.py seeds those entities too.


def _package_ids() -> set[str]:
    """Ids only the optional Packages/ define (the Templates/ ones are not test-mode)."""
    ids = set()
    for path in sorted((_DASH_DIR / "Packages").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        ids |= {f"{dom}.{key}" for dom, items in doc.items() if dom.startswith("input_") for key in items}
        for block in doc.get("template", []):
            for dom in ("sensor", "binary_sensor"):
                ids |= {f"{dom}.{_slug(ent['name'])}" for ent in block.get(dom, [])}
    return ids


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _condition_met(cond: dict, states: dict[str, str]) -> bool:
    """Mirror of the frontend's checkStateCondition for the 'state' conditions the dashboards
    use. A package entity missing from ``states`` is "unknown". Conditions on entities this test
    does not model (the add-on's own) count as met. Any other condition type fails the test
    rather than being guessed at, so a dashboard edit cannot slip past it."""
    kind = cond.get("condition", "state")
    if kind in ("and", "or", "not"):
        met = [_condition_met(c, states) for c in cond["conditions"]]
        return {"and": all(met), "or": any(met), "not": not any(met)}[kind]
    assert kind == "state", f"visibility condition type {kind!r} is not modelled here: {cond}"
    entity = cond["entity"]
    if entity not in states and entity not in _package_ids():
        return True
    current = states.get(entity, "unknown")
    if "state" in cond and current not in [str(s) for s in _as_list(cond["state"])]:
        return False
    return not ("state_not" in cond and current in [str(s) for s in _as_list(cond["state_not"])])


def _shown(card: dict, states: dict[str, str]) -> bool:
    conds = [*card.get("visibility", []),
             *(card.get("conditions", []) if card.get("type") == "conditional" else [])]
    return all(_condition_met(c, states) for c in conds)


def _walk_cards(node, states, visible=True):
    """Yield (card, visible) for every dict with a ``type``; a hidden parent hides its children."""
    if isinstance(node, list):
        for item in node:
            yield from _walk_cards(item, states, visible)
    elif isinstance(node, dict):
        if "type" in node:
            visible = visible and _shown(node, states)
            yield node, visible
        for key, value in node.items():
            if key not in ("card_mod", "visibility"):
                yield from _walk_cards(value, states, visible)


def _entity_refs(card: dict):
    """Entities a card itself binds to or acts on (not the Jinja/JS inside its styles)."""
    for key, value in card.items():
        if key in ("entity", "entity_id") and isinstance(value, str):
            yield value
        elif key in ("tap_action", "hold_action", "double_tap_action", "button_action", "data", "target"):
            if isinstance(value, dict):
                yield from _entity_refs(value)


def _load(name: str):
    return yaml.safe_load((_DASH_DIR / name).read_text(encoding="utf-8"))


def _visible_cards(name: str, states: dict[str, str]) -> list[dict]:
    return [card for card, visible in _walk_cards(_load(name), states) if visible]


def _card_lists(node):
    if isinstance(node, list):
        if node and all(isinstance(i, dict) for i in node):
            yield node
        for item in node:
            yield from _card_lists(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _card_lists(value)


@pytest.mark.parametrize("name", ["front-end.txt", "front-end-bubble.txt"])
def test_no_visible_card_needs_the_test_package_when_absent(name) -> None:
    """Default install (no package): nothing visible binds to a package-only entity, which would
    render a not-found card or a tap that does nothing."""
    package = _package_ids()
    assert len(package) >= 10, sorted(package)
    bad = sorted({e for c in _visible_cards(name, {}) for e in _entity_refs(c) if e in package})
    assert not bad, f"{name}: visible without the test package: {bad}"


def test_last_charge_shows_without_the_test_package() -> None:
    """The real Last Charge heading and its grid render without the package; the Test Charge ones
    do not. With the package they swap on show_panel, exactly as before."""
    pairs = [(a, b) for lst in _card_lists(_load("front-end.txt")) for a, b in zip(lst, lst[1:], strict=False)
             if a.get("type") == "heading" and a.get("heading") in ("Last Charge", "Test Charge")]
    assert sorted(a["heading"] for a, _ in pairs) == ["Last Charge", "Test Charge"], pairs
    panel = "binary_sensor.a290_test_show_panel"
    assert panel in _package_ids()
    for name, absent, off, on in (("Last Charge", True, True, False), ("Test Charge", False, False, True)):
        head, grid = next(p for p in pairs if p[0]["heading"] == name)
        assert grid.get("type") == "grid", (name, grid.get("type"))
        for card in (head, grid):
            assert _shown(card, {}) is absent, (name, "package absent")
            assert _shown(card, {panel: "off"}) is off, (name, "panel off")
            assert _shown(card, {panel: "on"}) is on, (name, "panel on")


def test_run_test_charge_button_follows_the_package() -> None:
    """Without the package the Run button shows on neither dashboard. With it, it shows on both
    while the test panel is off; while a test runs (panel on) only the Bubble one stays."""
    panel = "binary_sensor.a290_test_show_panel"
    for name, while_on in (("front-end.txt", False), ("front-end-bubble.txt", True)):
        for states, expected in (({}, False), ({panel: "off"}, True), ({panel: "on"}, while_on)):
            names = [c.get("name") for c in _visible_cards(name, states)]
            assert ("Run Test Charge" in names) is expected, (name, states)


def test_every_heading_card_wraps_the_h2_that_ha_2026_10_renders() -> None:
    """HA 2026.10 renders heading titles as <h2>; the wrap rule must cover it (and keep <p> for
    the declared minimum), or a long title truncates on a 360px phone."""
    headings = [c for c, _ in _walk_cards(_load("front-end.txt"), {}) if c.get("type") == "heading"]
    assert len(headings) == (_DASH_DIR / "front-end.txt").read_text(encoding="utf-8").count("type: heading\n")
    bad = [h["heading"] for h in headings if ".content p, .content h2{white-space:normal" not in h["card_mod"]["style"]]
    assert not bad, bad


def test_dark_sections_set_light_text() -> None:
    """The standard dashboard paints its sections dark whatever the user's theme. On HA's light
    theme the cards inside then drew dark text on that dark fill, so tile names were unreadable.
    Each dark section sets the text colours its cards inherit."""
    dash = _load("front-end.txt")
    dark = [c for c, _ in _walk_cards(dash, {}) if "rgba(20,23,27" in str(c.get("card_mod", {}).get("style", ""))]
    sections = dash[0]["sections"]
    assert len(dark) == len(sections) == 5, (len(dark), len(sections))
    missing = [c.get("cards", [{}])[0].get("heading", "?") for c in dark
               if "--primary-text-color:#e1e1e1" not in c["card_mod"]["style"]
               or "--secondary-text-color:#9e9e9e" not in c["card_mod"]["style"]]
    assert not missing, f"dark sections without light text: {missing}"
