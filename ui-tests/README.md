# Dashboard UI tests — responsive / truncation

Renders the bundled dashboards (`alpine_a290/dashboards/front-end.txt` and
`front-end-bubble.txt`) in a **real Home Assistant** across the top mobile device sizes and
**fails on any text truncation or broken card**. This is what catches regressions like the
Mushroom tile labels clipping on a phone.

## How it works

1. `run.sh` boots a throwaway Home Assistant container, vendors the custom cards
   (Mushroom, Button Card, card-mod into `www/`; Bubble Card from jsDelivr), and completes
   onboarding to get an API token.
2. `seed.py` gives every entity the dashboards reference a representative state via the REST
   `/api/states` API — no MQTT or add-on needed, since the cards read `hass.states`
   directly — then registers the card resources and creates the two dashboards from the
   bundled YAML.
3. `check_overflow.py` (Playwright) loads each dashboard at every viewport in `devices.json`,
   waits for the cards and the Zen Dots font, then walks the **shadow-DOM-pierced** tree for
   any text element that is clipped (`text-overflow:ellipsis` / `nowrap`+`overflow:hidden`
   with `scrollWidth > clientWidth`) or any `hui-error-card`. A screenshot is saved per
   device; the run exits non-zero with a report if anything is clipped.

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
