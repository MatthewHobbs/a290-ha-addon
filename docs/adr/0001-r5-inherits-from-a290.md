# ADR 0001: How r5 inherits from a290

- **Status:** Accepted (2026-09-26)
- **Context:** Every change is ported between `a290-ha-addon` and `r5-ha-addon` by hand, from one
  session to another by message. A read-only parity baseline on 2026-09-26 (a290 `bddda7f`,
  v1.28.1; r5 `6957d57`, v1.8.3) found 45 of about 85 shared files differing, 12 fixes r5 had
  that a290 lacked, 19 the other way, and 32 same-intent items that had diverged. Put to the owner
  as "RFC: How r5 inherits from a290" (cited below).
- **North star:** the shared runtime lives once, in the `renault-mqtt` core, with one set of
  tests covering both cars; both cars' entities carry the same descriptive names; each add-on
  holds only its catalog data, config, dashboards and docs, and nothing the two share drifts
  unnoticed.

*Format per `claude-config`'s `docs/adr/0000-template.md`, referenced rather than copied: the
North star and the Decision table are THE PLAN and are kept current; Context, Alternatives,
Consequences and Verification are THE RECORD and are dated, corrected but not revised. Accepting
this ADR is the approval to carry out its Open rows in their named repos, each in that repo's own
session and within its standing rules.*

## Decision

Stage A, a mechanical parity check with hand-porting, is the short-term solution. Stage B,
growing the shared `renault-mqtt` core, is the correct long-term solution. In the owner's words
(2026-09-26): "A is the short term solution. The overhead of maintenance of both is too great. B
is the correct solution, with a caveat that the r5 users are using the Topolini65 dashboards, if
they are using the dashboards bundled here surely the point is moot and entities can be
descriptive as with the a290."

Options C (generate r5 from a290) and D (one repository) are not adopted by this decision.

**Naming, decided later the same day, replacing the RFC's "r5 keeps its legacy names".** The
owner's caveat above was investigated read-only. The research reported that no revision of
Topolino65's dashboards uses any r5 entity id, so the compatibility the legacy names were kept
for never existed, and that every visible r5 user runs r5's bundled dashboard. The owner then
decided: r5 moves to descriptive, a290-style entity names, but only after the `renault-mqtt`
core supports (1) `default_entity_id` (RFC 0005, accepted), so new ids do not get area
prefixes, and (2) retiring old button and number discovery topics (it already retires
sensors). r5 then ships **one** migration release. Because `unique_id` is the object id, that
release creates new entities; history stays under the old ids, and the CHANGELOG warns about
both. A stub-HA experiment confirms the registry behaviour first. r5's docs claiming its
entities "bind straight to" Topolino65 are corrected now, and the owner posts a
dashboard-usage survey on r5 before the rename release.

After that release the shim no longer needs the legacy-rename table: the `legacy` rules in
`scripts/parity/map.tsv` and the `legacy` entries in `expected.tsv` stay valid until then and
are expected to shrink to nothing. The check enforces the shrinking: a map rule that rewrites
nothing fails as stale, as an expected entry does.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Stage A in a290: `scripts/parity_check.py`, its normalisation map and expected-differences list under `scripts/parity/`, `just parity`, and the `Parity with r5` CI job | a290-ha-addon | Open | branch `parity-check`; row goes Done with its PR |
| 2 | Stage A in r5: copy `scripts/parity_check.py` byte-for-byte and its own copies of `scripts/parity/map.tsv` and `expected.tsv`; add `just parity` and a CI job that checks out a290's main; then delete the `pending` entry for `scripts/parity_check.py` in both lists | r5-ha-addon | Open | |
| 3 | Decide whether `Parity with r5` becomes a required check, in each repo, once row 2 has landed and the list has held for a week | owner | Open | |
| 4 | Work the `pending` entries down: each is a port or a decision with a named Target. The list is the backlog; an entry is deleted when its difference is gone, and the check fails until it is | both add-on repos | Open | `scripts/parity/expected.tsv` |
| 5 | Decide the RFC's open behaviour differences (MQTT keepalive, re-login policy, hvac-settings, cabin temperature, plug-suspect input, coverage floor); each is a `pending` entry until then | owner | Open | |
| 6 | B1: move the verbatim duplicates into the core, starting with `resolve_account`, `VehicleSession`, `freshness_fields` and the breaker. Exit: both add-ons on the new core, container boot passing | renault-mqtt | Open | |
| 7 | B2: make `poll_once` catalog-driven, so the per-car data keys are catalog data, not code. Exit: `main.py` in each add-on shrinks to configuration | renault-mqtt | Open | |
| 8 | Core: `default_entity_id` in discovery (RFC 0005), so new ids get no area prefix | renault-mqtt | Open | |
| 9 | Core: retire old button and number discovery topics, as it already does for sensors | renault-mqtt | Open | |
| 10 | Correct r5's docs where they say its entities "bind straight to" Topolino65 | r5-ha-addon | Open | |
| 11 | Post a dashboard-usage survey on r5 before the rename release | owner | Open | |
| 12 | Stub-HA experiment: confirm the registry behaviour of the rename (new entities, since `unique_id` is the object id; history stays under the old ids) before anything ships | r5-ha-addon | Blocked | awaits rows 8 and 9 in a core release |
| 13 | r5's single naming migration release: a290-style descriptive names, the CHANGELOG warning about the new entities and the history left under the old ids; then delete the `legacy` rules and entries from both repos' parity map and list | r5-ha-addon | Blocked | awaits rows 8, 9, 11 and 12 |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**; a **Done** row carries Evidence.
Rows 6 to 9 happen in `renault-mqtt`'s own session; adopting each core release in an add-on is a
`CORE_REF` bump there, with its container boot. RFC 0005 lands in the core first.

