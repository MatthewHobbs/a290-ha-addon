# ADR 0003 — Wait for every pop-up's own cards before scanning it

- **Status:** Accepted (2026-09-26). I chose Option B of the RFC on the day it was raised; the rows below are the plan and are all Open.
- **Context:** #171 made the UI gate open every Bubble pop-up on every device and scan it for truncation. Its review found that the scan can finish before a pop-up's lazily rendered cards have painted, and that only a pop-up which declares expected labels waits for anything first. Most pop-ups declare none. Proposed as [Plan and RFC — renault-mqtt v0.19.0 and UI-gate completeness](https://claude.ai/artifact/73GYxp6ZAj1mB25D1gCCkh), RFC section; logged in `BACKLOG.md` as "UI gate: a pop-up with no declared label has no completeness signal before its scan".
- **North star:** the gate scans a pop-up only once every card the dashboard file gives it is on screen, so a truncation in a late-painting card cannot pass unseen, and a card that never paints fails by name.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

The seed manifest declares, for every pop-up, the static text of every card inside it, and `check_overflow.py` waits for that whole set to be laid out inside the open pop-up before it runs the truncation scan. A pop-up whose set never appears fails that device by name.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `seed.py`: a static-text collector beside `expected_labels`. For each pop-up (walked as `popups_in` walks, with the pop-up's hash as context) it takes every string under a `TEXT_KEYS` key that holds no Jinja (`{{` or `{%`), skipping `ACTION_KEYS` subtrees and `type: conditional` subtrees, whose names stay pass-driven because they only render when their conditions hold. Icon-only cards contribute nothing | a290 | Open | |
| 2 | `write_manifest`: every opened pop-up's `labels` is the union of its static set (row 1) and its pass-driven labels, unique and in order. A pop-up whose static set is empty is an error at manifest time, like `MIN_POPUPS`: every bundled pop-up has at least a separator heading, so an empty set means the walk missed it, not that there is nothing to wait for | a290 | Open | |
| 3 | `check_overflow.py`: the completeness wait requires each static label to be **laid out inside the open pop-up** (non-zero box, `visibility: visible`, inside the element `JS_POPUP_SHOWS` finds by hash), not in the viewport. Pop-ups scroll, so a card below the fold is rendered and must count; `JS_POPUP_SHOWS`'s in-viewport test stays for the header, which is what proves the pop-up open | a290 | Open | |
| 4 | One wait per pop-up, not one per label: a single `wait_for_function` over the whole set that returns the labels still missing, with one timeout. Serial 5 s timeouts per label would turn a genuinely broken pop-up into minutes. The ones still missing at the timeout become `not-rendered` findings, which already fail the device | a290 | Open | |
| 5 | Order inside `_capture_popup` stays as #171 left it: completeness wait, then `_stable_issues`, then the still-open re-check | a290 | Open | |
| 6 | Proof, isolated (a static page and Playwright, no HA): a card with only a static `name`, no pass label, appearing 3 s after the pop-up header. Must be missed by the tree before rows 1 to 4 and caught after them, the same fixture and timing that proved #171 round 4 | a290 | Open | |
| 7 | Proof, on the real gate: one static label hidden by injected CSS on a copy of the tree must produce `not-rendered` for that pop-up on every device; the unmodified tree must pass both legs (stable and the declared minimum) on the bundled dashboards | a290 | Open | |
| 8 | Runtime measured against #171's baseline of about 11 min per leg, and recorded here. The wait resolves at once when the cards are already there, so the expected cost is near zero on a healthy pop-up and one timeout on a broken one | a290 | Open | |
| 9 | `ui-tests/README.md` describes the completeness wait; the `BACKLOG.md` entry is closed with a pointer to this ADR; the parity list is recounted | a290 | Open | |
| 10 | Dual review, then merge under the repo's normal gates. This repo is Public, so both providers review | a290 | Open | |
| 11 | r5-ha-addon ports rows 1 to 5 from the merged SHA; its parity entries for `seed.py`, `check_overflow.py` and the README come out here when it lands | r5 | Open | |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context (2026-09-26)

`_stable_issues` polls the truncation scan across a settle window and exits early on two consecutive clean scans, which can be one to two seconds in. That is right for the card-mod race it was built for, and wrong for a card that has not painted yet: nothing is truncated in a card that is not there. #171 round 4 put the wait on a pop-up's declared labels ahead of the scan, and proved with an isolated fixture that a labelled card appearing three seconds after the header is missed under the old order and caught under the new one. The manifest's labels come from `expected_labels`, which collects only what a pass changes: a conditional card's name when its conditions hold, and the selected branch of an `IF_IS_STATE` template. On the normal pass most pop-ups therefore declare no labels at all, and for those the scan still starts whenever `_stable_issues` feels settled.

A general fix without a manifest was tried first and proven not to work. Polling the open pop-up's descendant count until it stopped changing, in the pattern of `_wait_card_mod`, is satisfied at once by a pop-up that has rendered nothing yet: a count of zero that has not changed in two polls looks the same as a count that finished at zero. The same fixture showed content appearing at three seconds still missed, with the wait returning after 2.34 s. It was reverted rather than shipped. There is no signal of "finished rendering" that does not come from knowing what to expect, and the dashboard file is where that knowledge already is.

What was and was not established that day: every full gate run, on both Home Assistant legs, on the dashboards before and after #170, rendered all ten pop-ups' real cards and found no instance of this. The risk is architectural, not observed. I chose to close it anyway rather than wait for a screenshot to show a half-painted pop-up, because the gate's claim is that every pop-up was scanned, and a claim the gate cannot fail on is not evidence.

## Alternatives

Copied from the RFC, with the chosen option marked.

| Option | What | Cost | Risk |
| --- | --- | --- | --- |
| A — Accept, revisit on evidence | Keep the `BACKLOG.md` P2 entry as the record; no code change. Revisit if a pop-up's content is ever seen partially rendered in a committed screenshot. | None now. | The gap persists for label-less pop-ups until observed. No instance in any run on 2026-09-26. |
| **B — Manifest-derived completeness set (chosen)** | Extend `seed.py`'s manifest generation to declare, per pop-up, the static `name`/`title`/`heading` of every card it contains (not just pass-driven state text), and wait on that full set before scanning, reusing the already-proven `_missing_labels` wait from #171 round 4 with a bigger, unconditional label set. | Moderate: new manifest-walking logic in `seed.py` (a superset of what `expected_labels` and `popups_in` already do), a larger label list per pop-up (one merged wait rather than serial 5 s timeouts), and it has to handle cards with no meaningful static text without false-waiting. | The correctly shaped fix, and the one the adversarial review itself named. Touches `seed.py`'s dashboard-walking logic, which the parity map's audit history (H-C4, H-C5) shows has produced real bugs before. Needs its own proof before shipping, to the same standard as #171's rounds 1 to 4. |
| C — Fixed minimum settle time | Add a flat `page.wait_for_timeout(N)` (1.5 to 2 s) inside `_capture_popup` before every scan, regardless of labels. | Very low: one line. | The kind of fix `_wait_card_mod`'s own docstring warns against: a magic number that is wrong under load or on a slower runner, and a permanent wall-clock cost on every one of the ~100 pop-up × device combinations in an already ~11 min gate, for a benefit that is currently unobserved. |
| D — Retry the stability heuristic, corrected | Require growth-then-quiet (compare the count after `_open_popup`'s own settle against the count after the quiet window) or a minimum poll count before trusting quiet. | Moderate: more engineering on an idea already shown to have a design flaw; needs a fresh adversarial proof before it could be trusted. | Uncertain payoff: a corrected heuristic could still be wrong for a legitimately fast, low-card pop-up, or add latency without closing the gap. Not attempted beyond the failed first version. |

## Consequences

**Accepted**

- The manifest grows: every pop-up carries every static card text, so a change to a card's `name` in the dashboard file changes what the gate waits for. That is the point; the gate reads the same file a regression would edit, as `write_manifest`'s own comment already says of the pass-driven labels.
- A broken pop-up costs one label timeout per device before it fails, instead of failing on the first scan. A healthy pop-up costs the time for its cards to be laid out, which they mostly already are by the time `_open_popup` returns.
- A Bubble release that changes how a card's `name` reaches the DOM (a different element, a transformed text node) will show up as `not-rendered` across every pop-up. That is a gate failure with a cause the log names, not a silent pass, and it is what the render-input pins and Renovate's grouped bump exist to catch before the pin moves.

**Watch**

- Row 3's "laid out" test must not drift back to "in viewport". A pop-up taller than the phone's screen is the normal case, not the exception.
- Row 2's empty-set error assumes every pop-up has at least one static text. If a future pop-up is genuinely icon-only, that row's rule is what changes, deliberately, not the check.

## Verification

Nothing verified yet. Rows 6 to 8 carry the evidence as they land, and this section is written then.

## References

- RFC: [Plan and RFC — renault-mqtt v0.19.0 and UI-gate completeness](https://claude.ai/artifact/73GYxp6ZAj1mB25D1gCCkh), RFC section. Decided 2026-09-26, Option B.
- #171 `test(ui-tests): render the Peak-rate badge and every Bubble pop-up`, merged as e0f7f5a: review rounds 4 to 6 in its comments, including the isolated fixture and the disproven heuristic.
- `BACKLOG.md`, "UI gate: a pop-up with no declared label has no completeness signal before its scan" (P2, 2026-09-26).
- `ui-tests/README.md` steps 4 and 5, and `ui-tests/seed.py` `expected_labels` / `write_manifest`, as they stand at e0f7f5a.
