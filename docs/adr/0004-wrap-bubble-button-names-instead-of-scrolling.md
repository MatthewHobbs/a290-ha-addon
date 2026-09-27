# ADR 0004 — Wrap Bubble button names instead of scrolling them

- **Status:** Accepted (2026-09-27). I chose Option A of the RFC, with the Activity dates handled as Option B; the rows below are the plan and are all Open.
- **Context:** the r5 twin, rendering its mirror of the Bubble dashboard through the all-pop-ups harness (#171's port), found labels cut short with a fade that the gate reported as 0 issues. It measured the mechanism: Bubble Card's scrolling-text marquee. Proposed as [RFC 0012: catch marquee-clipped labels in the UI gate](https://claude.ai/artifact/C3QQ5vbRHDrdGsWuvazmv3), indexed in claude-config; logged in `BACKLOG.md` as "UI gate misses text cut short with a fade".
- **Amended (2026-09-27):** Option A cannot fit the four rows that hold three buttons; they are re-laid two per row, decided in RFC 0013 (see Amendment below).
- **North star:** no label on either dashboard is clipped at any phone width, for anyone, including users with reduced motion, and the gate can see a clip wherever Bubble hides one.

<!-- Format: claude-config docs/adr/0000-template.md, referenced at source rather than copied.
The Decision table is the plan and is kept current; the rest is the dated record. -->

## Decision

The gate's detector measures a clipping box by its text content when it owns no text node, so Bubble's `.scrolling-container` is seen. The affected Bubble buttons stop scrolling and wrap their names, the way #170 made the separators wrap; the two Activity date buttons show a short date instead. The detector and the dashboard change land in one PR, a290 first, so trunk's gate is never red.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `check_overflow.py` `JS_DETECT`: a box that clips (`text-overflow: ellipsis`, or `overflow-x: hidden` with nowrap) but owns no text node is measured by its `textContent`. The r5 twin's diff, verbatim. **Amended:** also a vertical check, since Bubble clamps a wrapped name to two lines with an ellipsis: a box under a line clamp whose scrollHeight exceeds its clientHeight is a clip; must-fail on a clamped name, must-pass on a fitting one | a290 | Open | |
| 2 | Must-fail, on a290's own render before any dashboard change: the patched detector on the bundled Bubble dashboard at 360px reports the marquee clips by name (the r5 twin's four named cases and the Last Charge set are the expectation); the stock detector reports 0 on the same pages | a290 | Open | |
| 3 | The affected Bubble buttons (`#alpine-lastcharge`, `#alpine-diag`, `#alpine-presets`, the main menu's Smart Charging button, and any the must-fail names) get `scrolling_effect: false` and a wrap on the name (`white-space: normal`), in `front-end-bubble.txt` and in any deploy-time card `deploy.py` generates for them. **Amended:** the four rows that hold three buttons (Last Charge's Started / Ended / Duration, SoC Start / End / Gain, Energy Start / End / Added; Diagnostics' Last Updated / Run Test Charge / Refresh Location) are re-laid two per row in dashboard order (Last Charge's nine become five rows, Diagnostics' three become two), because a three-button row leaves 29 to 40px for a name and a single word does not fit (RFC 0013) | a290 | Open | |
| 4 | The two `#alpine-activity` date buttons (`hvac_last_activity`, `last_updated`) show a short date, day, month and time, instead of Home Assistant's long locale string, which is 369px of text for a 93 to 120px name area and would wrap to three lines | a290 | Open | |
| 5 | Full gate green on both legs (stable and the declared minimum) with the patched detector, every pop-up, every device; the number of marquee findings goes from the must-fail's count to 0 by name. Version bump and changelog in user terms | a290 | Open | |
| 6 | A wrapped button name's height is measured at 360px for the busiest rows (Last Charge's five, Diagnostics' three) and recorded here, so a later redesign can see what this cost | a290 | Open | |
| 7 | `ui-tests/README.md` says what `JS_DETECT` now measures and why; `BACKLOG.md`'s entry closes with a pointer here; parity recounted | a290 | Open | |
| 8 | Dual review, then merge under the repo's normal gates | a290 | Open | |
| 9 | r5-ha-addon mirrors the pair from the merged SHA; its own prepared patch files are the starting point | r5 | Open | |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context (2026-09-27)

The r5 session measured, on its mirror at 360px (HA 2026.9.3, Bubble 3.4.1, reduced motion as the gate runs): a Bubble button's name renders as `div.bubble-name > div.scrolling-container > span`, the span holding the text twice for a marquee. The span reports no overflow (scrollWidth equals clientWidth on all four cases), so the detector, which measures the element that owns the text, finds nothing. The box that clips is `.scrolling-container`, `overflow: hidden` with a mask that draws the fade, scrollWidth far above clientWidth (212 in 85; 189, 227 and 233 in 29); the detector skips it because it owns no text node. With reduced motion the marquee is frozen, so what the gate sees is what such users see: a clipped first frame.

The one-line detector fix, applied on r5's full gate, found 148 real clips and no false positives: Last Charge 85, Activity 40, Diagnostics 3, Presets 4, the main menu 12; the standard dashboard 0. None of that has been measured on a290's render yet; the cards and CSS are the same, and row 2 is where a290's own numbers come from.

The dashboards' stated rule is wrap, never clip (#170 applied it to the separators). A marquee is a clip for anyone who cannot see it scroll. I chose to make the buttons wrap rather than exempt the marquee from the gate, and to shorten the two dates rather than let them wrap to three lines.

## Alternatives

Copied from RFC 0012, with the chosen options marked. The detector fix was not in question; the choice was what the dashboards do about what it reveals.

| Option | What | Cost | Risk |
| --- | --- | --- | --- |
| **A: switch the marquee off and wrap (chosen)** | Set Bubble's `scrolling_effect: false` on the affected button cards and let `.bubble-name` wrap (`white-space: normal`), the same treatment #170 gave the separators; land the detector fix in the same PR so the gate goes red and green in one step. | One dashboard PR per repo, a290 first: the ~10 pop-up buttons the findings name (Last Charge, Activity, Diagnostics, Presets, the Smart Charging menu button), a version bump, a gate run on both legs. Labels that wrap may push rows taller; the gate proves nothing overflows. | The dashboards' own rule, applied consistently. A Bubble button whose name wraps to two lines is a visible change users will notice, and the two Activity dates (369px in 93 to 120px) may need a shorter format rather than a three-line wrap. |
| **B: shorter names and one-per-row layouts (chosen for the Activity dates)** | Keep the marquee where it fits; rename or re-lay out the cards that do not (Last Charge's row of five, Diagnostics' three-in-a-row). | Design work per pop-up, then the same PR and gate run as A. | More judgement per card, and the marquee stays for any future label that grows; the detector will catch each one as it appears, which is the point, but each becomes a dashboard change. |
| C: accept the marquee, exempt it from the gate | Treat scrolling text as intended UX, teach `JS_DETECT` to ignore `.scrolling-container`, and leave the labels as they are. | A line in the detector and a sentence in the README. | Contradicts the dashboards' stated rule (wrap, never clip) and leaves reduced-motion users with clipped labels, since for them the marquee never scrolls. A gate exemption is exactly the kind of blind spot #171 removed elsewhere. |
| D: land the detector alone, leave the gate red until the labels are fixed | Merge the one-line fix now; fix the dashboards after. | Nothing extra. | A red trunk is stop-the-line under the merge invariant; every PR in between inherits a red gate and the signal is lost. Not viable. |

## Consequences

**Accepted**

- Buttons whose names wrap grow taller on narrow phones; row 6 records by how much. That is the visible price of never clipping.
- The detector now measures any clipping box by its text content. A future card that clips deliberately (a badge, a ticker) will be flagged; the answer is to change the card or, if the design really wants a clip, to say so per card in the manifest, never a blanket exemption.
- The Activity dates lose Home Assistant's locale formatting in favour of a fixed short form.

**Watch**

- A Bubble release that changes the marquee's DOM (a different container, text no longer doubled) changes what the detector sees; the pinned render inputs and Renovate's grouped bump are where that surfaces.
- `scrolling_effect: false` is a per-card Bubble option; a new button added without it scrolls again and is caught by the gate, not by review.

## Amendment (2026-09-27)

The r5 twin measured the first cut (scrolling off on the 24 buttons) live at 360px and 393px: Presets, the Smart Charging button and Activity clear the gate, and two-per-row buttons have 85px of name width, where every name fits; but a row of three buttons leaves 29px (360) or 40px (393) beside the icon, and a single word is 36 to 51px (Started 43, Duration 50, Refresh Location 51). Option A as decided cannot fix those four rows. Proposed as [RFC 0013: three-button rows at 360px in Last Charge and Diagnostics](https://claude.ai/artifact/PmjD9CHbqoiKKiqwjUGGom); I chose A2, two per row, on 2026-09-27. Row 3 carries the change. The options weighed, copied from RFC 0013:

| Option | What | Cost | Risk |
| --- | --- | --- | --- |
| **A2: two per row (chosen)** | Re-lay the four three-button rows as two-per-row (Last Charge rows 1 to 3 become five rows; Diagnostics' row becomes two), keeping every button, icon and name. Measured width for two-per-row is 85px, where every current name fits. | Last Charge grows from 5 button rows to 7, Diagnostics from 1 to 2; a taller pop-up the user scrolls. One dashboard edit per repo, proven by the gate. | The one measured layout that fits every name unchanged. Visible change: the three related values (Start / End / Gain) no longer sit side by side. |
| B2: icons off on those rows | `show_icon: false` on the twelve buttons, giving the name the icon's ~30px back: roughly 60px at 360px. | Same edit count as A2, no extra rows. | Unmeasured: 60px may still not hold Duration (50) with padding, or the date states (58); the icon is part of the design and those rows would look different from the rest. Needs a measurement before it can be chosen. |
| C2: shorter names | Rename to fit 29px: not achievable; the shortest useful words (Start, End) are already 36 to 43px at this font. | None. | Not viable at 360px; listed to close it. |
| D2: three per row stays, marquee stays there | Keep the marquee on exactly those twelve buttons and exempt them in the manifest by name. | Nothing in layout. | Those twelve labels stay clipped for reduced-motion users, the case the decision was made to remove; the same compromise RFC 0012's Option C was rejected for, narrowed to twelve cards. |

Also established by that measurement, and carried into row 1: with scrolling off, Bubble clamps a name to two lines with an ellipsis by an inline style, so the detector needs a vertical check (a clamped box whose scrollHeight exceeds its clientHeight) before a green run is evidence; the r5 twin is measuring which element carries it.

## Verification

Nothing verified on a290 yet. Rows 2, 5 and 6 carry the evidence as they land, and this section is written then. The r5 twin's measurements that motivated the decision are in RFC 0012 and its BACKLOG entry.

## References

- RFC 0012: [catch marquee-clipped labels in the UI gate](https://claude.ai/artifact/C3QQ5vbRHDrdGsWuvazmv3), indexed in claude-config as "a290-ha-addon: catch marquee-clipped labels in the UI gate". Decided 2026-09-27.
- `BACKLOG.md`, "UI gate misses text cut short with a fade" (P2, 2026-09-27), with the r5 measurements.
- #170 (separators wrap, 1.28.6) and #178 (the separator's yellow line keeps a floor, 1.28.8): the same rule applied to the other Bubble text.
- ADR 0003, whose completeness wait is why a late-painting wrapped name is still scanned.