## Context

The cars differ far less than the trees do. For every endpoint the add-ons call, renault-api
0.5.13's maps for `A5E1AE` and `R5E1VE` are equivalent. The real differences are few: r5's
Topolino65-compatible legacy names (14 renames, 136 references), cabin temperature (the A290
never returns it), hvac-settings (the A290 answers `502000`; R5 behaviour unobserved), and car
renders and branding. Everything else is duplication that drifts.

Already settled by the owner on 2026-09-26 before the RFC's options were weighed: a290 is
upstream and r5 inherits, with model nuances in shims; r5 keeps its legacy names through a
rename table (replaced the same day by the naming decision above); Start Climate keeps
`precondition_temperature` but defaults to the car's own setting; the user docs keep saying
Refresh Location "may 403"; fixes r5 made on its own go into a290 first.

**Alternatives, copied from the RFC at decision time.** This is the authoritative record; the
RFC doc stays editable.

| Option | What changes | Cost | Risk |
|---|---|---|---|
| **A. Parity check, hand-port (ADOPTED, short term)** | Both repos stay; `just parity` normalises names and diffs the trees; a committed expected-differences list says why each allowed difference exists; CI fails on anything unlisted. | Small. | Double work continues; the list needs upkeep. |
| **B. Grow the shared core (ADOPTED, the target)** | Move the shared runtime into renault-mqtt: main loop, VehicleSession, resolve_account, breaker, health server, catalog-driven poll_once, deploy engine, with their tests. Each add-on keeps catalog data (incl. r5's rename table), config, dashboards, docs. | Large. | One core release reaches both add-ons at once; dashboards, docs and CI stay duplicated. |
| C. Generate r5 from a290 (not adopted in this decision) | a290 is the only hand-edited tree; a generator applies the shim map and overlays and opens a PR on r5 per change. | Medium. | A generator bug mis-renames r5 entities; hand edits on r5 must be blocked; a bot token is a new write credential. |
| D. One repository (not adopted in this decision) | Both add-ons in one repo. | Medium to large. | High: r5's install URL changes and existing users stop getting updates until they re-add it. |

The RFC recommended A now, then B for code and C for everything else, and ruled out D. The owner
adopted A and B; C was not taken up, so dashboards, docs, the UI harness and CI stay
hand-ported under A's check for now.

## Consequences

**Accepted.**

- Every change is still made twice until B lands; A only makes a missed port visible.
- The expected list needs upkeep. That is by design: a new difference fails until someone
  writes down why it exists, and a fixed one fails until its entry is deleted, so the list
  cannot silently outgrow or outlive the drift.
- The check reads the twin's main as it is at run time, so it is not hermetic: a merge in one
  repo can turn the other's check red with no change there, usually a stale entry to delete. Each
  repo therefore keeps its own copy of the map and list, so each can be made green by its own PR.
- Five places are excused wholesale (count `*`) and printed as such on every run: the tests, the
  CHANGELOG, `DOCS.md`, the root `README.md` and `CLAUDE.md`. Drift inside them is not caught.

**Watch.**

- The list becoming a place to bless drift. A raised count or a new entry is a review item: the
  reason must say why, and a `pending` entry must name its Target.
- Over-normalisation. A map rule hides every difference it rewrites, in every file, uncounted.
  New rules belong there only when the two names mean the same thing everywhere.

## Verification (2026-09-26)

See the PR that lands row 1 for the commands and output; in summary:

- Against a290 (this branch) and r5 `origin/main` the check passes with the initial list.
- It fails, naming the file and line, when a shared function body in a290's `main.py` is changed
  in a scratch copy, and when an unlisted line is added to a dashboard.
- It fails when an entry's difference no longer exists (stale), so the list cannot rot.
- It does not pass by over-normalising: removing the `MQTT_KEEPALIVE` entry makes the known 60
  vs 30 difference fail.
- Its `--self-test` proves each failure mode fires on synthetic trees, including a verbatim file
  whose difference normalisation would otherwise hide.
- It fails when a map rule rewrites nothing (stale), so the `legacy` rules cannot outlive r5's
  rename unnoticed.
- **Not re-verified here:** the naming research (Topolino65 dashboards use no r5 entity id;
  visible r5 users run the bundled dashboard) reached this ADR's author through the
  coordinating session; it was not re-checked for this ADR. The r5 session later confirmed the
  0-of-58 result independently on its own Topolino65 snapshot.
- **Not verified:** the CI job itself, which runs only once the PR is open; and that the audit's
  item ids resolve anywhere a future reader can follow (the baseline report was a session
  artefact, summarised in the RFC).

## References

- RFC: How r5 inherits from a290, unnumbered, to be listed in `claude-config`'s RFC index
  ([doc](https://claude.ai/artifact/91apR2Cw2v6LbPwPtnvJRu)).
- `scripts/parity_check.py`, `scripts/parity/map.tsv`, `scripts/parity/expected.tsv`.
- RFC 0005, pinning MQTT entity ids with `default_entity_id` (claude-config index).
