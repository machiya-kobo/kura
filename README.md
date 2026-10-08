# Kura

[Machiya](https://github.com/machiya-kobo/machiya) is a set of small self-hosted apps for finding what you've read: your pages ([Hister](https://github.com/asciimoo/hister)), the web ([SearXNG](https://github.com/searxng/searxng)), your notes ([Obsidian](https://obsidian.md)) and your code ([Forgejo](https://forgejo.org) or [GitHub](https://github.com)).

Kura (蔵, storehouse) is Machiya's notes app. Read every note in your Obsidian vault, with working links and full-text search. It's also a JSON API for your scripts and agents.

Agentically coded with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

<p align="center">
<a href="https://machiya-kobo.github.io/">Machiya</a> · <a href="#quickstart">Quickstart</a> · <a href="docs/install.md">Install</a> · <a href="docs/settings.md">Settings</a> · <a href="docs/access.md">Access</a> · <a href="https://github.com/machiya-kobo/machiya/blob/main/docs/contracts/kura-api.md">API</a> · <a href="#license">License</a>
</p>

<p align="center"><a href="docs/screenshots/kura-home-dark.png"><img src="docs/screenshots/kura-home-dark.png" alt="Kura in the dark theme: folders on the left, recently changed notes in the middle, the note Bamboo frames previewed on the right" width="100%"></a><br>Browse folders and preview notes side by side</p>

<table>
  <tr>
    <td align="center" width="33%"><a href="docs/screenshots/kura-note-light.png"><img src="docs/screenshots/kura-note-light.png" alt="The note Bamboo frames on its own page, with its backlinks, in the light theme" width="100%"></a><br>Read a note and its backlinks</td>
    <td align="center" width="33%"><a href="docs/screenshots/kura-search-dark.png"><img src="docs/screenshots/kura-search-dark.png" alt="Search results for bamboo, title matches first, in the dark theme" width="100%"></a><br>Search every note</td>
    <td align="center" width="33%"><a href="docs/screenshots/kura-tags-light.png"><img src="docs/screenshots/kura-tags-light.png" alt="Every tag in the sample vault with its note count, grouped by area, topic and type, in the light theme" width="100%"></a><br>Browse every tag</td>
  </tr>
</table>

<p align="center"><a href="docs/screenshots/kura-note-phone-dark.png"><img src="docs/screenshots/kura-note-phone-dark.png" alt="The note Bamboo frames on a phone, one column with a tab bar, in the dark theme" width="24%"></a><br>Your notes on a phone</p>

- **Follow every link.** Every `[[wikilink]]` works, with backlinks from the whole vault, folders, tags (nested ones too) and recently changed.
- **Search everything.** SQLite FTS5: `"phrases"`, `-exclusions`, `prefix*`, `title:`, `tag:`, `folder:` and `vault:`, ranked by bm25 with titles first.
- **Read offline.** Install it as a PWA. The 200 notes you read last stay on the device for when you're off The Internet, and notes with `offline: true` stay for good. Nothing under `Archive/` is ever kept.
- **Keep work notes apart.** `KURA_VAULTS` adds more vaults (say `work` or `team`) at `/v/<name>/`, with a switch in the header. A vault is **private** unless marked `+shared`: never pushed to Hister, stored on a device or put in a feed ([the rules](docs/settings.md), [the API](https://github.com/machiya-kobo/machiya/blob/main/docs/contracts/kura-api.md#vaults)).
- **Feed the other apps.** Every note link in Machiya lands here. Shiori, the search app, reads your notes from the [JSON API](https://github.com/machiya-kobo/machiya/blob/main/docs/contracts/kura-api.md), with an RSS feed at `/feed.xml`. With `KURA_HISTER_URL`, every note goes into Hister too.

## Quickstart

Run Kura on the sample vault: 27 invented notes about a paper-lantern workshop and a trip to Kyoto. You need `git`, `curl` and Python 3.11 or later. On Debian 13:

<!-- quickstart: quick-debian:packages -->
```bash
sudo apt-get update && sudo apt-get install -y git curl python3-venv
```

**1. Get the code**

```sh
git clone https://github.com/machiya-kobo/kura.git && cd kura
```

**2. Install `markdown` 3.11+ and `pyyaml`** in a virtual environment:

<!-- quickstart: quick:python -->
```bash
python3 -m venv .venv && .venv/bin/pip install -q 'markdown>=3.11' pyyaml
```

**3. Start it.** `KURA_AUTH=open` means no sign-in, so it listens on `127.0.0.1` only:

<!-- quickstart: quick:serve -->
```bash
KURA_AUTH=open KURA_BIND=127.0.0.1 KURA_PUBLIC_URL=http://127.0.0.1:8080 KURA_REPO_DIR="$PWD" KURA_REPO_SUBDIR=sample-vault/personal .venv/bin/python app/kura.py
```

| App | Address |
|---|---|
| Kura | http://127.0.0.1:8080/ |

**4. Try it.** Search for `bamboo`, `title:kyoto` or `tag:topic/travel`, or ask the API from another terminal:

<!-- quickstart: quick:check -->
```bash
curl -s 'http://127.0.0.1:8080/api/search?q=title:bamboo' | grep '"path"'
```

<!-- quickstart-expect: quick:check -->
```text
   "path": "Projects/Bamboo frame jig.md",
   "path": "Notes/Sourcing bamboo.md",
   "path": "Notes/Bamboo frames.md",
```

Ctrl-C stops it.

### Next

- **Read your own vault:** point `KURA_REPO_DIR` at its checkout, `KURA_DB` at a file of your own, and `KURA_REPO_SUBDIR` at the notes folder if it isn't the top ([more](docs/install.md#your-own-vault)).
- **Run it in a container or on the BSDs:** [the install guide](docs/install.md#in-a-container).
- **Let other people in, or sign in:** [who can use it](docs/access.md).
- **Run it with the other Machiya apps:** [the stack](docs/install.md#as-part-of-the-machiya-stack).
- **Every setting:** [settings](docs/settings.md).
- **Change the code:** [how Kura works](docs/layout.md).

## License

Copyright (C) 2026 Micheal Waltz and Machiya contributors.

Kura is free software under the GNU Affero General Public License, version 3 or (at your option) any later version: see [LICENSE](LICENSE). What it ships from other projects (Mermaid, Python Markdown, PyYAML) is listed with their licenses in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES). Contributions are welcome: [CONTRIBUTING.md](CONTRIBUTING.md); report a vulnerability privately: [SECURITY.md](SECURITY.md).
