// Behaviour for the static pages (pricing, account, legal, topics). No third-party code.
"use strict";
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const KEYS = { saved: "mct-saved", follow: "mct-follow", mute: "mct-mute", affinity: "mct-affinity", theme: "mct-theme" };
  const get = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } };

  // Theme toggle (shared with the feed page).
  const themeBtn = $("#theme");
  if (themeBtn) themeBtn.addEventListener("click", () => {
    const root = document.documentElement;
    const cur = root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEYS.theme, next); } catch { /* ignore */ }
  });

  // Pricing: monthly / yearly toggle for Pro.
  const toggle = $(".billing-toggle");
  if (toggle) {
    const price = $(".plan-featured .price");
    toggle.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-period]");
      if (!b) return;
      const yearly = b.dataset.period === "yearly";
      for (const x of toggle.querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
      // Values come from our own build step, not from user input.
      const [amt, unit] = (yearly ? price.dataset.yearly : price.dataset.monthly).split("<small>");
      price.replaceChildren();
      const a = document.createElement("span"); a.className = "amt"; a.textContent = amt;
      const u = document.createElement("small"); u.textContent = unit.replace("</small>", "");
      price.append(a, u);
      $("[data-monthly-btn]").hidden = yearly;
      $("[data-yearly-btn]").hidden = !yearly;
    });
  }

  // Account: data stored on this device.
  const device = $("#device-data");
  if (device) {
    const paint = () => {
      $('[data-stat="saved"]', device).textContent = Object.keys(get(KEYS.saved, {})).length;
      $('[data-stat="follow"]', device).textContent = get(KEYS.follow, []).length;
      $('[data-stat="mute"]', device).textContent = get(KEYS.mute, []).length;
    };
    paint();
    device.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-action]");
      if (!b) return;
      if (b.dataset.action === "export") {
        const data = {};
        for (const [k, v] of Object.entries(KEYS)) data[k] = get(v, null);
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "nascent-my-data.json";
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      } else if (b.dataset.action === "clear") {
        if (!confirm("Clear saved stories, followed topics, muted sources and preferences from this browser?")) return;
        try { for (const v of Object.values(KEYS)) localStorage.removeItem(v); } catch { /* ignore */ }
        paint();
      }
    });
  }
})();
