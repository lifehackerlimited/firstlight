#!/usr/bin/env python3
"""Nascent social auto-poster (optional). Standard library only.

Once a day (on the first refresh after SOCIAL_POST_HOUR UTC) it posts the strongest Nascent signal that hasn't been
posted in the last 14 days to Bluesky and/or X, linking to its signal page on the site. If there's no fresh signal,
it posts the day's top story that has an AI summary instead. Runs after build.py; remembers what it posted in
data/state.json. Each platform is skipped unless its secrets are set, and a failure never fails the site build.

Secrets (GitHub Actions):
  BLUESKY_HANDLE, BLUESKY_APP_PASSWORD                 Bluesky (use an app password, not the account password)
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET   X (OAuth 1.0a user-context keys with write access)
Optional env:
  SOCIAL_POST_HOUR   hour (UTC) after which the daily post goes out, default 12
  SOCIAL_DRY_RUN     "1" prints the posts instead of sending them
  SOCIAL_FORCE       "1" ignores the once-a-day and hour checks (for a manual test run)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "site", "data")
CFG = json.load(open(os.path.join(ROOT, "config.json")))
BASE = (os.environ.get("SITE_URL") or CFG["site_url"]).rstrip("/")
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
ENV = lambda k: (os.environ.get(k) or "").strip()
DRY = ENV("SOCIAL_DRY_RUN") == "1"
FORCE = ENV("SOCIAL_FORCE") == "1"
UA = "Nascent-Social/1.0 (+" + BASE + ")"
STAGE_WORDS = (("paper", "paper", "papers"), ("code", "code project", "code projects"),
               ("talk", "discussion", "discussions"), ("news", "news story", "news stories"))


def load(name, default):
    try:
        return json.load(open(os.path.join(DATA, name)))
    except Exception:
        return default


def http(method, url, headers=None, body=None, timeout=30):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(5_000_000)
            return r.status, (json.loads(raw) if raw and "json" in (r.headers.get("Content-Type") or "") else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")


def utm(url, source):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source={source}&utm_medium=social&utm_campaign=daily"


def nice(term):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build import nice as _nice  # same capitalisation as the site
    return _nice(term)


def stage_line(stages):
    bits = []
    for k, one, many in STAGE_WORDS:
        n = (stages or {}).get(k, 0)
        if n:
            bits.append(f"{n} {one if n == 1 else many}")
    return ", ".join(bits)


# ------------------------------------------------------------------ what to post
def pick(social):
    posted = social.get("posted", {})
    recent = {k for k, d in posted.items() if d >= (NOW - timedelta(days=14)).strftime("%Y-%m-%d")}
    hist = {h["slug"]: h for h in load("signals.json", [])}
    feed = load("feed.json", {})
    sigs = [s for s in feed.get("signals", []) if s.get("slug") in hist and s["slug"] not in recent]
    # Prefer ideas the mainstream hasn't picked up yet; that's the whole point of a signal.
    sigs.sort(key=lambda s: (not s.get("mainstream"), s.get("score", 0)), reverse=True)
    for s in sigs:
        h = hist[s["slug"]]
        if s.get("score", 0) < 40:
            continue
        return {
            "key": s["slug"],
            "headline": f"Early signal: {nice(h['term'])}",
            "body": h.get("about") or "",
            "detail": f"Spotted across {h.get('sources', 0)} independent source groups: {stage_line(h.get('stages'))}.",
            "url": f"{BASE}/signals/{s['slug']}/",
            "card_title": f"{nice(h['term'])}: early emerging-tech signal",
            "card_desc": h.get("about") or f"Spotted by Nascent across {h.get('sources', 0)} source groups.",
        }
    items = [i for i in feed.get("items", []) if i.get("ai") and i["id"] not in recent
             and i.get("published", "") >= (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")]
    items.sort(key=lambda i: ((i.get("heat") or 0) + 25 * (i.get("coverage", 1) - 1)), reverse=True)
    if items:
        i = items[0]
        return {
            "key": i["id"],
            "headline": i["title"][:140],
            "body": i["ai"],
            "detail": f"via {i['source']}",
            "url": f"{BASE}/",
            "card_title": f"{CFG['site_name']}: {CFG['tagline']}",
            "card_desc": CFG["description"],
        }
    return None


def compose(p, limit, url_len=None):
    """Fit headline / body / detail / link into the platform limit (X counts every link as 23 characters)."""
    url_cost = url_len if url_len is not None else len(p["link"])
    parts = [p["headline"], p["body"], p["detail"]]
    while True:
        text = "\n\n".join(x for x in parts if x)
        if len(text) + 2 + url_cost <= limit or not any(parts[1:]):
            break
        if parts[2]:
            parts[2] = ""
        elif parts[1]:
            parts[1] = ""
    text = text[: max(0, limit - 2 - url_cost)]
    return text + "\n\n" + p["link"]


# ------------------------------------------------------------------ Bluesky
def post_bluesky(p):
    handle, pw = ENV("BLUESKY_HANDLE"), ENV("BLUESKY_APP_PASSWORD")
    if not (handle and pw):
        return "skipped (no BLUESKY_HANDLE / BLUESKY_APP_PASSWORD)"
    pds = "https://bsky.social/xrpc"
    target = utm(p["url"], "bluesky")
    p = dict(p, link=re.sub(r"^https?://", "", p["url"]).rstrip("/"))  # short visible link; the facet carries the full URL
    text = compose(p, 300)
    if DRY:
        return "dry run:\n" + text
    st, sess = http("POST", f"{pds}/com.atproto.server.createSession", {"Content-Type": "application/json"},
                    json.dumps({"identifier": handle, "password": pw}).encode())
    if st != 200:
        return f"login failed ({st})"
    auth = {"Authorization": f"Bearer {sess['accessJwt']}"}
    did = sess["did"]
    # Don't double-post if the last run posted but its state didn't get published.
    st, af = http("GET", f"{pds}/app.bsky.feed.getAuthorFeed?actor={urllib.parse.quote(did)}&limit=10", auth)
    if st == 200:
        for f in af.get("feed", []):
            ext = ((f.get("post", {}).get("record", {}).get("embed") or {}).get("external") or {})
            if ext.get("uri", "").split("?")[0] == p["url"]:
                return "already posted"
    record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
              "createdAt": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    b = text.encode("utf-8")
    start = b.rfind(p["link"].encode("utf-8"))
    if start >= 0:
        record["facets"] = [{"index": {"byteStart": start, "byteEnd": start + len(p["link"].encode("utf-8"))},
                             "features": [{"$type": "app.bsky.richtext.facet#link", "uri": target}]}]
    external = {"uri": target, "title": p["card_title"][:300], "description": p["card_desc"][:1000]}
    og = os.path.join(ROOT, "site", "assets", "og.png")
    if os.path.exists(og):
        st, blob = http("POST", f"{pds}/com.atproto.repo.uploadBlob", dict(auth, **{"Content-Type": "image/png"}),
                        open(og, "rb").read())
        if st == 200:
            external["thumb"] = blob["blob"]
    record["embed"] = {"$type": "app.bsky.embed.external", "external": external}
    st, res = http("POST", f"{pds}/com.atproto.repo.createRecord", dict(auth, **{"Content-Type": "application/json"}),
                   json.dumps({"repo": did, "collection": "app.bsky.feed.post", "record": record}).encode())
    return "posted" if st == 200 else f"failed ({st}: {str(res)[:200]})"


# ------------------------------------------------------------------ X
def oauth1_header(method, url, ck, cs, tk, ts):
    q = lambda s: urllib.parse.quote(str(s), safe="~")
    params = {"oauth_consumer_key": ck, "oauth_nonce": secrets.token_hex(16), "oauth_signature_method": "HMAC-SHA1",
              "oauth_timestamp": str(int(time.time())), "oauth_token": tk, "oauth_version": "1.0"}
    base = "&".join([method, q(url), q("&".join(f"{q(k)}={q(v)}" for k, v in sorted(params.items())))])
    key = f"{q(cs)}&{q(ts)}".encode()
    params["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(params.items()))


def post_x(p):
    keys = [ENV(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    if not all(keys):
        return "skipped (no X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET)"
    p = dict(p, link=utm(p["url"], "x"))
    text = compose(p, 280, url_len=23)
    if DRY:
        return "dry run:\n" + text
    url = "https://api.x.com/2/tweets"
    st, res = http("POST", url, {"Authorization": oauth1_header("POST", url, *keys), "Content-Type": "application/json"},
                   json.dumps({"text": text}).encode())
    if st in (200, 201):
        return "posted"
    if st == 403 and "duplicate" in str(res).lower():
        return "already posted"
    return f"failed ({st}: {str(res)[:200]})"


def main():
    state = load("state.json", {})
    social = state.setdefault("social", {})
    if not FORCE:
        if social.get("last_date") == TODAY:
            print("Social: already posted today.", file=sys.stderr)
            return
        if NOW.hour < int(ENV("SOCIAL_POST_HOUR") or 12):
            print("Social: not posting time yet.", file=sys.stderr)
            return
    if not (ENV("BLUESKY_HANDLE") or ENV("X_API_KEY")) and not DRY:
        print("Social: no accounts configured, skipping.", file=sys.stderr)
        return
    p = pick(social)
    if not p:
        print("Social: nothing fresh to post.", file=sys.stderr)
        return
    ok = False
    for name, fn in (("Bluesky", post_bluesky), ("X", post_x)):
        try:
            r = fn(p)
        except Exception as e:  # never fail the site build over a social post
            r = f"error: {type(e).__name__}: {e}"
        print(f"Social {name}: {r}", file=sys.stderr)
        ok = ok or r in ("posted", "already posted")
    if ok and not DRY:
        social["last_date"] = TODAY
        posted = social.setdefault("posted", {})
        posted[p["key"]] = TODAY
        cutoff = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")
        social["posted"] = {k: d for k, d in posted.items() if d >= cutoff}
        json.dump(state, open(os.path.join(DATA, "state.json"), "w"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
