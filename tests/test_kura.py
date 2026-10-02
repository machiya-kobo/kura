"""Kura's tests: search syntax, the index, the sanitizer, and the API + reader over HTTP against a fixture vault.

Run with `python3 -m unittest discover -s tests` (needs markdown and pyyaml), or in the image:
  docker build -t kura-test app && docker run --rm --user 1000:1000 -v "$PWD":/k -w /k --entrypoint python3 kura-test -m unittest discover -s tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()
REPO = os.path.join(TMP, "vault")
NOTES = {
    "Projects/Lantern.md": "---\ntitle: Lantern\ndate: 2026-01-01\ntags: [type/project, area/tools, topic/hobby]\n"
                           "project: lantern\nboard: wip\npublish: true\nsummary: A tiny example project\n---\n"
                           "# Lantern\n\nLinks to [[Paper lanterns]] and [[MOC/Crafts|crafts]].\n\n"
                           "<script>alert(1)</script><iframe src=\"https://evil\"></iframe>\n"
                           "<a href=\"javascript:alert(2)\" onclick=\"x()\">bad</a> ![[pic.png]]\n\n"
                           "Docs: [Lantern docs](https://example.com/lantern) and <https://example.org/auto>, bare "
                           "https://example.net/bare. Again [the same](https://example.com/lantern), raw "
                           "<a href=\"https://example.com/raw\">raw <b>html</b></a>, ours [n](https://kura.test/n/X), "
                           "[card](https://konbini.test/p/lantern), [mail](mailto:a@b.c) and `https://code.example/inline`.\n\n"
                           "```\nhttps://code.example/fence [x](https://code.example/fenced-link)\n```\n",
    "Notes/Paper lanterns.md": "---\ntitle: Paper lanterns\ndate: 2026-01-15\ntags: [topic/hobby/paper]\n---\n"
                               "Café maps (地図) show collapsible paper lanterns on a bamboo frame. See [Wikipedia](https://en.wikipedia.org/wiki/Cat). Also [a gemlog](gemini://gem.example/lanterns) and gopher://goph.example/1/lanterns.\n",
    "MOC/Crafts.md": "---\ntags: [type/moc]\n---\nA map: [[Lantern]], [[Paper lanterns]].\n",
    "Notes/Tea brewing.md": "---\ntags: [topic/tea]\noffline: true\n---\nA teapot warms the cup first.\n",
    "Templates/Project.md": "---\ntitle: {{title}}\n---\nbamboo template [t](https://template.example/x)\n",
}
WORK = {                        # a work vault in the same repo (personal is the default vault)
    "Runbooks/Zebrafish deploy.md": "---\ntitle: Zebrafish deploy\ntags: [topic/client]\npublish: true\nproject: zf\n"
                                    "offline: true\ntype: project\n---\nZebrafish runbook. See [[Lantern]] and "
                                    "[[Paper lanterns]]. [Work link](https://work.example.com/secret) https://work.example.com/bare\n\n![[wpic.png]]\n",
    "Lantern.md": "---\ntags: 2025\n---\nThe work lantern: a different note with the same name.\n",   # a scalar tags:
}
TEAM = {                        # a shared vault in the same repo (SharedVaultTest adds it to its own Kura state)
    "Guides/Onboarding.md": "---\ntitle: Onboarding\ntags: [topic/team]\npublish: true\nproject: onboard\n"
                            "offline: true\ntype: project\n---\nOkapi onboarding. See [[Lantern]]. "
                            "[Team wiki](https://team.example.com/wiki) https://team.example.com/bare\n\n![[tpic.png]]\n",
    "Lantern.md": "---\ntags: [topic/team]\n---\nThe team lantern.\n",
    "Archive/Old.md": "---\ntags: [topic/team]\noffline: true\n---\nAn okapi from long ago.\n",
}


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_vault():
    for sub, notes in (("personal", NOTES), ("work", WORK), ("team", TEAM)):
        for rel, text in notes.items():
            path = os.path.join(REPO, sub, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
    for rel in ("personal/pic.png", "work/wpic.png", "team/tpic.png"):
        with open(os.path.join(REPO, rel), "wb") as f:
            f.write(b"\x89PNG")
    sh("git", "init", "-q", "-b", "main", cwd=REPO)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=REPO)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=REPO)


make_vault()
os.environ.update(KURA_REPO_DIR=REPO, KURA_REPO_URL="", KURA_USERS="owner@test", KURA_PORT="0",
                  KURA_PUBLIC_URL="https://kura.test", KURA_KONBINI_URL="https://konbini.test", KURA_POLL="3600",
                  KURA_VAULTS="personal=%s#personal, work:Work Notes=%s#work" % (REPO, REPO))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import kura        # noqa: E402
import search      # noqa: E402
import sites       # noqa: E402
import api         # noqa: E402

kura.state.sync()
SERVER = kura.ThreadingHTTPServer(("127.0.0.1", 0), kura.Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % SERVER.server_address[1]


def get(path, user="owner@test"):
    req = urllib.request.Request(BASE + path, headers={"Tailscale-User-Login": user} if user else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def fetch(path, cookie="", headers=None):
    """(status, headers, body) with the owner's login and an optional Cookie header (and any other `headers`)."""
    h = {"Tailscale-User-Login": "owner@test"}
    if cookie:
        h["Cookie"] = cookie
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, headers=h)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def getj(path):
    status, body = get(path)
    return status, json.loads(body)


class ParseTest(unittest.TestCase):
    def test_parse(self):
        pos, neg, tags, folders, vaults = search.parse('bamboo "paper lanterns" -teapot shio* title:lan* tag:topic folder:Notes/')
        self.assertEqual(pos, ['"bamboo"', '"paper lanterns"', '"shio"*', 'title : "lan"*'])
        self.assertEqual(neg, ['"teapot"'])
        self.assertEqual((tags, folders), (["topic"], ["Notes"]))

    def test_quotes_cannot_break_out(self):
        self.assertEqual(search.fts_term('a"b'), '"a""b"')
        self.assertIsNone(search.fts_term('""'))


class SearchApiTest(unittest.TestCase):
    def test_fulltext_and_diacritics(self):
        for q in ("cafe", "caf%C3%A9", "%E5%9C%B0%E5%9B%B3"):         # cafe folds to café; the CJK term (地図) is found as is
            status, d = getj("/api/search?q=" + q)
            self.assertEqual(status, 200, q)
            self.assertEqual([r["path"] for r in d["results"]], ["Notes/Paper lanterns.md"], q)
            self.assertEqual(d["total"], 1, q)

    def test_snippet_marks_and_escapes(self):
        _, d = getj("/api/search?q=bamboo")
        r = d["results"][0]
        self.assertIn("<mark>bamboo</mark>", r["snippet"])
        self.assertEqual(r["snippet"].replace("<mark>", "").replace("</mark>", "").count("<"), 0)
        self.assertEqual(r["url"], "https://kura.test/n/Notes/Paper%20lanterns")
        self.assertIsInstance(r["changed"], int)
        self.assertNotEqual(r["changed"] % 86400, 0)               # the commit's exact time, not midnight
        self.assertEqual(r["created"], 1768435200)             # 2026-01-15 UTC

    def test_templates_never_found(self):
        _, d = getj("/api/search?q=template")
        self.assertEqual(d["total"], 0)

    def test_title_weight_and_prefix(self):
        _, d = getj("/api/search?q=crafts")                     # MOC/Crafts's title beats Lantern's body
        self.assertEqual([r["path"] for r in d["results"]], ["MOC/Crafts.md", "Projects/Lantern.md"])
        _, d = getj("/api/search?q=lantern*")                   # prefix: lantern, lanterns
        self.assertEqual(d["total"], 3)
        _, d = getj("/api/search?q=title:brew*")
        self.assertEqual([r["title"] for r in d["results"]], ["Tea brewing"])

    def test_filters_and_negation(self):
        _, d = getj("/api/search?q=&tag=topic/hobby")             # nested tags count
        self.assertEqual({r["path"] for r in d["results"]}, {"Projects/Lantern.md", "Notes/Paper lanterns.md"})
        _, d = getj("/api/search?q=lanterns+-bamboo")
        self.assertNotIn("Notes/Paper lanterns.md", [r["path"] for r in d["results"]])
        _, d = getj("/api/search?q=folder:Notes+-teapot")
        self.assertEqual([r["path"] for r in d["results"]], ["Notes/Paper lanterns.md"])
        _, d = getj("/api/search?q=")
        self.assertEqual((d["total"], d["results"]), (0, []))

    def test_card_url(self):
        _, d = getj("/api/search?q=lantern*&sort=changed")
        by = {r["path"]: r for r in d["results"]}
        self.assertEqual(by["Projects/Lantern.md"]["card_url"], "https://konbini.test/p/lantern")
        self.assertIsNone(by["Notes/Paper lanterns.md"]["card_url"])

    def test_card_from_new_status_field(self):
        from vaultkit import Note
        self.assertEqual(api.card_slug(Note("Projects/X.md", {"status": "wip", "project": "x"}, "")), "x")
        self.assertEqual(api.card_slug(Note("Notes/Y.md", {"status": "draft"}, "")), "")

    def test_bad_sort(self):
        self.assertEqual(get("/api/search?q=x&sort=nope")[0], 400)


class NoteApiTest(unittest.TestCase):
    def test_note_is_sanitized_and_absolute(self):
        status, d = getj("/api/note?path=Projects/Lantern.md")
        self.assertEqual(status, 200)
        h = d["html"]
        for bad in ("<script", "<iframe", "javascript:", "onclick", "alert(1)"):
            self.assertNotIn(bad, h)
        self.assertIn('href="https://kura.test/n/Notes/Paper%20lanterns"', h)
        self.assertIn('src="https://kura.test/a/pic.png"', h)
        self.assertIn("project: lantern", d["markdown"])
        self.assertEqual([x["path"] for x in d["backlinks"]], ["MOC/Crafts.md"])
        self.assertEqual({x["path"] for x in d["outlinks"]}, {"MOC/Crafts.md", "Notes/Paper lanterns.md"})

    def test_batch_lookup(self):
        _, d = getj("/api/notes?paths=MOC/Crafts.md,Nope.md&urls=https://kura.test/n/Projects/Lantern")
        self.assertEqual([n["path"] for n in d["notes"]], ["MOC/Crafts.md", "Projects/Lantern.md"])
        self.assertEqual(d["missing"], ["Nope.md"])

    def test_recent_tags_folders(self):
        _, d = getj("/api/recent?limit=2")
        self.assertEqual((d["total"], len(d["results"])), (4, 2))
        _, d = getj("/api/tags")
        self.assertIn({"tag": "topic/tea", "count": 1}, d["tags"])
        _, d = getj("/api/folders")
        self.assertIn({"folder": "Notes", "count": 2}, d["folders"])
        self.assertNotIn("Templates", [f["folder"] for f in d["folders"]])


class ReaderTest(unittest.TestCase):
    def test_owner_gate(self):
        self.assertEqual(get("/", user=None)[0], 403)
        self.assertEqual(get("/api/search?q=x", user="guest@test")[0], 403)
        status, body = get("/api/status", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["notes"], 4)

    def test_pages(self):
        for path in ("/", "/recent", "/t/", "/t/topic", "/f/Notes", "/n/Projects/Lantern", "/preview/MOC/Crafts",
                     "/search?q=bamboo", "/search", "/feed.xml", "/manifest.webmanifest", "/sw.js", "/offline",
                     "/static/kura.css", "/static/icons/kura.svg", "/a/pic.png"):
            self.assertEqual(get(path)[0], 200, path)
        self.assertEqual(get("/n/Templates/Project")[0], 404)
        self.assertEqual(get("/n/nope")[0], 404)

    def test_note_page_links(self):
        _, body = get("/n/Projects/Lantern")
        self.assertIn('href="/n/Notes/Paper%20lanterns"', body)
        self.assertIn("View Card in Konbini", body)
        self.assertNotIn("Niwa</span>", body)                   # no NIWA_URL: no sister link

    def test_search_page(self):
        _, body = get("/search?q=bamboo")
        self.assertIn("Paper lanterns", body)


