#!/usr/bin/env python3
"""docs-sync-check: enforce the documentation rules instead of trusting them.

CLAUDE.md says a user-facing change adds a CHANGELOG entry under `## Unreleased` and leaves
`config.yaml` `version` alone (a release is a separate, requested PR; ADR 0006), and that the
options table and entity lists in the docs describe what the code publishes.
Those were checklist items, not checks, so they drifted: `stale_hours` was documented as
"mark data stale after this many hours without a successful poll" long after the option had
stopped meaning that, and it was only caught by reading the docs during an unrelated fix.

This fails a PR that changes a documented surface WITHOUT touching its documentation. It is a
GATE, not a generator — it never edits docs; it tells you which doc you owe.

Deliberately narrow. Four triggers, each chosen because it has (near) zero false positives:

  1. the set of add-on OPTIONS changed          -> DOCS.md must be updated
  2. the set of published ENTITIES changed      -> DOCS.md or a dashboards/*.md must be updated
  3. config.yaml `version` changed              -> CHANGELOG.md must carry that exact version
  4. config.yaml `version` changed              -> the PR must be exactly a release: only
     config.yaml and CHANGELOG.md change, the version line is the only config line that moves,
     the version goes up, and `## Unreleased` is renamed to the new version with nothing else
     touched. `scripts/prepare_release.py` (`just release`) makes exactly that edit. Merging
     publishes an image, so a version that moves inside an ordinary PR cuts a release nobody
     asked for. No label waives this one.

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
It waives checks 1-3 only. It never waives CHANGELOG history or the release shape: no
doc-neutral change needs to delete, rename, reorder or bury a release, or to move the version.

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
    """The add-on version, only in the one plain spelling the rest of the pipeline reads:
    a top-level `version: "X.Y.Z"` (quotes optional) with nothing after it. A comment, a YAML tag
    or a quoted key is valid YAML that a line regex cannot see, so those are refused by
    `version_problem` rather than read as "no version"; reading None there let a bump pass."""
    if config_text is None:
        return None
    m = re.search(r'^version:[ \t]*"?([0-9]+\.[0-9]+\.[0-9]+)"?[ \t]*(?=\r?$)', config_text, re.M)
    return m.group(1) if m else None


def tree_modes(ref):
    """{path: git mode} for every file at `ref`. `git show` on a symlink returns the link TEXT, so
    a symlinked config.yaml reads as whatever its target path happens to say, while the image
    publisher follows the link and reads the target file."""
    r = _run(["git", "ls-tree", "-r", "-z", ref])
    if r.returncode != 0:
        fail_infra(f"cannot read the tree at {ref}: {r.stderr.strip()}")
    out = {}
    for entry in filter(None, r.stdout.split("\0")):
        meta, _, path = entry.partition("\t")
        out[path] = meta.split()[0]
    return out


def mode_problem(modes, config):
    """None when config.yaml is a plain file (mode 100644), else why not."""
    mode = modes.get(config)
    if mode is None or mode == "100644":
        return None
    return (f"{config} has git mode {mode}, not a regular file (100644): a symlink makes git show "
            f"the link text while the image publisher reads the file it points to.")


def alt_config_problem(tree, addon):
    """The Home Assistant info helper that publishes the image reads `config.json`, then
    `config.yml`, then `config.yaml`, and stops at the first it finds. A PR that adds either of
    the first two puts a version where this check never looks."""
    found = [f"{addon}/{n}" for n in ("config.json", "config.yml") if f"{addon}/{n}" in tree]
    if found:
        return (f"{', '.join(found)} exists beside {addon}/config.yaml: the image publisher reads "
                f"config.json, then config.yml, then config.yaml and stops at the first, so this "
                f"check would be reading the wrong file. The add-on keeps one config, config.yaml.")
    return None


def _yaml():
    try:
        import yaml
    except ImportError:
        fail_infra("PyYAML is required to cross-check config.yaml against a YAML parser "
                   "(python3 -m pip install PyYAML).")
    return yaml


def _has_merge_key(yaml, node):
    """True when any mapping in the composed document has a key tagged as a YAML merge key."""
    stack, seen = [node], set()
    while stack:
        n = stack.pop()
        if n is None or id(n) in seen:
            continue
        seen.add(id(n))
        if isinstance(n, yaml.MappingNode):
            for key, value in n.value:
                if key.tag == "tag:yaml.org,2002:merge":
                    return True
                stack += [key, value]
        elif isinstance(n, yaml.SequenceNode):
            stack += list(n.value)
    return False


def version_problem(config_text, path):
    """None when config.yaml spells its version in the one plain way `version_of` reads, else why
    not. A config that cannot be read must be loud: the check that runs on it would otherwise see
    no version, and so no change."""
    if config_text is None:
        return None
    keyish = re.findall(r"""^["']?version["']?[ \t]*:""", config_text, re.M)
    if len(keyish) != 1 or version_of(config_text) is None:
        return (f"{path} must have exactly one top-level `version: \"X.Y.Z\"` line with nothing "
                f"after it (no comment, tag or quoted key): the release check and the image "
                f"publisher both read that line, and a spelling only one of them understands lets "
                f"a version move unnoticed.")
    # The line regex above and a YAML parser can still disagree: a decoy `version:` line inside a
    # multi-line quoted scalar, an explicit `? version` key, a stray trailing quote. The image
    # publisher reads the file as YAML too (with yq, not PyYAML), so a parser's answer has to match.
    # Merge keys are refused, found in the parsed structure and not by their spelling: `<<:`,
    # `? <<`, `!!merge m:` and `!<tag:yaml.org,2002:merge> m:` are all the same key to a parser.
    # PyYAML lets the explicit key win and yq lets a later merge overwrite it (reproduced on yq
    # 4.53.6), so one file can carry two different versions.
    yaml = _yaml()
    try:
        if _has_merge_key(yaml, yaml.compose(config_text)):
            return (f"{path} uses a YAML merge key, which parsers disagree about; the add-on "
                    f"config does not need one, and it could hide which version is published.")
        parsed = yaml.safe_load(config_text)
    except (yaml.YAMLError, RecursionError) as e:
        return f"{path} is not valid YAML: {str(e).splitlines()[0][:120] if str(e) else type(e).__name__}"
    read = parsed.get("version") if isinstance(parsed, dict) else None
    if read != version_of(config_text):
        return (f"{path}: a YAML parser reads version {read!r} but this check reads "
                f"{version_of(config_text)!r} — the file must spell its version so both agree.")
    return None


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


# Horizontal whitespace only, and the line end is a lookahead: under re.M a trailing `\s*` would
# swallow the newlines after the heading, and a consumed `\r` would turn CRLF into LF.
_UNRELEASED = re.compile(r"^##[ \t]+Unreleased[ \t]*(?=\r?$)", re.M)
_VERSION_LINE = re.compile(r'^(version:[ \t]*)"?[0-9]+\.[0-9]+\.[0-9]+"?([ \t]*)(?=\r?$)', re.M)


def version_key(version):
    """Numeric sort key, so 1.10.0 is above 1.9.0. Non-numeric text is not a version."""
    parts = re.findall(r"[0-9]+", version or "")
    if not parts:
        raise ValueError(f"'{version}' is not a version number")
    return tuple(int(x) for x in parts)


def release_changelog(text, version):
    """The CHANGELOG a release of `version` must leave: `## Unreleased` renamed, every other byte
    kept. None when there is nothing to release: no such heading, more than one, or an empty
    section. The guard and `prepare_release.py` both call this, so what the script writes is by
    construction what the guard accepts."""
    found = list(_UNRELEASED.finditer(text or ""))
    if len(found) != 1:
        return None
    rest = text[found[0].end():]
    following = re.search(r"^##\s", rest, re.M)
    body = rest[:following.start()] if following else rest
    if not body.strip():
        return None
    return text[:found[0].start()] + f"## {version}" + text[found[0].end():]


def release_config(text, version):
    """config.yaml with only its `version:` line moved to `version`, or None without one."""
    new, n = _VERSION_LINE.subn(lambda m: f'{m.group(1)}"{version}"{m.group(2)}', text or "", count=1)
    return new if n else None


def release_problems(changed, config, changelog, v_before, v_after, cfg_before, cfg_after,
                     log_before, log_after):
    """Reasons a PR that moves `version` is not exactly a release; [] when it is one. Silent when
    the version did not move: an ordinary PR is not this check's business."""
    if v_before == v_after or not v_after:
        return []
    out = []
    extra = sorted(set(changed) - {config, changelog})
    if extra:
        out.append(f"version went {v_before} -> {v_after} but the PR also changes "
                   f"{', '.join(extra)} — a release PR changes only {config} and {changelog}.")
    try:
        if v_before and version_key(v_after) <= version_key(v_before):
            out.append(f"version went {v_before} -> {v_after}, which is not an increase.")
    except ValueError as e:
        out.append(str(e))
    if release_config(cfg_before, v_after) != cfg_after:
        out.append(f"{config} changed beyond its `version:` line — a release moves nothing else.")
    if release_changelog(log_before, v_after) is None:
        out.append(f"{changelog} has no `## Unreleased` section with entries to release — "
                   f"ordinary PRs add their entry there and leave the version alone.")
    elif release_changelog(log_before, v_after) != log_after:
        out.append(f"{changelog} must differ from the base only by renaming `## Unreleased` "
                   f"to `## {v_after}`.")
    return out


def _clean_env():
    """The environment for a throwaway repo's git and child processes, without GIT_*: a hook that
    runs this self-test exports GIT_DIR and GIT_INDEX_FILE, and git would then commit the test's
    files into the REAL repository instead of the temporary one."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd, *args):
    r = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "user.name=t",
                        "-c", "user.email=t@example.invalid", *args], cwd=cwd, capture_output=True, text=True,
                       env=_clean_env())
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def end_to_end():
    """Run `_end_to_end` with GIT_DIR and GIT_INDEX_FILE pointing at a decoy repository, as a git
    hook would leave them, and fail if the decoy changed: without `_clean_env` the throwaway
    repo's commits land in whatever repository the hook was running for."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _git(d, "init", "-q", "-b", "main")
        _git(d, "commit", "-q", "--allow-empty", "-m", "decoy")
        before = _git(d, "rev-list", "--all", "--count")
        saved = {k: os.environ.get(k) for k in ("GIT_DIR", "GIT_INDEX_FILE")}
        os.environ["GIT_DIR"] = os.path.join(d, ".git")
        os.environ["GIT_INDEX_FILE"] = os.path.join(d, ".git", "index")
        try:
            failed = _end_to_end()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        if failed:
            return failed
        if _git(d, "rev-list", "--all", "--count") != before:
            return "the scenarios wrote into the repository named by GIT_DIR instead of their own"
    return None


def _end_to_end():
    """Run the real script on real branches of a throwaway repo. The function-level cases above
    cannot see main() lose its call to the release check; these can, because the exit code is
    what CI acts on. Returns an error string, or None."""
    import tempfile
    from pathlib import Path
    cfg = 'name: x\nversion: "1.2.0"\noptions:\n  a: 1\n'
    log = "# Changelog\n\n## Unreleased\n\n- wip\n\n## 1.2.0\n\n- two\n"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _git(d, "init", "-q", "-b", "main")
        (root / "a/app").mkdir(parents=True)
        (root / "a/config.yaml").write_text(cfg)
        (root / "a/CHANGELOG.md").write_text(log)
        (root / "a/app/main.py").write_text("x = 1\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "base")

        def bump(text):
            return lambda: (root / "a/config.yaml").write_text(text)

        def code():
            (root / "a/app/main.py").write_text("x = 2\n")

        def release(spelling='version: "1.2.1"'):
            def do():
                (root / "a/config.yaml").write_text(cfg.replace('version: "1.2.0"', spelling))
                (root / "a/CHANGELOG.md").write_text(release_changelog(log, "1.2.1"))
            return do

        def both(*fns):
            return lambda: [f() for f in fns]

        def symlinked():
            # The link's own text reads as a valid version line; the file it points to holds the
            # config that is actually published, at a different version.
            target = 'version: "1.2.0"'
            (root / "a/config.yaml").unlink()
            os.symlink(target, root / "a/config.yaml")
            (root / "a" / target).write_text(cfg.replace("1.2.0", "9.9.9"))
            code()

        # A decoy `version:` line inside a multi-line quoted scalar, with the real key spelled
        # `? version`: the line regex reads 1.2.0 on both sides, a YAML parser reads 1.2.1.
        decoy = ('name: x\ndescription: "a\nversion: 1.2.0"\n? version\n: "1.2.1"\noptions:\n  a: 1\n')
        # (name, edits, wanted exit code, text the output must contain). The text is what stops a
        # crash, which also exits 1, from passing as a refusal.
        cases = [
            ("a real release", release(), 0, ""),
            ("an ordinary PR", both(code, lambda: (root / "a/CHANGELOG.md").write_text(
                log.replace("- wip", "- wip\n- more"))), 0, ""),
            ("a feature PR that moves the version", both(release(), code), 1, "also changes"),
            ("a version moved with a trailing comment", both(release('version: "1.2.1"  # release'), code), 1,
             "exactly one top-level"),
            ("a version moved under a quoted key", both(release('"version": "1.2.1"'), code), 1,
             "exactly one top-level"),
            ("a version moved under a YAML tag", both(release("version: !!str 1.2.1"), code), 1,
             "exactly one top-level"),
            ("a version that is not X.Y.Z", both(release('version: "1.2.1-rc1"'), code), 1,
             "exactly one top-level"),
            ("a stray trailing quote on the version", both(release('version: 1.2.1"'), code), 1,
             "YAML parser reads"),
            ("a decoy version line and an explicit key", both(bump(decoy), code), 1, "YAML parser reads"),
            ("a version moved without a changelog rename", bump(cfg.replace("1.2.0", "1.2.1")), 1,
             "only by renaming"),
            ("a version published through config.json", both(code, lambda: (root / "a/config.json").write_text(
                '{"version": "9.9.9"}')), 1, "exists beside"),
            ("a version published through config.yml", both(code, lambda: (root / "a/config.yml").write_text(
                'version: "9.9.9"\n')), 1, "exists beside"),
            ("a merge key beside the version", both(release('version: "1.2.1"\n<<: {version: "9.9.9"}'), code), 1,
             "merge key"),
            ("an explicit merge key", both(release('version: "1.2.1"\n? <<\n: {version: "9.9.9"}'), code), 1,
             "merge key"),
            ("a !!merge tagged key", both(release('version: "1.2.1"\n!!merge m: {version: "9.9.9"}'), code), 1,
             "merge key"),
            ("a long-form merge tag", both(release('version: "1.2.1"\n!<tag:yaml.org,2002:merge> m: {a: 1}'), code), 1,
             "merge key"),
            ("a symlinked config.yaml", symlinked, 1, "not a regular file"),
        ]
        for name, edits, want, needle in cases:
            _git(d, "switch", "-q", "-c", "case", "main")
            edits()
            _git(d, "add", "-A")
            _git(d, "commit", "-q", "-m", name)
            got = subprocess.run([sys.executable, str(Path(__file__).resolve()), "main"], cwd=d,
                                 capture_output=True, text=True, env={**_clean_env(), "LABELS": ""})
            _git(d, "switch", "-q", "main")
            _git(d, "branch", "-q", "-D", "case")
            out = got.stdout + got.stderr
            if got.returncode != want or needle not in out or "Traceback" in out:
                return (f"{name}: want exit {want} naming '{needle}', got {got.returncode}: "
                        f"{out.strip()[:300]}")
        # A config the tree lists but git cannot read must stop the check (exit 2), never read as
        # "no config, so no version change": that passed a bump to 9.9.9 with an extra file.
        _git(d, "switch", "-q", "-c", "unreadable", "main")
        (root / "a/config.yaml").write_text(cfg.replace("1.2.0", "9.9.9"))
        code()
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "unreadable config")
        blob = _git(d, "rev-parse", "HEAD:a/config.yaml").strip()
        (root / ".git/objects" / blob[:2] / blob[2:]).unlink()
        got = subprocess.run([sys.executable, str(Path(__file__).resolve()), "main"], cwd=d,
                             capture_output=True, text=True, env={**_clean_env(), "LABELS": ""})
        if got.returncode != 2 or "cannot read" not in got.stdout + got.stderr:
            return (f"an unreadable config: want exit 2 naming 'cannot read', got {got.returncode}: "
                    f"{(got.stdout + got.stderr).strip()[:300]}")
        # The same for the CHANGELOG at HEAD, and for the config at the merge base (head's own blob
        # is intact there, so only the merge-base refusal can produce this exit).
        for name, edits, path, rev, needle in (
                ("an unreadable changelog", lambda: (root / "a/CHANGELOG.md").write_text(
                    log.replace("- wip", "- wip, edited")), "a/CHANGELOG.md", "HEAD", "cannot read"),
                ("an unreadable base config", lambda: (root / "a/config.yaml").write_text(
                    cfg.replace("a: 1", "a: 2")), "a/config.yaml", "main", "although its tree lists it")):
            _git(d, "switch", "-q", "-c", "u2", "main")
            edits()
            _git(d, "add", "-A")
            _git(d, "commit", "-q", "-m", name)
            blob = _git(d, "rev-parse", f"{rev}:{path}").strip()
            loose = root / ".git/objects" / blob[:2] / blob[2:]
            saved = loose.read_bytes()
            loose.unlink()
            got = subprocess.run([sys.executable, str(Path(__file__).resolve()), "main"], cwd=d,
                                 capture_output=True, text=True, env={**_clean_env(), "LABELS": ""})
            loose.write_bytes(saved)
            _git(d, "switch", "-q", "main")
            _git(d, "branch", "-q", "-D", "u2")
            if got.returncode != 2 or needle not in got.stdout + got.stderr:
                return (f"{name}: want exit 2 naming '{needle}', got {got.returncode}: "
                        f"{(got.stdout + got.stderr).strip()[:300]}")
    return None


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
    # Release shape. Every case is built from one valid release and then broken one way, so a
    # check that returned [] for everything would fail all but the first and last.
    cfg = 'name: x\nversion: "1.2.0"\noptions:\n  a: 1\n'
    log = "# Changelog\n\n## Unreleased\n\n- wip\n\n## 1.2.0\n\n- two\n"
    c, g = "x/config.yaml", "x/CHANGELOG.md"
    good = (release_config(cfg, "1.2.1"), release_changelog(log, "1.2.1"))
    releases = [
        # (name, changed, v_before, v_after, cfg_after, log_after, log_before, problems, needle)
        ("a real release", [c, g], "1.2.0", "1.2.1", good[0], good[1], log, 0, ""),
        ("version did not move", ["x/app/main.py"], "1.2.0", "1.2.0", cfg, log, log, 0, ""),
        ("1.10.0 is above 1.9.0", [c, g], "1.9.0", "1.10.0", release_config(cfg, "1.10.0"),
         release_changelog(log, "1.10.0"), log, 0, ""),
        ("version bumped inside a feature PR", [c, g, "x/app/main.py"], "1.2.0", "1.2.1", good[0],
         good[1], log, 1, "also changes x/app/main.py"),
        ("version goes down", [c, g], "1.2.0", "1.1.0", release_config(cfg, "1.1.0"),
         release_changelog(log, "1.1.0"), log, 1, "not an increase"),
        ("same number, different spelling", [c, g], "1.2.0", "1.02.0", release_config(cfg, "1.02.0"),
         release_changelog(log, "1.02.0"), log, 1, "not an increase"),
        ("another config line moved", [c, g], "1.2.0", "1.2.1", good[0].replace("a: 1", "a: 2"), good[1],
         log, 1, "beyond its `version:` line"),
        ("nothing under Unreleased", [c, g], "1.2.0", "1.2.1", good[0], "## 1.2.1\n\n- two\n",
         "# Changelog\n\n## 1.2.0\n\n- two\n", 1, "no `## Unreleased` section"),
        ("empty Unreleased", [c, g], "1.2.0", "1.2.1", good[0], "## 1.2.1\n\n## 1.2.0\n\n- two\n",
         "## Unreleased\n\n## 1.2.0\n\n- two\n", 1, "no `## Unreleased` section"),
        ("entry text changed while renaming", [c, g], "1.2.0", "1.2.1", good[0],
         good[1].replace("- wip", "- wip, edited"), log, 1, "only by renaming"),
        ("renamed to a different version", [c, g], "1.2.0", "1.2.1", good[0],
         release_changelog(log, "1.2.2"), log, 1, "only by renaming"),
    ]
    for name, changed, vb, va, cfg_a, log_a, log_b, n, needle in releases:
        got = release_problems(changed, c, g, vb, va, cfg, cfg_a, log_b, log_a)
        if len(got) != n or needle not in " | ".join(got):
            print(f"docs-sync self-test FAILED: release shape: {name}: want {n} problems naming "
                  f"'{needle}', got {got}", file=sys.stderr)
            return 1
    if release_changelog(log, "1.2.1") != "# Changelog\n\n## 1.2.1\n\n- wip\n\n## 1.2.0\n\n- two\n":
        print("docs-sync self-test FAILED: the rename is not byte-for-byte the heading swap", file=sys.stderr)
        return 1
    if release_config(cfg, "1.2.1") != 'name: x\nversion: "1.2.1"\noptions:\n  a: 1\n':
        print("docs-sync self-test FAILED: the version rewrite touched more than the version", file=sys.stderr)
        return 1
    for name, text in (("two Unreleased headings", "## Unreleased\n\n- a\n\n## Unreleased\n\n- b\n"),
                       ("no Unreleased heading", "## 1.2.0\n\n- two\n")):
        if release_changelog(text, "1.2.1") is not None:
            print(f"docs-sync self-test FAILED: release_changelog accepted {name}", file=sys.stderr)
            return 1
    crlf = "## Unreleased\r\n\r\n- wip\r\n\r\n## 1.2.0\r\n\r\n- two\r\n"
    if release_changelog(crlf, "1.2.1") != crlf.replace("Unreleased", "1.2.1"):
        print("docs-sync self-test FAILED: the rename does not preserve CRLF", file=sys.stderr)
        return 1
    if release_config('version: "1.2.0"\r\nname: x\r\n', "1.2.1") != 'version: "1.2.1"\r\nname: x\r\n':
        print("docs-sync self-test FAILED: the version rewrite does not preserve CRLF", file=sys.stderr)
        return 1
    for spelling in ('version: "1.2.1"  # r', '"version": "1.2.1"', "version: !!str 1.2.1",
                     'version: "1.2.1-rc1"', 'version: "1.2.1 beta"', 'version: "1.2.1"\nversion: "1.2.2"', "name: x",
                     'version: 1.2.1"', 'name: x\ndescription: "a\nversion: 1.2.0"\n? version\n: "1.2.1"\n',
                     'version: "\u0662.\u0660.\u0660"', 'version: "1.2.1"\n<<: {version: "9.9.9"}\n',
                     'version: "1.2.1"\nx: ' + "[" * 20000,
                     'version: "1.2.1"\n? <<\n: {version: "9.9.9"}\n',
                     'version: "1.2.1"\n!!merge m: {version: "9.9.9"}\n',
                     'version: "1.2.1"\n!<tag:yaml.org,2002:merge> m: {a: 1}\n'):
        if version_problem(spelling, "c.yaml") is None:
            print(f"docs-sync self-test FAILED: version_problem accepted {spelling!r}", file=sys.stderr)
            return 1
    if mode_problem({"a/config.yaml": "100644"}, "a/config.yaml") or \
            not mode_problem({"a/config.yaml": "120000"}, "a/config.yaml") or \
            not mode_problem({"a/config.yaml": "100755"}, "a/config.yaml"):
        print("docs-sync self-test FAILED: mode_problem is wrong", file=sys.stderr)
        return 1
    if alt_config_problem({"a/config.yaml"}, "a") or not alt_config_problem({"a/config.yaml", "a/config.json"}, "a"):
        print("docs-sync self-test FAILED: alt_config_problem is wrong", file=sys.stderr)
        return 1
    if version_problem('name: x\nversion: "1.2.1"\n', "c.yaml") or version_problem(None, "c.yaml"):
        print("docs-sync self-test FAILED: version_problem refused a plain version", file=sys.stderr)
        return 1
    failed = end_to_end()
    if failed:
        print(f"docs-sync self-test FAILED: end to end: {failed}", file=sys.stderr)
        return 1
    print(f"docs-sync self-test: {len(cases) + 1 + len(trees) + len(releases) + 10} cases ok, "
          f"end-to-end scenarios ok")
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
    fork_tree = ls_tree(fork)
    try:
        base_log = base_changelog(fork_tree)
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
    # 5. A version that moves must be exactly a release. Read at the merge base like check 4, so
    # a branch behind main does not read main's later releases as its own edits. Not waivable.
    alt = alt_config_problem(set(tree), addon)
    if alt:
        history.append(alt)
    not_regular = mode_problem(tree_modes("HEAD"), config)
    if not_regular:
        history.append(not_regular)
    cfg_head = git_show("HEAD", config)
    if config in tree and cfg_head is None:
        fail_infra(f"cannot read {config} at HEAD although the tree lists it.")
    unreadable = version_problem(cfg_head, config)
    if unreadable:
        history.append(unreadable)
    cfg_fork = git_show(fork, config)
    if config in fork_tree and cfg_fork is None:
        fail_infra(f"cannot read {config} at {fork} although its tree lists it.")
    history += release_problems(changed, config, changelog, version_of(cfg_fork),
                                version_of(cfg_head), cfg_fork, cfg_head, base_text, head_text)
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
        print("\nRestore the CHANGELOG history, or reshape the release, as named above; the "
              "'docs-sync-ok' label does not waive either. To cut a release, run "
              "`just release <version>` and open that as its own PR.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
