"""Kura's reader pages (蔵, the storehouse): every note in the vault, at the base URL Kura is served on.

The search box reads Kura's own full-text index (search.py); Niwa and Konbini are optional links.

Niwa (the garden) is the curated set of PUBLISHED notes, the thing that may go public. Kura is the
plain reader for EVERY note: every [[wikilink]] works (to its kura page), backlinks come from the whole vault, and
there is no garden decoration beyond one status line ("in the garden" with a link, or "not published"). Publishing
stays in the garden UI. Modern pages only; Templates/ is left out.

Pages: /  (search, folders, recently changed) · /f/<folder> · /n/<path> · /recent · /search?q= · /t/<tag> · /a/<image>
"""
import os
from urllib.parse import quote

import api
import shell
from shell import ICON, e
from vaultkit import relative

HIDDEN = ("Templates/",)            # never listed or served
NEVER_STORED = ("Archive/",)        # served, but never kept on a device (Cache-Control: no-store)
RECENT = 25


def visible(g):
    g.index()
    return [n for n in g.notes.values() if not n.rel.startswith(HIDDEN)]


def top(ctx, g, current, subtitle="", q="", everywhere=False, focus=False):
    """The header (shell.header): nav, the vault switch, the Rooms switcher, the settings gear and the search pill
    (`q`: the query on /search, "" elsewhere). `g` is the vault being read (a sites.Site)."""
    return shell.header(g, current, subtitle, q, everywhere, focus)


def npage(ctx, g, title, body, current="", head=""):
    return shell.page(ctx, g, title, body, current, head)


def never_stored(rel):
    """A note (or folder) that must never be kept on a device: its pages answer Cache-Control: no-store."""
    return (rel.rstrip("/") + "/").startswith(NEVER_STORED)


def pinned(g, n):
    """`offline: true` (not under Archive/, and never in a private vault): the service worker keeps the note for good."""
    return n.fm.get("offline") is True and not never_stored(n.rel) and not g.private


def crumbs(g, rel):
    parts = rel.split("/")[:-1]
    out, acc = ['<a href="%s/">%s</a>' % (g.prefix, "Kura" if g.default else e(g.title))], ""
    for p in parts:
        acc = acc + "/" + p if acc else p
        out.append('<a href="%s/f/%s">%s</a>' % (g.prefix, quote(acc), e(p)))
    return " / ".join(out)


def note_li(g, n, when=None, show_folder=True):
    date = when if when is not None else g.tended.get(n.rel, "")
    folder = os.path.dirname(n.rel) if show_folder else ""
    return ('<li><a class="ntl thing is-note" href="%s/n/%s">%s</a>%s%s%s</li>'
            % (g.prefix, quote(n.slug), e(n.title),
               (' <span class="nfolder">%s</span>' % e(folder)) if folder else "",
               (' <span class="tended" title="%s">%s</span>' % (e(date), e(relative(date)))) if date else "",
               ('<p class="summary">%s</p>' % e(n.description)) if n.description else ""))


def recent_notes(g, limit):
    notes = visible(g)
    return sorted(notes, key=lambda n: (g.tended.get(n.rel, ""), n.title.lower()), reverse=True)[:limit]


def missing(ctx, g, what):
    """Not found, with the header and tab bar (an installed app has no back button to escape a bare page)."""
    return npage(ctx, g, "Not Found", top(ctx, g, "") + shell.house.message(
        "Not Found", "Kura has nothing at %s." % what, [(g.prefix + "/search", "Search"), (g.prefix + "/", "Home")]))


def child_folders(g, notes, prefix):
    """{sub-folder name: its notes, newest first} for the notes under `prefix` ('' = a loose note)."""
    groups = {}
    for n in notes:
        rest = n.rel[len(prefix):]
        groups.setdefault(rest.split("/")[0] if "/" in rest else "", []).append(n)
    for members in groups.values():
        members.sort(key=lambda n: g.tended.get(n.rel, ""), reverse=True)
    return sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0].lower()))