class ShellTest(unittest.TestCase):
    """Machiya's shared shell (vaultkit.shell, ui/): the room, settings, the service worker, pins and no-store."""

    def test_room_page(self):
        _, body = get("/")
        for want in ('href="/static/machiya.css?v=', 'href="/static/kura.css?v=', 'class="theme-system room-kura"',
                     '<form class="search"', 'placeholder="Search Notes"', 'class="foot"', "synced ", 'href="/settings"'):
            self.assertIn(want, body)
        for name in ("machiya.css", "machiya.js", "machiya-sw.js"):
            self.assertEqual(get("/static/" + name)[0], 200, name)

    def test_settings(self):
        status, headers, body = fetch("/settings")
        self.assertEqual(status, 200)
        for want in ("Appearance", "Preview Pane", 'data-set="previewPane" data-cookie', "Obsidian Vault",
                     "Offline Copies", "About"):
            self.assertIn(want, body)

    def test_service_worker(self):
        _, headers, body = fetch("/sw.js")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertIn('importScripts("/static/machiya-sw.js?v=', body)
        cfg = json.loads(body[body.index("machiyaSW(") + 10:body.rindex(")")])
        self.assertEqual(cfg["notes"], {"match": "^/n/", "limit": 200})
        self.assertEqual(cfg["pins"], "/api/offline")
        self.assertIn("^/api/", cfg["bypass"])
        self.assertIn("/offline", cfg["precache"])

    def test_pins(self):
        _, d = getj("/api/offline")
        self.assertEqual(d, {"urls": ["/n/Notes/Tea%20brewing"]})
        self.assertIn('<meta name="machiya-offline" content="pin">', get("/n/Notes/Tea%20brewing")[1])
        self.assertNotIn("machiya-offline", get("/n/MOC/Crafts")[1])

    def test_never_stored(self):
        import pages
        old = pages.NEVER_STORED
        pages.NEVER_STORED = ("MOC/",)                 # stands in for Archive/ (the fixture has none)
        try:
            for path in ("/n/MOC/Crafts", "/f/MOC", "/preview/MOC/Crafts", "/?p=MOC/Crafts"):
                self.assertEqual(fetch(path)[1]["Cache-Control"], "no-store", path)
            self.assertNotEqual(fetch("/n/Projects/Lantern")[1]["Cache-Control"], "no-store")
            self.assertNotIn('data-path="MOC/Crafts.md"', fetch("/")[2])   # the default preview skips it
        finally:
            pages.NEVER_STORED = old

    def test_preview_pane_setting(self):
        self.assertIn('class="kpreview"', fetch("/")[2])
        body = fetch("/", cookie="previewPane=false")[2]
        self.assertNotIn('class="kpreview"', body)
        self.assertIn("kwide", body)

    def test_theme_fallback(self):
        status, headers, _ = fetch("/theme?set=auto")
        self.assertEqual(status, 302)
        self.assertIn("theme=system;", headers["Set-Cookie"])
        self.assertIn("theme-day", fetch("/", cookie="theme=day")[2])


