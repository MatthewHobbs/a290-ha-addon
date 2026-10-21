# Backlog

Open findings only, with severity (P0 blocking / P1 this sprint / P2 later / P3 whenever), the
component that owns the fix, and the evidence. Finished work is in `CHANGELOG.md` and git
history. `renault-mqtt` items live in the shared engine repo but are tracked here because they
surface as A290 add-on behaviour.

---

## P1 — Three PII leaks reached the public repo

**Component:** repo-wide · **Logged:** 2026-09-08

A real VIN in a test fixture, home coordinates in this file's prose, and the same coordinates in two
fixtures. All three were fixed forward and found by greps, not by a scanner. The guard is
`scripts/pii_check.py` (CI Security job and `just ci`); it now also refuses email addresses and
home-directory paths. History was not rewritten and the scrub is still pending. The r5 twin needs
the same fixture review. This file is public, so the values are not repeated here.

## P1 — A failed poll blanks about 40 entities: the retained state document is replaced, not merged

**Component:** `alpine_a290` (`main.py`, the `except` branch of the poll loop) · **Logged:** 2026-09-08

On any exception in `poll_once` the `except` branch publishes a short document (`api_auth_failure`,
`last_successful_poll` and the freshness fields) to `mqtt.STATE_TOPIC` with `retain=True`. That
replaces the retained state rather than merging into it. Every other sensor's `value_template` is
`{{ value_json.<key> }}`, so an absent key renders as an empty string. Only two entities carry the
compound availability that would save them (`catalog.py` `DATA_GATED_SENSORS`).

The intent was the opposite: the comment there says the last known car timestamp "simply ages",
and the status panel's `_LATEST["data"].update(...)` does merge, so the panel and MQTT disagree.
HA renders a JSON `null` safely, so emitting every catalog key as `null` or publishing
`{**last_data, **fresh}` both work.

`test_main_failure_path_publishes_both_signals` asserts the partial document but would still pass
after a merge fix, so the suite fails to detect the bug rather than encoding it.

**Fix:** merge instead of replace, and add a test that `battery_level` survives a failed poll.

## P1 — Endpoint support is probed once at startup and never re-probed

**Component:** `alpine_a290` (`main.py`) · **Logged:** 2026-09-08 · **Re-confirmed:** 2026-10-20

A transient boot-time login failure (HA restarting after a power cut, DNS not yet up) leaves
`supported` nearly empty. `publish_discovery` then writes empty retained configs, which is how HA
deletes an entity, so all six buttons and both numbers vanish. `_MQTT_CTX` re-publishes that
degraded set on every reconnect and `/healthz` still returns `ok`, so nothing restarts it. The
buttons stay hidden until someone restarts the add-on. A container booted with the Renault hosts
blackholed shows it: `Endpoint-support detection failed (publishing sensors, hiding action
buttons)`, then `buttons=none`.

**Fix:** re-probe after N consecutive failures, or whenever `supported` lacks the action
endpoints, and re-publish discovery. Never delete a button on a detection error, only on a
definite negative.

## P1 — A wedged poll loop is invisible: LWT catches process death, not a stalled producer

**Component:** `alpine_a290` + `renault-mqtt` · **Logged:** 2026-09-05

The last-will message only fires when the broker connection drops. A process whose Kamereon poll
loop has stopped succeeding keeps the socket open, so availability stays `online`. No stall has
been observed; this is a gap, not a live defect.

`last_reported` is not a liveness signal for MQTT entities: HA does not write state when the
payload is unchanged. Monitor a value the producer varies. `sensor.*_last_successful_poll`
(v1.24.0) is that heartbeat.

**Still open:** a producer-side watchdog that publishes `offline` to `AVAIL_TOPIC` after N failed
cycles, and `expire_after` on the tracker and volatile entities. `expire_after` counts message
arrival, and the poller republishes the whole state document every cycle, so it only fires when
publishing stops, which is the wedged case.

## P2 — `/healthz` always answers `ok`; a wedged poller looks healthy

**Component:** `alpine_a290` (`main.py`, Dockerfile `HEALTHCHECK`) · **Logged:** 2026-10-20

A wedged poller looks healthy to the image HEALTHCHECK and the Supervisor. Related to the entry
above. **Fix (proposed):** fail `/healthz` when the last successful poll is too old.

## P2 — No recorded or fake Kamereon client for the container gate

**Logged:** 2026-09 (restated 2026-10-21). a290 is observed on a real car; r5 can only ever be
tested against fixtures, and the container boot proves only that Renault was refused. A small fake
Kamereon server (or recorded responses) behind the blackholed hosts would let the boot test and the
Supervisor pilot exercise a successful poll end to end.

## P2 — r5 logs raw API responses at `log_level: debug`; a290 does not

