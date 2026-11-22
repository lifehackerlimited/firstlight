// MCT portal. No frameworks, no third-party requests.
// Security: all feed content is rendered with textContent / setAttribute (never innerHTML),
// and every outbound link is checked to be http(s).
"use strict";

// MCT is the engine; Firstlight is the public face.
const KINDS = {
  all: { label: "All" },
  research: { label: "Research", one: "Research", color: "var(--k-research)" },
  news: { label: "News", one: "News", color: "var(--k-news)" },
  insight: { label: "Insights", one: "Insight", color: "var(--k-insight)" },
  solution: { label: "Solutions", one: "Solution", color: "var(--k-solution)" },
  launch: { label: "Launches", one: "Launch", color: "var(--k-launch)" },
  learn: { label: "Learn", one: "Learn", color: "var(--k-learn)" },
  discussion: { label: "Discussions", one: "Discussion", color: "var(--k-discussion)" },
  saved: { label: "Saved", color: "var(--accent)" },
};
const PAGE = 25;
const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

let feed = { items: [], topics: {}, trending: [], sources: [] };
let saved = store.get("mct-saved", {});
const state = { kind: "all", topic: "", source: "", q: "", sort: "latest", limit: PAGE };
const sourceName = (id) => (feed.sources.find((s) => s.id === id) || { name: id }).name;
let focusIdx = -1;

// ---------- utils ----------
function safeUrl(u) {
  if (typeof u !== "string" || !/^https?:\/\//i.test(u)) return null;
  try { const p = new URL(u, location.href); return p.protocol === "https:" || p.protocol === "http:" ? p.href : null; }
  catch { return null; }
}
function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  const units = [[31536000, "y"], [2592000, "mo"], [604800, "w"], [86400, "d"], [3600, "h"], [60, "m"]];
  for (const [n, u] of units) if (s >= n) return `${Math.floor(s / n)}${u} ago`;
  return "now";
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 1800);
}
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  state.kind = KINDS[p.get("kind")] ? p.get("kind") : "all";
  state.topic = p.get("topic") || "";
  state.source = (p.get("src") || "").slice(0, 60);
  state.q = (p.get("q") || "").slice(0, 120);
  state.sort = p.get("sort") === "top" ? "top" : "latest";
}
function writeHash() {
  const p = new URLSearchParams();
  if (state.kind !== "all") p.set("kind", state.kind);
  if (state.topic) p.set("topic", state.topic);
  if (state.source) p.set("src", state.source);
  if (state.q) p.set("q", state.q);
  if (state.sort !== "latest") p.set("sort", state.sort);
  const h = p.toString();
  history.replaceState(null, "", h ? `#${h}` : location.pathname + location.search);
}

// ---------- data ----------
async function load() {
  const list = $("#list");
  for (let i = 0; i < 4; i++) list.append(el("div", "skeleton"));
  try {
    const r = await fetch("data/feed.json", { cache: "no-cache" });
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    if (!Array.isArray(data.items)) throw new Error("bad feed");
    feed = data;
  } catch (e) {
    list.replaceChildren();
    $("#count").textContent = "Couldn't load the feed. Please refresh in a moment.";
    return;
  }
  $("#stat-items").textContent = feed.items.length.toLocaleString();
  const live = feed.sources.filter((s) => s.ok).length;
  $("#stat-sources").textContent = live;
  $("#lede-sources").textContent = `${live} public sources`;
  $("#stat-updated").textContent = ago(feed.generated);
  $("#stat-updated").title = new Date(feed.generated).toLocaleString();
  renderSides();
  render();
}

