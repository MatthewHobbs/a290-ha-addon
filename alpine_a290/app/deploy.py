"""Optional dashboard auto-deploy.

Reads the bundled dashboard YAML, rewrites its /local image refs to jsDelivr CDN URLs
(served from this repo), registers the Zen Dots font, and creates + pushes the dashboard
via HA's WebSocket API. Create-once (left alone unless redeploy_dashboard); never fatal.
"""
import asyncio
import logging
import os
import re

import aiohttp
import yaml

LOG = logging.getLogger("alpine_a290.deploy")

REPO = "MatthewHobbs/a290-ha-addon"
# Pin dashboard assets to this release's git tag (created by release.yaml) so a deployed
# dashboard is reproducible per version; fall back to main for a dev/untagged build.
# (A290_VERSION defaults to "dev"; release.yaml passes the real version as BUILD_VERSION.)
_VERSION = os.environ.get("A290_VERSION", "dev")
_REF = f"v{_VERSION}" if _VERSION not in ("", "dev") else "main"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@{_REF}/alpine_a290/dashboards"
FONT_URL = "https://fonts.googleapis.com/css2?family=Zen+Dots&display=swap"
DASHBOARDS = {"standard": "front-end.txt", "bubble": "front-end-bubble.txt"}
DASHBOARD_DIR = os.environ.get("A290_DASHBOARD_DIR", "/app/dashboards")

# Built-in HA panels / dashboards we must never create-or-overwrite. (A custom dashboard
# url_path must also contain a hyphen, which already excludes most single-word panels —
# this set covers the hyphenated/underscored ones too.)
RESERVED_URL_PATHS = {
    "lovelace", "energy", "map", "logbook", "history", "config", "developer-tools",
    "profile", "todo", "calendar", "media-browser", "default_view", "hassio", "shopping-list",
}


def _validate_url_path(url_path):
    """Return a safe, lowercased dashboard url_path or raise ValueError. Guards against a
    typo'd/hostile value silently overwriting a built-in HA panel via lovelace/config/save."""
    p = url_path.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", p) or "-" not in p:
        raise ValueError(f"invalid dashboard_url_path {url_path!r}: must be lowercase "
                         "letters/digits/'-'/'_', start alphanumeric, and contain a hyphen")
    if p in RESERVED_URL_PATHS:
        raise ValueError(f"dashboard_url_path {url_path!r} is a reserved Home Assistant path")
    return p

IMG_MAP = {
    "alpine_a290_background.webp": "Images/Background/alpine_a290_background.webp",
    "alpine_a290_side.webp": "Images/Background/alpine_a290_side.webp",
    "charge-indicator.png": "Images/Charging/charge-indicator.png",
}


def _cdnify(text):
    """Rewrite /local/backgrounds/<file> -> jsDelivr CDN URL."""
    def repl(m):
        name = m.group(1)
        path = IMG_MAP.get(name)
        if not path:
            LOG.warning("No CDN mapping for /local/backgrounds/%s — left as-is", name)
            return m.group(0)
        return f"{CDN}/{path}"
    return re.sub(r"/local/backgrounds/([\w.\-]+)", repl, text)


def _read_dashboard(style):
    """Read a bundled dashboard YAML from the image (DASHBOARD_DIR)."""
    with open(os.path.join(DASHBOARD_DIR, DASHBOARDS[style]), encoding="utf-8") as fh:
        return fh.read()


# Optional "Smart Charging" controls — the user maps their own EV-charger control entities
# (any integration, e.g. Octopus Intelligent) via the charger_* options; each blank slot is
# skipped. On the standard dashboard these render as a built-in `entities` card; on the
# bubble dashboard they become their own pop-up "tab" of native Bubble Card controls.
_CHARGER_ENTITIES = (
    ("A290_CHARGER_SMART_CHARGE", "Smart Charge"),
    ("A290_CHARGER_BUMP_CHARGE", "Bump Charge"),
    ("A290_CHARGER_TARGET_SOC", "Charge Target"),
    ("A290_CHARGER_TARGET_TIME", "Target Time"),
    ("A290_CHARGER_DISPATCHING", "Off-Peak"),
)
_CHARGER_HASH = "#alpine-charging"
# The heading the standard-dashboard card is inserted directly beneath.
_CHARGER_ANCHOR_HEADING = "Climate/Charging Presets"
# The standard dashboard renders every control as a Mushroom card with a 55px coloured icon
# and Zen Dots styling — these constants mirror that exact look so the Smart Charging controls
# match the Climate/Charging Presets above them (and avoid the light MDC inputs a plain
# entities card would produce for the number/select).
_MUSH_SHAPE = ".shape{--icon-size:55px;--icon-symbol-size:28px;}"
_MUSH_INFO = (".container{--card-secondary-color:#FFFF00;}"
              ".primary{font-family:system-ui,sans-serif !important;font-size:12px !important;"
              "letter-spacing:0.3px;font-weight:400;white-space:normal !important;"
              "overflow:visible !important;text-overflow:clip !important;line-height:1.15;}"
              ".secondary{font-family:system-ui,sans-serif !important;font-size:11px !important;"
              "opacity:0.75;white-space:normal !important;overflow:visible !important;"
              "text-overflow:clip !important;}")
