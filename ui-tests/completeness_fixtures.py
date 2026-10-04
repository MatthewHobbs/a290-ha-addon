#!/usr/bin/env python3
"""The isolated proof of ADR 0003 row 6: every case there, missed by the gate before rows 1 to 4
and caught after them, on static pages and Playwright, no Home Assistant.

Each page is shaped the way Bubble 3.4.1 lays a pop-up out (a `bubble-card` host carrying the
hash, a `.bubble-pop-up.is-popup-opened` shell with a `.bubble-header-container` and a
`.bubble-cards-container` of `.card` wrappers) and inserts its late card 3 s after the hash opens
the pop-up, the delay #171 round 4 proved: `_stable_issues` waits ~1 s for card-mod quiet first,
so a shorter delay is caught by accident. Each DOM case runs the real `_capture_popup` of the
pre-change tree (read from git at PRE_CHANGE_REF, the SHA this change was built on) and of the
current one, so the comparison is between the two flows as shipped, not a re-enactment. A late
card is built truncated so the scan has something to catch once the wait holds for it. The
manifest cases call the real collector (seed.popup_items) on small in-memory dashboards.

One PASS/FAIL line per case, with what was measured; exit 1 if any case fails.

    python3 ui-tests/completeness_fixtures.py [--pre-ref <git ref>]
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_overflow as new  # noqa: E402
import seed  # noqa: E402

# The last commit before rows 1 to 4 landed, on origin/main; its check_overflow.py is the
# "before" of every must-miss leg.
PRE_CHANGE_REF = "5033512"
LATE_MS = 3000
HASH = "#fx"

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { margin: 0; font-family: system-ui, sans-serif; }
  bubble-card, button-card, mushroom-template-card, hui-picture-card, hui-conditional-card,
  hui-horizontal-stack-card { display: block; }
  .bubble-pop-up { position: fixed; inset: 0; overflow: auto; background: #fff; opacity: 1; }
  .card { display: block; margin: 8px; }
  .plain { display: block; }
  /* A label clipped the way the gate detects it: nowrap + hidden overflow, narrower than its text. */
  .trunc { display: block; width: 24px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style></head><body><script>
  const host = document.createElement('bubble-card');
  host.config = { hash: '%(hash)s' };
  const pop = document.createElement('div');
  pop.className = 'bubble-pop-up is-popup-opened';
  const hdr = document.createElement('div');
  hdr.className = 'bubble-header-container';
  hdr.innerHTML = '<div class="bubble-header"><div class="plain">Fixture</div></div>';
  const cont = document.createElement('div');
  cont.className = 'bubble-pop-up-container';
  const cards = document.createElement('div');
  cards.className = 'bubble-cards-container';
  cont.appendChild(cards); pop.appendChild(hdr); pop.appendChild(cont);
  host.appendChild(pop); document.body.appendChild(host);
  window.__t = { open: null, painted: null };
  window.addCard = (tag, html, opts = {}) => {
    const w = document.createElement('div'); w.className = 'card';
    const el = document.createElement(tag);
    if (opts.shadow) el.attachShadow({ mode: 'open' }).innerHTML = html; else el.innerHTML = html;
    w.appendChild(el); cards.appendChild(w);
    return el;
  };
  const setup = () => { %(setup)s };
  const late = () => { window.__t.painted = performance.now() - window.__t.open; %(late)s };
  // Bubble renders a pop-up's cards once its hash opens it; the fixture does the same, so the
  // delay counts from the open, whichever flow is driving the page.
  window.addEventListener('hashchange', () => {
    if (window.__t.open !== null || location.hash !== '%(hash)s') return;
    window.__t.open = performance.now();
    setup();
    setTimeout(late, %(late_ms)d);
  });
</script></body></html>
"""


