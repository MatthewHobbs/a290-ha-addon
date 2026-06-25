"""Optional dashboard auto-deploy.

When `deploy_dashboard` is `standard` or `bubble`, this reads the chosen dashboard
YAML bundled in the add-on image, rewrites its `/local/...` image references to
jsDelivr CDN URLs served from this same repo (so nothing has to be copied into
`/config/www`), registers a Zen Dots Google-Font CSS resource, and creates the
dashboard via Home Assistant's WebSocket API and pushes its config.

It is **create-once**: if the dashboard url_path already exists it is left alone
(so user edits are never clobbered) unless `redeploy_dashboard` is true. Every
failure here is non-fatal — the data poller runs regardless.
"""
import logging
import os
import re

import aiohttp
import yaml

LOG = logging.getLogger("alpine_a290.deploy")

# The dashboard now ships inside the add-on. The two front-end YAMLs are bundled into the
# image (read from DASHBOARD_DIR); their images load from this same repo via jsDelivr. @main
# is fine here — it's the add-on's *own* repo (same trust domain as the image HA already
# pulls), not a separate third-party source, so there's no cross-repo supply-chain gap.
REPO = "MatthewHobbs/a290-ha-addon"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@main/alpine_a290/dashboards"
FONT_URL = "https://fonts.googleapis.com/css2?family=Zen+Dots&display=swap"
DASHBOARDS = {"standard": "front-end.txt", "bubble": "front-end-bubble.txt"}
# The front-end YAMLs are COPYed here by the Dockerfile; overridable for tests/local runs.
DASHBOARD_DIR = os.environ.get("A290_DASHBOARD_DIR", "/app/dashboards")

# /local/backgrounds/<file> -> repo path (the dashboards flatten everything under
# /local/backgrounds/, but the repo keeps them in typed subfolders).
IMG_MAP = {
    "alpine_a290_background.png": "Images/Background/alpine_a290_background.png",
    "alpine_a290_side.png": "Images/Background/alpine_a290_side.png",
    "alpine_a290_tornado_grey.png": "Images/Background/alpine_a290_tornado_grey.png",
    "card-background.jpg": "Images/Background/card-background.jpg",
    "charge-indicator.png": "Images/Charging/charge-indicator.png",
    "map-marker-black.png": "Images/Map%20Markers/map-marker-black.png",
    "map-marker-grey.png": "Images/Map%20Markers/map-marker-grey.png",
    "map-marker-pop-green.png": "Images/Map%20Markers/map-marker-pop-green.png",
    "map-marker-pop-yellow.png": "Images/Map%20Markers/map-marker-pop-yellow.png",
    "map-marker-white.png": "Images/Map%20Markers/map-marker-white.png",
    "rmap-marker-midnight-blue.png": "Images/Map%20Markers/rmap-marker-midnight-blue.png",
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


async def _fetch_dashboard(style):
    # Bundled in the image now — no network fetch for the YAML; images load via CDN.
    views = yaml.safe_load(_cdnify(_read_dashboard(style)))
    if not isinstance(views, list):
        raise ValueError("dashboard YAML did not parse to a list of views")
    return {"title": "Alpine A290", "views": views}


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
        await self._ws.receive_json()  # auth_required
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
    redeploy = os.environ.get("A290_REDEPLOY_DASHBOARD", "false").strip().lower() in ("true", "1", "on")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("ws://supervisor/core/websocket",
                                           timeout=aiohttp.ClientTimeout(total=30)) as ws:
                api = _WS(session, ws, token)
                await api.auth()
                # Zen Dots font as a global CSS resource (once, shared by both dashboards).
                if not any(r.get("url") == FONT_URL for r in (await api.resources() or [])):
                    await api.create_resource(FONT_URL)
                    LOG.info("Registered Zen Dots font resource")
                for st, path, title in _deploy_targets(style, url_path):
                    await _deploy_one(api, st, path, title, redeploy)
    except Exception as err:  # noqa: BLE001 — deploy must never break the poller
        LOG.warning("Dashboard auto-deploy skipped (%s): %s", type(err).__name__, err)
