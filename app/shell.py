"""Kura's page shell: Machiya's shared shell (vaultkit.shell with ui/machiya.css, machiya.js and machiya-sw.js,
docs/ui.md in machiya) as the room `kura` (蔵, orange), plus what is Kura's own: the tab and reader glyphs, the PWA
manifest, the service worker's settings and the /settings page. kura.css keeps only the reader's own layout
(folders | notes | preview).

Rooms: the switcher lists MACHIYA_ROOMS (the stack sets it). Standing alone, it falls back to Kura's own sister
settings (kura.py sets NIWA_URL / KONBINI_URL from KURA_NIWA_URL / KURA_KONBINI_URL); with neither, no switcher.
"""
import hashlib
import os
import threading

from vaultkit import histerauth
from vaultkit import shell as house
from vaultkit.shell import OFFLINE_PIN, e, prefs  # noqa: F401  (kura.py and pages.py use them from here)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UI_DIR = house.UI_DIR
ICON_DIR = os.path.join(STATIC_DIR, "icons")
ICONS = set(n for n in os.listdir(ICON_DIR) if n.endswith((".png", ".svg"))) if os.path.isdir(ICON_DIR) else set()
OWN_FILES = ("kura.css", "kura.js", "mermaid.min.js")
NIWA_URL = ""                       # e.g. https://niwa.example.ts.net (no trailing slash); "" = no link
KONBINI_URL = ""
STATUS = None                       # kura.py: a function(site) returning the footer's {"text": …, "state": "ok|stale|down"}
SITES = []                          # kura.py: the vaults (sites.Site), the default first
view = threading.local()            # kura.py sets, per request: view.sites (the vaults it may read), view.who (the
                                    # signed-in name, identity file only) and view.prefs_url ("/api/prefs" or "")


def sites():
    """The vaults the current request may see (its identity's grant: possibly none), else all of them (nothing set)."""
    seen = getattr(view, "sites", None)
    return SITES if seen is None else seen
COUNTS = lambda: {}                 # kura.py: {vault name: notes}, for the vault switch
ROOM = "kura"
house.APP_PREFS = {"previewPane": {"type": "bool", "cookie": True}}      # follows the person (docs/contracts/prefs.md)
SIGNIN = False                      # kura.py: KURA_AUTH=hister, so every page carries the machiya-signin meta


def default_site():
    return SITES[0] if SITES else None


def nav(site):
    p = site.prefix if site else ""
    return [(p + "/", "kura", "Home"), (p + "/recent", "recent", "Recent"), (p + "/t/", "tags", "Tags")]


def tabs(site):
    p = site.prefix if site else ""
    return [(p + "/", "kura", "Home"), (p + "/recent", "recent", "Recent"), (p + "/t/", "tags", "Tags")]


def static_path(name):
    """Where /static/<name> lives: Kura's own files, or the vendored shared UI (machiya.css, .js, -sw.js)."""
    if name in house.UI_VERSION:
        return os.path.join(UI_DIR, name)
    return os.path.join(STATIC_DIR, name)


