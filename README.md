# Kura

[Machiya](https://github.com/machiya-kobo/machiya) is a set of small self-hosted apps for finding what you've read: your pages ([Hister](https://github.com/asciimoo/hister)), the web ([SearXNG](https://github.com/searxng/searxng)), your notes ([Obsidian](https://obsidian.md)) and your code ([Forgejo](https://forgejo.org) or [GitHub](https://github.com)).

Kura (蔵, "storehouse") is the notes app for Machiya and reads every note in your Obsidian vault, with working links and full-text search. It's also a JSON API for your scripts and agents.

<p align="center">
<a href="https://machiya-kobo.github.io/">Machiya</a> · <a href="#quickstart">Quickstart</a> · <a href="docs/access.md">Who Can Use It</a> · <a href="docs/install.md#with-a-container-docker-or-podman">Container</a> · <a href="docs/install.md#natively-on-the-bsds">BSDs</a> · <a href="docs/install.md#as-part-of-the-machiya-stack">Machiya Stack</a> · <a href="docs/settings.md">Settings</a> · <a href="docs/install.md#your-own-vault">Install</a> · <a href="#license">License</a>
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
- **Keep work notes apart.** `KURA_VAULTS` adds more vaults (say `work` or `team`) at `/v/<name>/`, with a vault switch in the header. A vault is **private** unless marked `+shared`: a yellow chip, never pushed to Hister, never stored on a device, no external links, no feed. A **shared** vault works like the default one: kept offline, external links, its own `/v/<name>/feed.xml`, pushed to Hister. Neither gets Niwa or Konbini links (those apps read the default vault only), and API clients see either only when they ask with `vault=`. See "Vaults" in the API contract.
- **Feed the other apps.** Every note link in Machiya lands here. Shiori, the search app, reads its notes from the JSON API: `/api/search`, `/api/notes`, `/api/note` (with `external_links`: the note's http, https, Gemini and Gopher links), `/api/links` (a folder's external links in one call, never for a private vault), `/api/recent`, `/api/tags`, `/api/folders`, `/api/vaults`, `/api/offline`, `/api/status`, `/api/changelog`, plus `/feed.xml`. With `KURA_HISTER_URL`, every note goes into Hister too.

The [Machiya repository](https://github.com/machiya-kobo/machiya) has the architecture, the principles and the API contract (`docs/contracts/kura-api.md`).

## Quickstart

Run Kura on the sample vault in `sample-vault/`: a made-up paper-lantern workshop and a trip to Kyoto, 27 notes. No
account, no Tailscale. You need `git`, `curl` and Python 3.12 or later. On Debian 13:

<!-- quickstart: quick-debian:packages -->
```bash
sudo apt-get update && sudo apt-get install -y git curl python3-venv
```

**1. Get the code**

```sh
git clone https://github.com/machiya-kobo/kura.git && cd kura
```

**2. Install the two Python packages** (`markdown` 3.11 or later and `pyyaml`) in a virtual environment:

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

- **Read your own vault:** point `KURA_REPO_DIR` at its checkout, and `KURA_REPO_SUBDIR` at the notes folder if it isn't the top ([more](docs/install.md#your-own-vault)).
- **Run it in a container or on the BSDs:** [install guide](docs/install.md).
- **Let other people in, or sign in:** [who can use it](docs/access.md).
- **Run it with the other Machiya apps:** [the stack](docs/install.md#as-part-of-the-machiya-stack).
- **Every setting:** [settings](docs/settings.md).
- **Change the code:** [how Kura works](docs/layout.md).

## More ways to run it

Containers, the BSDs, your own vault and the full Machiya stack are in the [install guide](docs/install.md).

## License

Copyright (C) 2026 Micheal Waltz and Machiya contributors.

Kura is free software: GNU Affero General Public License, version 3 or (at your option) any later version.
See `LICENSE`. Third-party software it ships or installs (Mermaid, Python Markdown, PyYAML) is listed with its
licenses in `THIRD_PARTY_NOTICES`.
