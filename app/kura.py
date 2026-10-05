"""Kura (蔵): every note in the vault, with working links, full-text search and a JSON API. Part of Machiya
(machiya-kobo/machiya: docs/services/kura.md, docs/contracts/kura-api.md).

Standalone: Kura keeps its own clone of the vault (vaultkit.Mirror, https/ssh/file, polled every KURA_POLL seconds),
its own index (vaultkit.Vault + search.Index, rebuilt when the commit changes), and a small SQLite file (KURA_DB) with
what it pushed into Hister (push.py, when KURA_HISTER_URL is set). Niwa, Konbini and Hister are optional URLs.
Every request needs a Tailscale-User-Login in KURA_USERS (`*` = anyone), except /api/status, which the monitoring
probes read; KURA_AUTH=open drops that check for localhost or a trusted LAN. Native installs (docs/install/bsd.md in
machiya) put the settings in a file: KURA_ENV_FILE or --env-file PATH, read before anything else.
"""
import ipaddress
import json
import os
import re
import sqlite3
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
from capped import Capped  # noqa: E402
import pages  # noqa: E402
import sites  # noqa: E402
import push  # noqa: E402
import search  # noqa: E402
import shell  # noqa: E402
from vaultkit import changelog  # noqa: E402
from vaultkit import histerauth  # noqa: E402
from vaultkit import websafe  # noqa: E402
from vaultkit import identity  # noqa: E402
from vaultkit import signin  # noqa: E402
from vaultkit import verify as vk_verify  # noqa: E402

VERSION = "0.9.1"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CHANGELOG = os.path.join(APP_DIR, "CHANGELOG.md")      # GET /api/changelog; inside app/, so the image's COPY carries it
PORT = int(os.environ.get("KURA_PORT", "8080"))
REPO_URL = os.environ.get("KURA_REPO_URL", "").strip()
REPO_DIR = os.environ.get("KURA_REPO_DIR", "/data/repo")
SUBDIR = os.environ.get("KURA_REPO_SUBDIR", "").strip("/")      # "" = the vault is the repo root
BRANCH = os.environ.get("KURA_REPO_BRANCH", "")
TOKEN_FILE = os.environ.get("KURA_REPO_TOKEN_FILE", "")
REPO_USER = os.environ.get("KURA_REPO_USER", "") or "token"
POLL = max(10, int(os.environ.get("KURA_POLL", "60")))
USERS = set(filter(None, (u.strip() for u in os.environ.get("KURA_USERS", "").split(","))))


def auth_mode(value, identity_file=""):
    """KURA_AUTH: "tailscale" (the default: Tailscale-User-Login must be in KURA_USERS, or in the identity file),
    "open" (no identity check, for localhost or a trusted LAN), with an identity file "header" (a trusted proxy's
    login header, KURA_AUTH_HEADER), or without one "hister" (Hister's users are the sign-in, through the hister-login
    helper: vaultkit.histerauth). Anything else refuses to start rather than guess."""
    value = (value or "tailscale").strip().lower()
    if value == "hister" and identity_file:
        raise SystemExit("kura: KURA_AUTH=hister doesn't combine with MACHIYA_IDENTITY_FILE yet: unset one of them")
    allowed = ("tailscale", "open", "header") if identity_file else ("tailscale", "open", "hister")
    if value not in allowed:
        raise SystemExit("kura: KURA_AUTH must be %s, not %r" % (" or ".join(allowed), value))
    return value


AUTH = auth_mode(os.environ.get("KURA_AUTH"), os.environ.get("MACHIYA_IDENTITY_FILE", "").strip())
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
if AUTH == "tailscale" and not os.environ.get("MACHIYA_IDENTITY_FILE", "").strip():     # the KURA_USERS gate trusts the header
    try:
        identity.check_bind("tailscale", BIND, os.environ.get("KURA_BIND_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes", "on"))
    except identity.IdentityError as err:
        raise SystemExit("kura: %s" % str(err).replace("<ROOM>_BIND_BEHIND_PROXY", "KURA_BIND_BEHIND_PROXY"))
try:        # Machiya's identity file (MACHIYA_IDENTITY_FILE); None without one: the KURA_USERS gate, as before
    IDENTITY = identity.load_for("kura", os.environ, bind=BIND, secure=not PUBLIC_URL.startswith("http://"))
except identity.IdentityError as err:
    raise SystemExit("kura: identity: %s" % err)


