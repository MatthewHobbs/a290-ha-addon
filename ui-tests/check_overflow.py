#!/usr/bin/env python3
"""Render the A290 dashboards across the mobile device matrix and fail on text truncation.

For every device viewport x dashboard it: injects auth, navigates, waits for the custom
cards + the Zen Dots font, then walks the (shadow-DOM-pierced) tree for any text element
that is clipped (text-overflow:ellipsis / nowrap+overflow:hidden with scrollWidth >
clientWidth) or any broken card (hui-error-card). A screenshot is saved per device. Exits
non-zero with a report if any truncation or card error is found.

A named pass (--pass-name alarm) writes its screenshots as <dashboard>__<pass>__<device>.png, so
it never overwrites the normal pass's files, which the screenshot-drift workflow reads by name.
--expect takes seed.py's alarm manifest: it picks the dashboards and lists labels that must be
visible on each, so a pass whose states failed to switch the cards on cannot pass.
"""
import argparse
import json
import os
import sys
import time
import traceback

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))

# The bubble dashboard auto-opens its main pop-up ~60ms after load by setting location.hash. If
# that fires while one of our evaluate/screenshot calls is in flight it tears down the execution
# context ("Execution context was destroyed") or leaves a webfont fetch pending (screenshot's
# font-wait then times out). Both are transient and viewport-independent. We settle the auto-open
# before touching the page and retry the whole main capture on such a render error — a genuine
# truncation is a returned finding (never an exception), so it is never retried away.
MAX_RENDER_ATTEMPTS = 3

UA_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like "
              "Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")

# Recurses shadow roots; returns truncated text elements + broken cards.
JS_DETECT = r"""
() => {
  const out = [];
  const walk = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      if (el.shadowRoot) walk(el.shadowRoot);
      const tag = (el.tagName || '').toLowerCase();
      if (tag === 'hui-error-card' || tag === 'hui-warning' || tag === 'hui-warning-card') {
        out.push({ type: 'card-error', tag, text: (el.textContent || '').trim().slice(0, 160) });
        continue;
      }
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
      own = own.trim();
      if (!own) continue;
      const cs = getComputedStyle(el);
      const clipsX = cs.textOverflow === 'ellipsis'
                  || (cs.overflowX === 'hidden' && cs.whiteSpace.indexOf('nowrap') >= 0);
      if (clipsX && el.scrollWidth > el.clientWidth + 1) {
        out.push({ type: 'truncated', tag, text: own.slice(0, 160),
                   scrollWidth: el.scrollWidth, clientWidth: el.clientWidth });
      }
    }
  };
  walk(document);
  return out;
}
"""

# Remove Home Assistant's transient startup toasts (e.g. "Starting radio_browser. Not
# everything will be available until it is finished" from default_config) so they don't leak
# into the captured documentation screenshots. The toast host (notification-manager) lives in
# home-assistant's shadow root; clear it (and any stray ha-toast/snackbar) before each capture.
JS_DISMISS_TOASTS = r"""
() => {
  const roots = [document];
  const ha = document.querySelector('home-assistant');
  if (ha && ha.shadowRoot) roots.push(ha.shadowRoot);
  for (const r of roots) {
    try { r.querySelectorAll('notification-manager, ha-toast, mwc-snackbar').forEach(e => e.remove()); }
    catch (e) {}
  }
}
"""

