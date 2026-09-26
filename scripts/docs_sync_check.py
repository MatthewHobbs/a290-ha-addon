#!/usr/bin/env python3
"""docs-sync-check: enforce the documentation rules instead of trusting them.

CLAUDE.md says a user-facing change bumps `config.yaml` version and adds a CHANGELOG entry,
and that the options table and entity lists in the docs describe what the code publishes.
Those were checklist items, not checks, so they drifted: `stale_hours` was documented as
"mark data stale after this many hours without a successful poll" long after the option had
stopped meaning that, and it was only caught by reading the docs during an unrelated fix.

This fails a PR that changes a documented surface WITHOUT touching its documentation. It is a
GATE, not a generator — it never edits docs; it tells you which doc you owe.

Deliberately narrow. Three triggers, each chosen because it has (near) zero false positives:

  1. the set of add-on OPTIONS changed          -> DOCS.md must be updated
  2. the set of published ENTITIES changed      -> DOCS.md or a dashboards/*.md must be updated
  3. config.yaml `version` changed              -> CHANGELOG.md must carry that exact version

Plus one invariant checked on every PR, bump or not: CHANGELOG history is append-above only.
Every `## <version>` heading at the merge base must still be there, once, in the same order, and
a version heading the PR adds must sit above all of them. Check 3 alone passed an edit that
REWROTE the previous heading into the new version instead of adding above it (r5 #92, #93): the
new heading exists, the old release is gone. The base CHANGELOG is read from the base tree's own
add-on directory, so renaming that directory cannot hide a lost release. Non-version `##` sections (`## Unreleased`) are not
entries, so they may go anywhere.
Changed TEXT under a released heading only warns: 8 of the 112 CHANGELOG commits on the two
mains did that, mostly to correct a wrong claim, a name or a dead link.

Cosmetic edits do not trigger it: renaming an icon, reordering a table, editing a comment, or
changing code that publishes nothing new. That is the point — a required check that constantly
needs waiving trains people to wave it through.

Escape hatch: the exact lowercase `docs-sync-ok` PR label, for a genuinely doc-neutral change.
It waives checks 1-3 only. It never waives CHANGELOG history: no doc-neutral change needs to
delete, rename, reorder or bury a release.

Usage: docs_sync_check.py [base-ref]        (default: origin/main)
       docs_sync_check.py --self-test
Env:   LABELS   comma-joined PR label names, supplied by the workflow

Exit 0 = clean, 1 = documentation owed or CHANGELOG history lost, 2 = the check itself could
not run (never silently passes — an unfetched base must be loud, or the gate quietly stops
gating).
"""
import os
import re
import subprocess
import sys


def _run(args):
    return subprocess.run(args, capture_output=True, text=True)


def git_show(ref, path):
    """File contents at a ref, or None when the file does not exist there (newly added)."""
    r = _run(["git", "show", f"{ref}:{path}"])
    return r.stdout if r.returncode == 0 else None


def fail_infra(msg):
    print(f"::error::docs-sync: {msg}")
    sys.exit(2)


def ls_tree(ref):
    r = _run(["git", "ls-tree", "-r", "-z", "--name-only", ref])
    if r.returncode != 0:
        fail_infra(f"cannot read the tree at {ref}: {r.stderr.strip()}")
    return [p for p in r.stdout.split("\0") if p]


def addon_dirs(paths):
    """Top-level directories holding a config.yaml. Discovered, not hard-coded, so this file is
    byte-identical in the a290 and r5 repos (they differ only in that directory's name)."""
    return sorted({p.split("/")[0] for p in paths if re.fullmatch(r"[^/]+/config\.yaml", p)})


def base_changelog(base_paths):
    """The base tree's CHANGELOG path, found in the BASE tree's own add-on directory, or None if
    it has none. Reading the head's path at the base found nothing when a PR renamed the
    directory, and "no CHANGELOG at the base" passed every deleted release."""
    addons = addon_dirs(base_paths)
    if len(addons) > 1:
        raise ValueError(f"expected at most one add-on directory at the merge base, found {addons}.")
    path = f"{addons[0]}/CHANGELOG.md" if addons else None
    return path if path in base_paths else None


