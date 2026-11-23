# Nascent

**Technology, at the moment it's born.** Catch it before it hits the mainstream at [nascent.news](https://nascent.news). A free, open-source media outlet for the frontier: the latest **research, news, analysis, open-source solutions, launches, learning material and community discussion** on emerging technology, collected from ~140 public sources every few hours and presented in one calm, fast feed.

Built on **MCT (Media Collection Tool)**. No server, no database and no ads. Google Analytics (via Tag Manager) loads only after a visitor accepts the cookie banner. It runs entirely on **GitHub Actions + GitHub Pages**, for free.

```
 ┌──────────────── GitHub (free) ────────────────┐
 │  Actions (cron, every 3h)                     │
 │   └─ scripts/collect.py ──► site/data/*.json  │
 │  Pages (static CDN)  index.html + feed.json ──┼──► readers' browsers
 └─────────────────────────────────────┼─────────┘
                                       └────────────► Freya (pulls, read-only)
```

## Sources

All defined in [`scripts/sources.json`](scripts/sources.json), grouped by content type. Add one line to add a source.

| Tab | What | Sources |
|---|---|---|
| **Research** | Papers & preprints | arXiv (AI/ML, vision, quantum, robotics, security, emerging tech & hardware, materials, space, quant-bio, computers & society), Hugging Face Daily Papers, OpenAlex scholarly search (AI, quantum, energy, biotech, robotics, semiconductors), bioRxiv, medRxiv, Nature, Nature Machine Intelligence / Electronics / Energy, PLOS ONE |
| **News** | Outlets, universities, agencies | MIT Technology Review, IEEE Spectrum, Ars Technica, WIRED, New Scientist, Quanta, Science News, Singularity Hub, TechXplore, ScienceDaily, The Conversation, The Register, The Verge · Rest of World, TechCabal (Africa), Sifted (Europe) · MIT, Harvard, Berkeley, Caltech, NIST, NASA, ESA · SpaceNews, The Quantum Insider, The Robot Report, Semiconductor Engineering, Canary Media, CleanTechnica, STAT, GEN, BleepingComputer, The Hacker News, Krebs |
| **Insights** | Newsletters & lab blogs | Substack: Import AI, Interconnects, One Useful Thing, Latent Space, Ahead of AI, AI as Normal Technology, Marcus on AI, Exponential View, Pragmatic Engineer, SemiAnalysis, Construction Physics, Not Boring · Chips and Cheese, Stratechery, Simon Willison · OpenAI, Google DeepMind, Google Research, Microsoft Research, Hugging Face, NVIDIA, BAIR, AWS ML |
| **Solutions** | Things you can use | GitHub rising repos (all + AI), Hugging Face trending models, DEV Community, Hackaday, r/selfhosted, r/opensource |
| **Launches** | New products | Product Hunt, Show HN, TechCrunch |
| **Learn** | Explainers & courses | YouTube: Two Minute Papers, 3Blue1Brown, Computerphile, Fireship, Veritasium, Scott Manley, Real Engineering, MIT OpenCourseWare, Stanford Online, freeCodeCamp · freeCodeCamp News, Class Central, EdSurge, Hechinger Report, Lil'Log, fast.ai, Towards Data Science |
| **Discussions** | Communities | Hacker News, Lobsters, Slashdot, Tildes (~tech, ~science, ~comp), Mastodon trending links (mastodon.social, Fosstodon, Hachyderm, Infosec.Exchange, mstdn.science), Bluesky feeds (Science, Tech, Machine Learning, Quantum, SciGuy Space, #cybersecurity), Reddit (r/MachineLearning, LocalLLaMA, singularity, Futurology, QuantumComputing, space, robotics, biotech, energy, netsec, hardware, programming, EverythingScience, edtech) |

Source types: `rss` (any RSS/Atom feed, including any Substack's `/feed`), `arxiv`, `openalex`, `biorxiv`, `youtube`, `reddit`, `hn`, `lobsters`, `hf_papers`, `hf_models`, `github`, `devto`, `producthunt`, `mastodon`, `bluesky`. General-interest sources use `require_topic` so only emerging-tech items get through. Every item is auto-tagged into topics (AI, Quantum, Space, Bio & Health, Energy & Climate, Robotics, Security, Chips & Hardware, Software, Materials & Physics, Education).

**Why not Google Scholar?** It has no API and its terms forbid automated scraping (it also blocks it). Nascent uses **OpenAlex** instead: an open index of 250M+ scholarly works covering the same journals, conferences and repositories, plus arXiv, bioRxiv and medRxiv for preprints.

### Keys and settings

Secrets go in **Settings → Secrets and variables → Actions → Secrets**. They're only used by the GitHub jobs and never reach the website. Everything works without them; each one switches a feature on.

| Secret | Switches on |
|---|---|
| `ANTHROPIC_API_KEY` | AI "why it matters" lines and the daily brief (`scripts/summarize.py`, Claude Opus 5.5 by default; set the variable `NASCENT_AI_MODEL` to change model) |
| `STRIPE_KEY` | Email digests and Team API feeds. Use a Stripe **restricted key** with *read* access to Customers, Subscriptions, Checkout Sessions and Prices only |
| `RESEND_API_KEY` + variable `DIGEST_FROM` | Sending digests (e.g. `Nascent <digest@nascent.news>`, on a domain verified in Resend) |
| `ARCHIVE_KEY` | The private 180-day archive for Team plans. Any long random string: `openssl rand -hex 32 \| gh secret set ARCHIVE_KEY` |
| `OPENALEX_API_KEY` | Reliable scholarly search (anonymous OpenAlex search is often throttled) |
| `PRODUCTHUNT_TOKEN` | Product Hunt votes and topics (otherwise the public RSS feed) |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Daily auto-post of the top signal to Bluesky (use an app password) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | Daily auto-post of the top signal to X (OAuth 1.0a keys with read and write access) |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME` | The 16 Reddit communities. Reddit currently isn't issuing new keys to most accounts |

Public settings (name, site address, company details, prices, Stripe payment links, Google Tag Manager ID, optional cookieless GoatCounter analytics) live in [`config.json`](config.json).

## How it fits together

```
collect.py   fetch ~140 sources → language filter, paywall badges, merge duplicates,
             archive (30 days public, 180 days private for Team), Nascent signals
summarize.py optional Claude "why it matters" + daily brief (cached; each story summarised once)
digest.py    team-api: private Team feeds · daily: email digests (subscribers read from Stripe)
build.py     pricing, account, legal, topic pages, Atom feeds, sitemap, PWA manifest
```

State between runs (archive, AI summaries, first-seen dates) is read back from the published site, so the workflow never needs write access to the repository.

**Plans** (`/pricing/`): Free (weekly digest) · Pro £6/month or £60/year · Student £3/month · Team £29/month, each with a 30-day free trial. Checkout, billing and the customer portal are Stripe-hosted; Stripe is the subscriber list, so there's no database.

## Deploy (≈5 minutes)

1. Create a new **public** GitHub repository (e.g. `nascent`) and push this folder to `main`.
2. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Actions** tab → *Collect & publish* → **Run workflow**.
4. Your portal is live at `https://<your-username>.github.io/nascent/` and refreshes itself every 3 hours.

Optional: add a custom domain in Settings → Pages (GitHub provides free HTTPS).

## Run locally

```bash
python3 scripts/collect.py          # refresh site/data/ (~1 min)
python3 scripts/collect.py arxiv-ai nasa   # test specific sources
python3 -m http.server -d site 8000 # open http://localhost:8000
```

## Connecting Freya

The portal never talks to Freya, and Freya's server is never exposed. Freya **pulls** the public files instead:

- `https://nascent.today/data/feed.json` — structured feed (schema below)
- `https://nascent.today/data/latest.md` — Markdown digest grouped by topic, ideal for RAG / summarisation

Each card also has **Copy for Freya**, which copies a ready-made brief for pasting into a chat.

```jsonc
{
  "generated": "2026-11-22T12:17:00Z",
  "topics":   { "ai": "AI & ML", ... },
  "trending": [{ "term": "world models", "count": 6 }],
  "sources":  [{ "id": "arxiv-ai", "ok": true, "count": 30, ... }],
  "items": [{
    "id": "3f2a…", "title": "…", "url": "https://…", "source": "arXiv · AI & ML",
    "kind": "research|news|solution|launch|discussion", "summary": "…",
    "published": "2026-11-22T09:00:00Z", "score": 412, "heat": 97,
    "comments": "https://…", "alt": "https://arxiv.org/abs/…",
    "authors": ["…"], "tags": ["…"], "topics": ["ai", "robotics"]
  }]
}
```

Minimal Freya ingestion (Python):

```python
import json, urllib.request
feed = json.load(urllib.request.urlopen("https://nascent.today/data/feed.json", timeout=20))
docs = [f"{i['title']}\n{i['summary']}\n{i['url']}" for i in feed["items"]]
# → embed / index `docs` in Freya's vector store on your own schedule
```

## Security model

- **No attack surface you own.** Static files on GitHub's CDN; nothing to log into, no API, no database.
- **Strict Content-Security-Policy**: only same-origin scripts, styles and data, plus Google Tag Manager/Analytics hosts when `analytics.gtm` is set. No inline script. Nothing is requested from Google until the visitor accepts the cookie banner (`site/assets/consent.js`).
- **Untrusted content is never rendered as HTML.** The collector strips markup to plain text; the front end uses `textContent` only, and allows only `http(s)` links, opened with `noopener noreferrer nofollow`.
- **Hardened collector**: https-only sources, per-host rate limits (honours arXiv's 3-second rule), 20 s timeouts, 5 MB response cap, XML DTD/entity rejection, per-source failure isolation, and it refuses to publish an empty feed.
- **Least-privilege CI**: the build job is read-only; only the deploy job can write to Pages. Dependabot keeps Actions up to date (you can additionally pin them to commit SHAs).
- **Privacy**: saved items and theme live in `localStorage` in the reader's browser only.

## Licence

MIT. Nascent is built on MCT (Media Collection Tool). Articles remain the property of their publishers; MCT shows titles, short excerpts and links back to the original.

## Signal pages and social posts

Every term that becomes a Nascent signal gets a permanent page at `/signals/<slug>/` (listed at `/signals/` and in the sitemap). The history is kept in `data/signals.json` for 180 days, and Claude writes a one-sentence explainer for each new signal.

`scripts/social.py` posts once a day (first refresh after 12:00 UTC, change with the `SOCIAL_POST_HOUR` variable) to whichever of Bluesky and X has secrets set. It picks the strongest signal that mainstream outlets haven't picked up and that hasn't been posted in 14 days, linking to its signal page with UTM tags. To post straight away, run the *Publish* workflow manually and tick **Post today's signal now**.

