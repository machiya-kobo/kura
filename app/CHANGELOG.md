# Changelog

Kura follows [Semantic Versioning](https://semver.org). Before 1.0, a new feature, a changed default or setting, or a
changed API field bumps the minor number; a fix or a wording change bumps the patch. `/api/status` and Settings → About
show the running version.

## 0.9.0

- The Hister push heals itself. Kura sent each note once and never again unless it changed, so a document Hister lost (a cleanup, a reset) stayed lost. Now, at start and then every day, a full run asks Hister whether each note it sent is still there (`HEAD /api/document?url=…`, or GET where HEAD isn't allowed), forgets the ones that are gone, and sends them again in the same run. The log says `kura push: reconcile: N missing, re-pushed (M checked)`, and `push.missing` in `/api/status` shows the last count, so a later loss is visible. When Hister can't answer (not signed in, throttled, down) the check stops and forgets nothing; the next sync tries again.

## 0.8.1

- The Hister push no longer gives up on a note for good when Hister answers 401, 403, 408, 425, 429 or 5xx (not signed in yet, no token, throttled, struggling): the run stops, and the next sync tries again. Only a refusal about the note itself (for example Hister's sensitive-content check) is kept until the note changes. Notes an older Kura marked `refused: HTTP 401…` are sent again on the next run.
- A vault mirror runs git with a fixed author and committer, so a host whose own name doesn't resolve no longer stalls every sync for over a minute. A failed update now says "the vault could not be updated" and what git said, not that the remote could not be reached.
- README: the BSD install blocks leave the packaged `markdown` out (all older than 3.11) and install it with pip in a virtual environment.

## 0.8.0

Takes vaultkit 0.22 (the sweep's shared fixes and the room sessions).

- Needs `markdown` 3.11 or later: vaultkit refuses to start with an older one, which can run out of memory on one note under Python 3.13. The image has it; on a BSD whose package is older, install it with pip in a virtual environment (README).
- Symlinks in a vault are skipped (a committed link can no longer read a file outside the vault), and the vault is cloned with `core.symlinks=false`.
- A vault that couldn't be fetched says so and keeps serving its last good copy, instead of reporting "synced".
- Every answer that isn't a page carries `nosniff`, `X-Frame-Options` and `Referrer-Policy`; vault images are served sandboxed by vaultkit's rules; redirects built from a request are percent-encoded local paths.
- Hister sign-in (`KURA_AUTH=hister`): Kura keeps a cookie of its own (host-only, from a one-time code the helper hands over; the shared `machiya_sso` is being retired), takes room tokens (`Authorization: Bearer mht_…`) in place of Hister's own token, and `KURA_AUTH_ACCEPT_ORIGINS` lists other origins whose room sessions it also accepts (Shiori's hosted pages). Inert until the helper switches.
- Menus close when you come back to a page, and an installed app can pull down to refresh.

## 0.7.2

- Kura accepts at most 64 connections at once and closes any connection still open after two minutes, so one client holding connections open (a byte at a time) can't use up every thread.

## 0.7.1

Fixes from the October security sweep.

- A vault's SVG no longer runs script in Kura's address: `/a/` serves it with a sandboxing `Content-Security-Policy` (and every vault image with `nosniff`), so opening one directly can't read the API. In a page, an `<img>` never ran it.
- Every `/api/*` answer is `no-store` (a private vault's or an Archive note's text is not kept by a browser or a client's cache), except `/api/status`, `/api/changelog` and `/api/offline`.
- With the old Tailscale gate (no identity file), Kura now refuses to start on a non-loopback `KURA_BIND` unless `KURA_BIND_BEHIND_PROXY=1`, as the identity modes always did: anyone who can reach the port could forge the login header. Listen on `127.0.0.1` behind `tailscale serve`, or set `KURA_BIND_BEHIND_PROXY=1` when a proxy is the only way in. `KURA_AUTH=open` and Hister sign-in are unchanged.
- Notes with non-ASCII file names (`町家.md`, `café notes.md`) keep their change dates, so they sort right in Recent, the API and the feed.
- A garbage or oversized request line gets a 400 answer instead of a dropped connection and a traceback in the log; the access log no longer records query strings (a private vault's search terms).
- An empty vault grant shows no vault names (identity file); the external-links cache is safe under concurrent requests; the image pins `markdown` to the version the tests run on.

## 0.7.0

- Settings follow the signed-in person (vaultkit 0.21.0): Theme, Appearance, Text Size and the Apps switches are saved to the Hister account and come with you to every Machiya app and device. Kura's Preview Pane follows you too. With `KURA_AUTH=hister`, `/api/prefs` forwards to the sign-in helper and a browser with no cookies yet is drawn in the account's theme from its first page. Without it, Kura keeps its own store, as before.
- Settings has the order every Machiya app uses: Shared, Reading, This Device (Use This Device's Size, Offline Copies, Obsidian Vault), Account, About. Obsidian Vault and Offline Copies moved to This Device.
- `/api/prefs` answers `{"v", "rev", "prefs", "updated"}` and accepts only the keys of Machiya's preferences schema (a wrong key or value is a 400).

## 0.6.14

- `MACHIYA_SSO_COOKIE` names the Hister sign-in cookie (default `machiya_sso`, unchanged), so a second stack on the same domain (the dev stack) can use its own (vaultkit 0.20.0).

## 0.6.13

- The open `/api/status` no longer names any vault: it gives `vault_count` and the default vault's counts; the names and per-vault detail are for the signed-in owner only.
- With Hister's sign-in, a request that carries a wrong `X-Access-Token` or `Authorization` gets 401, on pages too, instead of a redirect to sign in.

## 0.6.12

- The Rooms menu's Machiya row reads "Machiya · home": the stack's front door; its status page moved to /status (vaultkit 0.19.1).

## 0.6.11

- `KURA_AUTH=hister`: Hister's sign-in (through the hister-login helper) as Kura's gate, with no Tailscale fallback: signed out goes to sign-in, sign-in unavailable is a 503 (vaultkit 0.19.0). Off by default; `tailscale` is unchanged.

## 0.6.10

- `KURA_HISTER_TOKEN_FILE`: the owner's Hister token, sent as `X-Access-Token` on every call to Hister (and to the `hister` command's environment, never its arguments), for the coming Hister sign-in. Unset sends nothing, as before.

## 0.6.9

- A "Machiya · status" row in the Rooms menu and a link from the footer's "Part of Machiya" to the stack's status page, and `GET /api/changelog` serves this changelog for its recent deploys (vaultkit 0.18.0).

## 0.6.8

- A search pill under the header on every page, at every width, as Shiori's: results appear as you type, Escape or the X puts the page back, and on a phone a magnifier submits (vaultkit 0.17.2). The Search tab and nav link are gone; the tab bar has its old tabs again.

## 0.6.7

- In the installed app on an iPhone the header's logo and title sit lower, clear of the band under the status bar that iOS draws soft; the phone header is pinned again (vaultkit 0.16.8).

## 0.6.6

- On a phone the header scrolls with the page instead of staying pinned (vaultkit 0.16.7): the installed app on iOS drew a pinned header soft. The Rooms menu's text meets AA contrast in every theme.

## 0.6.5

- No search field in the header at any width (vaultkit 0.16.4): Search is the third tab on a phone and the third link on a wide screen, and "/" opens the search page. 0.6.4 was tagged but never deployed.

## 0.6.4

- The header is solid on a phone (vaultkit 0.16.3), the same in every room; 0.6.3 was tagged but never deployed.

## 0.6.3

- On a phone, search lives in the tab bar (Search, the third tab, as in every room); the header's search field is hidden at phone width (vaultkit 0.16.2).

## 0.6.2

- The phone tab bar is more see-through, frosted glass like Shiori's (vaultkit 0.16.1). 0.6.1 was tagged but never deployed.

## 0.6.1

- On a phone the tab bar is a floating pill like Shiori's (vaultkit 0.16.0): it fits five tabs on any phone, the current tab sits on a raised pill, and it follows the light or dark variant as Shiori does.

## 0.6.0

- **Identity** (Machiya's identity plan, phase 3; vaultkit v0.10.0): with `MACHIYA_IDENTITY_FILE`, Kura asks the
  identity file who is calling (a token, a Tailscale login or tagged node, a trusted proxy header, a session) instead
  of `KURA_USERS`. A principal needs the `kura` `read` grant and reads only the vaults it grants: an agent gets the
  default and shared vaults, never a private one unless its grant names it, whatever it asks; a vault it may not read
  answers exactly like one that doesn't exist. `/api/status`'s full view is the owner's. New settings:
  `KURA_AUTH=header` with `KURA_AUTH_HEADER`, `KURA_BIND_BEHIND_PROXY`, `KURA_ACCEPT_APP_CAPS`. Without the file
  nothing changes.
- `default` and `shared` can't be vault names any more (they are words in an identity grant).
- **Sign-in, pairing and preferences** (the identity plan's phase 6; vaultkit v0.11.0): with an identity file,
  `KURA_SIGNIN=1` turns on the built-in sign-in (`/signin`, `/signout`; a page refused with 401 links to it, and
  Settings has Sign Out for a signed-in browser). `POST /api/pair` trades a Shiori pairing code for a device token,
  and `GET`/`PUT /api/prefs` keeps each principal's preferences in `prefs.sqlite3` next to `KURA_DB`. Sign-in, sign-out
  and a prefs `PUT` made with a cookie must come from `KURA_PUBLIC_URL`'s origin (or, without it, Kura's own https
  page); over plain http, set `KURA_PUBLIC_URL`. Any other `POST` or `PUT` answers 405. Without an identity file the
  new routes are 404 and nothing else changes.
- **vaultkit v0.13 to v0.15** (the PWA pass): a note's HTML is sanitized before it is shown (a script in a note of a
  shared vault used to run on Kura's own origin), and every page sends a Content-Security-Policy, `nosniff` and a
  Referrer-Policy; signing out clears the offline copies; the 401, 404 and offline pages share one design; the web
  manifest's colours follow the device, so the splash screen is light on a light phone; ten themes, each dark and
  light, under Settings → Display (Theme and Appearance); preferences are kept and shared across rooms even without an
  identity file; a signed-in person is shown in the header.

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
- Redirects stay on Kura's host: `/v/<default>//other.host/…` and `/theme` with a Referer path such as `//other.host`
  answered with a redirect to another site; they now fall back to `/`.
- With `KURA_AUTH=open`, Kura answers only when `Host` is an IP address, `localhost`, `KURA_PUBLIC_URL`'s name or a
  name in the new `KURA_ALLOWED_HOSTS`; anything else gets 403. Before, a web page could point its own name at a
  localhost Kura (DNS rebinding) and read every note, private vaults included.
- A client that stops sending is dropped after 30 seconds instead of holding a thread for good.

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
