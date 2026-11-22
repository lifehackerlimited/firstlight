// Runs before paint so the saved theme never flashes.
(function () {
  try {
    var t = localStorage.getItem("mct-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: follow the OS */ }
})();