_MUSH_RESET = "ha-card{background:none !important;box-shadow:none !important;border:none !important;}"
_HEADING_STYLE = ('.content{font-family:"Zen Dots",system-ui,sans-serif !important;'
                  'font-size:13px !important;letter-spacing:2px !important;color:#FFFF00 !important;'
                  'border-bottom:1px solid var(--divider-color);padding-bottom:6px;'
                  'text-transform:uppercase;}.content p{white-space:normal !important;'
                  'overflow:visible !important;text-overflow:clip !important;}')
# Bubble Card separator styling, matched to the bundled bubble dashboard's other separators.
_BUBBLE_SEP_STYLE = (
    '.bubble-line{background:#FFFF00 !important;}'
    '.bubble-name{color:#FFFF00 !important;font-family:"Zen Dots",system-ui,sans-serif;'
    'text-transform:uppercase;letter-spacing:2px;}'
    '.bubble-icon-container{color:#FFFF00 !important;}'
)
# Alpine's recommended charge target — drawn as a draggable-to reference line on the slider.
# The marker is positioned by percent assuming a 0–100 number range (the common case); it's a
# visual guide, not a hard stop.
_CHARGE_TARGET_REC = 80
_CHARGE_TARGET_MARKER_STYLE = (
    ".bubble-range-slider::after{content:'';position:absolute;top:6px;bottom:6px;"
    f"left:{_CHARGE_TARGET_REC}%;width:3px;margin-left:-1px;background:#FFD60A;"
    "border-radius:2px;z-index:3;pointer-events:none;}"
)


def _charger_eid(env):
    """Return the configured entity id for a charger slot, or None if blank/null."""
    eid = os.environ.get(env, "").strip()
    return eid if eid and eid.lower() != "null" else None


def _mush_entity(entity, name, icon, icon_css, tap_action=None):
    """A Mushroom entity card styled to match the standard dashboard's preset cards (55px
    coloured icon, Zen Dots), with a per-control icon colour (`icon_css` may be a Jinja
    snippet evaluated by card-mod, e.g. green when on). `tap_action` overrides the default
    (more-info) — e.g. `toggle` for the switches, so they stay one-tap."""
    card = {"type": "custom:mushroom-entity-card", "entity": entity, "name": name, "icon": icon,
            "card_mod": {"style": {
                "mushroom-shape-icon$": _MUSH_SHAPE,
                "mushroom-state-info$": _MUSH_INFO,
                # NB the space after `{` — `{{` would be read as a Jinja expression and break
                # card-mod for the switch icons (whose colour is a `{% … %}` snippet).
                ".": "ha-state-icon{ " + icon_css + " }" + _MUSH_RESET}}}
    if tap_action:
        card["tap_action"] = tap_action
    return card


def _switch_icon_css(entity, on_colour):
    return ("{% if is_state('" + entity + "','on') %}color:" + on_colour
            + ";{% else %}color:grey;opacity:0.4;{% endif %}")


def _charger_cards():
    """Build the standard-dashboard 'Smart Charging' block — a styled heading plus Mushroom
    cards matching the Climate/Charging Presets (so the Charge Target shows as a value, not a
    light MDC box) and the off-peak badge. Returns a list of cards, or None when nothing is
    configured (so it only appears for users who opt in)."""
    smart = _charger_eid("A290_CHARGER_SMART_CHARGE")
    bump = _charger_eid("A290_CHARGER_BUMP_CHARGE")
    soc = _charger_eid("A290_CHARGER_TARGET_SOC")
    ttime = _charger_eid("A290_CHARGER_TARGET_TIME")
    dispatch = _charger_eid("A290_CHARGER_DISPATCHING")
    if not any((smart, bump, soc, ttime, dispatch)):
        return None
    out = [{"type": "heading", "heading": "Smart Charging", "heading_style": "title",
            "card_mod": {"style": _HEADING_STYLE}}]
    toggle = {"action": "toggle"}   # keep the switches one-tap (mushroom defaults to more-info)
    if smart:
        out.append(_mush_entity(smart, "Smart Charge", "mdi:ev-station",
                                _switch_icon_css(smart, "#33BEFA"), tap_action=toggle))
    if bump:
        out.append(_mush_entity(bump, "Bump Charge", "mdi:battery-plus-variant",
                                _switch_icon_css(bump, "orange"), tap_action=toggle))
    if soc:
        out.append(_mush_entity(soc, "Charge Target", "mdi:battery-charging-high", "color:green;"))
    if ttime:
        out.append(_mush_entity(ttime, "Target Time", "mdi:clock-outline", "color:#FFFF00;"))
    if dispatch:
        out.append(_offpeak_badge(dispatch, preset_style=True))
    return out


