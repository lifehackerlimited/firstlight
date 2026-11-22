#!/usr/bin/env python3
"""Nascent site builder.

Turns config.json + site/data/feed.json into the static parts of the site:
pricing / account / legal pages, crawlable topic pages, Atom feeds, sitemap,
robots.txt, the PWA manifest and assets/config.js. Standard library only.

Run after collect.py (the GitHub workflow does this automatically).
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
CFG = json.load(open(os.path.join(ROOT, "config.json")))
FEED_PATH = os.path.join(SITE, "data", "feed.json")
FEED = json.load(open(FEED_PATH)) if os.path.exists(FEED_PATH) else {"items": [], "topics": {}, "sources": []}

E = html.escape
NAME = CFG["site_name"]
BASE = (os.environ.get("SITE_URL") or CFG["site_url"]).rstrip("/")  # SITE_URL overrides for local testing
ABS = (urllib.parse.urlparse(BASE).path.rstrip("/") or "") + "/"  # e.g. "/nascent/" or "/"
NOW = datetime.now(timezone.utc)
UPDATED = "22 November 2026"
P = CFG["plans"]
CUR = P["currency"]
KIND_ONE = {"research": "Research", "news": "News", "insight": "Insight", "solution": "Solution",
            "launch": "Launch", "learn": "Learn", "discussion": "Discussion"}


# ------------------------------------------------------------------ shared bits
def csp() -> str:
    gc = (CFG.get("analytics") or {}).get("goatcounter", "").strip()
    script, img, connect = "'self'", "'self' data:", "'self'"
    if gc:
        script += " https://gc.zgo.at"
        img += f" https://{gc}.goatcounter.com"
        connect += f" https://{gc}.goatcounter.com"
    return (f"default-src 'none'; script-src {script}; style-src 'self'; img-src {img}; connect-src {connect}; "
            "manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'")


def analytics_tag() -> str:
    gc = (CFG.get("analytics") or {}).get("goatcounter", "").strip()
    if not gc:
        return ""
    return f'<script data-goatcounter="https://{E(gc)}.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>'


def meta(title: str, desc: str, path: str, noindex: bool = False) -> str:
    url = BASE + "/" + path.lstrip("/")
    og = f"{BASE}/assets/og.png"
    has_og = os.path.exists(os.path.join(SITE, "assets", "og.png"))
    lines = [
        f'<meta http-equiv="Content-Security-Policy" content="{csp()}">',
        '<meta name="referrer" content="no-referrer">',
        '<meta name="color-scheme" content="dark light">',
        f"<title>{E(title)}</title>",
        f'<meta name="description" content="{E(desc)}">',
        f'<link rel="canonical" href="{E(url)}">',
        f'<meta property="og:site_name" content="{E(NAME)}">',
        f'<meta property="og:title" content="{E(title)}">',
        f'<meta property="og:description" content="{E(desc)}">',
        f'<meta property="og:url" content="{E(url)}">',
        '<meta property="og:type" content="website">',
        f'<meta name="twitter:card" content="{"summary_large_image" if has_og else "summary"}">',
        f'<link rel="icon" href="{ABS}assets/icon.svg" type="image/svg+xml">',
        f'<link rel="manifest" href="{ABS}manifest.webmanifest">',
        '<meta name="theme-color" content="#0a0c11">',
        f'<link rel="alternate" type="application/atom+xml" title="{E(NAME)} — latest" href="{ABS}feed.xml">',
        f'<link rel="stylesheet" href="{ABS}assets/styles.css">',
        f'<script src="{ABS}assets/theme.js"></script>',
        f'<script src="{ABS}assets/config.js"></script>',
    ]
    if has_og:
        lines.append(f'<meta property="og:image" content="{E(og)}">')
    if noindex:
        lines.append('<meta name="robots" content="noindex">')
    tag = analytics_tag()
    if tag:
        lines.append(tag)
    return "\n  ".join(lines)


def footer() -> str:
    repo = CFG["github_repo"]
    c = CFG["company"]
    return f"""<footer class="footer">
    <nav class="footer-links" aria-label="Site">
      <a href="{ABS}topics/">Topics</a><a href="{ABS}pricing/">Pricing</a><a href="{ABS}account/">Account</a>
      <a href="{ABS}feed.xml">RSS</a><a href="https://github.com/{E(repo)}/issues/new?template=suggest-source.yml" rel="noopener">Suggest a source</a>
      <a href="{ABS}privacy/">Privacy</a><a href="{ABS}terms/">Terms</a><a href="{ABS}contact/">Contact</a>
      <a href="https://github.com/{E(repo)}" rel="noopener">Open source</a>
    </nav>
    <p>{E(NAME)} is open source, built on MCT (Media Collection Tool), and run by {E(c["name"])}. Content belongs to the original publishers; every story links to its source.</p>
  </footer>"""


def page(path: str, title: str, desc: str, body: str, noindex: bool = False, scripts: tuple = ()) -> None:
    sc = "".join(f'\n  <script src="{ABS}assets/{s}" defer></script>' for s in ("pages.js",) + tuple(scripts))
    doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {meta(title, desc, path, noindex)}{sc}
</head>
<body class="subpage">
  <a class="skip" href="#main">Skip to content</a>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="{ABS}" aria-label="{E(NAME)} home">
        <img src="{ABS}assets/icon.svg" alt="" width="28" height="28">
        <span class="brand-name">{E(NAME)}</span>
        <span class="brand-sub">Tech at the moment it's born</span>
      </a>
      <nav class="top-nav" aria-label="Main">
        <a href="{ABS}">Feed</a><a href="{ABS}topics/">Topics</a><a href="{ABS}pricing/">Pricing</a><a href="{ABS}account/">Account</a>
      </nav>
      <div class="top-actions">
        <button class="icon-btn" id="theme" type="button" aria-label="Toggle light and dark theme" title="Theme">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>
        </button>
      </div>
    </div>
  </header>
  <main id="main" class="page">
{body}
  </main>
  {footer()}
</body>
</html>
"""
    out = os.path.join(SITE, path.strip("/"), "index.html") if not path.endswith(".html") else os.path.join(SITE, path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(doc)


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%-d %b %Y")
    except Exception:
        return iso[:10]


def safe(u: str | None) -> str | None:
    return u if isinstance(u, str) and re.match(r"^https?://", u) else None


def story(i: dict) -> str:
    badges = ""
    if i.get("paywall"):
        badges += f'<span class="badge badge-paywall" title="May need a subscription">{E(i["paywall"])}</span>'
    if i.get("coverage", 1) > 1:
        badges += f'<span class="badge">{i["coverage"]} sources</span>'
    summary = i.get("ai") or i.get("summary") or ""
    href = safe(i.get("url")) or "#"
    return f"""      <article class="card" data-kind="{E(i.get("kind", ""))}">
        <div class="card-meta"><span class="kind">{E(KIND_ONE.get(i.get("kind"), i.get("kind", "")))}</span><span class="source">{E(i.get("source", ""))}</span><span class="dot" aria-hidden="true">·</span><time datetime="{E(i.get("published", ""))}">{E(fmt_date(i.get("published", "")))}</time>{badges}</div>
        <h3 class="card-title"><a href="{E(href)}" target="_blank" rel="noopener noreferrer nofollow">{E(i.get("title", ""))}</a></h3>
        {f'<p class="card-summary">{E(summary)}</p>' if summary else ''}
      </article>"""


def company_line() -> str:
    c = CFG["company"]
    bits = [E(c["name"])]
    if c.get("number"):
        bits.append(f"Company number {E(c['number'])} (England &amp; Wales)")
    if c.get("registered_office"):
        bits.append(f"Registered office: {E(c['registered_office'])}")
    bits.append(f'<a href="mailto:{E(c["email"])}">{E(c["email"])}</a>')
    return " · ".join(bits)


# ------------------------------------------------------------------ pages
def build_pricing():
    s = CFG["stripe"]

    def btn(key, label, primary=False):
        url = safe(s.get(key))
        cls = "btn btn-primary" if primary else "btn"
        if url:
            return f'<a class="{cls}" href="{E(url)}" rel="noopener">{E(label)}</a>'
        return f'<span class="{cls} is-disabled" aria-disabled="true">Opening soon</span>'

    t = P["trial_days"]
    body = f"""    <section class="page-hero">
      <p class="eyebrow">Pricing</p>
      <h1>Free to read. <em>Pro</em> to stay ahead.</h1>
      <p class="lede">The whole live feed stays free for everyone, forever. Pro sends you a personal briefing so you never have to go looking. Every paid plan starts with a {t}-day free trial.</p>
      <div class="seg billing-toggle" role="group" aria-label="Billing period">
        <button type="button" data-period="monthly" aria-pressed="true">Monthly</button>
        <button type="button" data-period="yearly" aria-pressed="false">Yearly <span class="save">2 months free</span></button>
      </div>
    </section>
    <section class="plans" aria-label="Plans">
      <article class="plan">
        <h2>Free</h2>
        <p class="price"><span class="amt">{CUR}0</span></p>
        <p class="plan-for">For curious readers</p>
        <ul>
          <li>The full live feed from 140+ sources</li>
          <li>Search, topics, Nascent signals, merged stories</li>
          <li>Saved stories and a private <b>For you</b> view</li>
          <li>Weekly email digest</li>
          <li>RSS feeds for every topic</li>
        </ul>
        {btn("free", "Get the weekly digest")}
      </article>
      <article class="plan plan-featured">
        <p class="plan-tag">Most popular</p>
        <h2>Pro</h2>
        <p class="price" data-monthly="{CUR}{P["pro_monthly"]}<small>/month</small>" data-yearly="{CUR}{P["pro_yearly"]}<small>/year</small>"><span class="amt">{CUR}{P["pro_monthly"]}</span><small>/month</small></p>
        <p class="plan-for">For people who need to know first</p>
        <ul>
          <li>Everything in Free</li>
          <li><b>Daily</b> digest, filtered to your keywords</li>
          <li>AI "why it matters" morning brief</li>
          <li>Nascent signals report: ideas rising across papers, code and discussion before the news</li>
          <li>Cancel anytime in two clicks</li>
        </ul>
        {f'<p class="founder">Founding members: use code <b>{E(CFG["founder_code"])}</b> for {CUR}{P["pro_monthly"] - 2}/month for life (first 100 people, monthly plan).</p>' if CFG.get("founder_code") else ""}
        <span class="plan-btn" data-monthly-btn>{btn("pro_monthly", f"Start {t}-day free trial", True)}</span>
        <span class="plan-btn" data-yearly-btn hidden>{btn("pro_yearly", f"Start {t}-day free trial", True)}</span>
      </article>
      <article class="plan">
        <h2>Student</h2>
        <p class="price"><span class="amt">{CUR}{P["student_monthly"]}</span><small>/month</small></p>
        <p class="plan-for">Everything in Pro, half price</p>
        <ul>
          <li>All Pro features</li>
          <li>For students and university staff</li>
          <li>Sign up with your university email (.ac.uk, .edu and similar)</li>
        </ul>
        {btn("student", f"Start {t}-day free trial")}
      </article>
      <article class="plan">
        <h2>Team</h2>
        <p class="price"><span class="amt">{CUR}{P["team_monthly"]}</span><small>/month</small></p>
        <p class="plan-for">For startups, labs, funds and newsrooms</p>
        <ul>
          <li>Pro digests for up to {P["team_seats"]} people</li>
          <li>Private JSON API with a 180-day archive</li>
          <li>Priority requests for new sources</li>
          <li>Invoices for your finance team</li>
        </ul>
        {btn("team", f"Start {t}-day free trial")}
      </article>
    </section>
    <section class="prose faq">
      <h2>Questions</h2>
      <h3>Will I be charged during the trial?</h3>
      <p>No. You're charged on day {t + 1} unless you cancel first. We'll email you before the trial ends, and you can cancel from your <a href="{ABS}account/">account page</a> in two clicks.</p>
      <h3>Is the website itself free?</h3>
      <p>Yes, always. Paid plans pay for the extras: personal email briefings, AI summaries and the team API. They also keep the site ad-free.</p>
      <h3>Who handles my payment?</h3>
      <p>Stripe. Your card details go straight to Stripe and never touch {E(NAME)}. For paid plans Stripe acts as the reseller (merchant of record), so your receipt comes from Stripe and VAT is handled correctly wherever you live. Prices include VAT where it applies.</p>
      <h3>Can I get a refund?</h3>
      <p>Yes. If you're charged and change your mind within 14 days, email us for a full refund. See the <a href="{ABS}terms/">terms</a>.</p>
    </section>"""
    page("pricing/", f"Pricing — {NAME}", f"{NAME} is free to read. Pro ({CUR}{P['pro_monthly']}/month) adds a daily personal digest, AI briefings and early signals. {P['trial_days']}-day free trial.", body)


def build_account():
    portal = safe(CFG["stripe"].get("portal"))
    manage = (f'<a class="btn btn-primary" href="{E(portal)}" rel="noopener">Manage subscription</a>' if portal
              else '<span class="btn btn-primary is-disabled" aria-disabled="true">Opening soon</span>')
    body = f"""    <section class="page-hero">
      <p class="eyebrow">Account</p>
      <h1>Your account</h1>
      <p class="lede">{E(NAME)} has no passwords to remember or lose. Subscriptions are managed securely by Stripe: enter your email and Stripe sends you a one-time sign-in code.</p>
    </section>
    <section class="account-grid">
      <article class="panel">
        <h2>Subscription &amp; billing</h2>
        <p>Change plan, update your card, download invoices, change your digest email or cancel.</p>
        {manage}
        <p class="muted">Not subscribed yet? <a href="{ABS}pricing/">See plans</a>, every paid plan starts with a {P["trial_days"]}-day free trial.</p>
      </article>
      <article class="panel">
        <h2>Digest keywords</h2>
        <p>Pro digests are filtered to the keywords you chose at sign-up. To change them, email <a href="mailto:{E(CFG["company"]["email"])}?subject=Nascent%20digest%20keywords">{E(CFG["company"]["email"])}</a> from your subscription email.</p>
      </article>
      <article class="panel" id="device-data">
        <h2>On this device</h2>
        <p>Saved stories, followed topics, muted sources and your <b>For you</b> view live only in this browser. They're never sent to us.</p>
        <dl class="device-stats">
          <div><dt>Saved stories</dt><dd data-stat="saved">0</dd></div>
          <div><dt>Followed topics</dt><dd data-stat="follow">0</dd></div>
          <div><dt>Muted sources</dt><dd data-stat="mute">0</dd></div>
        </dl>
        <div class="btn-row">
          <button type="button" class="btn" data-action="export">Export my data</button>
          <button type="button" class="btn btn-danger" data-action="clear">Clear everything</button>
        </div>
      </article>
    </section>"""
    page("account/", f"Account — {NAME}", f"Manage your {NAME} subscription and the data stored on this device.", body, noindex=True)


def build_legal():
    c = CFG["company"]
    em = E(c["email"])
    t = P["trial_days"]
    privacy = f"""    <section class="prose">
      <p class="eyebrow">Legal</p>
      <h1>Privacy policy</h1>
      <p class="muted">Last updated {UPDATED}</p>
      <p>{E(NAME)} is run by {company_line()}. We are the data controller for the personal data described here. We collect as little as possible.</p>
      <h2>Reading the site</h2>
      <p>You don't need an account to read {E(NAME)}. The site sets <b>no cookies</b> and loads no advertising or tracking scripts. Saved stories, followed topics, muted sources, your theme and your <b>For you</b> preferences are stored in your browser's local storage on your device only. They're never sent to us, and you can clear them at any time from the <a href="{ABS}account/">account page</a>.</p>
      <p>The site is hosted on GitHub Pages. GitHub processes visitors' IP addresses to deliver the site and keep it secure (see GitHub's privacy statement).{" We count visits with GoatCounter, which uses no cookies and does not identify individual visitors." if (CFG.get("analytics") or {}).get("goatcounter") else ""}</p>
      <h2>Subscriptions and email digests</h2>
      <p>If you sign up for a plan (including the free weekly digest), we process your name, email address, plan, digest keywords and billing history to provide the service. The lawful basis is <b>performance of a contract</b>. For the free digest, the basis is your <b>consent</b>, which you can withdraw at any time by unsubscribing.</p>
      <ul>
        <li><b>Stripe</b> handles sign-up, payments, invoices, tax and the account portal, and is the reseller (merchant of record) for paid plans. Card details go directly to Stripe and are never seen or stored by us.</li>
        <li><b>Resend</b> delivers digest emails on our behalf.</li>
        <li><b>GitHub</b> runs the automated jobs that build the site and send digests.</li>
      </ul>
      <p>These providers may process data in the United States under the UK International Data Transfer Addendum, the UK–US Data Bridge or equivalent safeguards. We keep subscription and billing records for up to 6 years after your subscription ends to meet UK tax law, then delete them.</p>
      <h2>AI summaries</h2>
      <p>Some story summaries are written with an AI model (Anthropic's Claude). Only public article titles and excerpts are sent. No personal data is sent.</p>
      <h2>Your rights</h2>
      <p>You can ask to access, correct, export or delete your personal data, or object to how we use it, by emailing <a href="mailto:{em}">{em}</a>. We reply within one month. If you're unhappy with how we handle your data, you can complain to the Information Commissioner's Office (ico.org.uk).</p>
    </section>"""
    page("privacy/", f"Privacy policy — {NAME}", f"How {NAME} handles personal data: no cookies, no trackers, minimal data.", privacy)

    terms = f"""    <section class="prose">
      <p class="eyebrow">Legal</p>
      <h1>Terms of service</h1>
      <p class="muted">Last updated {UPDATED}</p>
      <p>These terms apply to {E(NAME)}, provided by {company_line()}. By using the site or subscribing, you agree to them.</p>
      <h2>The service</h2>
      <p>{E(NAME)} collects headlines, short excerpts and links from public sources and organises them. Articles, papers, videos and posts belong to their publishers and authors. Follow the link to read the original. We don't guarantee that third-party content is accurate, complete or available, and listing something isn't an endorsement. AI-written summaries can contain mistakes, so check the original before relying on it.</p>
      <h2>Plans, trials and billing</h2>
      <p>Paid plans are sold through Stripe, which acts as the reseller (merchant of record) and collects any VAT or sales tax due. Stripe's own terms also apply to the payment.</p>
      <ul>
        <li>Paid plans (Pro, Student, Team) start with a {t}-day free trial. Unless you cancel before it ends, your subscription starts automatically and you're charged the price shown at sign-up, then again each month or year until you cancel.</li>
        <li>You can cancel at any time in the account portal. Cancelling stops future payments, and you keep access until the end of the period you've paid for.</li>
        <li>Student plans are for current students and university staff. We may ask for proof, and may move a subscription that doesn't qualify to the Pro price after telling you.</li>
        <li>Team plans cover up to {P["team_seats"]} named people in one organisation. Private API addresses are confidential to your team and must not be shared or republished.</li>
        <li>We may change prices with at least 30 days' notice by email. A change applies from your next renewal after the notice period.</li>
      </ul>
      <h2>Refunds and your right to cancel</h2>
      <p>If you're charged and change your mind, email us within 14 days of the payment for a full refund. This is in addition to your statutory rights as a UK consumer.</p>
      <h2>Acceptable use</h2>
      <p>Please don't overload the site or its data files, try to break its security, or resell access to paid features. Public data files may be used for personal and non-commercial projects. Third-party content in them stays subject to its owners' rights.</p>
      <h2>Liability</h2>
      <p>The free service is provided as is. For paid plans, our total liability to you is limited to the amount you paid in the 12 months before the claim. Nothing in these terms limits liability that can't legally be limited, such as for death or personal injury caused by negligence, or for fraud.</p>
      <h2>Changes and law</h2>
      <p>We may update these terms and will email subscribers about significant changes. These terms are governed by the laws of England and Wales.</p>
      <p>Questions: <a href="mailto:{em}">{em}</a></p>
    </section>"""
    page("terms/", f"Terms of service — {NAME}", f"Terms for using {NAME} and its Pro, Student and Team plans.", terms)

    repo = CFG["github_repo"]
    contact = f"""    <section class="prose">
      <p class="eyebrow">Contact</p>
      <h1>Get in touch</h1>
      <p>{company_line()}</p>
      <h2>Publishers and rights holders</h2>
      <p>{E(NAME)} shows only headlines, short excerpts and links that send readers to your site. If you'd like your content removed, or your feed listed differently, email <a href="mailto:{em}?subject=Content%20request">{em}</a> with the URL. We act on removal requests within 2 working days.</p>
      <h2>Suggest a source</h2>
      <p>Know a great feed, newsletter, lab blog or channel? <a href="https://github.com/{E(repo)}/issues/new?template=suggest-source.yml" rel="noopener">Suggest it on GitHub</a> or email us.</p>
      <h2>Security</h2>
      <p>Found a security issue? Please email us privately rather than opening a public issue.</p>
    </section>"""
    page("contact/", f"Contact — {NAME}", f"Contact {NAME}: support, content removal requests, source suggestions and security reports.", contact)


def ranked(items):
    return sorted(items, key=lambda i: ((i.get("heat") or 0) + 25 * (i.get("coverage", 1) - 1), i.get("published", "")), reverse=True)


def build_topics():
    topics = FEED.get("topics", {})
    items = FEED.get("items", [])
    cards = []
    for k, label in topics.items():
        its = [i for i in items if k in (i.get("topics") or [])]
        if not its:
            continue
        cards.append(f'<a class="topic-card" href="{ABS}topics/{k}/"><span>{E(label)}</span><b>{len(its)}</b></a>')
        top = ranked(its)[:40]
        body = f"""    <section class="page-hero">
      <p class="eyebrow"><a href="{ABS}topics/">Topics</a></p>
      <h1>{E(label)}</h1>
      <p class="lede">The latest {E(label)} research, news, projects and launches from {len({i["sourceId"] for i in its})} sources, ranked by engagement and coverage. Updated {E(fmt_date(FEED.get("generated", "")))}.</p>
      <div class="btn-row"><a class="btn btn-primary" href="{ABS}#topic={k}">Open the live {E(label)} feed</a><a class="btn" href="{ABS}topics/{k}/feed.xml">RSS feed</a></div>
    </section>
    <section class="list topic-list-static">
{chr(10).join(story(i) for i in top)}
    </section>"""
        page(f"topics/{k}/", f"{label}: latest emerging-tech news & research — {NAME}",
             f"Latest {label} research papers, news, open-source projects and launches, collected from {len({i['sourceId'] for i in its})} public sources.", body)
        atom(f"topics/{k}/feed.xml", f"{NAME} — {label}", sorted(its, key=lambda i: i["published"], reverse=True)[:50])
    body = f"""    <section class="page-hero">
      <p class="eyebrow">Topics</p>
      <h1>Browse by topic</h1>
      <p class="lede">Every story is tagged automatically. Pick a topic for the most important recent stories, or follow it in the live feed.</p>
    </section>
    <section class="topic-grid">
      {"".join(cards)}
    </section>"""
    page("topics/", f"Topics — {NAME}", f"Emerging-tech topics on {NAME}: AI, quantum, space, biotech, energy, robotics, security, chips and more.", body)


def atom(path: str, title: str, items: list) -> None:
    def x(s):
        return E(s or "", quote=True)
    entries = []
    for i in items:
        url = safe(i.get("url"))
        if not url:
            continue
        summary = i.get("ai") or i.get("summary") or ""
        entries.append(f"""  <entry>
    <title>{x(i["title"])}</title>
    <link href="{x(url)}"/>
    <id>{x(url)}</id>
    <updated>{x(i["published"])}</updated>
    <author><name>{x(i["source"])}</name></author>
    <category term="{x(i.get("kind"))}"/>
    <summary>{x(summary)}</summary>
  </entry>""")
    doc = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{x(title)}</title>
  <subtitle>{x(CFG["tagline"])}</subtitle>
  <link href="{x(BASE)}/"/>
  <link rel="self" href="{x(BASE + "/" + path)}"/>
  <id>{x(BASE + "/" + path)}</id>
  <updated>{x(FEED.get("generated", NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))}</updated>
{chr(10).join(entries)}
</feed>
"""
    out = os.path.join(SITE, path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(doc)


def build_404():
    body = f"""    <section class="page-hero">
      <p class="eyebrow">404</p>
      <h1>This page hasn't been born yet.</h1>
      <p class="lede">The link may be old, or the page may have moved.</p>
      <div class="btn-row"><a class="btn btn-primary" href="{ABS}">Back to the feed</a><a class="btn" href="{ABS}topics/">Browse topics</a></div>
    </section>"""
    page("404.html", f"Not found — {NAME}", "Page not found.", body, noindex=True)


def build_meta_files():
    paths = ["", "topics/", "pricing/", "privacy/", "terms/", "contact/"]
    paths += [f"topics/{k}/" for k in FEED.get("topics", {}) if any(k in (i.get("topics") or []) for i in FEED.get("items", []))]
    day = NOW.strftime("%Y-%m-%d")
    urls = "\n".join(f"  <url><loc>{E(BASE)}/{p}</loc><lastmod>{day}</lastmod></url>" for p in paths)
    with open(os.path.join(SITE, "sitemap.xml"), "w") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n')
    with open(os.path.join(SITE, "robots.txt"), "w") as f:
        f.write(f"User-agent: *\nAllow: /\nDisallow: {ABS}account/\nDisallow: {ABS}api/\n\nSitemap: {BASE}/sitemap.xml\n")
    manifest = {
        "name": f"{NAME} — {CFG['tagline']}",
        "short_name": NAME,
        "description": CFG["description"],
        "start_url": ABS,
        "scope": ABS,
        "display": "standalone",
        "background_color": "#0a0c11",
        "theme_color": "#0a0c11",
        "icons": [{"src": f"{ABS}assets/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]
        + ([{"src": f"{ABS}assets/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]
           if os.path.exists(os.path.join(SITE, "assets", "icon-512.png")) else []),
    }
    with open(os.path.join(SITE, "manifest.webmanifest"), "w") as f:
        json.dump(manifest, f, indent=2)
    public = {
        "name": NAME, "tagline": CFG["tagline"], "base": ABS, "repo": CFG["github_repo"],
        "plans": P, "stripe": {k: v for k, v in CFG["stripe"].items() if safe(v)},
    }
    with open(os.path.join(SITE, "assets", "config.js"), "w") as f:
        f.write("// Generated by scripts/build.py from config.json. Public values only.\n")
        f.write(f"window.NASCENT_CONFIG = {json.dumps(public, separators=(',', ':'))};\n")


def build_index():
    """Refresh the generated <head> block in the hand-written index.html."""
    p = os.path.join(SITE, "index.html")
    s = open(p).read()
    block = meta(f"{NAME} — {CFG['tagline'].rstrip('.')}", CFG["description"], "")
    s = re.sub(r"<!-- meta:start -->.*?<!-- meta:end -->", f"<!-- meta:start -->\n  {block}\n  <!-- meta:end -->", s, flags=re.S)
    s = re.sub(r"<!-- footer:start -->.*?<!-- footer:end -->", f"<!-- footer:start -->\n  {footer()}\n  <!-- footer:end -->", s, flags=re.S)
    with open(p, "w") as f:
        f.write(s)


def main():
    build_meta_files()
    build_index()
    build_pricing()
    build_account()
    build_legal()
    build_topics()
    build_404()
    atom("feed.xml", f"{NAME} — latest", sorted(FEED.get("items", []), key=lambda i: i["published"], reverse=True)[:60])
    print(f"Built pages for {len(FEED.get('items', []))} items at {BASE}")


if __name__ == "__main__":
    main()