def fold_link(g, path, name, members, cls="", depth=0):
    return ('<a class="kfold%s" href="%s/f/%s"%s>%s<span class="kfn"><b>%s</b><small>%s</small></span>'
            '<span class="kfc">%d</span></a>'
            % (cls, g.prefix, quote(path), (' style="--d:%d"' % depth) if depth else "", ICON["folder" if name else "note"],
               e(name.replace("-", " ") or "Loose Notes"), e(", ".join(n.title for n in members[:3])), len(members)))


def tree(g, notes, prefix, open_path, depth=0):
    """Left column: the folders, with the open folder's ancestors expanded."""
    rows = []
    for name, members in child_folders(g, notes, prefix):
        if not name and prefix:
            continue
        path = prefix + name
        here = open_path is not None and open_path == path
        along = open_path is not None and name and open_path.startswith(path + "/")
        rows.append("<li>" + fold_link(g, path, name, members, " here" if here else " along" if along else "", depth))
        if name and (here or along):
            sub = tree(g, members, path + "/", open_path, depth + 1)
            if sub:
                rows.append(sub)
        rows.append("</li>")
    return '<ul class="kfolds">%s</ul>' % "".join(rows) if rows else ""


def note_row(g, n, sel=None, show_folder=True, snippet=None, chip=None):
    """Middle column: one note (the link opens it; on wide screens kura.js previews it instead). `chip`: the vault
    to name on the row (a search across vaults; g is the note's own vault)."""
    date = g.tended.get(n.rel, "")
    meta = [e(os.path.dirname(n.rel))] if show_folder and "/" in n.rel else []
    if chip is not None:
        meta.insert(0, '<span class="chip%s">%s</span>' % (" private" if chip.private else "", e(chip.title)))
    if date:
        meta.append('<span title="%s">%s</span>' % (e(date), e(relative(date))))
    text = n.description if snippet is None else snippet
    return ('<li><a class="kn%s" href="%s/n/%s"><b>%s</b>%s%s</a></li>'
            % (" sel" if sel is n else "", g.prefix, quote(n.slug), e(n.title),
               ('<span class="kmeta">%s</span>' % " &middot; ".join(meta)) if meta else "",
               ('<small>%s</small>' % e(text)) if text else ""))


def tag_link(g, t, count=None):
    return '<a class="tag" href="%s/t/%s">%s%s</a>' % (g.prefix, quote(t), e(t), (' <span class="kfc">%d</span>' % count) if count else "")


def tag_notes(g, t):
    """Notes carrying tag `t` or one nested under it (topic -> topic/hobby, like Obsidian), newest first."""
    return sorted((n for n in visible(g) if any(x == t or (x.startswith(t + "/") and not x.endswith("/")) for x in n.tags)),
                  key=lambda n: (g.tended.get(n.rel, ""), n.title.lower()), reverse=True)


def tags_index(ctx, g):
    """/t/: every tag with its note count, grouped by its first part (area, topic, machine, ...)."""
    counts = {}
    for n in visible(g):
        for t in set(n.tags):
            if not t.endswith("/"):                 # "topic/" = an unfilled template tag
                counts[t] = counts.get(t, 0) + 1
    groups = {}
    for t in counts:
        groups.setdefault(t.split("/")[0] if "/" in t else "", []).append(t)
    order = sorted(groups, key=lambda k: (k == "", {"area": 0, "topic": 1, "machine": 2}.get(k, 3), k))
    head = '<h2 class="ktitle">Tags <span class="kcount">%d</span></h2>%s' % (len(counts), "".join(
        '<h3 class="sechead">%s</h3><div class="tagcloud">%s</div>'
        % (('<a href="%s/t/%s">%s</a>' % (g.prefix, quote(k), e(k))) if k else "Other",
           "".join(tag_link(g, t, counts[t]) for t in sorted(groups[k])))
        for k in order))
    return columns(ctx, g, "Tags", "tags", None, head, lambda n: (), [], "", wide=True)


