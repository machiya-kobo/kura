# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use GitHub's private vulnerability reporting (this
repository's Security tab, then Report a vulnerability), with what you found, how to reproduce it, and what it affects.
You'll get an answer within a week, and a fix or a plan before anything is disclosed.

## What's in scope

- **The owner gate:** reaching a note, the search or the API without being an allowed user (`KURA_USERS` and
  `Tailscale-User-Login`; with `KURA_AUTH=hister`, a Hister user in `KURA_HISTER_USERS` through the sign-in helper and
  the `machiya_sso` cookie, `MACHIYA_SSO_COOKIE`). Only `/api/status` (the reduced view: no vault names) and
  `/api/changelog` are open.
- **The identity file** (`MACHIYA_IDENTITY_FILE`): a principal reading more than its grant: a vault its `kura`
  `vaults` doesn't name (an agent reaching a private vault above all), Kura without the `read` grant, an invalid
  token or session accepted, or a vault it may not read answering differently from one that doesn't exist.
- **Sign-in, pairing and preferences** (with an identity file): a session cookie set or cleared by a cross-site
  request (`POST /signin`, `POST /signout`), a `/signin` `next` that leaves Kura, a password, token secret or hash in
  a page, an answer or a log, a pairing code accepted after it expired (a code
  stays good until then by design: Kura can't write the identity file), `/api/prefs` read or written without
  the `kura` `read` grant, one principal reading or changing another's preferences, or a cookie-made `PUT` accepted
  without a same-origin `Origin`. Kura accepts no other `POST` or `PUT`.
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
