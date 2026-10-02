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
| 1 | `docs_sync_check.py`: a PR that moves `version` must change only `config.yaml` and `CHANGELOG.md`, move no other config line, go up, and differ in the changelog only by renaming `## Unreleased`; not waivable by `docs-sync-ok` | a290 | Done | This PR. 41 self-test cases plus 8 end-to-end scenarios that run the real script on real branches of a throwaway git repo and assert the exit code CI acts on: a genuine release passes; an ordinary PR adding an Unreleased entry passes; a feature PR that moves the version fails; so do a version moved with a trailing comment, under a quoted key, under a YAML tag, as a non-`X.Y.Z` string, and without the changelog rename. Mutation-tested: deleting the check from `main()`, deleting the unreadable-config refusal, or deleting the extra-file test each makes the self-test fail |
| 2 | `scripts/prepare_release.py` and `just release <version>`: make that edit, refuse on an empty Unreleased, a version that does not go up, or a dirty changelog or config, print the entries so the number is chosen by reading them | a290 | Done | This PR. 11 unit cases plus an end-to-end test that runs the script in a throwaway repo, for LF and CRLF files: `--dry-run` writes nothing; a real run writes exactly the release edit; the guard accepts what it wrote; four refusals (version not going up, not `X.Y.Z`, nothing under Unreleased, uncommitted changes) exit 1 and leave the files untouched. Mutation-tested: removing either write call fails the self-test. Its first run caught a real bug: the heading pattern's trailing `\s*` under `re.M` swallowed the newlines below the heading, so the rename dropped the blank line. The guard and the script shared the bug and so agreed with each other, which a round-trip test cannot see; a byte-for-byte case now pins it, and fails on the old pattern |
| 3 | `CLAUDE.md`: replace "any user-facing change bumps the version" with this rule | a290 | Done | This PR |
| 4 | First batched release: the renault-api 0.5.14 and pyjwt 2.15.1 bumps land with entries under `## Unreleased` and no version change; I cut them as one release when I ask. That release is also the first real version bump since ADR 0005 row 2, so it closes that row's unverified wait-then-gate gap | a290 | Open | |
| 5 | r5 takes the same check, script, recipe and rule, after a290 merges this and never before | r5 | Open | A session rooted in r5; `docs_sync_check.py` is shared verbatim, so it changes in both |
| 6 | Index the RFC in claude-config's `docs/rfc/README.md` | claude-config | Open | A session rooted in claude-config |
| 7 | A reminder when an `Unreleased` entry has waited a long time, so a security fix does not sit forgotten | a290 | Dropped | Not built now. I would add it if an Unreleased security entry ever waits longer than I meant it to |
| 8 | `release.yaml`: only a PR or push that actually moves the version may publish an image. Today `is_new` only asks whether the tag `v<version>` is absent, so if a release merges and the post-merge gate (`validated`) then fails, the tag is never created and every later same-repo PR publishes its own unmerged build to `<image>:<version>`, the tag users pull. Before this ADR the next bump closed that window; now it stays open until the next requested release. Fix: a `version_changed` output (PR base against head, push parent against head) required for the push, manifest and tag paths | a290 | Open, needs my approval | Found by adversarial review; the mechanism is visible in `release.yaml` (`newcheck`, the `push:` line of `build`, the `if:` of `manifest`) but has not been run on Actions or GHCR. Adding it changes the plan, so it waits for me |

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
- Until row 8 lands, a failed post-merge gate on a release leaves a window in which an ordinary PR can overwrite the released image tag. It needs a release to merge and then fail its gate; I accept it only until row 8 is decided.

**Not decided here.** Whether r5 gets a release at the same moment as a290 or on its own; it follows a290 and is never ahead (row 5).

## Verification

What was checked, and what it could have returned instead.

- The release-shape check fails when it should: eight release-shape self-test cases each return a problem for one specific break (extra file changed, version down, same number spelled differently, another config line moved, nothing under Unreleased, an empty Unreleased, entry text changed while renaming, renamed to a different version), and three return none: the real release, a PR that does not move the version, and 1.10.0 over 1.9.0.
- The behaviours are exercised through the real script and `main()` on real branches of a throwaway repo, inside the self-test, so they are repeatable from the repo and not only from a scratch run I did once.
- The byte-for-byte case was shown to fail on the original heading pattern and pass on the corrected one; a test that passes on both would not have caught the bug.
- `just release 99.0.0 --dry-run` against the real tree refuses ("nothing to release"), which is correct while `main` has no Unreleased entries.

**Adversarial review** (two providers, on this head). It found, and I fixed in this PR: the guard read the version with a line regex, so a trailing comment, a quoted key or a YAML tag on the `version:` line hid a bump from it (high; the guard now requires exactly one plain `version: "X.Y.Z"` line and refuses anything else); the self-test could not see the check removed from `main()` (medium; the end-to-end scenarios now can, shown by mutation); the script's write path was untested (low; same); `just release` could write a version the guard cannot read, and `version_key` ranked `1.28.12-rc1` above `1.28.12` (low; both closed by requiring `X.Y.Z`); CRLF files blocked a release (low; the patterns no longer consume the line ending). It refuted one claim (a half-applied release on a failed write: the script refuses unless both files are clean in git, so nothing is lost) and showed a duplicate-`version:` bypass is stopped by the required yamllint check rather than by the guard. It also found row 8, which I have not fixed.

What this does not establish: that the first real release after this merges goes through `release.yaml` exactly as before. The PR that moves the version has the same shape as one did before this change, so I expect it to; row 4 is where that is confirmed. Nor that `yq`, which the Home Assistant info helper uses to read `version`, agrees with the guard on every spelling: the review used PyYAML in its place. The guard refuses every spelling but one, which is what makes that gap harmless, not a test against `yq`. Row 8 is likewise unrun: it was read from the workflow, not exercised.