**Component:** `renault_5` (`app/main.py`) · **Logged:** 2026-09-08

`alpine_a290/app/main.py` clamps the `renault_api`, `renault_api.kamereon` and
`renault_api.gigya` loggers to INFO, so the library's DEBUG records are never created. At
renault-api 0.5.13 its only DEBUG calls log the API-key config response and full Kamereon
request and response bodies (VIN, account ids, unrounded GPS). The Gigya JWT travels as a header
and is never logged. r5 has no clamp; its redaction filter only masks configured secrets, so the
rest of a response passes through. This is a live personal-data path on the twin and should not
wait behind the a290-first rule.

## P2 — The Bubble dashboard has none of the alarm semantics the standard one has

**Component:** `alpine_a290/dashboards/front-end-bubble.txt` · **Logged:** 2026-09-08

Counts, bubble vs standard: `poll_failing` 0 / 3, `auth_failure` 0 / 3, `data_stale` 0 / 3, "Not
Polling" 0 / 2, "Car Parked" 0 / 3. Its "Last Updated" tile is a static button with
`tap_action: none` and a hard-coded `color:green !important`, bound to no entity. On one of the
two dashboards a wedged poller or expired credential is permanently invisible. The v1.25.0 change
(red pulsing card re-pointed from `data_stale` to `poll_failing`) reached only the standard one.
**Fix:** port it, or add a test that diffs entity coverage between the two files.

## P2 — The shipped image is scanned by nothing, and the shared core has no SAST

**Component:** CI (`.github/workflows/ci.yaml`) + `renault-mqtt` · **Logged:** 2026-09-08

`trivy fs` scans the repo; the build job builds the image and does not scan it. `renault-mqtt` is
installed from a git SHA with `--no-deps` and is in no manifest, so `pip-audit` cannot see it, and
its own CI is ruff + pytest only. The unscanned code is the redaction net (`config.py`,
`debug.py`). `semgrep` is not run, so `bandit -ll` is the only SAST and misses what semgrep would
catch.

## P2 — Redistribution: a press render, a font with no licence, no disclaimer

**Component:** repo root + `alpine_a290/dashboards/` · **Logged:** 2026-09-08

- `dashboards/Images/Background/alpine_a290_side.webp` looks like an official Alpine studio press
  render. `LICENSE` grants everyone the right to copy and sell it, which is not ours to grant.
- `dashboards/Fonts/ZenDots-Regular.ttf` ships with no `OFL.txt`. OFL-1.1 §2 requires the licence
  to travel with redistribution, and `INSTALLATION.md` tells users to copy the file onward.
- `LICENSE` has no NOTICE for the MIT-licensed upstream the README credits.
- Nothing says "not affiliated", "trademark" or "at your own risk", and nothing states what
  happens if Renault changes or closes the API.

## P2 — Coverage is 99%, and the most defect-dense branch is unverified

**Component:** `alpine_a290/tests` · **Logged:** 2026-09-08

Thirteen tests across three files contain no assertions. Several are fair smoke tests. One is not:
`test_main_handles_failing_poll` drives the whole poll-failure branch (availability, the auth
heuristic, backoff, and the state publish in the P1 above) and asserts nothing. Coverage
measures execution, not verification. It also measures `alpine_a290/app` only, so `renault_mqtt`,
which builds every discovery payload, is outside the number.

**Fix:** assert `(AVAIL_TOPIC, "online")` is published, assert the auth flag for an auth-shaped
error, and drive two or three consecutive failures to pin the backoff doubling and its cap.

## P2 — A navigation side effect is hidden inside a CSS hook

**Component:** `alpine_a290/dashboards/front-end-bubble.txt` (bottom nav `styles:`) · **Logged:** 2026-09-08

The `styles:` field returns `""`; its real payload is `setTimeout(() => { location.hash =
"#alpine"; }, 60)`. This is shipped product behaviour, not a test flake: a user tapping a tile
within 60 ms of load can have the URL and open pop-up changed under them.
`ui-tests/check_overflow.py` works around it with a retry. **Fix:** open once on element connect
or on the first `hass` set, not from a computed-CSS return value on a timer.

## P2 — `stale_hours: 36` assumes a daily driver

**Component:** `alpine_a290/config.yaml` · **Logged:** 2026-09-08

Since v1.24.0 `data_stale` measures the age of the car's own reading, and the car only commits at
power-off. A car used as needed (weeks between journeys) sits at `data_stale: on` for much of its
life, which trains people to ignore the alarm. `poll_failing` is the signal that means something
is wrong. Open question: does `data_stale` earn a default-visible alarm at all? A longer
threshold is unsettable today because the schema is `int(1,48)`. The premise is reasoned, not
observed: there is no telemetry. Existing installs also keep their old value (the default moved
6→36 in v1.25.0), so the installed base is split.