def hister_auth(env, bind, secure):
    """KURA_AUTH=hister: the sign-in helper's check (vaultkit.histerauth), else None. Kura has NO Tailscale fallback and
    no grace period: with the helper or Hister unavailable every page and call is a 503. KURA_AUTH_FALLBACK defaults
    to (and may only be) "none"; KURA_PUBLIC_URL, KURA_AUTH_URL, KURA_AUTH_SIGNIN_URL and KURA_HISTER_USERS are
    required (histerauth.load_for refuses to start without them)."""
    if AUTH != "hister":
        return None
    env = dict(env)
    if (env.get("KURA_AUTH_FALLBACK") or "none").strip().lower() != "none":
        raise SystemExit("kura: KURA_AUTH_FALLBACK must be none: Kura has no Tailscale fallback in hister mode")
    env["KURA_AUTH_FALLBACK"] = "none"
    try:
        return histerauth.load_for("kura", env, bind=bind, secure=secure)
    except identity.IdentityError as err:
        raise SystemExit("kura: hister sign-in: %s" % err)


HISTER = hister_auth(os.environ, BIND, not PUBLIC_URL.startswith("http://"))
shell.SIGNIN = HISTER is not None       # pages carry the machiya-signin meta (a Sign Out row, a 401 goes to sign-in)


def host_name(value):
    """A Host header or a configured name, compared the way a browser means it: lowercase, no port, no trailing dot,
    no IPv6 brackets. "" when it can't be read."""
    try:
        host = urlsplit("//" + (value or "").strip()).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".")


# KURA_AUTH=open answers only to these names in Host, plus any IP literal: a page on another site whose name is pointed
# at this machine (DNS rebinding) arrives with that site's name in Host, and would otherwise read every note.
ALLOWED_HOSTS = {"localhost", (urlsplit(PUBLIC_URL).hostname or "").rstrip(".")} | {
    host_name(h) for h in os.environ.get("KURA_ALLOWED_HOSTS", "").split(",")}
ALLOWED_HOSTS.discard("")


def host_allowed(value, allowed=None):
    host = host_name(value)
    if not host:
        return False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])           # an IP literal (a v6 zone id aside) is never rebound
        return True
    except ValueError:
        return host in (ALLOWED_HOSTS if allowed is None else allowed)
