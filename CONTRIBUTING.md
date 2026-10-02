# Contributing to Kura

Thanks for helping. Kura is the note reader, search engine and JSON API of [Machiya](https://github.com/machiya-kobo/machiya): it serves a vault of
Markdown notes from a git repository. [CLAUDE.md](CLAUDE.md) is the detailed guide to how it works and the traps already
found; read it before changing the API, the vault handling or the offline code.

## Building

Kura is stdlib Python plus `markdown` (3.7 or later) and `pyyaml`, and it needs `git`. There is no build step:

```sh
pip install 'markdown>=3.7' pyyaml
KURA_AUTH=open KURA_BIND=127.0.0.1 KURA_REPO_DIR=/path/to/a/vault KURA_DB=/tmp/kura.sqlite3 python3 app/kura.py
docker build -t kura app            # the image; it also runs the vendored-code check
```

## Tests

Run them before you send a change:

```sh
python3 -m unittest discover -s tests
( cd app && python3 -m vaultkit.verify )     # the vendored vaultkit is unmodified
```

The tests start a real Kura against fixture vaults, so they also cover the HTTP API, the reader pages and the
sanitizer. Add a test with every change. A change to an endpoint or a query needs a test that a **work vault** (any
vault but the default) stays out of the answer.

## Rules

- `app/vaultkit/` is vendored from the Machiya repository and must stay byte-identical: fix it upstream, then
  re-vendor with `tools/vendor-vaultkit <tag>`.
- The API is a contract shared with other apps. Don't change a field, a parameter or the shape of `url`
  (`<base>/n/<slug>`) without changing `docs/contracts/kura-api.md` in the Machiya repository first.
- Kura never writes to the vault, and a note from a vault other than the default never reaches Hister, an AI engine, a
  feed, an offline cache or an export. A client that never sends `vault` gets the default vault only.
- Keep personal details out of the repo: hostnames, network names, names, emails, tokens. Say "the user".
- Keep the Tokyo Night / Tokyo Day look and the existing classes, and keep pages working on phones.
- Match the surrounding code: its naming, comment density and idiom. Commit messages start with `kura: `.

## Sending a change

Open a pull request with what changed and why, and which tests you ran. Keep one change per pull request. By
contributing, you agree that your work is licensed under the GNU AGPL-3.0-or-later, as the rest of Kura.
