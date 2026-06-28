"""Optional dashboard auto-deploy.

Reads the bundled dashboard YAML, rewrites its /local image refs to jsDelivr CDN URLs
(served from this repo), registers the Zen Dots font, and creates + pushes the dashboard
via HA's WebSocket API. Create-once (left alone unless redeploy_dashboard); never fatal.
"""
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


# Optional "Smart Charging" card — the user maps their own EV-charger control entities (any
# integration, e.g. Octopus Intelligent) via the charger_* options; each blank slot is
# skipped. It's a plain built-in `entities` card (no extra HACS card needed).
_CHARGER_ENTITIES = (
    ("A290_CHARGER_SMART_CHARGE", "Smart Charge"),
    ("A290_CHARGER_BUMP_CHARGE", "Bump Charge"),
    ("A290_CHARGER_TARGET_SOC", "Charge Target"),
    ("A290_CHARGER_TARGET_TIME", "Target Time"),
)


def _charger_card():
    """Build a 'Smart Charging' entities card from the configured charger entities, or None
    when none are set (so the card only appears for users who opt in)."""
    rows = []
    for env, name in _CHARGER_ENTITIES:
        eid = os.environ.get(env, "").strip()
        if eid and eid.lower() != "null":
            rows.append({"entity": eid, "name": name})
    if not rows:
        return None
    return {"type": "entities", "title": "Smart Charging",
            "show_header_toggle": False, "entities": rows}


async def _fetch_dashboard(style):
    views = yaml.safe_load(_cdnify(_read_dashboard(style)))
    if not isinstance(views, list):
        raise ValueError("dashboard YAML did not parse to a list of views")
    card = _charger_card()
    if card and views and isinstance(views[0], dict):
        _add_card(views[0], card)
    return {"title": "Alpine A290", "views": views}


def _add_card(view, card):
    """Add a card to a view, supporting both the `sections` layout (the standard dashboard)
    and the plain `cards` layout (the bubble dashboard)."""
    if isinstance(view.get("sections"), list):
        view["sections"].append({"type": "grid", "cards": [card]})
    else:
        view.setdefault("cards", []).append(card)


class _WS:
    """Minimal Home Assistant WebSocket API client over the Supervisor proxy."""

    def __init__(self, session, ws, token):
        self._ws, self._token, self._id = ws, token, 0

    async def _cmd(self, **payload):
        self._id += 1
        payload["id"] = self._id
        await self._ws.send_json(payload)
        while True:
            msg = await self._ws.receive_json()
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
