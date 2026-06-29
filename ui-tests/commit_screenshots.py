#!/usr/bin/env python3
"""Commit refreshed dashboard screenshots to a PR branch via the GitHub GraphQL API.

Runs from the **trusted** `refresh-screenshots.yaml` (a `workflow_run` job checked out from the
default branch), so the SCREENSHOT_PAT never executes a PR's version of any script. The newly
rendered images (downloaded as an artifact, then resized into `docs/screenshots/`) are compared
against the PR branch's current versions over the REST API — no local `git`, no PR checkout — and
only the differing files are committed.

`createCommitOnBranch` produces a web-flow-signed ("Verified") commit, so the refresh satisfies
the repo's "require signed commits" rule without a signing key in CI; authoring it with a
fine-grained PAT (not GITHUB_TOKEN) makes the commit trigger the required checks so the PR stays
mergeable. A no-op when nothing changed.

Env: GITHUB_REPOSITORY (owner/name), BRANCH (PR head ref), HEAD_SHA (PR head commit — used as
the `expectedHeadOid`, so the mutation fails closed if the branch advanced), SCREENSHOT_PAT
(contents:write PAT). Only the listed files are considered; their on-disk content is committed.
"""
import base64
import json
import os
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


def _branch_bytes(repo, path, ref, token):
    """Return the branch's current bytes for `path` at `ref`, or None if the file is absent.
    Uses the raw media type so files near/over the 1 MB contents-API base64 limit still work."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}",
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github.raw"})
    try:
        return urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ["BRANCH"]
    head = os.environ["HEAD_SHA"]
    token = os.environ["SCREENSHOT_PAT"]

    additions = []
    for f in FILES:
        if not os.path.exists(f):
            continue
        new = open(f, "rb").read()
        if _branch_bytes(repo, f, head, token) != new:   # differs (or absent) on the branch
            additions.append({"path": f, "contents": base64.b64encode(new).decode()})
    if not additions:
        print("No screenshot changes — nothing to commit.")
        return

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
    print(f"Committed signed refresh {oid} to {branch} ({len(additions)} file(s)).")


if __name__ == "__main__":
    main()