def _toggle_card(entity, name, icon, on_colour):
    """A compact dark-pill toggle that matches the dashboard's other command buttons: the icon
    colours up when the switch is on and dims when off (Bubble Card evaluates `${...}` in
    `styles`), and a tap toggles it."""
    return {"type": "custom:bubble-card", "card_type": "button", "button_type": "name",
            "entity": entity, "name": name, "icon": icon,
            "button_action": {"tap_action": {"action": "toggle"}},
            "styles": (".bubble-icon-container{color:${state === 'on' ? '"
                       + on_colour + "' : '#777'} !important;}")}


def _offpeak_badge(entity, *, preset_style=False):
    """Off-peak rate badge. A Mushroom template card (full Jinja) so it can show BOTH the live
    rate and the cheap-window times: green "Off-peak" / red "Peak" for the current rate, with
    the off-peak window (24-hour) as the sub-line. `preset_style` matches the standard
    dashboard's preset cards (55px icon + small wrapping text, so it fits the 2-up grid)."""
    def fill(s):
        return s.replace("@E@", entity)
    reset = fill("ha-card{background:none !important;box-shadow:none !important;"
                 "border:none !important;width:100% !important;"
                 "--card-primary-color:{% if is_state('@E@','on') %}#5BE36A"
                 "{% else %}#FF6B6B{% endif %};}")
    # On the standard dashboard the badge sits in a narrow 2-up cell; we keep its label short
    # ("Off-peak"/"Peak rate") so it fits, since Mushroom renders the template card's text in
    # an inner shadow root that card-mod text rules here can't reach.
    style = ({"mushroom-shape-icon$": _MUSH_SHAPE, ".": reset} if preset_style else reset)
    return {
        "type": "custom:mushroom-template-card",
        "entity": entity,
        "fill_container": True,
        "multiline_secondary": True,
        "icon": fill("{% if is_state('@E@','on') %}mdi:leaf{% else %}mdi:flash-alert{% endif %}"),
        "icon_color": fill("{% if is_state('@E@','on') %}green{% else %}red{% endif %}"),
        # "Now:" prefix on the roomy bubble pop-up; just the rate in the narrow standard cell.
        "primary": fill(("" if preset_style else "Now: ")
                        + "{% if is_state('@E@','on') %}Off-peak{% else %}Peak rate{% endif %}"),
        "secondary": fill(
            "{% set s = state_attr('@E@','current_start') or state_attr('@E@','next_start') %}"
            "{% set e = state_attr('@E@','current_end') or state_attr('@E@','next_end') %}"
            "{% if s and e %}Off-peak {{ as_timestamp(s)|timestamp_custom('%H:%M', true) }}"
            "–{{ as_timestamp(e)|timestamp_custom('%H:%M', true) }}{% endif %}"),
        "tap_action": {"action": "more-info"},
        "card_mod": {"style": style},
    }


