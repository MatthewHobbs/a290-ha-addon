#!/usr/bin/env python3
"""pii-check: refuse committed VINs and high-precision coordinate pairs.

This repo is public and the add-on publishes one real car's identity and location. Three
real-PII leaks reached it in two days (a VIN in a test fixture, home coordinates in BACKLOG.md
prose, the same coordinates in two test fixtures), each found by a grep run for another reason.
No scanner has a rule for either shape, so this is the rule.

It replaces two inline CI steps that could not fail when they should have: the coordinate step
piped `git grep` into `grep -v` under the default Actions shell (no pipefail), so a failing
`git grep` read as "clean"; and it scanned an extension allowlist that missed shipped text such
as panel.html. Here every tracked text file is scanned, and anything that stops the scan exits 2.
CI and `just ci` both run it, so a leak is caught before push rather than after publication.

Rules, applied to every git-tracked text file:
  VIN         a 17-character string over A-HJ-NPR-Z0-9. Real VINs never contain I, O or Q, so a
              stub such as VF1STUBVIN0000000 does not match and needs no exemption.
  coordinates two numbers with 4+ decimals forming a valid latitude/longitude in either order,
              within GAP characters of each other on one line or across two adjacent
              lines (pretty-printed JSON/YAML puts them on consecutive lines). A key whose
              number sits alone on a deeper line (`latitude:` then `  51.5...`) is read as
              if it were on the key's line, so the split form is caught exactly where the
              one-line form would be; nested mappings and block scalars are not joined.

A deliberate synthetic coordinate fixture opts out with a trailing `# synthetic-coords: <why>`
(or `//`) comment on the SAME line as either number. JSON cannot carry one: use a pair below
four decimals instead (e.g. 51.5 / -0.1), which is also the preferred fix everywhere else.

Usage: pii_check.py [--self-test]
Exit 0 = clean, 1 = PII found, 2 = the check itself could not run.
"""
import re
import subprocess
import sys

SKIP_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".ico", ".ttf", ".woff", ".woff2")
GAP = 80

# Neighbours are any letter or digit, but not underscore: \b let `car_<VIN>` through, and
# excluding lower case keeps digit runs inside hex hashes from matching.
VIN = re.compile(r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])")
NUM = re.compile(r"(?<![\d.])[-+]?\d{1,3}\.\d{4,}(?![\d.])")
# A trailing comment only: nothing quoted may follow it, or a string value could carry it.
MARKER = re.compile(r"(#|//)\s*synthetic-coords:[^\"'`]*$")
# `key:` with nothing after the colon but a comment. A block scalar (`key: |`) has content there.
KEY_ONLY = re.compile(r"""^(\s*)(?:-\s+)?(?:"[^"]*"|'[^']*'|[^\s#"'-][^#]*?)\s*:\s*(?:#.*)?$""")
# A lone, optionally quoted number, as a split key's value. A mapping (`x: 1.2345`) is not one.
# JSON may close its object or array on the same line (`<n>}]`, `<n>},`); nothing else may follow.
BARE_NUM = re.compile(r"""^(\s*)["']?[-+]?\d{1,3}\.\d{4,}["']?\s*,?\s*(?:[}\]]+\s*,?\s*)?(?:(?:#|//).*)?$""")
BLANK_OR_COMMENT = re.compile(r"^\s*(?:#.*)?$")


def vin_hits(lines):
    return [i for i, line in enumerate(lines) if VIN.search(line)]


def _pair_in(text):
    nums = list(NUM.finditer(text))
    # Every pair within GAP, not just neighbours: an altitude or accuracy can sit between them.
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if b.start() - a.end() > GAP:
                break
            x, y = abs(float(a.group())), abs(float(b.group()))
            # Either order: JSON/YAML key order is not fixed, and GeoJSON is longitude-first.
            if max(x, y) <= 180 and min(x, y) <= 90:
                return True
    return False


