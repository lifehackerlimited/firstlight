#!/usr/bin/env python3
"""Nascent email digests and Team API feeds. Standard library only.

Stripe is the subscriber list (including the free plan), so there's no database to run or protect.
Each plan's Stripe price carries metadata `nascent_plan` = free | pro | student | team.
Choices made at checkout (keywords, digest frequency, team emails) come from the Checkout Session's
custom fields.

Modes:
  python scripts/digest.py daily       Daily digest for Pro/Student/Team subscribers who chose daily,
                                       plus the weekly digest for everyone else on Mondays.
  python scripts/digest.py team-api    Write each active Team subscriber's private API files into site/api/.
  python scripts/digest.py preview     Write sample digests to ./digest-preview/ without contacting Stripe.

Secrets (GitHub Actions):
  STRIPE_KEY       a Stripe *restricted* key with read-only access (Customers, Subscriptions, Checkout Sessions, Prices)
  RESEND_API_KEY   sends the emails (no key: digests are written to ./digest-preview/ instead of sent)
  DIGEST_FROM      e.g. "Nascent <digest@nascent.news>" (a sender on a domain verified in Resend)
  ARCHIVE_KEY      random secret used to derive private Team API addresses
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json")))
SITE = (os.environ.get("SITE_URL") or CFG["site_url"]).rstrip("/")
NAME = CFG["site_name"]
NOW = datetime.now(timezone.utc)
E = html.escape
ENV = os.environ.get
PREVIEW_DIR = os.path.join(ROOT, "digest-preview")
ACADEMIC = re.compile(r"(\.ac\.[a-z]{2}|\.edu(\.[a-z]{2})?|\.uni-[a-z]+\.[a-z]{2}|\.edu\.au)$", re.I)


# ------------------------------------------------------------------ HTTP helpers
def get_json(url: str, headers: dict | None = None, data: bytes | None = None) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "Nascent-Digest/1.0", **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read(60_000_000))


def stripe(path: str, **params) -> dict:
    key = ENV("STRIPE_KEY")
    q = urllib.parse.urlencode(params, doseq=True)
    return get_json(f"https://api.stripe.com/v1/{path}?{q}", {"Authorization": f"Bearer {key}"})


def stripe_list(path: str, **params):
    after = None
    while True:
        page = stripe(path, limit=100, **({"starting_after": after} if after else {}), **params)
        yield from page["data"]
        if not page.get("has_more"):
            return
        after = page["data"][-1]["id"]


# ------------------------------------------------------------------ subscribers
def subscribers() -> list[dict]:
    """Active and trialling subscriptions, with the choices the subscriber made at checkout."""
    out = []
    for status in ("active", "trialing"):
        for sub in stripe_list("subscriptions", status=status, **{"expand[]": ["data.customer"]}):
            price = sub["items"]["data"][0]["price"]
            plan = (price.get("metadata") or {}).get("nascent_plan")
            if not plan:
                continue  # some other product on the same Stripe account (e.g. Bid Ben)
            cust = sub.get("customer") or {}
            if isinstance(cust, str) or cust.get("deleted"):
                continue
            fields = {}
            sessions = stripe("checkout/sessions", subscription=sub["id"]).get("data", [])
            for f in (sessions[0].get("custom_fields") if sessions else []) or []:
                val = (f.get("text") or {}).get("value") or (f.get("dropdown") or {}).get("value")
                if val:
                    fields[f["key"]] = val
            emails = [cust.get("email")]
            if plan == "team":
                extra = re.split(r"[,;\s]+", fields.get("teamemails", ""))
                emails += [e for e in extra if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)][: CFG["plans"]["team_seats"] - 1]
            out.append({
                "id": sub["id"], "plan": plan, "status": sub["status"], "name": cust.get("name") or "",
                "emails": [e for e in dict.fromkeys(emails) if e],
                "keywords": [k.strip().lower() for k in re.split(r"[,;\n]+", fields.get("keywords", "")) if k.strip()][:10],
                "frequency": "weekly" if plan == "free" else fields.get("frequency", "daily"),
            })
    return out


# ------------------------------------------------------------------ content
def load_site():
    feed = get_json(f"{SITE}/data/feed.json")
    try:
        archive = get_json(f"{SITE}/data/archive.json")
    except Exception:
        archive = []
    return feed, archive


def rank(i):
    return (bool(i.get("signal")), i.get("coverage", i.get("cv", 1)), i.get("heat") or 0, bool(i.get("ai")))


def from_archive(a):
    return {"id": a["id"], "title": a["t"], "url": a["u"], "source": a["s"], "kind": a["k"], "published": a["p"],
            "topics": a.get("tp", []), "summary": a.get("sm", ""), "ai": a.get("ai"), "coverage": a.get("cv", 1)}


def pick(feed, archive, keywords, days, n):
    since = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pool = {i["id"]: i for i in map(from_archive, archive) if i["published"] >= since}
    pool.update({i["id"]: i for i in feed["items"] if i["published"] >= since})
    items = sorted(pool.values(), key=rank, reverse=True)
    hay = lambda i: f"{i['title']} {i.get('summary', '')} {i.get('ai') or ''}".lower()
    matched = [i for i in items if keywords and any(k in hay(i) for k in keywords)]
    rest = [i for i in items if i not in matched]
    return matched[:n], rest[: max(n - len(matched[:n]), 6)]


def safe(u):
    return u if isinstance(u, str) and re.match(r"^https?://", u) else SITE


def story_html(i):
    why = i.get("ai") or i.get("summary") or ""
    return (f'<tr><td style="padding:14px 0;border-bottom:1px solid #e6e6e1">'
            f'<div style="font:600 11px Menlo,monospace;color:#6b7280;text-transform:uppercase;letter-spacing:.06em">{E(i["kind"])} · {E(i["source"])}</div>'
            f'<a href="{E(safe(i["url"]))}" style="display:block;margin:4px 0;font:500 19px Georgia,serif;color:#14161b;text-decoration:none">{E(i["title"])}</a>'
            f'<div style="font:14px/1.5 -apple-system,Segoe UI,sans-serif;color:#4b5260">{E(why[:300])}</div></td></tr>')


def render(sub, feed, archive, weekly: bool, api_url: str | None = None):
    paid = sub["plan"] != "free"
    days = 7 if weekly else 1.5
    matched, top = pick(feed, archive, sub["keywords"], days, 12 if paid else 10)
    title = f"{NAME} {'weekly' if weekly else 'daily'}: " + (matched or top or [{"title": "the latest"}])[0]["title"][:70]
    parts = [f'<p style="font:15px -apple-system,Segoe UI,sans-serif;color:#4b5260">{"Your" if paid else "This week in"} emerging tech, {NOW:%A %-d %B}.</p>']
    brief = (feed.get("brief") or {}).get("text") if paid else None
    if brief:
        lis = "".join(f"<li style='margin:0 0 6px'>{E(l.lstrip('•-* ').strip())}</li>" for l in brief.splitlines() if l.strip())
        parts.append(f'<h2 style="font:500 20px Georgia,serif;margin:24px 0 8px">Today\'s brief</h2>'
                     f'<ul style="font:14.5px/1.55 -apple-system,Segoe UI,sans-serif;color:#30343c;padding-left:18px">{lis}</ul>'
                     f'<p style="font:11px Menlo,monospace;color:#9097a3">AI-written from today\'s top stories. Check the sources.</p>')
    sigs = feed.get("signals") or []
    if sigs:
        rows = "".join(f'<li style="margin:0 0 4px"><b>{E(s["term"])}</b> <span style="color:#6b7280">({s["mentions"]} mentions, {s["sources"]} source groups)</span></li>' for s in sigs[: 5 if paid else 3])
        parts.append(f'<h2 style="font:500 20px Georgia,serif;margin:24px 0 8px">Nascent signals</h2>'
                     f'<p style="font:13px -apple-system,sans-serif;color:#6b7280;margin:0 0 6px">Rising across papers, code and discussion before the mainstream.</p>'
                     f'<ul style="font:14px -apple-system,Segoe UI,sans-serif;padding-left:18px">{rows}</ul>')
    if matched:
        parts.append(f'<h2 style="font:500 20px Georgia,serif;margin:24px 0 0">Matching your keywords</h2>'
                     f'<p style="font:12px Menlo,monospace;color:#9097a3;margin:4px 0">{E(", ".join(sub["keywords"]))}</p>'
                     f'<table width="100%" cellpadding="0" cellspacing="0">{"".join(map(story_html, matched))}</table>')
    parts.append(f'<h2 style="font:500 20px Georgia,serif;margin:24px 0 0">{"Also important" if matched else "Top stories"}</h2>'
                 f'<table width="100%" cellpadding="0" cellspacing="0">{"".join(map(story_html, top))}</table>')
    if api_url:
        parts.append(f'<p style="font:13px -apple-system,sans-serif;color:#4b5260;margin-top:24px"><b>Your team API</b> (keep private): '
                     f'<a href="{E(api_url)}feed.json">{E(api_url)}feed.json</a> and <a href="{E(api_url)}archive.json">archive.json</a> (180 days).</p>')
    if not paid:
        parts.append(f'<p style="font:14px -apple-system,sans-serif;margin-top:24px">Want this every morning, filtered to your keywords, with an AI brief? '
                     f'<a href="{SITE}/pricing/">Try Pro free for {CFG["plans"]["trial_days"]} days</a>.</p>')
    portal = CFG["stripe"].get("portal") or f"{SITE}/account/"
    c = CFG["company"]
    footer = (f'<p style="font:12px -apple-system,sans-serif;color:#9097a3;margin-top:32px;border-top:1px solid #e6e6e1;padding-top:14px">'
              f'You get this because you subscribed to {E(NAME)} ({E(sub["plan"].title())}). '
              f'<a href="{E(portal)}">Change keywords, plan or unsubscribe</a> · <a href="{SITE}">Open {E(NAME)}</a><br>'
              f'{E(c["name"])} · {E(c["email"])}</p>')
    body = (f'<!doctype html><html><body style="margin:0;background:#f6f6f3">'
            f'<div style="max-width:640px;margin:0 auto;padding:28px 22px;background:#ffffff">'
            f'<div style="font:700 15px -apple-system,sans-serif;letter-spacing:.04em">{E(NAME)}</div>'
            f'{"".join(parts)}{footer}</div></body></html>')
    text = "\n\n".join(f"{i['title']}\n{i['url']}" for i in matched + top)
    return title, body, text + f"\n\nManage or unsubscribe: {portal}", portal


def send(messages: list[dict]):
    key, sender = ENV("RESEND_API_KEY"), ENV("DIGEST_FROM")
    if not (key and sender):
        os.makedirs(PREVIEW_DIR, exist_ok=True)
        for n, m in enumerate(messages):
            with open(os.path.join(PREVIEW_DIR, f"{n:03d}-{re.sub(r'[^a-z0-9]+', '-', m['to'][0].lower())}.html"), "w") as f:
                f.write(m["html"])
        print(f"No RESEND_API_KEY/DIGEST_FROM: wrote {len(messages)} previews to {PREVIEW_DIR}", file=sys.stderr)
        return
    for n in range(0, len(messages), 100):
        batch = [{"from": sender, **m} for m in messages[n:n + 100]]
        get_json("https://api.resend.com/emails/batch",
                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json.dumps(batch).encode())
    print(f"Sent {len(messages)} digests.", file=sys.stderr)


# ------------------------------------------------------------------ modes
def team_path(sub_id: str) -> str:
    key = ENV("ARCHIVE_KEY", "").encode()
    return "api/" + hmac.new(key, f"team:{sub_id}".encode(), hashlib.sha256).hexdigest()[:32] + "/"


def mode_daily():
    feed, archive = load_site()
    subs = subscribers()
    weekly_day = NOW.weekday() == 0  # Monday
    msgs, students_to_check = [], []
    for s in subs:
        weekly = s["frequency"] == "weekly"
        if weekly and not weekly_day:
            continue
        if s["plan"] == "student" and not any(ACADEMIC.search(e.split("@")[-1]) for e in s["emails"]):
            students_to_check.append(s["emails"][0])
        api = f"{SITE}/{team_path(s['id'])}" if s["plan"] == "team" and ENV("ARCHIVE_KEY") else None
        subject, body, text, portal = render(s, feed, archive, weekly, api)
        for e in s["emails"]:
            msgs.append({"to": [e], "subject": subject, "html": body, "text": text,
                         "headers": {"List-Unsubscribe": f"<{portal}>"}})
    send(msgs)
    if students_to_check:
        print(f"Student plans without an academic email (please review in Stripe): {len(students_to_check)}", file=sys.stderr)


def mode_team_api():
    if not (ENV("STRIPE_KEY") and ENV("ARCHIVE_KEY")):
        print("Team API: STRIPE_KEY or ARCHIVE_KEY not set, skipping.", file=sys.stderr)
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import enrich
    src = os.path.join(ROOT, "site", enrich.private_archive_path(ENV("ARCHIVE_KEY")))
    full = json.load(open(src)) if os.path.exists(src) else []
    feed = json.load(open(os.path.join(ROOT, "site", "data", "feed.json")))
    teams = [s for s in subscribers() if s["plan"] == "team"]
    for s in teams:
        d = os.path.join(ROOT, "site", team_path(s["id"]))
        os.makedirs(d, exist_ok=True)
        json.dump(feed, open(os.path.join(d, "feed.json"), "w"), ensure_ascii=False, separators=(",", ":"))
        json.dump(full, open(os.path.join(d, "archive.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"Team API: wrote private feeds for {len(teams)} team(s).", file=sys.stderr)


def mode_preview():
    feed, archive = load_site()
    samples = [
        {"id": "sub_preview_free", "plan": "free", "emails": ["free@example.com"], "keywords": [], "frequency": "weekly", "name": ""},
        {"id": "sub_preview_pro", "plan": "pro", "emails": ["pro@example.com"], "keywords": ["quantum", "robot", "battery"], "frequency": "daily", "name": ""},
    ]
    msgs = []
    for s in samples:
        subject, body, text, portal = render(s, feed, archive, s["frequency"] == "weekly")
        msgs.append({"to": s["emails"], "subject": subject, "html": body, "text": text})
        print(f"{s['plan']}: {subject}", file=sys.stderr)
    os.environ.pop("RESEND_API_KEY", None)
    send(msgs)


if __name__ == "__main__":
    mode = (sys.argv[1:] or ["preview"])[0]
    if mode in ("daily", "team-api") and not ENV("STRIPE_KEY"):
        print(f"{mode}: STRIPE_KEY not set, skipping.", file=sys.stderr)
        sys.exit(0)
    {"daily": mode_daily, "team-api": mode_team_api, "preview": mode_preview}[mode]()
