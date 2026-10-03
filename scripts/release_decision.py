#!/usr/bin/env python3
"""release-decision: should this run publish an image? (ADR 0006, row 8)

`release.yaml` used to publish whenever the tag `v<version>` was absent. That asks "is this
version unreleased?" but never "did this change move the version?", so after a release merged
and its post-merge gate failed (no tag), every later same-repo PR republished its own unmerged
build over the live `<image>:<version>`; and a failed `git ls-remote` (a network blip, exit 128)
read as "no such tag" with the same result. Publishing now needs both: the tag is absent AND the
version differs between the comparison base and this commit.

A PR whose base branch is not the default branch never publishes: it would be compared against
that branch, not against what users get from main.

Inputs (environment): HEAD_VERSION and BASE_VERSION, each read by the pinned Home Assistant info
helper on its own checkout (so the reader is the publisher's, not a copy), and TAG_RC, the exit
code of `git ls-remote --exit-code --tags origin refs/tags/v<version>`: 0 found, 2 absent,
anything else a failure. PR_BASE_REF and DEFAULT_BRANCH, to tell a PR into main from any other.
Output: `is_new`, `version_changed` and `publish` as `key=value` lines for $GITHUB_OUTPUT.
Exit 0 = decided, 1 = refused (and blocks the run: unsure is not "publish" and not "skip").

Usage: release_decision.py            (reads the environment)
       release_decision.py --self-test
"""
import os
import sys


class Refused(Exception):
    pass


def decide(head_version, base_version, tag_rc, off_main=False):
    """{"is_new", "version_changed", "publish"} as booleans, or raises Refused."""
    for name, value in (("HEAD_VERSION", head_version), ("BASE_VERSION", base_version)):
        if not value or value == "null":
            raise Refused(f"{name} is empty: the info helper could not read a version, so there is "
                          f"nothing to compare. Refusing to guess.")
    if tag_rc == 0:
        is_new = False
    elif tag_rc == 2:
        is_new = True
    else:
        raise Refused(f"git ls-remote exited {tag_rc} (only 0 = found and 2 = absent are answers): "
                      f"cannot tell whether v{head_version} is already released. Refusing to "
                      f"read a failure as 'no such tag'.")
    changed = head_version != base_version
    return {"is_new": is_new, "version_changed": changed,
            "publish": is_new and changed and not off_main}


def off_main(pr_base_ref, default_branch):
    """True for a PR whose base is some branch other than the default one. A push has no PR base."""
    return bool(pr_base_ref) and pr_base_ref != default_branch


def self_test():
    cases = [  # (name, head, base, rc, expected publish or None for a refusal)
        ("an ordinary change, version released", "1.2.0", "1.2.0", 0, False),
        ("a release: version moved, tag absent", "1.2.1", "1.2.0", 2, True),
        ("row 8: a release merged, its gate failed, no tag; an ordinary change follows",
         "1.2.1", "1.2.1", 2, False),
        ("version moved back to one that is already released", "1.2.0", "1.2.1", 0, False),
        ("git ls-remote failed (exit 128)", "1.2.1", "1.2.0", 128, None),
        ("git ls-remote failed on an ordinary change", "1.2.0", "1.2.0", 128, None),
        ("git ls-remote exit 1", "1.2.1", "1.2.0", 1, None),
        ("the head version could not be read", "", "1.2.0", 2, None),
        ("the base version could not be read", "1.2.1", "", 2, None),
        ("the helper printed null", "null", "1.2.0", 2, None),
    ]
    off_main_cases = [  # (name, base ref, default branch, expected off_main)
        ("a push", "", "main", False),
        ("a PR into main", "main", "main", False),
        ("a PR into another branch", "feature", "main", True),
    ]
    for name, ref, default, want in off_main_cases:
        if off_main(ref, default) != want:
            print(f"release-decision self-test FAILED: {name}: want off_main={want}", file=sys.stderr)
            return 1
    if decide("1.2.1", "1.2.0", 2, off_main=True)["publish"] or not decide("1.2.1", "1.2.0", 2)["publish"]:
        print("release-decision self-test FAILED: a PR off the default branch must not publish",
              file=sys.stderr)
        return 1
    for name, head, base, rc, want in cases:
        try:
            got = decide(head, base, rc)["publish"]
        except Refused:
            got = None
        if got != want:
            print(f"release-decision self-test FAILED: {name}: want {want}, got {got}", file=sys.stderr)
            return 1
    # What the old rule did, so the row-8 case above is shown to differ: it published on "tag absent".
    if not decide("1.2.1", "1.2.1", 2)["is_new"] or decide("1.2.1", "1.2.1", 2)["publish"]:
        print("release-decision self-test FAILED: the row-8 case no longer separates the rules",
              file=sys.stderr)
        return 1
    print(f"release-decision self-test: {len(cases) + len(off_main_cases) + 2} cases ok")
    return 0


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    try:
        out = decide(os.environ.get("HEAD_VERSION", ""), os.environ.get("BASE_VERSION", ""),
                     int(os.environ.get("TAG_RC", "-1")),
                     off_main(os.environ.get("PR_BASE_REF", ""), os.environ.get("DEFAULT_BRANCH", "")))
    except (Refused, ValueError) as e:
        print(f"::error::release-decision: {e}")
        return 1
    for key, value in out.items():
        print(f"{key}={'true' if value else 'false'}")
    print(f"::notice::release-decision: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
