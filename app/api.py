"""Kura's JSON API: the shapes in docs/contracts/kura-api.md (machiya-kobo/machiya), plus the card link, the HTML
sanitizer for /api/note and the RSS feed.

A note in a list: path, slug, vault, folder, title, url, summary, tags, created, changed (unix seconds),
published, card_url, and snippet (search results only: escaped HTML whose only markup is <mark>). A note in a work
vault is always published: false and card_url: null, and its url is https://kura…/v/<vault>/n/<slug>.

external_links (/api/note, /api/links): the http, https, gemini and gopher links of a note's body, for Shiori's "Save
Links". Default vault only: a work-vault note has none, so no work link can enter a save flow.
"""
import html
import os
import re
from email.utils import formatdate
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from vaultkit import BOARD_STATUSES, _str, note_status
import search
import shell

KONBINI_URL = ""                    # kura.py sets it; "" = no card links
PUBLIC_URL = ""                     # kura.py sets it: Kura's own address, left out of external_links
SHIORI_LINKS = False                # kura.py sets it (KURA_SHIORI_LINKS): "Save links in Shiori" on a note


def slugify(name):
    """The board card slug rule: lowercase, runs of other characters become one hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "card"


def card_slug(n):
    """The Konbini card slug of a project note (a board status in `status:` or the older `board:`, or the
    type/project tag), else ''."""
    if not (note_status(n.fm) in BOARD_STATUSES or "type/project" in n.tags):
        return ""
    return _str(n.fm.get("project")) or slugify(os.path.splitext(os.path.basename(n.rel))[0])


def card_url(n):
    slug = card_slug(n) if KONBINI_URL else ""
    return "%s/p/%s" % (KONBINI_URL, quote(slug)) if slug else None


def changed(vault, n):
    """Unix time of the note's latest commit (exact, from kura.changed_times), else the day's midnight UTC."""
    return getattr(vault, "changed_at", {}).get(n.rel) or search.unix(vault.tended.get(n.rel))


def note_url(base, n, prefix=""):
    return "%s%s/n/%s" % (base, prefix, quote(n.slug))


def snippet_html(text):
    """FTS5 snippet (hits between \\x02 and \\x03) -> escaped HTML with <mark>."""
    return (html.escape(text or "", quote=False)
            .replace(search.HIT_OPEN, "<mark>").replace(search.HIT_CLOSE, "</mark>"))


def note_json(base, vault, n, snippet=None):
    """`vault` is a sites.Site: its name and prefix, and whether it is private (no Niwa or Konbini links)."""
    folder = n.rel.rsplit("/", 1)[0] if "/" in n.rel else ""
    out = {"path": n.rel, "slug": n.slug, "vault": vault.name, "folder": folder, "title": n.title,
           "url": note_url(base, n, vault.prefix), "summary": n.description, "tags": n.tags,
           "created": search.unix(n.planted), "changed": changed(vault, n),
           "published": bool(n.published) and not vault.private, "card_url": None if vault.private else card_url(n)}
    if snippet is not None:
        out["snippet"] = snippet_html(snippet)
    return out


def link_json(base, n, prefix=""):
    return {"path": n.rel, "title": n.title, "url": note_url(base, n, prefix)}


# -- external links ----------------------------------------------------------------------------------------------

SCHEMES = r"(?:https?|gemini|gopher)://"           # what Save Links can take; mailto:, obsidian: and the rest are not links to save
BARE_URL = re.compile(SCHEMES + r"[^\s<>\"']+", re.I)
TAG_URL = re.compile(r"^<(" + SCHEMES + r"[^>\s]+)>$", re.I)   # <gemini://host/x>: Markdown only autolinks http(s), the rest arrives as a tag
CLOSERS = {")": "(", "]": "[", "}": "{"}


def trim_url(url):
    """A bare URL without the sentence around it: trailing .,;:!? and any closing bracket it doesn't open."""
    while url:
        last = url[-1]
        if last in ".,;:!?":
            url = url[:-1]
        elif last in CLOSERS and url.count(last) > url.count(CLOSERS[last]):
            url = url[:-1]
        else:
            break
    return url
_links = {}                         # {(id of the vault, note path): [links]}, valid for one commit of that vault
_links_head = {}


