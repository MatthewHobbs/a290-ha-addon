#!/usr/bin/env python3
"""Commit refreshed dashboard screenshots to a branch via the GitHub GraphQL API.

`createCommitOnBranch` produces a **web-flow-signed ("Verified") commit** — so the refreshed
screenshots satisfy the repo's "require signed commits" rule *without* putting a signing key
in CI. Authoring it with a fine-grained PAT (rather than GITHUB_TOKEN) also makes the commit
trigger the required checks, so the PR stays mergeable. A no-op when nothing changed.

Env: GITHUB_REPOSITORY (owner/name), BRANCH (head ref), SCREENSHOT_PAT (contents:write PAT).
Only the listed screenshot files are considered; their on-disk content is committed as-is.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

FILES = [
    "docs/screenshots/standard-iphone-15-pro.png",
    "docs/screenshots/standard-galaxy-s24.png",
    "docs/screenshots/bubble-iphone-15-pro.png",
    "docs/screenshots/bubble-galaxy-s24.png",
    "docs/screenshots/smart-charging-iphone-15-pro.png",
]
MUTATION = """
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid } }
}
"""


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ["BRANCH"]
    token = os.environ["SCREENSHOT_PAT"]

    changed = _git("diff", "--name-only", "--", *FILES).split()
    if not changed:
        print("No screenshot changes — nothing to commit.")
        return

    head = _git("rev-parse", "HEAD")
    additions = [{"path": f, "contents": base64.b64encode(open(f, "rb").read()).decode()}
                 for f in changed]
    variables = {"input": {
        "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
        "expectedHeadOid": head,
        "fileChanges": {"additions": additions},
        "message": {"headline": "chore: refresh dashboard screenshots [refresh-shots]"},
    }}
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": MUTATION, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as err:
        print(f"GraphQL HTTP {err.code}: {err.read().decode()}", file=sys.stderr)
        sys.exit(1)
    if resp.get("errors"):
        print("GraphQL errors:", json.dumps(resp["errors"]), file=sys.stderr)
        sys.exit(1)
    oid = resp["data"]["createCommitOnBranch"]["commit"]["oid"]
    print(f"Committed signed refresh {oid} to {branch} ({len(changed)} file(s)).")


if __name__ == "__main__":
    main()