## P2 — GPS staleness guard is blind on a process that has never had a usable fix

**Component:** `alpine_a290` (`poll_once`) · **Logged:** 2026-09-06

`gps_last_activity` carries the last usable fix time forward when a location payload is rejected.
Both fallbacks (persisted `state` and in-process `_LATEST`) are empty on the first poll of a fresh
process, so if that poll returns a sentinel the key is omitted and `gps_stale` reads "not stale"
until the first usable fix. Advancing the timestamp is the original bug and publishing a
fabricated old one is dishonest. **Fix:** seed `state["gps_last_activity"]` at startup from the
add-on's own retained state topic. Mitigated by a WARNING on every rejected fix and no tracker
attributes being published. A retained payload from an older version is also not cleared by this
fix; clear the attributes topic by hand.

## P2 — Conformance test: the domain allowlist exempts more than it names

**Component:** `alpine_a290/tests/test_dashboard_conformance.py` · **Logged:** 2026-09-27

No effect while `_ALLOWED_DOMAINS` is empty. (1) `_allowed()` is domain-wide, so listing a domain
also exempts a misspelt or nonexistent id in it; per-id scoping would close that. (2)
`test_ui_gate_seeds_what_the_add_on_publishes` does not consult `_allowed()`, so a seeded id in a
listed domain reads "not published": a false fail, never a false pass. a290 fixes first, r5
mirrors.

## P2 — The dashboard render is not reproducible, so screenshots cannot be gated

**Component:** `ui-tests/` · **Logged:** 2026-09-07

Treat every file in `docs/screenshots/` as untested: a plausible picture, not a verified one. Two
runs of identical code and seed differed as follows.

| Class | Magnitude | Diagnosis |
|---|---|---|
| Rasterisation noise | 0.024–0.032% of pixels | identical DOM and size; anti-aliasing |
| Pop-up visual diff | 1.3–3.1% | same cards and size, large regions differ |
| Page size differs | 734x5002 vs 724x5454 | same 253 cards, different layout |

A byte-exact gate cannot work. A tolerance near 0.5% separates noise from real change.
**Untested hypothesis:** the size-differing pair is about 10px narrower, which is the signature of
a scrollbar in one run only; `--hide-scrollbars` or `scrollbar-gutter: stable` would test it.
**Do not** wait for stable scroll size, card count and images instead of a sleep: a blank page is
stable, so the condition passed at once and captured an empty dashboard. Any retry needs a
completeness condition (a known-good card count), as the pop-up gate has.

## P2 — An account behind two-factor sign-in cannot use the add-on

**Logged:** 2026-10-21. renault-api 0.6 raises `PendingTwoFactorAuthenticationException` when
Renault requires an emailed code. The add-on shows API Auth Failure and retries on its backoff; it
cannot complete the flow unattended. Supporting it needs a way to enter the code (an option or a
service call) and to store the remembered device ids.

## P3 — The Dockerfile's HEALTHCHECK comment claims the Supervisor acts on it

**Logged:** 2026-09 (restated 2026-10-21). The comment says the Supervisor monitors the HEALTHCHECK
and restarts a deadlocked add-on. HA's add-on docs are silent on whether the Supervisor acts on a
Docker HEALTHCHECK, and the `watchdog:` key is rejected by the add-on linter as obsolete. Disproving
check: on a real HA host, wedge the event loop and watch whether the Supervisor restarts the add-on.

## P3 — CI tools are pinned by version only

**Logged:** 2026-10-21. bandit, pip-audit, pytest, pytest-cov and the UI gate's pip installs are
`name==version`, so their transitive dependencies resolve fresh on every run. A hash-locked
`requirements-ci.txt` installed with `--require-hashes` would close that.

## P3 — Trivy's binary is checked for integrity, not authenticity

**Logged:** 2026-10-21. `setup-trivy` (pinned, v0.3.1) installs through `install.sh` at a pinned
trivy commit, which checks the release's own `checksums.txt`. Verifying the release's cosign
signature would also prove who built it.

## P3 — UI gate flake: Bubble location pop-up measured mid-animation at 360x640

**Logged:** 2026-10-21. On #235 (head c53f092) the minimum leg (HA 2026.8.1) reported
`TRUNCATED <span> 224>92px` for the Bubble location pop-up on the 360x640 narrow bound; a re-run of
the same commit passed. A 92px box for a 224px label fits a pop-up still sliding in. Disproving
check: re-run that leg on the same commit until it fails again, then read whether the pop-up's
completeness wait was satisfied before the scan.

## P3 — No regression test for the map marker's dark circle

**Logged:** 2026-10-21. The map card sets `--card-background-color` so its marker stays dark on the
light theme; the light-text test covers the sections, not this line, and the UI gate checks
truncation, not contrast. Assert the map card's style keeps the variable.