def _joined(lines):
    """(index, text) per line, minus each key-only line whose value is a lone number on a deeper
    line, and the blank or comment lines between them. Dropping the key puts that number where
    the one-line form had it, next to its partner, and keeps the marker rule "same line as the
    number"."""
    out, i = [], 0
    while i < len(lines):
        key = KEY_ONLY.match(lines[i])
        if key:
            j = i + 1
            while j < len(lines) and BLANK_OR_COMMENT.match(lines[j]):
                j += 1
            val = BARE_NUM.match(lines[j]) if j < len(lines) else None
            if val and len(val.group(1)) > len(key.group(1)):
                i = j
        out.append((i, lines[i]))
        i += 1
    return out


def coord_hits(lines):
    hits = []
    joined = _joined(lines)
    for k, (i, line) in enumerate(joined):
        if MARKER.search(line):
            continue
        if _pair_in(line):
            hits.append(i)
            continue
        # A pair split across this line and the next; either line may carry the marker.
        nxt = joined[k + 1][1] if k + 1 < len(joined) else ""
        if NUM.search(line) and NUM.search(nxt) and not MARKER.search(nxt) and _pair_in(line + " " + nxt):
            hits.append(i)
    return hits


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True)
    if out.returncode != 0:
        print(f"pii-check: git ls-files failed: {out.stderr.decode(errors='replace').strip()}", file=sys.stderr)
        sys.exit(2)
    return [p for p in out.stdout.decode().split("\0") if p and not p.lower().endswith(SKIP_SUFFIXES)]


def read_text(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"pii-check: cannot read {path}: {e}", file=sys.stderr)
        sys.exit(2)
    # No binary sniffing: one NUL byte would hide the whole file. Known binary types are skipped
    # by suffix above; an unlisted one that trips a rule fails loudly and gets its suffix added.
    return data.decode("utf-8", errors="replace").splitlines()


def scan():
    files = tracked_files()
    if not files:
        print("pii-check: no tracked files found; refusing to report clean", file=sys.stderr)
        return 2
    found = False
    for path in files:
        lines = read_text(path)
        for rule, hits in (("VIN-shaped string", vin_hits(lines)), ("coordinate pair", coord_hits(lines))):
            for i in hits:
                found = True
                print(f"::error file={path},line={i + 1}::{rule} committed ({path}:{i + 1})")
    if found:
        print("PII found. VINs: use a stub containing I, O or Q (e.g. VF1STUBVIN0000000). Coordinates: "
              "use a pair below four decimals, or mark a deliberate fixture with a same-line "
              "'# synthetic-coords: <why>' comment.")
        return 1
    print(f"pii-check: {len(files)} tracked files, no VINs or unmarked coordinate pairs")
    return 0


