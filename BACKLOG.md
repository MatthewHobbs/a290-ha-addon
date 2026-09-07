# Backlog

Findings are logged with severity (P0 blocking / P1 this sprint / P2 later), the component
that owns the fix, and the evidence that produced them. `renault-mqtt` items live in the
shared engine repo but are tracked here because they surface as A290 add-on behaviour.

---

## ~~P1 — Tracker discovery declares a `state_topic` that nothing ever writes~~ — DONE

**Component:** `renault-mqtt` — `renault_mqtt/mqtt.py:165-172`
**Logged:** 2026-09-05 · **Fixed:** 2026-09-05

> **Resolved.** renault-mqtt#12 (v0.13.0), a290-ha-addon#99 (v1.21.0), r5-ha-addon#63 (v1.4.0),
> all merged. The title was wrong in one respect worth recording: the topic was **not**
> unwritten. Both add-ons published `"online"` to it on every successful poll — an earlier
> draft claimed nothing wrote it, because the search covered only the shared engine and never
> the add-ons' own `app/main.py`. Codex caught it in review. The fix therefore spanned three
> repos: retire the topic from discovery, and stop both runtimes writing it.

The `device_tracker` discovery payload sets `"state_topic": TRACKER_STATE_TOPIC`
(`<NODE>/location/state`), but the normal publish path never writes a value to that topic —
the only write is `client.publish(TRACKER_STATE_TOPIC, "", retain=True)` in the
location opt-out branch.

In Home Assistant's MQTT device tracker, the state-topic payload becomes `location_name`,
and `TrackerEntity.state` returns `location_name` whenever it is not `None`, only falling
back to computing the zone from `latitude`/`longitude` when it is `None`. So *any* value
retained on that topic — including one left by an older build — permanently masks the GPS
coordinates.

**Observed live:** `device_tracker.alpine_a290_location` read `online` while its own
attributes carried valid coordinates at home (`51.9473, -0.6274`, accuracy 11 m) and
`in_zones` correctly resolved `[garage, parking, home]`. The entity could never report
`home` regardless of where the car was.

**Fix:** drop `state_topic` from the tracker discovery payload entirely and rely on
`json_attributes_topic` + `source_type: gps`, letting HA resolve the zone. That is the
correct shape for a position proxy, it inherits zone renames for free, and it is immune to
a stale retained value. If the topic must be kept, publish the reset payload (the literal
string `None`) alongside every location update so `location_name` is cleared each cycle.

---

## P2 — UNTESTED: the dashboard render is not reproducible, so screenshots cannot be gated

**Component:** `ui-tests/` · **Logged:** 2026-09-07 · *updated with instrumented measurements*

**Marker: treat every committed file in `docs/screenshots/` as UNTESTED.** They are a plausible
picture of the dashboards, not a verified one.

Run `UI_TESTS_DIAG=1 bash ui-tests/run.sh` to write a `.diag.json` beside each screenshot
recording card count, scroll dimensions, pending images and `readyState` **at the moment of
capture**. Everything below came from that; the two fixes attempted before it existed were
guesses, and one of them made things worse.

### FIXED: the pop-up wrote blank screenshots over good ones

The worst class, and a real bug rather than flakiness. The smart-charging pop-up capture wrapped
its `wait_for_selector` in a bare `except: pass` and then screenshotted regardless — so when the
pop-up failed to open, an **empty page** was written as the documentation screenshot. Measured at
**24 cards on one run and 0 on the next**, identical page dimensions, **99.89% of pixels
different**. Fixed with a completeness gate: reopen once, and if it still renders nothing, skip
the capture and keep the committed file. Verified over two runs — zero-card captures went to 0,
and the pop-up genuinely needed the retry (once, then twice).

### The hypothesis that was WRONG

`JS_RENDERED` returns true as soon as ONE card exists, so it looked like the main captures were
firing early and catching a half-built page. They were not: `alpine-standard__iphone_15` shows
**253 cards in both runs**. The main dashboard is fully rendered at capture. Do not spend time
there again.

### What remains, measured (two runs, identical code and seed)

