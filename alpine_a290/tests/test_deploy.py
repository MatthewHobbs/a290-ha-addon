"""Tests for the optional dashboard auto-deploy.

Covers the pure CDN URL rewrite and the create-once-vs-redeploy decision — two opposite
failure modes (no dashboard on first install / silently clobbering edits) from one branch.
"""
import asyncio

import deploy
import pytest


# --------------------------------------------------------------------------- #
# _cdnify — pure URL rewrite
# --------------------------------------------------------------------------- #
def test_cdnify_maps_known_image_to_cdn():
    out = deploy._cdnify("image: /local/backgrounds/alpine_a290_background.webp")
    assert "cdn.jsdelivr.net" in out
    assert "/local/backgrounds/" not in out


def test_cdnify_leaves_unmapped_image_untouched():
    s = "image: /local/backgrounds/not_in_the_map.png"
    assert deploy._cdnify(s) == s   # unmapped -> left as-is (logged a warning)


def test_cdnify_points_at_the_addon_repo():
    out = deploy._cdnify("image: /local/backgrounds/alpine_a290_background.webp")
    assert "a290-ha-addon" in out and "alpine_a290/dashboards" in out


def test_fetch_dashboard_reads_bundled_yaml_and_cdnifies(tmp_path, monkeypatch):
    import asyncio

    (tmp_path / "front-end.txt").write_text(
        "- title: Home\n"
        "  cards:\n"
        "    - type: picture\n"
        "      image: /local/backgrounds/alpine_a290_background.webp\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))   # read the bundled file
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    assert cfg["title"] == "Alpine A290"
    assert isinstance(cfg["views"], list)
    img = cfg["views"][0]["cards"][0]["image"]
    assert "cdn.jsdelivr.net" in img and "/local/backgrounds/" not in img


# --------------------------------------------------------------------------- #
# _charger_card — optional "Smart Charging" section
# --------------------------------------------------------------------------- #
def test_charger_card_none_when_no_entities_set(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    assert deploy._charger_card() is None


def test_charger_card_skips_blank_and_null(monkeypatch):
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.octopus_intelligent_smart_charge")
    monkeypatch.setenv("A290_CHARGER_BUMP_CHARGE", "")        # blank -> skipped
    monkeypatch.setenv("A290_CHARGER_TARGET_SOC", "null")     # bashio empty -> skipped
    monkeypatch.setenv("A290_CHARGER_TARGET_TIME", "select.octopus_intelligent_target_time")
    card = deploy._charger_card()
    assert card["type"] == "entities" and card["title"] == "Smart Charging"
    names = [r["name"] for r in card["entities"]]
    ents = [r["entity"] for r in card["entities"]]
    assert names == ["Smart Charge", "Target Time"]          # only the two set, in order
    assert "switch.octopus_intelligent_smart_charge" in ents


def test_fetch_dashboard_appends_charger_card_when_configured(tmp_path, monkeypatch):
    (tmp_path / "front-end.txt").write_text("- title: Home\n  cards: []\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.x")
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    last = cfg["views"][0]["cards"][-1]
    assert last["type"] == "entities" and last["title"] == "Smart Charging"


def test_fetch_dashboard_no_charger_card_when_unset(tmp_path, monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    (tmp_path / "front-end.txt").write_text("- title: Home\n  cards: []\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    assert cfg["views"][0]["cards"] == []                    # nothing appended


def test_add_card_sections_layout():
    # the standard dashboard is a `sections` view — the card must land in a new grid section
    view = {"type": "sections", "sections": [{"type": "grid", "cards": []}]}
    deploy._add_card(view, {"type": "entities", "title": "Smart Charging"})
    assert view["sections"][-1] == {"type": "grid", "cards": [{"type": "entities", "title": "Smart Charging"}]}


def test_add_card_cards_layout():
    # the bubble dashboard is a plain `cards` view — the card is appended to cards
    view = {"cards": [{"type": "x"}]}
    deploy._add_card(view, {"type": "entities", "title": "Smart Charging"})
    assert view["cards"][-1]["title"] == "Smart Charging"


def test_charger_card_is_dark_styled(monkeypatch):
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.x")
    card = deploy._charger_card()
    assert "background: none" in card["card_mod"]["style"]         # transparent over dark panel


def test_charger_card_target_time_is_plain_row(monkeypatch):
    # the target-time select is shown as a plain value row (not a light inline MDC dropdown)
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.smart")
    monkeypatch.setenv("A290_CHARGER_TARGET_TIME", "select.ttime")
    rows = {r["entity"]: r for r in deploy._charger_card()["entities"]}
    assert rows["select.ttime"].get("type") == "simple-entity"
    assert "type" not in rows["switch.smart"]                      # switches stay interactive


def test_add_card_inserts_beneath_presets_heading():
    # standard dashboard: the card goes directly after the Climate/Charging Presets block —
    # i.e. immediately before the next heading, not at the end of the section.
    view = {"type": "sections", "sections": [{"type": "grid", "cards": [
        {"type": "heading", "heading": "Climate/Charging Presets"},
        {"type": "tile", "entity": "x"},
        {"type": "heading", "heading": "Last Charge"},
        {"type": "tile", "entity": "y"},
    ]}]}
    card = {"type": "entities", "title": "Smart Charging"}
    deploy._add_card(view, card)
    cards = view["sections"][0]["cards"]
    assert cards[2] is card                       # inserted before the "Last Charge" heading
    assert cards[3]["heading"] == "Last Charge"


def test_add_card_appends_new_section_when_no_anchor():
    view = {"type": "sections", "sections": [{"type": "grid", "cards": [
        {"type": "heading", "heading": "Something Else"}]}]}
    deploy._add_card(view, {"type": "entities", "title": "Smart Charging"})
    assert view["sections"][-1]["cards"][0]["title"] == "Smart Charging"


# --------------------------------------------------------------------------- #
# bubble dashboard — Smart Charging pop-up + main-menu restructure
# --------------------------------------------------------------------------- #
def test_charger_popup_none_when_unset(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    assert deploy._charger_popup() is None


def _flat_popup_cards(pop):
    out = {}
    for c in pop["cards"]:
        for inner in (c["cards"] if c.get("type") == "horizontal-stack" else [c]):
            if "entity" in inner:
                out[inner["entity"]] = inner
    return out


def test_charger_popup_builds_native_controls(monkeypatch):
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.smart")
    monkeypatch.setenv("A290_CHARGER_BUMP_CHARGE", "switch.bump")
    monkeypatch.setenv("A290_CHARGER_TARGET_SOC", "number.soc")
    monkeypatch.setenv("A290_CHARGER_TARGET_TIME", "select.ttime")
    monkeypatch.setenv("A290_CHARGER_DISPATCHING", "binary_sensor.disp")
    pop = deploy._charger_popup()
    assert pop["card_type"] == "pop-up" and pop["hash"] == deploy._CHARGER_HASH
    # smart + bump share one horizontal-stack row (compact toggles)
    assert any(c.get("type") == "horizontal-stack" and len(c["cards"]) == 2 for c in pop["cards"])
    by_entity = _flat_popup_cards(pop)
    # toggles match the dashboard's other command buttons (dark pill + icon, not a blue fill)
    assert by_entity["switch.smart"]["button_type"] == "name"
    assert by_entity["switch.smart"]["button_action"]["tap_action"]["action"] == "toggle"
    assert by_entity["number.soc"]["button_type"] == "slider"       # charge target slider
    assert by_entity["number.soc"]["show_state"] is True            # shows the %
    assert "FFD60A" in by_entity["number.soc"]["styles"]            # 80% recommendation marker
    assert by_entity["select.ttime"]["card_type"] == "select"       # target time dropdown
    # off-peak badge: a Mushroom template card showing the current rate + the window times
    badge = next(c for c in pop["cards"] if c.get("type") == "custom:mushroom-template-card")
    assert "Off-peak" in badge["primary"] and "Peak rate" in badge["primary"]
    assert "next_start" in badge["secondary"] and "%H:%M" in badge["secondary"]


def _flat_menu_names(menu):
    out = []
    for item in menu["cards"]:
        if item.get("type") == "horizontal-stack":
            out.extend(c["name"] for c in item["cards"])
        else:
            out.append(item["name"])
    return out


def _bubble_menu_view():
    def btn(name):
        return {"type": "custom:bubble-card", "card_type": "button", "button_type": "name",
                "name": name}
    menu = {"type": "custom:bubble-card", "card_type": "pop-up", "hash": "#alpine", "cards": [
        {"type": "horizontal-stack", "cards": [btn("Vehicle Status"), btn("Charge Status")]},
        {"type": "horizontal-stack", "cards": [btn("Activity"), btn("Last Charge")]},
        btn("Diagnostics"),
        btn("Location"),
    ]}
    return {"cards": [menu]}


def test_inject_bubble_charging_button_popup_and_location_full_width(monkeypatch):
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.smart")
    view = _bubble_menu_view()
    deploy._inject_bubble_charging(view)
    assert any(c.get("hash") == deploy._CHARGER_HASH for c in view["cards"])   # pop-up added
    menu = view["cards"][0]
    assert "Smart Charging" in _flat_menu_names(menu)                          # menu button
    assert menu["cards"][-1]["name"] == "Location"                            # last item
    assert menu["cards"][-1]["type"] == "custom:bubble-card"                  # full-width btn


def test_inject_bubble_charging_noop_when_unset(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    view = _bubble_menu_view()
    deploy._inject_bubble_charging(view)
    assert len(view["cards"]) == 1                                            # no pop-up
    assert "Smart Charging" not in _flat_menu_names(view["cards"][0])         # menu untouched


def test_fetch_dashboard_bubble_injects_popup(tmp_path, monkeypatch):
    (tmp_path / "front-end-bubble.txt").write_text(
        "- title: Alpine\n"
        "  cards:\n"
        "    - type: custom:bubble-card\n"
        "      card_type: pop-up\n"
        "      hash: '#alpine'\n"
        "      cards:\n"
        "        - type: horizontal-stack\n"
        "          cards:\n"
        "            - {type: custom:bubble-card, card_type: button, name: Charge Status}\n"
        "            - {type: custom:bubble-card, card_type: button, name: Location}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    monkeypatch.setenv("A290_CHARGER_SMART_CHARGE", "switch.smart")
    cfg = asyncio.run(deploy._fetch_dashboard("bubble"))
    assert any(c.get("hash") == deploy._CHARGER_HASH for c in cfg["views"][0]["cards"])


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
# dashboard_url_path validation — never overwrite a built-in HA panel
# --------------------------------------------------------------------------- #
def test_validate_url_path_normalises_valid():
    assert deploy._validate_url_path("  Alpine-A290  ") == "alpine-a290"


@pytest.mark.parametrize("reserved", ["developer-tools", "media-browser", "shopping-list"])
def test_validate_url_path_rejects_reserved(reserved):
    with pytest.raises(ValueError):
        deploy._validate_url_path(reserved)


@pytest.mark.parametrize("bad", ["", "alpine_a290", "energy", "no space", "-leading", "UPPER"])
def test_validate_url_path_rejects_invalid(bad):
    # no hyphen ("energy", "alpine_a290", "UPPER"), bad charset ("no space", "-leading"), empty
    with pytest.raises(ValueError):
        deploy._validate_url_path(bad)


def test_run_deploy_skips_reserved_url_path(monkeypatch):
    # Reaches validation (token present) and returns before any WS connection / save.
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", "standard")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    monkeypatch.setenv("A290_DASHBOARD_URL_PATH", "energy")
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


def test_ws_auth_failure_raises():
    import pytest

    class WS:
        def __init__(self):
            self.n = 0

        async def send_json(self, payload):
            pass

        async def receive_json(self):
            self.n += 1
            return {"type": "auth_required"} if self.n == 1 else {"type": "auth_invalid"}

    async def scenario():
        api = deploy._WS(None, WS(), "token")
        with pytest.raises(RuntimeError):
            await api.auth()

    asyncio.run(scenario())


def test_fetch_dashboard_rejects_non_list(tmp_path, monkeypatch):
    import pytest
    (tmp_path / "front-end.txt").write_text("just_a_string", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        asyncio.run(deploy._fetch_dashboard("standard"))


def test_run_deploy_swallows_connection_errors(monkeypatch):
    class BadSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def ws_connect(self, *a, **k):
            raise RuntimeError("ws connect failed")

    monkeypatch.setattr(deploy.aiohttp, "ClientSession", BadSession)
    monkeypatch.setenv("A290_DEPLOY_DASHBOARD", "standard")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    asyncio.run(deploy.run_deploy())   # exception caught + logged, never raised
