"""The vault push: every note Kura serves goes into Hister as label `vault`, so Hister's own UI and its extension
find notes next to the pages they cite. Notes stay in Hister once pushed; Shiori reads notes from Kura's API instead.

- every note -> its Kura page, https://kura…/n/<slug> (project notes included; the card slug is kept in
  metadata.vault_card)
- metadata follows Hister's convention (source + <source>_* keys, as its importers do): source "vault", tags,
  vault_path, vault_published, vault_card and ignore_skip_rules. Plain `published` clashed with Hister's own
  metadata.published (an RFC3339 date its extractor writes).
- the hister_docs table (in KURA_DB) remembers what was pushed (URL + content hash), so a run pushes only new or
  changed notes and deletes the Hister documents of notes that were deleted, renamed or moved to another URL. The
  hash covers DOC_V, the URL, the published flag and the text: bump
  DOC_V when the document's shape changes, and every note is re-sent once.
- Hister's skip rules are expected to refuse the rooms' own hosts (the extension must never capture Kura/Konbini/Niwa
  pages); vault documents carry metadata.ignore_skip_rules, the per-document override.
- Every Hister call sends `Origin: hister://`. Only the default vault (its folder in the repo, KURA_REPO_SUBDIR) is pushed.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request

import api
import search

LABEL = "vault"
DOC_V = 2                           # the document's shape; 2 = the vault_* metadata keys
SCHEMA = """CREATE TABLE IF NOT EXISTS hister_docs (
    rel TEXT PRIMARY KEY, url TEXT, sha TEXT, pushed TEXT, status TEXT)"""


class Hister:
    """The two Hister calls the push needs, over HISTER_URL (for example http://hister:4433 on a shared Docker network)."""

    def __init__(self, url):
        self.api = url.rstrip("/")
        self.error = ""

    def call(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Origin": "hister://", "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.error = "hister unreachable: %s" % e
            return 0, None
        self.error = "" if status < 400 else "HTTP %d: %s" % (status, raw[:200].decode("utf-8", "replace"))
        return status, raw

    def add(self, doc):
        status, _ = self.call("POST", "/api/add", doc)
        return 200 <= status < 300

    def delete(self, url):
        status, _ = self.call("POST", "/api/delete", {"query": 'url:"%s"' % url.replace('"', "%22")})
        return 200 <= status < 300


class Push:
    def __init__(self, hister_url, db_path, subdir, base_url):
        self.h = Hister(hister_url)
        self.subdir, self.base = subdir, base_url.rstrip("/")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.commit()
        self.lock = threading.Lock()
        self.last = {}
        self.pending = True             # run on the first sync, and again after an unreachable Hister

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
        meta = {"source": "vault", "tags": note.tags,
                "vault_path": "%s/%s" % (self.subdir, note.rel) if self.subdir else note.rel,
                "vault_published": note.published, "vault_card": api.card_slug(note), "ignore_skip_rules": True}
        doc = {"url": url, "title": note.title, "text": body, "label": LABEL, "metadata": meta}
        try:
            doc["html"] = api.sanitize(vault.render(note, "", False, mode="all"), self.base)
        except Exception:
            pass
        added = search.unix(note.planted)
        if added:
            doc["added"] = added
        return doc

    def run_once(self, vault, notes, head=""):
        """Push `notes` (the notes Kura shows), delete what's gone. Returns the summary."""
        if getattr(vault, "private", False):      # work vaults never reach Hister (docs/contracts/kura-api.md, rule 2)
            raise ValueError("refusing to push the private vault %r to Hister" % getattr(vault, "name", ""))
        known = self.rows()
        current = {n.rel: n for n in notes}
        pushed = deleted = failed = 0
        complete = True
        for rel, note in current.items():
            url = api.note_url(self.base, note)
            sha = hashlib.sha1(("%s\n%s\n%s\n%s" % (DOC_V, url, note.published, note.text)).encode()).hexdigest()
            old = known.get(rel)
            if old and old["sha"] == sha:
                continue
            if old and old["url"] != url:
                self.h.delete(old["url"])
                deleted += 1
            ok = self.h.add(self.doc(vault, note, url))
            if not ok and self.h.error.startswith("hister unreachable"):
                complete = False
                break                   # nothing recorded; the next sync tries again
            # a refused note (e.g. Hister's sensitive-content check) is retried when it changes
            self.save_row(rel, url, sha, "ok" if ok else "refused: " + self.h.error[:120])
            pushed += ok
            failed += not ok
        if complete:
            for rel in set(known) - set(current):
                if self.h.delete(known[rel]["url"]) or not self.h.error:
                    self.save_row(rel, None, None, None)
                    deleted += 1
        self.pending = not complete
        self.last = {"at": int(datetime.datetime.now().timestamp()), "pushed": pushed, "deleted": deleted,
                     "failed": failed, "head": head, "complete": complete}
        if pushed or deleted or failed or not complete:
            print("kura push: %d pushed, %d deleted, %d refused%s" % (
                pushed, deleted, failed, "" if complete else " (Hister unreachable; will retry)"), flush=True)
        return self.last

    def status(self):
        rows = self.rows()
        return dict(self.last, docs=len(rows), refused=sum(1 for r in rows.values() if (r["status"] or "") != "ok"),
                    error=self.h.error or None)