class LinkFinder(HTMLParser):
    """The http, https, gemini and gopher links of rendered note HTML, in order: <a href> (markdown links and raw HTML
    alike), <gemini://…> autolinks and bare URLs in the text, which Obsidian shows as links too. It reads the rendered
    HTML before the sanitizer, which drops these schemes from /api/note's `html`. Nothing inside <code>/<pre> counts (a
    fence shows a URL, it doesn't link it)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found, self.code, self.href, self.text = [], 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag in ("code", "pre"):
            self.code += 1
        elif tag == "a" and not self.code:
            href = (dict(attrs).get("href") or "").strip()
            self.href, self.text = (href if re.match(SCHEMES, href, re.I) else None), []
        elif not self.code and self.href is None:
            m = TAG_URL.match(self.get_starttag_text() or "")
            if m:
                self.found.append({"url": m.group(1), "text": m.group(1)})

    def handle_endtag(self, tag):
        if tag in ("code", "pre"):
            self.code = max(0, self.code - 1)
        elif tag == "a" and self.href is not None:
            self.found.append({"url": self.href, "text": " ".join("".join(self.text).split())})
            self.href = None

    def handle_data(self, data):
        if self.code:
            return
        if self.href is not None:
            self.text.append(data)
        else:
            for m in BARE_URL.finditer(data):
                url = trim_url(m.group(0))
                if re.fullmatch(SCHEMES, url, re.I) is None:
                    self.found.append({"url": url, "text": url})


def note_links(g, n):
    """Every http, https, gemini or gopher link in the note's body (frontmatter excluded), in order, duplicates kept.
    [] for a work vault."""
    if g.private:
        return []
    if _links_head.get(id(g)) != g.head:
        for key in [k for k in _links if k[0] == id(g)]:
            del _links[key]
        _links_head[id(g)] = g.head
    key = (id(g), n.rel)
    if key not in _links:
        finder = LinkFinder()
        finder.feed(g.render(n, "", False, mode="all", prefix=g.prefix))
        finder.close()
        _links[key] = finder.found
    return _links[key]


def own_hosts(base=""):
    """Kura's own hosts and the other rooms' (MACHIYA_ROOMS, the sister settings): not a note's external links."""
    urls = [base, PUBLIC_URL, KONBINI_URL, shell.NIWA_URL] + list(shell.rooms().values())
    return {h.lower() for h in (urlsplit(u).hostname for u in urls if u) if h}


def external_links(g, n, base=""):
    """[{"url", "text"}]: the note's links without Kura's and the rooms' hosts, deduplicated by url in order."""
    skip, seen, out = own_hosts(base), set(), []
    for link in note_links(g, n):
        host = (urlsplit(link["url"]).hostname or "").lower()
        if host and host not in skip and link["url"] not in seen:
            seen.add(link["url"])
            out.append(dict(link))
    return out


def folder_links(g, folder, base="", visible=None):
    """[(note, external links)] of the default vault's notes under `folder` (subfolders included), by path; notes
    without external links are left out. `visible`: the notes that may be served (Templates/ and the like are not)."""
    folder = folder.strip("/")
    under = sorted((n for n in (visible if visible is not None else g.notes.values()) if n.rel.startswith(folder + "/")),
                   key=lambda n: n.rel)
    return [(n, links) for n, links in ((n, external_links(g, n, base)) for n in under) if links]


# -- sanitizer -------------------------------------------------------------------------------------------------

ALLOWED = {"a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "details", "div", "dl", "dt", "em",
           "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark",
           "ol", "p", "pre", "s", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td",
           "tfoot", "th", "thead", "tr", "u", "ul"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math",
                     "form", "textarea", "select", "button", "head", "title"}
ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title", "width", "height"},
         "td": {"colspan", "rowspan", "align"}, "th": {"colspan", "rowspan", "align"}, "ol": {"start"}}
COMMON = {"class"}
SAFE_URL = re.compile(r"^(https?:|mailto:|obsidian:|/|#|\.\.?/|[^:/?#]+(?:[/?#]|$))", re.I)
VOID = {"br", "hr", "img"}


class Sanitizer(HTMLParser):
    """Allow-list HTML: known tags and attributes only, no scripts/frames/styles/event handlers, safe URL schemes;
    relative links and images made absolute on `base` (Shiori renders the HTML outside Kura)."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.skip = base.rstrip("/") + "/", [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        kept = []
        for k, v in attrs:
            if k not in ATTRS.get(tag, set()) | COMMON or v is None:
                continue
            if k in ("href", "src"):
                v = v.strip()
                if not SAFE_URL.match(v):
                    continue
                if not re.match(r"^(https?:|mailto:|obsidian:|#)", v, re.I):
                    v = urljoin(self.base, v)
            kept.append(' %s="%s"' % (k, html.escape(v, quote=True)))
        self.out.append("<%s%s>" % (tag, "".join(kept)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in DROP_WITH_CONTENT and self.skip:
            self.skip -= 1

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in ALLOWED and tag not in VOID:
            self.out.append("</%s>" % tag)

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def sanitize(markup, base):
    s = Sanitizer(base)
    s.feed(markup)
    s.close()
    return "".join(s.out)


# -- feed ------------------------------------------------------------------------------------------------------

def rss(base, title, notes_with_dates):
    """RSS 2.0: [(note, changed unix or None)] of the default vault (the feed never carries a work vault)."""
    items = "".join(
        "<item><title>%s</title><link>%s</link><guid isPermaLink=\"true\">%s</guid>%s<description>%s</description></item>"
        % (html.escape(n.title), html.escape(note_url(base, n)), html.escape(note_url(base, n)),
           ("<pubDate>%s</pubDate>" % formatdate(when, usegmt=True)) if when else "", html.escape(n.description))
        for n, when in notes_with_dates)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel><title>%s</title><link>%s/</link>'
            "<description>Notes in Kura</description>%s</channel></rss>\n" % (html.escape(title), html.escape(base), items))
