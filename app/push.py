"""The vault push: every note Kura serves goes into Hister as label `vault`, so Hister's own UI and its extension
find notes next to the pages they cite. Notes stay in Hister once pushed; Shiori reads notes from Kura's API instead.

- every note -> its Kura page, https://kura…/n/<slug> (project notes included; the card slug is kept in
  metadata.vault_card); a shared vault's notes -> https://kura…/v/<name>/n/<slug>, same label and metadata
- metadata follows Hister's convention (source + <source>_* keys, as its importers do): source "vault", tags,
  vault_path, vault_published, vault_card and ignore_skip_rules. Plain `published` clashed with Hister's own
  metadata.published (an RFC3339 date its extractor writes).
- the hister_docs table (in KURA_DB) remembers what was pushed (URL + content hash), so a run pushes only new or
  changed notes and deletes the Hister documents of notes that were deleted, renamed or moved to another URL. The
  hash covers DOC_V, the URL, the published flag and the text: bump
  DOC_V when the document's shape changes, and every note is re-sent once. A row's key is the note's path in the
  default vault, /v/<name>/<path> in a shared one (a path never starts with /).
- Private vaults are never pushed (run_once refuses them), and run() withdraws the documents of a vault that is no
  longer shared (made private, renamed or removed from KURA_VAULTS): fail closed.
- Hister's skip rules are expected to refuse the rooms' own hosts (the extension must never capture Kura/Konbini/Niwa
  pages); vault documents carry metadata.ignore_skip_rules, the per-document override.
- Reconcile: Hister can lose documents Kura believes it holds (a cleanup, a reset), and an unchanged note is never
  sent again. So a full run checks each `ok` row against Hister (`HEAD /api/document?url=`; GET if HEAD isn't
  allowed) at start and then daily, forgets the rows whose document is gone, and the same run sends them again
  (`push.missing` in /api/status is the last count). A caller-or-moment failure stops the check, as it stops a push.
- Every Hister call sends `Origin: hister://`. The default vault and the shared vaults are pushed, nothing else.
- With KURA_HISTER_TOKEN_FILE every call also sends the owner's Hister token as `X-Access-Token`. The file is read on
  every call through vaultkit's histerauth.hister_headers (a regenerated token needs no restart); a file that is set
  but empty or unreadable stops the call (fail closed: nothing goes out without the token Hister will want). The
  token is never logged, put in a URL or argv or an error text, and a redirect is never followed, so it can't go to
  another host.
"""
import datetime
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import api
import search
from vaultkit import histerauth

RECONCILE_EVERY = 24 * 3600        # seconds between checks that Hister still holds what Kura sent
RECONCILE_LIMIT = 300              # seconds a check may take before it stops (the next run starts over)
RETRY_HTTP = (401, 403, 408, 425, 429)     # about the caller or the moment, not the note: never "refused"
RETRY_ROW = re.compile(r"^refused: (hister unreachable|HTTP (401|403|408|425|429|5[0-9]{2})\b)")
LABEL = "vault"
DOC_V = 2                           # the document's shape; 2 = the vault_* metadata keys
SCHEMA = """CREATE TABLE IF NOT EXISTS hister_docs (
    rel TEXT PRIMARY KEY, url TEXT, sha TEXT, pushed TEXT, status TEXT)"""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                 # a 3xx is an error answer: the token never follows to another address


OPENER = urllib.request.build_opener(NoRedirect)