def option_keys(config_text):
    """The `options:` and `schema:` blocks as {key: value} — the user-facing option surface.

    Values are compared too, not just keys: DOCS.md quotes defaults ("default 300"), so
    changing one without touching the docs leaves the table lying just as surely as adding an
    option would.
    """
    if config_text is None:
        return {}
    out, block = {}, None
    for line in config_text.splitlines():
        if re.match(r"^(options|schema):\s*$", line):
            block = line.split(":")[0]
            continue
        if re.match(r"^\S", line):          # any other top-level key ends the block
            block = None
            continue
        if block:
            m = re.match(r"^  ([A-Za-z0-9_]+):\s*(.*)$", line)
            if m:
                out[f"{block}.{m.group(1)}"] = m.group(2).strip()
    return out


# The catalog tables that decide what actually appears in Home Assistant. ICONS is deliberately
# absent: swapping an icon changes nothing a user reads about in the docs.
ENTITY_TABLES = ("SENSORS", "BINARY_SENSORS", "ACTION_BUTTONS", "NUMBERS")


def entity_ids(catalog_text):
    """Every object_id declared in the entity tables — the published-entity surface."""
    if catalog_text is None:
        return set()
    found = set()
    for table in ENTITY_TABLES:
        m = re.search(rf"^{table}\s*=\s*\{{(.*?)^\}}", catalog_text, re.S | re.M)
        if m:
            found |= set(re.findall(r'^\s*"([a-z0-9_]+)":', m.group(1), re.M))
    return found


def version_of(config_text):
    if config_text is None:
        return None
    m = re.search(r'^version:\s*"?([^"\s]+)"?\s*$', config_text, re.M)
    return m.group(1) if m else None


_VERSION_HEADING = re.compile(r"^##\s+(v?\d+(?:\.\d+)+\S*)\s*$")


def changelog_entries(text):
    """[(version, body)] in file order. A body runs to the next `## ` heading of ANY kind, so a
    non-version section (`## Unreleased`) inserted between releases is not counted as an edit to
    the release above it. Blank lines around a body are ignored; inside it, every byte counts."""
    entries, current = [], None
    for line in (text or "").splitlines():
        if re.match(r"^##\s", line):
            m = _VERSION_HEADING.match(line)
            current = [m.group(1), []] if m else None
            if current:
                entries.append(current)
        elif current:
            current[1].append(line)
    return [(v, "\n".join(body).strip()) for v, body in entries]


def changelog_history_problems(base_text, head_text, path):
    """(problems, warnings): released headings dropped, reordered or duplicated fail, as does a
    new version heading below one of them; released text that changed only warns."""
    before, after = changelog_entries(base_text), changelog_entries(head_text)
    after_versions = [v for v, _ in after]
    problems, warnings = [], []

    lost = [v for v, _ in before if v not in after_versions]
    if lost:
        problems.append(f"{path} no longer has the released entr{'y' if len(lost) == 1 else 'ies'} "
                        f"{', '.join('## ' + v for v in lost)} — add the new version ABOVE the "
                        f"previous one; do not rename or delete an existing heading.")

    kept = [v for v, _ in before if v in after_versions]
    if [v for v in dict.fromkeys(after_versions) if v in kept] != kept:
        problems.append(f"{path} reordered its released entries (was {', '.join(kept)}) — "
                        f"history is append-above only.")

    released = set(v for v, _ in before)
    top = next((i for i, v in enumerate(after_versions) if v in released), len(after_versions))
    buried = [v for v in after_versions[top:] if v not in released]
    if buried:
        problems.append(f"{path} adds {', '.join('## ' + v for v in buried)} below the released "
                        f"## {after_versions[top]} — a new version goes above every released one.")

    dupes = sorted({v for v in after_versions if after_versions.count(v) > 1})
    if dupes:
        problems.append(f"{path} has more than one '## {dupes[0]}' heading"
                        f"{' (and ' + ', '.join(dupes[1:]) + ')' if dupes[1:] else ''}.")

    after_body = dict(reversed(after))  # first occurrence wins, matching how a reader sees it
    edited = [v for v, body in before if v in after_body and after_body[v] != body]
    if edited:
        warnings.append(f"{path} changed the text of released entr{'y' if len(edited) == 1 else 'ies'} "
                        f"{', '.join('## ' + v for v in edited)} — fine for a correction; new notes "
                        f"belong under a new version.")
    return problems, warnings