| Class | Shots | Magnitude | Diagnosis |
|---|---|---|---|
| Rasterisation noise | 3 | **0.024–0.032%** (240–3072 px) | identical DOM *and* dimensions — anti-aliasing. Not fixable by waiting. |
| Pop-up visual diff | 3 | **1.3–3.1%** (34k–111k px) | 24/24 cards, same size, yet large regions differ |
| Page size differs | 2 | 734x5002 vs 724x5454 | **253/253 cards** — same content, different layout |

**Next hypothesis, untested:** the size-differing pair is ~450px taller *and ~10px narrower*.
Same content at a different width is the signature of a **scrollbar** appearing in one run and
not the other, reflowing everything below it. `--hide-scrollbars` on the Chromium launch, or
`scrollbar-gutter: stable`, would test it cheaply. Measure before believing it — the last two
plausible fixes did not survive contact with the numbers.

### Consequence for the drift gate

A byte-exact gate (the `gotoad` model) **cannot work** while the rasterisation class exists: it
would fail on 240 changed pixels out of a million. A **tolerance-based** comparison is the only
viable form, and the numbers now say where to draw the line — genuine differences are >=1.3%,
noise is <=0.032%. Two orders of magnitude apart, so a threshold near 0.5% separates them
cleanly. That is why `refresh-screenshots` reports drift with an artifact rather than failing.

### Do NOT retry this as written

Waiting for stable scroll dimensions + card count + image completion instead of a fixed sleep is
the obvious idea and it **made things worse**: a blank page is *stable*, so the condition was
satisfied instantly and captured an empty dashboard. `alpine-bubble__pixel_8.png` had been
byte-identical at 169,749 bytes across two runs; with the wait it captured at 17,255. Any retry
needs a **completeness** condition (a known-good card count), which is what the pop-up gate now
does.

---

## P1 — A wedged poll loop is invisible: LWT catches process death, not a stalled producer

**Component:** `alpine_a290` + `renault-mqtt`
**Logged:** 2026-09-05

`client.will_set(AVAIL_TOPIC, "offline", retain=True)` (`mqtt.py:114`) is correct and
already in place, but it only fires when the **broker connection drops**. A process that is
alive with a healthy socket, but whose Kamereon poll loop has stopped succeeding, keeps the
connection open — so the LWT never fires and availability stays `online`.

**No stall has been observed.** This is a theoretical gap, not a live defect. An earlier
draft of this file claimed the add-on had stalled for two hours; that was wrong, and the
correction is worth recording because the faulty reasoning is easy to repeat:

> Every `alpine_a290_*` entity showed a `last_reported` frozen at the 09:13 restart, which
> was read as "the publisher is dead". It was not. The add-on's own log shows 163
> uninterrupted publishes at 5-minute intervals across exactly that window, and a clean
> `Logged in to the Renault API` with no auth or rate-limit errors. Home Assistant's MQTT
> integration uses `write_state_on_attr_change`, so an unchanged payload produces **no state
> write at all** — and therefore no `last_reported` update. The car had sat at 55%, plugged
> in and not charging all morning, so nothing changed.

**`last_reported` is not a liveness signal for MQTT entities.** Anything built to monitor
this add-on from the Home Assistant side must compare a value the producer actually varies
(a heartbeat counter or a publish timestamp), not entity metadata.