class AuthTest(unittest.TestCase):
    def test_auth_modes(self):
        self.assertEqual(kura.auth_mode(None), "tailscale")          # the default: the Tailscale-User-Login allow-list
        self.assertEqual(kura.auth_mode(" Open "), "open")
        with self.assertRaises(SystemExit):
            kura.auth_mode("opne")                                    # a typo never opens the notes
        self.assertEqual(kura.BIND, "0.0.0.0")
        self.assertEqual(json.loads(get("/api/status", user=None)[1])["auth"], "tailscale")

    def test_redirects_stay_on_this_host(self):
        for good in ("/", "/n/X", "/v/work/n/A%20B", "/search"):
            self.assertEqual(kura.local_path(good), good)
        for bad in ("", "//evil.test", "/\\evil.test", "evil.test", "https://evil.test/", "/\tevil", "/x\ny", "/x\x7f"):
            self.assertEqual(kura.local_path(bad), "/", repr(bad))
        st, h, _ = fetch("/v/personal//evil.test/n/X")
        self.assertEqual((st, h["Location"]), (301, "/"))                 # was //evil.test/n/X: off-site
        st, h, _ = fetch("/v/personal/n/X")
        self.assertEqual((st, h["Location"]), (301, "/n/X"))
        for ref, where in (("https://kura.test//evil.test/x", "/"), ("https://kura.test/\\evil.test", "/"),
                           ("https://kura.test/n/X", "/n/X"), ("", "/")):
            st, h, _ = fetch("/theme?set=day", headers={"Referer": ref} if ref else {})
            self.assertEqual((st, h["Location"]), (302, where), ref)

    def test_public_url_is_an_origin(self):
        # Clients tell a work vault's note by /v/ at the start of its path: a base with a path would hide it.
        self.assertEqual(kura.public_url(None), "")
        self.assertEqual(kura.public_url(" https://kura.test/ "), "https://kura.test")
        self.assertEqual(kura.public_url("http://kura.test:8080"), "http://kura.test:8080")
        for bad in ("https://host.test/kura", "https://host.test/kura/", "https://host.test/?x=1", "https://host.test/#n",
                    "kura.test", "ftp://kura.test", "https://user@kura.test", "https://kura.test:port", "https://"):
            with self.assertRaises(SystemExit, msg=bad):
                kura.public_url(bad)

    def test_open_mode_skips_the_allow_list_only(self):
        kura.AUTH = "open"
        try:
            self.assertEqual(get("/", user=None)[0], 200)
            self.assertEqual(get("/api/search?q=bamboo", user="guest@test")[0], 200)
            self.assertEqual(json.loads(get("/api/status", user=None)[1])["auth"], "open")
            h = kura.Handler.__new__(kura.Handler)
            h.headers = {"Tailscale-User-Login": "mallory@test"}
            self.assertEqual(h.actor(), "local")                      # the header never names who asked
        finally:
            kura.AUTH = "tailscale"
        self.assertEqual(get("/", user=None)[0], 403)

    def test_open_mode_answers_only_known_hosts(self):
        """DNS rebinding: a page on evil.test, its name pointed at Kura, sends Host: evil.test (and a matching Origin)."""
        allowed = {"localhost", "kura.test", "notes.lan"}
        for good in ("localhost", "LOCALHOST:8080", "localhost.", "kura.test", "Kura.Test.:443", "notes.lan",
                     "127.0.0.1", "127.0.0.1:8080", "[::1]", "[::1]:8080", "192.168.1.5", "[fe80::1%25eth0]:80"):
            self.assertTrue(kura.host_allowed(good, allowed), good)
        for bad in ("", None, "evil.test", "evil.test:8080", "localhost.evil.test", "kura.test.evil.test", "[::1",
                    "0x7f.1", "2130706433"):
            self.assertFalse(kura.host_allowed(bad, allowed), bad)
        self.assertIn("kura.test", kura.ALLOWED_HOSTS)                  # KURA_PUBLIC_URL's name
        port = SERVER.server_address[1]
        kura.AUTH = "open"
        try:
            for host, status in (("evil.test", 403), ("evil.test:%d" % port, 403), ("127.0.0.1:%d" % port, 200),
                                 ("localhost:%d" % port, 200), ("kura.test", 200)):
                st, _, body = fetch("/api/search?q=bamboo", headers={"Host": host})
                self.assertEqual(st, status, host)
                if status == 403:
                    self.assertNotIn("Lantern", body)
            self.assertEqual(fetch("/api/status", headers={"Host": "evil.test"})[0], 403)
        finally:
            kura.AUTH = "tailscale"
        self.assertEqual(fetch("/api/search?q=bamboo", headers={"Host": "evil.test"})[0], 200)   # the allow-list's job

    def test_a_silent_client_is_dropped(self):
        import socket
        old, kura.Handler.timeout = kura.Handler.timeout, 0.5
        try:
            s = socket.create_connection(SERVER.server_address, timeout=10)
            s.sendall(b"GET /api/status HTTP/1.0\r\n")              # and never the blank line
            self.assertEqual(s.recv(100), b"")                        # Kura hung up instead of waiting for good
            s.close()
        finally:
            kura.Handler.timeout = old
        self.assertEqual(kura.Handler.timeout, 30)

    def run_kura(self, env, code="import kura; print(kura.AUTH, kura.BIND, kura.PORT, kura.POLL)"):
        full = {k: v for k, v in os.environ.items() if not k.startswith("KURA_")}
        full.update(KURA_REPO_DIR=REPO, KURA_REPO_URL="", **env)
        return subprocess.run([sys.executable, "-c", code], cwd=os.path.join(HERE, "..", "app"), env=full,
                              capture_output=True, text=True, timeout=60)

    def test_defaults_name_no_owner(self):
        """Nothing is set: the vault is the repo root, nobody is allowed, and the one vault is called notes."""
        r = self.run_kura({}, "import kura, sites; print(repr(kura.SUBDIR), sorted(kura.USERS), sites.DEFAULT_NAME)")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "'' [] notes")
        r = self.run_kura({"KURA_REPO_SUBDIR": "/personal/"}, "import kura; print(repr(kura.SUBDIR))")
        self.assertEqual(r.stdout.strip(), "'personal'")                  # an explicit value still wins, slashes trimmed

    def test_env_file(self):
        path = os.path.join(TMP, "kura.env")
        with open(path, "w") as f:
            f.write("# a native install\nexport KURA_AUTH=open\nKURA_BIND='127.0.0.1'\nKURA_POLL=90  # seconds\n"
                    "KURA_PORT=9999\n")
        r = self.run_kura({"KURA_ENV_FILE": path, "KURA_PORT": "1234"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["open", "127.0.0.1", "1234", "90"])   # the real environment wins
        with open(path, "w") as f:
            f.write("KURA_USERS=me@test\nthis line holds s3cret\n")
        r = self.run_kura({"KURA_ENV_FILE": path})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("line 2", r.stderr)
        self.assertNotIn("s3cret", r.stderr)                          # errors never echo a line
        r = self.run_kura({"KURA_AUTH": "opne"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("KURA_AUTH must be tailscale or open", r.stderr)


class VaultsTest(unittest.TestCase):
    """Other vaults (docs/contracts/kura-api.md, "Vaults"): additive, private, never stored, never pushed."""

    def test_default_api_unchanged(self):
        _, d = getj("/api/search?q=zebrafish")
        self.assertEqual(d["total"], 0)                                  # a client that never sends vault
        _, d = getj("/api/search?q=vault:work+zebrafish")
        self.assertEqual(d["total"], 0)                                  # vault: narrows, never widens
        _, d = getj("/api/recent?limit=50")
        self.assertEqual({n["vault"] for n in d["results"]}, {"personal"})
        self.assertEqual(d["total"], 4)
        _, d = getj("/api/note?path=Projects/Lantern.md")
        self.assertEqual((d["vault"], d["url"]), ("personal", "https://kura.test/n/Projects/Lantern"))
        self.assertEqual(get("/api/note?path=Runbooks/Zebrafish%20deploy.md")[0], 404)   # not in the default vault
        self.assertNotIn("topic/client", [t["tag"] for t in getj("/api/tags")[1]["tags"]])

    def test_vault_parameter(self):
        _, d = getj("/api/search?q=zebrafish&vault=work")
        n = d["results"][0]
        self.assertEqual((d["total"], n["vault"], n["url"]), (1, "work", "https://kura.test/v/work/n/Runbooks/Zebrafish%20deploy"))
        self.assertEqual((n["published"], n["card_url"]), (False, None))   # publish:true and project: are ignored there
        self.assertIn("<mark>", n["snippet"])
        _, d = getj("/api/search?q=vault:work+zebrafish&vault=all")
        self.assertEqual(d["total"], 1)
        _, d = getj("/api/search?q=vault:personal+zebrafish&vault=work")
        self.assertEqual(d["total"], 0)                                  # narrows within the allowed set
        _, d = getj("/api/search?q=lantern&vault=all")
        self.assertEqual({n["vault"] for n in d["results"]}, {"personal", "work"})
        _, d = getj("/api/recent?vault=work")
        self.assertEqual((d["total"], {n["vault"] for n in d["results"]}), (2, {"work"}))
        for bad in ("nope", "work,nope"):
            self.assertEqual(get("/api/search?q=x&vault=" + bad)[0], 400, bad)
        self.assertEqual(get("/api/note?path=x&vault=all")[0], 400)      # one vault only
        self.assertEqual(get("/api/tags?vault=work,personal")[0], 400)
        _, d = getj("/api/tags?vault=work")
        self.assertEqual(d["tags"], [{"tag": "2025", "count": 1}, {"tag": "topic/client", "count": 1}])   # tags: 2025 indexes
        _, d = getj("/api/folders?vault=work")
        self.assertEqual(d["folders"], [{"folder": "Runbooks", "count": 1}])

    def test_notes_and_note(self):
        u = "https://kura.test/v/work/n/Runbooks/Zebrafish%20deploy"
        _, d = getj("/api/notes?urls=%s&paths=Lantern.md&urls=https://kura.test/n/Projects/Lantern" % u)
        self.assertEqual({(n["vault"], n["path"]) for n in d["notes"]},
                         {("work", "Runbooks/Zebrafish deploy.md"), ("personal", "Projects/Lantern.md")})
        self.assertEqual(d["missing"], ["Lantern.md"])                    # paths mean the default vault
        _, d = getj("/api/notes?paths=Lantern.md&vault=work")
        self.assertEqual([n["vault"] for n in d["notes"]], ["work"])
        _, d = getj("/api/note?path=Runbooks/Zebrafish%20deploy.md&vault=work")
        self.assertIn('href="https://kura.test/v/work/n/Lantern"', d["html"])          # wikilinks stay in the vault
        self.assertNotIn("/n/Projects/Lantern", d["html"])
        self.assertIn('src="https://kura.test/v/work/a/wpic.png"', d["html"])
        self.assertEqual([x["url"] for x in d["backlinks"]], [])
        self.assertEqual([x["url"] for x in d["outlinks"]], ["https://kura.test/v/work/n/Lantern"])

    def test_vaults_and_status(self):
        _, d = getj("/api/vaults")
        self.assertEqual([(v["name"], v["title"], v["default"], v["private"], v["obsidian"], v["notes"], v["error"])
                          for v in d["vaults"]],
                         [("personal", "Personal", True, False, "personal", 4, None),
                          ("work", "Work Notes", False, True, "work", 2, None)])
        _, st = getj("/api/status")
        self.assertEqual((st["notes"], st["error"], sorted(st["vaults"])), (4, None, ["personal", "work"]))
        self.assertEqual(st["vaults"]["work"], {"error": None})            # unauthenticated: no head or count for a work vault
        self.assertEqual(sorted(st["vaults"]["personal"]), ["error", "head", "notes", "synced_at"])
        kura.state.by_name["work"].error = "boom"
        try:
            self.assertEqual(getj("/api/status")[1]["error"], "boom")     # any vault's error fails the probe
        finally:
            kura.state.by_name["work"].error = None

    def test_reader_routes_and_no_store(self):
        status, h, body = fetch("/v/work/n/Runbooks/Zebrafish%20deploy")
        self.assertEqual((status, h["Cache-Control"]), (200, "no-store"))
        self.assertIn('href="/v/work/n/Lantern"', body)
        self.assertIn('class="chip private"', body)
        self.assertNotIn("View in Niwa", body)
        self.assertNotIn("View Card in Konbini", body)
        self.assertNotIn("machiya-offline", body)                          # offline: true is ignored in a work vault
        self.assertIn('src="/v/work/a/wpic.png"', body)
        for path in ("/v/work/", "/v/work/recent", "/v/work/t/", "/v/work/t/topic/client", "/v/work/f/Runbooks",
                     "/v/work/preview/Lantern", "/v/work/search?q=zebrafish", "/v/work/n/Nope", "/v/work/a/wpic.png"):
            st, h, _ = fetch(path)
            self.assertEqual(h["Cache-Control"], "no-store", path)
            self.assertIn(st, (200, 404), path)
        self.assertEqual(fetch("/v/work/a/wpic.png")[0], 200)
        self.assertEqual(fetch("/a/wpic.png")[0], 404)                     # the default vault has no such image
        self.assertNotEqual(fetch("/a/pic.png")[1]["Cache-Control"], "no-store")
        st, h, _ = fetch("/v/personal/n/Projects/Lantern?p=x")
        self.assertEqual((st, h["Location"]), (301, "/n/Projects/Lantern?p=x"))
        self.assertEqual(fetch("/v/nope/n/x")[0], 404)
        self.assertEqual(fetch("/n/Runbooks/Zebrafish%20deploy")[0], 404)   # a work note is not at the default URL
        self.assertEqual(fetch("/v/work/feed.xml")[0], 404)                 # no feed for a work vault

    def test_search_scopes(self):
        _, _, body = fetch("/search?q=zebrafish")
        self.assertNotIn("Zebrafish", body)                                # the default vault's page finds nothing
        self.assertIn("All Vaults", body)
        st, h, body = fetch("/search?q=zebrafish&vaults=all")
        self.assertIn("/v/work/n/Runbooks/Zebrafish%20deploy", body)
        self.assertIn('<span class="chip private">Work Notes</span>', body)
        self.assertEqual(h["Cache-Control"], "no-store")                   # it carries work notes
        _, _, body = fetch("/v/work/search?q=zebrafish")
        self.assertIn("Zebrafish deploy", body)

    def test_header_switch(self):
        _, _, body = fetch("/")
        self.assertIn('<details class="vaults"><summary class="chip" ', body)
        self.assertIn('<a href="/v/work/">Work Notes<small>2</small></a>', body)
        _, _, body = fetch("/v/work/")
        self.assertIn('<summary class="chip private"', body)
        self.assertIn('action="/v/work/search"', body)
        self.assertIn('href="/v/work/recent"', body)                        # nav and tabs keep the prefix

    def test_feed_and_offline_are_default_only(self):
        self.assertNotIn("Zebrafish", get("/feed.xml")[1])
        self.assertNotIn("Zebrafish", get("/feed.xml?q=zebrafish")[1])
        self.assertNotIn("Zebrafish", get("/feed.xml?q=vault:work")[1])
        self.assertNotIn("/v/work", get("/api/offline")[1])
        cfg = json.loads(fetch("/sw.js")[2][fetch("/sw.js")[2].index("machiyaSW(") + 10:].rsplit(")", 1)[0])
        self.assertIn("^/v/", cfg["network"])                               # the worker never stores /v/ navigations

    def test_push_refuses_a_work_vault(self):
        import push
        p = push.Push("http://127.0.0.1:9", os.path.join(TMP, "push-private.sqlite3"), "personal", "https://kura.test")
        work = kura.state.by_name["work"]
        with self.assertRaises(ValueError):
            p.run_once(work, kura.pages.visible(work))


class DefaultVaultOnlyTest(unittest.TestCase):
    """Kura's half of "Rules for clients" 3 (docs/contracts/kura-api.md): a client that never sends `vault` gets the
    default vault and nothing else, however it words the query, the filters or the URL. Guards every client (Shiori,
    machiya-mcp, anything that feeds a model), not just the ones that filter again on their side."""

    def seen(self, node, found):
        """Every `vault` value and every url in a JSON answer."""
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "vault":
                    found.add(v)
                elif k in ("url", "card_url") and isinstance(v, str) and "/v/" in v:
                    found.add(v)
                self.seen(v, found)
        elif isinstance(node, list):
            for v in node:
                self.seen(v, found)
        return found

    def assert_default_only(self, path):
        status, body = get(path)
        self.assertEqual(status, 200, path)
        self.assertNotIn("Zebrafish", body, path)
        self.assertNotIn("/v/", body, path)
        self.assertLessEqual(self.seen(json.loads(body), set()), {"personal"}, path)
        return json.loads(body)

    def test_queries_never_widen(self):
        for q in ("vault:work zebrafish", "VAULT:WORK zebrafish", 'vault:"work" zebrafish', "vault:/work/ zebrafish",
                  "vault:all zebrafish", "-vault:personal zebrafish", "vault:work", "vault:work lantern",
                  "title:zebrafish", "tag:topic/client", "folder:Runbooks", "zebrafish* vault:work", "vault:work -x"):
            for extra in ("", "&sort=changed"):
                d = self.assert_default_only("/api/search?q=%s%s" % (q.replace(" ", "+").replace('"', "%22"), extra))
                self.assertEqual(d["total"], 0, q)
        for extra in ("tag=topic/client", "folder=Runbooks", "tag=2025"):
            self.assertEqual(self.assert_default_only("/api/search?q=zebrafish&" + extra)["total"], 0, extra)

    def test_no_vault_parameter_means_the_default_vault(self):
        for path in ("/api/search?q=lantern", "/api/search?q=lantern&vault=", "/api/search?q=lantern&vault=%20",
                     "/api/search?q=lantern&vault=personal", "/api/recent?limit=50", "/api/recent?limit=50&vault=",
                     "/api/notes?paths=Lantern.md,Projects/Lantern.md",
                     "/api/note?path=Projects/Lantern.md", "/api/tags", "/api/folders", "/api/offline"):
            self.assert_default_only(path)
        _, d = getj("/api/search?q=lantern")
        self.assertEqual({n["path"] for n in d["results"]}, {"Projects/Lantern.md", "MOC/Crafts.md"})   # not work's Lantern.md

    def test_default_notes_never_link_into_a_work_vault(self):
        # the work vault has its own Lantern.md; the default vault's links and backlinks must stay on personal notes
        _, d = getj("/api/note?path=Projects/Lantern.md")
        for x in d["backlinks"] + d["outlinks"]:
            self.assertNotIn("/v/", x["url"])
        self.assertNotIn("/v/", d["html"])
        _, d = getj("/api/note?path=MOC/Crafts.md")
        self.assertNotIn("/v/", d["html"] + json.dumps(d["backlinks"] + d["outlinks"]))

    def test_a_vault_url_or_path_needs_the_exact_shape(self):
        work = "Runbooks/Zebrafish%20deploy"
        for url in ("https://kura.test/V/work/n/" + work, "https://kura.test/v/WORK/n/" + work,
                    "https://kura.test//v/work/n/" + work,
                    "https://kura.test/n/../v/work/n/" + work, "https://kura.test/n/%2e%2e/v/work/n/" + work,
                    "https://kura.test/n/v/work/n/" + work, "https://kura.test/v/work/a/wpic.png",
                    "https://kura.test/v/work/n/Nope"):
            _, d = getj("/api/notes?urls=" + url)
            self.assertEqual(d["notes"], [], url)
        for path in ("v/work/" + work + ".md", "/v/work/" + work + ".md", "../work/" + work + ".md"):
            self.assertEqual(get("/api/note?path=" + path)[0], 404, path)
        for same in ("//v/work/n/", "/%76/work/n/"):   # http.server folds a leading // and Kura decodes %76: /v/ itself
            st, h, _ = fetch(same + work)
            self.assertEqual((st, h["Cache-Control"]), (200, "no-store"), same)
        for path in ("/V/work/n/" + work, "/v/WORK/n/" + work,
                     "/n/../v/work/n/" + work, "/n/%2e%2e/v/work/n/" + work, "/n/v/work/n/" + work, "/V/work/"):
            status, _, body = fetch(path)
            self.assertNotIn("Zebrafish runbook", body, path)
            self.assertIn(status, (301, 400, 404), path)

    def test_a_work_vault_needs_the_owner_too(self):
        for path in ("/api/search?q=zebrafish&vault=work", "/api/search?q=zebrafish&vault=all", "/api/recent?vault=all",
                     "/api/vaults", "/v/work/n/Runbooks/Zebrafish%20deploy", "/v/work/search?q=zebrafish"):
            for user in ("stranger@test", None):
                status, body = get(path, user=user)
                self.assertEqual(status, 403, (path, user))
                self.assertNotIn("Zebrafish", body, path)

    def test_empty_vault_names_mean_the_default_vault(self):
        for v in (",", ",,", "%20,%20"):             # once an IndexError (no answer at all)
            for path in ("/api/search?q=zebrafish&vault=", "/api/recent?vault="):
                self.assert_default_only(path + v)


class ExternalLinksTest(unittest.TestCase):
    """external_links on /api/note and GET /api/links (the contract's "Save This Note's Links"): the default vault's
    http(s) links, never a work vault's."""

    LANTERN = [("https://example.com/lantern", "Lantern docs"), ("https://example.org/auto", "https://example.org/auto"),
               ("https://example.net/bare", "https://example.net/bare"), ("https://example.com/raw", "raw html")]

    def test_a_note_has_its_body_links_in_order(self):
        _, d = getj("/api/note?path=Projects/Lantern.md")
        self.assertEqual([(x["url"], x["text"]) for x in d["external_links"]], self.LANTERN)
        # markdown, an autolink, a bare URL and raw HTML all count; a repeated url is kept once; the code span, the
        # fence, mailto:, Kura's own host and Konbini's (the rooms) are left out
        _, d = getj("/api/note?path=MOC/Crafts.md")
        self.assertEqual(d["external_links"], [])
        _, d = getj("/api/notes?paths=Projects/Lantern.md")
        self.assertNotIn("external_links", d["notes"][0])               # only /api/note and /api/links carry it

    def test_other_rooms_and_own_host_are_left_out(self):
        import unittest.mock
        site = kura.state.default
        note = site.notes["Projects/Lantern.md"]
        with unittest.mock.patch.object(api.shell, "rooms", lambda: {"shiori": "https://example.net", "kura": "https://example.org"}):
            urls = [x["url"] for x in api.external_links(site, note, "https://example.com")]
        self.assertEqual(urls, [])                                       # every link here is on a room or the own host
        urls = [x["url"] for x in api.external_links(site, note, "https://example.com:8443")]
        self.assertEqual(urls, [x[0] for x in self.LANTERN if "example.com" not in x[0]])   # ports don't matter

    def test_gemini_and_gopher_links(self):
        _, d = getj("/api/note?path=Notes/Paper%20lanterns.md")
        self.assertEqual([(x["url"], x["text"]) for x in d["external_links"]],
                         [("https://en.wikipedia.org/wiki/Cat", "Wikipedia"),
                          ("gemini://gem.example/lanterns", "a gemlog"),
                          ("gopher://goph.example/1/lanterns", "gopher://goph.example/1/lanterns")])
        self.assertNotIn('href="gemini:', d["html"])                    # the sanitized html gains no new scheme: the href
        self.assertNotIn('href="gopher:', d["html"])                    # is dropped as before; bare text stays text
        self.assertIn("gemini://gem.example/lanterns", d["markdown"])
        _, d = getj("/api/links?folder=Notes")
        self.assertEqual([x["url"] for x in d["notes"][0]["external_links"]][1:],
                         ["gemini://gem.example/lanterns", "gopher://goph.example/1/lanterns"])

    def test_a_work_note_never_has_links(self):
        _, d = getj("/api/note?path=Runbooks/Zebrafish%20deploy.md&vault=work")
        self.assertEqual((d["vault"], d["external_links"]), ("work", []))   # it does contain two http links
        self.assertIn("https://work.example.com/secret", d["markdown"])
        work = kura.state.by_name["work"]
        self.assertEqual(api.note_links(work, work.notes["Runbooks/Zebrafish deploy.md"]), [])
        for path in ("/api/links?folder=Runbooks&vault=work", "/api/links?folder=Runbooks&vault=all",
                     "/api/links?folder=Runbooks&vault=personal,work", "/api/links?folder=Runbooks&vault=nope"):
            self.assertEqual(get(path)[0], 400, path)
        for path in ("/api/links?folder=Runbooks", "/api/links?folder=Runbooks/", "/api/search?q=work.example.com"):
            status, body = get(path)
            self.assertEqual(status, 200, path)
            self.assertNotIn("work.example.com", body, path)

    def test_links_of_a_folder(self):
        _, d = getj("/api/links?folder=Projects")
        self.assertEqual(d["total"], 1)
        self.assertEqual({k: d["notes"][0][k] for k in ("path", "title", "url")},
                         {"path": "Projects/Lantern.md", "title": "Lantern", "url": "https://kura.test/n/Projects/Lantern"})
        self.assertEqual([(x["url"], x["text"]) for x in d["notes"][0]["external_links"]], self.LANTERN)
        _, d = getj("/api/links?folder=Notes")
        self.assertEqual([n["path"] for n in d["notes"]], ["Notes/Paper lanterns.md"])    # Tea brewing: no links
        self.assertEqual(getj("/api/links?folder=Projects&vault=personal")[1]["total"], 1)  # the default's own name is fine
        _, d = getj("/api/links?folder=Projects&limit=1&offset=1")
        self.assertEqual((d["total"], d["notes"]), (1, []))              # total counts the notes with links, paging cuts
        _, d = getj("/api/links?folder=Nope")
        self.assertEqual((d["total"], d["notes"]), (0, []))
        for path in ("/api/links", "/api/links?folder=", "/api/links?folder=%2F"):
            self.assertEqual(get(path)[0], 400, path)                    # folder is required
        self.assertEqual(get("/api/links?folder=Projects", user="stranger@test")[0], 403)   # owner-only
        self.assertEqual(get("/api/links?folder=Projects", user=None)[0], 403)

    def test_every_way_of_writing_a_link(self):
        """Bare links in prose, lists and tables, after a colon, in brackets; the four schemes; what is left out."""
        root = os.path.join(TMP, "linkforms")
        os.makedirs(root)
        text = "\n".join([
            "---", "title: Forms", "---",
            "Colon: see https://x.example/a. And (https://x.example/paren) and [https://x.example/brk] and {https://x.example/brace}.",
            "", "- list item https://x.example/list", "- another: https://x.example/list2, then more", "",
            "| name | link |", "|---|---|", "| table | https://x.example/table |", "| gem | gemini://gem.example/table |", "",
            "Bare gemini://gem.example/bare. and gopher://goph.example/1/bare; end. Upper-case HTTPS://X.EXAMPLE/UP too.",
            "[gem link](gemini://gem.example/md), raw <a href=\"gopher://goph.example/raw\">raw gopher</a>, <gemini://gem.example/auto>"
            " and <https://x.example/auto>.",
            "Balanced https://x.example/wiki_(thing) and \"https://x.example/quoted\" and *https://x.example/em* and https://x.example/a_b_c.",
            "Left out: https://x.example/a (again), [mail](mailto:a@b.c), obsidian://open?vault=v, ftp://f.example/x, `gemini://code.example/inline`,",
            "own https://kura.test/n/Home, room https://konbini.test/p/x, gemini://kura.test/capsule, and a bare scheme gemini:// or https://.",
            "", "```", "gemini://code.example/fence https://code.example/fence", "```", ""])
        with open(os.path.join(root, "Note.md"), "w") as f:
            f.write(text)
        sh("git", "init", "-q", "-b", "main", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=root)
        made, srcs = sites.build({}, "", root, "", "", "", "token")
        site = made[0]
        st = kura.State.__new__(kura.State)
        st.sites, st.sources, st.default, st.by_name = made, srcs, site, {site.name: site}
        st.index, st.loop_error, st.push = search.Index(), None, None
        st.sync()
        got = [x["url"] for x in api.external_links(site, site.notes["Note.md"], "https://kura.test")]
        self.assertEqual(got, [
            "https://x.example/a", "https://x.example/paren", "https://x.example/brk", "https://x.example/brace",
            "https://x.example/list", "https://x.example/list2", "https://x.example/table", "gemini://gem.example/table",
            "gemini://gem.example/bare", "gopher://goph.example/1/bare", "HTTPS://X.EXAMPLE/UP",
            "gemini://gem.example/md", "gopher://goph.example/raw", "gemini://gem.example/auto", "https://x.example/auto",
            "https://x.example/wiki_(thing)", "https://x.example/quoted", "https://x.example/em", "https://x.example/a_b_c"])
        self.assertEqual(api.trim_url("https://x.example/a)]."), "https://x.example/a")
        self.assertEqual(api.trim_url("https://x.example/a_(b)"), "https://x.example/a_(b)")

    def test_an_escaped_pipe_alias_in_a_table_links(self):
        """`[[Note\\|alias]]` (how Obsidian writes an alias inside a table cell) is a link to Note and a backlink of it."""
        root = os.path.join(TMP, "pipealias")
        os.makedirs(root)
        with open(os.path.join(root, "Index.md"), "w") as f:
            f.write("| what | note |\n|---|---|\n| tea | [[Tea\\|the tea note]] |\n")
        with open(os.path.join(root, "Tea.md"), "w") as f:
            f.write("Hot water.\n")
        sh("git", "init", "-q", "-b", "main", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=root)
        made, srcs = sites.build({}, "", root, "", "", "", "token")
        site = made[0]
        st = kura.State.__new__(kura.State)
        st.sites, st.sources, st.default, st.by_name = made, srcs, site, {site.name: site}
        st.index, st.loop_error, st.push = search.Index(), None, None
        st.sync()
        html = site.render(site.notes["Index.md"], "", False, mode="kura", prefix="")
        self.assertIn('href="/n/Tea"', html)
        self.assertIn(">the tea note<", html)
        self.assertNotIn("\\", html)                                     # no backslash is left on the link
        self.assertEqual(sorted(site.backlinks.get("Tea.md", ())), ["Index.md"])

    def test_templates_never_appear(self):
        with open(os.path.join(REPO, "personal", "Templates", "Project.md")) as f:
            self.assertIn("template.example", f.read())                  # the fixture's template does hold a link
        for path in ("/api/links?folder=Templates", "/api/links?folder=Templates/"):
            self.assertEqual(getj(path)[1], {"total": 0, "notes": []})

    def test_links_at_the_repo_root_and_paging(self):
        root = os.path.join(TMP, "linkroot")
        os.makedirs(os.path.join(root, "Reading"))
        for rel, text in (("Home.md", "A [root link](https://root.example/a).\n"),
                          ("Reading/One.md", "[a](https://one.example/a) [b](https://one.example/b) [a again](https://one.example/a)\n"),
                          ("Reading/Two.md", "no links here\n"),
                          ("Reading/Three.md", "https://three.example/x, (https://three.example/y), and <https://three.example/z>.\n")):
            with open(os.path.join(root, rel), "w") as f:
                f.write(text)
        sh("git", "init", "-q", "-b", "main", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=root)
        made, srcs = sites.build({}, "", root, "", "", "", "token")
        site = made[0]
        st = kura.State.__new__(kura.State)
        st.sites, st.sources, st.default, st.by_name = made, srcs, site, {site.name: site}
        st.index, st.loop_error, st.push = search.Index(), None, None
        st.sync()
        found = api.folder_links(site, "Reading", "https://kura.test", list(site.notes.values()))
        self.assertEqual([(n.rel, [x["url"] for x in links]) for n, links in found],
                         [("Reading/One.md", ["https://one.example/a", "https://one.example/b"]),
                          ("Reading/Three.md", ["https://three.example/x", "https://three.example/y", "https://three.example/z"])])
        self.assertEqual(api.folder_links(site, "Nope", "", list(site.notes.values())), [])

    def test_the_note_view_link_is_off_unless_asked(self):
        _, _, page = fetch("/n/Projects/Lantern")
        self.assertNotIn("shiori://", page)                               # KURA_SHIORI_LINKS unset
        api.SHIORI_LINKS = True
        try:
            _, _, page = fetch("/n/Projects/Lantern")
            self.assertIn('href="shiori://save-links?path=Projects/Lantern.md">Save links in Shiori</a>', page)
            _, _, page = fetch("/n/Notes/Paper%20lanterns")
            self.assertIn("shiori://save-links?path=Notes/Paper%20lanterns.md", page)    # the vault path, percent-encoded
            _, _, page = fetch("/n/MOC/Crafts")
            self.assertNotIn("shiori://", page)                           # no external links, no action
            _, _, page = fetch("/v/work/n/Runbooks/Zebrafish%20deploy")
            self.assertNotIn("shiori://", page)                           # a work note has none, whatever the setting
        finally:
            api.SHIORI_LINKS = False


class StatusViewTest(unittest.TestCase):
    """/api/status answers without identity (the probes read it), so that view carries no configuration."""

    def test_the_open_view_has_no_repo_or_folder(self):
        status, body = get("/api/status", user=None)
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertNotIn("repo", d)
        self.assertNotIn("subdir", d)
        self.assertNotIn(REPO, body)                                     # no path of the checkout either
        self.assertEqual(sorted(d), ["auth", "error", "head", "notes", "push", "ready", "synced_at", "vaultkit", "vaults", "version"])
        self.assertIn('"ready": true', body)                             # what the monitoring probe matches on
        self.assertNotIn('"error": "', body)
        d = json.loads(get("/api/status", user="stranger@test")[1])      # a login that isn't allowed is no owner
        self.assertNotIn("repo", d)
        owner = getj("/api/status")[1]
        self.assertEqual(owner["subdir"], "personal")
        self.assertIn("repo", owner)

    def test_error_texts_are_short_in_the_open_view(self):
        work = kura.state.by_name["work"]
        work.error = "RuntimeError: git fetch https://forge.example/team/private-vault.git failed in /srv/vault"
        try:
            status, body = get("/api/status", user=None)
            self.assertEqual(status, 200)
            self.assertNotIn("forge.example", body)
            self.assertNotIn("/srv/vault", body)
            d = json.loads(body)
            self.assertEqual((d["error"], d["vaults"]["work"]), ("sync failed", {"error": "sync failed"}))
            self.assertIn('"error": "sync failed"', body)                # still trips a probe that looks for `"error": "`
            d = getj("/api/status")[1]                                   # the owner gets the text
            self.assertIn("forge.example", d["error"])
            self.assertEqual(d["vaults"]["work"]["error"], work.error)
        finally:
            work.error = None
        self.assertIsNone(json.loads(get("/api/status", user=None)[1])["error"])

    def test_the_push_error_is_short_too(self):
        class Stub:
            def status(self):
                return {"docs": 3, "error": "ConnectionError: http://hister.internal:4433 refused"}
        kura.state.push = Stub()
        try:
            _, body = get("/api/status", user=None)
            self.assertNotIn("hister.internal", body)
            self.assertEqual(json.loads(body)["push"], {"docs": 3, "error": "push failed"})
            self.assertIn("hister.internal", getj("/api/status")[1]["push"]["error"])
        finally:
            kura.state.push = None

    def test_open_mode_shows_everything_it_would_show_the_owner(self):
        kura.AUTH = "open"
        try:
            d = json.loads(get("/api/status", user=None)[1])
            self.assertIn("repo", d)                                     # no identity check in this mode: same as the notes
        finally:
            kura.AUTH = "tailscale"


class MirrorModeTest(unittest.TestCase):
    def test_clone_from_url(self):
        """Standalone: a git URL in KURA_VAULTS is cloned once, under KURA_REPO_DIR, and both vaults read it."""
        dest = os.path.join(TMP, "clones")
        url = "file://" + REPO
        env = {"KURA_VAULTS": "personal=%s#personal, work=%s#work" % (url, url)}
        st = kura.State.__new__(kura.State)
        st.sites, st.sources = sites.build(env, "", dest, "personal", "", "", "token")
        st.default, st.by_name = st.sites[0], {x.name: x for x in st.sites}
        st.index, st.loop_error, st.push = search.Index(), None, None
        self.assertEqual(len(st.sources), 1)                      # one clone for two vaults
        st.sync()
        self.assertTrue(st.ready)
        self.assertEqual(st.index.counts, {"personal": 4, "work": 2})

    def test_single_vault_from_repo_settings(self):
        """KURA_VAULTS unset: the KURA_REPO_* settings describe one vault, notes (sites.DEFAULT_NAME)."""
        made, srcs = sites.build({}, "", REPO, "personal", "", "", "token")
        self.assertEqual([(x.name, x.title, x.default, x.prefix, x.private) for x in made],
                         [("notes", "Notes", True, "", False)])
        self.assertEqual((made[0].subdir, made[0].obsidian), ("personal", "personal"))   # the folder, not the name
        self.assertEqual(len(srcs), 1)
        st = kura.State.__new__(kura.State)
        st.sites, st.sources = made, srcs
        st.default, st.by_name = made[0], {"notes": made[0]}
        st.index, st.loop_error, st.push = search.Index(), None, None
        st.sync()
        self.assertEqual(st.index.count, 4)

    def test_a_vault_at_the_repo_root(self):
        """The default KURA_REPO_SUBDIR is empty: the repo itself is the vault (what a fresh install has)."""
        root = os.path.join(TMP, "rootvault")
        os.makedirs(os.path.join(root, "Projects"))
        for rel, text in (("Home.md", "---\ntitle: Home\n---\nSee [[Idea]]. Bamboo.\n"),
                          ("Projects/Idea.md", "---\ntags: [topic/x]\n---\nA bamboo idea.\n")):
            with open(os.path.join(root, rel), "w") as f:
                f.write(text)
        sh("git", "init", "-q", "-b", "main", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=root)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=root)
        made, srcs = sites.build({}, "", root, "", "", "", "token")
        site = made[0]
        self.assertEqual((site.name, site.subdir, site.obsidian), ("notes", "", "notes"))
        st = kura.State.__new__(kura.State)
        st.sites, st.sources, st.default, st.by_name = made, srcs, site, {"notes": site}
        st.index, st.loop_error, st.push = search.Index(), None, None
        st.sync()
        self.assertEqual(st.index.count, 2)
        self.assertEqual(sorted(site.notes), ["Home.md", "Projects/Idea.md"])
        self.assertEqual(sorted(site.changed_at), ["Home.md", "Projects/Idea.md"])       # the git log covers the root
        total, rows, _ = st.index.search("bamboo", vaults=["notes"])
        self.assertEqual((total, sorted(r[1] for r in rows)), (2, ["Home.md", "Projects/Idea.md"]))

    def test_config_is_strict(self):
        self.assertEqual(sites.parse("a=/x#p, b:Big B=https://h/r.git#"),
                         [("a", "A", "/x", "p", False), ("b", "Big B", "https://h/r.git", "", False)])
        self.assertEqual(sites.parse("a=/x#p, b+shared:C++ & B=/x#b, c + shared =/x#c"),
                         [("a", "A", "/x", "p", False), ("b", "C++ & B", "/x", "b", True), ("c", "C", "/x", "c", True)])
        for bad in ("a", "A=/x", "v=/x", "a=/x, a=/y", "a b=/x", "=/x",
                    "a+shared=/x",                              # the default vault is never private: no flag
                    "a=/x, b+public=/x", "a=/x, b+=/x", "a=/x, b+Shared=/x", "a=/x, b:Work+shared=/x#b",
                    "a=/x, +shared=/x", "a=/x, v+shared=/x"):
            with self.assertRaises(SystemExit, msg=bad):
                sites.parse(bad)
        made, _ = sites.build({"KURA_VAULTS": "p=/x#personal, client=/x#client", "KURA_VAULT_CLIENT_OBSIDIAN": "Work Client"},
                              "", "/d", "personal", "", "", "token")
        self.assertEqual([(x.name, x.title, x.default, x.prefix, x.obsidian) for x in made],
                         [("p", "P", True, "", "personal"), ("client", "Client", False, "/v/client", "Work Client")])


class FakeHister(kura.BaseHTTPRequestHandler):
    calls = []
    refuse = False                  # answer 500 to every delete

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHister.calls.append((self.path, self.headers.get("Origin"), body))
        refused = FakeHister.refuse and self.path == "/api/delete"
        self.send_response(500 if refused else 200 if self.headers.get("Origin") == "hister://" else 403)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


class PushTest(unittest.TestCase):
    def setUp(self):
        import push
        self.srv = kura.ThreadingHTTPServer(("127.0.0.1", 0), FakeHister)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        FakeHister.calls = []
        self.db = os.path.join(TMP, "push-%s.sqlite3" % self.id().rsplit(".", 1)[-1])
        self.p = push.Push("http://127.0.0.1:%d" % self.srv.server_address[1], self.db, "personal", "https://kura.test")
        self.notes = kura.pages.visible(kura.state.vault)

    def tearDown(self):
        self.srv.shutdown()

    def test_push_then_nothing(self):
        r = self.p.run_once(kura.state.vault, self.notes)
        self.assertEqual((r["pushed"], r["deleted"], r["complete"]), (4, 0, True))
        adds = [b for path, origin, b in FakeHister.calls if path == "/api/add"]
        self.assertTrue(all(o == "hister://" for _, o, _ in FakeHister.calls))
        lantern = next(b for b in adds if b["title"] == "Lantern")
        self.assertEqual(lantern["url"], "https://kura.test/n/Projects/Lantern")      # a card note -> its Kura page
        self.assertEqual(lantern["label"], "vault")
        meta = lantern["metadata"]                    # Hister's convention: source + vault_* keys
        self.assertEqual((meta["source"], meta["vault_card"]), ("vault", "lantern"))
        self.assertEqual(meta["vault_path"], "personal/Projects/Lantern.md")
        self.assertIs(meta["vault_published"], True)                    # publish: true
        self.assertTrue(meta["ignore_skip_rules"])
        self.assertFalse({"path", "card", "published"} & set(meta))    # Hister's own `published` is a date
        self.assertNotIn("<script", lantern["html"])
        self.assertNotIn("---", lantern["text"][:3])
        FakeHister.calls = []
        r = self.p.run_once(kura.state.vault, self.notes)
        self.assertEqual((r["pushed"], FakeHister.calls), (0, []))

    def test_new_doc_version_resends_every_note_once(self):
        import push
        self.p.run_once(kura.state.vault, self.notes)
        FakeHister.calls = []
        old = push.DOC_V
        try:
            push.DOC_V = old + 1
            self.assertEqual(self.p.run_once(kura.state.vault, self.notes)["pushed"], len(self.notes))
            self.assertEqual(self.p.run_once(kura.state.vault, self.notes)["pushed"], 0)
        finally:
            push.DOC_V = old
        self.assertFalse([c for c in FakeHister.calls if c[0] == "/api/delete"])    # re-sent in place, same URLs

    def test_moved_url_and_deleted_note(self):
        self.p.run_once(kura.state.vault, self.notes)
        self.p.save_row("Projects/Lantern.md", "https://konbini.test/p/lantern", "old", "ok")   # a row seeded earlier
        self.p.save_row("Gone.md", "https://kura.test/n/Gone", "x", "ok")
        FakeHister.calls = []
        r = self.p.run_once(kura.state.vault, self.notes)
        paths = [(path, b.get("url") or b.get("query")) for path, _, b in FakeHister.calls]
        self.assertIn(("/api/delete", 'url:"https://konbini.test/p/lantern"'), paths)
        self.assertIn(("/api/add", "https://kura.test/n/Projects/Lantern"), paths)
        self.assertIn(("/api/delete", 'url:"https://kura.test/n/Gone"'), paths)
        self.assertEqual((r["pushed"], r["deleted"]), (1, 2))
        self.assertNotIn("Gone.md", self.p.rows())

    def test_unreachable_hister_retries(self):
        import push
        p = push.Push("http://127.0.0.1:9", self.db + "-down", "personal", "https://kura.test")
        r = p.run_once(kura.state.vault, self.notes)
        self.assertFalse(r["complete"])
        self.assertTrue(p.pending)
        self.assertEqual(p.rows(), {})


class SharedVaultTest(unittest.TestCase):
    """A vault marked +shared in KURA_VAULTS is treated like the default vault at its /v/<name>/ addresses: stored on
    devices, external links, its own feed, pushed to Hister. It still gets no Niwa or Konbini links (those rooms read
    the default vault only), and a client that never sends `vault` still sees the default vault alone. An unflagged
    vault next to it stays private."""

    @classmethod
    def setUpClass(cls):
        import push
        cls.hister = kura.ThreadingHTTPServer(("127.0.0.1", 0), FakeHister)
        threading.Thread(target=cls.hister.serve_forever, daemon=True).start()
        FakeHister.calls = []
        env = {"KURA_VAULTS": "personal=%s#personal, work:Work Notes=%s#work, team+shared:Team=%s#team" % (REPO, REPO, REPO)}
        st = kura.State.__new__(kura.State)
        st.sites, st.sources = sites.build(env, "", REPO, "personal", "", "", "token")
        st.default, st.by_name = st.sites[0], {x.name: x for x in st.sites}
        st.index, st.loop_error = search.Index(), None
        cls.db = os.path.join(TMP, "push-shared.sqlite3")
        st.push = push.Push("http://127.0.0.1:%d" % cls.hister.server_address[1], cls.db, "personal", "https://kura.test")
        cls.saved = kura.state, kura.shell.SITES
        kura.state, kura.shell.SITES = st, st.sites
        st.sync()
        cls.st = st

    @classmethod
    def tearDownClass(cls):
        kura.state, kura.shell.SITES = cls.saved
        cls.hister.shutdown()

    def test_flags(self):
        team, work = self.st.by_name["team"], self.st.by_name["work"]
        self.assertEqual((team.shared, team.private, team.prefix), (True, False, "/v/team"))
        self.assertEqual((work.shared, work.private), (False, True))
        self.assertEqual((self.st.default.shared, self.st.default.private), (False, False))

    def test_vaults_and_status(self):
        _, d = getj("/api/vaults")
        self.assertEqual([(v["name"], v["default"], v["private"]) for v in d["vaults"]],
                         [("personal", True, False), ("work", False, True), ("team", False, False)])
        st = json.loads(get("/api/status", user=None)[1])
        self.assertEqual(sorted(st["vaults"]["team"]), ["error", "head", "notes", "synced_at"])   # shown like the default
        self.assertEqual(st["vaults"]["team"]["notes"], 3)
        self.assertEqual(st["vaults"]["work"], {"error": None})            # still only its error

    def test_reader_keeps_a_shared_vault(self):
        st, h, body = fetch("/v/team/n/Guides/Onboarding")
        self.assertEqual(st, 200)
        self.assertNotEqual(h["Cache-Control"], "no-store")
        self.assertIn('<span class="chip">Team</span>', body)              # named, not yellow
        self.assertNotIn('class="chip private"', body.split("<main", 1)[-1])
        self.assertNotIn("View in Niwa", body)                             # publish: true: Niwa reads the default vault only
        self.assertNotIn("View Card in Konbini", body)
        self.assertIn("machiya-offline", body)                             # offline: true counts here
        self.assertIn('href="/v/team/n/Lantern"', body)
        api.SHIORI_LINKS = True
        try:
            self.assertNotIn("shiori://save-links", fetch("/v/team/n/Guides/Onboarding")[2])   # the scheme has no vault
        finally:
            api.SHIORI_LINKS = False
        for path in ("/v/team/", "/v/team/recent", "/v/team/f/Guides", "/v/team/t/topic/team", "/v/team/a/tpic.png"):
            self.assertNotEqual(fetch(path)[1]["Cache-Control"], "no-store", path)
        for path in ("/v/team/n/Archive/Old", "/v/team/f/Archive"):
            self.assertEqual(fetch(path)[1]["Cache-Control"], "no-store", path)   # Archive/ is never kept, in any vault
        self.assertEqual(fetch("/v/work/n/Runbooks/Zebrafish%20deploy")[1]["Cache-Control"], "no-store")

    def test_api_and_links(self):
        _, d = getj("/api/note?path=Guides/Onboarding.md&vault=team")
        self.assertEqual((d["vault"], d["url"]), ("team", "https://kura.test/v/team/n/Guides/Onboarding"))
        self.assertEqual((d["published"], d["card_url"]), (False, None))
        self.assertEqual([x["url"] for x in d["external_links"]],
                         ["https://team.example.com/wiki", "https://team.example.com/bare"])
        st, d = getj("/api/links?vault=team&folder=Guides")
        self.assertEqual((st, d["total"], d["notes"][0]["url"]), (200, 1, "https://kura.test/v/team/n/Guides/Onboarding"))
        self.assertEqual(get("/api/links?vault=work&folder=Runbooks")[0], 400)
        _, d = getj("/api/note?path=Runbooks/Zebrafish%20deploy.md&vault=work")
        self.assertEqual(d["external_links"], [])

    def test_no_vault_parameter_still_means_the_default_vault(self):
        for path in ("/api/search?q=okapi", "/api/search?q=vault:team+okapi", "/api/recent?limit=50", "/api/tags"):
            body = get(path)[1]
            self.assertNotIn("okapi", body.lower(), path)
            self.assertNotIn("/v/", body, path)
        self.assertEqual(getj("/api/search?q=okapi&vault=team")[1]["total"], 2)

    def test_feeds(self):
        st, h, body = fetch("/v/team/feed.xml")
        self.assertEqual((st, h["Content-Type"].split(";")[0]), (200, "application/rss+xml"))
        self.assertIn("<title>Kura · Team</title>", body)
        self.assertIn("<link>https://kura.test/v/team/n/Guides/Onboarding</link>", body)
        self.assertNotIn("Zebrafish", body)
        self.assertNotIn("okapi", get("/feed.xml")[1].lower())             # the default feed stays the default vault's
        self.assertIn("Okapi", get("/v/team/feed.xml?q=okapi")[1])
        st, h, _ = fetch("/v/work/feed.xml")
        self.assertEqual((st, h["Cache-Control"]), (404, "no-store"))

    def test_an_agent_reads_the_shared_vault(self):
        from vaultkit import identity
        folder = os.path.join(TMP, "identity-shared")
        tokens = write_identity(folder)
        saved, kura.IDENTITY = kura.IDENTITY, identity.Identity(os.path.join(folder, "identity.toml"), "kura")
        try:
            agent = {"Authorization": "Bearer " + tokens["mcp"]}
            _, _, body = as_("/api/vaults", **agent)
            self.assertEqual([v["name"] for v in json.loads(body)["vaults"]], ["personal", "team"])   # not work
            self.assertEqual(json.loads(as_("/api/search?q=okapi&vault=team", **agent)[2])["total"], 2)
            self.assertEqual(as_("/v/team/n/Guides/Onboarding", **agent)[0], 200)
            self.assertEqual(as_("/v/work/n/Runbooks/Zebrafish%20deploy", **agent)[0], 404)
        finally:
            kura.IDENTITY = saved

    def test_offline_pins(self):
        urls = getj("/api/offline")[1]["urls"]
        self.assertIn("/v/team/n/Guides/Onboarding", urls)
        self.assertIn("/n/Notes/Tea%20brewing", urls)
        self.assertNotIn("/v/team/n/Archive/Old", urls)
        self.assertFalse([u for u in urls if u.startswith("/v/work/")])

    def test_service_worker_stores_only_shared_prefixes(self):
        import re
        text = fetch("/sw.js")[2]
        cfg = json.loads(text[text.index("machiyaSW(") + 10:].rsplit(")", 1)[0])
        net = [re.compile(x) for x in cfg["network"]]
        note, asset = re.compile(cfg["notes"]["match"]), [re.compile(x) for x in cfg["assetMatch"]]
        for p in ("/v/work/n/Lantern", "/v/work/", "/v/work/a/wpic.png", "/v/teams/n/x", "/v/te/n/x", "/v/nope/",
                  "/v/team-b/n/x", "/search", "/v/team/search"):
            self.assertTrue(any(r.search(p) for r in net), p)              # network-only: never stored
        for p in ("/v/team/n/Lantern", "/v/team/", "/v/team/f/Guides", "/n/Lantern"):
            self.assertFalse(any(r.search(p) for r in net), p)
        self.assertTrue(note.search("/v/team/n/Lantern") and note.search("/n/Lantern"))
        self.assertFalse(note.search("/v/work/n/Lantern"))
        self.assertTrue(any(r.search("/v/team/a/tpic.png") for r in asset))
        self.assertFalse(any(r.search("/v/work/a/wpic.png") for r in asset))

    def test_push_sends_shared_and_withdraws_when_private(self):
        adds = {b["url"]: b for path, _, b in FakeHister.calls if path == "/api/add"}
        self.assertIn("https://kura.test/n/Projects/Lantern", adds)
        doc = adds["https://kura.test/v/team/n/Guides/Onboarding"]
        self.assertEqual((doc["label"], doc["metadata"]["source"], doc["metadata"]["vault_path"]),
                         ("vault", "vault", "team/Guides/Onboarding.md"))
        self.assertTrue(doc["metadata"]["ignore_skip_rules"])
        self.assertIn('href="https://kura.test/v/team/n/Lantern"', doc["html"])
        self.assertFalse([u for u in adds if "/v/work/" in u])
        self.assertNotIn("Zebrafish", json.dumps(FakeHister.calls))
        self.assertEqual(len(adds), 4 + 3)
        r = self.st.push.last
        self.assertEqual((r["pushed"], r["complete"], self.st.push.pending), (7, True, False))
        FakeHister.calls = []
        self.st.push.run([(self.st.default, kura.pages.visible(self.st.default), self.st.default.head)], [])
        gone = sorted(b["query"] for path, _, b in FakeHister.calls if path == "/api/delete")
        self.assertEqual(len(gone), 3)                                     # team made private: its documents leave Hister
        self.assertTrue(all("/v/team/n/" in q for q in gone))
        self.assertFalse([k for k in self.st.push.rows() if k.startswith("/v/")])
        self.assertEqual(len(self.st.push.rows()), 4)                      # the default vault's rows are untouched
        self.st.push.save_row("/v/gone/X.md", "https://kura.test/v/gone/n/X", "x", "ok")
        FakeHister.refuse = True
        try:
            r = self.st.push.run([(self.st.default, kura.pages.visible(self.st.default), self.st.default.head)], [])
        finally:
            FakeHister.refuse = False
        self.assertIn("/v/gone/X.md", self.st.push.rows())                # Hister said no: kept, and tried again
        self.assertEqual((r["complete"], self.st.push.pending), (False, True))
        self.st.push.run([(self.st.default, kura.pages.visible(self.st.default), self.st.default.head)], [])
        self.assertNotIn("/v/gone/X.md", self.st.push.rows())
        with self.assertRaises(ValueError):
            self.st.push.run_once(self.st.by_name["work"], kura.pages.visible(self.st.by_name["work"]))


def write_identity(folder):
    """An identity file (vaultkit.identity) for the tests: the owner, an agent with a token, a person allowed the work
    vault only, a person with nothing, and a service without a kura grant. -> {name: token}."""
    from vaultkit import identity
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "session.key"), "w") as f:
        f.write("k" * 43)
    data = {"version": 1, "session_key_file": "session.key", "principals": {
        "owner": {"id": "ownerid000000001", "kind": "person", "owner": True, "tailscale": ["owner@test"]},
        "mcp": {"kind": "agent", "grants": {"kura": ["read"]}},
        "partner": {"id": "partnerid0000001", "kind": "person", "tailscale": ["partner@test"],
                    "grants": {"kura": {"read": True, "vaults": ["work"]}}},
        "nobody": {"id": "nobodyid00000001", "kind": "person", "tailscale": ["nobody@test"]},
        "niwa": {"kind": "service", "grants": {"konbini": ["read"]}}}}
    tokens = {n: identity.new_token(data, n, "test") for n in ("mcp", "niwa")}
    identity.write_file(os.path.join(folder, "identity.toml"), data)
    return tokens


