<!-- renault-api-watch:__LATEST__ -->

**Pinned:** `__PINNED__` — **latest upstream:** `__LATEST__`
https://github.com/hacf-fr/renault-api/releases/tag/v__LATEST__

This is a notification, not a request to bump. `renault-api` is excluded from both dependency
bots on purpose: per-model endpoint support is hard-coded in
`renault_api/kamereon/models.py` → `_VEHICLE_ENDPOINTS`, so a bump changes runtime behaviour and
needs a container-verified review.

### What to check before bumping

- Diff the `A5E1AE` entry in `_VEHICLE_ENDPOINTS`. That map, not the readthedocs pages, is the
  authoritative source for what this car exposes.
- **0.5.14 or later carries [#2254](https://github.com/hacf-fr/renault-api/pull/2254) and
  [#2255](https://github.com/hacf-fr/renault-api/pull/2255)** (both merged 2026-09-08), which add
  `actions/refresh-location` and set `hvac-settings` to `None` for `A5E1AE`. Once a release carries
  them, review whether the add-on's own guards can go: `PESSIMISTIC_ENDPOINTS` in `catalog.py`, and
  the v1.23.1 `hvac-settings` circuit breaker. The breaker guards a **Renault server 502**, so
  confirm the behaviour on the car before removing it — a library table fix does not stop the
  server answering `502000`.
- Bump the pin in **both** `requirements.in` and `requirements.txt`. Nothing in CI compares the
  pair, so missing one is silent until the next regeneration reverts it.
- Mirror to `r5-ha-addon` only after this lands, per the a290-first rule.
