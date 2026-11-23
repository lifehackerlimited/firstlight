"""Nascent enrichment: quality filters, story merging, archive and the Nascent score.

Called by collect.py after fetching. Standard library only.

State between runs (archive, first-seen dates, AI-summary cache) is read back from the
previously *published* site, so the GitHub workflow never needs write access to the repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)
ISO = "%Y-%m-%dT%H:%M:%SZ"


def iso_ago(**kw) -> str:
    return (NOW - timedelta(**kw)).strftime(ISO)


# ------------------------------------------------------------------ language
EN = set("the and of to in is for on with that this how why what new are from by as it at be an your you we our "
         "can will has have its into about after over more not but or was were their they".split())
OTHER = set("der die das und ist mit für nicht auf ein eine den dem zu von wie sich auch "
            "le la les des et est pour une dans du au sur pas qui que "
            "el los las y para con una del por se como más "
            "em não uma os com para mais "
            "het een van voor niet met zijn "
            "il di che per non con della sono "
            "och att det som för inte med".split())


def is_english(text: str) -> bool:
    if not text:
        return True
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c) > 0x24F) / len(letters) > 0.3:
        return False  # mostly non-Latin script
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    en = sum(w in EN for w in words)
    other = sum(w in OTHER for w in words)
    return not (other >= 2 and other > en)


# ------------------------------------------------------------------ paywalls
PAYWALL = {
    "Paywall": ["wsj.com", "ft.com", "bloomberg.com", "economist.com", "theinformation.com", "barrons.com",
                "thetimes.co.uk", "telegraph.co.uk", "newscientist.com", "businessinsider.com", "hbr.org",
                "statnews.com/pharmalot", "statnews.com/2", "stratechery.com"],
    "Metered": ["nytimes.com", "washingtonpost.com", "wired.com", "technologyreview.com", "theatlantic.com",
                "newyorker.com", "fortune.com", "spectrum.ieee.org/premium", "science.org/doi"],
}


def paywall(url: str) -> str | None:
    u = re.sub(r"^https?://(www\.)?", "", url.lower())
    for label, hosts in PAYWALL.items():
        if any(u.startswith(h) or ("." + h) in u.split("/", 1)[0] for h in hosts):
            return label
    return None


# ------------------------------------------------------------------ merging duplicate stories
KIND_RANK = {"research": 0, "news": 1, "insight": 2, "solution": 3, "launch": 4, "learn": 5, "discussion": 6}
TSTOP = set("""a an the and or of to in on for with from by at as is are was were be been this that these those it its into over
under via how why what when who new your you we our can will not no than more most about after before up out show hn ask
using use based just now one two make made says said here there all has have had his her their they them get""".split())


TRACKING = re.compile(r"^(utm_|ref$|ref_|source$|fbclid$|gclid$|mc_cid$|mc_eid$|cmpid$|via$|s$|si$)")


def norm_url(u: str) -> str:
    u = re.sub(r"^https?://(www\.|m\.)?", "", u.strip())
    u = u.split("#", 1)[0]
    if "?" in u:  # keep meaningful query parameters (e.g. YouTube's ?v=), drop tracking ones
        base, q = u.split("?", 1)
        keep = sorted(p for p in q.split("&") if p and not TRACKING.match(p.split("=", 1)[0].lower()))
        u = base + ("?" + "&".join(keep) if keep else "")
    u = u.lower() if "youtube.com" not in u.lower() else u
    u = re.sub(r"(arxiv\.org/(abs|pdf)/[\d.]+)v\d+", r"\1", u)
    return u.replace("arxiv.org/pdf/", "arxiv.org/abs/").rstrip("/")


def title_tokens(t: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9][a-z0-9+\-.]*", t.lower()) if w not in TSTOP and len(w) > 2)


def merge_stories(items: list) -> list:
    """Group the same story across sources (same link, or near-identical headline within 4 days).
    The best original source leads; the rest become `also` with their discussion links."""
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_url = {}
    for n, i in enumerate(items):
        for u in filter(None, [i["url"], i.get("alt")]):
            k = norm_url(u)
            if k in by_url:
                union(by_url[k], n)
            else:
                by_url[k] = n
    toks = [title_tokens(i["title"]) for i in items]
    index = defaultdict(list)
    df = Counter(t for ts in toks for t in ts)
    for n, ts in enumerate(toks):
        for t in ts:
            if df[t] <= 40:  # only index reasonably distinctive words
                index[t].append(n)
    for n, ts in enumerate(toks):
        if len(ts) < 4:
            continue
        cands = Counter(m for t in ts if df[t] <= 40 for m in index[t] if m > n)
        for m, shared in cands.items():
            if shared < 4:
                continue
            jac = shared / len(ts | toks[m])
            if jac >= 0.6 and abs(_ts(items[n]) - _ts(items[m])) < 4 * 86400:
                union(n, m)

    groups = defaultdict(list)
    for n in range(len(items)):
        groups[find(n)].append(items[n])
    out = []
    for g in groups.values():
        if len(g) == 1:
            g[0]["coverage"] = 1
            out.append(g[0])
            continue
        g.sort(key=lambda i: (KIND_RANK.get(i["kind"], 9), i["published"]))
        lead = dict(g[0])
        rest = [i for i in g[1:] if i["sourceId"] != lead["sourceId"] or i["url"] != lead["url"]]
        lead["also"] = [{"source": i["source"], "sourceId": i["sourceId"], "kind": i["kind"],
                         "url": i["url"], "comments": i.get("comments")} for i in rest][:8]
        lead["coverage"] = len({i["sourceId"].split("-")[0] for i in g})
        if not lead.get("comments"):
            lead["comments"] = next((i["comments"] for i in rest if i.get("comments")), None)
        lead["topics"] = list(dict.fromkeys(t for i in g for t in i.get("topics", [])))[:4]
        heats = [i.get("heat") for i in g if isinstance(i.get("heat"), (int, float))]
        if heats:
            lead["heat"] = max(heats)
        lead["published"] = min(i["published"] for i in g)
        out.append(lead)
    return out


def _ts(i) -> float:
    try:
        return datetime.strptime(i["published"], ISO).replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return NOW.timestamp()


# ------------------------------------------------------------------ state from the live site
def fetch_previous(site_url: str, path: str, default):
    if not site_url:
        return default
    try:
        req = urllib.request.Request(f"{site_url.rstrip('/')}/{path}", headers={"User-Agent": "Nascent-Collector/2.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read(60_000_000))
    except Exception:
        return default


def private_archive_path(key: str) -> str:
    return "api/" + hashlib.sha256(("nascent-archive:" + key).encode()).hexdigest()[:32] + "/archive.json"


def compact(i: dict, summary_len: int = 160) -> dict:
    c = {"id": i["id"], "t": i["title"], "u": i["url"], "s": i["source"], "sid": i["sourceId"], "k": i["kind"],
         "p": i["published"], "tp": i.get("topics", []), "sm": (i.get("summary") or "")[:summary_len]}
    if i.get("ai"):
        c["ai"] = i["ai"]
    if i.get("coverage", 1) > 1:
        c["cv"] = i["coverage"]
    if i.get("comments"):
        c["c"] = i["comments"]
    return c


def update_archive(prev: list, items: list, days: int, cap: int, summary_len: int = 160) -> list:
    keep = {a["id"]: a for a in prev if a.get("p", "") >= iso_ago(days=days)}
    for i in items:
        keep[i["id"]] = compact(i, summary_len)
    out = sorted(keep.values(), key=lambda a: a["p"], reverse=True)
    return out[:cap]


# ------------------------------------------------------------------ Nascent score
MAINSTREAM = {"the-verge", "wired-science", "wired-ai", "techcrunch", "ars", "ars-science", "mit-tech-review",
              "new-scientist", "the-register", "the-conversation", "stat-news", "venturebeat-ai"}
STAGE = {"research": "paper", "solution": "code", "launch": "code", "discussion": "talk", "insight": "talk",
         "learn": "talk", "news": "news"}
GENERIC = set("""language models language model large language machine learning deep learning open source artificial intelligence
neural networks neural network reinforcement learning computer vision data science new study study finds researchers
paper code release model release state art real world case study""".split("\n"))
GENERIC_WORDS = set("""model models data system systems learning paper study research approach method methods results
analysis framework based using toward towards network networks via benchmark dataset agent agents tool tools language
large small new first open source code app apps time world people year years technology tech science scientists
llm llms gpt api apis""".split())

# Household names and permanent topics: always in the news, so never an "early" signal on their own.
ESTABLISHED = set("""github gitlab openai anthropic deepmind google alphabet microsoft apple meta facebook amazon aws nvidia
intel amd tesla spacex nasa esa jaxa isro starship starlink chatgpt claude gemini copilot grok xai youtube tiktok
linkedin reddit wikipedia huggingface hugging face samsung huawei tsmc arm qualcomm ibm oracle netflix uber
openai's stackoverflow iphone ipad android windows linux macos ios""".split()) | {
    "elon musk", "sam altman", "jensen huang", "sundar pichai", "satya nadella", "mark zuckerberg", "tim cook",
    "dario amodei", "demis hassabis", "climate change", "global warming", "artificial intelligence", "hugging face",
    "united states", "white house", "silicon valley", "supreme court", "european union", "data centers",
    "data center", "stock market", "wall street", "social media", "electric vehicles", "renewable energy",
    "nuclear power", "space station", "mars rover", "james webb", "webb telescope", "black holes", "black hole",
}