def _charger_popup():
    """Build the bubble-dashboard 'Smart Charging' pop-up of native Bubble Card controls:
    smart/bump-charge as compact toggles on one line, a charge-target slider (with the live %
    and an 80% recommendation marker), a target-time dropdown, and an Octopus off-peak badge.
    Returns None when no charger entities are configured."""
    smart = _charger_eid("A290_CHARGER_SMART_CHARGE")
    bump = _charger_eid("A290_CHARGER_BUMP_CHARGE")
    soc = _charger_eid("A290_CHARGER_TARGET_SOC")
    ttime = _charger_eid("A290_CHARGER_TARGET_TIME")
    dispatch = _charger_eid("A290_CHARGER_DISPATCHING")
    if not any((smart, bump, soc, ttime, dispatch)):
        return None
    cards = [{"type": "custom:bubble-card", "card_type": "separator",
              "name": "Smart Charging", "icon": "mdi:ev-station",
              "styles": _BUBBLE_SEP_STYLE}]
    # Smart + Bump as compact toggles side-by-side; a lone one stays on its own row.
    toggles = []
    if smart:
        toggles.append(_toggle_card(smart, "Smart Charge", "mdi:ev-station", "#33BEFA"))
    if bump:
        toggles.append(_toggle_card(bump, "Bump Charge", "mdi:battery-plus-variant", "orange"))
    if len(toggles) == 2:
        cards.append({"type": "horizontal-stack", "cards": toggles})
    elif toggles:
        cards.append(toggles[0])
    if soc:
        cards.append({"type": "custom:bubble-card", "card_type": "button",
                      "button_type": "slider", "entity": soc, "name": "Charge Target",
                      "icon": "mdi:battery-charging-high", "show_state": True,
                      "styles": _CHARGE_TARGET_MARKER_STYLE})
    if ttime:
        cards.append({"type": "custom:bubble-card", "card_type": "select",
                      "entity": ttime, "name": "Target Time", "icon": "mdi:clock-outline",
                      "show_state": True})
    if dispatch:
        cards.append(_offpeak_badge(dispatch))
    return {"type": "custom:bubble-card", "card_type": "pop-up", "hash": _CHARGER_HASH,
            "name": "Smart Charging", "icon": "mdi:ev-station", "button_type": "name",
            "bg_color": "#14171b", "bg_opacity": "85", "bg_blur": "6",
            "shadow_opacity": "0", "cards": cards}


async def _fetch_dashboard(style):
    views = yaml.safe_load(_cdnify(_read_dashboard(style)))
    if not isinstance(views, list):
        raise ValueError("dashboard YAML did not parse to a list of views")
    view = views[0] if views and isinstance(views[0], dict) else None
    if view is not None:
        if style == "bubble":
            _inject_bubble_charging(view)
        else:
            new_cards = _charger_cards()
            if new_cards:
                _add_cards(view, new_cards)
    return {"title": "Alpine A290", "views": views}


def _add_cards(view, new_cards):
    """Insert the Smart Charging cards into a view. On the `sections` layout (standard
    dashboard) they go directly beneath the Climate/Charging Presets block (before the next
    heading); otherwise they're appended to the view's `cards`."""
    sections = view.get("sections")
    if isinstance(sections, list):
        for sec in sections:
            cards = sec.get("cards")
            if not isinstance(cards, list):
                continue
            hi = next((i for i, c in enumerate(cards)
                       if isinstance(c, dict) and c.get("type") == "heading"
                       and c.get("heading") == _CHARGER_ANCHOR_HEADING), None)
            if hi is None:
                continue
            nxt = next((j for j in range(hi + 1, len(cards))
                        if isinstance(cards[j], dict) and cards[j].get("type") == "heading"),
                       len(cards))
            cards[nxt:nxt] = new_cards
            return
        view["sections"].append({"type": "grid", "cards": new_cards})
    else:
        view.setdefault("cards", []).extend(new_cards)


def _grid_rows(buttons):
    """Pair buttons into 2-up horizontal-stacks; a trailing odd button spans full width."""
    rows = []
    for i in range(0, len(buttons), 2):
        pair = buttons[i:i + 2]
        rows.append({"type": "horizontal-stack", "cards": pair} if len(pair) == 2
                    else pair[0])
    return rows


def _inject_bubble_charging(view):
    """Add the Smart Charging pop-up + a main-menu button to the bubble dashboard, and move
    the Location button to a full-width row at the bottom of the menu. No-op when no charger
    entities are configured (the menu is left exactly as bundled)."""
    popup = _charger_popup()
    if popup is None:
        return
    menu = next((c for c in view.get("cards", [])
                 if isinstance(c, dict) and c.get("hash") == "#alpine"), None)
    if menu is None:
        return
    buttons = []
    for item in menu.get("cards", []):
        if isinstance(item, dict) and item.get("type") == "horizontal-stack":
            buttons.extend(item.get("cards", []))
        else:
            buttons.append(item)
    location = next((b for b in buttons
                     if isinstance(b, dict) and b.get("name") == "Location"), None)
    rest = [b for b in buttons if b is not location]
    btn = {"type": "custom:bubble-card", "card_type": "button", "button_type": "name",
           "name": "Smart Charging", "icon": "mdi:ev-station",
           "button_action": {"tap_action": {"action": "navigate",
                                             "navigation_path": _CHARGER_HASH}}}
    idx = next((i for i, b in enumerate(rest)
                if isinstance(b, dict) and b.get("name") == "Charge Status"), len(rest) - 1)
    rest.insert(idx + 1, btn)
    rows = _grid_rows(rest)
    if location is not None:
        rows.append(location)
    menu["cards"] = rows
    view.setdefault("cards", []).append(popup)


