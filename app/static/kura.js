// Kura's own script, after the shared machiya.js (settings, the Rooms menu, "/" for the search field, the update
// toast). Loaded as a module, so old browsers never run it; every page works without it.
document.body.classList.add("js");

const $ = (sel, root = document) => root.querySelector(sel);
const main = $("main");

function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
}
function banner(cls, html) {
  const el = document.createElement("div");
  el.className = "banner " + cls;
  el.innerHTML = html;
  (main || document.body).prepend(el);
  return el;
}

// -- Escape leaves a field ("/" to reach the search field is machiya.js's) -----
document.addEventListener("keydown", (k) => {
  const t = k.target;
  if (k.key === "Escape" && t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) t.blur();
});

// -- Reading → Obsidian Vault (/settings, per device): "Edit in Obsidian" on every note -------------
function obsidianLinks(root = document) {
  const device = String(stored("kuraSettings", {}).obsidianVault || "").trim();
  if (!device) return;                                   // Obsidian isn't set up on this device
  for (const art of root.querySelectorAll("article.note[data-path]")) {
    const meta = art.querySelector(".nmeta");
    if (!meta || meta.querySelector("a.obsidian")) continue;
    const a = document.createElement("a");
    a.className = "obsidian";
    // the default vault is the one named in Settings; a work vault carries its own Obsidian name (data-obsidian)
    a.href = "obsidian://open?vault=" + encodeURIComponent(art.dataset.obsidian || device) + "&file=" +
      encodeURIComponent(art.dataset.path.replace(/\.md$/, ""));
    a.textContent = "Edit in Obsidian";
    const br = meta.querySelector("br");
    meta.insertBefore(document.createTextNode(" \u00b7 "), br);
    meta.insertBefore(a, br);
  }
}
obsidianLinks();

// -- PWA: service worker, offline copy, install hint ----------------------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* http on the LAN, or blocked */ });
}
if (document.body.dataset.offline !== undefined) {
  const at = Date.parse(document.body.dataset.offline || "");
  let when = "";
  if (!isNaN(at)) {
    const mins = Math.max(1, Math.round((Date.now() - at) / 60000));
    when = mins < 60 ? mins + " min ago" : mins < 1440 ? Math.round(mins / 60) + " h ago" : Math.round(mins / 1440) + " d ago";
  }
  banner("offline", "<span><b>Offline.</b> Showing a saved copy" + (when ? " from " + when : "") + ". Check your network or VPN.</span>");
}
const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
const standalone = navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
// Installed apps have no browser chrome: give inner pages a back control.
const roots = ["/", "/recent", "/search", "/t/"];      // the tab bar's pages
if (standalone && history.length > 1 && !roots.includes(location.pathname)) {
  const back = document.createElement("button");
  back.className = "back";
  back.type = "button";
  back.setAttribute("aria-label", "Back");
  back.textContent = "\u2039";
  back.addEventListener("click", () => history.back());
  $(".topbar")?.prepend(back);
}
if (ios && !standalone && !stored("app.hint", false)) {
  const el = banner("hint", "<span>Install this as an app: tap <b>Share</b>, then <b>Add to Home Screen</b>.</span>"
    + '<button type="button" aria-label="Dismiss">&times;</button>');
  $("button", el).addEventListener("click", () => { store("app.hint", true); el.remove(); });
}

// -- the vault menu (a <details>) closes on Escape or a click outside --------------------------------
document.addEventListener("click", (ev) => {
  for (const d of document.querySelectorAll("details.vaults[open]")) if (!d.contains(ev.target)) d.open = false;
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") for (const d of document.querySelectorAll("details.vaults[open]")) d.open = false;
});

