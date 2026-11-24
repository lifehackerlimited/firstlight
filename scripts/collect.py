#!/usr/bin/env python3
"""Nascent collector (built on MCT, the Media Collection Tool).

Pulls every public source listed in sources.json, normalises it into one schema,
tags topics, and writes:

  site/data/feed.json   - the full feed the portal (and Freya) reads
  site/data/latest.md   - a compact Markdown digest, handy for LLM ingestion

Standard library only, so it runs on a stock GitHub Actions runner with nothing
to install (and nothing third-party to trust).

Optional secrets (set as GitHub Actions secrets; never shipped to the browser):
  REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET   official Reddit API (recommended in CI)
  PRODUCTHUNT_TOKEN                          Product Hunt API (else the public RSS feed)
  OPENALEX_API_KEY                           higher OpenAlex limits
  GITHUB_TOKEN                               provided automatically by Actions
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import enrich
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "site", "data")
SOURCES = os.path.join(ROOT, "scripts", "sources.json")
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))

UA = "Nascent-Collector/2.0 (open-source emerging-tech reader; built on MCT)"
TIMEOUT = 20
HOST_TIMEOUT = {"api.biorxiv.org": 45}  # slow API; occasionally takes 20s+ to answer
MAX_BYTES = 6_000_000
MAX_ITEMS = 2200
NOW = datetime.now(timezone.utc)
ENV = os.environ.get

# How many items to take per source, and how far back to look, by content type.
KIND_DEFAULTS = {
    "research":   {"limit": 20, "days": 14},
    "news":       {"limit": 15, "days": 10},
    "insight":    {"limit": 6,  "days": 45},
    "learn":      {"limit": 6,  "days": 60},
    "solution":   {"limit": 20, "days": 14},
    "launch":     {"limit": 20, "days": 7},
    "discussion": {"limit": 12, "days": 4},
}

# Politeness: minimum seconds between requests to the same host (arXiv asks for 3s).
HOST_DELAY = {
    "export.arxiv.org": 3.2,
    "www.reddit.com": 2.5,
    "oauth.reddit.com": 0.8,
    "api.github.com": 1.0,
    "api.openalex.org": 1.5,
    "www.youtube.com": 0.3,
}

TOPICS = {
    "ai": ("AI & ML", r"\b(ai|artificial intelligence|machine learning|deep learning|neural|llms?|language models?|transformers?|gpt\w*|diffusion|agents?|agentic|reinforcement learning|computer vision|multimodal|chatbots?|generative|genai|inference|fine-?tun\w*)\b"),
    "quantum": ("Quantum", r"\b(quantum|qubits?|entangle\w*)\b"),
    "space": ("Space", r"\b(nasa|esa|spacex|satellites?|orbit\w*|rockets?|mars|lunar|moon|astronom\w*|telescopes?|spacecraft|spaceflight|exoplanets?|galax\w*|asteroids?|astrophys\w*)\b"),
    "bio": ("Bio & Health", r"\b(biotech\w*|genom\w*|genes?|crispr|proteins?|drugs?|medical|medicine|health\w*|clinical|dna|rna|vaccines?|neuro\w*|brain|cancer|patients?|synthetic biology|biolog\w*)\b"),
    "energy": ("Energy & Climate", r"\b((?<!dark )energy|batter(y|ies)|solar|fusion|nuclear|climate|carbon|power grid|grid-scale|hydrogen|electric vehicles?|evs?|renewables?|emissions?|geothermal)\b"),
    "robotics": ("Robotics", r"\b(robot\w*|drones?|autonomous|humanoids?|self-driving|manipulation)\b"),
    "security": ("Security", r"\b(security|cyber\w*|vulnerabilit\w*|exploits?|malware|ransomware|encryption|privacy|cves?|breach\w*|zero-day|phishing|attackers?|hackers?)\b"),
    "hardware": ("Chips & Hardware", r"\b(chips?|semiconductors?|gpus?|cpus?|tpus?|processors?|silicon|hardware|risc-v|fpgas?|transistors?|photonic\w*|lithography|tsmc|nvidia)\b"),
    "software": ("Software & Dev", r"\b(open[- ]source|programming|developers?|rust|python|javascript|typescript|compilers?|databases?|kubernetes|linux|frameworks?|apis?|sdks?|cli|librar(y|ies)|github|self-hosted)\b"),
    "materials": ("Materials & Physics", r"\b(materials?|graphene|superconduct\w*|physics|physicists?|alloys?|polymers?|nano\w*|lasers?|optics?|metamaterials?)\b"),
    "education": ("Education", r"\b(education\w*|students?|teach\w*|universit(y|ies)|courses?|curriculum|edtech|classrooms?|tutorials?|lectures?|learners?)\b"),
}
TOPIC_RX = {k: re.compile(rx, re.I) for k, (_, rx) in TOPICS.items()}

STOP = set("""a an the and or of to in on for with from by at as is are was were be been this that these those it its
into over under via using use new how why what when who we our you your their they them can will not no yes than then more
most less about after before up down out off per vs show hn ask based towards toward through across between within
large small model models data system systems approach method methods study paper results learning first just now one two
make made build built get got need open source free tool tools year years day days week weeks says said here there
like only also still even back way time things thing want really apod your i'm it's don't does did has have had
january february march april may june july august september october november december video part guide
scientists researchers research science tech technology correction news update review human people world could would""".split())


# ------------------------------------------------------------------ http
_glock = threading.Lock()
_host_sem: dict[str, threading.Semaphore] = {}


def _sem(host):
    with _glock:
        if host not in _host_sem:
            _host_sem[host] = threading.Semaphore(1 if host in HOST_DELAY else 3)
        return _host_sem[host]


def fetch(url: str, headers: dict | None = None, data: bytes | None = None) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("only https sources are allowed")
    host = urllib.parse.urlparse(url).netloc
    h = {"User-Agent": UA, "Accept": "application/json, application/atom+xml, application/rss+xml, text/xml, */*"}
    if host == "api.github.com" and ENV("GITHUB_TOKEN"):
        h["Authorization"] = "Bearer " + ENV("GITHUB_TOKEN")
    if host.endswith("reddit.com") and ENV("REDDIT_USERNAME"):
        # Reddit's API rules ask for "<platform>:<app id>:<version> (by /u/<username>)".
        h["User-Agent"] = f"web:nascent:2.0 (by /u/{ENV('REDDIT_USERNAME')})"
    h.update(headers or {})
    with _sem(host):
        try:
            for attempt in range(2):
                try:
                    req = urllib.request.Request(url, headers=h, data=data)
                    with urllib.request.urlopen(req, timeout=HOST_TIMEOUT.get(host, TIMEOUT)) as r:
                        body = r.read(MAX_BYTES + 1)
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 502, 503) and attempt == 0:
                        ra = (e.headers.get("Retry-After") or "").strip()
                        time.sleep(min(int(ra), 35) if ra.isdigit() else 6)
                        continue
                    raise
                except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
                    timed_out = isinstance(e, (TimeoutError, socket.timeout)) or isinstance(
                        getattr(e, "reason", None), (TimeoutError, socket.timeout))
                    if timed_out and attempt == 0:  # a slow server gets one more try
                        time.sleep(3)
                        continue
                    raise
        finally:
            time.sleep(HOST_DELAY.get(host, 0))
    if len(body) > MAX_BYTES:
        raise ValueError("response too large")
    return body


def get_json(url, headers=None, data=None):
    return json.loads(fetch(url, headers, data))


# ------------------------------------------------------------------ helpers
def clean(text, limit: int = 360) -> str:
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", str(text), flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(html.unescape(text))
    text = re.sub(r"https?://\S{60,}", "", text)  # drop giant tracking URLs in descriptions
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^arXiv:\S+\s+Announce Type:\s*\S+\s+Abstract:\s*", "", text)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text


def safe_url(u) -> str | None:
    if not u or not isinstance(u, str):
        return None
    u = u.strip()
    p = urllib.parse.urlparse(u)
    return u if p.scheme in ("http", "https") and p.netloc else None


def to_iso(s) -> str | None:
    if s is None or s == "":
        return None
    dt = None
    if isinstance(s, (int, float)):
        dt = datetime.fromtimestamp(s, tz=timezone.utc)
    else:
        s = str(s).strip()
        try:
            dt = parsedate_to_datetime(s)
        except Exception:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt > NOW + timedelta(hours=1):
        dt = NOW
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def item(src, title, url, published, summary="", score=None, comments=None, authors=None, tags=None, extra_url=None):
    url = safe_url(url)
    title = clean(title, 220)
    if not url or not title:
        return None
    return {
        "id": hashlib.sha1(url.encode()).hexdigest()[:12],
        "title": title,
        "url": url,
        "source": src["name"],
        "sourceId": src["id"],
        "kind": src["kind"],
        "summary": clean(summary),
        "published": to_iso(published) or NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "score": score if isinstance(score, (int, float)) else None,
        "comments": safe_url(comments),
        "alt": safe_url(extra_url),
        "authors": [clean(a, 60) for a in (authors or []) if a][:4],
        "tags": [clean(t, 30) for t in (tags or []) if t][:5],
    }


def lim(src):
    return src["limit"]


# ------------------------------------------------------------------ XML (RSS / Atom)
def parse_xml(data: bytes):
    # Refuse DTDs/entities outright (XXE / billion-laughs defence).
    if re.search(rb"<!(DOCTYPE|ENTITY)", data[:4096], re.I):
        data = re.sub(rb"<!DOCTYPE[^>\[]*(\[.*?\])?\s*>", b"", data, count=1, flags=re.S | re.I)
        if re.search(rb"<!ENTITY", data, re.I):
            raise ValueError("XML entities are not allowed")
    root = ET.fromstring(data.lstrip())
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def txt(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def h_rss(src):
    root = parse_xml(fetch(src["url"]))
    entries = root.findall(".//item") or root.findall(".//entry")
    out = []
    for e in entries[: lim(src)]:
        link = None
        for l in e.findall("link"):
            href = l.get("href")
            if href and l.get("rel", "alternate") == "alternate":
                link = href
                break
            if (l.text or "").strip():
                link = l.text.strip()
                break
        if not link:
            g = txt(e.find("guid")) or txt(e.find("id"))
            link = g if g.startswith("http") else None
        summary = (txt(e.find("summary")) or txt(e.find("description")) or txt(e.find(".//description"))
                   or txt(e.find("encoded")) or txt(e.find("content")))
        date = (txt(e.find("published")) or txt(e.find("pubDate")) or txt(e.find("updated"))
                or txt(e.find("date")) or txt(e.find("issued")))
        authors = [txt(a.find("name")) or txt(a) for a in e.findall("author")] + [txt(c) for c in e.findall("creator")]
        tags = [c.get("term") or txt(c) for c in e.findall("category")]
        score = None
        stats = e.find(".//statistics")  # YouTube view counts
        if stats is not None and (stats.get("views") or "").isdigit():
            score = int(stats.get("views"))
        out.append(item(src, txt(e.find("title")), link, date, summary, score=score, authors=authors, tags=tags))
    return out


def h_arxiv(src):
    q = "+OR+".join(f"cat:{c}" for c in src["cats"])
    src = dict(src, url=f"https://export.arxiv.org/api/query?search_query={q}&sortBy=submittedDate&sortOrder=descending&max_results={lim(src)}")
    return h_rss(src)


def h_youtube(src):
    return h_rss(dict(src, url=f"https://www.youtube.com/feeds/videos.xml?channel_id={src['channel']}"))


# ------------------------------------------------------------------ JSON APIs
def h_hn(src):
    data = get_json(src["url"])
    out = []
    for h in data.get("hits", [])[: lim(src)]:
        disc = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append(item(src, h.get("title"), h.get("url") or disc, h.get("created_at"), h.get("story_text") or "",
                        score=h.get("points"), comments=disc, authors=[h.get("author")]))
    return out


def h_lobsters(src):
    data = get_json(src["url"])
    return [item(src, s.get("title"), s.get("url") or s.get("comments_url"), s.get("created_at"),
                 s.get("description_plain") or "", score=s.get("score"), comments=s.get("comments_url"),
                 tags=s.get("tags")) for s in data[: lim(src)]]


_reddit = {"token": None, "tried": False}


def reddit_token():
    cid, sec = ENV("REDDIT_CLIENT_ID"), ENV("REDDIT_CLIENT_SECRET")
    if not (cid and sec):
        return None
    with _glock:
        if not _reddit["tried"]:
            _reddit["tried"] = True
            auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
            try:
                body = fetch("https://www.reddit.com/api/v1/access_token",
                             {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
                             b"grant_type=client_credentials")
                _reddit["token"] = json.loads(body).get("access_token")
            except Exception as e:
                print(f"Reddit OAuth failed, falling back to public JSON: {e}", file=sys.stderr)
    return _reddit["token"]


def h_reddit(src):
    # Reddit blocks anonymous API/RSS access, so this needs a (free) Reddit app's client id + secret.
    sub, n = src["sub"], lim(src)
    tok = reddit_token()
    if not tok:
        raise RuntimeError("Reddit OAuth token unavailable")
    data = get_json(f"https://oauth.reddit.com/r/{sub}/top?t=day&limit={n}&raw_json=1", {"Authorization": f"Bearer {tok}"})
    out = []
    for c in data.get("data", {}).get("children", []):
        d = c.get("data", {})
        if d.get("over_18") or d.get("stickied"):
            continue
        perm = "https://www.reddit.com" + d.get("permalink", "")
        link = d.get("url_overridden_by_dest") or perm
        if d.get("is_self") or re.match(r"https://(i|v|preview)\.redd\.it|https://www\.reddit\.com/(gallery|r/)", link or ""):
            link = perm
        out.append(item(src, d.get("title"), link, d.get("created_utc"), d.get("selftext", ""), score=d.get("score"),
                        comments=perm, authors=[d.get("author")], tags=[d.get("link_flair_text")]))
    return out


def h_hf_papers(src):
    data = get_json(src["url"])
    out = []
    for d in data[: lim(src)]:
        p = d.get("paper", {})
        pid = p.get("id")
        if not pid:
            continue
        out.append(item(src, p.get("title") or d.get("title"), f"https://huggingface.co/papers/{pid}",
                        p.get("publishedAt") or d.get("publishedAt"), p.get("summary"), score=p.get("upvotes"),
                        authors=[a.get("name") for a in p.get("authors", [])], extra_url=f"https://arxiv.org/abs/{pid}"))
    return out


def h_hf_models(src):
    data = get_json(f"https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit={lim(src)}")
    out = []
    for m in data:
        mid = m.get("id") or m.get("modelId")
        if not mid:
            continue
        task = (m.get("pipeline_tag") or "model").replace("-", " ")
        summary = f"Trending {task} on Hugging Face · {m.get('likes', 0):,} likes · {m.get('downloads', 0):,} downloads."
        out.append(item(src, mid, f"https://huggingface.co/{mid}", m.get("createdAt"), summary, score=m.get("likes"),
                        authors=[mid.split("/")[0]], tags=[m.get("pipeline_tag"), m.get("library_name")]))
    return out


def h_github(src):
    since = (NOW - timedelta(days=7)).strftime("%Y-%m-%d")
    q = urllib.parse.urlencode({"q": f"created:>{since} {src.get('q', 'stars:>40')}", "sort": "stars", "order": "desc", "per_page": lim(src)})
    data = get_json(f"https://api.github.com/search/repositories?{q}")
    out = []
    for r in data.get("items", []):
        tags = ([r["language"]] if r.get("language") else []) + r.get("topics", [])
        out.append(item(src, r.get("full_name"), r.get("html_url"), r.get("created_at"), r.get("description"),
                        score=r.get("stargazers_count"), authors=[(r.get("owner") or {}).get("login")], tags=tags))
    return out


def h_devto(src):
    data = get_json(src["url"])
    return [item(src, a.get("title"), a.get("url"), a.get("published_at"), a.get("description"),
                 score=a.get("positive_reactions_count"), comments=a.get("url"),
                 authors=[(a.get("user") or {}).get("name")], tags=a.get("tag_list")) for a in data[: lim(src)]]


def h_openalex(src):
    """Scholarly literature (the open alternative to Google Scholar): recent works on a
    subject, ranked by early citations so the ones already getting traction rise."""
    since = (NOW - timedelta(days=src.get("window", 45))).strftime("%Y-%m-%d")
    params = {
        "search": src["query"],
        "filter": f"from_publication_date:{since},to_publication_date:{NOW:%Y-%m-%d},has_abstract:true,type:article|preprint",
        "sort": "cited_by_count:desc",
        "per-page": lim(src),
    }
    if ENV("OPENALEX_API_KEY"):
        params["api_key"] = ENV("OPENALEX_API_KEY")
    data = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
    out = []
    for w in data.get("results", []):
        inv = w.get("abstract_inverted_index") or {}
        words = sorted(((p, word) for word, ps in inv.items() for p in ps))
        abstract = " ".join(word for _, word in words)
        loc = w.get("primary_location") or {}
        venue = (loc.get("source") or {}).get("display_name")
        url = loc.get("landing_page_url") or w.get("doi") or w.get("id")
        authors = [(a.get("author") or {}).get("display_name") for a in w.get("authorships", [])]
        out.append(item(src, w.get("display_name") or w.get("title"), url, w.get("publication_date"), abstract,
                        score=w.get("cited_by_count"), authors=authors, tags=[venue], extra_url=w.get("doi")))
    return out


def h_biorxiv(src):
    """bioRxiv / medRxiv preprints from the last few days, filtered to the listed subject categories."""
    server, cats = src.get("server", "biorxiv"), {c.lower() for c in src["cats"]}
    start = (NOW - timedelta(days=4)).strftime("%Y-%m-%d")
    out, seen = [], set()
    for cursor in range(0, 500, 100):
        data = get_json(f"https://api.biorxiv.org/details/{server}/{start}/{NOW:%Y-%m-%d}/{cursor}")
        coll = data.get("collection") or []
        for p in coll:
            if (p.get("category") or "").lower() not in cats or p.get("doi") in seen:
                continue
            seen.add(p.get("doi"))
            authors = [a.strip() for a in (p.get("authors") or "").split(";") if a.strip()]
            out.append(item(src, p.get("title"), f"https://www.{server}.org/content/{p.get('doi')}v{p.get('version', 1)}",
                            p.get("date"), p.get("abstract"), authors=authors, tags=[p.get("category")]))
        if len(coll) < 100 or len(out) >= lim(src):
            break
    return out[: lim(src)]


def h_mastodon(src):
    """Links trending on a Mastodon server (public, no login). Scored by how many people shared them."""
    data = get_json(f"https://{src['instance']}/api/v1/trends/links?limit={min(lim(src) * 2, 40)}")
    out = []
    for l in data:
        title = l.get("title") or ""
        if re.search(r"\(@[^)\s]+@[^)\s]+\)", title):
            continue  # a trending fediverse profile/post rather than an article
        hist = l.get("history") or []
        shares = sum(int(h.get("uses", 0)) for h in hist)
        # Without a publish date, use the day it started trending, not "now" (which would pin it to the top).
        when = l.get("published_at") or (int(hist[0]["day"]) if hist and str(hist[0].get("day", "")).isdigit() else None)
        out.append(item(src, title, l.get("url"), when, l.get("description"),
                        score=shares, authors=[l.get("author_name")], tags=[l.get("provider_name")]))
    return out


def h_bluesky(src):
    """Posts from a public Bluesky custom feed. Posts that share a link use the link's title and URL."""
    q = urllib.parse.urlencode({"feed": src["feed"], "limit": 60})
    data = get_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed?{q}")
    out = []
    for f in data.get("feed", []):
        p = f.get("post", {})
        likes = p.get("likeCount", 0) + 2 * p.get("repostCount", 0)
        if likes < src.get("min_likes", 5):
            continue
        rec, author = p.get("record", {}), p.get("author", {})
        handle, rkey = author.get("handle"), (p.get("uri") or "").rsplit("/", 1)[-1]
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        ext = ((p.get("embed") or {}).get("external") or ((p.get("embed") or {}).get("media") or {}).get("external") or {})
        raw = rec.get("text") or ""
        if len(re.findall(r"#\w+", raw)) >= 4:
            continue  # hashtag-stuffed promo posts
        text = clean(re.sub(r"(?:\s*#\w+)+\s*$", "", raw), 600)  # drop trailing hashtag blocks
        ext_title = (ext.get("title") or "").strip()
        if re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/\S*)?", ext_title, re.I) or len(ext_title.split()) < 3:
            ext_title = ""  # link previews that only carry the domain name aren't a headline
        if ext.get("uri") and ext_title:
            ext["title"] = ext_title
            title, url, summary = ext["title"], ext["uri"], text or ext.get("description")
        else:
            if len(text) < 40 or re.fullmatch(r"\S+", text):
                continue  # too short to stand alone as a story
            title, url, summary = clean(text, 140), post_url, text
        out.append(item(src, title, url, rec.get("createdAt") or p.get("indexedAt"), summary, score=likes,
                        comments=post_url, authors=[author.get("displayName") or handle]))
    out.sort(key=lambda i: -(i["score"] or 0) if i else 0)
    return out[: lim(src)]