# True once a custom card (or an error card) is present in the (pierced) tree.
# Opt-in capture diagnostics (UI_TESTS_DIAG=1). Writes a JSON sidecar next to each screenshot
# recording what the DOM actually contained at the moment of capture. Added because the render is
# not reproducible and every fix attempted so far was a guess: freezing animations and anchoring
# the seeded clock did not move the 20-of-30 identical figure, and waiting for a "stable" layout
# made it WORSE by capturing a blank page (a blank page is stable). This measures instead.
#
# The specific suspicion it exists to test: JS_RENDERED below returns true as soon as ONE card
# exists anywhere in the tree, so readiness fires on the FIRST card and everything after it rests
# on a fixed 1200ms sleep. If card counts vary between runs at capture time, that is the cause,
# and the fix is a COMPLETENESS condition (a known-good count), not another wait.
JS_DIAG = r"""
() => {
  const tags = {};
  const walk = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      const t = (el.tagName || '').toLowerCase();
      if (t.indexOf('mushroom') >= 0 || t === 'bubble-card' || t === 'button-card'
          || t === 'hui-error-card' || t === 'ha-card') tags[t] = (tags[t] || 0) + 1;
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return {
    cards: Object.values(tags).reduce((a, b) => a + b, 0), byTag: tags,
    scrollW: document.documentElement.scrollWidth,
    scrollH: document.documentElement.scrollHeight,
    imagesPending: Array.from(document.images).filter(i => !i.complete).length,
    readyState: document.readyState,
  };
}
"""


# True only while an OPEN Bubble pop-up shows an element whose own text is exactly `label`, both
# inside the viewport. Each condition closes a way the pop-up capture can pass with it shut:
#  - a document card count: the main-menu pop-up behind a failed open has ~24 cards;
#  - `text=Charge Target`: a case-insensitive substring, so it would also match "Charge Target SoC"
#    (the A290's own number entity) if a card ever rendered that friendly name;
#  - Playwright "visible": a non-empty box, not "on screen" and not opacity>0. Bubble 3.2.5 detaches
#    a closed standalone pop-up, but its centered/adaptive-dialog modes keep a closed one in layout
#    at opacity 0, and a closing one animates out while still in the DOM.
JS_POPUP_SHOWS = r"""
(label) => {
  const inView = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0
        && r.top < innerHeight && r.left < innerWidth;
  };
  const hasLabel = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return false; }
    for (const el of nodes) {
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
      if (own.trim() === label && getComputedStyle(el).visibility === 'visible' && inView(el)) return true;
      if (el.shadowRoot && hasLabel(el.shadowRoot)) return true;
    }
    return false;
  };
  const findOpen = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return false; }
    for (const el of nodes) {
      const c = el.classList;
      if (c && c.contains('bubble-pop-up') && c.contains('is-popup-opened') && !c.contains('is-closing')
          && parseFloat(getComputedStyle(el).opacity) > 0 && inView(el) && hasLabel(el)) return true;
      if (el.shadowRoot && findOpen(el.shadowRoot)) return true;
    }
    return false;
  };
  return findOpen(document);
}
"""


# True once an element whose own text is exactly `label` is laid out anywhere in the pierced tree.
JS_SHOWS_TEXT = r"""
(label) => {
  const find = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return false; }
    for (const el of nodes) {
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
      if (own.trim() === label && getComputedStyle(el).visibility === 'visible') {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return true;
      }
      if (el.shadowRoot && find(el.shadowRoot)) return true;
    }
    return false;
  };
  return find(document);
}
"""


def _missing_labels(page, labels, timeout_ms=5000):
    """Findings for each expected label that never became visible. Only a timeout is a finding;
    any other error (a torn-down context) propagates to the caller's retry like the rest."""
    missing = []
    for label in labels:
        try:
            page.wait_for_function(JS_SHOWS_TEXT, arg=label, timeout=timeout_ms)
        except PlaywrightTimeout:
            missing.append({"type": "not-rendered", "tag": "-", "text": label})
    return missing


def _failing_step(err):
    """The deepest line of THIS file `err` came through, plus its message's first line. The last
    traceback frame is inside Playwright, which cannot tell a screenshot timeout from a scan one."""
    here = os.path.abspath(__file__)
    frames = [f for f in traceback.extract_tb(err.__traceback__) if os.path.abspath(f.filename) == here]
    where = f"line {frames[-1].lineno}: {frames[-1].line}" if frames else "outside check_overflow.py"
    msg = (str(err).strip().splitlines() or [""])[0]
    return f"{where} — {msg}"