function matches(i, { skipKind = false, skipTopic = false } = {}) {
  if (!skipKind && state.kind !== "all" && state.kind !== "saved" && i.kind !== state.kind) return false;
  if (!skipTopic && state.topic && !(i.topics || []).includes(state.topic)) return false;
  if (state.source && i.sourceId !== state.source) return false;
  if (state.q) {
    const hay = `${i.title} ${i.summary} ${i.source} ${(i.tags || []).join(" ")} ${(i.authors || []).join(" ")}`.toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}
function pool() { return state.kind === "saved" ? Object.values(saved) : feed.items; }
function filtered() {
  const out = pool().filter((i) => matches(i));
  if (state.sort === "top") {
    // engagement percentile, gently decayed by age (half-life ~2 days)
    const rank = (i) => (i.heat ?? 15) * Math.pow(0.5, (Date.now() - Date.parse(i.published)) / 1.728e8);
    out.sort((a, b) => rank(b) - rank(a));
  } else {
    out.sort((a, b) => (a.published < b.published ? 1 : -1));
  }
  return out;
}

// ---------- render: sidebars ----------
function renderSides() {
  // kind tabs
  const kinds = $("#kinds");
  kinds.replaceChildren();
  for (const [k, v] of Object.entries(KINDS)) {
    const b = el("button", "tab");
    b.type = "button";
    b.setAttribute("role", "tab");
    b.dataset.kind = k;
    if (v.color) { b.style.setProperty("--tab-c", v.color); b.append(el("span", "swatch")); }
    b.append(el("span", null, v.label), el("span", "n"));
    b.addEventListener("click", () => {
      const src = feed.sources.find((s) => s.id === state.source);
      if (src && k !== "all" && k !== "saved" && src.kind !== k) state.source = "";
      state.kind = k; state.limit = PAGE; update();
    });
    kinds.append(b);
  }
  // trending
  const tr = $("#trending");
  tr.replaceChildren();
  for (const t of feed.trending || []) {
    const c = el("button", "chip", t.term);
    c.type = "button";
    c.append(el("span", "n", String(t.count)));
    c.addEventListener("click", () => { state.q = t.term; $("#q").value = t.term; state.limit = PAGE; update(); });
    tr.append(c);
  }
  if (!tr.children.length) tr.append(el("p", "feed-count", "Not enough signal yet."));
  // sources, grouped by content type; click one to filter the feed to it
  const sl = $("#sources");
  sl.replaceChildren();
  for (const [k, v] of Object.entries(KINDS)) {
    const group = (feed.sources || []).filter((s) => s.kind === k);
    if (!group.length) continue;
    const live = group.filter((s) => s.ok).length;
    const det = el("details", "src-group");
    det.style.setProperty("--tab-c", v.color);
    const sum = el("summary");
    sum.append(el("span", "swatch"), el("span", "label", v.label), el("span", "n", `${live}/${group.length}`));
    const ul = el("ul", "source-list");
    for (const s of group) {
      const li = el("li");
      const b = el("button");
      b.type = "button";
      b.dataset.src = s.id;
      const dot = el("span", "st" + (s.ok ? "" : s.off ? " off" : " bad"));
      b.title = s.ok ? `Show only ${s.name}` : s.off ? `Not enabled yet: ${s.error}` : `Last fetch failed: ${s.error || "unknown"}`;
      b.disabled = !s.ok;
      b.append(dot, el("span", "name", s.name), el("span", "n", s.ok ? String(s.count) : s.off ? "key" : "—"));
      b.addEventListener("click", () => setSource(s.id));
      li.append(b);
      ul.append(li);
    }
    det.append(sum, ul);
    sl.append(det);
  }
}
function setSource(id) {
  state.source = state.source === id ? "" : id;
  state.kind = "all";
  state.limit = PAGE;
  update();
  window.scrollTo({ top: $("#feed").offsetTop - 140, behavior: "smooth" });
}

function renderCounts() {
  for (const b of $("#kinds").children) {
    const k = b.dataset.kind;
    const n = k === "saved" ? Object.keys(saved).length
      : feed.items.filter((i) => (k === "all" || i.kind === k) && matches(i, { skipKind: true })).length;
    b.querySelector(".n").textContent = n;
    b.setAttribute("aria-selected", String(k === state.kind));
  }
  const tl = $("#topics");
  tl.replaceChildren();
  const base = pool().filter((i) => matches(i, { skipTopic: true }));
  const entries = [["", "Everything", base.length]];
  for (const [k, label] of Object.entries(feed.topics || {})) {
    entries.push([k, label, base.filter((i) => (i.topics || []).includes(k)).length]);
  }
  for (const [k, label, n] of entries) {
    if (k && !n && state.topic !== k) continue;
    const li = el("li");
    const b = el("button");
    b.type = "button";
    b.setAttribute("aria-pressed", String(state.topic === k));
    b.append(el("span", null, label), el("span", "n", String(n)));
    b.addEventListener("click", () => { state.topic = k; state.limit = PAGE; update(); });
    li.append(b);
    tl.append(li);
  }
}

// ---------- render: feed ----------
function card(i, featured) {
  const node = $("#card-tpl").content.firstElementChild.cloneNode(true);
  node.dataset.kind = i.kind;
  node.dataset.id = i.id;
  if (featured) node.classList.add("featured");
  node.querySelector(".kind").textContent = (KINDS[i.kind] || {}).one || i.kind;
  const srcBtn = node.querySelector(".source");
  srcBtn.textContent = i.source;
  srcBtn.addEventListener("click", () => setSource(i.sourceId));
  const t = node.querySelector("time");
  t.dateTime = i.published;
  t.textContent = ago(i.published);
  t.title = new Date(i.published).toLocaleString();
  if (typeof i.heat === "number") {
    const h = node.querySelector(".heat");
    h.hidden = false;
    const bar = el("i"); const fill = el("b"); fill.style.width = `${i.heat}%`; bar.append(fill);
    h.append(bar, document.createTextNode(String(i.score)));
    h.title = `Engagement: ${i.score} (top ${100 - i.heat + 1}% for ${i.source})`;
  }
  if (featured) node.querySelector(".card-meta").prepend(el("span", "featured-label", "Top story ·"));

  const a = node.querySelector(".card-title a");
  a.textContent = i.title;
  const href = safeUrl(i.url);
  if (href) a.href = href;

  const sum = node.querySelector(".card-summary");
  sum.textContent = i.summary || "";
  sum.addEventListener("click", () => node.classList.toggle("expanded"));

  const tops = node.querySelector(".card-topics");
  for (const k of i.topics || []) {
    if (!feed.topics[k]) continue;
    const b = el("button", null, feed.topics[k]);
    b.type = "button";
    b.addEventListener("click", () => { state.topic = k; state.limit = PAGE; update(); window.scrollTo({ top: 0, behavior: "smooth" }); });
    tops.append(b);
  }
  const disc = safeUrl(i.comments);
  if (disc && disc !== href) { const d = node.querySelector(".act-discuss"); d.hidden = false; d.href = disc; }
  const alt = safeUrl(i.alt);
  if (alt) { const d = node.querySelector(".act-alt"); d.hidden = false; d.href = alt; }

  node.querySelector(".act-freya").addEventListener("click", () => copyForFreya(i));
  const sv = node.querySelector(".act-save");
  const paint = () => { const on = !!saved[i.id]; sv.setAttribute("aria-pressed", String(on)); sv.textContent = on ? "Saved" : "Save"; };
  paint();
  sv.addEventListener("click", () => { toggleSave(i); paint(); });
  return node;
}

function render() {
  const items = filtered();
  const list = $("#list");
  list.replaceChildren();
  focusIdx = -1;
  const canFeature = !state.q && state.kind !== "saved" && items.length > 3;
  if (canFeature) {
    // Feature the most-engaged story from the last ~36h, not merely the newest.
    const fresh = Date.now() - 1.3e8;
    let best = -1;
    items.forEach((i, n) => {
      if (Date.parse(i.published) >= fresh && (i.heat ?? 0) > (items[best]?.heat ?? 0)) best = n;
    });
    if (best > 0) items.unshift(items.splice(best, 1)[0]);
  }
  const shown = items.slice(0, state.limit);
  shown.forEach((i, n) => list.append(card(i, canFeature && n === 0)));
  const kindLabel = state.kind === "all" ? "stories" : KINDS[state.kind].label.toLowerCase();
  $("#count").textContent = items.length
    ? `${items.length.toLocaleString()} ${kindLabel}${state.topic ? ` in ${feed.topics[state.topic] || state.topic}` : ""}`
    : "";
  $("#more").hidden = items.length <= state.limit;
  $("#more").textContent = `Show more (${items.length - state.limit} left)`;
  $("#empty").hidden = items.length > 0;
  if (state.kind === "saved" && !items.length) {
    $("#empty").querySelector(".empty-title").textContent = "No saved stories yet";
  } else {
    $("#empty").querySelector(".empty-title").textContent = "Nothing here yet";
  }

  // active filter pills
  const af = $("#active-filters");
  af.replaceChildren();
  const pill = (label, clear) => {
    const b = el("button", "pill", label);
    b.type = "button";
    b.setAttribute("aria-label", `Remove filter ${label}`);
    b.append(el("span", null, "×"));
    b.addEventListener("click", () => { clear(); update(); });
    af.append(b);
  };
  if (state.topic) pill(feed.topics[state.topic] || state.topic, () => (state.topic = ""));
  if (state.source) pill(sourceName(state.source), () => (state.source = ""));
  for (const b of document.querySelectorAll("#sources button[data-src]")) b.setAttribute("aria-pressed", String(b.dataset.src === state.source));
  if (state.q) pill(`“${state.q}”`, () => { state.q = ""; $("#q").value = ""; });

  for (const b of document.querySelectorAll(".seg button")) b.setAttribute("aria-pressed", String(b.dataset.sort === state.sort));
  renderCounts();
}
function update() { writeHash(); render(); }

// ---------- actions ----------
function toggleSave(i) {
  if (saved[i.id]) { delete saved[i.id]; toast("Removed from saved"); }
  else { saved[i.id] = i; toast("Saved to this browser"); }
  store.set("mct-saved", saved);
  if (state.kind === "saved") render(); else renderCounts();
}
async function copyForFreya(i) {
  const brief = [
    `Title: ${i.title}`,
    `Type: ${i.kind} · Source: ${i.source} · Published: ${i.published.slice(0, 10)}`,
    i.authors && i.authors.length ? `Authors: ${i.authors.join(", ")}` : null,
    i.topics && i.topics.length ? `Topics: ${i.topics.map((k) => feed.topics[k] || k).join(", ")}` : null,
    `Link: ${i.url}`,
    i.summary ? `\nSummary: ${i.summary}` : null,
    `\nFreya, summarise this, explain why it matters, and list related work.`,
  ].filter(Boolean).join("\n");
  try { await navigator.clipboard.writeText(brief); toast("Brief copied. Paste it into Freya"); }
  catch { toast("Clipboard blocked by the browser"); }
}

function moveFocus(d) {
  const cards = [...document.querySelectorAll(".card")];
  if (!cards.length) return;
  cards[focusIdx]?.classList.remove("is-focus");
  focusIdx = Math.max(0, Math.min(cards.length - 1, focusIdx + d));
  const c = cards[focusIdx];
  c.classList.add("is-focus");
  c.focus({ preventScroll: true });
  c.scrollIntoView({ block: "center", behavior: "smooth" });
}

// ---------- wiring ----------
function init() {
  readHash();
  const q = $("#q");
  q.value = state.q;
  let deb;
  q.addEventListener("input", () => {
    clearTimeout(deb);
    deb = setTimeout(() => { state.q = q.value.trim().slice(0, 120); state.limit = PAGE; update(); }, 140);
  });
  for (const b of document.querySelectorAll(".seg button")) {
    b.addEventListener("click", () => { state.sort = b.dataset.sort; update(); });
  }
  $("#more").addEventListener("click", () => { state.limit += PAGE; render(); });
  $("#reset").addEventListener("click", () => { Object.assign(state, { kind: "all", topic: "", source: "", q: "", limit: PAGE }); q.value = ""; update(); });
  $("#about-btn").addEventListener("click", () => $("#about").showModal());
  $("#theme").addEventListener("click", toggleTheme);
  window.addEventListener("hashchange", () => { readHash(); q.value = state.q; render(); });

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (e.key === "Escape") {
      if (typing) { q.value = ""; state.q = ""; q.blur(); update(); }
      return;
    }
    if (typing) return;
    const cur = document.querySelectorAll(".card")[focusIdx];
    if (e.key === "/") { e.preventDefault(); q.focus(); q.select(); }
    else if (e.key === "j") moveFocus(1);
    else if (e.key === "k") moveFocus(-1);
    else if (e.key === "t") toggleTheme();
    else if (e.key === "o" && cur) cur.querySelector(".card-title a").click();
    else if (e.key === "s" && cur) cur.querySelector(".act-save").click();
  });
  load();
  setInterval(() => { if (feed.generated) $("#stat-updated").textContent = ago(feed.generated); }, 60000);
}
function toggleTheme() {
  const root = document.documentElement;
  const cur = root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("mct-theme", next); } catch { /* ignore */ }
}

init();
