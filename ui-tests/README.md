# Dashboard UI tests — responsive / truncation

Renders the bundled dashboards (`alpine_a290/dashboards/front-end.txt` and
`front-end-bubble.txt`) in a **real Home Assistant** across the top mobile device sizes and
**fails on any text truncation or broken card**. This is what catches regressions like the
Mushroom tile labels clipping on a phone.

## How it works

1. `run.sh` boots a throwaway Home Assistant container, vendors all four custom cards
   (Mushroom, Button Card, card-mod, Bubble Card) as pinned single-file bundles into `www/`
   — served same-origin so no card loads over the network at render time (only the Zen Dots
   webfont is still fetched remotely) — and completes
   onboarding to get an API token.
2. `seed.py` gives every entity the dashboards reference a representative state via the REST
   `/api/states` API — no MQTT or add-on needed, since the cards read `hass.states`
   directly — then registers the card resources and creates the two dashboards from the
   bundled YAML. card-mod is the exception: `run.sh` loads it as a frontend module
   (`extra_module_url`) so it is defined before any card renders; as a resource it can lose
   that race and leave cards unstyled.
3. `check_overflow.py` (Playwright) loads each dashboard at every viewport in `devices.json`,
   waits for the cards and the Zen Dots font, then walks the **shadow-DOM-pierced** tree for
   any text element that is clipped (`text-overflow:ellipsis` / `nowrap`+`overflow:hidden`
   with `scrollWidth > clientWidth`) or any `hui-error-card`. A screenshot is saved per
   device; the run exits non-zero with a report if anything is clipped.
4. **Bubble pop-ups.** The Bubble dashboard is nothing but pop-ups, and Bubble renders a pop-up
   only while it is open (a closed one is detached from the DOM, measured on 3.4.1), so step 3
   sees only the main menu the dashboard auto-opens. `seed.py` lists every pop-up the dashboard
   defines (`card_type: pop-up`, its `hash` and `name`) in the manifest, and `check_overflow.py`
   opens each by hash navigation, waits until **that** pop-up (matched to its hash through the
   `bubble-card` that renders it) is open on screen showing its header `name`, scans it with the
   same truncation check, and screenshots it as `<dashboard>__popup_<hash>__<device>.png`
   (`#alpine-charging` keeps its `smart_charging` name, which the drift workflow reads). Findings
   inside a pop-up are reported with its hash. The listing cannot go quietly empty: `seed.py`
   stops on a pop-up without a hash or a name, on two sharing either, on a `navigate` action whose
   target no pop-up defines, and on a Bubble dashboard defining fewer than `MIN_POPUPS`.

   A pop-up that never stays open on one device is skipped there and reported, never captured as
   the menu behind it, and the run goes on: opening by hash can tear down the JS context on a slow
   viewport, and the pop-up config is identical across viewports. HA also reloads the page once,
   about five seconds after a context's first load, as its service worker takes control; when
   that lands inside a pop-up's scan the pop-up is reopened and rescanned once before it counts
   as skipped. Each pass prints how many devices skipped each pop-up, and **fails when a pop-up
   was skipped on every device**, since it was then never checked at all.
