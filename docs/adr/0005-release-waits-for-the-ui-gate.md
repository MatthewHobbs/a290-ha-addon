# ADR 0005 — Release publishing waits for the UI gate

- **Status:** Accepted (2026-09-27). I chose Option B of the RFC on the day it was raised.
- **Context:** on `v1.28.11`, the release/tag/image-publish pipeline (`release.yaml`) tagged and published within 2 minutes of the merge while the same push's UI-gate render (`ui-tests.yaml`) was still running for another 11 minutes on that commit. Separately, the UI gate is not a required PR check at all. Proposed as [RFC 0015: release publishing must wait for the UI gate](https://claude.ai/artifact/F5katxNE46yugU8HH5GDv7), indexed in claude-config's `docs/rfc/README.md` since a290 has no RFC index of its own yet.
- **North star:** a release cannot publish, and a PR cannot merge, ahead of a UI-gate verdict on that exact commit — whether or not the UI gate was even relevant to what changed.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

`ui-tests.yaml` reports a verdict on every push and every PR, applicable or not, using the same `if: always()` pattern `release.yaml`'s own `release-gate` job already proves out. `release.yaml`'s push-triggered build/manifest/tag sequence waits for that verdict (and `CI`'s) before doing anything. The UI gate then becomes safe to add as a required PR check for the first time.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `ui-tests.yaml`: remove the trigger-level `paths:` filter (both `push` and `pull_request`); add a `changes` job (using `dorny/paths-filter`, the same patterns moved from the trigger) and gate the render jobs on it (`needs` + `if`); the workflow's overall result is `success` whether relevant or not | a290 | Done | #189 (`84c8671`). Verified live both ways: a change touching `ui-tests.yaml` itself rendered fully on both legs (13-14 min each, zero findings); a throwaway PR touching neither dashboards nor `ui-tests/**` reported `Decide whether this touches the render: pass` and `Dashboard responsive render: skipping` in seconds |
| 2 | `release.yaml`: gate the push path on `CI` and `UI Tests` reporting success on `github.sha`, using a maintained wait-for-check action (`lewagon/wait-on-check-action`) rather than a hand-rolled poll | a290 | Done, one case unverified | #189 (`84c8671`). Narrowed to gating `tag` alone, not `build`/`manifest`: the image is already published from the version-bump PR before merge (see the file's own header comment), so only the git tag + GitHub Release — the actual "this version is out" signal — needed the wait. Verified live that `validated` correctly no-ops (reports skipped, does not block `tag`) when there is no version bump. **Not yet verified**: the actual wait-then-gate sequence for a real version bump, since none has shipped since this merged — to be confirmed and recorded here at the next real release |
| 3 | `refresh-screenshots.yaml`: add a guard so a render that was itself skipped (not failed) does not try to download a `dashboard-screenshots` artifact that was never produced | a290 | Done | #189 (`84c8671`). First test attempt (against an unmerged branch) failed for an unrelated reason worth recording: `workflow_run`-triggered workflows always read their own YAML from the repo's default branch, never the PR branch, so the fix couldn't be exercised pre-merge. Re-tested after merging to main (throwaway PR #191): `Screenshot drift` correctly logged "No dashboard-screenshots artifact on this run ... Nothing to check for drift" and skipped the download entirely |
| 4 | Add `Dashboard responsive render (mobile matrix, stable)` and `(minimum)` to `main`'s required status checks, once rows 1-3 have held on a live test PR | a290 | Done | Added via the branch-protection API once rows 1-3's live tests both passed; confirmed compliant with `repo-protect.sh`. A real docs-only PR (#192) opened right after came back `mergeStateStatus: BLOCKED` despite every check showing pass/skipping — a genuine bug, not a false alarm: a job-level `if:` on a matrix job that evaluates false stops the matrix from ever expanding, so GitHub creates one check-run under the raw, unexpanded template name instead of the two named ones the requirement lists, leaving both permanently "Expected". Fixed by moving the `if:` onto each substantive step instead of the job, so the matrix still expands into two real, correctly-named, fast-skipping jobs. Re-verified: #192 went to `mergeStateStatus: CLEAN` with both required render checks showing their proper names |
| 5 | Verify live against two real PRs before calling this Done: one that touches a dashboard (must still fully render and gate), one that touches neither dashboards nor `ui-tests/**` (must report green fast, with no render attempted, and must not stall the release path) | a290 | Done, with one gap noted | Both done (#189, and throwaway #190/#191 for the two workflows that needed a merged main to test for real), plus row 4's matrix check-run bug caught by #192 and fixed in the same PR before merging. The one remaining gap: no real version-bump release has happened yet to prove row 2's wait-then-gate sequence end to end, rather than only its no-op path |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context (2026-09-27)

`ui-tests.yaml` is deliberately path-filtered — a docs-only edit once triggered a full ten-viewport render and a screenshot commit that rejected an in-flight PR twice (#115) — so it simply does not start for most PRs. `release.yaml`'s push-triggered jobs and `ui-tests.yaml`'s push-triggered job fire from the same `push: branches: [main]` event but are separate workflow files with no dependency between them, so nothing stops the former from finishing first.

**Alternatives, copied from the RFC at decision time.**

| Option | What changes | Cost | Risk |
|---|---|---|---|
| A. Fold the render jobs into `ci.yaml`; gate `release.yaml` on `workflow_run: ["CI"]` alone | The UI-gate jobs move into the always-running `ci.yaml`, gated by an internal relevance check instead of a trigger-level filter | Restructures two files; simplest possible final gate | Breaks `refresh-screenshots.yaml`'s `workflow_run: workflows: ["UI Tests"]` hook outright — that named workflow stops existing — and still needs the same artifact-existence guard Option B needs anyway. Raises the blast radius of `ci.yaml`, which gates every PR merge |
| **B. Make `ui-tests.yaml` trigger unconditionally with an internal skip job (mirroring `release-gate`); gate `release.yaml` on both `CI` and `UI Tests` (ADOPTED)** | `ui-tests.yaml` keeps its name and file; a cheap first job decides relevance and the render jobs run or no-op behind it; `release.yaml` waits for both named checks; `refresh-screenshots.yaml` gains one skip-guard | Three files touched, each by a small, precedented change; makes the UI gate eligible to become a required PR check | Moderate: the "skipped counts as fine" logic must agree across three places — needs a live test both with and without a dashboard change |
| C. Leave `ci.yaml`/`ui-tests.yaml` untouched; poll actual check-runs in `release.yaml` with a grace period, inferring relevance from what shows up | No trigger/path-filter changes anywhere | Smallest diff; zero risk to the existing path-filter tuning | Timing-dependent by construction — distinguishing "not relevant" from "queued but not registered yet" is a guessed grace period, wrong in either direction under a backed-up runner queue, in the one place (releases) where that is hardest to notice |

The RFC recommended B over A because A's extra blast radius (touching the always-running gate for every merge) buys nothing A doesn't already need to spend on the same screenshot-drift guard; and over C because C trades a deterministic answer for a timing heuristic in the highest-stakes place to be wrong. The owner adopted B.

## Consequences

**Accepted.**
- `ui-tests.yaml` now runs (a fast no-op) on every PR and push, not only dashboard-touching ones — slightly more workflow-minutes spent, in exchange for a check that can actually be required.
- `refresh-screenshots.yaml` fires on every PR too, needing its own render-skipped guard, which did not exist before because the workflow could previously assume it was only ever invoked when a render had actually happened.
- Releases take marginally longer wall-clock time on dashboard-touching pushes, waiting for the full render legs (~13-15 minutes) before the image is retagged from main — the image itself was already published from the PR build before merge, so this only delays the tag/attestation/release, not availability of a working image.

**Watch.**
- The three "skipped counts as fine" checks (the internal relevance job, the release wait step's allowed-conclusions, and the screenshot-drift guard) are three independent places encoding the same idea; a future change to one without the others is the kind of drift this ADR exists to prevent, and nothing currently checks that they agree beyond this document and the live test PRs in row 5.

## Verification (2026-09-27)

Four real PRs, three of them throwaway and closed without merging:
- **#189** (real, merged `84c8671`): touches `ui-tests.yaml` itself, so `changes` correctly reported `relevant: true`. Both render legs completed and passed (stable 14m23s, minimum 12m41s, zero findings). `Parity with r5` failed once, honestly, on an early push before the new drift was listed — fixed forward in the same PR by adding `pending` entries naming ADR 0005 and r5 as the port target, not by weakening the check.
- **#190** (throwaway, closed): a comment-only change to `main.py`. `changes` reported `relevant: false`; `Dashboard responsive render` and `Wait for CI + UI Tests` both reported `skipping` (which GitHub treats as a passing conclusion) in seconds, not the ~14 minutes a real render costs.
- **#191** (throwaway, closed, opened after #189 merged): re-ran the same no-op change to specifically test `refresh-screenshots.yaml`'s new guard, since that workflow is `workflow_run`-triggered and therefore always runs the copy of itself on `main`, not on the PR branch — the first attempt (bundled into #190's testing, before #189 merged) could not have exercised the fix at all, which is itself a useful, generalisable fact about `workflow_run` recorded here rather than only in a commit message. With the fix live on main, `Screenshot drift` logged "No dashboard-screenshots artifact on this run ... Nothing to check for drift" and skipped cleanly.

What this does NOT establish: row 2's actual wait-then-gate behaviour for a real version bump, since `validated` has only been observed taking its no-op (skipped) path so far. The next PR that bumps `alpine_a290/config.yaml`'s version is the real test; record its result here.

## References

- RFC 0015: release publishing must wait for the UI gate — https://claude.ai/artifact/F5katxNE46yugU8HH5GDv7
- `release.yaml`'s `release-gate` job (the precedent this ADR reuses)
- #115 (the incident that produced `ui-tests.yaml`'s original path filter)
