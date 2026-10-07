# How Kura works

## Reading the vault

- Kura keeps its own clone of the vault repository (https, ssh or file) and fetches it every `KURA_POLL` seconds.
- It rebuilds its in-memory search index whenever the commit changes. A few hundred notes take under a second.
- With `KURA_HISTER_URL` it pushes every note into Hister (label `vault`, each under its Kura URL) and remembers what it
  sent in `KURA_DB`.
- That and your preferences (theme and text size, in `prefs.sqlite3` next to it) are its only state. Lose `/data` and
  Kura clones again, re-sends every note once and forgets the preferences.
- Niwa, Konbini and Hister are optional. Kura never writes to the vault.

## The code

- `app/kura.py`: the server: settings, the sync loop, routes, the owner gate and the API
- `app/pages.py`: the reader pages; every page takes the vault (`g`, a `sites.Site`) and builds its links with `g.prefix`
- `app/sites.py`: the vaults: `KURA_VAULTS` parsing, `Site` (a vaultkit `Vault` with a name, title, prefix, and whether it's shared or private) and the shared checkouts
- `app/search.py`: the FTS5 index (one table, a `vault` column) and the query syntax
- `app/api.py`: JSON shapes, card links, the HTML sanitizer for `/api/note`, and RSS
- `app/push.py`: the vault push into Hister
- `app/shell.py`: Kura's room on the shared shell (`vaultkit.shell`): tabs, glyphs, the manifest, the service worker's settings, `/settings`
- `app/static/`: `kura.css` (the reader's own layout, over `machiya.css`) and `kura.js` (preview pane, Mermaid, Edit in Obsidian, the offline banner), vendored Mermaid (MIT), icons
- `app/vaultkit/`: the shared vault core and UI (`ui/machiya.css`, `machiya.js`, `machiya-sw.js`), **vendored** from machiya-kobo/machiya (`tools/vendor-vaultkit <tag>`)
- `tests/`: the tests, against a real Kura on fixture vaults
- `tools/`: `quickstart-test`, `screenshots` and `vendor-vaultkit`
- `skills/kura/`: a short agent skill for finding and reading notes through the API

`python3 -m unittest discover -s tests` runs the tests ([CLAUDE.md](../CLAUDE.md), "Testing", has the details).
`tools/quickstart-test` runs the [install steps](install.md#testing-these-steps), and `tools/screenshots` retakes the
README's screenshots from the sample vault. [CONTRIBUTING.md](../CONTRIBUTING.md) says how to send a change.