def as_(path, **headers):
    """GET with these headers only (no owner login added): (status, headers, body)."""
    req = urllib.request.Request(BASE + path, headers={k.replace("_", "-"): v for k, v in headers.items()})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


class IdentityTest(unittest.TestCase):
    """Machiya's identity file (MACHIYA_IDENTITY_FILE, vaultkit.identity): who may read Kura, and which vaults. An agent
    gets the default and shared vaults only, whatever it asks; a private vault is readable only when its grant names
    it, and one that isn't granted answers like one that doesn't exist."""

    @classmethod
    def setUpClass(cls):
        from vaultkit import identity
        folder = os.path.join(TMP, "identity")
        cls.tokens = write_identity(folder)
        cls.saved = kura.IDENTITY
        kura.IDENTITY = identity.Identity(os.path.join(folder, "identity.toml"), "kura")

    @classmethod
    def tearDownClass(cls):
        kura.IDENTITY = cls.saved

    def agent(self, path):
        return as_(path, Authorization="Bearer " + self.tokens["mcp"])

    def test_who_gets_in(self):
        self.assertEqual(as_("/", Tailscale_User_Login="owner@test")[0], 200)
        self.assertEqual(self.agent("/api/search?q=bamboo")[0], 200)
        st, _, body = as_("/", Tailscale_User_Login="stranger@test")
        self.assertEqual((st, body.strip()), (403, "this login has no access"))
        self.assertEqual(as_("/", Tailscale_User_Login="nobody@test")[0], 403)    # in the file, granted nothing
        self.assertEqual(as_("/api/search?q=x", Authorization="Bearer " + self.tokens["niwa"])[0], 403)   # no kura grant
        self.assertEqual(as_("/", Authorization="Bearer mch_zzzzzz_nope")[0], 401)
        self.assertEqual(as_("/", Authorization="Bearer mch_zzzzzz_nope", Tailscale_User_Login="owner@test")[0], 401)
        self.assertEqual(as_("/")[0], 401)                                                     # no proof at all
        self.assertEqual(as_("/api/status")[0], 200)                                           # still open

    def test_the_full_status_is_the_owners(self):
        self.assertIn("repo", json.loads(as_("/api/status", Tailscale_User_Login="owner@test")[2]))
        self.assertNotIn("repo", json.loads(self.agent("/api/status")[2]))
        self.assertEqual(json.loads(self.agent("/api/status")[2])["vaults"]["work"], {"error": None})

    def test_an_agent_never_reads_a_private_vault(self):
        _, _, body = self.agent("/api/vaults")
        self.assertEqual([v["name"] for v in json.loads(body)["vaults"]], ["personal"])
        for path in ("/api/search?q=zebrafish&vault=work", "/api/recent?vault=work", "/api/tags?vault=work",
                     "/api/note?path=Runbooks/Zebrafish%20deploy.md&vault=work", "/api/search?q=x&vault=personal,work"):
            st, _, body = self.agent(path)
            self.assertEqual(st, 400, path)
            self.assertNotIn("Zebrafish", body, path)
        for path in ("/api/search?q=zebrafish&vault=all", "/api/search?q=vault:work+zebrafish&vault=all",
                     "/api/recent?vault=all&limit=50", "/api/offline", "/feed.xml?q=zebrafish",
                     "/search?q=zebrafish&vaults=all", "/"):
            st, _, body = self.agent(path)
            self.assertEqual(st, 200, path)
            self.assertNotIn("Zebrafish", body, path)
            self.assertNotIn("/v/work", body, path)
        for path in ("/v/work/", "/v/work/n/Runbooks/Zebrafish%20deploy", "/v/work/search?q=zebrafish",
                     "/v/work/a/wpic.png", "//v/work/n/Runbooks/Zebrafish%20deploy", "/%76/work/"):
            st, _, body = self.agent(path)
            self.assertEqual(st, 404, path)                                                 # like no such vault
            self.assertNotIn("Zebrafish runbook", body, path)                               # (the 404 echoes the path)
            self.assertEqual(body, as_(path.replace("work", "nope"), Authorization="Bearer " + self.tokens["mcp"])[2]
                             .replace("nope", "work"), path)                               # the same page, word for word
        st, _, body = self.agent("/api/notes?urls=https://kura.test/v/work/n/Runbooks/Zebrafish%20deploy")
        self.assertEqual(json.loads(body)["notes"], [])                                    # only echoed in missing
        self.assertNotIn('class="vaults"', self.agent("/")[2])                             # one vault: no switch

    def test_a_person_granted_one_private_vault(self):
        partner = dict(Tailscale_User_Login="partner@test")
        st, _, body = as_("/api/search?q=zebrafish&vault=work", **partner)
        self.assertEqual((st, json.loads(body)["total"]), (200, 1))
        self.assertEqual(as_("/v/work/n/Runbooks/Zebrafish%20deploy", **partner)[0], 200)
        for path in ("/", "/n/Projects/Lantern", "/feed.xml", "/api/search?q=bamboo", "/api/note?path=Projects/Lantern.md"):
            self.assertIn(as_(path, **partner)[0], (400, 404), path)                       # the default isn't granted
        self.assertEqual([v["name"] for v in json.loads(as_("/api/vaults", **partner)[2])["vaults"]], ["work"])
        _, _, body = as_("/api/search?q=lantern&vault=all", **partner)
        self.assertEqual({n["vault"] for n in json.loads(body)["results"]}, {"work"})

    def test_the_owner_sees_everything(self):
        owner = dict(Tailscale_User_Login="owner@test")
        self.assertEqual(json.loads(as_("/api/search?q=zebrafish&vault=work", **owner)[2])["total"], 1)
        self.assertEqual(as_("/v/work/n/Runbooks/Zebrafish%20deploy", **owner)[0], 200)
        self.assertIn('href="/v/work/"', as_("/", **owner)[2])

    def test_default_vault_only_holds_for_agents_too(self):
        """DefaultVaultOnlyTest's rule with an agent's token instead of the owner's login."""
        for q in ("vault:work zebrafish", "title:zebrafish", "tag:topic/client", "folder:Runbooks"):
            st, _, body = self.agent("/api/search?q=%s&vault=all" % q.replace(" ", "+"))
            self.assertEqual((st, json.loads(body)["total"]), (200, 0), q)

    def test_header_mode_needs_the_identity_file(self):
        with self.assertRaises(SystemExit):
            kura.auth_mode("header")
        self.assertEqual(kura.auth_mode("header", "/etc/machiya/identity.toml"), "header")

    def test_identity_settings_at_start(self):
        """With an identity file a header mode refuses a public bind unless a proxy is the only way in."""
        folder = os.path.join(TMP, "identity-start")
        write_identity(folder)
        code = "import kura; print(kura.IDENTITY.auth, kura.IDENTITY.room)"
        env = {"MACHIYA_IDENTITY_FILE": os.path.join(folder, "identity.toml"), "KURA_VAULTS": ""}
        r = AuthTest.run_kura(self, dict(env, KURA_BIND="0.0.0.0"), code)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("127.0.0.1", r.stderr)
        r = AuthTest.run_kura(self, dict(env, KURA_BIND="127.0.0.1"), code)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "tailscale kura"), r.stderr)
        r = AuthTest.run_kura(self, dict(env, KURA_BIND_BEHIND_PROXY="1"), code)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reserved_vault_names(self):
        for bad in ("a=/x, shared=/x", "a=/x, default:Default=/x", "a=/x, shared+shared=/x"):
            with self.assertRaises(SystemExit, msg=bad):
                sites.parse(bad)