def _write_diag(page, shot_path):
    """Record the DOM state at capture time, when UI_TESTS_DIAG=1."""
    if os.environ.get("UI_TESTS_DIAG") != "1":
        return
    try:
        data = page.evaluate(JS_DIAG)
    except Exception as err:      # never let diagnostics break the gate they are diagnosing
        data = {"error": f"{type(err).__name__}: {err}"}
    try:
        with open(os.path.splitext(shot_path)[0] + ".diag.json", "w") as fh:
            json.dump(data, fh, sort_keys=True)
    except OSError:
        pass


JS_RENDERED = r"""
() => {
  const find = (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return false; }
    for (const el of nodes) {
      const t = (el.tagName || '').toLowerCase();
      if (t.indexOf('mushroom') >= 0 || t === 'bubble-card' || t === 'button-card'
          || t === 'hui-error-card') return true;
      if (el.shadowRoot && find(el.shadowRoot)) return true;
    }
    return false;
  };
  return find(document);
}
"""


def auth_script(base, tokens):
    payload = {
        "access_token": tokens["access_token"],
        "token_type": "Bearer",
        "expires_in": tokens.get("expires_in", 1800),
        "hassUrl": base,
        "clientId": base + "/",
        "expires": 4102444800000,  # year 2100 — don't trigger a refresh during the run
        "refresh_token": tokens["refresh_token"],
    }
    return f"window.localStorage.setItem('hassTokens', {json.dumps(json.dumps(payload))});"


# How far card-mod (the pinned v4.2.1) has got. It styles a card only through prototype patches
# (hui-card._loadElement, ha-card.firstUpdated, hui-grid-section.firstUpdated: src/patch/*.ts), so
# a card built before card-mod.js runs is NEVER styled on that load. That is a lost load-order race,
# not a slow apply, and no wait recovers it; run.sh loads card-mod as a frontend module for a head
# start in that race, which is not a guarantee.
# Scanning an unstyled page reports every wrapping label as truncated, so this proves the styles
# are in before the scan. A card is applied once card-mod's `_cardMod` list on the element that
# declares `card_mod` is non-empty and each <card-mod> in it is connected, has processed its
# input, rendered its own `.` style into its <style>, and resolved every `selector$` child
# (recursively). Promises are peeked with Promise.race, so a pending one never blocks the probe.
# hui-card/hui-section hold a card's config but are never styled themselves; conditional and
# entity-filter are skipped by card-mod (src/patch/hui-card.ts EXCLUDED_CARDS).
JS_CARD_MOD_STATE = r"""
async () => {
  const PENDING = {};
  const peek = (p) => Promise.race([p, Promise.resolve(PENDING)]);
  const nonEmpty = (v) => (typeof v === 'string' ? v.trim() !== '' : !!v && Object.keys(v).length > 0);
  // How many elements card-mod's selectTree(parent, key, all) would style now; a synchronous replica
  // of src/helpers/selecttree.ts (split on '$' and ' ', '$' enters shadow roots, first match onward).
  const targets = (cm, key) => {
    let el = [cm.parentElement || cm.parentNode];
    const path = key.split(/(\$| )/);
    while (path[path.length - 1] === '') path.pop();
    for (const p of path) {
      if (p === '$') { el = [...el].map((e) => e && e.shadowRoot); continue; }
      if (!el[0]) return 0;
      if (p.trim()) el = el[0].querySelectorAll(p);
    }
    return el.length;
  };
  const cmReady = async (cm, depth) => {
    if (!cm || !cm.isConnected || cm._processStylesOnConnect) return false;
    const fixed = cm._fixed_styles || {};
    if (nonEmpty(cm.card_mod_input) && Object.keys(fixed).length === 0) return false;
    // A `.` style must be in the <style>. For a template that means its first render_template
    // result arrived: card-mod renders "" until then (src/helpers/templates.ts), so a template that
    // legitimately renders "" reads as pending; none on these dashboards does (measured).
    const own = typeof fixed['.'] === 'string' ? fixed['.'] : '';
    if (own.trim()) {
      const st = cm.querySelector(':scope > style');
      if (!st || !st.textContent.trim()) return false;
    }
    if (depth > 8) return true;
    const kids = cm.card_mod_children || {};
    for (const key of Object.keys(fixed)) {
      if (key === '.') continue;
      if (!(key in kids)) return false;
      const list = await peek(kids[key]);
      if (list === PENDING) return false;
      // Nullish: card-mod gave up on the selector (or a restyle cancelled it). Ready only while
      // nothing matches it: then there is nothing to style (mushroom-template-card has no
      // mushroom-state-info at all). A target that exists but was given up on stays pending.
      if (list == null) {
        if (targets(cm, key) > 0) return false;
        continue;
      }
      for (const p of list) {
        const child = await peek(p);
        if (child === PENDING || !(await cmReady(child, depth + 1))) return false;
      }
    }
    return true;
  };
  const WRAPPERS = new Set(['hui-card', 'hui-section']);
  const EXCLUDED = new Set(['conditional', 'entity-filter']);
  // `ids` names each declaring element across polls, so the caller can see the set change.
  const ids = window.__cardModProbeIds = window.__cardModProbeIds || new WeakMap();
  const out = { declared: 0, applied: 0, pending: [], ids: [], loaded: !!customElements.get('card-mod') };
  const walk = async (root) => {
    let nodes; try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      let cfg = null;
      try { cfg = el._config || el.config; } catch (e) {}
      if (!WRAPPERS.has(el.localName) && cfg && typeof cfg === 'object' && nonEmpty(cfg.card_mod)
          && !EXCLUDED.has(String(cfg.type || '').toLowerCase())) {
        out.declared++;
        if (!ids.has(el)) ids.set(el, (window.__cardModProbeSeq = (window.__cardModProbeSeq || 0) + 1));
        out.ids.push(ids.get(el));
        const cms = Array.isArray(el._cardMod) ? el._cardMod : [];
        let ok = cms.length > 0;
        for (const cm of cms) if (ok && !(await cmReady(cm, 0))) ok = false;
        if (ok) out.applied++;
        else out.pending.push(el.localName + (cfg.type ? ' (' + cfg.type + ')' : ''));
      }
      if (el.shadowRoot) await walk(el.shadowRoot);
    }
  };
  await walk(document);
  return out;
}
"""