5. **Problem-sensor and toggle passes.** The seed is a parked car on a working add-on (Data
   Stale on, Poll Failing and API Auth Failure off) with the demo charger dispatching, so the rest
   of what those sensors switch (the Not Polling and Auth Failure cards, the Last Updated branch
   of the Car Parked tile, the off-peak badge's "Peak rate") never renders in steps 3-4. `seed.py`
   derives further passes over every problem-class binary sensor the dashboards reference (from
   `catalog.py`, not listed here) and over `TOGGLES`, and every pass must be a state production
   can publish:
   - `IMPLIES` declares the states production cannot publish apart. Poll Failing is on only
     when the last successful poll is older than `stale_hours`, and the car timestamp Data
     Stale ages came from a poll, so Poll Failing on forces Data Stale on. `DERIVED_AGE` gives
     Last Updated the age its Data Stale state needs (older than the `stale_hours` default when
     on, newer when off).
   - **alarm** inverts every problem sensor, then applies `IMPLIES`: Auth Failure and Not
     Polling on, Data Stale left on with its old timestamp.
   - A branch that closure kept off the page gets a pass of its own, named `<sensor>_<state>`:
     today **data_stale_off**, a healthy car with a fresh Last Updated.
   - `TOGGLES` names demo entities with a second state production publishes that the seed keeps
     off the page, `{entity: that state}`; each gets a `<entity>_<state>` pass: today
     **demo_intelligent_dispatching_off**, Octopus outside a dispatch window, when the badge reads
     "Peak rate" on the standard dashboard and "Now: Peak rate" in the Smart Charging pop-up.
     Every other pass reseeds each toggle at its seeded state. A toggle no dashboard references,
     or given its seeded state, is an error.

   `seed.py --list-passes` prints them; `run.sh` reseeds each with `seed.py --pass <name>` and
   re-checks, with `check_overflow.py --pass-name <name>`, only the dashboards that reference a
   state the pass changed, across the same devices, opening only the pop-ups that reference one.
   Each pass reseeds every problem sensor, toggle and derived timestamp, not only the ones it
   changes, and reads them back: it stops unless HA holds what was seeded and `IMPLIES` and the
   ages hold, so a state left over from the previous pass cannot render.

   Every pass, the normal one included, also fails unless the text its states select is
   visible: the `name` of each conditional card whose conditions they meet, and the chosen
   branch of each text field (`primary`, `secondary`, `name`, `label`, `title`, `heading`,
   `content`) of the form `A{% if is_state('<sensor>','<state>') %}B{% else %}C{% endif %}D`
   (A and D literal, possibly empty), read from the dashboard file. A label inside a pop-up must
   be visible inside that open pop-up. The seed stops with an error if a text field reads one of
   these sensors' **state** (`is_state`, `states(...)`, `states.<id>`) in any other form; one that
   reads only its attributes (`state_attr`) renders the same in every pass and is not a label.
   It also stops if a dashboard references a sensor a pass changes but no text on it switches
   with that sensor. The second check is what catches a tile hard-coded to one branch: the
   expected text is read from the same file, so it disappears along with the switch, but the
   sensor is still referenced by the tile's icon and colour templates. It cannot catch a change
   that removes every reference to the sensor, and it names the sensor, not the lost text. Not
   asserted: icons, colours and `card_mod` styles keyed on those sensors (they are rendered and
   truncation-checked, but carry no text). `IMPLIES` holds only the relations declared in it; one
   production gains is not checked until it is added there. A named pass's screenshots are
   `<dashboard>__<pass>__<device>.png` (pop-ups `<dashboard>__<pass>__popup_<hash>__<device>.png`),
   so they never overwrite the normal pass's.

## Device matrix

`devices.json` — the top mobile devices for 2024-25 plus the narrow/wide bounds (CSS
viewport width is the truncation-critical dimension):
iPhone 15 Pro Max / Pro / 15 / SE, Pixel 8 / 7a, Galaxy S24 / S23 / A54, and a 360px Android
narrow bound.

## Run locally

Needs `docker` + `curl` and a Python with `aiohttp PyYAML playwright` (and
`playwright install chromium`):

```bash
PYTHON=/path/to/venv/bin/python bash ui-tests/run.sh
```

Screenshots land in `ui-tests/screenshots/` (git-ignored; uploaded as a CI artifact). In CI
this is the **UI Tests** workflow, which runs whenever the dashboards or this harness change.

## Docs-screenshot drift report

On a PR, a trusted `workflow_run` companion (`.github/workflows/refresh-screenshots.yaml`)
downloads the rendered artifact, resizes the phone shots, and reports whether they differ from
the committed `docs/screenshots/`. If they do, the regenerated PNGs are attached to the run as
the **regenerated-screenshots** artifact; download and commit them yourself if the change is
real. It writes a job summary and nothing else.

**It does not commit, and it never fails the build.** Both are deliberate.

It used to commit refreshed PNGs to the PR head via a GitHub App token, and that raced with
whoever was working on the PR: push → render differs → bot commits → head moves → your push is
rejected non-fast-forward, or the `CLEAN` verdict you just read is invalidated and the merge
refused. On 2026-09-07 that rejected three pushes, twice mid-merge, and moved the head five
times across the two repos. The `[refresh-shots]` guard only stopped the bot re-triggering
*itself*; it did nothing about a human merging that commit and pushing again. CI writing to a
branch a human is working on **is** the race, so CI no longer writes to it — the App token and
its `Contents: write` scope are gone from the workflow entirely.

It reports rather than failing because **the render is not yet reproducible**. Two consecutive
runs of identical code against the same seed produced 10 of 30 screenshots differing, two at
different page *dimensions* (724x5454 vs 734x5002) — cards still land after capture, so
`full_page` grabs a different-height document. Freezing the CSS animations
(`animations="disabled"` plus `reduced_motion`) and anchoring the seeded timestamps to run time
fixed the fast oscillation; the remaining layout settling must be solved before this can become
a required drift gate like the one in `gotoad`.
