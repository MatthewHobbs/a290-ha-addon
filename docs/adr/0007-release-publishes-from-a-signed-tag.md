# ADR 0007 — A release publishes from my signed tag

- **Status:** Accepted (2026-10-25). It supersedes the pre-merge publish in ADR 0006 and the pre-merge position of the pilot in ADR 0002 row 5.
- **Context:** `release.yaml` published the image on the bump PR, blocked the merge until it resolved, and then created `v<version>` with `gh release create`. That tag is lightweight and unsigned, so GitHub shows no verified tick against it, while the polygonal-zones releases, whose tags I sign, show two. Polygonal's model is the other way round: my signed tag is what publishes.
- **North star:** the tag is mine and signed, and the image users pull exists only after that tag, the Supervisor boot and CI have all passed.

## Decision

A release PR changes only `version` and the changelog heading, and publishes nothing. After it merges I run `just tag <version>`, which signs `v<version>` on that merge commit and pushes it. The tag run of `release.yaml` first checks that the tag is annotated, verified, on `main`, matches `config.yaml` and is above every earlier tag. It then builds and pushes the per-arch images, boots them under the stable Supervisor on four legs (ADR 0002 row 5), waits for CI and the UI gate on that commit (ADR 0005), publishes the multi-arch manifest users pull, attests it and creates the Release. A PR or dispatch is a dry run that builds and pushes nothing.

Removed: the `Release image published` merge gate and `release_decision.py`, which existed only to decide a PR-time publish. Added: `check-version-order.sh`, taken from polygonal-zones.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `release.yaml` publishes from a signed tag; `just tag`; version-order check; `release_decision.py` and the merge gate removed | a290 | Open | |
| 2 | `Release image published` removed from `main`'s required checks, as this merges | a290 | Open | |
| 3 | First release cut this way shows a verified tick on the tag, the manifest published after the pilot, and an attestation that verifies | a290 | Open | |
| 4 | r5 takes the same workflow, recipe and script, after a290 and never before | r5 | Open | |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Consequences

- **A window opens that ADR 0006 closed.** `main` shows the new `version` as soon as the release PR merges, and the Supervisor reads add-on files straight from `main`, so until the tag run publishes the manifest an update can be offered whose image is not there. I close it by tagging straight after the merge. The tag run takes however long the pilot and the UI gate take.
- A merged release with no tag stays unreleased and visible on `main`; nothing publishes it but me.
- Only a signed tag I push can publish, so a workflow or another contributor cannot publish by merging.
- **Not established:** that GitHub shows the verified tick for a tag cut this way, that `validated` still finds its checks on a tag run, and that `needs` over the skipped dry-run jobs behaves as I expect. Row 3 settles all three; a failure there is fixed and re-run, not worked around.
- **Not carried over from polygonal-zones:** its refusal to overwrite a different build of a version (`check-published-revision.sh`), the tag ruleset that stops `v*` tags being moved, and its failure issue.