def h_producthunt(src):
    tok = ENV("PRODUCTHUNT_TOKEN")
    if not tok:
        return h_rss(src)  # public Atom feed, no key needed
    after = (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = """query($after: DateTime!, $n: Int!) { posts(first: $n, order: VOTES, postedAfter: $after) {
      edges { node { name tagline description url votesCount createdAt makers { name }
      topics(first: 3) { edges { node { name } } } } } } }"""
    body = json.dumps({"query": query, "variables": {"after": after, "n": lim(src)}}).encode()
    data = get_json("https://api.producthunt.com/v2/api/graphql",
                    {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}, body)
    out = []
    for e in (((data.get("data") or {}).get("posts") or {}).get("edges") or []):
        n = e.get("node", {})
        tags = [t["node"]["name"] for t in (n.get("topics") or {}).get("edges", [])]
        out.append(item(src, f"{n.get('name')} — {n.get('tagline')}", n.get("url"), n.get("createdAt"),
                        n.get("description"), score=n.get("votesCount"), comments=n.get("url"),
                        authors=[m.get("name") for m in n.get("makers") or []], tags=tags))
    return out


HANDLERS = {
    "rss": h_rss, "arxiv": h_arxiv, "youtube": h_youtube, "hn": h_hn, "lobsters": h_lobsters, "reddit": h_reddit,
    "hf_papers": h_hf_papers, "hf_models": h_hf_models, "github": h_github, "devto": h_devto,
    "openalex": h_openalex, "producthunt": h_producthunt, "biorxiv": h_biorxiv,
    "mastodon": h_mastodon, "bluesky": h_bluesky,
}
# Sources that only work once the named secrets are configured; skipped quietly otherwise.
NEEDS = {"reddit": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"]}


# ------------------------------------------------------------------ pipeline
def load_sources():
    with open(SOURCES) as f:
        groups = json.load(f)
    out = []
    for kind, lst in groups.items():
        if kind.startswith("_"):
            continue
        for s in lst:
            s = {**KIND_DEFAULTS[kind], **s, "kind": s.get("kind", kind)}
            s.setdefault("type", "rss")
            if s["type"] == "reddit":
                s.setdefault("id", f"reddit-{s['sub'].lower()}")
                s.setdefault("name", f"r/{s['sub']}")
            s.setdefault("group", s.get("group") or kind)
            out.append(s)
    ids = [s["id"] for s in out]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"Duplicate source ids: {sorted(dupes)}")
    return out


def run_source(src):
    t0 = time.time()
    missing = [k for k in NEEDS.get(src["type"], []) if not ENV(k)]
    if missing:
        return src, [], "off:" + " + ".join(missing), 0.0
    try:
        items = [i for i in HANDLERS[src["type"]](src) if i]
        for i in items:
            hay = f"{i['title']} {i['summary']} {' '.join(i['tags'])}"
            found = [k for k, rx in TOPIC_RX.items() if rx.search(hay)]
            i["topics"] = list(dict.fromkeys(src.get("topics", []) + found))[:4]
        if src.get("require_topic"):
            # General-interest sources (e.g. Mastodon trends): keep only items that match a tech topic.
            text = lambda i: f"{i['title']} {i['summary']}"
            items = [i for i in items if any(rx.search(text(i)) for rx in TOPIC_RX.values())][: lim(src)]
        return src, items, None, time.time() - t0
    except Exception as e:  # one bad source never breaks the build
        return src, [], f"{type(e).__name__}: {e}"[:160], time.time() - t0


# How much a source's engagement number says about broad importance (default 1.0).
TYPE_WEIGHT = {"youtube": 0.7, "openalex": 0.8, "hf_models": 0.85, "devto": 0.6, "rss": 0.7,
               "bluesky": 0.6, "mastodon": 0.75}


def add_heat(items, weights):
    """Per-source percentile of engagement (0-100), scaled by how meaningful that source's signal is,
    so HN points, GitHub stars and paper upvotes are comparable."""
    by_src = {}
    for i in items:
        if isinstance(i["score"], (int, float)):
            by_src.setdefault(i["sourceId"], []).append(i)
    for group in by_src.values():
        if len(group) < 3:
            continue
        group.sort(key=lambda i: i["score"])
        n = len(group)
        for rank, i in enumerate(group):
            i["heat"] = round(100 * (rank + 1) / n * weights.get(i["sourceId"], 1.0))


# Headline filler: common in titles but says nothing about what's trending.
TREND_STOP = set("""next work help critical today latest report reports update updates news announces announced launch
launches launched world people best top inside could would should every many much really better big biggest major
amid plans plan wants calls call warns warn returns return back gets takes take goes going look looks deal deals
says said following around without against during while where which still ever never another other first last
week month year today's company companies firm users user team teams source sources official officially
code find finds found problem problems test tests testing thing things part parts power control
shows showing reveals revealed explains explained makes making used uses""".split())


def trending(items, limit: int = 16):
    """Most-mentioned word pairs, then words, in the last 72 hours of headlines, with one tag per story cluster:
    a term is skipped when most of its stories are already covered by a term picked before it
    (so "jensen huang", "nvidia ceo" and "huang reinstated" collapse into one tag)."""
    since = (NOW - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = [i for i in items if i["published"] >= since]
    stop = STOP | TREND_STOP
    stories, sources, proper = defaultdict(set), defaultdict(set), Counter()
    for n, i in enumerate(recent):
        raw = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}", i["title"])
        low = [t.lower() for t in raw]
        for k, w in enumerate(raw[1:], 1):  # capitalised mid-headline = a name (Huang, Google, NASA, COP31)
            if w[0].isupper():
                proper[low[k]] += 1
        terms = {t for t in low if t not in stop and len(t) > 3}
        terms |= {f"{a} {b}" for a, b in zip(low, low[1:]) if a not in stop and b not in stop}
        for t in terms:
            stories[t].add(n)
            sources[t].add(i.get("sourceId"))

    def ok(t):
        if len(sources[t]) < 2:
            return False  # one prolific feed isn't a trend
        if " " in t:
            return len(stories[t]) >= 3
        is_name = proper[t] >= len(stories[t]) / 2
        return len(stories[t]) >= (4 if is_name else 6)

    pairs = sorted((t for t in stories if " " in t and ok(t)), key=lambda t: len(stories[t]), reverse=True)
    words = sorted((t for t in stories if " " not in t and ok(t)), key=lambda t: len(stories[t]), reverse=True)
    picked, covered = [], []
    for t in pairs[:limit] + words:  # pairs say more ("jensen huang" beats "huang"), so they go first
        st = stories[t]
        if any(len(st & c) / len(st) >= 0.5 for c in covered):
            continue  # same story cluster as a tag we already have
        picked.append(t)
        covered.append(st)
        if len(picked) >= limit:
            break
    return [{"term": t, "count": len(stories[t])} for t in picked]


def digest(feed):
    lines = [f"# {feed['name']} digest — {feed['generated']}", "",
             "Latest emerging-tech research, news, analysis and launches.", ""]
    if feed.get("brief"):
        lines += ["## Today's brief", "", feed["brief"]["text"], ""]
    if feed.get("signals"):
        lines += ["## Nascent signals (rising before the mainstream)", ""]
        for sg in feed["signals"]:
            st = ", ".join(f"{k} {v}" for k, v in sg["stages"].items() if v)
            lines.append(f"- **{sg['term']}** (score {sg['score']}; {sg['mentions']} mentions across {sg['sources']} source groups: {st})")
        lines.append("")
    for key, (label, _) in TOPICS.items():
        its = [i for i in feed["items"] if key in i["topics"]][:10]
        if not its:
            continue
        lines += [f"## {label}", ""]
        for i in its:
            s = f" — {(i.get('ai') or i['summary'])[:200]}" if (i.get("ai") or i["summary"]) else ""
            lines.append(f"- [{i['title']}]({i['url']}) ({i['source']}, {i['published'][:10]}){s}")
        lines.append("")
    return "\n".join(lines)


def main():
    sources = load_sources()
    only = sys.argv[1:]  # optional: collect.py arxiv-ai reddit-space  (for testing)
    if only:
        sources = [s for s in sources if s["id"] in only]
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(run_source, sources))

    items, status, dropped_lang = [], [], 0
    for src, its, err, secs in results:
        cutoff = (NOW - timedelta(days=src["days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        kept = 0
        for i in its:
            if i["published"] < cutoff:
                continue
            if not enrich.is_english(f"{i['title']} {i['summary']}"):
                dropped_lang += 1
                continue
            pw = enrich.paywall(i["url"])
            if pw:
                i["paywall"] = pw
            items.append(i)
            kept += 1
        off = bool(err and err.startswith("off:"))
        status.append({"id": src["id"], "name": src["name"], "kind": src["kind"],
                       "ok": err is None, "off": off, "count": kept,
                       "error": f"Needs {err[4:]} secret" if off else err})
        flag = "ok " if err is None else ("off" if off else "ERR")
        print(f"{flag} {src['id']:<26} {kept:>3} {secs:5.1f}s  {err or ''}", file=sys.stderr)

    if not items:
        print("No items collected; refusing to overwrite the existing feed.", file=sys.stderr)
        sys.exit(1)
    if only:
        return

    # Engagement first (per source), then merge the same story across sources.
    add_heat(items, {s['id']: s.get('weight', TYPE_WEIGHT.get(s['type'], 1.0)) for s in sources})
    before = len(items)
    items = enrich.merge_stories(items)
    items.sort(key=lambda i: i["published"], reverse=True)
    items = items[:MAX_ITEMS]

    # State carried between runs lives on the published site (no repo write access needed).
    site_url = os.environ.get("SITE_URL") or CONFIG["site_url"]
    prev_state = enrich.fetch_previous(site_url, "data/state.json", {})
    prev_archive = enrich.fetch_previous(site_url, "data/archive.json", [])
    ai_cache = prev_state.get("ai", {})
    for i in items:
        if i["id"] in ai_cache:
            i["ai"] = ai_cache[i["id"]]
    archive = enrich.update_archive(prev_archive, items, days=30, cap=15000)
    first_seen = enrich.update_first_seen(prev_state.get("first_seen", {}), archive)
    signals = enrich.nascent_signals(archive, first_seen)
    signal_hist = enrich.update_signal_history(enrich.fetch_previous(site_url, "data/signals.json", []), signals)
    about = {h["slug"]: h.get("about") for h in signal_hist if h.get("about")}
    for s in signals:
        s["slug"] = enrich.slugify(s["term"])
        if about.get(s["slug"]):
            s["about"] = about[s["slug"]]
    in_signal = {x["id"]: s["term"] for s in signals for x in s["items"]}
    for i in items:
        if i["id"] in in_signal:
            i["signal"] = in_signal[i["id"]]

    feed = {
        "version": 3,
        "name": CONFIG["site_name"],
        "generated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "topics": {k: v[0] for k, v in TOPICS.items()},
        "trending": trending(items),
        "signals": signals,
        "brief": prev_state.get("brief"),
        "sources": status,
        "items": items,
    }
    state = {
        "updated": feed["generated"],
        "first_seen": first_seen,
        "ai": {a["id"]: a["ai"] for a in archive if a.get("ai")},
        "brief": prev_state.get("brief"),
        "social": prev_state.get("social", {}),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    dump = lambda name, obj: json.dump(obj, open(os.path.join(OUT_DIR, name), "w"), ensure_ascii=False, separators=(",", ":"))
    dump("feed.json", feed)
    dump("archive.json", archive)
    dump("state.json", state)
    dump("signals.json", signal_hist)
    with open(os.path.join(OUT_DIR, "latest.md"), "w") as f:
        f.write(digest(feed))

    # Private 180-day archive for Team plans, at an unguessable path derived from a secret.
    key = os.environ.get("ARCHIVE_KEY")
    if key:
        path = enrich.private_archive_path(key)
        prev = enrich.fetch_previous(site_url, path, [])
        full = enrich.update_archive(prev, items, days=180, cap=60000, summary_len=400)
        os.makedirs(os.path.dirname(os.path.join(ROOT, "site", path)), exist_ok=True)
        json.dump(full, open(os.path.join(ROOT, "site", path), "w"), ensure_ascii=False, separators=(",", ":"))
        print(f"Private archive: {len(full)} items", file=sys.stderr)
    print(f"Merged {before} items into {len(items)} stories; dropped {dropped_lang} non-English; "
          f"{len(signals)} signals; archive {len(archive)}.", file=sys.stderr)
    ok = sum(s["ok"] for s in status)
    print(f"\nWrote {len(items)} items from {ok}/{len(status)} sources.", file=sys.stderr)


if __name__ == "__main__":
    main()
