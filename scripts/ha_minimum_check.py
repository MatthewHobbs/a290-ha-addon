#!/usr/bin/env python3
"""ha-minimum-check: the add-on's declared minimum Home Assistant must be a real, allowed floor.

Global rule (claude-config #165): every HA add-on declares the oldest HA it supports as
`homeassistant:` in its config.yaml, and that minimum is never newer than the .1 release of the
month before current stable (stable 2026.9.x allows 2026.8.1 or older; a January stable rolls back
to the previous December). Current stable is read live from version.home-assistant.io, so a floor
raised past the ceiling fails here rather than silently locking out users who lag a month.

It also fails when the key is missing: an undeclared range is exactly what the rule forbids.

Usage: ha_minimum_check.py <config.yaml> | --self-test
Exit 0 = compliant, 1 = not compliant, 2 = the check itself could not run (never reported clean).
"""
import json
import re
import sys
import urllib.request

STABLE_URL = "https://version.home-assistant.io/stable.json"
VERSION = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d+)$")


def parse(v):
    m = VERSION.match(v.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"not a YYYY.M.P Home Assistant version: {v!r}")
    return tuple(int(x) for x in m.groups())


def ceiling(stable):
    """The newest minimum allowed while `stable` is current: the .1 of the month before."""
    year, month, _ = parse(stable)
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return (year, month, 1)


def declared(path):
    # config.yaml is flat YAML; read the one top-level key without a YAML dependency.
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"""^homeassistant:\s*["']?([^"'\s#]+)["']?\s*(#.*)?$""", line)
            if m:
                return m.group(1)
    return None


def current_stable():
    # The endpoint answers urllib's default User-Agent with 403, so name ourselves.
    req = urllib.request.Request(STABLE_URL, headers={"User-Agent": "ha-minimum-check (MatthewHobbs HA add-ons)"})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (fixed https URL)
        data = json.load(r)
    return data["homeassistant"]["default"]


def check(declared_v, stable_v):
    """Returns (ok, message)."""
    if declared_v is None:
        return False, "no `homeassistant:` minimum declared in config.yaml"
    floor, cap = parse(declared_v), ceiling(stable_v)
    cap_s = ".".join(map(str, cap))
    if floor > cap:
        return False, f"declared minimum {declared_v} is newer than {cap_s} (the .1 of the month before stable {stable_v})"
    return True, f"declared minimum {declared_v} <= {cap_s} (stable {stable_v})"


def self_test():
    cases = [
        ("2026.7.1", "2026.9.3", True),
        ("2026.8.1", "2026.9.3", True),    # the ceiling itself is allowed
        ("2026.8.2", "2026.9.3", False),   # past the .1
        ("2026.9.0", "2026.9.3", False),
        ("2025.12.1", "2026.1.0", True),   # January rolls back to the previous December
        ("2026.1.1", "2026.1.4", False),
        ("2026.10.1", "2026.11.0", True),  # two-digit months compare numerically, not as text
        ("2026.9.1", "2026.10.2", True),
        (None, "2026.9.3", False),         # undeclared
    ]
    failed = 0
    for d, s, want in cases:
        got, _ = check(d, s)
        if got != want:
            failed += 1
            print(f"ha-minimum-check self-test FAILED: declared={d} stable={s}: expected {want}", file=sys.stderr)
    for bad in ("2026.9", "latest", "2026.10.0b1", "2026.0.1", "2025.13.1", "2025.99.1"):
        try:
            parse(bad)
            failed += 1
            print(f"ha-minimum-check self-test FAILED: parsed malformed {bad!r}", file=sys.stderr)
        except ValueError:
            pass
    if failed:
        return 2
    print(f"ha-minimum-check self-test: {len(cases) + 6} cases ok")
    return 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 1:
        print("usage: ha_minimum_check.py <config.yaml> | --self-test", file=sys.stderr)
        return 2
    try:
        d = declared(argv[0])
        if d is None:                 # undeclared fails on its own, network or not
            print("ha-minimum-check: no `homeassistant:` minimum declared in config.yaml")
            return 1
        stable = current_stable()
        ok, msg = check(d, stable)
    except Exception as err:  # network, JSON shape, unreadable file, malformed version
        print(f"ha-minimum-check: could not run: {err}", file=sys.stderr)
        return 2
    print(f"ha-minimum-check: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
