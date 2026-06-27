#!/usr/bin/env python3
"""Render the A290 dashboards across the mobile device matrix and fail on text truncation.

For every device viewport x dashboard it: injects auth, navigates, waits for the custom
cards + the Zen Dots font, then walks the (shadow-DOM-pierced) tree for any text element
that is clipped (text-overflow:ellipsis / nowrap+overflow:hidden with scrollWidth >
clientWidth) or any broken card (hui-error-card). A screenshot is saved per device. Exits
non-zero with a report if any truncation or card error is found.
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))

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

# True once a custom card (or an error card) is present in the (pierced) tree.
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


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--tokens", default="/tmp/ha_tokens.json")
    ap.add_argument("--devices", default=os.path.join(HERE, "devices.json"))
    ap.add_argument("--dashboards", nargs="+", default=["alpine-standard", "alpine-bubble"])
    ap.add_argument("--out", default=os.path.join(HERE, "screenshots"))
    args = ap.parse_args()

    tokens = json.load(open(args.tokens))
    devices = json.load(open(args.devices))["devices"]
    os.makedirs(args.out, exist_ok=True)
    init = auth_script(args.base, tokens)

    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for dev in devices:
            ua = UA_IOS if "iphone" in dev["name"].lower() or "ipad" in dev["name"].lower() else UA_ANDROID
            ctx = browser.new_context(
                viewport={"width": dev["width"], "height": dev["height"]},
                device_scale_factor=dev.get("deviceScaleFactor", 2),
                is_mobile=dev.get("isMobile", True), has_touch=dev.get("hasTouch", True),
                user_agent=ua)
            ctx.add_init_script(init)
            page = ctx.new_page()
            for dash in args.dashboards:
                slug = dev["name"].lower().replace(" ", "_").replace("(", "").replace(")", "")
                shot = os.path.join(args.out, f"{dash}__{slug}.png")
                try:
                    page.goto(f"{args.base}/{dash}", wait_until="domcontentloaded", timeout=30000)
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
                    issues = page.evaluate(JS_DETECT)
                    page.screenshot(path=shot, full_page=True)
                except Exception as err:
                    issues = [{"type": "render-error", "tag": "-", "text": f"{type(err).__name__}: {err}"}]
                    try:
                        page.screenshot(path=shot, full_page=True)
                    except Exception:
                        pass
                if issues:
                    failures.append((dash, dev["name"], dev["width"], issues))
                status = "FAIL" if issues else "ok"
                print(f"  [{status}] {dash} @ {dev['name']} ({dev['width']}px): "
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
    print("All dashboards render with no text truncation across the device matrix. ✅")


if __name__ == "__main__":
    run()