def _hash(name):
    try:
        with open(static_path(name), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return "0"


STATIC_V = {n: _hash(n) for n in OWN_FILES}
STATIC_V.update(house.UI_VERSION)
VERSION = hashlib.sha1("".join(sorted(STATIC_V.values())).encode()).hexdigest()[:10]


def static_url(name):
    if name in house.UI_VERSION:
        return house.ui_url(name)
    return "/static/%s?v=%s" % (name, STATIC_V.get(name, "0"))


def rooms():
    """The switcher's rooms: the stack's MACHIYA_ROOMS, else Kura's own sister settings."""
    return house.rooms() or {k: v for k, v in (("konbini", KONBINI_URL), ("niwa", NIWA_URL)) if v}


# -- PWA ---------------------------------------------------------------------

NAME, DESC = "Kura", "Every note in the vault, with working links and full-text search"


def shell_urls():
    """What the service worker precaches: the exact (versioned) URLs the pages link."""
    return [static_url("machiya.css"), static_url("machiya.js"), static_url("kura.css"), static_url("kura.js"),
            "/static/icons/kura.svg", "/static/icons/kura-192.png", "/offline"]


def manifest(theme, headers=None, palette=None):
    """theme: the request's Settings choice; headers: the request's (Sec-CH-Prefers-Color-Scheme picks System's
    colours: house.manifest_colors)."""
    return {
        "name": NAME, "short_name": NAME, "description": DESC,
        "id": "/", "start_url": "/", "scope": "/", "display": "standalone", "lang": "en",
        "categories": ["productivity", "books"],
        "shortcuts": [{"name": name, "url": url, "icons": [{"src": "/static/icons/kura-192.png", "sizes": "192x192"}]}
                      for name, url in (("Recently Changed", "/recent"), ("Search", "/search"), ("Tags", "/t/"))],
        "icons": [
            {"src": "/static/icons/kura-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/kura-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/kura-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        **house.manifest_colors(theme, headers, palette or house.palettes.DEFAULT),
    }


def service_worker():
    """/sw.js: Machiya's shared worker core configured for the reader. Notes (/n/) are kept for offline reading (the
    200 most recently read; pinned ones, `offline: true`, for good, fetched ahead via /api/offline); search and
    settings are never stored; the APIs, /theme and /preview/ (the pane's fragments) are never touched. Pages
    that must never be on a device (Archive/, and every private vault under /v/, which is network-only here) answer
    Cache-Control: no-store, which the worker honours. Fail closed: /v/ is network-only except the shared vaults
    named here, so a vault the worker doesn't know of (or one made private since) is never stored."""
    shared = "|".join(x.name for x in SITES if x.shared)       # names are [a-z0-9-]+: nothing to escape
    under = "(?:/v/(?:%s))?" % shared if shared else ""
    v = "^/v/(?!(?:%s)/)" % shared if shared else "^/v/"
    return house.service_worker(VERSION, shell_urls(), offline="/offline", bypass=["^/api/", "^/theme$", "^/preview/"],
                                network=["^%s/search$" % under, "^/settings$", "^/signin$", v], notes={"match": "^%s/n/" % under, "limit": 200},
                                pages=30, assetMatch=["^%s/a/" % under], assets=100, pins="/api/offline")


# -- the page ------------------------------------------------------------------

_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true">%s</svg>')
ICON = {    # Kura's tabs and reader glyphs; Home is the house's kura glyph (a book)
    "kura": house.GLYPH["kura"],
    "recent": _SVG % '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "search": _SVG % '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "tags": _SVG % '<path d="M3 12V4h8l9 9-8 8-9-9z"/><circle cx="7" cy="8" r="1.5"/>',
    "folder": _SVG % '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>',
    "open": _SVG % '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "note": _SVG % '<path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6"/>',
}


def vault_switch(site):
    """The vault chip and menu (only with more than one vault): the current vault's title, then every vault with
    its note count. A work vault's chip is yellow, so a work note never looks like a personal one."""
    if len(sites()) < 2:
        return ""
    counts = COUNTS()
    def what(x):            # the count, and whether the vault stays off devices (private) or is shared
        return "%d%s" % (counts.get(x.name, 0), " · private" if x.private else " · shared" if x.shared else "")
    rows = "".join(
        ('<b>%s<small>%s</small></b>' if x is site else '<a href="%s/">%s<small>%s</small></a>')
        % ((e(x.title), what(x)) if x is site else (e(x.prefix), e(x.title), what(x)))
        for x in sites())
    return ('<details class="vaults"><summary class="chip%s" title="Vaults" aria-label="Vaults: %s">%s</summary>'
            '<nav class="menu" aria-label="Vaults">%s</nav></details>'
            % (" private" if site.private else "", e(site.title), e(site.title), rows))


def search_bar(site, q="", everywhere=False, focus=False):
    """The search pill under the header (vaultkit's search_bar; machiya.js shows the results as you type by fetching the
    vault's /search and swapping <main>). `everywhere`: the owner's "All Vaults" search keeps `vaults=all` in both the
    form and the live fetch. `focus`: the empty search page puts the cursor in it."""
    p = site.prefix if site else ""
    what = "Search Notes" if not site or site.default else "Search " + site.title
    bar = house.search_bar(q, p + "/search" + ("?vaults=all" if everywhere else ""), what, "Search every note")
    if everywhere:
        bar = bar.replace("</form>", '<input type="hidden" name="vaults" value="all"></form>', 1)
    if focus:
        bar = bar.replace('enterkeyhint="search"', 'enterkeyhint="search" autofocus', 1)
    return bar


def header(site, current, subtitle="", q="", everywhere=False, focus=False):
    """The room's header: nav, the vault switch, the Rooms switcher and the gear, with the search pill (the page's own
    field: /search has none in <main>) under them. `site`: the vault being read (its prefix goes into every link)."""
    site = site or default_site()
    tools = vault_switch(site) if site else ""
    return house.header(ROOM, nav(site), current, rooms(), subtitle, tools, who=getattr(view, "who", ""),
                        search=search_bar(site, q, everywhere, focus))


def feed_url(site):
    """The vault's own feed (the default's /feed.xml, a shared one's /v/<name>/feed.xml); a private vault has none."""
    if not site or site.private:
        return ""
    return site.prefix + "/feed.xml"


def feed_link(site):
    url = feed_url(site)
    return ('<link rel="alternate" type="application/rss+xml" title="%s" href="%s">\n'
            % (e(house.title(ROOM, "" if site.default else site.title)), e(url))) if url else ""


def footer(site):
    st = STATUS(site) if STATUS else None
    url = feed_url(site)
    return house.footer(ROOM, st, [(url, "RSS")] if url else [])


def page(ctx, site, what, body, current="", head=""):
    """body holds the header and <main>; the footer and the tab bar are added here. `what`: the page's own name
    ("" on a vault's home); the title adds the vault (another vault than the default) and the room."""
    site = site or default_site()
    vault = site.title if site and not site.default else ""
    title = house.title(ROOM, " · ".join(x for x in (what, vault) if x))
    meta = histerauth.signin_meta("/signout") if SIGNIN else ""
    return house.page(ctx, ROOM, title, body + footer(site), tabs(site), current, links=rooms(), head=head + meta + feed_link(site),
                      prefs_url=getattr(view, "prefs_url", ""), who=getattr(view, "who", ""),
                      stylesheets=[static_url("kura.css")], scripts=[static_url("kura.js")], icons=ICON)


def message(ctx, title, text, site=None, actions=()):
    return page(ctx, site, title, header(site, "") + house.message(title, text, actions))


def signed_out(ctx):
    """Where /signout lands (hister mode, before the gate): no notes, no vault switch, nothing to name to a stranger."""
    body = house.message("Signed Out", "You're signed out of every room.", [("/", "Sign In Again")])
    return house.page(ctx, ROOM, house.title(ROOM, "Signed Out"), house.header(ROOM, [], "", {}, settings=False) + body,
                      links={}, manifest=False)


def offline(ctx):
    """The precached /offline: no header search or status line (they'd be frozen at install time)."""
    return house.page(ctx, ROOM, house.title(ROOM, "Offline"), house.header(ROOM, [], "", rooms()) + house.offline(ROOM),
                      tabs(None), "", links=rooms(), stylesheets=[static_url("kura.css")], scripts=[static_url("kura.js")],
                      icons=ICON)


def preview_pane(ctx):
    """Reading → Preview Pane (a cookie, so the first render knows): on unless switched off on this device."""
    return (getattr(ctx, "extra", {}) or {}).get("previewPane") != "false"


def settings(ctx, version, status_text, vaultkit, account="", prefs_state="standalone", who=""):
    """vaultkit's Settings order (docs/ui.md): Shared, Kura's Reading, This Device, Account, About. `prefs_state`: where
    the Shared choices are kept (histerauth.prefs_state, "room" for Kura's own store, else "standalone"); `who`: the
    signed-in name for its line. `account`: the principal's name when this request came with a sign-in session; it gets
    a Sign Out button."""
    reading = ("Reading", [house.toggle("Preview Pane", "previewPane", preview_pane(ctx), cookie=True)],
               "On wide screens, a note picked in a list opens beside it.")
    device = house.device_section(
        ctx, [house.offline_row(), house.text_field("Obsidian Vault", "obsidianVault", "", "my-vault")],
        "Obsidian Vault adds Edit in Obsidian links. Archive/ notes are never kept offline.")
    signed_in = None
    if account:         # a plain form: sign-out is a same-origin POST (vaultkit.signin), and works without JavaScript
        signed_in = ("Account", ['<form class="item" method="post" action="/signout"><span>Signed in as %s</span>'
                                 '<button type="submit">Sign Out</button></form>' % e(account)],
                     "Signs this browser out, of every room if they share a sign-in.")
    sections = [house.shared_section(ctx, ROOM, rooms(), prefs_state, who), reading, device, signed_in,
                house.about_section(ROOM, version, status_text, vaultkit)]
    return page(ctx, None, "Settings", header(None, "", "Settings") + house.settings_page(sections, ROOM))