# A lost race never recovers, so this cap only decides how long a failure takes to report.
CARD_MOD_WAIT_S = 25
# How long the set of declaring cards must hold still, all applied. "All applied" is also true of
# a page whose card_mod cards have not rendered yet (0 of 0); readiness only requires ONE custom
# card, so a later card must not slip in after this returns. Cold loads declare their last card
# within ~0.3 s of the first, and the scan already waits 1.2 s past the first card before this.
CARD_MOD_QUIET_S = 1.0


def _wait_card_mod(page, cap_s=CARD_MOD_WAIT_S, poll_ms=250, quiet_s=CARD_MOD_QUIET_S):
    """Wait until card-mod has applied every `card_mod` the page declares, with the same cards
    declaring it for `quiet_s`. Returns [] once it has (or when nothing on the page declares
    card_mod for that long), else a single finding: a page card-mod never styled must fail the
    gate by name, never pass on whatever the scan happens to measure."""
    deadline = time.monotonic() + cap_s
    quiet_since, last_ids = None, None
    while True:
        st = page.evaluate(JS_CARD_MOD_STATE)
        now = time.monotonic()
        done = st["applied"] == st["declared"]
        if not (done and st["ids"] == last_ids and quiet_since is not None):
            quiet_since = now if done else None
        last_ids = st["ids"]
        if quiet_since is not None and now - quiet_since >= quiet_s:
            return []
        if now >= deadline:
            break
        page.wait_for_timeout(poll_ms)
    missing = st["declared"] - st["applied"]
    if not missing:
        return [{"type": "card-mod-not-applied", "tag": "card-mod",
                 "text": f"card-mod state never settled within {cap_s}s: the cards declaring card_mod "
                         f"kept changing ({st['declared']} at the cap)"}]
    return [{"type": "card-mod-not-applied", "tag": "card-mod",
             "text": f"card-mod never applied to {missing} of {st['declared']} card(s) declaring card_mod "
                     f"within {cap_s}s (card-mod.js {'loaded' if st['loaded'] else 'NOT loaded'}); "
                     f"e.g. {', '.join(st['pending'][:4])}"}]