## P3 — The visibility evaluator's `not` semantics are unverified

**Logged:** 2026-10-21. `test_dashboard_conformance.py` models `not` as "none met"; the frontend's
Lovelace `not` may be "not all met". No dashboard uses `not` today. Read
`validate-condition.ts` before relying on it.

## P3 — No test that a whole-number float charge limit (`"100.0"`) is accepted

**Logged:** 2026-10-21. The code accepts it; nothing pins that against a stricter parser later.

## P3 — Parity rows appended at the end of `expected.tsv` collide between PRs

**Logged:** 2026-10-21. Two open PRs that both append rows at the end conflict, and each rebase
costs a review round. Insert a new row beside the existing rows for the same path.

## P3 — The UI gate no longer samples a 2.6 device scale

**Logged:** 2026-10-21. Dropping Pixel 7a left 2, 2.625 and 3. No defect is known at 2.6.

## P3 — CI "Build (amd64 sanity)" duplicates the release workflow's build

**Component:** CI (`.github/workflows/ci.yaml`) · **Logged:** 2026-10-20

`release.yaml` already builds the image. Removing the job needs the required-check list changed.

## P3 — `home-assistant/actions/helpers/info` is pinned to a master SHA

**Component:** CI workflows · **Logged:** 2026-10-20

No upstream tag contains that commit, so the pin cannot carry an exact-tag version comment.

## P3 — `log_level` accepts `trace` and `notice` but maps both to INFO

**Component:** `alpine_a290/config.yaml` schema · **Logged:** 2026-10-20

Both map to INFO. A schema change belongs in a release PR (see `CONTRIBUTING.md`, "What reaches
users on merge").

## P3 — Parity is a required check, so every dependency-bot PR is blocked

**Component:** CI (`Parity with r5`) · **Logged:** 2026-10-20

Every dependency-bot PR is blocked until a `pending` row is written by hand.

## P3 — The comment above `DATA_GATED_SENSORS` describes renault-api 0.5.13

**Component:** `alpine_a290/app/catalog.py` · **Logged:** 2026-09-08

"A5E1AE advertises hvac-settings as supported..." is stale since 0.5.14 set it to `None`. The
breaker still guards the server's `502000`. Correct it in the next change that touches
`catalog.py` for a real reason (it is in the UI gate's path filter).

## P3 — Bubble's bottom bar draws two labels on top of each other, and the gate cannot see it

**Component:** `alpine_a290/dashboards/front-end-bubble.txt` (bottom navigation) · **Logged:** 2026-09-24

In every Bubble screenshot checked, the main menu's bottom pill shows two labels ("Test" and
"Home") and two icons drawn over each other, including the committed `bubble-galaxy-s24.png`. The
gate fails on truncation and `hui-error-card`, not on two complete labels in one box. **Not
established:** the cause. Candidates are two buttons whose visibility conditions both hold under
the seeded state, or a transition captured mid-animation.

## P3 — Accessibility and client-performance cluster in the dashboards

**Component:** `alpine_a290/dashboards/` + `app/panel.html` · **Logged:** 2026-09-08

- No `prefers-reduced-motion` guard against about 17 infinite CSS animations. The UI harness
  forces `reduced_motion="reduce"` because production never respects the OS setting.
- `dashboards/CSS/zen-dots.css` has no `font-display`, risking FOIT on a slow connection.
- `panel.html` updates auth-failure and data-stale status every 10s with no `aria-live`, and has no
  `<main>` landmark.
- `front-end-bubble.txt` uses `background-attachment: fixed`, a known mobile scroll-jank pattern.
- Unavailable data renders like critically-low (`range` → `float(0)` → red band).

## P3 — Retained discovery: document the manual clear

**Component:** `renault-mqtt` (documentation) · **Logged:** 2026-09-05

Discovery is retained, so an entity outlives the add-on that created it. The code already clears
every retired and unsupported object id on each discovery publish (`renault_mqtt/mqtt.py`). What
is left is documenting the manual clear for an entity the catalog no longer knows (publish a
zero-length retained payload to its discovery topic). No confirmed instance of the risk.

## P3 — Three concurrent Kamereon clients coexisted without harm

**Component:** `alpine_a290` (documentation only) · **Logged:** 2026-09-05

A legacy `command_line` poller (~5 min), the core `renault` integration (~20 min) and this add-on
(5 min) polled one account together with no auth failures or rate limiting, and disabling the
others changed nothing. Kamereon can invalidate tokens on concurrent login, so the risk is real in
principle and worth a line in the README. Open question: should the add-on publish location at all
when core `renault` already provides a fresh `device_tracker`? Its distinct value is climate,
charge control and the dashboards.