def load_pre_change(ref):
    """The pre-change check_overflow module, read from git history into a temp dir."""
    src = subprocess.run(["git", "-C", HERE, "show", f"{ref}:ui-tests/check_overflow.py"],
                         check=True, capture_output=True, text=True).stdout
    path = os.path.join(tempfile.mkdtemp(prefix="pre-"), "check_overflow_pre.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    spec = importlib.util.spec_from_file_location("check_overflow_pre", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "_popup_short"):
        raise SystemExit(f"{ref} already carries the completeness wait; it is not the pre-change tree")
    return mod


def write_page(tmp, name, setup_js, late_js):
    path = os.path.join(tmp, f"{name}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(PAGE % {"hash": HASH, "setup": setup_js, "late": late_js, "late_ms": LATE_MS})
    return "file://" + path


def truncated_texts(issues):
    return sorted(i["text"] for i in issues if i["type"] == "truncated")


def not_rendered(issues):
    return sorted(i["text"] for i in issues if i["type"] == "not-rendered")


class Runner:
    def __init__(self, browser, tmp, pre):
        self.browser, self.tmp, self.pre = browser, tmp, pre
        self.results = []

    def record(self, ok, case, detail):
        self.results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {case}: {detail}")

    def capture(self, mod, url, items):
        """Run one tree's `_capture_popup` on the page. Returns (issues, seconds it took, cards in
        the DOM when it returned, page timings, what the truncation scan finds once the late card
        has landed). The last is what makes a miss evidence: the scan that ran too early found
        nothing, and the same scan run after the paint finds the truncation."""
        ctx = self.browser.new_context(viewport={"width": 430, "height": 932})
        page = ctx.new_page()
        page.goto(url)
        popup = {"hash": HASH, "name": "Fixture", "labels": []}
        if items is not None:
            popup["cards"] = items
        shot = os.path.join(self.tmp, f"shot-{int(time.time() * 1000)}.png")
        t0 = time.monotonic()
        # The tree under test may predate the navigation log; give it only what its signature takes.
        extra = (mod._NavLog(page),) if hasattr(mod, "_NavLog") else ()
        issues = mod._capture_popup(page, popup, "fixture", "chromium", shot, *extra)
        secs = time.monotonic() - t0
        cards = page.evaluate("() => document.querySelectorAll('.bubble-cards-container .card').length")
        page.wait_for_function("() => window.__t.painted !== null", timeout=LATE_MS + 3000)
        after = page.evaluate(new.JS_DETECT)
        timing = page.evaluate("() => window.__t")
        ctx.close()
        return issues, secs, cards, timing, after

    def probe_short(self, url, items, at_ms):
        """What JS_POPUP_SHORT reports `at_ms` after the pop-up opens, without any wait."""
        ctx = self.browser.new_context(viewport={"width": 430, "height": 932})
        page = ctx.new_page()
        page.goto(url)
        page.evaluate("(h) => { location.hash = h; }", HASH)
        page.wait_for_timeout(at_ms)
        short = page.evaluate(new.JS_POPUP_SHORT, {"hash": HASH, "items": items})
        ctx.close()
        return short

    def dom_case(self, name, items, setup_js, late_js, *, catch, miss):
        """The two legs of one DOM case: the pre-change flow must miss, the new flow must catch.
        `miss(issues, after)` judges the first from what its capture returned and what the scan
        finds after the paint; `catch(issues, cards)` judges the second from what its capture
        returned and the cards in the DOM when it did. Both legs are printed with their timings."""
        url = write_page(self.tmp, name, setup_js, late_js)
        pre_issues, pre_secs, pre_cards, pre_t, pre_after = self.capture(self.pre, url, None)
        new_issues, new_secs, new_cards, new_t, _ = self.capture(new, url, items)
        pre_ok = miss(pre_issues, pre_after)
        new_ok = catch(new_issues, new_cards)
        detail = (f"pre-change {'missed' if pre_ok else 'DID NOT MISS'}: findings {summary(pre_issues)}, "
                  f"capture returned after {pre_secs:.2f}s with {pre_cards} card(s) in the DOM; the late card "
                  f"painted at {pre_t['painted'] / 1000:.2f}s, after which the scan finds {summary(pre_after)}. "
                  f"new {'caught' if new_ok else 'DID NOT CATCH'}: findings {summary(new_issues)}, "
                  f"capture returned after {new_secs:.2f}s with {new_cards} card(s) in the DOM; the late card "
                  f"painted at {new_t['painted'] / 1000:.2f}s")
        self.record(pre_ok and new_ok, name, detail)
        return url

    def manifest_case(self, name, fn, must_name):
        """A collector call that must stop with SystemExit naming `must_name`."""
        try:
            out = fn()
        except SystemExit as err:
            msg = str(err)
            ok = all(part in msg for part in must_name)
            self.record(ok, name, f"SystemExit {'names' if ok else 'DOES NOT NAME'} {must_name}: {msg!r}")
            return
        self.record(False, name, f"no SystemExit; returned {out!r}")


def summary(issues):
    if issues is None:
        return "None (pop-up never scanned)"
    if not issues:
        return "none"
    return "; ".join(f"{i['type']} {i['text']!r}" for i in issues)


def fixture_effective(extra=()):
    """The complete seeded map for the entities the fixture dashboards name, from the real seed."""
    eids = sorted({"sensor.alpine_a290_plug_status", "sensor.alpine_a290_battery_level",
                   "binary_sensor.alpine_a290_charging", *extra})
    return seed.effective_states(eids, seed.problem_sensors(), {})


CHARGE_OR = {"condition": "or", "conditions": [
    {"entity": "sensor.alpine_a290_plug_status", "state": "Connected"},
    {"entity": "sensor.alpine_a290_plug_status", "state": "Test (Connected)"}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre-ref", default=PRE_CHANGE_REF, help="git ref of the pre-change tree")
    args = ap.parse_args()
    pre = load_pre_change(args.pre_ref)
    tmp = tempfile.mkdtemp(prefix="completeness-")
    print(f"pre-change tree: {args.pre_ref}; pages in {tmp}; late card at {LATE_MS} ms after open\n")
    eff = fixture_effective()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        r = Runner(browser, tmp, pre)

        def caught_trunc(text):
            return lambda issues, cards: issues is not None and text in truncated_texts(issues)

        def missed_trunc(text):
            # Nothing reported, though the same scan run after the paint finds the truncation.
            return lambda issues, after: issues == [] and text in truncated_texts(after)

        # 1. A card with only a static name, painted late.
        r.dom_case("static_name_late", [["bubble-card", ["Battery"]]],
                   "", "addCard('bubble-card', '<div class=\"bubble-name trunc\">Battery</div>');",
                   catch=caught_trunc("Battery"), miss=missed_trunc("Battery"))

        # 2. A nameless card whose only text is a state, under the Charge Status card's nested
        # `condition: or`, with the seeded plug state (Connected) meeting it: the item comes from
        # the real collector, so the nested evaluator is what puts "80%" on the list.
        popup = {"hash": HASH, "name": "Fixture", "cards": [
            {"type": "conditional", "conditions": [CHARGE_OR],
             "card": {"type": "custom:button-card", "entity": "sensor.alpine_a290_battery_level",
                      "show_name": False, "show_state": True}}]}
        items, _ = seed.popup_items("fx", popup, eff)
        assert items == [["button-card", ["80%"]]] or items == [("button-card", ["80%"])], items
        items = [[t, x] for t, x in items]
        print(f"      collector on the nested or: {items}")
        r.dom_case("nested_or_state_only", items,
                   "", "addCard('button-card', '<div id=\"state\" class=\"trunc\">80%</div>');",
                   catch=caught_trunc("80%"), miss=missed_trunc("80%"))

        # 3. Two cards with the same text: the first paints at once with the text twice in its own
        # DOM, the second late. The probe at 1 s shows the first card's two elements do not satisfy
        # the second item (distinct roots).
        items = [["bubble-card", ["HVAC"]], ["bubble-card", ["HVAC"]]]
        url = r.dom_case(
            "same_text_twice", items,
            "addCard('bubble-card', '<div class=\"plain\">HVAC</div><div class=\"plain\">HVAC</div>');",
            "addCard('bubble-card', '<div class=\"bubble-name trunc\">HVAC</div>');",
            catch=caught_trunc("HVAC"), miss=missed_trunc("HVAC"))
        short = r.probe_short(url, items, 1000)
        r.record(len(short) == 1 and "taken" in short[0]["why"], "same_text_twice_probe",
                 f"JS_POPUP_SHORT at 1 s (one card, text twice, in the DOM): {short}")

        # 4. A correctly tagged host laid out at once, its state text painted inside its shadow
        # root late.
        r.dom_case("shadow_state_late", [["bubble-card", ["Level", "80%"]]],
                   "window.__host = addCard('bubble-card', "
                   "'<div class=\"plain\">Level</div><div id=\"st\" style=\"min-height:20px\"></div>', {shadow: true});",
                   # The document's stylesheet does not reach into a shadow root: the clip is inline.
                   "window.__host.shadowRoot.getElementById('st').innerHTML = '<div style=\"display:block;"
                   "width:24px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis\">80%</div>';",
                   catch=caught_trunc("80%"), miss=missed_trunc("80%"))

        # 5. A named state card whose name paints at once and whose state text paints late: the
        # probe at 1 s shows the wait still pending on the name alone.
        items = [["bubble-card", ["Level", "80%"]]]
        url = r.dom_case("named_state_late", items,
                         "window.__c = addCard('bubble-card', '<div class=\"plain\">Level</div>');",
                         "window.__c.insertAdjacentHTML('beforeend', '<div class=\"bubble-state trunc\">80%</div>');",
                         catch=caught_trunc("80%"), miss=missed_trunc("80%"))
        short = r.probe_short(url, items, 1000)
        r.record(len(short) == 1 and "80%" in short[0]["why"], "named_state_late_probe",
                 f"JS_POPUP_SHORT at 1 s (name laid out, state not): {short}")

        # 6. A state card whose state paints at once and whose declared badge text paints late.
        r.dom_case("badge_late", [["button-card", ["80%", "Min SOC"]]],
                   "window.__c = addCard('button-card', '<div id=\"state\" class=\"plain\">80%</div>');",
                   "window.__c.insertAdjacentHTML('beforeend', '<div class=\"minsmall trunc\">Min SOC</div>');",
                   catch=caught_trunc("Min SOC"), miss=missed_trunc("Min SOC"))

        # 7. An undeclared templated text field fails at manifest time naming the field.
        r.manifest_case(
            "undeclared_js_field",
            lambda: seed.popup_items("fx", {"hash": HASH, "name": "Fixture", "cards": [
                {"type": "custom:button-card", "entity": "sensor.alpine_a290_battery_level",
                 "name": "[[[ return 'x' ]]]"}]}, eff),
            ["fx", HASH, "cards[0]", "'name'"])
        r.manifest_case(
            "undeclared_jinja_field",
            lambda: seed.popup_items("fx", {"hash": HASH, "name": "Fixture", "cards": [
                {"type": "custom:mushroom-template-card", "entity": "sensor.alpine_a290_battery_level",
                 "primary": "{{ states.sensor.alpine_a290_battery_level.state }} left"}]}, eff),
            ["fx", HASH, "cards[0]", "'primary'"])
        r.manifest_case(
            "undeclared_custom_field",
            lambda: seed.popup_items("fx", {"hash": HASH, "name": "Fixture", "cards": [
                {"type": "custom:button-card", "entity": "sensor.alpine_a290_battery_level",
                 "custom_fields": {"badge": "[[[ return 'x' ]]]"}}]}, eff),
            ["fx", HASH, "cards[0]", "'custom_fields.badge'"])

        # 8. A card with two static fields where the second paints late.
        r.dom_case("two_static_fields", [["mushroom-template-card", ["LOCATION", "Trafalgar Square"]]],
                   "window.__c = addCard('mushroom-template-card', '<span class=\"plain\">LOCATION</span>');",
                   "window.__c.insertAdjacentHTML('beforeend', '<span class=\"trunc\">Trafalgar Square</span>');",
                   catch=caught_trunc("Trafalgar Square"), miss=missed_trunc("Trafalgar Square"))

        # 9. Two conditional cards, one active and one not: only the active one's card is on the
        # list, no hui-conditional-card is, and the page's inactive wrapper (an empty box) does not
        # satisfy anything.
        popup = {"hash": HASH, "name": "Fixture", "cards": [
            {"type": "conditional", "conditions": [CHARGE_OR],
             "card": {"type": "custom:bubble-card", "card_type": "button", "button_type": "name", "name": "Plugged"}},
            {"type": "conditional", "conditions": [
                {"condition": "state", "entity": "sensor.alpine_a290_plug_status", "state": "Disconnected"}],
             "card": {"type": "picture", "image": "/local/x.webp"}}]}
        items, _ = seed.popup_items("fx", popup, eff)
        items = [[t, x] for t, x in items]
        print(f"      collector on the two conditionals: {items}")
        r.record(items == [["bubble-card", ["Plugged"]]], "two_conditionals_manifest",
                 f"active inner card only, no wrapper: {items}")
        r.dom_case("two_conditionals", items,
                   "addCard('hui-conditional-card', '');",
                   "addCard('hui-conditional-card', '<bubble-card><div class=\"bubble-name trunc\">Plugged</div></bubble-card>');",
                   catch=caught_trunc("Plugged"), miss=missed_trunc("Plugged"))

        # 10. A laid-out card whose text element is at opacity 1 under a wrapper at opacity 0:
        # nothing is truncated, so the pre-change flow passes it; the new flow must stay pending
        # and end as not-rendered.
        r.dom_case("opacity_zero_ancestor", [["bubble-card", ["Ghost"]]],
                   "addCard('bubble-card', '<div style=\"opacity:0\"><div class=\"plain\" style=\"opacity:1\">Ghost</div></div>');",
                   "",
                   catch=lambda issues, t: issues is not None and any("'Ghost'" in x for x in not_rendered(issues)),
                   miss=lambda issues, t: issues == [])

        # 11. A card with no text at all (a picture) whose only signal is its count, appearing late:
        # the pre-change scan ends before it exists (0 cards in the DOM at the end of its capture),
        # the new flow holds until it is there.
        r.dom_case("textless_late", [["hui-picture-card", []]],
                   "", "addCard('hui-picture-card', '<div style=\"height:40px;background:#ccc\"></div>');",
                   catch=lambda issues, cards: issues == [] and cards == 1,
                   miss=lambda issues, after: issues == [])
        # The count-based evidence for 11: the pre-change capture returns before the card exists,
        # the new one only once it does.
        url = write_page(tmp, "textless_late_count", "",
                         "addCard('hui-picture-card', '<div style=\"height:40px;background:#ccc\"></div>');")
        _, pre_secs, pre_cards, pre_t, _ = r.capture(pre, url, None)
        _, new_secs, new_cards, _, _ = r.capture(new, url, [["hui-picture-card", []]])
        r.record(pre_cards == 0 and new_cards == 1 and new_secs >= LATE_MS / 1000, "textless_late_count",
                 f"pre-change: {pre_cards} card(s) in the DOM when its capture returned after {pre_secs:.2f}s "
                 f"(the card painted at {pre_t['painted'] / 1000:.2f}s); new: {new_cards} card(s) when its capture "
                 f"returned after {new_secs:.2f}s")

        # 12. The same textless card never appearing: not-rendered naming the card type.
        r.dom_case("textless_never", [["hui-picture-card", []]], "", "",
                   catch=lambda issues, t: issues is not None and any(
                       "hui-picture-card" in i["tag"] and i["type"] == "not-rendered" for i in issues),
                   miss=lambda issues, t: issues == [])

        # Manifest-time errors beyond 7: a declaration no card matches, a condition on an entity
        # the seed does not set, a pop-up with no cards.
        def declared_not_found():
            saved = seed.DECLARED, seed.STATE_TEXT_OVERRIDE, seed.MIN_POPUPS
            seed.DECLARED = {("fx", HASH, "custom:bubble-card", "sensor.gone"): lambda e: {"name": ["x"]}}
            seed.STATE_TEXT_OVERRIDE = {}
            seed.MIN_POPUPS = 1
            try:
                views = [{"cards": [{"type": "custom:bubble-card", "card_type": "pop-up", "hash": HASH,
                                     "name": "Fixture", "cards": [
                                         {"type": "custom:bubble-card", "card_type": "button",
                                          "button_type": "name", "name": "Here"}]}]}]
                seed.write_manifest(os.path.join(tmp, "m.json"), {"fx": views}, "", {}, {})
            finally:
                seed.DECLARED, seed.STATE_TEXT_OVERRIDE, seed.MIN_POPUPS = saved

        r.manifest_case("declared_card_not_found", declared_not_found, ["DECLARED", "match no card", "sensor.gone"])
        r.manifest_case(
            "unseeded_condition_entity",
            lambda: seed.popup_items("fx", {"hash": HASH, "name": "Fixture", "cards": [
                {"type": "conditional", "conditions": [{"entity": "sensor.not_seeded", "state": "on"}],
                 "card": {"type": "picture", "image": "/local/x.webp"}}]}, eff),
            ["sensor.not_seeded", "does not set"])
        r.manifest_case("popup_without_cards",
                        lambda: seed.popup_items("fx", {"hash": HASH, "name": "Fixture", "cards": []}, eff),
                        ["no cards"])

        # Extra, outside row 6: the seed's timestamp rendering against this Chromium's Intl, the
        # way HA's formatDateTime builds it (en, h12, the browser's zone), whitespace normalised.
        iso = seed.KNOWN["sensor.alpine_a290_hvac_last_activity"][0]
        ctx = browser.new_context()
        page = ctx.new_page()
        got = page.evaluate(
            "(iso) => new Intl.DateTimeFormat('en', {year: 'numeric', month: 'long', day: 'numeric', "
            "hour: 'numeric', minute: '2-digit', hourCycle: 'h12'}).format(new Date(iso))", iso)
        ctx.close()
        want = seed._format_date_time(iso)
        norm = got.replace(" ", " ").replace(" ", " ")
        r.record(norm == want, "extra_timestamp_intl",
                 f"Chromium {browser.version} Intl gives {got!r} (normalised {norm!r}); seed gives {want!r}")

        browser.close()

    print(f"\n{sum(r.results)} of {len(r.results)} cases passed")
    sys.exit(0 if all(r.results) else 1)


if __name__ == "__main__":
    main()
