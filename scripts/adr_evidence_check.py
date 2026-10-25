"""Hold the decision record's evidence to the tree.

Every row of the retrospective ADR names the files and symbols it was read from. A
symbol renamed or a file moved leaves the only record of why this add-on behaves as it
does asserting something that is no longer true, and no other check notices: the parity
and docs-sync gates compare the tree against the tree, not the prose against either.

This reads the Evidence column of each row and fails on a cited file that is not there,
and on a cited symbol that appears in none of the files the row cites. Write
``core.<name>`` for a symbol that lives in the pinned renault-mqtt commit instead.

Usage: python3 scripts/adr_evidence_check.py [ADR.md]
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

DEFAULT = "docs/adr/0001-the-project-as-it-stands.md"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PATH = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|yaml|yml|sh|txt|json|md|toml|in|html)")
BARE = re.compile(r"`(Dockerfile|justfile)`")
FENCE = re.compile(r"`([^`]+)`")


def rows(text: str) -> list[tuple[str, str]]:
    """`(row number, Evidence cell)` for each table row, refusing a malformed row."""
    out = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        if line.startswith("| # ") or line.startswith("| ---"):
            continue
        cells = line.split("|")
        if len(cells) != 7:
            raise ValueError(f"row is not five columns: {line[:60]}")
        out.append((cells[1].strip(), cells[5]))
    return out


def cited(evidence: str) -> list[str]:
    """The file names a row points at, as written."""
    return PATH.findall(evidence) + BARE.findall(evidence)


def symbols(evidence: str) -> list[str]:
    """Backticked bare identifiers that are not file names and not the core's."""
    named = set(cited(evidence))
    return [
        s
        for s in FENCE.findall(evidence)
        if IDENT.fullmatch(s) and s not in named and not s.startswith("core.")
    ]


def resolve(name: str, tree: dict[str, str]) -> list[str]:
    """Every tracked path the citation could mean: exact, or the tail of a path."""
    hits = [p for p in tree if p == name or p.endswith("/" + name)]
    return hits or []


def check(text: str, tree: dict[str, str]) -> list[str]:
    """One failure line per broken citation, empty when the record holds."""
    problems = []
    for number, evidence in rows(text):
        names = cited(evidence)
        hits = [p for name in names for p in resolve(name, tree)]
        for name in names:
            if name.startswith("core/") or name.startswith("core."):
                continue
            if not resolve(name, tree):
                problems.append(f"row {number}: cites {name}, which is not in the tree")
        body = "".join(tree[p] for p in dict.fromkeys(hits)) or "".join(tree.values())
        for symbol in symbols(evidence):
            if symbol not in body:
                where = ", ".join(dict.fromkeys(hits)) or "the tree"
                problems.append(f"row {number}: cites {symbol}, absent from {where}")
    return problems


def tracked() -> dict[str, str]:
    """Tracked and new files with their text, or every file outside a checkout."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
    )
    paths = listing.stdout.split() if listing.returncode == 0 else [
        str(p) for p in pathlib.Path(".").rglob("*") if p.is_file()
    ]
    tree = {}
    for path in paths:
        if path.startswith("docs/adr/"):
            continue
        try:
            tree[path] = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return tree


def self_test() -> int:
    """Prove the check fails on rot: a rename, a missing file and a malformed row."""
    good = (
        "| # | Decision | Owner | Status | Evidence |\n| --- | --- | --- | --- | --- |\n"
        "| 1 | It polls | a290 | Done | `app/main.py` (`poll_once`) |\n"
    )
    renamed = good.replace("`poll_once`", "`poll_everything`")
    gone = good.replace("`app/main.py`", "`app/nowhere.py`")
    malformed = good.replace("| Done | `app/main.py` (`poll_once`) |", "| Done |")
    core = good.replace("(`poll_once`)", "(`core._redacted`)")
    tree = {"app/main.py": "async def poll_once():\n    pass\n"}
    cases = [
        ("a row that holds passes", good, []),
        ("a renamed symbol fails", renamed, ["poll_everything"]),
        ("a missing file fails", gone, ["nowhere.py"]),
        ("a core symbol is exempt", core, []),
    ]
    bad = 0
    for name, text, expect in cases:
        found = check(text, tree)
        ok = all(any(token in line for line in found) for token in expect) if expect else not found
        print(f"self-test {'ok  ' if ok else 'FAIL'} {name}")
        bad += not ok
    try:
        check(malformed, tree)
    except ValueError:
        print("self-test ok   a five-column row is required")
    else:
        print("self-test FAIL  a five-column row is required")
        bad += 1
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--self-test"]:
        return self_test()
    path = pathlib.Path(argv[0] if argv else DEFAULT)
    problems = check(path.read_text(encoding="utf-8"), tracked())
    for line in problems:
        print(f"::error::adr-evidence: {line}" if sys.stdout.isatty() else line)
    if problems:
        print(f"{len(problems)} citation(s) the tree does not support")
        return 1
    print(f"OK {path} cites only what the tree has")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
