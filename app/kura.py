"""Kura (蔵): every note in the vault, with working links, full-text search and a JSON API. Part of Machiya
(machiya-kobo/machiya: docs/services/kura.md, docs/contracts/kura-api.md).

Standalone: Kura keeps its own clone of the vault (vaultkit.Mirror, https/ssh/file, polled every KURA_POLL seconds),
its own index (vaultkit.Vault + search.Index, rebuilt when the commit changes), and a small SQLite file (KURA_DB) with
what it pushed into Hister (push.py, when KURA_HISTER_URL is set). Niwa, Konbini and Hister are optional URLs.
Every request needs a Tailscale-User-Login in KURA_USERS (`*` = anyone), except /api/status, which the monitoring
probes read; KURA_AUTH=open drops that check for localhost or a trusted LAN. Native installs (docs/install/bsd.md in
machiya) put the settings in a file: KURA_ENV_FILE or --env-file PATH, read before anything else.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from vaultkit import envfile

try:        # first: every setting below (and vaultkit.shell's MACHIYA_*) may come from the file
    ENV_FILE = envfile.load_for("kura")
except (OSError, envfile.EnvFileError) as err:
    raise SystemExit("kura: env file: %s" % err)

import api  # noqa: E402
import pages  # noqa: E402
import sites  # noqa: E402
import push  # noqa: E402
import search  # noqa: E402
import shell  # noqa: E402
from vaultkit import verify as vk_verify  # noqa: E402

VERSION = "0.4.3"
PORT = int(os.environ.get("KURA_PORT", "8080"))
REPO_URL = os.environ.get("KURA_REPO_URL", "").strip()
REPO_DIR = os.environ.get("KURA_REPO_DIR", "/data/repo")
SUBDIR = os.environ.get("KURA_REPO_SUBDIR", "").strip("/")      # "" = the vault is the repo root
BRANCH = os.environ.get("KURA_REPO_BRANCH", "")
TOKEN_FILE = os.environ.get("KURA_REPO_TOKEN_FILE", "")
REPO_USER = os.environ.get("KURA_REPO_USER", "") or "token"
POLL = max(10, int(os.environ.get("KURA_POLL", "60")))
USERS = set(filter(None, (u.strip() for u in os.environ.get("KURA_USERS", "").split(","))))


def auth_mode(value):
    """KURA_AUTH: "tailscale" (the default: Tailscale-User-Login must be in KURA_USERS) or "open" (no identity check,
    for localhost or a trusted LAN). Anything else refuses to start rather than guess."""
    value = (value or "tailscale").strip().lower()
    if value not in ("tailscale", "open"):
        raise SystemExit("kura: KURA_AUTH must be tailscale or open, not %r" % value)
    return value


AUTH = auth_mode(os.environ.get("KURA_AUTH"))
# The address Kura listens on. A native install behind `tailscale serve` binds 127.0.0.1: on a public bind the
# Tailscale-User-Login header could be sent by anyone who reaches the port.
BIND = os.environ.get("KURA_BIND", "0.0.0.0").strip() or "0.0.0.0"


def public_url(value):
    """KURA_PUBLIC_URL: an origin only (http(s), a host, maybe a port), with no path. Every note's url is <origin>/n/…
    or <origin>/v/<vault>/n/…, and clients tell a work vault's note by /v/ at the start of the path; under a path
    (https://host/kura) they couldn't, and the reader's own links start at the root anyway. Anything else refuses to
    start."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    u = urlsplit(value)
    try:
        u.port                                                     # a malformed port raises
        ok = u.scheme in ("http", "https") and bool(u.hostname) and not (u.path or u.query or u.fragment)
    except ValueError:
        ok = False
    if not ok or "@" in u.netloc:
        raise SystemExit("kura: KURA_PUBLIC_URL must be an origin with no path, like https://kura.example, not %r"
                         % value)
    return value


PUBLIC_URL = public_url(os.environ.get("KURA_PUBLIC_URL"))
api.PUBLIC_URL = PUBLIC_URL
api.SHIORI_LINKS = os.environ.get("KURA_SHIORI_LINKS", "").strip().lower() in ("1", "true", "yes", "on")
HISTER_URL = os.environ.get("KURA_HISTER_URL", "").rstrip("/")         # set: push every note into Hister
DB = os.environ.get("KURA_DB", "/data/kura.sqlite3")
shell.NIWA_URL = os.environ.get("KURA_NIWA_URL", "").rstrip("/")
shell.KONBINI_URL = api.KONBINI_URL = os.environ.get("KURA_KONBINI_URL", "").rstrip("/")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
STATIC_TYPES = {"kura.css": "text/css", "kura.js": "text/javascript", "mermaid.min.js": "text/javascript",
                "machiya.css": "text/css", "machiya.js": "text/javascript",         # machiya.*: the vendored shared UI
                "machiya-sw.js": "text/javascript"}
NO_STORE = ("Cache-Control", "no-store")      # pages never kept on a device (pages.NEVER_STORED: Archive/)
STALE = 600                                   # seconds without a good sync before the footer turns yellow
MAX_LIMIT = 100


def changed_times(run, subdirs):
    """{subdir: {rel: unix time of the note's latest commit}}: the exact time for the API (vault.tended has only the
    day). One `git log` for every vault of a checkout; a file counts for the longest matching subdir."""
    subdirs = list(subdirs)
    out = run("log", "--format=@%at", "--name-only", "--", *[d or "." for d in subdirs])
    times = {d: {} for d in subdirs}
    current = None
    for line in out.splitlines():
        if line.startswith("@"):
            current = int(line[1:]) if line[1:].isdigit() else None
        elif line and current:
            best = max((d for d in subdirs if not d or line.startswith(d + "/")), key=len, default=None)
            if best is not None:
                times[best].setdefault(line[len(best) + 1:] if best else line, current)
    return times


def footer_status(site=None):
    """The footer's status line (shell.footer) and About's Vault row: "synced abc1234 3 min ago · 42 notes". A work
    vault's line starts with its title."""
    site = site or state.default
    label = (site.title + " · ") if site.private else ""
    if not site.ready:
        return {"text": label + "starting: cloning the vault", "state": "down"}
    age = int(time.time()) - (site.synced_at or 0)
    when = "just now" if age < 60 else "%d min ago" % (age // 60) if age < 3600 else "%d h ago" % (age // 3600)
    text = "%ssynced %s %s · %d notes" % (label, site.head[:7], when, state.index.counts.get(site.name, 0))
    if site.error:
        return {"text": text + " · last sync failed", "state": "down"}
    return {"text": text, "state": "stale" if age > STALE else "ok"}


shell.STATUS = footer_status


def vaultkit_version():
    """'v0.9.6' from the vendored manifest's first line."""
    return vk_verify.version().split(" - ")[0]


def safe_url(url):
    """The repo URL without credentials, for logs and /api/status."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        return parts._replace(netloc=parts.hostname + (":%d" % parts.port if parts.port else "")).geturl()
    return url


class State:
    """The vaults (sites.py: the default first), their checkouts, the one search index and the Hister push, refreshed
    by the sync thread."""

    def __init__(self):
        self.sites, self.sources = sites.build(os.environ, REPO_URL, REPO_DIR, SUBDIR, BRANCH, TOKEN_FILE, REPO_USER)
        self.default = self.sites[0]
        self.by_name = {x.name: x for x in self.sites}
        self.index = search.Index()
        self.loop_error = None
        # The push needs a stable public base for its URLs (Hister documents key on them), so it needs PUBLIC_URL.
        # It only ever sends the default vault: work vaults never reach Hister (push.py refuses a private one).
        self.push = push.Push(HISTER_URL, DB, self.default.subdir, PUBLIC_URL) if HISTER_URL and PUBLIC_URL else None

    @property
    def vault(self):
        return self.default

    @property
    def ready(self):
        return all(x.ready for x in self.sites)

    @property
    def error(self):
        """The first vault's sync error (the probe fails when any vault's last sync failed)."""
        return next((x.error for x in self.sites if x.error), None) or self.loop_error

    def sync(self):
        times = {}
        for src in self.sources:
            try:
                src.update()
                src.error = None
            except Exception as err:                    # this checkout only: the others keep syncing
                src.error = "%s: %s" % (type(err).__name__, err)
                print("kura: sync of %s failed: %s" % (src.dir, src.error), flush=True)
        for site in self.sites:
            src = site.checkout
            if src.error:
                site.error = src.error
                continue
            head = src.head
            if not head:
                site.error = "no commit yet (clone or fetch failed; see the log)"
                continue
            if head != site.revision or not site.ready:
                started = time.monotonic()
                site.revision = head
                site.index()
                if (id(src), head) not in times:
                    times[(id(src), head)] = changed_times(src.git.run, [x.subdir for x in self.sites if x.checkout is src])
                site.changed_at = times[(id(src), head)].get(site.subdir, {})
                self.index.rebuild(site.name, site, pages.visible(site))
                print("kura: indexed %d notes of %s at %s in %.1fs" % (
                    self.index.counts[site.name], site.name, head[:10], time.monotonic() - started), flush=True)
                site.ready = True
                if site.default and self.push:
                    self.push.pending = True
            site.head, site.synced_at, site.error = head, int(time.time()), None
        self.loop_error = None
        if self.push and self.push.pending and self.default.ready:
            self.push.run_once(self.default, pages.visible(self.default), self.default.head)

    def loop(self):
        while True:
            try:
                self.sync()
            except Exception as err:                    # keep serving the last good index
                self.loop_error = "%s: %s" % (type(err).__name__, err)
                print("kura: sync failed: %s" % self.loop_error, flush=True)
            time.sleep(POLL)


state = State()
shell.SITES = state.sites
shell.COUNTS = lambda: state.index.counts


def ints(query, key, default, top):
    try:
        return max(0, min(top, int((query.get(key) or [default])[0])))
    except ValueError:
        return default


def multi(query, key):
    """?paths=a&paths=b or ?paths=a,b"""
    out = []
    for v in query.get(key, []):
        out.extend(p for p in v.split(",") if p)
    return out[:MAX_LIMIT]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "kura/" + VERSION

    def log_message(self, fmt, *args):
        if self.path == "/api/status":              # the healthcheck and probes, every minute
            return
        sys.stderr.write("%s %s\n" % (self.actor() or "-", fmt % args))

    def actor(self):
        """Who is asking, for the log. Open mode: always "local", since nothing vouches for the header there."""
        if AUTH == "open":
            return "local"
        return self.headers.get("Tailscale-User-Login", "")

    def allowed(self):
        if AUTH == "open":
            return True
        return "*" in USERS or self.headers.get("Tailscale-User-Login", "") in USERS

    def base(self):
        return PUBLIC_URL or "https://%s" % (self.headers.get("Host") or "localhost")

    def ctx(self):
        return shell.prefs(self.headers.get("Cookie"))     # theme, text size, previewPane (machiya.js writes them)

    def send(self, status, body, ctype="text/html", headers=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        if isinstance(body, str):
            ctype += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, status, obj):
        self.send(status, json.dumps(obj, ensure_ascii=False, indent=1), "application/json",
                  headers=[("Cache-Control", "no-cache")])

    def do_HEAD(self):
        self.do_GET()

    def status(self, owner):
        """/api/status needs no identity (the probes read it), so the open view carries no configuration: no repo URL, no
        folder, and error texts reduced to "sync failed" / "push failed" (they can name hosts and paths), which still
        fail a probe that looks for `"error": "`. A request that passes the owner gate gets the full answer."""
        d = state.default

        def err(text, short):
            return text if owner or not text else short
        push = state.push.status() if state.push else "off"
        if owner is False and isinstance(push, dict):
            push = dict(push, error=err(push.get("error"), "push failed"))
        out = {"head": d.head, "synced_at": d.synced_at, "notes": state.index.counts.get(d.name, 0),
               "ready": state.ready, "version": VERSION, "vaultkit": vaultkit_version()}
        if owner:
            out.update(repo=safe_url(REPO_URL) or REPO_DIR, subdir=d.subdir)
        out["error"] = err(state.error, "sync failed")
        # a work vault shows only its error here (/api/vaults, owner-gated, has the head and the count)
        out["vaults"] = {x.name: {"error": err(x.error, "sync failed")} if x.private else
                         {"head": x.head, "synced_at": x.synced_at, "notes": state.index.counts.get(x.name, 0),
                          "error": err(x.error, "sync failed")} for x in state.sites}
        out["push"], out["auth"] = push, AUTH
        return out

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = unquote(url.path), parse_qs(url.query)
        if path == "/api/status":
            return self.send_json(200, self.status(self.allowed()))
        if not self.allowed():
            return self.send(403, "forbidden\n", "text/plain")
        ctx = self.ctx()
        if path == "/manifest.webmanifest":
            return self.send(200, json.dumps(shell.manifest(ctx.theme), indent=1), "application/manifest+json",
                             headers=[("Cache-Control", "no-cache")])
        if path == "/sw.js":
            return self.send(200, shell.service_worker(), "text/javascript", headers=[("Cache-Control", "no-cache")])
        if path == "/offline":
            return self.send(200, shell.message(ctx, "Offline", "This page needs the network. Check your network or "
                                                "VPN. Notes you have read before still open."))
        if path.startswith("/static/"):
            return self.static(path[8:], query)
        if path == "/theme":            # the no-JavaScript fallback for /settings' Theme
            theme = (query.get("set") or ["system"])[0]
            theme = {"auto": "system"}.get(theme, theme)
            theme = theme if theme in ("night", "day", "system") else "system"
            ref = urlsplit(self.headers.get("Referer") or "")
            cookies = [("Set-Cookie", "theme=%s; path=/; max-age=31536000; samesite=lax" % theme)]
            if shell.house.COOKIE_DOMAIN:     # the shared cookie wins over the room's own (shell.prefs)
                cookies.append(("Set-Cookie", "machiya_theme=%s; domain=%s; path=/; max-age=31536000; samesite=lax"
                                % (theme, shell.house.COOKIE_DOMAIN)))
            return self.send(302, "", "text/plain", headers=[("Location", ref.path or "/")] + cookies)
        if path == "/settings":
            return self.send(200, shell.settings(ctx, VERSION, footer_status()["text"], vaultkit_version()),
                             headers=[("Cache-Control", "no-cache")])
        if not state.default.ready:
            if path.startswith("/api/") or path == "/feed.xml":
                return self.send_json(503, {"error": "Kura is still cloning the vault; try again shortly"})
            return self.send(503, shell.message(ctx, "Starting", "Kura is still cloning the vault. Try again in a "
                                                "minute."), headers=[("Retry-After", "30")])
        if path.startswith("/api/"):
            return self.api(path, query)
        if path == "/feed.xml":
            return self.feed(query)
        site, rest = state.default, path
        if path.startswith("/v/"):                      # another vault: /v/<name>/n/…, /v/<name>/f/…, …
            name, _, tail = path[3:].partition("/")
            site, rest = state.by_name.get(name), "/" + tail
            if site is None:
                return self.send(404, pages.missing(ctx, state.default, path), headers=[NO_STORE])
            if site.default:                            # /v/<default>/n/X is /n/X
                return self.send(301, "", "text/plain", headers=[("Location", rest + ("?" + url.query if url.query else ""))])
            if not site.ready:
                return self.send(503, shell.message(ctx, "Starting", "This vault is still being read. Try again in a "
                                                    "minute.", site), headers=[("Retry-After", "30"), NO_STORE])
        return self.reader(site, rest, query, ctx)

    # -- static ----------------------------------------------------------------------------------------------

    def static(self, name, query):
        if name.startswith("icons/"):
            icon = name[6:]
            if icon in shell.ICONS:
                with open(os.path.join(shell.ICON_DIR, icon), "rb") as f:
                    return self.send(200, f.read(), "image/svg+xml" if icon.endswith(".svg") else "image/png",
                                     headers=[("Cache-Control", "public, max-age=604800")])
        elif name in STATIC_TYPES:
            cache = "public, max-age=31536000, immutable" if query.get("v") else "max-age=300"
            with open(shell.static_path(name), "rb") as f:
                return self.send(200, f.read(), STATIC_TYPES[name], headers=[("Cache-Control", cache)])
        self.send(404, "not found\n", "text/plain")

    # -- the reader ------------------------------------------------------------------------------------------

    def hold(self, site, rel=""):
        """Cache-Control: no-store for what must never be kept on a device: every work vault, and Archive/ anywhere."""
        return [NO_STORE] if site.private or (rel and pages.never_stored(rel)) else []

    def reader(self, site, path, query, ctx):
        g = site
        sel = (query.get("p") or [""])[0]
        chosen = g.get(sel) if sel else None       # a list page's ?p= note: no-store when it's under Archive/
        keep = self.hold(g, chosen.rel if chosen else "")
        if path in ("", "/"):
            self.send(200, pages.home(ctx, g, sel), headers=keep)
        elif path == "/recent":
            self.send(200, pages.recent(ctx, g, sel), headers=keep)
        elif path.startswith("/preview/"):
            n = g.get(path[9:])
            if n and not n.rel.startswith(pages.HIDDEN):
                self.send(200, pages.preview(g, n), headers=self.hold(g, n.rel))
            else:
                self.send(404, "not found\n", "text/plain", headers=self.hold(g))
        elif path == "/t" or path.startswith("/t/"):
            html = pages.tag(ctx, g, path[3:], sel)
            self.send(200 if html else 404, html or pages.missing(ctx, g, "#" + path[3:]), headers=keep)
        elif path == "/f" or path.startswith("/f/"):
            html = pages.folder(ctx, g, path[3:], sel)
            self.send(200 if html else 404, html or pages.missing(ctx, g, path[3:]),
                      headers=self.hold(g, path[3:]) or keep)
        elif path.startswith("/n/"):
            n = g.get(path[3:])
            if n and not n.rel.startswith(pages.HIDDEN):
                self.send(200, pages.note(ctx, g, n), headers=self.hold(g, n.rel))
            else:
                self.send(404, pages.missing(ctx, g, path[3:]), headers=self.hold(g))
        elif path == "/search":
            q = (query.get("q") or [""])[0].strip()[:300]
            everywhere = (query.get("vaults") or [""])[0] == "all"
            names = [x.name for x in state.sites] if everywhere else [g.name]
            hits, total, err = [], 0, ""
            if q:
                try:
                    total, rows, _ = state.index.search(q, limit=50, vaults=names)
                    hits = [(state.by_name[v], state.by_name[v].notes.get(rel),
                             snip.replace(search.HIT_OPEN, "").replace(search.HIT_CLOSE, "") if snip else None)
                            for v, rel, snip in rows]
                except ValueError as e:
                    err = str(e)
            self.send(200, pages.search(ctx, g, q, hits, total, err, sel, everywhere),
                      headers=[NO_STORE] if everywhere or g.private else keep)
        elif path.startswith("/a/"):
            full = g.asset_path(path[3:])
            ctype = IMAGE_TYPES.get(os.path.splitext(path)[1].lower())
            if full and ctype:
                rel = os.path.relpath(full, g.root).replace(os.sep, "/")
                with open(full, "rb") as f:
                    self.send(200, f.read(), ctype, headers=self.hold(g, rel) or [("Cache-Control", "max-age=86400")])
            else:
                self.send(404, "not found\n", "text/plain", headers=self.hold(g))
        else:
            self.send(404, pages.missing(ctx, g, path), headers=self.hold(g))

    # -- the API ---------------------------------------------------------------------------------------------

    def vault_sites(self, query, one=False):
        """The vaults a request asks for: ?vault=name, a comma list or `all`; omitted = the default vault only, so a
        client that never sends it sees exactly the API it always saw. ValueError (-> 400) for an unknown name."""
        raw = ",".join(query.get("vault", [])).strip()
        if not raw:
            return [state.default]
        names = list(dict.fromkeys(n.strip().lower() for n in raw.split(",") if n.strip()))
        if not names:                                   # vault=, says nothing: the default vault, as when omitted
            return [state.default]
        if names == ["all"]:
            if one:
                raise ValueError("this endpoint takes one vault, not all")
            return list(state.sites)
        unknown = [n for n in names if n not in state.by_name]
        if unknown:
            raise ValueError("no such vault: %s" % ", ".join(unknown))
        if one and len(names) > 1:
            raise ValueError("this endpoint takes one vault")
        return [state.by_name[n] for n in names]

    def site_of_url(self, url):
        """(site, note path) of a Kura note URL: /n/<slug> is the default vault, /v/<name>/n/<slug> another one."""
        u = urlsplit(url)
        path = unquote(u.path)
        if path.startswith("/v/"):
            name, _, rest = path[3:].partition("/")
            site = state.by_name.get(name)
            return (site, rest[2:]) if site and rest.startswith("n/") else (None, "")
        return (state.default, path[3:]) if path.startswith("/n/") else (None, "")

    def note_in(self, site, path):
        if not site or not path:
            return None
        n = site.notes.get(path) or site.get(path[:-3] if path.endswith(".md") else path)
        return n if n and not n.rel.startswith(pages.HIDDEN) else None

    def api(self, path, query):
        base = self.base()
        limit, offset = ints(query, "limit", 20, MAX_LIMIT), ints(query, "offset", 0, 10 ** 6)
        if path == "/api/vaults":
            counts = state.index.counts
            return self.send_json(200, {"vaults": [
                {"name": x.name, "title": x.title, "default": x.default, "private": x.private, "obsidian": x.obsidian,
                 "notes": counts.get(x.name, 0), "head": x.head, "synced_at": x.synced_at, "error": x.error}
                for x in state.sites]})
        if path == "/api/offline":      # the service worker's pins: default-vault notes with offline: true, fetched ahead
            g = state.default
            return self.send_json(200, {"urls": ["/n/" + quote(n.slug) for n in pages.visible(g) if pages.pinned(g, n)]})
        try:
            asked = self.vault_sites(query, one=path in ("/api/note", "/api/tags", "/api/folders", "/api/links"))
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        if any(not x.ready for x in asked):
            return self.send_json(503, {"error": "a requested vault is still being read; try again shortly"})
        names = [x.name for x in asked]
        g = asked[0]
        if path == "/api/search":
            q = (query.get("q") or [""])[0][:500]
            sort = (query.get("sort") or ["relevance"])[0]
            if sort not in ("relevance", "changed"):
                return self.send_json(400, {"error": "sort is relevance or changed"})
            try:
                total, rows, took = state.index.search(q, sort, limit, offset, (query.get("tag") or [""])[0],
                                                       (query.get("folder") or [""])[0], names)
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            results = [api.note_json(base, state.by_name[v], state.by_name[v].notes[rel], snip or "")
                       for v, rel, snip in rows if rel in state.by_name[v].notes]
            return self.send_json(200, {"total": total, "took_ms": took, "results": results})
        if path == "/api/notes":
            found, missing = [], []
            for v in multi(query, "paths"):
                n = self.note_in(g, v)
                (found.append(api.note_json(base, g, n)) if n else missing.append(v))
            for v in multi(query, "urls"):
                site, rel = self.site_of_url(v)         # a /v/… URL asks for that vault by itself
                n = self.note_in(site, rel)
                (found.append(api.note_json(base, site, n)) if n else missing.append(v))
            return self.send_json(200, {"notes": found, "missing": missing})
        if path == "/api/note":
            n = self.note_in(g, (query.get("path") or [""])[0])
            if not n:
                return self.send_json(404, {"error": "no such note"})
            out = api.note_json(base, g, n)
            out["html"] = api.sanitize(g.render(n, "", False, mode="all", prefix=g.prefix), base)
            out["markdown"] = n.text
            back = sorted((g.notes[r] for r in g.backlinks.get(n.rel, ()) if r in g.notes and not r.startswith(pages.HIDDEN)),
                          key=lambda x: x.title.lower())
            fwd = sorted((g.notes[r] for r in n.links if r in g.notes and not r.startswith(pages.HIDDEN)),
                         key=lambda x: x.title.lower())
            out["external_links"] = api.external_links(g, n, base)       # [] for a work vault
            out["backlinks"] = [api.link_json(base, x, g.prefix) for x in back]
            out["outlinks"] = [api.link_json(base, x, g.prefix) for x in fwd]
            return self.send_json(200, out)
        if path == "/api/links":                # a folder's external links in one call (Shiori's Save Links)
            if g.private:
                return self.send_json(400, {"error": "links are only served for the default vault"})
            folder = (query.get("folder") or [""])[0].strip().strip("/")
            if not folder:
                return self.send_json(400, {"error": "folder is required"})
            found = api.folder_links(g, folder, base, pages.visible(g))
            return self.send_json(200, {"total": len(found), "notes": [
                {"path": n.rel, "title": n.title, "url": api.note_url(base, n, g.prefix), "external_links": links}
                for n, links in found[offset:offset + limit]]})
        if path == "/api/recent":
            total, found = state.index.recent(limit, offset, names)
            return self.send_json(200, {"total": total, "results": [
                api.note_json(base, state.by_name[v], state.by_name[v].notes[r]) for v, r in found
                if r in state.by_name[v].notes]})
        if path == "/api/tags":
            counts = {}
            for n in pages.visible(g):
                for t in set(n.tags):
                    if not t.endswith("/"):
                        counts[t] = counts.get(t, 0) + 1
            return self.send_json(200, {"tags": [{"tag": t, "count": c} for t, c in sorted(counts.items())]})
        if path == "/api/folders":
            counts = {}
            for n in pages.visible(g):
                parts = n.rel.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    f = "/".join(parts[:i])
                    counts[f] = counts.get(f, 0) + 1
            return self.send_json(200, {"folders": [{"folder": f, "count": c} for f, c in sorted(counts.items())]})
        self.send_json(404, {"error": "no such endpoint"})

    def feed(self, query):
        """RSS of the default vault only: no vault parameter, so a feed or an OPML export can't carry a work note."""
        g, base = state.default, self.base()
        q, tag, folder = ((query.get(k) or [""])[0] for k in ("q", "tag", "folder"))
        if q or tag or folder:
            try:
                _, rows, _ = state.index.search(q, "changed", 50, 0, tag, folder, [g.name])
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            rels = [r for _, r, _ in rows]
        else:
            _, found = state.index.recent(50, 0, [g.name])
            rels = [r for _, r in found]
        notes = [(g.notes[r], api.changed(g, g.notes[r])) for r in rels if r in g.notes]
        title = "Kura" + ((": " + " ".join(x for x in (q, tag and "#" + tag, folder) if x)) if (q or tag or folder) else "")
        self.send(200, api.rss(base, title, notes), "application/rss+xml", headers=[("Cache-Control", "max-age=300")])


def main():
    drift = vk_verify.check()
    for p in drift:
        print("kura: vaultkit drift: %s" % p, flush=True)
    print("kura %s (vaultkit %s): repo %s, vaults %s, poll %ds, users %s, push %s%s" % (
        VERSION, vaultkit_version(), safe_url(REPO_URL) or REPO_DIR + " (as is)",
        ", ".join("%s%s" % (x.name, " (default)" if x.default else "") for x in state.sites), POLL,
        "anyone (KURA_AUTH=open)" if AUTH == "open" else ",".join(sorted(USERS)) or "NOBODY (set KURA_USERS)",
        ("to %s" % HISTER_URL) if state.push else ("off (needs KURA_PUBLIC_URL)" if HISTER_URL else "off"),
        (", settings from %s" % ENV_FILE) if ENV_FILE else ""), flush=True)
    print("kura: listening on %s:%d" % (BIND, PORT), flush=True)
    if AUTH == "open":
        print("kura: WARNING: KURA_AUTH=open: no identity check. Anyone who can reach %s:%d can read every note. "
              "Use it only on localhost or a trusted LAN." % (BIND, PORT), flush=True)
    threading.Thread(target=state.loop, daemon=True).start()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
