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
also covers the core-published device_tracker without a hand-kept list.

Pure string/YAML/AST work plus one in-process discovery run: no HA, no browser, no network.
"""

from __future__ import annotations

import ast
import json
import re
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
_DOMAINS = ("sensor", "binary_sensor", "number", "button", "device_tracker",
            "input_boolean", "input_button", "input_number", "input_datetime", "input_text")
# Entity-id prefix, object_id prefix, and the brand word the helper templates use. Matching only
# the entity-id prefix would silently skip the commonest mistake: an id written as an object_id.
_PREFIXES = sorted({_DEVICE_SLUG + "_", catalog.OBJ_PREFIX, _DEVICE_SLUG.split("_")[0] + "_"})
_REF = re.compile(r"\b(" + "|".join(_DOMAINS) + r")\.((?:" + "|".join(_PREFIXES) + r")[a-z0-9_]+)")


class _Recorder:
    """Keeps the last payload per topic, i.e. what the broker would retain."""

    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic, payload, retain=False):
        self.retained[topic] = payload


@pytest.fixture
def published(monkeypatch) -> set[str]:
    """Every entity_id this build can publish, as Home Assistant would name it.

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
    ids = set()
    for topic, payload in rec.retained.items():
        parts = topic.split("/")
        if parts[0] != mqtt.DISCOVERY_PREFIX or parts[-1] != "config" or not payload:
            continue          # state/attribute topics, and cleared (tombstoned) configs
        conf = json.loads(payload)
        ids.add(f"{parts[1]}.{_slug(conf['device']['name'] + ' ' + conf['name'])}")
    return ids


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
        if eid not in published and eid not in helpers and not _elsewhere(eid, published)
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
        if eid not in published and _elsewhere(eid, published)
    )
    assert not wrong, "Dashboard entity domains disagree with what is published:\n  " + "\n  ".join(wrong)


def test_helper_packages_do_not_shadow_published_ids(published) -> None:
    """A helper id the add-on also publishes would collide on install and exempt nothing real."""
    assert not _helper_ids() & published, sorted(_helper_ids() & published)


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
