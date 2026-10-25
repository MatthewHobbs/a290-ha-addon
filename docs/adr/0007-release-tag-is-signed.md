# ADR 0007 — The release tag is signed, and I push it

- **Status:** Accepted (2026-10-25).
- **Context:** `release.yaml` created `v<version>` with `gh release create`, which makes a lightweight tag on the runner. A lightweight tag carries no signature, so GitHub shows no verified tick against it, while the polygonal-zones releases, whose tags I sign, show two. Nothing signed said that I cut the release.
- **North star:** a release's tag is an annotated tag signed with my key, and no workflow can create one in my name.

## Decision

After a release PR merges I run `just tag <version>`. It signs `v<version>` on that PR's merge commit and pushes it. `release-tag.yaml` runs on the tag push and creates the GitHub Release only when the tag is annotated and verified, sits on `main`, names `config.yaml`'s version, has a published and attested image, and has green CI and UI-gate checks on its commit. `release.yaml` no longer tags, and its `validated` job moves into the new workflow as a check on the commit, because nothing waits on it any more.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `release-tag.yaml`, `just tag`, and the `tag` and `validated` jobs removed from `release.yaml` | a290 | Open | |
| 2 | First release cut this way shows a verified tick on the tag and the commit | a290 | Open | |
| 3 | r5 takes the same workflow and recipe, after a290 and never before | r5 | Open | |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Consequences

- A merged release has no tag, and no Release, until I push one. The image is already live from the PR, so users are not held back; only the release notes page is.
- The publish decision (`release_decision.py`) still asks whether the tag is absent and the version moved. Between the merge and my tag it behaves exactly as before, because only a release moves the version.
- A GitHub Actions run can no longer create a release tag. That is the point, and it also means a forgotten tag stays forgotten.
- **Not established:** that GitHub reports the verified tick for a tag from this workflow's checks, until row 2 has run.
