"""Kura's full-text index: SQLite FTS5 in memory, rebuilt from the vault index whenever the synced commit changes
(a few hundred notes take well under a second, so there is nothing to persist or migrate).

Query syntax (docs/contracts/kura-api.md in machiya-kobo/machiya):
  words            all of them (AND)            -word      none of it
  "a phrase"       the phrase                   word*      a prefix
  title:word       in the title (title:shio* for suggestions)
  tag:x folder:x   filters, like the tag= / folder= parameters (a tag includes its nested tags)
  vault:x          only that vault. It narrows within the vaults the caller may search and never widens them
                   (the API's vault parameter, default: the default vault), so a query can't reach a work vault
Ranking: bm25 with the title weighted most, then tags, then the body. Snippets mark hits with \\x02 ... \\x03, which
api.py turns into escaped HTML with <mark>.
"""
import datetime
import re
import sqlite3
import threading
import time

from vaultkit import FRONT_RE

HIT_OPEN, HIT_CLOSE = "\x02", "\x03"
WEIGHTS = (0.0, 0.0, 10.0, 4.0, 1.0)     # vault, rel (unindexed), title, tags, body
SCHEMA = """
CREATE TABLE notes (vault TEXT, rel TEXT, title TEXT, folder TEXT, tags TEXT, created INTEGER, changed INTEGER,
    PRIMARY KEY (vault, rel));
CREATE VIRTUAL TABLE fts USING fts5(vault UNINDEXED, rel UNINDEXED, title, tags, body,
    tokenize = "unicode61 remove_diacritics 2", prefix = '2 3');
"""
TOKEN_RE = re.compile(r'(-?)(?:(title|tag|folder|vault):)?("[^"]*"?|\S+)')


def unix(day):
    """'2026-01-15' -> unix seconds at UTC midnight (None when it isn't a date)."""
    try:
        d = datetime.date.fromisoformat(str(day or "")[:10])
    except ValueError:
        return None
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp())


def plain(text):
    """A note's searchable text: no frontmatter, comments, markup; wikilinks and links become their labels."""
    body = FRONT_RE.sub("", text, count=1)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"!\[\[[^\]]*\]\]", " ", body)                                   # embeds
    body = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]",
                  lambda m: m.group(2) or m.group(1).split("/")[-1], body)          # [[target|label]]
    body = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", body)                           # images -> alt
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)                            # links -> text
    body = re.sub(r"<[^>]+>", " ", body)                                            # raw HTML tags
    body = re.sub(r"^\s{0,3}(#{1,6}|>|\s*[-*+]\s+\[[ xX]\]|\s*[-*+]|\s*\d+\.)\s*", "", body, flags=re.M)
    body = re.sub(r"[*_`~|]+", " ", body)
    return re.sub(r"[ \t]+", " ", body).strip()


def tag_words(tags):
    """'topic/hobby' is findable as the whole tag and as its parts."""
    out = []
    for t in tags:
        out.append(t)
        out.extend(p for p in t.split("/") if p)
    return " ".join(out)


def fts_term(word):
    """One word or phrase as an FTS5 string; a trailing * (outside quotes) keeps prefix matching."""
    prefix = word.endswith("*") and not word.startswith('"')
    w = word.rstrip("*") if prefix else word
    if w.startswith('"'):
        w = w.strip('"')
    w = w.replace('"', '""').strip()
    if not w:
        return None
    return '"%s"%s' % (w, "*" if prefix else "")