class _WS:
    """Minimal Home Assistant WebSocket API client over the Supervisor proxy."""

    def __init__(self, session, ws, token):
        self._ws, self._token, self._id = ws, token, 0

    async def _cmd(self, **payload):
        self._id += 1
        payload["id"] = self._id
        await self._ws.send_json(payload)
        while True:
            # Per-receive timeout so an unexpected event/close frame can't hang the deploy.
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=15)
            if msg.get("id") == self._id and msg.get("type") == "result":
                if not msg.get("success", False):
                    raise RuntimeError(f"{payload['type']} failed: {msg.get('error')}")
                return msg.get("result")

    async def auth(self):
        await self._ws.receive_json()
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        if (await self._ws.receive_json()).get("type") != "auth_ok":
            raise RuntimeError("HA WebSocket auth failed")

    async def dashboards(self):
        return await self._cmd(type="lovelace/dashboards/list")

    async def create_dashboard(self, url_path, title):
        return await self._cmd(type="lovelace/dashboards/create", url_path=url_path,
                               title=title, icon="mdi:car-sports", mode="storage",
                               show_in_sidebar=True, require_admin=False)

    async def save_config(self, url_path, config):
        return await self._cmd(type="lovelace/config/save", url_path=url_path, config=config)

    async def resources(self):
        return await self._cmd(type="lovelace/resources")

    async def create_resource(self, url, res_type="css"):
        return await self._cmd(type="lovelace/resources/create", url=url, res_type=res_type)


def _deploy_targets(style, url_path):
    """(style, url_path, title) to deploy for the chosen deploy_dashboard option. 'both'
    installs the standard dashboard at url_path and the bubble one at '<url_path>-bubble'."""
    if style == "both":
        return [("standard", url_path, "Alpine A290"),
                ("bubble", f"{url_path}-bubble", "Alpine A290 (Bubble)")]
    return [(style, url_path, "Alpine A290")]


async def _deploy_one(api, style, url_path, title, redeploy):
    """Create-once (or overwrite when redeploy) a single dashboard."""
    config = await _fetch_dashboard(style)
    existing = {d.get("url_path") for d in (await api.dashboards() or [])}
    if url_path in existing and not redeploy:
        LOG.info("Dashboard '%s' already exists — leaving it (set redeploy_dashboard to "
                 "overwrite). %d views available.", url_path, len(config["views"]))
        return
    if url_path not in existing:
        await api.create_dashboard(url_path, title)
        LOG.info("Created dashboard '%s'", url_path)
    await api.save_config(url_path, config)
    LOG.info("Deployed '%s' dashboard to '%s' (%d views, CDN assets)",
             style, url_path, len(config["views"]))


async def run_deploy():
    style = os.environ.get("A290_DEPLOY_DASHBOARD", "none").strip().lower()
    if style in ("", "none"):
        return
    if style not in (set(DASHBOARDS) | {"both"}):
        LOG.warning("deploy_dashboard=%r not recognised; skipping", style)
        return
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        LOG.warning("No SUPERVISOR_TOKEN (set homeassistant_api: true); skipping dashboard deploy")
        return
    url_path = os.environ.get("A290_DASHBOARD_URL_PATH", "alpine-a290").strip() or "alpine-a290"
    try:
        url_path = _validate_url_path(url_path)
    except ValueError as err:
        LOG.warning("Dashboard auto-deploy skipped — %s", err)
        return
    redeploy = os.environ.get("A290_REDEPLOY_DASHBOARD", "false").strip().lower() in ("true", "1", "on")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("ws://supervisor/core/websocket",
                                           timeout=aiohttp.ClientTimeout(total=30)) as ws:
                api = _WS(session, ws, token)
                await api.auth()
                if not any(r.get("url") == FONT_URL for r in (await api.resources() or [])):
                    await api.create_resource(FONT_URL)
                    LOG.info("Registered Zen Dots font resource")
                for st, path, title in _deploy_targets(style, url_path):
                    await _deploy_one(api, st, path, title, redeploy)
    except Exception as err:  # noqa: BLE001 — deploy must never break the poller
        LOG.warning("Dashboard auto-deploy skipped (%s): %s", type(err).__name__, err)
