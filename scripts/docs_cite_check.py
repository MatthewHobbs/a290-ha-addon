#!/usr/bin/env python3
"""docs-cite-check: every entity_id named in the docs must be one the add-on actually publishes.

The docs tell people which entities to build automations on. When an entity is renamed — or when
a passage is mirrored from the r5 twin without adjusting — the docs keep naming an id that no
longer exists, and nothing notices. The automation silently targets nothing.

That is not hypothetical. This check, while being prototyped, found `DOCS.md` documenting
`number.alpine_a290_soc_min_target` and `number.alpine_a290_soc_max_target`. The add-on publishes
neither: those are the R5's ids. a290 publishes `number.alpine_a290_minimum_soc` and
`number.alpine_a290_charge_target_soc`.

THE THING THAT MAKES THIS NON-TRIVIAL: an MQTT entity's id is NOT its discovery `object_id`.
Home Assistant ignores `object_id` and derives the id as slug(device name + " " + friendly name).
Matching on `object_id` resolves only 2 of the 10 ids cited in these docs — so a checker written
the obvious way reports eight false failures and gets switched off. Ids are derived from NAMES
here, deliberately.

Advisory by design: it prints findings and exits 0 unless --strict is passed. The docs
legitimately name third-party entities (a user's Octopus charger), the optional test package, and
wildcard forms in prose; those are filtered below, but the filter is a judgement call and a
judgement call should not block a merge until it has earned trust.

Usage: docs_cite_check.py [--strict]
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Entity domains worth checking. `device_tracker` is included because the tracker is published
# outside the catalog tables (renault_mqtt.mqtt), which is exactly the sort of entity a
# catalog-only checker would wrongly call missing.
DOMAINS = ("sensor", "binary_sensor", "button", "number", "device_tracker", "switch", "select")

# Entities the add-on does not publish and must not be judged on:
#   - anything outside the add-on's own device prefix (a user's charger, the demo entities)
#   - the optional test package (`*_test_*`), which ships separately
#   - truncated / wildcard forms used in prose, e.g. `sensor.alpine_a290_heated_…`
WILDCARDISH = re.compile(r"(\*|_)$")


def slug(text):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")


def published_ids():
    """Every entity_id the add-on actually publishes, derived from friendly NAMES."""
    addon = next(d for d in os.listdir(ROOT)
                 if os.path.isfile(os.path.join(ROOT, d, "app", "catalog.py")))
    sys.path.insert(0, os.path.join(ROOT, addon, "app"))
    import catalog

    device = catalog.DEVICE["name"]
    ids = {slug(f"{device} Location")}          # the device_tracker, published by renault_mqtt.mqtt
    for table in ("SENSORS", "BINARY_SENSORS", "ACTION_BUTTONS", "NUMBERS"):
        for meta in getattr(catalog, table, {}).values():
            ids.add(slug(f"{device} {meta[0]}"))
    return ids, slug(device)


def cited(paths):
    """(entity_id, file, line) for every entity reference in the documentation."""
    pat = re.compile(rf"\b(?:{'|'.join(DOMAINS)})\.([a-z0-9_]+)")
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    for m in pat.finditer(line):
                        out.append((m.group(1), os.path.relpath(path, ROOT), n))
        except OSError:
            continue
    return out


def main():
    strict = "--strict" in sys.argv
    ids, prefix = published_ids()

    docs = []
    for pattern in ("*/DOCS.md", "*/dashboards/*.md", "docs/*.md", "README.md"):
        docs += glob.glob(os.path.join(ROOT, pattern))

    # Some documented entities are real but user-supplied: the optional template sensors and
    # test-mode package are shipped as something you install yourself (r5 does not ship them at
    # all and its README says so). They belong in the docs and cannot be in the catalog, so each
    # repo lists them explicitly rather than the checker guessing at a prefix rule.
    ignore = set()
    ignore_file = os.path.join(HERE, "docs_cite_ignore.txt")
    if os.path.exists(ignore_file):
        with open(ignore_file, encoding="utf-8") as fh:
            ignore = {ln.split("#")[0].strip() for ln in fh if ln.split("#")[0].strip()}

    problems = []
    checked = 0
    for eid, path, line in cited(docs):
        if not eid.startswith(prefix + "_"):
            continue                       # third-party / demo / test-package entity
        if WILDCARDISH.search(eid) or "_test_" in eid or eid in ignore:
            continue                       # prose wildcard, test package, or a listed exception
        checked += 1
        if eid not in ids:
            problems.append((eid, path, line))

    if not problems:
        print(f"docs-cite-check: {checked} documented entity references all resolve.")
        return 0

    print(f"docs-cite-check: {len(problems)} documented entity reference(s) do not exist:\n")
    for eid, path, line in sorted(set(problems)):
        near = sorted(i for i in ids if slug(i).startswith(eid.split("_")[2] if eid.count("_") > 2 else eid))
        hint = f"  (did you mean: {', '.join(near[:3])}?)" if near else ""
        print(f"  {path}:{line}  {eid}{hint}")
    print("\nEntity ids are slug(device name + friendly name) — check the catalog's NAMES, "
          "not its object_ids.")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