def parse(q):
    """-> (positive FTS terms, negative FTS terms, tags, folders, vaults)"""
    pos, neg, tags, folders, vaults = [], [], [], [], []
    for m in TOKEN_RE.finditer(q or ""):
        negate, field, word = m.group(1) == "-", m.group(2), m.group(3)
        if field == "tag":
            tags.append(word.strip('"').lstrip("#").strip("/"))
            continue
        if field == "folder":
            folders.append(word.strip('"').strip("/"))
            continue
        if field == "vault":
            vaults.append(word.strip('"').strip("/").lower())
            continue
        term = fts_term(word)
        if not term:
            continue
        if field == "title":
            term = "title : " + term
        (neg if negate else pos).append(term)
    return pos, neg, [t for t in tags if t], [f for f in folders if f], [v for v in vaults if v]


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Index:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.revision = None
        self.counts = {}                # {vault name: notes indexed}

    @property
    def count(self):
        return sum(self.counts.values())

    def rebuild(self, name, vault, visible):
        """Index `visible` (the notes to show) of the vault called `name` (a vaultkit.Vault, already indexed),
        replacing that vault's rows only."""
        rows, fts = [], []
        for n in visible:
            folder = n.rel.rsplit("/", 1)[0] if "/" in n.rel else ""
            changed = getattr(vault, "changed_at", {}).get(n.rel) or unix(vault.tended.get(n.rel))
            rows.append((name, n.rel, n.title, folder, "\n".join(n.tags), unix(n.planted), changed))
            fts.append((name, n.rel, n.title, tag_words(n.tags), plain(n.text)))
        with self.lock, self.db:
            self.db.execute("DELETE FROM notes WHERE vault = ?", (name,))
            self.db.execute("DELETE FROM fts WHERE vault = ?", (name,))
            self.db.executemany("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self.db.executemany("INSERT INTO fts VALUES (?, ?, ?, ?, ?)", fts)
            self.counts[name] = len(rows)

    def search(self, q, sort="relevance", limit=20, offset=0, tag="", folder="", vaults=()):
        """-> (total, [(vault, rel, snippet or None)], took_ms). `vaults`: the names the caller may search (none = no
        results); a `vault:` term in q narrows within them. Raises ValueError for a query FTS5 can't parse."""
        started = time.monotonic()
        pos, neg, tags, folders, asked = parse(q)
        allowed = [v for v in vaults if not asked or v in asked]
        if not allowed:
            return 0, [], 0
        if tag:
            tags.append(tag.strip().lstrip("#").strip("/"))
        if folder:
            folders.append(folder.strip().strip("/"))
        where, args = ["n.vault IN (%s)" % ",".join("?" * len(allowed))], list(allowed)
        for t in tags:
            where.append("(('\n' || n.tags || '\n') LIKE ? ESCAPE '\\' OR ('\n' || n.tags) LIKE ? ESCAPE '\\')")
            args += ["%\n" + like_escape(t) + "\n%", "%\n" + like_escape(t) + "/%"]
        for f in folders:
            where.append("(n.folder = ? OR n.folder LIKE ? ESCAPE '\\')")
            args += [f, like_escape(f) + "/%"]
        if neg:
            where.append("(n.vault, n.rel) NOT IN (SELECT vault, rel FROM fts WHERE fts MATCH ?)")
            args.append(" OR ".join(neg))
        if not pos and len(where) == 1:               # only the vault filter: nothing was asked for
            return 0, [], 0
        order = "n.changed DESC, n.title COLLATE NOCASE" if sort == "changed" else None
        try:
            with self.lock:
                if pos:
                    match = " AND ".join(pos)
                    cond = " AND ".join(["fts MATCH ?"] + where)
                    base = "FROM fts JOIN notes n ON n.rel = fts.rel AND n.vault = fts.vault WHERE " + cond
                    total = self.db.execute("SELECT count(*) " + base, [match] + args).fetchone()[0]
                    rows = self.db.execute(
                        "SELECT n.vault, n.rel, snippet(fts, -1, ?, ?, '…', 24) %s ORDER BY %s LIMIT ? OFFSET ?"
                        % (base, order or "bm25(fts, %s), n.changed DESC" % ", ".join(map(str, WEIGHTS))),
                        [HIT_OPEN, HIT_CLOSE] + [match] + args + [limit, offset]).fetchall()
                else:
                    base = "FROM notes n WHERE " + " AND ".join(where)
                    total = self.db.execute("SELECT count(*) " + base, args).fetchone()[0]
                    rows = self.db.execute("SELECT n.vault, n.rel, NULL %s ORDER BY %s LIMIT ? OFFSET ?"
                                           % (base, order or "n.changed DESC, n.title COLLATE NOCASE"),
                                           args + [limit, offset]).fetchall()
        except sqlite3.OperationalError as err:
            raise ValueError("can't search for that: %s" % err)
        return total, rows, int((time.monotonic() - started) * 1000)

    def recent(self, limit=20, offset=0, vaults=()):
        """-> (total, [(vault, rel)]) newest change first, in the given vaults."""
        if not vaults:
            return 0, []
        marks = ",".join("?" * len(vaults))
        with self.lock:
            total = self.db.execute("SELECT count(*) FROM notes WHERE vault IN (%s)" % marks, list(vaults)).fetchone()[0]
            rows = self.db.execute("SELECT vault, rel FROM notes WHERE vault IN (%s) "
                                   "ORDER BY changed DESC, title COLLATE NOCASE LIMIT ? OFFSET ?" % marks,
                                   list(vaults) + [limit, offset]).fetchall()
        return total, [(r[0], r[1]) for r in rows]
