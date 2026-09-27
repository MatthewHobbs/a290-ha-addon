# ADR 0005 — Release publishing waits for the UI gate

- **Status:** Accepted (2026-09-27). I chose Option B of the RFC on the day it was raised.
- **Context:** on `v1.28.11`, the release/tag/image-publish pipeline (`release.yaml`) tagged and published within 2 minutes of the merge while the same push's UI-gate render (`ui-tests.yaml`) was still running for another 11 minutes on that commit. Separately, the UI gate is not a required PR check at all. Proposed as [RFC: release publishing must wait for the UI gate](https://claude.ai/artifact/F5katxNE46yugU8HH5GDv7); indexed in claude-config pending a number, since a290 has no RFC index of its own yet.
- **North star:** a release cannot publish, and a PR cannot merge, ahead of a UI-gate verdict on that exact commit — whether or not the UI gate was even relevant to what changed.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

`ui-tests.yaml` reports a verdict on every push and every PR, applicable or not, using the same `if: always()` pattern `release.yaml`'s own `release-gate` job already proves out. `release.yaml`'s push-triggered build/manifest/tag sequence waits for that verdict (and `CI`'s) before doing anything. The UI gate then becomes safe to add as a required PR check for the first time.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `ui-tests.yaml`: remove the trigger-level `paths:` filter (both `push` and `pull_request`); add a first job that decides relevance with the same path patterns, moved from the trigger into an in-workflow check, and gate the render jobs on it (`needs` + `if`); the workflow's overall result must be `success` whether relevant or not | a290 | Open | |
| 2 | `release.yaml`: the push path's `build`/`manifest`/`tag` jobs wait for both `CI` and `UI Tests` to report success on `github.sha` before proceeding, using a maintained wait-for-check action rather than a hand-rolled poll | a290 | Open | |
| 3 | `refresh-screenshots.yaml`: add a guard so a render that was itself skipped (not failed) does not try to download a `dashboard-screenshots` artifact that was never produced | a290 | Open | |
| 4 | Add `Dashboard responsive render (mobile matrix, stable)` and `(minimum)` to `main`'s required status checks, once rows 1-3 have held on a live test PR | a290 | Open | |
| 5 | Verify live against two real PRs before calling this Done: one that touches a dashboard (must still fully render and gate), one that touches neither dashboards nor `ui-tests/**` (must report green fast, with no render attempted, and must not stall the release path) | a290 | Open | |

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

Not yet run — rows 1-5 are Open. Row 5 names the two real-PR tests (dashboard-touching, and neither-dashboards-nor-ui-tests) that must both pass before any row above it is called Done.

## References

- RFC: release publishing must wait for the UI gate — https://claude.ai/artifact/F5katxNE46yugU8HH5GDv7
- `release.yaml`'s `release-gate` job (the precedent this ADR reuses)
- #115 (the incident that produced `ui-tests.yaml`'s original path filter)
