#!/usr/bin/env python3
"""ha-minimum-check: the add-on's declared minimum Home Assistant must be a real, allowed floor.

Global rule (claude-config #165): every HA add-on declares the oldest HA it supports as
`homeassistant:` in its config.yaml, and that minimum is never newer than the .1 release of the
month before current stable (stable 2026.9.x allows 2026.8.1 or older; a January stable rolls back
to the previous December). Current stable is read live from version.home-assistant.io, so a floor
raised past the ceiling fails here rather than silently locking out users who lag a month.

It also fails when the key is missing: an undeclared range is exactly what the rule forbids.

The minimum must also be a published release, not just well-formed: `2026.7.999` or `1900.1.1`
sit under the ceiling but name no HA anyone can run. Core publishes every release to PyPI, which
answers 404 for a version it never had. Pre-releases are refused before the lookup, because PyPI
does publish betas. A lookup that fails any other way exits 2 rather than passing.

Usage: ha_minimum_check.py <config.yaml> | --self-test
Exit 0 = compliant, 1 = not compliant, 2 = the check itself could not run (never reported clean).
"""
import io
import json
import re
import sys
import urllib.error
import urllib.request

STABLE_URL = "https://version.home-assistant.io/stable.json"
PYPI_URL = "https://pypi.org/pypi/homeassistant/{}/json"
# version.home-assistant.io answers urllib's default User-Agent with 403, so name ourselves.
HEADERS = {"User-Agent": "ha-minimum-check (MatthewHobbs HA add-ons)"}
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
    req = urllib.request.Request(STABLE_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (fixed https URL)
        data = json.load(r)
    return data["homeassistant"]["default"]


def release_problem(version, urlopen=urllib.request.urlopen):
    """None if `version` is a published Home Assistant release, else why not. Only a 404 means
    "never released"; any other failure raises, so a lookup that did not happen is never an answer."""
    req = urllib.request.Request(PYPI_URL.format(version), headers=HEADERS)
    try:
        with urlopen(req, timeout=20) as r:
            info = json.load(r)["info"]
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return f"{version} is not a published Home Assistant release (PyPI has no such version)"
        raise
    # PyPI normalises the request: 2026.08.1 answers 200 as 2026.8.1.
    if info["version"] != version:
        return f"{version} is not a release as written; PyPI knows it as {info['version']}"
    if info.get("yanked"):
        return f"{version} was yanked from PyPI"
    return None


def check(declared_v, stable_v, lookup=release_problem):
    """Returns (ok, message)."""
    if declared_v is None:
        return False, "no `homeassistant:` minimum declared in config.yaml"
    floor, cap = parse(declared_v), ceiling(stable_v)
    cap_s = ".".join(map(str, cap))
    if floor > cap:
        return False, f"declared minimum {declared_v} is newer than {cap_s} (the .1 of the month before stable {stable_v})"
    problem = lookup(declared_v)
    if problem:
        return False, f"declared minimum {problem}"
    return True, f"declared minimum {declared_v} <= {cap_s} (stable {stable_v}) and is a published release"


def _fake_pypi(responses):
    """An offline urlopen: `responses` maps version -> info dict, or an HTTP status to raise."""
    def urlopen(req, timeout):
        version = req.full_url.split("/")[-2]
        if req.full_url != PYPI_URL.format(version) or req.get_header("User-agent") != HEADERS["User-Agent"]:
            raise AssertionError(f"unexpected request {req.full_url} {req.header_items()}")
        got = responses.get(version, 404)
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, "fake", None, None)
        return io.BytesIO(json.dumps({"info": got}).encode())
    return urlopen


def self_test():
    released = {"2026.7.1", "2026.8.1", "2025.12.1", "2026.10.1", "2026.9.1", "2026.9.0b1"}

    def lookup(v):
        return None if v in released else f"{v} not released (fake)"

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
        ("2026.7.999", "2026.9.3", False),  # under the ceiling, but never released
        ("1900.1.1", "2026.9.3", False),
    ]
    failed = 0
    for d, s, want in cases:
        got, _ = check(d, s, lookup)
        if got != want:
            failed += 1
            print(f"ha-minimum-check self-test FAILED: declared={d} stable={s}: expected {want}", file=sys.stderr)
    # PyPI publishes betas, so "released" alone must not let one through: parse refuses it first.
    malformed = ("2026.9", "latest", "2026.10.0b1", "2026.9.0b1", "2026.9.0.dev0", "2026.0.1", "2025.13.1", "2025.99.1")
    for bad in malformed:
        try:
            check(bad, "2026.10.2", lookup)
            failed += 1
            print(f"ha-minimum-check self-test FAILED: accepted malformed {bad!r}", file=sys.stderr)
        except ValueError:
            pass
    pypi = _fake_pypi({
        "2026.8.1": {"version": "2026.8.1", "yanked": False},
        "2026.08.1": {"version": "2026.8.1", "yanked": False},  # PyPI's normalised answer
        "2026.6.2": {"version": "2026.6.2", "yanked": True},
        "2026.6.3": 503,
    })
    lookups = [("2026.8.1", True), ("2026.7.999", False), ("2026.08.1", False), ("2026.6.2", False)]
    for v, want in lookups:
        if (release_problem(v, pypi) is None) != want:
            failed += 1
            print(f"ha-minimum-check self-test FAILED: lookup {v}: expected released={want}", file=sys.stderr)
    try:
        release_problem("2026.6.3", pypi)
        failed += 1
        print("ha-minimum-check self-test FAILED: a 503 from PyPI was read as an answer", file=sys.stderr)
    except urllib.error.HTTPError:
        pass
    if failed:
        return 2
    print(f"ha-minimum-check self-test: {len(cases) + len(malformed) + len(lookups) + 1} cases ok")
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