def tag(ctx, g, t, sel=""):
    t = t.strip("/")
    if not t:
        return tags_index(ctx, g)
    listed = tag_notes(g, t)
    if not listed:
        return None
    parts, acc = ['<a href="%s/t/">Tags</a>' % g.prefix], ""
    for p in t.split("/")[:-1]:
        acc = acc + "/" + p if acc else p
        parts.append('<a href="%s/t/%s">%s</a>' % (g.prefix, quote(acc), e(p)))
    children = {}
    for n in listed:
        for x in set(n.tags):
            if x.startswith(t + "/") and not x.endswith("/"):
                children[x] = children.get(x, 0) + 1
    cloud = '<div class="tagcloud">%s</div>' % "".join(tag_link(g, x, c) for x, c in sorted(children.items()))
    if len(children) > 12:                          # #topic has 100+: keep the note list in view
        cloud = '<details class="ktags"><summary>%d Nested Tags</summary>%s</details>' % (len(children), cloud)
    head = ('<p class="crumbs">%s</p><h2 class="ktitle">#%s <span class="kcount">%d</span></h2>%s'
            % (" / ".join(parts), e(t), len(listed), cloud if children else ""))
    return columns(ctx, g, "#" + t, "tags", None, head,
                   lambda n: (note_row(g, x, n) for x in listed), listed, sel)


def note_parts(g, n):
    """(heading, meta, body, linked-from) of a note: shared by the full page and the preview pane."""
    body = g.render(n, "", False, mode="kura", prefix=g.prefix).lstrip()
    if body.startswith("<h1") and "</h1>" in body:
        cut = body.index("</h1>") + 5
        heading, body = body[:cut], body[cut:]
    else:
        heading = "<h1>%s</h1>" % e(n.title)
    tended = g.tended.get(n.rel, "")
    meta = []
    if n.planted:
        meta.append('created <span title="%s">%s</span>' % (e(n.planted), e(relative(n.planted))))
    if tended:
        meta.append('changed <span title="%s">%s</span>' % (e(tended), e(relative(tended))))
    if not g.default:               # Niwa and Konbini read the default vault only: no garden, no board here
        meta.append('<span class="chip%s">%s</span>' % (" private" if g.private else "", e(g.title)))
    elif n.published:
        meta.append(('<a class="thing is-garden" href="%s/n/%s">View in Niwa</a>' % (e(shell.NIWA_URL), quote(n.slug)))
                    if shell.NIWA_URL else '<span class="chip">Published</span>')
    else:
        meta.append('<span class="chip">Not Published</span>')
    # shiori://save-links names a path, not a vault: Shiori reads it in the default vault
    if api.SHIORI_LINKS and g.default and api.external_links(g, n, api.PUBLIC_URL):
        meta.append('<a class="thing" href="shiori://save-links?path=%s">Save links in Shiori</a>' % e(quote(n.rel)))
    card = api.card_url(n) if g.default else None
    if card:
        meta.append('<a class="thing is-card" href="%s">View Card in Konbini</a>' % e(card))
    tags = " ".join(tag_link(g, t) for t in n.tags if t.startswith(("topic/", "area/", "machine/")) and not t.endswith("/"))
    back = sorted((g.notes[r] for r in g.backlinks.get(n.rel, ()) if r in g.notes and not r.startswith(HIDDEN)),
                  key=lambda x: x.title.lower())
    linked = ('<section class="gsec"><h3 class="sechead">Linked From</h3><ul class="garden-list plain">%s</ul></section>'
              % "".join(note_li(g, x) for x in back)) if back else ""
    return heading, " &middot; ".join(meta) + (("<br>" + tags) if tags else ""), body, linked


def obsidian_attr(g):
    """Another vault's Obsidian name, for kura.js's Edit in Obsidian (the default vault uses the device's setting)."""
    return "" if g.default else (' data-obsidian="%s"' % e(g.obsidian))


