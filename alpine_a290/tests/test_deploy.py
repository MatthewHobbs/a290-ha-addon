"""Tests for the optional dashboard auto-deploy.

Covers the pure CDN URL rewrite and the create-once-vs-redeploy decision — two opposite
failure modes (no dashboard on first install / silently clobbering edits) from one branch.
"""
import asyncio

import deploy


# --------------------------------------------------------------------------- #
# _cdnify — pure URL rewrite
# --------------------------------------------------------------------------- #
def test_cdnify_maps_known_image_to_cdn():
    out = deploy._cdnify("image: /local/backgrounds/alpine_a290_background.png")
    assert "cdn.jsdelivr.net" in out
    assert "/local/backgrounds/" not in out


def test_cdnify_leaves_unmapped_image_untouched():
    s = "image: /local/backgrounds/not_in_the_map.png"
    assert deploy._cdnify(s) == s   # unmapped -> left as-is (logged a warning)


def test_cdnify_points_at_the_addon_repo():
    out = deploy._cdnify("image: /local/backgrounds/alpine_a290_background.png")
    assert "a290-ha-addon" in out and "alpine_a290/dashboards" in out


def test_fetch_dashboard_reads_bundled_yaml_and_cdnifies(tmp_path, monkeypatch):
    import asyncio

    (tmp_path / "front-end.txt").write_text(
        "- title: Home\n"
        "  cards:\n"
        "    - type: picture\n"
        "      image: /local/backgrounds/alpine_a290_background.png\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))   # read the bundled file
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    assert cfg["title"] == "Alpine A290"
    assert isinstance(cfg["views"], list)
    img = cfg["views"][0]["cards"][0]["image"]
    assert "cdn.jsdelivr.net" in img and "/local/backgrounds/" not in img


# --------------------------------------------------------------------------- #
# run_deploy — create-once vs redeploy
# --------------------------------------------------------------------------- #
class FakeWS:
    """Stand-in for deploy._WS; records the mutating calls."""

    def __init__(self, existing):
        self._existing = existing
        self.created, self.saved = [], []

    async def auth(self):
        pass

    async def resources(self):
        return []

    async def create_resource(self, url, res_type="css"):
        pass

    async def dashboards(self):
        return [{"url_path": p} for p in self._existing]

    async def create_dashboard(self, url_path, title):
        self.created.append(url_path)

    async def save_config(self, url_path, config):
        self.saved.append(url_path)


class _FakeWSConn:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def ws_connect(self, *a, **k):
        return _FakeWSConn()


def _run_deploy_with(monkeypatch, *, existing, redeploy, style="standard"):
    fake = FakeWS(existing)

    async def fake_fetch(style):
        return {"title": "Alpine A290", "views": [{"cards": []}]}

    monkeypatch.setattr(deploy, "_fetch_dashboard", fake_fetch)
    monkeypatch.setattr(deploy, "_WS", lambda session, ws, token: fake)
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", style)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    monkeypatch.setenv("A290_DASHBOARD_URL_PATH", "alpine-a290")
    monkeypatch.setenv("A290_REDEPLOY_DASHBOARD", "true" if redeploy else "false")
    asyncio.run(deploy.run_deploy())
    return fake


def test_run_deploy_creates_and_saves_when_absent(monkeypatch):
    fake = _run_deploy_with(monkeypatch, existing=[], redeploy=False)
    assert fake.created == ["alpine-a290"]
    assert fake.saved == ["alpine-a290"]


def test_run_deploy_leaves_existing_dashboard_untouched(monkeypatch):
    fake = _run_deploy_with(monkeypatch, existing=["alpine-a290"], redeploy=False)
    assert fake.created == []
    assert fake.saved == []           # create-once: edits are never clobbered


def test_run_deploy_overwrites_existing_when_redeploy_true(monkeypatch):
    fake = _run_deploy_with(monkeypatch, existing=["alpine-a290"], redeploy=True)
    assert fake.created == []          # already exists -> not re-created
    assert fake.saved == ["alpine-a290"]   # ...but config is pushed


def test_run_deploy_both_installs_standard_and_bubble(monkeypatch):
    fake = _run_deploy_with(monkeypatch, existing=[], redeploy=False, style="both")
    assert fake.created == ["alpine-a290", "alpine-a290-bubble"]
    assert fake.saved == ["alpine-a290", "alpine-a290-bubble"]


# --------------------------------------------------------------------------- #
# run_deploy early-exit branches
# --------------------------------------------------------------------------- #
def test_run_deploy_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", "none")
    asyncio.run(deploy.run_deploy())   # returns immediately


def test_run_deploy_warns_on_unknown_style(monkeypatch):
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", "fancy")
    asyncio.run(deploy.run_deploy())


def test_run_deploy_skips_without_supervisor_token(monkeypatch):
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", "standard")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    asyncio.run(deploy.run_deploy())


# --------------------------------------------------------------------------- #
# _WS WebSocket client (auth + every command + failure)
# --------------------------------------------------------------------------- #
class _FakeOKWS:
    """Replies to every command with a matching successful result."""

    def __init__(self):
        self.sent, self.n = [], 0

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_json(self):
        self.n += 1
        return {"id": self.n, "type": "result", "success": True, "result": None}


def test_ws_auth_then_commands():
    class WS(_FakeOKWS):
        async def receive_json(self):
            # auth() consumes two frames before any command id-matching
            if self.n == 0 and not getattr(self, "_authed", False):
                self._authed = True
                return {"type": "auth_required"}
            if getattr(self, "_authed", False) and not getattr(self, "_ok", False):
                self._ok = True
                return {"type": "auth_ok"}
            return await super().receive_json()

    async def scenario():
        api = deploy._WS(None, WS(), "token")
        await api.auth()
        await api.dashboards()
        await api.create_dashboard("p", "T")
        await api.save_config("p", {"views": []})
        await api.resources()
        await api.create_resource("https://font")

    asyncio.run(scenario())


def test_ws_raises_on_failed_result():
    import pytest

    class WS:
        async def send_json(self, payload):
            pass

        async def receive_json(self):
            return {"id": 1, "type": "result", "success": False, "error": "denied"}

    async def scenario():
        api = deploy._WS(None, WS(), "token")
        with pytest.raises(RuntimeError):
            await api.dashboards()

    asyncio.run(scenario())