def _stable_issues(page, settle_ms=500, max_passes=12):
    """Poll the truncation scan across a settle window and report only issues that SURVIVE it.
    card-mod styles (e.g. `white-space:normal` on the card labels) and webfonts apply
    asynchronously after a card first paints, so an early measurement can catch a label still in
    its default nowrap/ellipsis state and false-flag it as truncated.

    Crucially, a phantom truncation is *stable* for the whole pre-card-mod plateau (the flagged
    set doesn't change from scan to scan) and then clears all at once when card-mod lands — so a
    fixed two-pass, or any "stop when two scans agree" rule, can return the phantom set outright.
    On the narrowest, most-card-dense viewport (Galaxy S24 @ 360px) that plateau outlasts a couple
    of samples and the gate flakes (observed pass->fail->fail->pass on identical dashboards).

    The reliable signal is *survival*, not stability: a phantom clears once card-mod applies (a
    terminal state — card-mod does not un-apply), whereas a genuine truncation stays flagged for
    the entire window. So keep scanning up to `max_passes`; exit early only on a clean state
    (confirmed by two consecutive empty scans, to rule out a transient empty), and otherwise report
    whatever is still flagged when the window closes. Fast path: a clean pair returns quickly; a
    genuine failure uses the full window (~6s), which is fine since failures are rare.

    The window alone assumes card-mod DOES land; when it lost the load race it never will, and the
    window reported the unstyled page as mass truncation. So first wait for card-mod
    (`_wait_card_mod`), whose own finding fails the gate by name when it never applies."""
    not_applied = _wait_card_mod(page)
    issues, clean_streak = [], 0
    for i in range(max_passes):
        issues = page.evaluate(JS_DETECT)
        clean_streak = clean_streak + 1 if not issues else 0
        if clean_streak >= 2:                  # two consecutive clean scans → genuinely settled
            return not_applied
        if i < max_passes - 1:                 # no point sleeping after the final scan
            page.wait_for_timeout(settle_ms)
    return not_applied + issues                # still flagged when the window closed → genuine


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--tokens", default="/tmp/ha_tokens.json")
    ap.add_argument("--devices", default=os.path.join(HERE, "devices.json"))
    ap.add_argument("--dashboards", nargs="+", default=["alpine-standard", "alpine-bubble"])
    ap.add_argument("--out", default=os.path.join(HERE, "screenshots"))
    ap.add_argument("--pass-name", default="", help="names this pass in screenshots and the report")
    ap.add_argument("--expect", metavar="MANIFEST",
                    help="seed.py --alarm manifest {dashboard: [labels]}; replaces --dashboards")
    args = ap.parse_args()

    expect = {}
    if args.expect:
        with open(args.expect, encoding="utf-8") as fh:
            expect = json.load(fh)
        if not expect:
            sys.exit(f"{args.expect} names no dashboard: this pass would check nothing")
        args.dashboards = list(expect)
    tokens = json.load(open(args.tokens))
    devices = json.load(open(args.devices))["devices"]
    os.makedirs(args.out, exist_ok=True)
    init = auth_script(args.base, tokens)

    failures = []
    # The truncation gate runs in light mode (stable); set UI_TESTS_DARK=1 to render in dark
    # mode instead (used to produce the documentation screenshots).
    colour_scheme = "dark" if os.environ.get("UI_TESTS_DARK") else "light"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for dev in devices:
            ua = UA_IOS if "iphone" in dev["name"].lower() or "ipad" in dev["name"].lower() else UA_ANDROID
            ctx = browser.new_context(
                viewport={"width": dev["width"], "height": dev["height"]},
                device_scale_factor=dev.get("deviceScaleFactor", 2),
                is_mobile=dev.get("isMobile", True), has_touch=dev.get("hasTouch", True),
                color_scheme=colour_scheme, user_agent=ua,
                # The dashboards run FIVE infinite CSS animations (pulse, spin, flap-wiggle,
                # flip180, socFillToTarget). A page that never stops moving cannot be captured
                # reproducibly, and no amount of settling time fixes it — every shot samples a
                # different frame. That, not runner timing, is why the committed screenshots
                # oscillated between two byte sizes all day (143783 <-> 143801). reduced_motion
                # asks the page not to start them; screenshot(animations="disabled") below is the
                # belt-and-braces that freezes anything that ignores the preference.
                reduced_motion="reduce")
            ctx.add_init_script(init)
            page = ctx.new_page()
            for dash in args.dashboards:
                slug = dev["name"].lower().replace(" ", "_").replace("(", "").replace(")", "")
                stem = f"{dash}__{args.pass_name}" if args.pass_name else dash
                where = f"{dash} [{args.pass_name}]" if args.pass_name else dash
                shot = os.path.join(args.out, f"{stem}__{slug}.png")
                issues = None
                for attempt in range(MAX_RENDER_ATTEMPTS):
                    try:
                        page.goto(f"{args.base}/{dash}", wait_until="domcontentloaded", timeout=30000)
                        # Let the bubble dashboard's auto-open hash navigation fire (~60ms) and
                        # settle BEFORE any evaluate/screenshot, so it can't tear down an in-flight
                        # execution context or leave a font fetch pending mid-capture.
                        page.wait_for_timeout(300)
                        page.wait_for_function(JS_RENDERED, timeout=30000)
                        try:  # actually load Zen Dots before measuring — fonts.ready alone resolves
                            page.evaluate(                                 # before a not-yet-applied font fetches
                                "async () => { try {"
                                " await document.fonts.load('400 12px \"Zen Dots\"');"
                                " await document.fonts.load('700 13px \"Zen Dots\"');"
                                " await document.fonts.ready;"
                                " } catch (e) {} }")
                        except Exception:
                            pass
                        page.wait_for_timeout(1200)  # settle layout + late cards
                        issues = _stable_issues(page)   # confirm truncations across two passes (see helper)
                        issues += _missing_labels(page, expect.get(dash, []))
                        # Drop HA's startup toasts only AFTER the truncation scan, so removing the
                        # toast node can never perturb the gate's measurement — it only cleans the shot.
                        page.evaluate(JS_DISMISS_TOASTS)
                        _write_diag(page, shot)
                        page.screenshot(path=shot, full_page=True, animations="disabled")
                        break
                    except Exception as err:
                        # A render error here is transient (auto-open context teardown / font-wait
                        # timeout). Retry the whole capture; only record it as a failure once the
                        # attempts are exhausted. Genuine truncations are returned by _stable_issues,
                        # not raised, so they never reach this branch and are never retried away.
                        if attempt < MAX_RENDER_ATTEMPTS - 1:
                            print(f"    [retry {attempt + 1}/{MAX_RENDER_ATTEMPTS - 1}] {where} @ "
                                  f"{dev['name']}: transient {type(err).__name__} — re-rendering")
                            page.wait_for_timeout(600)
                            continue
                        issues = [{"type": "render-error", "tag": "-", "text": f"{type(err).__name__}: {err}"}]
                        try:
                            page.evaluate(JS_DISMISS_TOASTS)
                            _write_diag(page, shot)
                            page.screenshot(path=shot, full_page=True, animations="disabled")
                        except Exception:
                            pass
                # The Smart Charging pop-up ("tab") capture is best-effort and ISOLATED from the
                # gate: opening it via hash navigation can tear down the JS context on slower
                # viewports ("Execution context was destroyed"), and that must never fail the run.
                # When the scan DOES complete, its issues (truncation + broken cards) are escalated
                # through the same two-pass stability filter as the main dashboard, so the pop-up
                # keeps its truncation coverage without the transient flake. A context teardown at
                # any point is caught here and skipped — the pop-up config is identical across
                # viewports, so a real break still surfaces on the ones that scan cleanly.
                if dash == "alpine-bubble":
                    try:
                        # COMPLETENESS, not just settling. The selector timeout below used to be
                        # swallowed by a bare `except: pass`, after which the capture ran anyway —
                        # so when the pop-up failed to open, an EMPTY page was written as the
                        # documentation screenshot. Measured: the same shot captured 24 cards on
                        # one run and 0 on the next, at identical page dimensions, differing in
                        # 99.89% of its pixels. A screenshot known to be empty must never be
                        # written; retry the open, and if it still has not rendered, skip the
                        # capture and keep the previous good file. Judge completeness by the
                        # pop-up itself being open and showing its label (JS_POPUP_SHOWS), never
                        # by a document card count: the main menu behind a failed open has cards.
                        for popup_attempt in range(2):
                            page.evaluate("() => { location.hash = '#alpine-charging'; }")
                            try:  # the pop-up's inner cards lazy-render (Bubble Card)
                                page.wait_for_function(JS_POPUP_SHOWS, arg="Charge Target", timeout=8000)
                                page.wait_for_timeout(800)
                                page.evaluate(JS_DISMISS_TOASTS)
                                # Re-check after the settle: seen opening is not still open. The
                                # pop-up can close in that window, or the page reload under it: HA
                                # reloads once, ~3s after a context's first load, as its service
                                # worker takes control (measured on Bubble 3.2.5 and 3.4.0; blocking
                                # service workers removes it), leaving the pop-up sliding in below
                                # the viewport. Treated as a failed open, so the reopen recovers it.
                                opened = page.evaluate(JS_POPUP_SHOWS, "Charge Target")
                            except Exception:
                                opened = False
                            if opened:
                                break
                            print(f"    [popup empty {popup_attempt + 1}/2] {where} @ "
                                  f"{dev['name']}: pop-up not open on screen — reopening")
                            page.evaluate("() => { location.hash = ''; }")
                            page.wait_for_timeout(400)
                        else:
                            print(f"    [popup skipped] {where} @ {dev['name']}: pop-up never "
                                  f"stayed open; not overwriting the committed screenshot")
                            raise RuntimeError("smart-charging pop-up never stayed open on screen")
                        pshot = os.path.join(args.out, f"{stem}__smart_charging__{slug}.png")
                        _write_diag(page, pshot)
                        page.screenshot(path=pshot, full_page=True, animations="disabled")
                        issues += _stable_issues(page)
                    except Exception as err:
                        print(f"    pop-up capture skipped ({type(err).__name__}) — not failing the gate\n"
                              f"      at {_failing_step(err)}")
                # De-dupe: the pop-up scan re-walks the whole document, so a main-dashboard finding
                # can otherwise appear twice when both the main view and the pop-up are flagged.
                _seen, _uniq = set(), []
                for _it in issues:
                    _k = (_it["type"], _it.get("tag"), _it.get("text"))
                    if _k not in _seen:
                        _seen.add(_k)
                        _uniq.append(_it)
                issues = _uniq
                if issues:
                    failures.append((where, dev["name"], dev["width"], issues))
                status = "FAIL" if issues else "ok"
                print(f"  [{status}] {where} @ {dev['name']} ({dev['width']}px): "
                      f"{len(issues)} issue(s)  -> {os.path.relpath(shot, HERE)}")
            ctx.close()
        browser.close()

    print()
    if failures:
        print(f"=== {len(failures)} device/dashboard combos with issues ===")
        for dash, name, width, issues in failures:
            print(f"\n{dash} @ {name} ({width}px):")
            for i in issues[:12]:
                if i["type"] == "truncated":
                    print(f"  - TRUNCATED <{i['tag']}> {i['scrollWidth']}>{i['clientWidth']}px: "
                          f"{i['text']!r}")
                else:
                    print(f"  - {i['type'].upper()} <{i['tag']}>: {i['text']!r}")
            if len(issues) > 12:
                print(f"  …and {len(issues) - 12} more")
        sys.exit(1)
    print(f"All dashboards render with no text truncation across the device matrix"
          f"{f' ({args.pass_name} pass)' if args.pass_name else ''}. ✅")


if __name__ == "__main__":
    run()
