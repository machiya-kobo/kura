# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use GitHub's private vulnerability reporting (this
repository's Security tab, then Report a vulnerability), with what you found, how to reproduce it, and what it affects.
You'll get an answer within a week, and a fix or a plan before anything is disclosed.

## What's in scope

- **The owner gate:** reaching a note, the search or the API without being an allowed user (`KURA_USERS`,
  `Tailscale-User-Login`, `KURA_AUTH`). Only `/api/status` is open.
- **Private vaults:** a note from a private vault (any vault but the default without `+shared` in `KURA_VAULTS`)
  appearing where it shouldn't: an API answer to a client that sent no `vault`, a search or a query (`vault:` must
  never widen), a feed, `/api/offline`, a Hister push, `external_links`, a cached or offline copy, or a vault that
  isn't marked shared being treated as one. A shared vault is treated like the default one on purpose, but a client
  that sends no `vault` still gets the default vault alone.
- **The HTML Kura serves:** script injection from a note, a title, a tag or a snippet; a way around the sanitizer on
  `/api/note`; a path that reads a file outside the vault or the `Templates/` and `CLAUDE.md` notes Kura hides.
- **The vault checkout:** a token or credential leaking into a URL, `.git/config`, a log or an error message.
- **Offline copies:** a note that must not be kept on a device (`Archive/`, a private vault) being kept.

Hister, Tailscale, Obsidian and the other servers Kura talks to are separate projects: report their problems to them.
Kura trusts the `Tailscale-User-Login` header, so it must listen on `127.0.0.1` behind `tailscale serve` (see the
README); running it on a reachable address with `KURA_AUTH=tailscale` or `open` is a misconfiguration, not a
vulnerability.

## Supported versions

Fixes go into the latest release on the main branch.
