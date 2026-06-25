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


def _run_deploy_with(monkeypatch, *, existing, redeploy):
    fake = FakeWS(existing)

    async def fake_fetch(style):
        return {"title": "Alpine A290", "views": [{"cards": []}]}

    monkeypatch.setattr(deploy, "_fetch_dashboard", fake_fetch)
    monkeypatch.setattr(deploy, "_WS", lambda session, ws, token: fake)
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", "standard")
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