def family(sid: str) -> str:
    return sid.split("-")[0]


def terms_of(title: str) -> set:
    raw = re.findall(r"[A-Za-z][A-Za-z0-9+\-.]*[A-Za-z0-9+]|[A-Za-z]", title)
    low = [w.lower() for w in raw]
    out = set()
    for a, b in zip(low, low[1:]):
        if a in TSTOP or b in TSTOP or a in GENERIC_WORDS and b in GENERIC_WORDS:
            continue
        if len(a) > 2 and len(b) > 2:
            out.add(f"{a} {b}")
    for w, lw in zip(raw, low):
        # model/product-like names: digits mixed with letters (gpt-5, v4) or CamelCase (DeepSeek); not bare acronyms
        distinctive = (re.search(r"\d", w) and re.search(r"[a-z]", lw) and len(lw) >= 4) or re.search(r"[a-z][A-Z]", w)
        if distinctive and lw not in GENERIC_WORDS and lw not in TSTOP and len(lw) >= 3:
            out.add(lw)
    return {t for t in out if t not in GENERIC}


def nascent_signals(archive: list, first_seen: dict, limit: int = 12):
    """Ideas showing up across papers, code and discussion before mainstream outlets cover them."""
    recent = [a for a in archive if a["p"] >= iso_ago(days=21)]
    n_docs = max(len(recent), 1)
    by_term = defaultdict(list)
    for a in recent:
        for t in terms_of(a["t"]):
            by_term[t].append(a)
    # Rising, not steady: once the archive holds 2+ weeks, a term must have at least 35% of its 30-day mentions in
    # the last 7 days (an even spread would be ~23%). Terms that are always around never qualify.
    span = 0
    if archive:
        oldest = min(a["p"] for a in archive)
        span = (NOW - datetime.strptime(oldest, ISO).replace(tzinfo=timezone.utc)).days
    total30, last7 = Counter(), Counter()
    if span >= 14:
        week = iso_ago(days=7)
        for a in archive:
            for t in terms_of(a["t"]):
                total30[t] += 1
                if a["p"] >= week:
                    last7[t] += 1
    scored = []
    for t, docs in by_term.items():
        if t in ESTABLISHED or all(w in ESTABLISHED or w in GENERIC_WORDS for w in t.split()):
            continue  # a household name (or two of them, e.g. "spacex starship") isn't an early signal
        if span >= 14 and total30[t] and last7[t] / total30[t] < 0.35:
            continue
        fams = {family(a["sid"]) for a in docs}
        stages = {STAGE.get(a["k"], "news") for a in docs}
        if len(docs) < 3 or len(fams) < 3 or len(stages) < 2 or len(docs) / n_docs > 0.03:
            continue
        if not stages & {"paper", "code"}:
            continue  # nascent ideas start with researchers or builders, not newsrooms
        if sum(STAGE.get(a["k"]) == "news" for a in docs) / len(docs) >= 0.5:
            continue  # already mostly a news story
        main = [a for a in docs if a["sid"] in MAINSTREAM]
        main_share = len(main) / len(docs)
        recent3 = sum(a["p"] >= iso_ago(days=3) for a in docs) / len(docs)
        seen = first_seen.get(t) or min(a["p"] for a in docs)
        age_days = (NOW - datetime.strptime(seen, ISO).replace(tzinfo=timezone.utc)).days
        fresh = max(0.0, 1 - age_days / 30)
        score = 100 * (0.35 * min(len(fams), 6) / 6 + 0.25 * len(stages) / 4 + 0.2 * recent3 + 0.2 * fresh)
        score *= 1 - 0.75 * main_share
        if "paper" in stages and "news" not in stages:
            score *= 1.1  # the sweet spot: research and builders are on it, newsrooms aren't yet
        scored.append((min(round(score), 99), t, docs, stages, fams, main, seen))
    scored.sort(key=lambda x: -x[0])
    picked, used = [], set()
    for sc, t, docs, stages, fams, main, seen in scored:
        ids = frozenset(a["id"] for a in docs)
        words = set(t.split())
        if any(len(ids & u) / len(ids) > 0.6 for u in used) or any(words & set(p["term"].split()) and p["term"] in t for p in picked):
            continue  # same story under a different phrase
        used.add(ids)
        docs_sorted = sorted(docs, key=lambda a: a["p"])
        picked.append({
            "term": t, "score": sc, "first_seen": seen,
            "stages": {s: sum(STAGE.get(a["k"], "news") == s for a in docs) for s in ("paper", "code", "talk", "news")},
            "sources": len(fams), "mentions": len(docs), "mainstream": len(main) > 0,
            "items": [{"id": a["id"], "t": a["t"], "u": a["u"], "s": a["s"], "k": a["k"], "p": a["p"]} for a in docs_sorted[-6:]][::-1],
        })
        if len(picked) >= limit:
            break
    return picked


