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

**Fix:** publish a monotonic heartbeat or `last_publish` timestamp on each successful poll,
so a consumer can distinguish "nothing changed" from "nothing published". A producer-side
watchdog that publishes `offline` to `AVAIL_TOPIC` after N failed cycles would then make a
stall visible the same way a crash is.

---

## P2 — No `expire_after` on location and state entities

**Component:** `renault-mqtt`
**Logged:** 2026-09-05

Nothing bounds the age of a published value. A fix from days ago renders identically to a
fresh one. Setting `expire_after` on the tracker and the volatile state entities makes them
go `unavailable` on their own once stale, which degrades honestly instead of lying
confidently, and does not depend on the add-on being well enough to notice.

---

## P2 — Retained discovery from removed builds creates permanent ghost entities

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

**Fix:** document the clear procedure (publish a zero-length retained payload to the
discovery topic), and consider shipping a cleanup routine that clears discovery topics for
object-ids the running catalog no longer declares — the existing opt-out branch already does
exactly this for the tracker, so the pattern is established.

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

Also seen in the same log, and worth watching rather than fixing here:

```
WARNING hvac-settings unavailable: err.tech.vcps.ev.hvac-settings.error
  errorCode 502000 "something went wrong"
```

That is a Renault-side 502 on the HVAC settings endpoint, not an add-on fault. If it proves
persistent rather than transient, degrade the affected entities to `unavailable` instead of
logging a warning each cycle.

---

## P2 — `gps_last_activity` measures the car, not the feed, so staleness fires on a parked car

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

**Fix:** derive staleness from **when the add-on last successfully fetched**, not from when
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