**Half of this shipped in v1.24.0 (a290 #115).** `sensor.alpine_a290_last_successful_poll`
now publishes the poll clock on every successful cycle, which is exactly the heartbeat this
entry asked for — a consumer can now distinguish "nothing changed" from "nothing published" by
watching that timestamp rather than entity metadata. **Still open: the producer-side watchdog.**
Nothing yet publishes `offline` to `AVAIL_TOPIC` after N failed cycles, so a wedged loop is
visible only to something actively watching the heartbeat, not to HA's own availability.

**`expire_after` is the other half of this, merged in from its own entry (logged 2026-09-05).**
Nothing currently bounds the age of a published value, so a value from a wedged producer renders
exactly like a fresh one. Setting `expire_after` on the tracker and the volatile state entities
makes them go `unavailable` on their own, without depending on the add-on being well enough to
notice.

Worth being precise about why it belongs here rather than as a separate concern: `expire_after`
counts **message arrival**, not value change, and the poller republishes the whole state document
every cycle whether or not anything changed. So it cannot fire for a car that is merely parked —
it fires only when publishing actually stops, which is exactly the wedged-producer case above. An
earlier reading of it as "bounds data age" was wrong; `data_stale` does that, and has done it
from the car's own timestamp since v1.24.0.

**Fix (original wording, heartbeat half now done):** publish a monotonic heartbeat or
`last_publish` timestamp on each successful poll,
so a consumer can distinguish "nothing changed" from "nothing published". A producer-side
watchdog that publishes `offline` to `AVAIL_TOPIC` after N failed cycles would then make a
stall visible the same way a crash is.

---

## P3 — Retained discovery: document the manual clear (the routine already exists)

**Component:** `renault-mqtt` (documentation + cleanup routine)
**Logged:** 2026-09-05

Discovery messages are published with `retain=True`, so an entity outlives the add-on that
created it: the broker replays the discovery config and Home Assistant faithfully recreates
the entity on every restart, frozen at whatever was last published.

**No confirmed instance.** An earlier draft cited `device_tracker.r5_car_location_raw` as an
example. That was wrong: it turned out to be a hand-written YAML entity in the user's
`mqtt/renault-5.yaml`, not a retained discovery message, and its `unique_id`
(`r5_location_raw_tracker`) never matched this code's `{prefix}car_location` scheme — which
should have been the clue. The risk below is real in principle; the evidence offered for it
was not.

**Related and confirmed, in that same legacy YAML:**

```yaml
- device_tracker:
    unique_id: r5_location_raw_tracker
    state_topic: "r5/location_raw"
    json_attributes_topic: "r5/location_raw"
```

A `state_topic` declared on a GPS tracker — the identical anti-pattern to P1 above, which
this add-on inherited from the hand-built setup it replaced. Worth fixing in both places, and
worth remembering that the pattern propagates by copying.

**The cleanup routine this entry proposed already exists** — checked 2026-09-07.
`renault_mqtt/mqtt.py:135-136` publishes a zero-length retained payload for every retired and
every unsupported sensor on **each** discovery publish, so an object-id the running catalog no
longer declares is cleared automatically. That is why this drops from P2 to P3.

**What is actually left:** documenting the *manual* clear for an entity that predates the
catalog's knowledge of it (publish a zero-length retained payload to its discovery topic), since
the automatic sweep only covers ids the catalog still lists.

---

## ~~P1 — Climate schedule sensors publish empty strings, not `unavailable`~~ — DONE

**Component:** `alpine_a290` · **Logged:** 2026-09-05 · **Fixed:** 2026-09-07 (a290 #126, v1.26.0;
r5 #82, v1.6.0; core renault-mqtt #26, v0.16.0)

> **Resolved**, and the fix was not where three attempts looked for it. It needed **no poller
> change at all**: the breaker already omits the keys, so declaring the two sensors in
> `DATA_GATED_SENSORS` gives them a compound MQTT availability (the add-on's own online/offline
> topic AND a key-presence test on the state topic, `availability_mode: all`) and the template
> resolves to `offline` on its own. Everyone reached for the poller; the answer was in discovery.
>
> Not verifiable by container boot on the a290: with the Renault hosts blackholed, detection
> fails and `hvac-settings` is PESSIMISTIC, so the two sensors are withheld from discovery
> entirely and the gating is unreachable. Verified at the discovery layer instead, and the
> identical core path was container-verified live on r5 where they are always published.

`sensor.alpine_a290_climate_schedule_mode` and `…_climate_ready_time` are fed by `hvac-settings`,
which returns `502000` on every call for this model. When the v1.23.1 circuit breaker trips, the
poller simply stops writing those two keys — so the retained state document omits them and both
entities render as **empty strings** rather than `unavailable`.

Empty is worse than unavailable: a card shows a blank value as though the car reported nothing,
instead of HA marking the entity unavailable and letting a dashboard hide or grey it.

**Fix:** withhold or explicitly mark the two entities unavailable when the breaker is tripped.

**Why this is its own entry now.** It was a paragraph inside the `hvac-settings` 502 entry above,
and was missed twice because of it — once when v1.23.0 shipped a gate that changed nothing, and
again when v1.23.1 shipped the breaker and stopped at the log spam. It is the oldest unfinished
item in this file: specified 2026-09-05, still not done across three releases.

---

## P3 — Three concurrent Kamereon clients coexist without apparent harm

**Component:** `alpine_a290` (documentation only)
**Logged:** 2026-09-05 · *tested and refuted; kept for the evidence*

Three independent clients polled the same Kamereon account on this installation: the legacy
R5 `command_line` sensors (~5 min), the core `renault` integration (~20 min), and this
add-on (5 min).

An earlier draft of this file asserted that this was starving the add-on's session. **That
was tested and did not hold.** The add-on's log shows uninterrupted 5-minute publishes and a
clean `Logged in to the Renault API (session cached for reuse)` right through the period of
supposed contention, with no auth failures and no rate limiting — while both other clients
were polling. Disabling the legacy pollers changed nothing, because nothing was wrong.

Kamereon *can* invalidate tokens on concurrent login, so the risk is real in principle and
worth a line in the README. On the evidence here, three clients coexisted fine and the
session caching in `renault-api` appears to absorb it.

**Standing recommendation, on its own merits rather than as a fix:** decide whether the
add-on should publish location at all when core `renault` already provides a correct, fresh
`device_tracker` — the add-on's distinct value is climate, charge control and the dashboards.

---

## P3 — Undocumented `actions/refresh-location` endpoint for model A5E1AE

**Component:** upstream `renault-api`
**Logged:** 2026-09-05

Logged on every start:

```
WARNING Endpoint actions/refresh-location for model A5E1AE is not documented,
using default endpoints.
```

The add-on falls back to default endpoints and works, so this is cosmetic for users — but it
is a standing upstream gap, and this installation has the model in hand to close it.
Contribute the A5E1AE endpoint mapping to
[hacf-fr/renault-api#1747](https://github.com/hacf-fr/renault-api/issues/1747), then drop the
pin once a release carries it.

**Status 2026-09-07 — DONE: both PRs are open upstream.**

Filed upstream as [hacf-fr/renault-api#2250](https://github.com/hacf-fr/renault-api/issues/2250);
maintainer `epenet` replied *"Feel free to create a PR"*. The work is **pushed to the fork** as
`MatthewHobbs/renault-api` branch `a5e1ae-refresh-location-and-hvac-settings` (`add7a95`, signed,
authored as matt@matthobbs.net) making exactly two changes to `kamereon/models.py`:

- `"actions/refresh-location": _DEFAULT_ENDPOINTS[...]` — verified working on the car: the
  vehicle answered 12s after invocation. `R5E1VE`, from which this entry was derived, already
  declares it; the line was missed in the copy.
- `"hvac-settings": None` — see the persistence evidence below.

Upstream test suite passes (363 passed, 30 skipped, 1 snapshot updated).

**DONE 2026-09-07 — both PRs are open upstream.** The bundled branch was split, as `epenet`
asked twice (*"separate PR for each issue mentionnned above"*), rebased onto current upstream
`main` (`42e92ab`), and the `.ambr` snapshot regenerated by the suite rather than hand-edited:

| PR | Change | Diff |
|---|---|---|
| [#2254](https://github.com/hacf-fr/renault-api/pull/2254) | adds `actions/refresh-location` | +5/-0 |
| [#2255](https://github.com/hacf-fr/renault-api/pull/2255) | sets `hvac-settings` to `None` | +2/-5 |

Both cut independently from `main`, so neither blocks the other. Verified before opening: full
suite 366 passed / 30 skipped with the same warning count as a clean `main`, upstream `ruff`
lint + format clean, commits signed, no PII in messages or diffs. Upstream titles follow *their*
house style (`Add … to A5E1AE (Alpine A290)`), not Conventional Commits — that rule is ours and
does not travel.

A durable clone now lives at `~/Documents/GitHub/renault-api`; the original was under
`/private/tmp`, which was the real fragility.

**Housekeeping, still outstanding — do this only once #2254 and #2255 have both merged:**
delete the superseded bundled branch `a5e1ae-refresh-location-and-hvac-settings` from the fork
(`git push origin --delete a5e1ae-refresh-location-and-hvac-settings`). It is kept until then in
case a maintainer wants the combined view. Nothing depends on it; both PRs use their own
branches.

**Correction still owed on #2250 — a wrong claim is standing publicly.** §4 of the issue claims
`hvac-history` and `hvac-sessions` are absent from the `A5E1AE` entry and need setting to
`None`. Both were **already `None` upstream** before this branch was cut (confirmed by reading
`add7a95^:src/renault_api/kamereon/models.py`); the finding was made against the 0.5.12
installed locally rather than upstream `main`. The commit correctly does not touch them, so only
the issue text is wrong. §3 was already corrected in the thread on 2026-09-07; §4 was not.

Also seen in the same log, and worth watching rather than fixing here:

```
WARNING hvac-settings unavailable: err.tech.vcps.ev.hvac-settings.error
  errorCode 502000 "something went wrong"
```

That is a Renault-side 502 on the HVAC settings endpoint, not an add-on fault. If it proves
persistent rather than transient, degrade the affected entities to `unavailable` instead of
logging a warning each cycle.

**Persistence established 2026-09-07: it is not transient.** ~94 consecutive failures over
7h35m at a 5-minute interval, zero successes, across four distinct `error_reference` prefixes —
so not one unhealthy backend node.

**Partially actioned.** v1.23.1 ships a circuit breaker: three consecutive failures pause the
call for the session, it retries every 12 polls (~hourly) and re-enables itself on recovery.
That stops the log spam. It does **not** degrade the entities — see the entry below, which was
split out of this one because it kept being missed while buried here.

Note also that gating on advertised support does not work here — `supports_endpoint()` reads a
static per-model table which returns True for this endpoint, so v1.23.0 shipped a gate that
changed nothing. Only the call's behaviour is a usable signal.

---

## ~~P1 — r5 has the `data_stale` defect a290 fixed in v1.24.0~~ — DONE

**Component:** `renault_5` · **Logged:** 2026-09-07 · **Fixed:** 2026-09-07 (r5 #82, v1.6.0)

> **Resolved.** `freshness_fields()` ported, `r5_poll_failing` + `r5_last_successful_poll` added,
> the `or iso(now_ts())` fabrication on `battery_last_activity` removed, and `stale_hours`
> defaulted to 36. Entity names stayed r5's own for forked-view compatibility. Container-verified:
> 41 sensors / 8 binary_sensors (was 40/7), and the retained state document omits `data_stale`
> when no car timestamp has been seen rather than asserting a confident "off".

`renault_5/app/main.py` still sets `data["data_stale"] = "off"` unconditionally on every
successful poll, so an R5 that stops reporting reads as healthy indefinitely — the identical
defect described (and fixed for the a290) below. It also carries the same
`or iso(now_ts())` fabrication on `battery_last_activity`.

The a290 side is complete and released, so per the a290-first rule this is now mirrorable:
port `freshness_fields()`, add `r5_poll_failing` + `r5_last_successful_poll` to the catalog, and
drop the fabricated timestamp fallback. **Entity names must stay r5's own** (forked-view
backward compatibility), so this is not a copy-paste.

---

## ~~P1 — `data_stale` measures the poll, not the car, so three days of silence read as healthy~~ — DONE

**Component:** `alpine_a290` (`main.py`)
**Logged:** 2026-09-07 · **Fixed:** 2026-09-07 (a290 #115, v1.24.0)

> **Resolved.** `data_stale` now compares the timestamp inside the **battery-status** payload
> against `stale_hours`. The poll-success signal moved to a new
> `binary_sensor.*_poll_failing` carrying the identical old rule, so no alerting was lost, plus
> a `sensor.*_last_successful_poll` heartbeat. Two corrections to the fix as specified below:
>
> - **Not `cockpit`.** The entry says "`cockpit`/`battery-status`", but cockpit commits only at
>   power-off, so keying on it would mark every parked car stale — the very mistake the
>   `gps_last_activity` entry describes. battery-status is the source.
> - **`last_updated` was fabricating freshness.** `getattr(battery, "timestamp", None) or
>   iso(now_ts())` stamped a timestamp-less payload as arriving that instant, which would have
>   made staleness permanently unfireable. Removed; it now reads `unknown`.
>
> Note the consequence, since it looks like a regression and is not: a car parked longer than
> `stale_hours` now reads `data_stale: on`, because the reading genuinely is that old. Paired
> with `poll_failing: off` that means "working fine, car simply parked". Documented in v1.24.1.
> **r5 has the identical defect and is NOT yet mirrored.**

`binary_sensor.alpine_a290_data_stale` is derived from `state["last_success"]`, which records
when the **poll** last succeeded — not when the **car** last reported. `main.py:624` sets it
`"off"` unconditionally on every successful poll.

Observed on this installation: the vehicle last reported at **2026-09-04T15:50Z**; ~68 hours
later, with `stale_hours` at 6:

| Entity | Value |
|---|---|
| `sensor.alpine_a290_last_updated` | `2026-09-04T15:50:04Z` — correct |
| `binary_sensor.alpine_a290_data_stale` | **`off`** — "healthy" |

The add-on polled Renault successfully every five minutes throughout, received the same
three-day-old payload each time, and reported it as fresh. Battery showed 55% while the car was
actually at 83% — a 28-point error presented as current.

This is the same defect as the `gps_last_activity` entry below, inverted: that one measures the
car where it should measure the feed; this one measures the feed where it should measure the
car. Both should key off the timestamp **inside the payload**.

Fix: compare the payload timestamp (`cockpit`/`battery-status` `timestamp`) against
`stale_hours`, and keep the poll-success signal as a separate concern — a failed poll and stale
data are different conditions and deserve different entities.

---

## P2 — `Refresh Location` should be opt-in, not published by default

**Component:** `alpine_a290` + `renault_5` (catalog / config schema) · **Logged:** 2026-09-07

The button is destructive on a parked car and has no established upside on this platform. It
should be gated behind a new `enable_refresh_location` option defaulting to **`false`**.

**Why it is destructive.** Invoking it on a vehicle that cannot obtain a fix replaces the last
valid cached position with `gpsLatitude 91` / `gpsLongitude 181` on a *current* timestamp, and
that state persists until the next completed journey — 18 hours, observed. There is no call that
restores it; only driving does.

**Why it is now WORSE than before the v1.23.0 fix, which is the part worth understanding.** That
release made the add-on reject the sentinel, so the tracker keeps its last known position and
Home Assistant looks perfectly healthy. But rejection only protects *our* entity — the Kamereon
data is still overwritten, which is why Renault's own app reported "We are unable to geolocate
your vehicle" for those 18 hours. The damage did not go away; it went **silent**. A user now
presses the button, sees nothing wrong in HA, and has a broken position in the official app.

**Why there is no upside to weigh against it.** `location` commits at power-off together with
`cockpit` — it is a trip-end event, not live telemetry (see the update-cadence section in
`DOCS.md`). A parked car has nothing newer to fetch, and the one tested invocation returned the
sentinel. On a moving car `location` was observed not to update at all during an 18-minute drive.

**Fix:**

- New option `enable_refresh_location`, default `false`, with the warning in its description.
- Gate BOTH the discovery publish and the inbound command on it, exactly as `publish_location`
  already gates them (`mqtt.py` publishes the button only when the endpoint is supported and
  `PUBLISH_LOCATION`; `main.py` ignores the command via `LOCATION_CMDS`). Gating at the source
  matters because the entity is pressable from voice, automations and any dashboard — a
  confirmation on the bundled dashboard would only cover one path.
- Prominent CHANGELOG entry: existing installs lose the button until they opt in. That is a
  visible change and should not be a quiet line.

**Open question for r5.** The harm mechanism is platform-level — a sleeping car cannot obtain a
fix — and the R5 is the same CMF-BEV / KCM platform, so it very likely applies. But it is
**untested there**, and the standing rule is not to assume A290 behaviour holds for R5. Suggested:
default `false` in both for consistency, and say plainly in r5's changelog that it is
precautionary rather than observed.

Runtime change in both add-ons: needs container verification and a release each.

---

## P2 — `actions/refresh-location` is destructive on a parked vehicle

**Component:** `alpine_a290` (Refresh Location button) + upstream docs
**Logged:** 2026-09-07

Invoking it on a car that cannot obtain a fix **replaces the last valid cached position** with
`gpsLatitude 91` / `gpsLongitude 181` and a *current* timestamp. There is no call that restores
it; only completing a journey does. Observed here: the marker persisted **18 hours** until the
next drive, during which the vehicle reported `not_home` while parked on the drive, and Renault's
own app showed "We are unable to geolocate your vehicle."

The fresh timestamp is what makes it dangerous — a staleness check passes while the coordinates
are unusable, so the bad data looks *more* current than the good fix it replaced.

Established by testing on 2026-09-07: `location` and `cockpit` commit together at **power-off**
with an identical timestamp; they are trip-end events, not continuous telemetry. `battery-status`
and `hvac-status` refresh independently while parked. So `91/181` means "no fix available right
now", **not** a fault — a parked car simply keeps serving its last committed fix.

v1.23.1 already rejects `91/181` rather than publishing it, which protects our entity but **not
the car's data** — Renault's own app still showed no position for 18 hours. See the entry above,
which proposes the actual remedy: make the button opt-in behind `enable_refresh_location`,
defaulting to off, rather than documenting a hazard and leaving it enabled.

---

## ~~P2 — `gps_last_activity` measures the car, not the feed~~ — ADD-ON SIDE DONE

**Component:** `alpine_a290` (and any consumer of `binary_sensor.a290_gps_stale`)
**Logged:** 2026-09-05

`binary_sensor.a290_gps_stale` compares `sensor.alpine_a290_gps_last_activity` against a
26-hour threshold. But that timestamp is the **car's** last GPS activity as reported by
Kamereon — and a stationary car generates none. So the sensor fires for any vehicle left
still for more than a day, which for a car parked at home is most of the time.

**Observed live:** the sensor read `on` with the car on its own driveway, plugged in and
charging normally, because `gps_last_activity` was 2026-09-02 and the car had not moved
since. The Home dashboard card consequently rendered
`GPS stale 72.5h · last: Home · 55%` for a car that was fine.

This is the same confusion as the `last_reported` mistake recorded in P1 above: **data
freshness and subject activity are different quantities.** "The car has not moved" and
"we have not heard from the car" look identical if you only look at the car's own
timestamps.

**Workaround already applied** (display layer, not a fix): the dashboard card now tests
`Home` before `stale`, so a car at home never shows a staleness warning. The underlying
sensor is still wrong for any parked-away-from-home case.

> **The add-on half is complete (v1.24.0, #115).** This entry asked for a
> `last_successful_poll` timestamp published on every completed cycle, keyed off instead of the
> car's own activity — that is exactly `sensor.alpine_a290_last_successful_poll`, which now
> exists alongside `binary_sensor.*_poll_failing`.
>
> What remains is **not in this repo.** `binary_sensor.a290_gps_stale` is not published by the
> add-on at all — it appears only in a code comment and a test here. It is a user-side template
> sensor, so re-pointing it at `last_successful_poll` is a Home Assistant config change, not an
> add-on change. Kept only as a note to whoever owns that template.

**Original fix (now shipped):** derive staleness from **when the add-on last successfully fetched**, not from when
the car last moved. Publish a `last_successful_poll` timestamp on each completed cycle
(this is the same heartbeat proposed in P1 — one field satisfies both), and key the
staleness binary sensor off that. Then "stale" means "we have not heard from Kamereon",
which is the thing worth alerting on, and a parked car reads as healthy.

## P2 — GPS staleness guard is blind on a process that has never had a usable fix

**Component:** `alpine_a290` (`poll_once`) · logged 2026-09-06

`gps_last_activity` carries the last USABLE fix time forward when a location payload is
rejected, so `binary_sensor.*_gps_stale` stays armed. Both fallbacks — persisted `state` and
the in-process `_LATEST` — are empty on the first poll of a freshly started process, so if that
poll returns a sentinel the key is omitted from the retained state document and the guard reads
"not stale" until the first usable fix arrives.

Surfaced by dual review across three separate rounds; not fixed because every cheap option is
wrong (advancing the timestamp is the original bug; publishing a fabricated old one is
dishonest). The correct fix is to seed `state["gps_last_activity"]` at startup by reading the
add-on's own retained state topic before the first publish, which is real MQTT work rather than
a tweak.

Mitigations already in place: a WARNING on every rejected fix, and no tracker attributes
published, so nothing asserts a false position. Self-corrects permanently on the first usable
fix.

**Separate operational note:** a retained payload published by an OLDER version is not cleared
by this fix. An install that already retained sentinel coordinates keeps showing them until a
usable fix replaces them, or until the attributes topic is cleared by hand.