class Hister:
    """The two Hister calls the push needs, over HISTER_URL (for example http://hister:4433 on a shared Docker network).
    `token_file`: KURA_HISTER_TOKEN_FILE, the owner's Hister token ('' = send none)."""

    def __init__(self, url, token_file=""):
        self.api = url.rstrip("/")
        self.token_file = token_file
        self.error = ""
        self.retry = False          # the last call failed for a reason that isn't about the document (try again later)
        self.head_ok = True         # HEAD /api/document works (else GET)

    def call(self, method, path, body=None, timeout=30):
        self.retry = False
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(histerauth.hister_headers(self.token_file))     # Origin, and X-Access-Token (re-read when it changes)
        if self.token_file and "X-Access-Token" not in headers:         # set but empty or unreadable: fail closed
            self.error = "hister unreachable: no usable token in KURA_HISTER_TOKEN_FILE"      # "unreachable": retried
            self.retry = True
            return 0, None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers=headers)
        try:
            with OPENER.open(req, timeout=timeout) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.error = "hister unreachable: %s" % e
            self.retry = True
            return 0, None
        self.error = "" if status < 300 else "HTTP %d: %s" % (status, raw[:200].decode("utf-8", "replace"))
        self.retry = status in RETRY_HTTP or status >= 500      # not signed in (yet), throttled, or Hister is struggling
        return status, raw

    def add(self, doc):
        status, _ = self.call("POST", "/api/add", doc)
        return 200 <= status < 300

    def exists(self, url):
        """Is this exact URL a document in Hister? True or False; None when Hister can't say now (self.retry, or an
        answer that is neither 200 nor 404): the caller stops."""
        path = "/api/document?url=" + urllib.parse.quote(url, safe="")
        status, _ = self.call("HEAD" if self.head_ok else "GET", path)
        if self.head_ok and status in (405, 501):               # no HEAD here: ask with GET from now on
            self.head_ok = False
            status, _ = self.call("GET", path)
        if status == 200:
            return True
        if status == 404:
            self.error = ""                                     # "not there" is an answer, not an error to show
            return False
        return None

    def delete(self, url):
        status, _ = self.call("POST", "/api/delete", {"query": 'url:"%s"' % url.replace('"', "%22")})
        return 200 <= status < 300


def scope(vault):
    """The key prefix of a vault's rows: "" for the default vault (its rows predate shared vaults), else /v/<name>/."""
    prefix = getattr(vault, "prefix", "")
    return prefix + "/" if prefix else ""


def in_scope(key, prefix):
    return key.startswith(prefix) if prefix else not key.startswith("/v/")


