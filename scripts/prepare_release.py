#!/usr/bin/env python3
"""prepare-release: make the one edit that turns accumulated `## Unreleased` entries into a release.

A release here is a change to `config.yaml` `version` (the Supervisor keys updates on it, and
`release.yaml` publishes the image for any PR that moves it), so releases are cut on request, not
by ordinary merges (ADR 0006). Ordinary PRs add their entry under `## Unreleased` and leave the
version alone; `docs_sync_check.py` fails any PR that moves the version without being exactly this
edit. This writes that edit and nothing else. It does not commit, push or choose the number: the
caller reads the entries below and passes a version.

Usage: prepare_release.py <version> [--dry-run]
       prepare_release.py --self-test
Exit 0 = written (or would be), 1 = refused with a reason, 2 = could not run.
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docs_sync_check import (  # noqa: E402
    _UNRELEASED,
    _clean_env,
    addon_dirs,
    changelog_entries,
    release_changelog,
    release_config,
    version_key,
    version_of,
)


def unreleased_body(text):
    m = _UNRELEASED.search(text or "")
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip("\n")


def plan(config_text, log_text, version):
    """(new config, new changelog, [reasons to refuse]). The files are only written when the list
    is empty."""
    refusals, current = [], version_of(config_text)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        # The only spelling docs_sync_check.py and the image publisher both read.
        return None, None, [f"'{version}' is not a plain X.Y.Z version number."]
    try:
        if current and version_key(version) <= version_key(current):
            refusals.append(f"{version} is not above the current version {current}.")
        released = [v for v, _ in changelog_entries(log_text)]
        if released and version_key(version) <= version_key(released[0]):
            refusals.append(f"{version} is not above the newest released entry {released[0]}.")
    except ValueError as e:
        refusals.append(str(e))
    new_log = release_changelog(log_text, version)
    if new_log is None:
        refusals.append("CHANGELOG.md has no single `## Unreleased` section with entries in it: "
                        "nothing to release.")
    new_config = release_config(config_text, version)
    if new_config is None:
        refusals.append("config.yaml has no `version:` line.")
    return new_config, new_log, refusals


def self_test():
    cfg = 'name: x\nversion: "1.2.0"\n'
    log = "# Changelog\n\n## Unreleased\n\n- wip\n\n## 1.2.0\n\n- two\n"
    cases = [  # (name, config, changelog, version, refusal needle or None)
        ("a valid release", cfg, log, "1.3.0", None),
        ("not above the current version", cfg, log, "1.2.0", "not above the current version"),
        ("below the current version", cfg, log, "1.1.0", "not above the current version"),
        ("not a version", cfg, log, "soon", "not a plain X.Y.Z"),
        ("a release candidate", cfg, log, "1.3.0-rc1", "not a plain X.Y.Z"),
        ("a version with a space", cfg, log, "1.3.0 beta", "not a plain X.Y.Z"),
        ("nothing unreleased", cfg, "## 1.2.0\n\n- two\n", "1.3.0", "nothing to release"),
        ("empty unreleased", cfg, "## Unreleased\n\n## 1.2.0\n\n- two\n", "1.3.0", "nothing to release"),
        ("config without a version", "name: x\n", log, "1.3.0", "no `version:` line"),
        ("behind the newest release entry", 'version: "1.0.0"\n', log, "1.1.0",
         "not above the newest released entry"),
    ]
    for name, c, g, v, needle in cases:
        _, _, got = plan(c, g, v)
        if (needle is None) != (not got) or (needle and needle not in " | ".join(got)):
            print(f"prepare-release self-test FAILED: {name}: want {needle!r}, got {got}", file=sys.stderr)
            return 1
    new_cfg, new_log, _ = plan(cfg, log, "1.3.0")
    if 'version: "1.3.0"' not in new_cfg or "## 1.3.0\n\n- wip" not in new_log or "## Unreleased" in new_log:
        print("prepare-release self-test FAILED: output is not the rename it claims", file=sys.stderr)
        return 1
    failed = end_to_end(cfg, log)
    if failed:
        print(f"prepare-release self-test FAILED: end to end: {failed}", file=sys.stderr)
        return 1
    print(f"prepare-release self-test: {len(cases) + 1} cases ok, end-to-end write path ok")
    return 0


def end_to_end(cfg, log):
    """Run this script for real in a throwaway git repo: it writes the files, the guard accepts
    what it wrote, and every refusal leaves the files untouched. Without this the write calls
    could be deleted and the self-test would still pass. Returns an error string, or None."""
    here = pathlib.Path(__file__).resolve().parent

    def run(cwd, script, *args):
        return subprocess.run([sys.executable, str(here / script), *args], cwd=cwd,
                              capture_output=True, text=True, env={**_clean_env(), "LABELS": ""})

    def git(cwd, *args):
        subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "user.name=t",
                        "-c", "user.email=t@example.invalid", *args], cwd=cwd, check=True,
                       capture_output=True, env=_clean_env())

    def repo(d, config, changelog):
        root = pathlib.Path(d)
        git(d, "init", "-q", "-b", "main")
        (root / "a").mkdir()
        for name, text in (("config.yaml", config), ("CHANGELOG.md", changelog)):
            with open(root / "a" / name, "w", newline="") as f:
                f.write(text)
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "base")
        return root

    def read(path):
        with open(path, newline="") as f:
            return f.read()

    crlf_cfg, crlf_log = cfg.replace("\n", "\r\n"), log.replace("\n", "\r\n")
    for label, config, changelog in (("LF", cfg, log), ("CRLF", crlf_cfg, crlf_log)):
        with tempfile.TemporaryDirectory() as d:
            root = repo(d, config, changelog)
            if run(d, "prepare_release.py", "1.3.0", "--dry-run").returncode != 0 or \
                    read(root / "a/config.yaml") != config:
                return f"{label}: --dry-run failed or wrote something"
            got = run(d, "prepare_release.py", "1.3.0")
            if got.returncode != 0:
                return f"{label}: release refused: {got.stderr.strip()[:200]}"
            want_cfg, want_log = release_config(config, "1.3.0"), release_changelog(changelog, "1.3.0")
            if read(root / "a/config.yaml") != want_cfg or read(root / "a/CHANGELOG.md") != want_log:
                return f"{label}: the files written are not the release edit"
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "chore(release): 1.3.0")
            guard = run(d, "docs_sync_check.py", "HEAD~1")
            if guard.returncode != 0:
                return f"{label}: the guard rejected what the script wrote: {(guard.stdout + guard.stderr).strip()[:200]}"

    refusals = [  # (name, config, changelog, args, dirty the tree first, text the refusal must say)
        ("a version that does not go up", cfg, log, ("1.2.0",), False, "not above"),
        ("a version that is not X.Y.Z", cfg, log, ("1.3.0-rc1",), False, "plain X.Y.Z"),
        ("a version in non-ASCII digits", cfg, log, ("\u0662.\u0660.\u0660",), False, "plain X.Y.Z"),
        ("nothing under Unreleased", cfg, "## 1.2.0\n\n- two\n", ("1.3.0",), False, "nothing to release"),
        ("uncommitted changes in the files", cfg, log, ("1.3.0",), True, "uncommitted"),
    ]
    for name, config, changelog, args, dirty, why in refusals:
        with tempfile.TemporaryDirectory() as d:
            root = repo(d, config, changelog)
            if dirty:
                with open(root / "a/CHANGELOG.md", "a") as f:
                    f.write("- uncommitted\n")
            before = (read(root / "a/config.yaml"), read(root / "a/CHANGELOG.md"))
            got = run(d, "prepare_release.py", *args)
            after = (read(root / "a/config.yaml"), read(root / "a/CHANGELOG.md"))
            if got.returncode != 1 or before != after or why not in got.stderr or "Traceback" in got.stderr:
                return (f"{name}: want a refusal (exit 1) saying '{why}' that writes nothing, "
                        f"got exit {got.returncode}: {got.stderr.strip()[:200]}")
    return None


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1 or args[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    version = args[0].lstrip("v")

    tracked = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True)
    if tracked.returncode != 0:
        print(f"cannot list tracked files: {tracked.stderr.strip()}", file=sys.stderr)
        return 2
    addons = addon_dirs([p for p in tracked.stdout.split("\0") if p])
    if len(addons) != 1:
        print(f"expected exactly one add-on directory, found {addons or 'none'}.", file=sys.stderr)
        return 2
    cfg_path, log_path = pathlib.Path(addons[0], "config.yaml"), pathlib.Path(addons[0], "CHANGELOG.md")

    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(cfg_path), str(log_path)],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print(f"refusing: {cfg_path} / {log_path} have uncommitted changes:\n{dirty}", file=sys.stderr)
        return 1

    with open(cfg_path, newline="") as f:   # newline="" keeps CRLF as it is
        config_text = f.read()
    with open(log_path, newline="") as f:
        log_text = f.read()
    new_config, new_log, refusals = plan(config_text, log_text, version)
    if refusals:
        for r in refusals:
            print(f"refusing: {r}", file=sys.stderr)
        return 1

    print(f"Releasing {version_of(config_text)} -> {version}. Entries under Unreleased:\n")
    print(unreleased_body(log_text), "\n")
    if dry:
        print("--dry-run: nothing written.")
        return 0
    with open(cfg_path, "w", newline="") as f:
        f.write(new_config)
    with open(log_path, "w", newline="") as f:
        f.write(new_log)
    print(f"Wrote {cfg_path} and {log_path}. Commit them as the whole of the release PR "
          f"(`chore(release): {version}`); docs_sync_check.py accepts nothing else.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