api.PUBLIC_URL = PUBLIC_URL
api.SHIORI_LINKS = os.environ.get("KURA_SHIORI_LINKS", "").strip().lower() in ("1", "true", "yes", "on")
HISTER_URL = os.environ.get("KURA_HISTER_URL", "").rstrip("/")         # set: push every note into Hister
HISTER_TOKEN_FILE = os.environ.get("KURA_HISTER_TOKEN_FILE", "")       # the owner's Hister token (X-Access-Token); unset: none
DB = os.environ.get("KURA_DB", "/data/kura.sqlite3")
# Per-user preferences (/api/prefs): their own file next to KURA_DB, so clearing what was pushed
# into Hister never clears anyone's preferences. Made on first use.
PREFS_DB = os.path.join(os.path.dirname(os.path.abspath(DB)), "prefs.sqlite3")
# The origins a same-origin check accepts for sign-in, sign-out and a prefs PUT made with a cookie: KURA_PUBLIC_URL.
# Without it the request's own Host counts, over https only; over plain http sign-in then always refuses (signin.py).
ORIGINS = (PUBLIC_URL,) if PUBLIC_URL else ()
# Without an identity file, preferences belong to the one person the old gate admits (identity.ambient): a prefs PUT is
# same-origin with KURA_PUBLIC_URL, else with the request's Host over https (Tailscale serve), or http in open mode.
SECURE = PUBLIC_URL.startswith("https://") if PUBLIC_URL else AUTH != "open"
SHARED_UI = ("/static/machiya.css", "/static/machiya.js", "/static/machiya-sw.js")   # vaultkit's, before the gate
SIGNIN_LIMITS = {"/signin": signin.MAX_FORM, "/signout": signin.MAX_FORM, "/api/pair": signin.MAX_PAIR}
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
    day). One `git log` for every vault of a checkout; a file counts for the longest matching subdir. NUL-separated
    and unquoted (`core.quotePath=false`), so a name like 町家.md or "café notes.md" keeps its date."""
    subdirs = list(subdirs)
    out = run("-c", "core.quotePath=false", "log", "-z", "--format=%x01%at", "--name-only", "--", *[d or "." for d in subdirs])
    times = {d: {} for d in subdirs}
    current = None
    for item in out.split("\0"):
        item = item.lstrip("\n")                       # git puts a newline before each commit's first name
        if item.startswith("\x01"):
            current = int(item[1:]) if item[1:].isdigit() else None
        elif item and current:
            best = max((d for d in subdirs if not d or item.startswith(d + "/")), key=len, default=None)
            if best is not None:
                times[best].setdefault(item[len(best) + 1:] if best else item, current)
    return times


def local_path(path):
    """A redirect target that stays on this host: a local path (percent-encoded, with its query), never //host, /\\host or
    a control character (vaultkit.websafe.location). Anything else is /."""
    return websafe.location(path)


def footer_status(site=None):
    """The footer's status line (shell.footer) and About's Vault row: "synced abc1234 3 min ago · 42 notes". Another
    vault's line starts with its title."""
    site = site or state.default
    label = "" if site.default else (site.title + " · ")
    if not site.ready:
        return {"text": label + "starting: cloning the vault", "state": "down"}
    age = int(time.time()) - (site.synced_at or 0)
    when = "just now" if age < 60 else "%d min ago" % (age // 60) if age < 3600 else "%d h ago" % (age // 3600)
    count = state.index.counts.get(site.name, 0)
    text = "%ssynced %s %s · %d %s" % (label, site.head[:7], when, count, "note" if count == 1 else "notes")
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
        # It sends the default vault and the shared ones: private vaults never reach Hister (push.py refuses them).
        self.push = push.Push(HISTER_URL, DB, self.default.subdir, PUBLIC_URL, HISTER_TOKEN_FILE) if HISTER_URL and PUBLIC_URL else None

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
                if not site.private and self.push:
                    self.push.pending = True
            site.head, site.synced_at, site.error = head, int(time.time()), None
        self.loop_error = None
        if self.push and self.push.due() and self.default.ready:
            self.push.run([(x, pages.visible(x), x.head) for x in self.sites if not x.private and x.ready],
                          [x.name for x in self.sites if x.shared])

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


_prefs = None
_prefs_lock = threading.Lock()


def prefs_store():
    """The preferences file (signin.Prefs at PREFS_DB), opened on first use; OSError or sqlite3.Error when it can't be."""
    global _prefs
    with _prefs_lock:
        if _prefs is None or _prefs.path != PREFS_DB:
            _prefs = signin.Prefs(PREFS_DB)
        return _prefs


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


class CappedHTTPServer(Capped, ThreadingHTTPServer):
    """At most 64 connections at once, none open longer than two minutes (capped.py): a slowloris client can't hold
    every thread."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):      # a client that left, or one we cut off
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    timeout = 30                    # seconds a client may stay silent: one that never sends doesn't hold a thread for good
    server_version = "kura/" + VERSION

    def log_message(self, fmt, *args):
        """One log line: who and the request without its query (a private vault's search terms stay out of the log).
        Works for a request that never parsed (a garbage request line has no path or headers)."""
        if getattr(self, "path", "") == "/api/status":              # the healthcheck and probes, every minute
            return
        try:
            line = fmt % args
        except (TypeError, ValueError):
            line = str(fmt)
        sys.stderr.write("%s %s\n" % (self.actor() or "-", re.sub(r"\?\S*", "", line)[:300]))

    def actor(self):
        """Who is asking, for the log. Open mode: always "local", since nothing vouches for the header there."""
        if HISTER is not None:
            res = getattr(self, "_hres", None)          # only when the gate already asked: a log line never calls the helper
            return (res.actor if res is not None else "") or "-"
        headers = getattr(self, "headers", None)
        if headers is None:                             # the request line itself was refused: nobody was identified
            return "-"
        if IDENTITY is not None:
            who = self.who()
            return "%s(%s)" % (who.principal.name, who.principal.via) if who else "-"
        if AUTH == "open":
            return "local"
        return headers.get("Tailscale-User-Login", "")

    def who(self):
        """The identity file's answer for this request (vaultkit.identity), worked out once."""
        if getattr(self, "_who", None) is None:
            self._who = IDENTITY.resolve(self.headers, self.client_address[0] if self.client_address else "")
        return self._who

    def hister(self):
        """The Hister sign-in's answer for this request (histerauth.Result), worked out once."""
        if getattr(self, "_hres", None) is None:
            self._hres = HISTER.resolve(self.headers, is_page=self.hister_page(), path=self.path)
        return self._hres

    def hister_page(self):
        """A page a browser opens, which a signed-out answer redirects to the sign-in. A request that names its
        credential itself (X-Access-Token or Authorization: a browser never adds either) is a client: a wrong one is a
        401, never a redirect, for pages too."""
        return self.is_page() and not (self.headers.get("X-Access-Token") or self.headers.get("Authorization"))

    def allowed(self):
        if HISTER is not None:
            return bool(self.hister())
        if IDENTITY is not None:
            who = self.who()
            return bool(who) and who.principal.can("kura", "read")
        if AUTH == "open":
            return True
        return "*" in USERS or self.headers.get("Tailscale-User-Login", "") in USERS

    def owner(self):
        """The full /api/status (repo URL, folder, error texts): the owner only. Without an identity file, everyone the
        gate admits is the owner, as before."""
        if HISTER is not None:      # a probe sends no credential: no need to ask the helper about nobody
            if not any(self.headers.get(h) for h in ("Authorization", "X-Access-Token", "Cookie")):
                return False
            return bool(self.hister())
        if IDENTITY is not None:
            who = self.who()
            return bool(who) and who.principal.owner
        return self.allowed()

    def principal(self):
        """Whose preferences these are: the identity file's principal, else (after the old gate) identity.ambient's
        one owner for Tailscale or open mode; None for nobody."""
        if HISTER is not None:
            return self.hister().principal
        if IDENTITY is not None:
            who = self.who()
            return who.principal if who else None
        return identity.ambient(AUTH, self.headers) if self.allowed() else None

    def visible(self):
        """The vaults this request may read: every vault without an identity file, else the principal's grant
        (vaultkit.identity Principal.vaults: "*", or vault names plus "default" and "shared"). A private vault is
        readable only when named."""
        if IDENTITY is None:
            return list(state.sites)
        scope = self.who().principal.vaults("kura")
        if scope == "*":
            return list(state.sites)
        return [x for x in state.sites if x.name in scope or (x.default and "default" in scope)
                or (x.shared and "shared" in scope)]

    def refuse(self):
        if HISTER is not None:      # signed out: a page goes to the helper's sign-in, an API call gets 401 JSON; 403; 503
            return self.reply(*HISTER.respond(self.hister(), self.hister_page(), self.ctx()))
        if IDENTITY is not None:
            who = self.who()
            status = who.status if not who else 403
            if status == 401 and IDENTITY.signin and self.is_page():
                return self.send(401, signin.needed(shell.ROOM, self.path, self.ctx()), headers=[NO_STORE])
            body = (who.error if not who else "not allowed in kura") + "\n"
            return self.send(status, body, "text/plain", headers=[NO_STORE])
        return self.send(403, "forbidden\n", "text/plain")

    def is_page(self):
        """A page a browser opens (a 401 there links to /signin), not the API, a feed, a script or a stylesheet."""
        path = urlsplit(self.path).path
        return not (path.startswith(("/api/", "/static/")) or path.endswith((".xml", ".js", ".webmanifest")))

    def reply(self, status, headers, body):
        """A vaultkit.signin answer, (status, [(header, value)], bytes), as it is: its own Set-Cookie headers and no
        others (a renewed session added to a sign-out would undo it)."""
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        if dict(headers).get("Content-Type", "").startswith("text/html"):
            self.send_header("Content-Security-Policy", shell.house.CSP)    # beside signin's own frame-ancestors 'none'
        else:
            have = {k.lower() for k, _ in headers}
            for k, v in websafe.base_headers():
                if k.lower() not in have:
                    self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def too_large(self):
        self.close_connection = True
        return self.send(413, "request body too large\n", "text/plain", headers=[NO_STORE])

    def do_POST(self):
        """Kura is read-only: the built-in sign-in, sign-out and Shiori's pairing are the only POSTs, before the gate
        (they are how a browser or a device gets past it), and only with an identity file. Anything else: 405."""
        if AUTH == "open" and not host_allowed(self.headers.get("Host")):
            self.close_connection = True
            return self.send(403, "forbidden: unknown Host (KURA_ALLOWED_HOSTS)\n", "text/plain")
        path = unquote(urlsplit(self.path).path)
        if path not in SIGNIN_LIMITS:
            self.close_connection = True
            return self.send(405, "Kura is read-only\n", "text/plain", headers=[("Allow", "GET, HEAD")])
        if HISTER is not None and path == "/signout":
            return self.hister_signout()
        if IDENTITY is None:
            self.close_connection = True
            return self.send(404, "not found\n", "text/plain")
        body = signin.read_body(self.headers, self.rfile, SIGNIN_LIMITS[path])
        if body is None:
            return self.too_large()
        client = self.client_address[0] if self.client_address else ""
        if path == "/signin":
            return self.reply(*signin.handle_post(IDENTITY, self.headers, body, client, origins=ORIGINS))
        if path == "/signout":
            return self.reply(*signin.handle_signout(IDENTITY, self.headers, origins=ORIGINS))
        return self.reply(*signin.handle_pair(IDENTITY, self.headers, body, client))

    def hister_signout(self):
        """POST /signout in hister mode (the Rooms menu's Sign Out): same-origin only; the helper ends the Hister session
        and every id on it, this room clears its cookie, and the browser lands on /signed-out."""
        if signin.read_body(self.headers, self.rfile, signin.MAX_FORM) is None:
            return self.too_large()
        if not signin.same_origin(self.headers, HISTER.secure, ORIGINS):
            return self.send(403, "cross-site sign-out refused\n", "text/plain", headers=[NO_STORE])
        ended, cookies = HISTER.signout(self.headers)
        print("kura: sign-out%s" % ("" if ended else " (the sign-in service didn't answer; this room's cookie is cleared)"),
              file=sys.stderr, flush=True)
        return self.reply(303, [("Location", "/signed-out"), NO_STORE] + [("Set-Cookie", c) for c in cookies], b"")

    def do_PUT(self):
        """PUT /api/prefs only (a principal's own preferences, behind the gate); anything else: 405."""
        if AUTH == "open" and not host_allowed(self.headers.get("Host")):
            self.close_connection = True
            return self.send(403, "forbidden: unknown Host (KURA_ALLOWED_HOSTS)\n", "text/plain")
        path = unquote(urlsplit(self.path).path)
        if path != "/api/prefs":
            self.close_connection = True
            return self.send(405, "Kura is read-only\n", "text/plain", headers=[("Allow", "GET, HEAD")])
        if not self.allowed():
            self.close_connection = True
            return self.refuse()
        body = signin.read_body(self.headers, self.rfile, signin.MAX_PREFS)
        if body is None:
            return self.too_large()
        return self.prefs(body)

    def prefs(self, body=b""):
        """GET/PUT /api/prefs as the resolved principal; a renewed session cookie rides along. In hister mode with the
        helper they are the account's (vaultkit 0.21, docs/contracts/prefs.md): forwarded with the caller's own
        credential. Otherwise (no helper, or not hister mode) this room's own store answers."""
        if HISTER is not None:
            fwd = HISTER.forward_prefs(self.hister(), "GET" if self.command == "HEAD" else self.command, self.headers,
                                       body, self.origins())
            if fwd is not None:
                status, headers, data = fwd
                return self.reply(status, headers + [("Set-Cookie", c) for c in self.hister().cookies], data)
        try:
            store = prefs_store()
        except (OSError, sqlite3.Error) as err:
            print("kura: preferences: %s" % err, file=sys.stderr, flush=True)
            out = (503, list(signin.JSON_HEADERS), b'{"error": "preferences unavailable"}')
        else:
            out = signin.handle_prefs(store, self.principal(), self.command, self.headers, body,
                                      IDENTITY.secure if IDENTITY is not None else SECURE, origins=self.origins())
        status, headers, data = out
        cookies = self.hister().cookies if HISTER is not None else self.who().cookies if IDENTITY is not None else ()
        return self.reply(status, headers + [("Set-Cookie", c) for c in cookies], data)

    def origins(self):
        """Where a cookie-borne prefs PUT may come from: KURA_PUBLIC_URL; else, in open mode with no identity file, this
        request's own Host, which host_allowed() already checked (an IP, localhost or a listed name, never a name a
        stranger's page pointed here), so a prefs PUT works over plain http on localhost. Plus Shiori's address in
        MACHIYA_ROOMS: the hosted Shiori reaches Kura's /api/prefs at /kura/ on its own origin (sign-in and sign-out
        never take this list: they stay Kura's own)."""
        own = ORIGINS
        if not own and IDENTITY is None and AUTH == "open":
            host = (self.headers.get("Host") or "").strip().lower()
            own = ("http://" + host, "https://" + host) if host_allowed(host) else ()
        shiori = shell.house.rooms().get("shiori", "")
        return tuple(own) + ((shiori,) if shiori.startswith(("https://", "http://")) and own else ())

    def base(self):
        return PUBLIC_URL or "https://%s" % (self.headers.get("Host") or "localhost")

    def ctx(self):
        """theme, text size, previewPane (machiya.js writes them as cookies). A browser with none yet is drawn in the
        signed-in account's settings (histerauth's check carries them)."""
        res = getattr(self, "_hres", None) if HISTER is not None else None
        return shell.prefs(self.headers.get("Cookie"), account=res.prefs if res else None)

    def send(self, status, body, ctype="text/html", headers=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        if isinstance(body, str):
            ctype += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        if ctype.startswith("text/html"):
            for k, v in shell.house.security_headers():    # a note's HTML is cleaned; this is the second wall
                self.send_header(k, v)
        else:                                               # JSON, text, CSS, images, redirects (vaultkit.websafe)
            have = {k.lower() for k, _ in headers}
            for k, v in websafe.base_headers():
                if k.lower() not in have:
                    self.send_header(k, v)
        res = getattr(self, "_hres", None)
        if res is None:
            res = getattr(self, "_who", None)
        for c in (res.cookies if res is not None else ()):
            self.send_header("Set-Cookie", c)          # a renewed session, or a bad one cleared
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, status, obj, cache="no-store"):
        """JSON answers are never kept by a browser or a client's URL cache (a private vault's notes, an Archive/ note, a
        search): no-store, except what is public by design (/api/status, /api/offline's list) which may be revalidated."""
        self.send(status, json.dumps(obj, ensure_ascii=False, indent=1), "application/json",
                  headers=[("Cache-Control", cache)])

    def do_HEAD(self):
        self.do_GET()

    def changelog(self):
        """GET/HEAD /api/changelog: CHANGELOG.md's first 64 KiB (vaultkit.changelog), ETag/304, 404 without the file."""
        status, body, headers = changelog.handle(CHANGELOG, self.headers)
        ctype = next(v for k, v in headers if k == "Content-Type")
        return self.send(status, body, ctype, headers=[(k, v) for k, v in headers if k != "Content-Type"])

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
        if owner:       # a private vault shows only its error here (/api/vaults, owner-gated, has the head and the count)
            out["vaults"] = {x.name: {"error": err(x.error, "sync failed")} if x.private else
                             {"head": x.head, "synced_at": x.synced_at, "notes": state.index.counts.get(x.name, 0),
                              "error": err(x.error, "sync failed")} for x in state.sites}
        else:           # no vault names for anyone not signed in, just a count (a failing vault still trips "error")
            out["vault_count"] = len(state.sites)
        out["push"], out["auth"] = push, AUTH
        return out

    def do_GET(self):
        if AUTH == "open" and not host_allowed(self.headers.get("Host")):
            return self.send(403, "forbidden: unknown Host (KURA_ALLOWED_HOSTS)\n", "text/plain")
        url = urlsplit(self.path)
        path, query = unquote(url.path), parse_qs(url.query)
        if path == "/api/status":
            return self.send_json(200, self.status(self.owner()), cache="no-cache")
        if path == "/api/changelog":                # the landing page's "recent deploys": open, like /api/status
            return self.changelog()
        if path == "/signin" and IDENTITY is not None:      # the form, before the gate (404 when sign-in is off)
            return self.reply(*signin.handle_get(IDENTITY, self.headers, url.query))
        if HISTER is not None and path == "/signed-out":    # where a sign-out lands: no notes, no vault names
            return self.send(200, shell.signed_out(self.ctx()), headers=[NO_STORE])
        if (HISTER is not None or (IDENTITY is not None and IDENTITY.signin)) and (
                path in SHARED_UI or path.startswith("/static/icons/")):
            return self.static(path[8:], query)          # the sign-in page's stylesheet and icons: vendored, no notes
        if not self.allowed():
            return self.refuse()
        if path == "/api/prefs":
            return self.prefs()
        p = self.principal()
        shell.view.prefs_url = "/api/prefs" if p is not None else ""
        shell.view.who = p.name if IDENTITY is not None and p is not None else ""
        self.sites = self.visible()
        self.names = {x.name for x in self.sites}
        shell.view.sites = self.sites               # this request's vault switch and "All Vaults"
        ctx = self.ctx()
        if path == "/manifest.webmanifest":
            return self.send(200, json.dumps(shell.manifest(ctx.theme, self.headers, ctx.palette), indent=1), "application/manifest+json",
                             headers=[("Cache-Control", "no-cache"), ("Vary", shell.house.MANIFEST_VARY)])
        if path == "/sw.js":
            return self.send(200, shell.service_worker(), "text/javascript", headers=[("Cache-Control", "no-cache")])
        if path == "/offline":
            return self.send(200, shell.offline(ctx))
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
            return self.send(302, "", "text/plain", headers=[("Location", local_path(ref.path))] + cookies)
        if path == "/settings":
            p = self.principal()
            if HISTER is not None:
                prefs_state = HISTER.prefs_state(self.hister())
                account = p.name if p is not None and p.via == "hister" else ""
            else:
                prefs_state = "room" if shell.view.prefs_url else "standalone"
                who = self.who() if IDENTITY is not None else None
                account = who.principal.name if who and who.principal.via == "session" else ""
            return self.send(200, shell.settings(ctx, VERSION, footer_status()["text"], vaultkit_version(), account,
                                                 prefs_state, account),
                             headers=[("Cache-Control", "no-cache")])
        if not state.default.ready:
            if path.startswith("/api/") or path == "/feed.xml":
                return self.send_json(503, {"error": "Kura is still cloning the vault; try again shortly"})
            return self.send(503, shell.message(ctx, "Starting", "Kura is still cloning the vault. Try again in a "
                                                "minute."), headers=[("Retry-After", "30")])
        if path.startswith("/api/"):
            return self.api(path, query)
        if path == "/feed.xml":
            if state.default.name not in self.names:
                return self.send(404, "not found\n", "text/plain")
            return self.feed(query)
        site, rest = state.default, path
        if path.startswith("/v/"):                      # another vault: /v/<name>/n/…, /v/<name>/f/…, …
            name, _, tail = path[3:].partition("/")
            site, rest = state.by_name.get(name), "/" + tail
            if site is not None and site.name not in self.names:
                site = None                             # a vault this principal may not read is one that isn't there
            if site is None:
                return self.send(404, pages.missing(ctx, state.default, path), headers=[NO_STORE])
            if site.default:                            # /v/<default>/n/X is /n/X
                return self.send(301, "", "text/plain", headers=[("Location", local_path(quote(rest, safe="/") + ("?" + url.query if url.query else "")))])
            if not site.ready:
                return self.send(503, shell.message(ctx, "Starting", "This vault is still being read. Try again in a "
                                                    "minute.", site), headers=[("Retry-After", "30"), NO_STORE])
        if site.name not in self.names:                # the default vault, not granted (a grant naming others only)
            return self.send(404, "not found\n", "text/plain", headers=[NO_STORE])
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
            names = [x.name for x in self.sites] if everywhere else [g.name]
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
        elif path == "/feed.xml" and not g.private:      # a shared vault's own feed
            self.feed(query, g)
        elif path.startswith("/a/"):
            full = g.asset_path(path[3:])
            if full and os.path.splitext(path)[1].lower() in IMAGE_TYPES:
                rel = os.path.relpath(full, g.root).replace(os.sep, "/")
                # a vault file is never a document of Kura's own origin: sandboxed (an SVG runs no script), never sniffed
                safe = websafe.asset_headers(rel)
                ctype = next(v for k, v in safe if k == "Content-Type")
                with open(full, "rb") as f:
                    self.send(200, f.read(), ctype, headers=(self.hold(g, rel) or [("Cache-Control", "max-age=86400")])
                              + [(k, v) for k, v in safe if k != "Content-Type"])
            else:
                self.send(404, "not found\n", "text/plain", headers=self.hold(g))
        else:
            self.send(404, pages.missing(ctx, g, path), headers=self.hold(g))

    # -- the API ---------------------------------------------------------------------------------------------

    def vault_sites(self, query, one=False):
        """The vaults a request asks for: ?vault=name, a comma list or `all`; omitted = the default vault only, so a
        client that never sends it sees exactly the API it always saw. ValueError (-> 400) for an unknown name; a vault
        the principal may not read is unknown too (its name doesn't leak), and `all` means all it may read."""
        raw = ",".join(query.get("vault", [])).strip()
        names = list(dict.fromkeys(n.strip().lower() for n in raw.split(",") if n.strip()))
        if not names:                                   # omitted, or vault=, (says nothing): the default vault
            if state.default.name not in self.names:
                raise ValueError("no such vault: %s" % state.default.name)
            return [state.default]
        if names == ["all"]:
            if one:
                raise ValueError("this endpoint takes one vault, not all")
            return list(self.sites)
        unknown = [n for n in names if n not in state.by_name or n not in self.names]
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
            ok = site and site.name in self.names and rest.startswith("n/")
            return (site, rest[2:]) if ok else (None, "")
        ok = path.startswith("/n/") and state.default.name in self.names
        return (state.default, path[3:]) if ok else (None, "")

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
                for x in self.sites]})
        if path == "/api/offline":      # the service worker's pins: notes with offline: true, fetched ahead (never private)
            return self.send_json(200, {"urls": [g.prefix + "/n/" + quote(n.slug) for g in self.sites
                                                 if not g.private and g.ready for n in pages.visible(g) if pages.pinned(g, n)]},
                                  cache="no-cache")
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
            out["external_links"] = api.external_links(g, n, base)       # [] for a private vault
            out["backlinks"] = [api.link_json(base, x, g.prefix) for x in back]
            out["outlinks"] = [api.link_json(base, x, g.prefix) for x in fwd]
            return self.send_json(200, out)
        if path == "/api/links":                # a folder's external links in one call (Shiori's Save Links)
            if g.private:
                return self.send_json(400, {"error": "links are not served for a private vault"})
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

    def feed(self, query, g=None):
        """RSS of one vault: /feed.xml the default, /v/<name>/feed.xml a shared one. No vault parameter, and never a
        private vault, so a feed or an OPML export can't carry a work note. Notes under Archive/ stay out too."""
        g, base = g or state.default, self.base()
        if g.private:
            raise ValueError("no feed for a private vault")
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
        notes = [(g.notes[r], api.changed(g, g.notes[r])) for r in rels if r in g.notes and not pages.never_stored(r)]
        title = ("Kura" if g.default else "Kura · " + g.title) + (
            (": " + " ".join(x for x in (q, tag and "#" + tag, folder) if x)) if (q or tag or folder) else "")
        self.send(200, api.rss(base, title, notes, g.prefix), "application/rss+xml",
                  headers=[("Cache-Control", "max-age=300")])