class Push:
    def __init__(self, hister_url, db_path, subdir, base_url, token_file=""):     # subdir: the default vault's folder
        self.h = Hister(hister_url, token_file)
        self.subdir, self.base = subdir, base_url.rstrip("/")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.commit()
        self.lock = threading.Lock()
        self.last = {}
        self.pending = True             # run on the first sync, and again after an unreachable Hister
        self.reconciled = None          # when the last complete reconcile ended (time.time()); None: not yet
        self.missing = None             # how many documents the last reconcile found gone
        self.reconcile_every = RECONCILE_EVERY

    def rows(self):
        with self.lock:
            return {r[0]: {"url": r[1], "sha": r[2], "status": r[4]}
                    for r in self.db.execute("SELECT rel, url, sha, pushed, status FROM hister_docs")}

    def save_row(self, rel, url, sha, status):
        with self.lock, self.db:
            if url is None:
                self.db.execute("DELETE FROM hister_docs WHERE rel = ?", (rel,))
            else:
                self.db.execute("INSERT OR REPLACE INTO hister_docs VALUES (?, ?, ?, ?, ?)",
                                (rel, url, sha, datetime.datetime.now().isoformat(timespec="seconds"), status))

    def doc(self, vault, note, url):
        body = search.FRONT_RE.sub("", note.text, count=1).strip()
        subdir = getattr(vault, "subdir", self.subdir) if getattr(vault, "prefix", "") else self.subdir
        meta = {"source": "vault", "tags": note.tags,
                "vault_path": "%s/%s" % (subdir, note.rel) if subdir else note.rel,
                "vault_published": note.published, "vault_card": api.card_slug(note), "ignore_skip_rules": True}
        doc = {"url": url, "title": note.title, "text": body, "label": LABEL, "metadata": meta}
        try:
            doc["html"] = api.sanitize(vault.render(note, "", False, mode="all", prefix=getattr(vault, "prefix", "")),
                                       self.base)
        except Exception:
            pass
        added = search.unix(note.planted)
        if added:
            doc["added"] = added
        return doc

    def run_once(self, vault, notes, head=""):
        """Push `notes` (the notes Kura shows) of the default vault or a shared one, delete what's gone. Returns the
        summary."""
        if getattr(vault, "private", False):      # private vaults never reach Hister (docs/contracts/kura-api.md, rule 2)
            raise ValueError("refusing to push the private vault %r to Hister" % getattr(vault, "name", ""))
        key = scope(vault)
        known = {k[len(key):]: v for k, v in self.rows().items() if in_scope(k, key)}
        current = {n.rel: n for n in notes}
        pushed = deleted = failed = 0
        complete = True
        for rel, note in current.items():
            url = api.note_url(self.base, note, getattr(vault, "prefix", ""))
            sha = hashlib.sha1(("%s\n%s\n%s\n%s" % (DOC_V, url, note.published, note.text)).encode()).hexdigest()
            old = known.get(rel)
            if old and old["sha"] == sha and not RETRY_ROW.match(old["status"] or ""):
                continue                # sent, or refused for the note's own sake (until it changes); a refusal that was
                                        # about the caller (HTTP 401 before the token was set) is sent again
            if old and old["url"] != url:
                self.h.delete(old["url"])
                deleted += 1
            ok = self.h.add(self.doc(vault, note, url))
            if not ok and self.h.retry:
                complete = False
                break                   # nothing recorded; the next sync tries again
            # a refused note (e.g. Hister's sensitive-content check) is retried when it changes
            self.save_row(key + rel, url, sha, "ok" if ok else "refused: " + self.h.error[:120])
            pushed += ok
            failed += not ok
        if complete:
            for rel in set(known) - set(current):
                if self.h.delete(known[rel]["url"]) or not self.h.error:
                    self.save_row(key + rel, None, None, None)
                    deleted += 1
        self.pending = not complete
        self.last = {"at": int(datetime.datetime.now().timestamp()), "pushed": pushed, "deleted": deleted,
                     "failed": failed, "head": head, "complete": complete}
        if pushed or deleted or failed or not complete:
            print("kura push: %d pushed, %d deleted, %d refused%s" % (
                pushed, deleted, failed, "" if complete else " (Hister didn't take them; will retry)"), flush=True)
        return self.last

    def withdraw(self, shared):
        """Delete from Hister what was pushed for a vault that isn't shared any more (not in `shared`, the names of the
        shared vaults now configured). A row stays until Hister confirms the delete. -> (deleted, complete)."""
        deleted, complete = 0, True
        for k, row in self.rows().items():
            if k.startswith("/v/") and k[3:].split("/", 1)[0] not in shared:
                if not self.h.delete(row["url"]):
                    complete = False                    # kept; the next run tries again
                    if self.h.retry:
                        break
                    continue
                self.save_row(k, None, None, None)
                deleted += 1
        if deleted:
            print("kura push: withdrew %d notes of vaults that are no longer shared" % deleted, flush=True)
        return deleted, complete

    def due(self):
        """Is a run needed: something to send again, or the reconcile is due (at start, then daily)."""
        return self.pending or self.reconciled is None or time.time() - self.reconciled >= self.reconcile_every

    def reconcile(self):
        """Check every `ok` row against Hister and forget the rows whose document is gone, so the run that follows sends
        them again. -> (checked, missing, complete). Stops at once when Hister can't answer (a failure about the caller
        or the moment, or anything but 200 and 404): complete is False and the next run starts over."""
        checked = missing = 0
        started = time.monotonic()
        for k, row in sorted(self.rows().items()):
            if (row["status"] or "") != "ok" or not row["url"]:
                continue
            if time.monotonic() - started > RECONCILE_LIMIT:
                return checked, missing, False
            here = self.h.exists(row["url"])
            if here is None:
                return checked, missing, False
            checked += 1
            if not here:
                self.save_row(k, None, None, None)
                missing += 1
        return checked, missing, True

    def run(self, jobs, shared):
        """jobs: [(vault, notes, head)], the default vault first, then the shared vaults that are ready; `shared`: the
        names of every shared vault configured (one still being read keeps its documents). Withdraws first, then
        pushes each vault. Returns the summary of the whole run."""
        deleted, complete = self.withdraw(set(shared))
        total = {"pushed": 0, "deleted": deleted, "failed": 0}
        checking = complete and (self.reconciled is None or time.time() - self.reconciled >= self.reconcile_every)
        report = None
        if checking:
            checked, gone, done = self.reconcile()
            if done:
                self.reconciled, self.missing = time.time(), gone
                report = (gone, checked)
            else:
                complete = False                        # Hister couldn't say: the next sync checks again
        for vault, notes, head in jobs:
            r = self.run_once(vault, notes, head)
            for k in total:
                total[k] += r[k]
            complete = complete and r["complete"]
        self.pending = not complete
        self.last = dict(total, at=int(datetime.datetime.now().timestamp()), head=jobs[0][2] if jobs else "",
                         complete=complete)
        if report:
            print("kura push: reconcile: %d missing%s (%d checked)" % (report[0], ", re-pushed" if report[0] else "", report[1]),
                  flush=True)
        return self.last

    def status(self):
        rows = self.rows()
        return dict(self.last, docs=len(rows), refused=sum(1 for r in rows.values() if (r["status"] or "") != "ok"),
                    missing=self.missing, error=self.h.error or None)