def send(path, body=b"", method="POST", **headers):
    """A POST (or PUT) with these headers only: (status, headers, body). Content-Length is urllib's own."""
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={k.replace("_", "-"): v for k, v in headers.items()})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def session_of(headers):
    """The machiya_session=... pair from a response's Set-Cookie headers ("" when none sets a value)."""
    for c in headers.get_all("Set-Cookie") or []:
        pair = c.split(";", 1)[0]
        if pair.startswith("machiya_session=") and pair != "machiya_session=":
            return pair
    return ""


class SignInTest(unittest.TestCase):
    """The built-in sign-in, Shiori's pairing and per-user preferences (vaultkit.signin, identity plan phase 6), with an
    identity file whose sign-in is on (KURA_SIGNIN=1). The sign-in routes sit before the gate, /api/prefs after it."""

    SITE = "https://kura.test"
    FORM = "application/x-www-form-urlencoded"

    @classmethod
    def setUpClass(cls):
        from vaultkit import identity
        cls.folder = os.path.join(TMP, "identity-signin")
        cls.tokens = write_identity(cls.folder)
        path = os.path.join(cls.folder, "identity.toml")
        import tomllib
        with open(path, "rb") as f:
            data = tomllib.load(f)
        data["principals"]["reader"] = {"id": "readerid00000001", "kind": "person", "password":
                                        identity.hash_password("lantern-reader"), "grants": {"kura": ["read"]}}
        data["principals"]["other"] = {"id": "otherid000000001", "kind": "person", "password":
                                       identity.hash_password("lantern-other"), "grants": {"kura": ["read"]}}
        data["principals"]["nobody"]["password"] = identity.hash_password("lantern-nobody")
        cls.code, _ = identity.new_pairing(data, "reader", "iPhone")
        identity.write_file(path, data)
        cls.path = path
        cls.saved = (kura.IDENTITY, kura.ORIGINS, kura.PREFS_DB)
        kura.PREFS_DB = os.path.join(TMP, "prefs-signin.sqlite3")

    @classmethod
    def tearDownClass(cls):
        kura.IDENTITY, kura.ORIGINS, kura.PREFS_DB = cls.saved

    def setUp(self):
        from vaultkit import identity
        kura.IDENTITY = identity.Identity(self.path, "kura", signin=True)      # fresh throttles for every test
        kura.ORIGINS = (self.SITE,)

    def sign_in(self, name, password, origin=SITE, nxt="/"):
        from urllib.parse import urlencode
        body = urlencode({"name": name, "password": password, "next": nxt}).encode()
        return send("/signin", body, Origin=origin, Content_Type=self.FORM)

    def test_the_sign_in_page_has_its_stylesheet(self):
        """A signed-out browser loads the shared UI the sign-in page needs, and nothing of Kura's own."""
        self.assertEqual(as_("/static/machiya.css")[0], 200)
        self.assertEqual(as_("/static/icons/kura.svg")[0], 200)
        for path in ("/static/kura.css", "/static/kura.js", "/", "/api/search?q=bamboo"):
            self.assertEqual(as_(path)[0], 401, path)
        from vaultkit import identity
        kura.IDENTITY = identity.Identity(self.path, "kura", signin=False)          # sign-in off: all behind the gate
        self.assertEqual(as_("/static/machiya.css")[0], 401)

    def test_sign_in_read_sign_out(self):
        st, _, body = as_("/signin?next=/n/Projects/Lantern")
        self.assertEqual(st, 200)
        self.assertIn('action="/signin"', body)
        self.assertIn('value="/n/Projects/Lantern"', body)
        st, h, body = self.sign_in("reader", "lantern-reader", nxt="/n/Projects/Lantern")
        self.assertEqual((st, h["Location"]), (303, "/n/Projects/Lantern"), body)
        cookie = session_of(h)
        self.assertTrue(cookie)
        self.assertIn("Secure", h["Set-Cookie"])
        st, _, body = as_("/n/Projects/Lantern", Cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn("Lantern", body)
        self.assertNotIn("Zebrafish", as_("/api/search?q=zebrafish&vault=all", Cookie=cookie)[2])   # default and shared only
        st, _, body = as_("/settings", Cookie=cookie)
        self.assertIn('action="/signout"', body)
        self.assertIn("Signed in as reader", body)
        self.assertNotIn('action="/signout"', as_("/settings", Tailscale_User_Login="owner@test")[2])
        st, h, _ = send("/signout", Origin=self.SITE, Cookie=cookie)
        self.assertEqual((st, h["Location"]), (303, "/"))
        cleared = [c for c in h.get_all("Set-Cookie") if c.startswith("machiya_session=")]
        self.assertEqual(len(cleared), 1)                                   # only the clearing one, nothing renewed
        self.assertIn("Max-Age=0", cleared[0])

    def test_wrong_password_and_cross_site(self):
        st, h, body = self.sign_in("reader", "wrong")
        self.assertEqual((st, session_of(h)), (401, ""))
        self.assertIn("Wrong name or password.", body)
        self.assertNotIn("wrong", body.replace("Wrong", ""))
        st, h, _ = self.sign_in("reader", "lantern-reader", origin="https://evil.test")
        self.assertEqual((st, session_of(h)), (403, ""))
        st, h, _ = send("/signin", b"name=reader&password=lantern-reader", Content_Type=self.FORM)   # no Origin at all
        self.assertEqual((st, session_of(h)), (403, ""))
        cookie = session_of(self.sign_in("reader", "lantern-reader")[1])
        self.assertEqual(send("/signout", Origin="https://evil.test", Cookie=cookie)[0], 403)
        self.assertEqual(self.sign_in("reader", "lantern-reader", nxt="//evil.test/x")[1]["Location"], "/")

    def test_a_page_without_a_session_links_to_sign_in(self):
        st, h, body = as_("/n/Projects/Lantern?p=x")
        self.assertEqual((st, h["Cache-Control"]), (401, "no-store"))
        self.assertIn('href="/signin?next=%2Fn%2FProjects%2FLantern%3Fp%3Dx"', body)
        self.assertNotIn("Work Notes", body)                               # nothing about the vaults
        self.assertNotIn("/v/work", body)
        st, h, body = as_("/api/search?q=lantern")
        self.assertEqual((st, h["Content-Type"].split(";")[0]), (401, "text/plain"))
        self.assertNotIn("/signin", as_("/feed.xml")[2])
        from vaultkit import identity
        kura.IDENTITY = identity.Identity(self.path, "kura")                 # sign-in off: no link, no form
        self.assertNotIn("/signin", as_("/")[2])
        self.assertEqual(as_("/signin")[0], 404)
        self.assertEqual(self.sign_in("reader", "lantern-reader")[0], 404)

    def test_over_plain_http(self):
        """Over http the same-origin check needs KURA_PUBLIC_URL: without it Host and Origin prove nothing."""
        from vaultkit import identity
        kura.IDENTITY = identity.Identity(self.path, "kura", signin=True, secure=False)
        kura.ORIGINS = ("http://kura.test",)
        st, h, _ = self.sign_in("reader", "lantern-reader", origin="http://kura.test")
        self.assertEqual(st, 303)
        self.assertNotIn("Secure", h["Set-Cookie"])
        self.assertEqual(as_("/", Cookie=session_of(h))[0], 200)
        self.assertEqual(self.sign_in("reader", "lantern-reader", origin="http://evil.test")[0], 403)
        kura.ORIGINS = ()
        host = "127.0.0.1:%d" % SERVER.server_address[1]
        self.assertEqual(self.sign_in("reader", "lantern-reader", origin="http://" + host)[0], 403)

    def test_pairing(self):
        st, _, body = send("/api/pair", json.dumps({"code": "ZZZZ-ZZZZ", "device": "iPhone"}).encode(),
                           Content_Type="application/json")
        self.assertEqual(st, 401, body)
        st, _, body = send("/api/pair", json.dumps({"code": self.code.lower(), "device": "iPhone"}).encode(),
                           Content_Type="application/json")
        self.assertEqual(st, 200, body)
        d = json.loads(body)
        self.assertEqual(d["principal"], "reader")
        self.assertTrue(d["token"].startswith("mcd_"))
        device = {"Authorization": "Bearer " + d["token"]}
        st, _, body = as_("/api/search?q=bamboo", **device)
        self.assertEqual((st, json.loads(body)["total"]), (200, 1))
        self.assertEqual(as_("/v/work/n/Runbooks/Zebrafish%20deploy", **device)[0], 404)     # still its own grant
        self.assertEqual(send("/api/pair", b"{}", Content_Type="text/plain")[0], 415)

    def test_prefs(self):
        st, h, body = as_("/api/prefs", Tailscale_User_Login="owner@test")
        self.assertEqual((st, json.loads(body), h["Cache-Control"]), (200, {"prefs": {}}, "no-store"))
        reader = session_of(self.sign_in("reader", "lantern-reader")[1])
        other = session_of(self.sign_in("other", "lantern-other")[1])
        put = json.dumps({"prefs": {"theme": "night", "kura.previewpane": "false"}}).encode()
        st, _, body = send("/api/prefs", put, "PUT", Cookie=reader, Content_Type="application/json")
        self.assertEqual(st, 403, body)                                    # a cookie: same-origin only
        st, _, body = send("/api/prefs", put, "PUT", Cookie=reader, Origin="https://evil.test",
                           Content_Type="application/json")
        self.assertEqual(st, 403, body)
        st, _, body = send("/api/prefs", put, "PUT", Cookie=reader, Origin=self.SITE, Content_Type="application/json")
        self.assertEqual((st, json.loads(body)["prefs"]["theme"]), (200, "night"), body)
        self.assertEqual(json.loads(as_("/api/prefs", Cookie=reader)[2])["prefs"],
                         {"kura.previewpane": "false", "theme": "night"})
        self.assertEqual(json.loads(as_("/api/prefs", Cookie=other)[2])["prefs"], {})       # one principal's alone
        self.assertEqual(json.loads(as_("/api/prefs", Tailscale_User_Login="owner@test")[2])["prefs"], {})
        agent = {"Authorization": "Bearer " + self.tokens["mcp"]}
        st, _, body = send("/api/prefs", json.dumps({"prefs": {"theme": "day"}}).encode(), "PUT",
                           Content_Type="application/json", **agent)
        self.assertEqual(st, 200, body)                                    # a token needs no Origin
        self.assertEqual(json.loads(as_("/api/prefs", **agent)[2])["prefs"], {"theme": "day"})
        self.assertEqual(json.loads(as_("/api/prefs", Cookie=reader)[2])["prefs"]["theme"], "night")
        st, _, _ = send("/api/prefs", json.dumps({"prefs": {"theme": None}}).encode(), "PUT",
                        Content_Type="application/json", **agent)
        self.assertEqual(json.loads(as_("/api/prefs", **agent)[2])["prefs"], {})
        st, _, _ = send("/api/prefs", json.dumps({"prefs": {"Bad Key": "x"}}).encode(), "PUT",
                        Content_Type="application/json", **agent)
        self.assertEqual(st, 400)
        self.assertEqual(oct(os.stat(kura.PREFS_DB).st_mode & 0o777), "0o600")

    def test_prefs_need_read(self):
        nobody = session_of(self.sign_in("nobody", "lantern-nobody")[1])   # signed in, but no kura grant
        self.assertTrue(nobody)
        self.assertEqual(as_("/api/prefs", Cookie=nobody)[0], 403)
        self.assertEqual(send("/api/prefs", b'{"prefs": {}}', "PUT", Cookie=nobody, Origin=self.SITE,
                              Content_Type="application/json")[0], 403)
        self.assertEqual(as_("/api/prefs", Authorization="Bearer " + self.tokens["niwa"])[0], 403)
        self.assertEqual(as_("/api/prefs")[0], 401)
        self.assertEqual(send("/api/prefs", b'{"prefs": {}}', "PUT", Content_Type="application/json")[0], 401)

    def test_routes(self):
        self.assertEqual(send("/api/search", b"")[0], 405)                 # Kura is read-only
        self.assertEqual(send("/n/Projects/Lantern", b"", "PUT")[0], 405)
        self.assertEqual(send("/api/prefs", b"", "POST")[0], 405)
        self.assertEqual(send("/signin", b"", "PUT")[0], 405)
        st, _, _ = send("/signin", b"x" * (8 * 1024), Origin=self.SITE, Content_Type=self.FORM)
        self.assertEqual(st, 413)
        saved, kura.IDENTITY = kura.IDENTITY, None                         # no identity file: as before
        try:
            for path in ("/signin", "/signout", "/api/pair"):
                self.assertEqual(send(path, b"", Origin=self.SITE)[0], 404, path)
            self.assertEqual(send("/api/prefs", b"{}", "PUT", Origin=self.SITE, Content_Type="application/json")[0], 404)
            self.assertEqual(get("/api/prefs")[0], 404)
            self.assertEqual(get("/signin")[0], 404)
            self.assertEqual(get("/signin", user="stranger@test")[0], 403)  # the old gate first, as before
            self.assertEqual(send("/api/search", b"")[0], 405)
        finally:
            kura.IDENTITY = saved


def tearDownModule():
    SERVER.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