def preview(g, n):
    """The right-hand pane's content (also served alone at /preview/<slug> for kura.js)."""
    if n is None:
        return '<div class="empty kempty"><h2>No Note Selected</h2><p>Pick a note in the list to preview it here.</p></div>'
    heading, meta, body, linked = note_parts(g, n)
    return ('<div class="kphead"><p class="crumbs">%s</p><a class="kopen" href="%s/n/%s" title="Open the full note">'
            'Open %s</a></div><article class="note is-note" data-path="%s"%s><header class="nhead">%s<p class="nmeta">%s</p>'
            '</header><div class="nbody">%s</div></article>%s'
            % (crumbs(g, n.rel), g.prefix, quote(n.slug), ICON["open"], e(n.rel), obsidian_attr(g), heading, meta, body,
               linked))


def pick(g, listed, sel):
    """The note to preview: ?p=<slug> when it's a visible note, else the first one listed that may be stored on a
    device (so a list page never carries an Archive/ note's body unless one was asked for; kura.py then sends
    no-store)."""
    n = g.get(sel) if sel else None
    if n is None or n.rel.startswith(HIDDEN):
        n = next((x for x in listed if not never_stored(x.rel)), None)
    return n


def columns(ctx, g, title, current, open_path, head, rows, listed, sel, extra="", wide=False, preview_of=None,
            searching=None):
    """The three-column reader: folders | notes | preview (two columns on tablets, one on phones). `preview_of`:
    (vault, note) to preview instead of picking from `listed` (a search across vaults). `searching`: (query,
    everywhere, focus) on /search, whose pill is the page's field; its <main> then names its own classes
    (`live-main`) so kura.js can give them to any page the results are shown on as you type."""
    pane = shell.preview_pane(ctx) and not wide          # Reading → Preview Pane, off: the list has the room
    pg = g
    if preview_of:
        pg, n = preview_of
    else:
        n = pick(g, listed, sel) if pane else None
    items = "".join(rows(n))
    cls = "kgrid notes%s%s" % (" khome" if open_path is None and current == "kura" else "", "" if pane else " kwide")
    body = ('<main class="%s">%s<nav class="kside" aria-label="Folders"><h3 class="sechead">Folders</h3>%s</nav>'
            '<section class="klist">%s%s%s</section>%s</main>'
            % (cls, ('<div class="live-main" data-class="%s" hidden></div>' % cls) if searching else "",
               tree(g, visible(g), "", open_path), head, ('<ul class="kns">%s</ul>' % items) if items else "", extra,
               ('<aside class="kpreview" aria-label="Preview">%s</aside>' % preview(pg, n)) if pane else ""))
    q, everywhere, focus = searching or ("", False, False)
    return npage(ctx, g, title, top(ctx, g, current, q=q, everywhere=everywhere, focus=focus) + body, current)


def home(ctx, g, sel=""):
    listed = recent_notes(g, 40)
    return columns(ctx, g, "", "kura", None,
                   '<h3 class="sechead">Recently Changed</h3>', lambda n: (note_row(g, x, n) for x in listed), listed, sel,
                   '<p class="more"><a href="%s/recent">Everything Changed Recently &rsaquo;</a></p>' % g.prefix)


def folder(ctx, g, path, sel=""):
    path = path.strip("/")
    if path.startswith(tuple(h.rstrip("/") for h in HIDDEN)):
        return None
    prefix = path + "/" if path else ""
    notes = [n for n in visible(g) if n.rel.startswith(prefix)]
    if not notes:
        return None
    direct = sorted((n for n in notes if "/" not in n.rel[len(prefix):]),
                    key=lambda n: g.tended.get(n.rel, ""), reverse=True)
    subs = "".join("<li>%s</li>" % fold_link(g, prefix + name, name, members)
                   for name, members in child_folders(g, notes, prefix) if name) if prefix else ""
    title = path.split("/")[-1].replace("-", " ") if path else "Loose notes"
    head = ('<p class="crumbs">%s</p><h2 class="ktitle">%s <span class="kcount">%d</span></h2>%s'
            % (crumbs(g, prefix + "x"), e(title), len(notes),
               ('<ul class="kfolds ksubs">%s</ul>' % subs) if subs else ""))
    return columns(ctx, g, title, "", path, head,
                   lambda n: (note_row(g, x, n, False) for x in direct), direct, sel)


