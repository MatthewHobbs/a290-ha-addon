#!/usr/bin/env python3
"""Watch the Home Assistant blogs and open backlog issues for posts that affect this add-on.

Runs on a schedule (see ha-cadence-watch.yaml). For each recent post on the HA developer blog
and the HA release/user blog it:
  1. keyword-prefilters (cheap) to drop obviously-irrelevant posts,
  2. asks Claude whether the post requires action *for this add-on* and, if so, drafts a short
     backlog item (why it matters + suggested action + severity),
  3. opens ONE labelled GitHub issue per relevant post, de-duped by an embedded post-URL marker
     so the same post is never filed twice.

Self-contained (stdlib only: urllib + xml.etree) so the CI job needs no third-party deps. A
no-op when ANTHROPIC_API_KEY is absent (dormant), and `--dry-run` files nothing.

Env: GITHUB_REPOSITORY (owner/name), GITHUB_TOKEN (issues:write), ANTHROPIC_API_KEY, and
optional ANTHROPIC_MODEL / HA_WATCH_DAYS.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

FEEDS = [
    ("HA developer blog", "https://developers.home-assistant.io/blog/rss.xml"),
    ("HA release blog", "https://www.home-assistant.io/atom.xml"),
]
# Cheap pre-filter: only posts whose title/summary mention something that could plausibly touch
# a container add-on or its Python data layer are sent to Claude. Conservative on the keep side.
KEYWORDS = (
    "deprecat", "breaking", "backward-incompat", "backward incompat", "removed", "removal",
    "python", "alpine", "base image", "supervisor", "add-on", "addon", "quality scale",
    "manifest", "minimum version", "end of life", "eol", "bashio", "s6-overlay", "s6 overlay",
    "mqtt", "discovery", "hassio", "security", "cve", "container", "docker",
)
LABEL = "ha-cadence"
MARKER = "<!-- ha-cadence-post:"   # embedded in the issue body; used for de-dup
ADDON_CONTEXT = (
    "A Home Assistant ADD-ON (container image) for the Alpine A290 EV. It is a Python asyncio "
    "app that polls the Renault/Kamereon API via the pinned `renault-api` library and publishes "
    "sensors/binary_sensors/buttons/numbers over MQTT auto-discovery. It ships as a multi-arch "
    "image FROM ghcr.io/home-assistant/base (Alpine, Python 3.14), uses bashio + s6-overlay, a "
    "HEALTHCHECK, and the HA Supervisor builder. It is NOT a custom integration/component."
)


# The HA blogs sit behind a CDN that 403s the default python-urllib User-Agent, so send a
# descriptive one. Harmless for the GitHub/Anthropic API calls (which key off their own headers).
_UA = "ha-cadence-watch/1.0 (+https://github.com/MatthewHobbs/a290-ha-addon)"


def _get(url, headers=None, data=None, timeout=30):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", _UA)
    req = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:  # ISO 8601 (Atom), e.g. 2026-06-23T00:00:00Z
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:  # RFC 822 (RSS), e.g. Tue, 23 Jun 2026 00:00:00 GMT
        return parsedate_to_datetime(text)
    except (ValueError, TypeError):
        return None


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def fetch_entries(url):
    """Parse an RSS or Atom feed into [{title, link, summary, published(datetime|None)}]."""
    root = ElementTree.fromstring(_get(url))
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    if root.tag.endswith("rss"):  # RSS 2.0
        for item in root.findall("./channel/item"):
            out.append({
                "title": _text(item.find("title")),
                "link": _text(item.find("link")),
                "summary": _text(item.find("description")),
                "published": _parse_date(_text(item.find("pubDate"))),
            })
    else:  # Atom
        for entry in root.findall(f"{atom}entry"):
            link = ""
            for ln in entry.findall(f"{atom}link"):
                rel = ln.get("rel", "alternate")
                if rel == "alternate" or not link:
                    link = ln.get("href", "")
            summary = entry.find(f"{atom}summary")
            if summary is None:
                summary = entry.find(f"{atom}content")
            out.append({
                "title": _text(entry.find(f"{atom}title")),
                "link": link,
                "summary": _text(summary),
                "published": _parse_date(_text(entry.find(f"{atom}published"))
                                         or _text(entry.find(f"{atom}updated"))),
            })
    return out


def keyword_hit(entry):
    blob = (entry["title"] + " " + entry["summary"]).lower()
    return any(k in blob for k in KEYWORDS)


def already_filed(repo, token, link):
    """True if an open OR closed ha-cadence issue already embeds this post's URL marker. Lists
    the labelled issues and substring-matches (reliable, vs the search index's lag/tokenizing).
    Caps at 100 — far beyond years of weekly posts; worst case past that is a rare duplicate."""
    marker = f"{MARKER}{link}"
    url = (f"https://api.github.com/repos/{repo}/issues"
           f"?labels={urllib.parse.quote(LABEL)}&state=all&per_page=100")
    try:
        issues = json.loads(_get(url, headers={"Authorization": f"Bearer {token}",
                                               "Accept": "application/vnd.github+json"}))
        return any(marker in (i.get("body") or "") for i in issues)
    except urllib.error.HTTPError as err:
        print(f"  ! issue list failed ({err.code}); assuming not filed", file=sys.stderr)
        return False


def assess(entry, api_key, model):
    """Ask Claude whether the post needs add-on action. Returns a dict or None on error."""
    prompt = (
        f"You triage Home Assistant blog posts for the maintainer of this project:\n{ADDON_CONTEXT}\n\n"
        "Decide whether the blog post below describes something that REQUIRES action or tracking "
        "for THIS add-on specifically (e.g. a base-image/Python/Supervisor change, an add-on "
        "config/manifest/quality-scale rule, a deprecation or breaking change touching add-ons or "
        "the renault-api/MQTT/bashio/s6 stack, or a security item). Ignore posts that only affect "
        "custom integrations/components, the frontend, or unrelated features. Be conservative — "
        "only flag genuinely actionable items.\n\n"
        f"TITLE: {entry['title']}\nURL: {entry['link']}\n\nCONTENT:\n{entry['summary'][:6000]}\n\n"
        'Reply with ONLY a JSON object: {"relevant": true|false, "severity": "high|medium|low", '
        '"rationale": "<=2 sentences on why it matters here", "action": "<=2 sentences suggested '
        'next step"}. If not relevant, just {"relevant": false}.'
    )
    body = json.dumps({"model": model, "max_tokens": 600,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    try:
        raw = _get("https://api.anthropic.com/v1/messages", data=body,
                   headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                            "content-type": "application/json"}, timeout=60)
        text = json.loads(raw)["content"][0]["text"].strip()
        if text.startswith("```"):  # tolerate ```json fences
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, ValueError, IndexError) as err:
        print(f"  ! Claude assessment failed for {entry['link']}: {err}", file=sys.stderr)
        return None


def create_issue(repo, token, source, entry, verdict):
    sev = verdict.get("severity", "low")
    title = f"[HA cadence] {entry['title']}"
    body = (
        f"{MARKER}{entry['link']} -->\n\n"
        f"Flagged by the HA-cadence watcher from the **{source}**.\n\n"
        f"- **Post:** {entry['link']}\n- **Severity:** {sev}\n\n"
        f"**Why it matters here**\n{verdict.get('rationale', '(n/a)')}\n\n"
        f"**Suggested action**\n{verdict.get('action', '(n/a)')}\n\n"
        "---\n_Auto-filed; close if not applicable. Relevance was judged by Claude against the "
        "add-on context — verify before acting._"
    )
    payload = json.dumps({"title": title, "body": body, "labels": [LABEL, f"severity:{sev}"]}).encode()
    res = json.loads(_get(f"https://api.github.com/repos/{repo}/issues", data=payload,
                          headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json",
                                   "content-type": "application/json"}))
    return res.get("html_url", "?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="assess + print, but file nothing")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    days = int(os.environ.get("HA_WATCH_DAYS", "10"))
    if not api_key:
        print("No ANTHROPIC_API_KEY — HA-cadence watcher is dormant (add the secret to enable).")
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filed = 0
    for source, url in FEEDS:
        try:
            entries = fetch_entries(url)
        except (urllib.error.URLError, ElementTree.ParseError) as err:
            print(f"! could not read {source} ({url}): {err}", file=sys.stderr)
            continue
        for e in entries:
            if not e["link"] or (e["published"] and e["published"] < cutoff):
                continue
            if not keyword_hit(e):
                continue
            if not args.dry_run and already_filed(repo, token, e["link"]):
                print(f"  = already filed: {e['title']}")
                continue
            verdict = assess(e, api_key, model)
            if not verdict or not verdict.get("relevant"):
                print(f"  - not relevant: {e['title']}")
                continue
            if args.dry_run:
                print(f"  * WOULD FILE [{verdict.get('severity')}]: {e['title']}\n"
                      f"      {verdict.get('rationale')}")
                continue
            url_out = create_issue(repo, token, source, e, verdict)
            filed += 1
            print(f"  + filed [{verdict.get('severity')}]: {e['title']} -> {url_out}")
    print(f"Done — {filed} issue(s) filed.")


if __name__ == "__main__":
    main()
