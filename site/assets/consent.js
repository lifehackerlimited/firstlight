// Nascent cookie consent + Google Tag Manager loader.
// Google Tag Manager (and the Google Analytics tags inside it) only loads after the visitor clicks "Accept".
// Until then nothing is sent to Google and no cookies are set. Google Consent Mode is set to "denied"
// by default; accepting grants analytics storage only. Advertising storage stays denied.
(function () {
  "use strict";
  var me = document.currentScript;
  var GTM = me && me.getAttribute("data-gtm");
  if (!GTM || !/^GTM-[A-Z0-9]+$/.test(GTM)) return;
  var KEY = "nascent-consent";
  var get = function () { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  var set = function (v) { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode */ } };

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  gtag("consent", "default", {
    analytics_storage: "denied", ad_storage: "denied", ad_user_data: "denied",
    ad_personalization: "denied", functionality_storage: "granted", security_storage: "granted"
  });

  var loaded = false;
  function load() {
    if (loaded) return;
    loaded = true;
    gtag("consent", "update", { analytics_storage: "granted" });
    window.dataLayer.push({ "gtm.start": Date.now(), event: "gtm.js" });
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtm.js?id=" + encodeURIComponent(GTM);
    document.head.appendChild(s);
  }

  function clearCookies() {
    var host = location.hostname, parts = host.split("."), domains = ["", host];
    for (var i = 1; i < parts.length - 1; i++) domains.push("." + parts.slice(i).join("."));
    document.cookie.split(";").forEach(function (c) {
      var name = c.split("=")[0].trim();
      if (!/^(_ga|_gid|_gat|_gcl)/.test(name)) return;
      domains.forEach(function (d) {
        document.cookie = name + "=; Max-Age=0; path=/" + (d ? "; domain=" + d : "");
      });
    });
  }

  var banner;
  function hide() { if (banner) { banner.remove(); banner = null; } }
  function choose(v) {
    var was = get();
    set(v);
    hide();
    if (v === "granted") load();
    else if (was === "granted" || loaded) {
      gtag("consent", "update", { analytics_storage: "denied" });
      clearCookies();
      location.reload();
    }
  }
  function show() {
    if (banner) return;
    banner = document.createElement("div");
    banner.className = "consent";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-label", "Cookie choices");
    var p = document.createElement("p");
    p.appendChild(document.createTextNode("Can we use Google Analytics cookies to see which pages people use? It helps us improve Nascent. No advertising, and nothing is set unless you accept. "));
    var a = document.createElement("a");
    a.href = ((window.NASCENT_CONFIG || {}).base || "/") + "privacy/#cookies";
    a.textContent = "Privacy policy";
    p.appendChild(a);
    var row = document.createElement("div");
    row.className = "consent-actions";
    [["Reject", "denied", "btn"], ["Accept", "granted", "btn btn-primary"]].forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = b[2];
      btn.textContent = b[0];
      btn.addEventListener("click", function () { choose(b[1]); });
      row.appendChild(btn);
    });
    banner.appendChild(p);
    banner.appendChild(row);
    (document.body || document.documentElement).appendChild(banner);
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-consent-open]");
    if (t) { e.preventDefault(); show(); }
  });

  var choice = get();
  if (choice === "granted") load();
  else if (choice === "denied") clearCookies();  // removes anything left from an earlier "Accept"
  else {
    if (document.body) show(); else document.addEventListener("DOMContentLoaded", show);
  }
})();