def note(ctx, g, n):
    heading, meta, body, linked = note_parts(g, n)
    main = ('<main class="garden notes"><article class="note is-note" data-path="%s"%s><p class="crumbs">%s</p>'
            '<header class="nhead">%s<p class="nmeta">%s</p></header><div class="nbody">%s</div></article>%s</main>'
            % (e(n.rel), obsidian_attr(g), crumbs(g, n.rel), heading, meta, body, linked))
    return npage(ctx, g, n.title, top(ctx, g, "") + main, head=shell.OFFLINE_PIN if pinned(g, n) else "")


def recent(ctx, g, sel=""):
    listed = recent_notes(g, 150)
    return columns(ctx, g, "Recent", "recent", None, '<h3 class="sechead">Recently Changed</h3>',
                   lambda n: (note_row(g, x, n) for x in listed), listed, sel)


def search(ctx, g, q, hits, total, error="", sel="", everywhere=False):
    """hits: [(site, note, plain-text snippet)] from the full-text index (search.py), best first: the vault being read,
    or with `everywhere` every vault (the owner's "All Vaults"; the response is then never stored)."""
    action = g.prefix + "/search"
    scope_sites = shell.sites() if everywhere else [g]
    box = ""
    if len(shell.sites()) > 1:
        other = ('<a href="%s?q=%s">%s Only</a>' % (action, quote(q), e(g.title))) if everywhere else \
            ('<a href="%s?q=%s&amp;vaults=all">All Vaults</a>' % (action, quote(q)))
        box = '<p class="muted scope">Searching <b>%s</b> &middot; %s</p>' % ("All Vaults" if everywhere else e(g.title), other)
    searching = (q, everywhere, not q)                          # the pill is the field; the empty page focuses it
    if not q:
        return columns(ctx, g, "Search", "search", None,
                       box + '<p class="muted">Titles and the full text of every note. <code>"a phrase"</code>, '
                       '<code>-word</code>, <code>word*</code>, <code>title:</code>, <code>tag:</code>, '
                       '<code>folder:</code> and <code>vault:</code> work too.</p>',
                       lambda n: (), [], sel, searching=searching)
    title_hits = [(x, n) for x in scope_sites for n in visible(x) if q.lower() in n.title.lower()][:20]
    seen = {(x.name, n.rel) for x, n in title_hits}
    text_hits = []
    for x, n, snip in hits:
        if n is not None and not n.rel.startswith(HIDDEN) and (x.name, n.rel) not in seen:
            seen.add((x.name, n.rel))
            text_hits.append((x, n, (snip or n.description or "")[:220]))
    shown = [(x, n) for x, n in title_hits] + [(x, n) for x, n, _ in text_hits]
    mine = [n for x, n in shown if x is g]                     # the pane previews these (the first hit, in any vault)
    first = next(((x, n) for x, n in shown if not never_stored(n.rel)), None) if everywhere else None
    if everywhere:
        sel = ""                                                # a ?p= slug is ambiguous across vaults
    def row(x, n, sel_n, snip=None):
        return note_row(x, n, sel_n, snippet=snip, chip=x if x is not g else None) if snip is not None \
            else note_row(x, n, sel_n, chip=x if x is not g else None)

    def rows(sel_n):
        if not title_hits and not text_hits:        # nothing at all: one empty state instead of two headings
            return ['<li class="empty"><h2>%s</h2><p>%s</p></li>' % (
                ("Invalid Search", e(error)) if error else ("No Results", "No note's title or text matches this search."))]
        out = []
        if title_hits:
            out.append('<li class="khd">Titles</li>')
            out.extend(row(x, n, sel_n) for x, n in title_hits)
        out.append('<li class="khd">Full Text%s</li>' % ((" (%d)" % total) if total else ""))
        out.extend(row(x, n, sel_n, snip) for x, n, snip in text_hits)
        if not text_hits:
            out.append('<li class="muted">%s</li>' % e(error or "Nothing else in the note text."))
        return out
    return columns(ctx, g, q, "search", None, box, rows, mine, sel, shell.house.handoff(q, shell.rooms()),
                   preview_of=first, searching=searching)
