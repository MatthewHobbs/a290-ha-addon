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

Cosmetic edits do not trigger it: renaming an icon, reordering a table, editing a comment, or
changing code that publishes nothing new. That is the point — a required check that constantly
needs waiving trains people to wave it through.

Escape hatch: the exact lowercase `docs-sync-ok` PR label, for a genuinely doc-neutral change.

Usage: docs_sync_check.py [base-ref]        (default: origin/main)
Env:   LABELS   comma-joined PR label names, supplied by the workflow

Exit 0 = clean, 1 = documentation owed, 2 = the check itself could not run (never silently
passes — an unfetched base must be loud, or the gate quietly stops gating).
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


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"

    labels = [x.strip() for x in os.environ.get("LABELS", "").split(",")]
    if "docs-sync-ok" in labels:
        print("docs-sync: skipped — 'docs-sync-ok' label present.")
        return 0

    if _run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"]).returncode != 0:
        fail_infra(f"base ref '{base}' is not available — is the checkout fetch-depth: 0?")

    diff = _run(["git", "-c", "core.quotePath=false", "diff", "--name-only", f"{base}...HEAD"])
    if diff.returncode != 0:
        fail_infra(f"cannot diff {base}...HEAD: {diff.stderr.strip()}")
    changed = set(diff.stdout.split())

    # Locate the add-on directory rather than hard-coding it, so this file is byte-identical
    # in the a290 and r5 repos (they differ only in that directory's name).
    tree = _run(["git", "ls-tree", "-r", "--name-only", "HEAD"])
    if tree.returncode != 0:
        fail_infra("cannot read the HEAD tree.")
    addons = sorted({p.split("/")[0] for p in tree.stdout.split()
                     if re.fullmatch(r"[^/]+/config\.yaml", p)})
    if len(addons) != 1:
        fail_infra(f"expected exactly one add-on directory, found {addons or 'none'}.")
    addon = addons[0]

    config, catalog = f"{addon}/config.yaml", f"{addon}/app/catalog.py"
    docs, changelog = f"{addon}/DOCS.md", f"{addon}/CHANGELOG.md"
    guides = {p for p in tree.stdout.split()
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

    for line in satisfied:
        print(f"docs-sync: OK — {line}.")

    if not problems:
        if not satisfied:
            print("docs-sync: no documented surface changed (options, entities, version).")
        return 0

    for p in problems:
        print(f"::error::docs-sync: {p}")
    print("\nUpdate the documentation named above, or add the 'docs-sync-ok' label if this "
          "change genuinely has no documentation impact.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