def main():
    drift = vk_verify.check()
    for p in drift:
        print("kura: vaultkit drift: %s" % p, flush=True)
    repo = "" if os.environ.get("KURA_VAULTS", "").strip() else \
        "repo %s, " % (safe_url(REPO_URL) or REPO_DIR + " (as is)")      # KURA_VAULTS names each vault's own source
    print("kura %s (vaultkit %s): %svaults %s, poll %ds, users %s, push %s%s" % (
        VERSION, vaultkit_version(), repo,
        ", ".join("%s%s" % (x.name, " (default)" if x.default else "") for x in state.sites), POLL,
        "from %s" % IDENTITY.path if IDENTITY is not None else
        "Hister sign-in (%s)" % ", ".join(sorted(HISTER.users)) if HISTER is not None else
        "anyone (KURA_AUTH=open)" if AUTH == "open" else ",".join(sorted(USERS)) or "NOBODY (set KURA_USERS)",
        ("to %s%s" % (HISTER_URL, " with a token" if HISTER_TOKEN_FILE else "")) if state.push else ("off (needs KURA_PUBLIC_URL)" if HISTER_URL else "off"),
        (", settings from %s" % ENV_FILE) if ENV_FILE else ""), flush=True)
    print("kura: listening on %s:%d" % (BIND, PORT), flush=True)
    if AUTH == "open":
        print("kura: WARNING: KURA_AUTH=open: no identity check. Anyone who can reach %s:%d can read every note. "
              "Use it only on localhost or a trusted LAN." % (BIND, PORT), flush=True)
    if IDENTITY is not None and IDENTITY.signin and not PUBLIC_URL:
        print("kura: KURA_SIGNIN without KURA_PUBLIC_URL: sign-in works only over https (the request's own Host); "
              "served over plain http, set KURA_PUBLIC_URL=http://<this address> or every sign-in is refused",
              flush=True)
    threading.Thread(target=state.loop, daemon=True).start()
    server = CappedHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