def self_test():
    # Built at runtime so this file never contains the shapes it rejects.
    lat, lon = "12." + "3456", "-45." + "6789"
    vin = "VF1" + "A" * 14
    cases = [
        ("vin", [f"vin: {vin}"], True),
        ("vin stub with I/O/Q", ["vin: VF1STUBVIN0000000"], False),
        ("vin after an underscore", [f"car_{vin}"], True),
        ("vin inside a longer run", [f"X{vin}"], False),
        ("digit run inside a hex hash", ["sha256:9f9a" + "f" + "1" * 17 + "f99"], False),
        ("pair, one line", [f"gpsLatitude={lat}, gpsLongitude={lon}"], True),
        ("pair, other keys between", [f'"latitude": {lat}, "gps_accuracy": 8, "source_type": "gps", "longitude": {lon}'], True),
        ("pair with an altitude between", [f"latitude: {lat}, altitude: 250.{'1234'}, longitude: {lon}"], True),
        ("pair, two lines", [f"latitude: {lat}", f"longitude: {lon}"], True),
        ("marker, same line", [f"x = ({lat}, {lon})  # synthetic-coords: test"], False),
        ("marker, js comment", [f"f({lat}, {lon}); // synthetic-coords: test"], False),
        ("marker, second line of a split pair", [f"latitude: {lat}", f"longitude: {lon}  # synthetic-coords: t"], False),
        ("marker inside a string value", [f'{{"note": "# synthetic-coords: x", "latitude": {lat}, "longitude": {lon}}}'], True),
        ("marker word in prose does not exempt", [f"the synthetic-coords marker was not used for {lat}, {lon}"], True),
        ("low precision", ["gpsLatitude=51.5, gpsLongitude=-0.1"], False),
        ("longitude first, beyond 90", [f"longitude: 151.{'2093'}, latitude: -33.{'8688'}"], True),
        ("both beyond 90", [f"ratio 123.{'4567'} vs 145.{'6789'}"], False),
        ("vin, key and value split", ["vin:", f"  {vin}"], True),
        ("split keys, both", ["latitude:", f"  {lat}", "longitude:", f"  {lon}"], True),
        ("split key, second only", [f"latitude: {lat}", "longitude:", f"  {lon}"], True),
        ("split key, comment and blank between", ["latitude:  # home", "", "  # degrees", f"  {lat}", "longitude:", f"  {lon}"], True),
        ("split keys, JSON", ['"latitude":', f"  {lat},", '"longitude":', f"  {lon}"], True),
        ("split keys, sequence item", ["- latitude:", f"    {lat}", "  longitude:", f"    {lon}"], True),
        ("split keys, marker on a value line", ["latitude:", f"  {lat}", "longitude:", f"  {lon}  # synthetic-coords: t"], False),
        ("split keys, marker on the key line only", ["latitude:  # synthetic-coords: t", f"  {lat}", "longitude:", f"  {lon}"], True),
        ("split keys, low precision", ["latitude:", "  51.5", "longitude:", "  -0.1"], False),
        ("numbers in sibling nested mappings", ["zone_a:", f"  radius: {lat}", "zone_b:", f"  radius: {lon}"], False),
        ("nested mappings under commented keys", ["zone_a:  # home", f"  radius: {lat}", "zone_b:  # work", f"  radius: {lon}"], False),
        ("block scalars are not split keys", ["a: |", f"  {lat}", "b: >", f"  {lon}"], False),
        ("value not deeper than its key", ["a:", f"{lat}", "b:", f"{lon}"], False),
        ("comment ending in a colon is not a key", [f"gain: {lat}", "# offset:", f"  {lon}"], False),
        ("split JSON, last property closes the object", ["{", '  "latitude":', f"    {lat},", '  "longitude":', f"    {lon}}}"], True),
        ("split JSON, last value closes an array", ["[", '  "latitude":', f"    {lat},", '  "longitude":', f"    {lon}]"], True),
        ("split JSON, closes object then array", ["[{", '  "latitude":', f"    {lat},", '  "longitude":', f"    {lon}}}]"], True),
        ("split JSON, nested closes then a comma", ['{"a": {', '  "latitude":', f"    {lat},", '  "longitude":', f"    {lon}}}}},"], True),
        ("split JSON, array then object close", ['{"a": [{', '  "latitude":', f"    {lat},", '  "longitude":', f"    {lon}}}]}}"], True),
        ("split values followed by other text", ["gain:", f"  {lat}}} dB", "trim:", f"  {lon} dB"], False),
        ("closer with no partner coordinate", ["{", '  "latitude":', f"    {lat}}}", "{", '  "zoom":', "    3}"], False),
    ]
    failed = 0
    for name, lines, want in cases:
        got = bool(vin_hits(lines) if name.startswith("vin") else coord_hits(lines))
        if got != want:
            failed += 1
            print(f"pii-check self-test FAILED: {name}: expected {'hit' if want else 'clean'}", file=sys.stderr)
    if failed:
        return 2
    print(f"pii-check self-test: {len(cases)} cases ok")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    if sys.argv[1:]:
        print("usage: pii_check.py [--self-test]", file=sys.stderr)
        sys.exit(2)
    sys.exit(scan())
