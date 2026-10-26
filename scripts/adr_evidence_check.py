"""Hold the decision record's evidence to the tree.

Every row of the retrospective ADR names the files and symbols it was read from. A
symbol renamed or a file moved leaves the only record of why this add-on behaves as it
does asserting something that is no longer true, and no other check notices: the parity
and docs-sync gates compare the tree against the tree, not the prose against either.

This reads the Evidence column of each row and fails on a cited file that is not there,
and on a cited symbol that appears in none of the files the row cites. Write
``core.<name>`` for a symbol that lives in the pinned renault-mqtt commit, and ``api.<name>``
for a setting that lives in GitHub and is read with ``gh api``.

Usage: python3 scripts/adr_evidence_check.py [ADR.md]
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

DEFAULT = "docs/adr/0001-what-this-add-on-is-why-and-how.md"
RECORD_DIR = "docs/adr"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PATH = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:py|yaml|yml|sh|txt|json|md|toml|in|html)"
    r"|[A-Za-z0-9_./-]*(?:Dockerfile|justfile)"
)
FENCE = re.compile(r"`([^`]+)`")
ROW_REF = re.compile(r"\brows? ([0-9][0-9,\sand–-]*)")
LOCAL_ONLY = {"HANDOVER.md", "AGENTS.md", "CLAUDE.md"}


def row_refs(text: str) -> list[int]:
    """Every row number the prose points at, so a deleted row cannot be cited."""
    out = []
    for group in ROW_REF.findall(text):
        out += [int(n) for n in re.findall(r"[0-9]+", group)]
    return out


def prose_paths(text: str) -> list[str]:
    """Files the prose, rather than a table row, points at."""
    body = "".join(line + "\n" for line in text.splitlines() if not line.startswith("| "))
    return [p for p in PATH.findall(body) if "/" in p]


def single_record(directory: pathlib.Path) -> list[str]:
    """Fail unless the directory holds one numbered record: a290 keeps one."""
    found = sorted(p.name for p in directory.glob("[0-9][0-9][0-9][0-9]-*.md"))
    if len(found) == 1:
        return []
    return [f"{directory} holds {len(found)} records ({', '.join(found) or 'none'}), not one"]


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
    return PATH.findall(evidence)


OUTSIDE = ("core.", "api.")


def symbols(evidence: str) -> list[str]:
    """Backticked identifiers that are neither file names nor outside the tree."""
    named = set(cited(evidence))
    return [
        s
        for s in FENCE.findall(evidence)
        if IDENT.fullmatch(s) and s not in named and not s.startswith(OUTSIDE)
    ]


def resolve(name: str, tree: dict[str, str]) -> list[str]:
    """Every tracked path the citation could mean: exact, or the tail of a path."""
    hits = [p for p in tree if p == name or p.endswith("/" + name)]
    return hits or []


def check(text: str, tree: dict[str, str]) -> list[str]:
    """One failure line per broken citation, empty when the record holds."""
    problems = []
    known = {int(number) for number, _ in rows(text)}
    for ref in row_refs(text):
        if ref not in known:
            problems.append(f"prose cites row {ref}, which the table does not have")
    for name in prose_paths(text):
        if name.startswith("core/") or name in LOCAL_ONLY:
            continue
        if not resolve(name, tree):
            problems.append(f"the prose cites {name}, which is not in the tree")
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
    api = good.replace("(`poll_once`)", "(`api.required_signatures`)")
    tree = {"app/main.py": "async def poll_once():\n    pass\n"}
    cases = [
        ("a row that holds passes", good, []),
        ("a renamed symbol fails", renamed, ["poll_everything"]),
        ("a missing file fails", gone, ["nowhere.py"]),
        ("a core symbol is exempt", core, []),
        ("a GitHub setting is exempt", api, []),
    ]
    bad = 0
    for name, text, expect in cases:
        found = check(text, tree)
        ok = all(any(token in line for line in found) for token in expect) if expect else not found
        print(f"self-test {'ok  ' if ok else 'FAIL'} {name}")
        bad += not ok
    stale_path = good + "\nRead `scripts/gone.py` first.\n"
    for name, text, token in (("a prose path that is gone fails", stale_path, "scripts/gone.py"),):
        found = check(text, tree)
        ok = any(token in line for line in found)
        print(f"self-test {'ok  ' if ok else 'FAIL'} {name}")
        bad += not ok
    stale_ref = good + "\nSee row 9 for the rest.\n"
    for name, text, token in (("a reference to a missing row fails", stale_ref, "row 9"),):
        found = check(text, tree)
        ok = any(token in line for line in found)
        print(f"self-test {'ok  ' if ok else 'FAIL'} {name}")
        bad += not ok
    try:
        check(malformed, tree)
    except ValueError:
        print("self-test ok   a five-column row is required")
    else:
        print("self-test FAIL  a five-column row is required")
        bad += 1
    bare = (
        "| # | Decision | Owner | Status | Evidence |\n| --- | --- | --- | --- | --- |\n"
        "| 1 | It builds | a290 | Done | `Dockerfile` |\n"
    )
    ok = not check(bare, {"Dockerfile": "FROM scratch\n"}) and bool(check(bare, {}))
    print(f"self-test {'ok  ' if ok else 'FAIL'} a bare Dockerfile is resolved, not skipped")
    bad += not ok
    with tempfile.TemporaryDirectory() as tmp:
        (pathlib.Path(tmp) / "0001-one.md").write_text("x")
        one = not single_record(pathlib.Path(tmp))
        (pathlib.Path(tmp) / "0002-two.md").write_text("y")
        two = bool(single_record(pathlib.Path(tmp)))
        ok = one and two
        print(f"self-test {'ok  ' if ok else 'FAIL'} a second record fails")
        bad += not ok
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--self-test"]:
        return self_test()
    path = pathlib.Path(argv[0] if argv else DEFAULT)
    problems = check(path.read_text(encoding="utf-8"), tracked())
    for line in problems:
        print(f"::error::adr-evidence: {line}" if sys.stdout.isatty() else line)
    directory = path
    while directory.name != pathlib.Path(RECORD_DIR).name and directory.parent != directory:
        directory = directory.parent
    problems += single_record(directory)
    if problems:
        print(f"{len(problems)} citation(s) the tree does not support")
        return 1
    print(f"OK {path} cites only what the tree has")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
