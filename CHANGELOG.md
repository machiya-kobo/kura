# Changelog

Kura follows [Semantic Versioning](https://semver.org). Before 1.0, a new feature, a changed default or setting, or a
changed API field bumps the minor number; a fix or a wording change bumps the patch. `/api/status` and Settings → About
show the running version.

## 0.5.0

- A vault can be marked shared in `KURA_VAULTS`: `name+shared[:Title]=source#subdir`. A shared vault is treated like the
  default one at its `/v/<name>/` addresses: kept for offline reading (`offline: true` counts, `/api/offline` lists it),
  `external_links` and `/api/links`, its own `/v/<name>/feed.xml`, pushed to Hister, shown in full in `/api/status`,
  and `private: false` in `/api/vaults`. A vault without the flag stays private, as before. `+shared` on the default
  vault, or an unknown flag, refuses to start. No other vault gets Niwa, Konbini or "Save links in Shiori" links.
- The Hister push withdraws the documents of a vault that is no longer shared.
- `KURA_PUBLIC_URL` must be an origin (`https://kura.example`, maybe with a port) and Kura refuses to start otherwise.
  Under a path (`https://host/kura`), a work vault's note had an address clients couldn't tell from any other page
  (they look for `/v/` at the start of the path), so it could reach Hister or AI; Kura's own pages already needed the
  root.

## 0.4.3

- An Obsidian alias in a table cell, `[[Note\|alias]]` (the pipe is escaped there), links to the note and counts as a
  backlink of it.
- A callout without a title keeps its next line as its body.
- `/api/status` and About show the vendored vaultkit as `v0.9.6`.
- The README has a Quickstart (standalone, on Debian, OpenBSD, FreeBSD and NetBSD, or in a container, and as part of the
  Machiya stack), a sample vault (`sample-vault/`) and screenshots, and `tools/quickstart-test` runs the Quickstart's
  commands from a fresh clone (or over ssh on another machine).

## 0.4.2

- `/api/status` answers without identity, for monitoring, and that view carries no configuration: there is no `repo` or
  `subdir`, and error texts read `sync failed` and `push failed` (the full texts can name hosts and paths). A request
  that passes the owner gate gets the full answer, and so does every request in `KURA_AUTH=open`. A probe that matches
  `"ready": true` and `"error": "` works as before.

## 0.4.1

- The offline page and the offline banner say "Check your network or VPN.", whatever the network is.
- The README's Docker example runs as written: it mounts the token file and sets `KURA_AUTH=open` for localhost.

## 0.4.0

### External links

- `GET /api/note` returns `external_links`: the links in the note's body as `[{"url", "text"}]`, for apps that save a
  note's links. They are `http`, `https`, `gemini` and `gopher` links, written as Markdown links, raw HTML `<a>`,
  `<scheme://…>` autolinks or bare URLs in prose, lists and tables; trailing punctuation and an unbalanced closing
  bracket are trimmed from a bare URL. Links inside inline code and code blocks are ignored, duplicates appear once in
  order, and Kura's own host, the other rooms' hosts (`MACHIYA_ROOMS`) and `mailto:` links are left out. The sanitized
  `html` field carries no new schemes.
- `GET /api/links?folder=<folder>&limit=&offset=` lists every note under a folder (subfolders included) that has
  external links: `{"total", "notes": [{"path", "title", "url", "external_links"}]}`. `folder` is required; `limit`
  defaults to 20 (maximum 100). Only the default vault is served; any other `vault` is a 400.
- A note that has external links shows "Save links in Shiori", a link to `shiori://save-links?path=<vault path>`, when
  `KURA_SHIORI_LINKS=1`. Without the setting nothing is shown.
- Work vaults (every vault but the default) have no external links: their notes return `external_links: []`.

### Vaults and defaults

- `vault=` with only empty names means the default vault, like an absent parameter.
- Without `KURA_VAULTS`, the `KURA_REPO_*` settings describe one vault called `notes` (title `Notes`), and
  `KURA_REPO_SUBDIR` defaults to empty: the vault is the repository root. An install that keeps its vault in a folder
  sets `KURA_REPO_SUBDIR=<folder>`, and `KURA_VAULTS=<name>=<source>#<folder>` names the vault. An install that sets
  `KURA_VAULTS` needs no change. Note URLs (`/n/<path>`) are the same either way.
- The Obsidian Vault setting's placeholder is `my-vault`.
- `/api/status` and About show the vendored vaultkit as its tag (`v0.9.4`).
- `MACHIYA_SOURCE_URL` adds a source-code link to the footer and About (AGPL section 13); unset, nothing is shown.

### Project

- A test suite guards the rule that a client that never sends `vault` sees the default vault only: queries, filters, URL
  shapes and the owner gate.
- AGPL-3.0-or-later licence files and third-party notices, `CONTRIBUTING.md`, `SECURITY.md`, issue templates, and an
  install section in the README.

## 0.3.0

- `KURA_VAULTS` serves several vaults: the first is the default (`/n/…`), the others are private work vaults at
  `/v/<name>/…` with their own chip and a vault switch in the header. Work vaults have no Niwa or Konbini links, are
  never pushed to Hister, answer `Cache-Control: no-store`, are network-only in the service worker, and stay out of
  the API and the feed unless a client asks with `vault=`. `GET /api/vaults` lists them.
- `KURA_AUTH` is `tailscale` (the default: the `Tailscale-User-Login` allow-list) or `open` (no check, for localhost).
  `KURA_BIND` sets the listening address, and `KURA_ENV_FILE` (or `--env-file`) reads the settings from a file, for
  service managers such as rc.d. Together they allow native installs without Docker.
- `/api/status` shows a work vault's error only, and the monitoring probe fails when any vault's last sync failed.
- `markdown` 3.7 or later is enough (the BSD packages ship 3.7–3.10).

## 0.1.0 – 0.2.0

- The reader: every note with working `[[wikilinks]]`, backlinks, folder and tag browsing, recently changed, and three
  columns (one column and a tab bar on phones), in Machiya's shared shell with Tokyo Night and Tokyo Day.
- Full-text search (SQLite FTS5) with phrases, `-exclusions`, `prefix*`, `title:`, `tag:` and `folder:`, ranked by
  bm25 with titles first.
- A JSON API (`/api/search`, `/api/notes`, `/api/note`, `/api/recent`, `/api/tags`, `/api/folders`, `/api/status`) and
  an RSS feed, owner-only through `KURA_USERS` and the `Tailscale-User-Login` header; `/api/note` HTML is sanitized.
- An optional push of every note into Hister (`KURA_HISTER_URL`) with `vault_*` metadata.
- A PWA with offline reading: the notes read last stay on the device, and notes with `offline: true` stay for good;
  `Archive/` notes are never kept. "Edit in Obsidian" links per device.
