# ADR 0006 — Releases are batched and cut on request

- **Repo:** a290-ha-addon
- **Status:** Accepted (2026-10-03). I chose Option A of the RFC on the day it was raised.
- **Context:** `release.yaml` publishes a release for any merge that moves `version` in `config.yaml`, and the rule in `CLAUDE.md` was that every user-facing change moves it. `gh release list` shows v1.28.0 to v1.28.11 between 2026-09-25 and 2026-09-27, twelve releases in three days. I do not want a release per merge; I want releases batched until I ask for one. Proposed as [RFC: a290-ha-addon, batch releases until one is requested](https://claude.ai/artifact/PA549BZ4tSBdwGsbtbxDj6), which is indexed in claude-config's `docs/rfc/README.md` since a290 has no RFC index of its own yet.
- **North star:** a release happens when I ask for one, and in no other way: no merge can publish a version, and the version on `main` always has its image.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

Ordinary PRs stop moving `version`. A user-facing change adds its entry under `## Unreleased` in `CHANGELOG.md` and leaves `config.yaml` alone. A release is its own PR, opened only when I ask: it renames `## Unreleased` to `## <version>`, moves `version` to match, and changes nothing else. `release.yaml` is untouched: it already builds the image on the PR that moves the version, blocks the merge until the image is pullable, and tags after CI and the UI gate (ADR 0005), so a version on `main` still never lacks its image.

Two scripts make this mechanical rather than a habit. `scripts/prepare_release.py` (`just release <version>`) makes the release edit. `scripts/docs_sync_check.py` gains a check, which no label waives, that a PR moving `version` is exactly that edit. Both call the same function to build the changelog, so the script can only write what the check accepts.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `docs_sync_check.py`: a PR that moves `version` must change only `config.yaml` and `CHANGELOG.md`, move no other config line, go up, and differ in the changelog only by renaming `## Unreleased`; not waivable by `docs-sync-ok` | a290 | Done | This PR. 35 self-test cases, of which 11 are release-shape cases: eight each built from one valid release and broken one way, three that must pass. Run for real against a scratch worktree with the real script and `main()`: a genuine release exits 0; a release that also touches code exits 1; a feature PR that moves the version exits 1; an ordinary PR adding an Unreleased entry exits 0. The scratch run is not repeatable from the repo, only the self-test is |
| 2 | `scripts/prepare_release.py` and `just release <version>`: make that edit, refuse on an empty Unreleased, a version that does not go up, or a dirty changelog or config, print the entries so the number is chosen by reading them | a290 | Done | This PR. 10 self-test cases. Its first self-test run caught a real bug: the heading pattern's trailing `\s*` under `re.M` swallowed the newlines below the heading, so the rename dropped the blank line. The guard and the script shared the bug and so agreed with each other, which a round-trip test cannot see; a byte-for-byte case now pins it, and fails on the old pattern |
| 3 | `CLAUDE.md`: replace "any user-facing change bumps the version" with this rule | a290 | Done | This PR |
| 4 | First batched release: the renault-api 0.5.14 and pyjwt 2.15.1 bumps land with entries under `## Unreleased` and no version change; I cut them as one release when I ask. That release is also the first real version bump since ADR 0005 row 2, so it closes that row's unverified wait-then-gate gap | a290 | Open | |
| 5 | r5 takes the same check, script, recipe and rule, after a290 merges this and never before | r5 | Open | A session rooted in r5; `docs_sync_check.py` is shared verbatim, so it changes in both |
| 6 | Index the RFC in claude-config's `docs/rfc/README.md` | claude-config | Open | A session rooted in claude-config |
| 7 | A reminder when an `Unreleased` entry has waited a long time, so a security fix does not sit forgotten | a290 | Dropped | Not built now. I would add it if an Unreleased security entry ever waits longer than I meant it to |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context (2026-10-03)

A release here is a change to `version`, because the Supervisor resolves the add-on image by it. `release.yaml` therefore builds and pushes the image on the PR that moves the version, before the merge, and a required check (`Release image published`) blocks the merge until the image is pullable. On push to `main`, with the tag new, it republishes, attests provenance, waits for CI and the UI gate, then tags `v<version>` and creates the GitHub Release. Nothing in that sequence is wrong; the fault was upstream of it, in a rule that made every merged change move the version.

`docs_sync_check.py` already enforced the other half of the old rule (a version change needs a changelog heading for that exact version) and already treated `## Unreleased` as a section that is not a release, so entries could accumulate there before this ADR. What was missing was anything stopping the version from moving in an ordinary PR.

Not measured: what a user sees per release, or how many updates they skipped. My saying that releases should wait for a request is the evidence for the need.

**Alternatives, copied from the RFC at decision time.**

| Option | What changes | Cost | Risk |
|---|---|---|---|
| **A. Release PR on request (ADOPTED)** | Ordinary PRs stop bumping `version` and add their changelog entry under `## Unreleased`. A request opens one release PR that bumps the version and renames `Unreleased` to the version. The existing image gate, tag and UI-gate wait run unchanged on that PR | A small script or `just` recipe, one CI check that only a release PR may change `version`, a CLAUDE.md rule change, and an ADR | `main` runs ahead of the released add-on, so README and DOCS can describe behaviour no one has yet. A security fix waits for a request |
| B. Release bot (release-please style) | A bot keeps a release PR open, built from Conventional Commit subjects; merging it is the request | A new bot to pin and trust, and a changelog generated from commit subjects instead of the hand-written entries this repo keeps | Same drift as A, plus the bot's PR is one more thing to reconcile (one bot per ecosystem) |
| C. Keep bumping, withhold only the tag and GitHub Release | Merges still change `version` on `main` | Smallest edit | Does not batch anything: HA reads the version from `main`, so the update is still offered with its image already published. Only the release notes page is delayed. Rejected |

The RFC recommended A over B because B adds a bot and replaces entries I write with ones generated from commit subjects, for no gain A does not already give; and over C because C changes nothing a user can see. Status quo was the behaviour this ADR exists to change.

**The RFC's two open questions, and how I took them.** I accepted that docs run ahead of releases and did not add a rule to hold doc edits for the release PR: the drift is real, but a rule to manage it would be a process I would have to remember, and I have no evidence yet that it misleads anyone. I left the stale-Unreleased reminder out (row 7). Both are defaults taken to keep this change small, and either can be reversed by amendment.

## Consequences

**Accepted.**
- Between releases, `main`'s README and DOCS can describe behaviour that has not shipped.
- A security or dependency fix waits under `## Unreleased` until I ask for a release. `pyjwt` and renault-api are the first two.
- A release now needs a request and a PR of its own, with the image build and gates running on it rather than on whichever feature PR happened to carry the bump.
- The changelog on `main` can carry `## Unreleased` at the top. Whether the Supervisor's Changelog tab then shows that heading to users is not verified; it ships in `CHANGELOG.md` like any other section.
- No label waives the release-shape check. If a release ever has to move another file, I amend this ADR first.

**Not decided here.** Whether r5 gets a release at the same moment as a290 or on its own; it follows a290 and is never ahead (row 5).

## Verification

What was checked, and what it could have returned instead.

- The release-shape check fails when it should: eight release-shape self-test cases each return a problem for one specific break (extra file changed, version down, same number spelled differently, another config line moved, nothing under Unreleased, an empty Unreleased, entry text changed while renaming, renamed to a different version), and three return none: the real release, a PR that does not move the version, and 1.10.0 over 1.9.0.
- The same four behaviours were run through the real script and `main()` in a scratch worktree, not only through the functions: one real release passed and three non-releases were refused with the specific reason.
- The byte-for-byte case was shown to fail on the original heading pattern and pass on the corrected one; a test that passes on both would not have caught the bug.
- `just release 99.0.0 --dry-run` against the real tree refuses ("nothing to release"), which is correct while `main` has no Unreleased entries.

What this does not establish: that the first real release after this merges goes through `release.yaml` exactly as before. The PR that moves the version has the same shape as one did before this change, so I expect it to; row 4 is where that is confirmed.