def self_test():
    base = "# Changelog\n\n## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n"
    wip = "## Unreleased\n\n- wip\n\n## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n"
    # (name, head CHANGELOG, problems, warnings, substrings they must name[, base if not `base`])
    cases = [
        ("append above", "## 1.3.0\n\n- three\n\n## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n", 0, 0, []),
        ("heading replaced", "## 1.3.0\n\n- two\n\n## 1.1.0\n\n- one\n", 1, 0, ["## 1.2.0"]),
        ("reordered", "## 1.1.0\n\n- one\n\n## 1.2.0\n\n- two\n", 1, 0, ["reordered"]),
        ("released entry edited", "## 1.2.0\n\n- two, edited\n\n## 1.1.0\n\n- one\n", 0, 1,
         ["changed the text", "## 1.2.0"]),
        ("duplicate heading", "## 1.2.0\n\n- x\n\n## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n", 1, 1,
         ["more than one", "changed the text"]),
        ("file deleted", None, 1, 0, ["## 1.2.0, ## 1.1.0"]),
        ("blank lines at the boundary", "## 1.2.0\n\n- two\n\n\n\n## 1.1.0\n\n- one", 0, 0, []),
        ("non-version section inserted", "## 1.2.0\n\n- two\n\n## Notes\n\n- n\n\n## 1.1.0\n\n- one\n", 0, 0, []),
        ("demoted to ###", "## 1.2.0\n\n- two\n\n### 1.1.0\n\n- one\n", 1, 1, ["## 1.1.0", "## 1.2.0"]),
        ("unreleased section renamed to a release", "## 1.3.0\n\n- wip\n\n## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n",
         0, 0, [], wip),
        ("new version at the bottom", "## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n\n## 1.3.0\n\n- three\n", 1, 0,
         ["adds ## 1.3.0 below the released ## 1.2.0"]),
        ("new version in the middle", "## 1.2.0\n\n- two\n\n## 1.3.0\n\n- three\n\n## 1.1.0\n\n- one\n", 1, 0,
         ["adds ## 1.3.0 below"]),
        ("two new, one above and one buried", "## 1.4.0\n\n- f\n\n## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n\n"
         "## 1.3.0\n\n- three\n", 1, 0, ["adds ## 1.3.0 below"]),
        ("unreleased section at the bottom", "## 1.2.0\n\n- two\n\n## 1.1.0\n\n- one\n\n## Unreleased\n\n- wip\n",
         0, 0, []),
    ]
    for name, head, n, w, needles, *was in cases:
        got, warned = changelog_history_problems(was[0] if was else base, head, "CHANGELOG.md")
        if len(got) != n or len(warned) != w or any(x not in " | ".join(got + warned) for x in needles):
            print(f"docs-sync self-test FAILED: {name}: want {n} problems + {w} warnings naming {needles}, "
                  f"got {got} + {warned}", file=sys.stderr)
            return 1
    if changelog_history_problems(None, base, "CHANGELOG.md") != ([], []):
        print("docs-sync self-test FAILED: a newly added CHANGELOG was treated as lost history", file=sys.stderr)
        return 1
    # (name, merge-base tree, expected base CHANGELOG path or ValueError). The head of the
    # "renamed" case lives in new/, so reading the head's path at the base would find nothing.
    trees = [
        ("same directory", ["a/config.yaml", "a/CHANGELOG.md"], "a/CHANGELOG.md"),
        ("add-on directory renamed", ["old/config.yaml", "old/CHANGELOG.md", "README.md"], "old/CHANGELOG.md"),
        ("base has no CHANGELOG", ["a/config.yaml"], None),
        ("base has no add-on", ["README.md"], None),
        ("base has two add-ons", ["a/config.yaml", "b/config.yaml"], ValueError),
    ]
    for name, paths, want in trees:
        try:
            got = base_changelog(paths)
        except ValueError as e:
            got = type(e)
        if got != want:
            print(f"docs-sync self-test FAILED: {name}: want {want}, got {got}", file=sys.stderr)
            return 1
    print(f"docs-sync self-test: {len(cases) + 1 + len(trees)} cases ok")
    return 0


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"

    labels = [x.strip() for x in os.environ.get("LABELS", "").split(",")]
    waived = "docs-sync-ok" in labels

    if _run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"]).returncode != 0:
        fail_infra(f"base ref '{base}' is not available — is the checkout fetch-depth: 0?")

    diff = _run(["git", "-c", "core.quotePath=false", "diff", "--name-only", f"{base}...HEAD"])
    if diff.returncode != 0:
        fail_infra(f"cannot diff {base}...HEAD: {diff.stderr.strip()}")
    changed = set(diff.stdout.split())

    tree = ls_tree("HEAD")
    addons = addon_dirs(tree)
    if len(addons) != 1:
        fail_infra(f"expected exactly one add-on directory, found {addons or 'none'}.")
    addon = addons[0]

    config, catalog = f"{addon}/config.yaml", f"{addon}/app/catalog.py"
    docs, changelog = f"{addon}/DOCS.md", f"{addon}/CHANGELOG.md"
    guides = {p for p in tree
              if p.startswith(f"{addon}/dashboards/") and p.endswith(".md")}

    problems = []
    satisfied = []

    # 1. Options surface -> DOCS.md
    before, after = option_keys(git_show(base, config)), option_keys(git_show("HEAD", config))
    if before != after:
        delta = sorted(set(before) ^ set(after)) or \
            sorted(k for k in after if before.get(k) != after.get(k))
        if docs in changed:
            satisfied.append(f"options changed ({', '.join(delta)}) and {docs} was updated")
        else:
            problems.append(f"the add-on options changed ({', '.join(delta)}) but {docs} "
                            f"was not updated — its options table documents every one.")

    # 2. Published entities -> DOCS.md or a dashboards guide
    e_before, e_after = entity_ids(git_show(base, catalog)), entity_ids(git_show("HEAD", catalog))
    if e_before != e_after:
        delta = sorted(e_before ^ e_after)
        if changed & ({docs} | guides):
            satisfied.append(f"entities changed ({', '.join(delta)}) and the docs were updated")
        else:
            problems.append(f"the published entities changed ({', '.join(delta)}) but neither "
                            f"{docs} nor a dashboards guide was updated.")

    # 3. Version bump -> a CHANGELOG entry for that exact version
    v_before, v_after = version_of(git_show(base, config)), version_of(git_show("HEAD", config))
    if v_before != v_after and v_after:
        body = git_show("HEAD", changelog) or ""
        if re.search(rf"^##\s+{re.escape(v_after)}\s*$", body, re.M):
            satisfied.append(f"version {v_before} -> {v_after} and {changelog} documents it")
        else:
            problems.append(f"version went {v_before} -> {v_after} but {changelog} has no "
                            f"'## {v_after}' entry — Supervisor shows the update with no notes.")

    # 4. Released CHANGELOG headings survive. Against the merge base, not `base`: run locally on a
    # branch behind main, `base` has releases this branch has not merged yet, and they would read
    # as deleted. In CI HEAD is the PR merge ref, so the merge base IS `base`.
    fork = _run(["git", "merge-base", base, "HEAD"])
    if fork.returncode != 0:
        fail_infra(f"no merge base between {base} and HEAD: {fork.stderr.strip()}")
    fork = fork.stdout.strip()
    try:
        base_log = base_changelog(ls_tree(fork))
    except ValueError as e:
        fail_infra(str(e))
    # None must mean "the tree has no CHANGELOG", never "git could not read one": the first
    # passes every deletion, so a read failure on a listed file is loud instead.
    base_text = git_show(fork, base_log) if base_log else None
    head_text = git_show("HEAD", changelog) if changelog in tree else None
    if (base_log and base_text is None) or (changelog in tree and head_text is None):
        fail_infra(f"cannot read {base_log} at {fork} or {changelog} at HEAD although the tree lists it.")
    label = changelog if base_log in (None, changelog) else f"{changelog} (was {base_log})"
    history, warnings = changelog_history_problems(base_text, head_text, label)
    if waived:
        print("docs-sync: checks 1-3 waived — 'docs-sync-ok' label present; CHANGELOG history "
              "is still checked.")
        problems, satisfied = [], []
    surface = bool(problems)
    problems += history

    for line in satisfied:
        print(f"docs-sync: OK — {line}.")
    for line in warnings:
        print(f"::warning::docs-sync: {line}")

    if not problems:
        if not satisfied and not waived:
            print("docs-sync: no documented surface changed (options, entities, version).")
        return 0

    for p in problems:
        print(f"::error::docs-sync: {p}")
    if surface:
        print("\nUpdate the documentation named above, or add the 'docs-sync-ok' label if this "
              "change genuinely has no documentation impact.")
    if history:
        print("\nRestore the CHANGELOG history named above; the 'docs-sync-ok' label does not "
              "waive it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