def update_first_seen(first_seen: dict, archive: list) -> dict:
    """Remember when each recurring term first appeared, so 'new' ideas can be told from old ones."""
    counts, earliest = Counter(), {}
    for a in archive:
        for t in terms_of(a["t"]):
            counts[t] += 1
            if t not in earliest or a["p"] < earliest[t]:
                earliest[t] = a["p"]
    fs = {t: d for t, d in first_seen.items() if d >= iso_ago(days=60)}
    for t, c in counts.items():
        if c >= 2 and (t not in fs or earliest[t] < fs[t]):
            fs[t] = earliest[t]
    return fs


def slugify(term: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
    return s[:60] or "signal"


def update_signal_history(prev: list, signals: list, keep_days: int = 180) -> list:
    """Every term that has ever been a Nascent signal, so each one keeps a permanent page (signals/<slug>/)
    after it drops off the live panel. Stored publicly at data/signals.json."""
    today = NOW.strftime("%Y-%m-%d")
    hist = {h["slug"]: h for h in prev if isinstance(h, dict) and h.get("slug")}
    for s in signals:
        slug = slugify(s["term"])
        h = hist.get(slug) or {"slug": slug, "term": s["term"], "first_flagged": today, "peak": 0, "history": [], "items": []}
        h.update({
            "term": s["term"], "last_flagged": today, "score": s["score"], "first_seen": s.get("first_seen"),
            "stages": s.get("stages", {}), "sources": s.get("sources", 0), "mentions": s.get("mentions", 0),
            "mainstream": s.get("mainstream", False),
        })
        h["peak"] = max(h.get("peak", 0), s["score"])
        days = dict(h.get("history", []))
        days[today] = max(days.get(today, 0), s["score"])
        h["history"] = sorted(days.items())[-90:]
        seen, merged = set(), []
        for it in list(s.get("items", [])) + h.get("items", []):
            if it.get("id") and it["id"] not in seen:
                seen.add(it["id"])
                merged.append(it)
        h["items"] = sorted(merged, key=lambda x: x.get("p", ""), reverse=True)[:12]
        hist[slug] = h
    cutoff = (NOW - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    generic = lambda t: t in ESTABLISHED or all(w in ESTABLISHED or w in GENERIC_WORDS for w in t.split())
    out = [h for h in hist.values() if h.get("last_flagged", "") >= cutoff and not generic(h.get("term", ""))]
    return sorted(out, key=lambda h: (h.get("last_flagged", ""), h.get("score", 0)), reverse=True)