// Mermaid diagrams in notes (Obsidian's ```mermaid blocks, which python-markdown renders as
// <pre><code class="language-mermaid">). The vendored library (static/mermaid.min.js, ~5.5 MB, MIT) is fetched only
// on a page that has a diagram, and cached for a year (bump MERMAID_V with the file). Colours follow the page theme
// (Tokyo Night / Day). securityLevel "strict": no scripts or click handlers from note text. A diagram that fails to
// render keeps its source visible.
const MERMAID_V = "12.0.0";
let mermaidN = 0, mermaidLoad = null;
function mermaidDiagrams(root = document) {
  const blocks = [...root.querySelectorAll("pre > code.language-mermaid")];
  if (!blocks.length) return;
  const body = document.body.classList;
  const dark = body.contains("theme-night") ||
    (!body.contains("theme-day") && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const vars = dark
    ? {background: "#1a1b26", primaryColor: "#24283b", primaryTextColor: "#c0caf5", primaryBorderColor: "#7aa2f7",
       secondaryColor: "#1f2335", tertiaryColor: "#16161e", lineColor: "#7aa2f7", textColor: "#c0caf5",
       clusterBkg: "#1f2335", clusterBorder: "#3b4261", edgeLabelBackground: "#1f2335", noteBkgColor: "#292e42",
       noteTextColor: "#c0caf5", actorBkg: "#24283b", actorBorder: "#7aa2f7", actorTextColor: "#c0caf5",
       signalColor: "#c0caf5", signalTextColor: "#c0caf5"}
    : {background: "#e1e2e7", primaryColor: "#e9e9ed", primaryTextColor: "#343b58", primaryBorderColor: "#2e7de9",
       secondaryColor: "#d5d8e4", tertiaryColor: "#eef0f5", lineColor: "#2e7de9", textColor: "#343b58",
       clusterBkg: "#eef0f5", clusterBorder: "#c4c8da", edgeLabelBackground: "#eef0f5", noteBkgColor: "#d5d8e4",
       noteTextColor: "#343b58", actorBkg: "#e9e9ed", actorBorder: "#2e7de9", actorTextColor: "#343b58",
       signalColor: "#343b58", signalTextColor: "#343b58"};
  mermaidLoad = mermaidLoad || new Promise((ok) => {      // the library loads once per page
    const s = document.createElement("script");
    s.src = "/static/mermaid.min.js?v=" + MERMAID_V;
    s.onload = () => {
      window.mermaid.initialize({startOnLoad: false, securityLevel: "strict", theme: "base", themeVariables: vars,
                                 fontFamily: "inherit", flowchart: {htmlLabels: true, useMaxWidth: true}});
      ok(window.mermaid);
    };
    document.head.appendChild(s);
  });
  mermaidLoad.then(async (mm) => {
    for (const code of blocks) {
      const n = ++mermaidN;
      const pre = code.parentElement;
      try {
        const {svg} = await mm.render("mmd-" + n, code.textContent);
        const div = document.createElement("div");
        div.className = "mermaid-diagram";
        div.innerHTML = svg;
        pre.replaceWith(div);
      } catch (err) {
        pre.title = "Mermaid couldn't render this diagram: " + (err && err.message || err);
        document.querySelectorAll("#dmmd-" + n).forEach((x) => x.remove());   // mermaid's error box
      }
    }
  });
}
mermaidDiagrams();

// kura's reader: on wide screens a note link in the list (or inside the preview) loads that note into the preview
// pane instead of leaving the page; "Open" (or a modifier click) opens it fully. ?p=<slug> keeps the choice on reload.
(function kuraPreview() {
  const pane = document.querySelector(".kpreview");
  if (!pane) return;
  let seq = 0;
  const select = (href) => {
    for (const a of document.querySelectorAll(".klist a.kn")) a.classList.toggle("sel", a.getAttribute("href") === href);
  };
  document.addEventListener("click", async (ev) => {
    const a = ev.target.closest(".klist a.kn, .kpreview .nbody a[href*='/n/'], .kpreview .gsec a[href*='/n/']");
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
    if (getComputedStyle(pane).display === "none") return;            // tablets and phones: open normally
    const href = a.getAttribute("href");
    const m = href.match(/^((?:\/v\/[a-z0-9-]+)?)\/n\/(.*)$/);      // "/n/<slug>" or "/v/<vault>/n/<slug>"
    if (!m) return;                                                  // not a note link (https://x/n/y): open it normally
    ev.preventDefault();
    const my = ++seq;
    select(href);
    pane.classList.add("loading");
    try {
      const res = await fetch(m[1] + "/preview/" + m[2]);
      if (!res.ok) throw new Error(res.status);
      const html = await res.text();
      if (my !== seq) return;                                          // a later click won
      pane.innerHTML = html;
      pane.scrollTop = 0;
      mermaidDiagrams(pane);
      obsidianLinks(pane);
      const u = new URL(location.href);
      u.searchParams.set("p", decodeURIComponent(m[2]));
      history.replaceState(null, "", u);
    } catch (err) {
      location.href = href;                                            // offline or gone: open it the plain way
    } finally {
      if (my === seq) pane.classList.remove("loading");
    }
  });
})();
