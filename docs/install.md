# Install Kura

Kura needs Python 3.11 or later (the image has 3.13), `markdown` 3.11 or later, `pyyaml` and `git`, or the container
image. Nothing else from Machiya. It reads a git repository (or a folder in one) that holds an Obsidian vault, and it
never writes to it.

The walkthroughs below start in a clone, like the [Quickstart](../README.md#quickstart).

## In a container

Neither installed? On Debian 13, install `podman` (and `catatonit`, the small init that `--init` uses with podman):

<!-- quickstart: container-debian:packages -->
```bash
sudo apt-get update
sudo apt-get install -y git curl podman catatonit
```

Pick your engine (the rest of this section runs `$DOCKER`):

<!-- quickstart: container-docker:engine -->
```bash
DOCKER=docker
```

*or, with Podman* (`--cgroup-manager=cgroupfs` spares podman a systemd user session, which a fresh or ssh-only machine
may not have yet):

<!-- quickstart: container-podman:engine -->
```bash
DOCKER="podman --cgroup-manager=cgroupfs"
```

Then make the sample vault a repository and start Kura:

<!-- quickstart: container:vault -->
```bash
cp -R sample-vault/personal vault
git -C vault init -q -b main
git -C vault add -A
git -C vault -c user.name=Demo -c user.email=demo@example.com commit -q -m "sample vault"
```

<!-- quickstart: container:run -->
```bash
$DOCKER build -t kura app
$DOCKER run -d --init --name kura -p 127.0.0.1:8080:8080 \
  -v "$PWD/vault:/vault:ro" -v kura-data:/data \
  -e KURA_REPO_DIR=/vault -e KURA_AUTH=open \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  kura
```

The three `GIT_CONFIG_*` settings tell git that the mounted vault, owned by your user, is safe to read. `--init` makes
`stop` quick. Wait until Kura has read the vault, then check it:

<!-- quickstart: container:check -->
```bash
for i in $(seq 60); do curl -s http://127.0.0.1:8080/api/status | grep -q '"ready": true' && break; sleep 1; done
curl -s http://127.0.0.1:8080/api/status | grep -E '^ "(notes|ready|error)": '
curl -s 'http://127.0.0.1:8080/api/search?q=title:bamboo' | grep '"path"'
```

What you should see:

<!-- quickstart-expect: container:check -->
```text
 "notes": 27,
 "ready": true,
 "error": null,
   "path": "Projects/Bamboo frame jig.md",
   "path": "Notes/Sourcing bamboo.md",
   "path": "Notes/Bamboo frames.md",
```

Open <http://127.0.0.1:8080/>. Stop it with:

<!-- quickstart: container:stop -->
```bash
$DOCKER rm -f kura
```

## Natively on the BSDs

On Linux and macOS, the Quickstart is the native install. On the BSDs:

- Kura also needs SQLite with FTS5. Every package below has it.
- Every BSD's `markdown` package is too old today (FreeBSD and OpenBSD 3.10.2, NetBSD 3.10.3), and Kura refuses to
  start with one: an old `markdown` can run out of memory on a single note under Python 3.13. So a virtual environment
  gets it from pip.
- The package blocks use `sudo` or `doas`; as root, drop it. A fresh FreeBSD or NetBSD has neither: run them as root,
  or install `sudo` first.

**OpenBSD 7.9:**

<!-- quickstart: native-openbsd:packages -->
```bash
doas pkg_add python%3 py3-yaml git curl
```

<!-- quickstart: native-openbsd:python -->
```bash
PY=python3
```

**FreeBSD 14 or 15** (`py312-sqlite3` is separate there):

<!-- quickstart: native-freebsd:packages -->
```bash
sudo pkg install -y python312 py312-sqlite3 py312-pyyaml git-lite curl
```

<!-- quickstart: native-freebsd:python -->
```bash
PY=python3.12
```

**NetBSD 10 or 11** (a fresh NetBSD has no `pkgin`; `pkg_add` reads the binary packages from `PKG_PATH`):

<!-- quickstart: native-netbsd:packages -->
```bash
PKG_PATH="https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/$(uname -p)/$(uname -r | cut -d_ -f1)/All"
sudo env PKG_PATH="$PKG_PATH" /usr/sbin/pkg_add python313 py313-yaml git-base curl
```

<!-- quickstart: native-netbsd:python -->
```bash
PATH=/usr/pkg/bin:$PATH
PY=python3.13
```

Then clone Kura (Quickstart step 1) and, in the clone, get `markdown` from pip in a virtual environment that also sees
the packages you just installed (`$PY` becomes its Python):

<!-- quickstart: native:venv -->
```bash
$PY -m venv --system-site-packages .venv && .venv/bin/pip install -q 'markdown>=3.11'
PY=.venv/bin/python
```

Make the sample vault a repository and start Kura:

<!-- quickstart: native:vault -->
```bash
cp -R sample-vault/personal vault
git -C vault init -q -b main
git -C vault add -A
git -C vault -c user.name=Demo -c user.email=demo@example.com commit -q -m "sample vault"
```

<!-- quickstart: native:run -->
```bash
mkdir -p data
KURA_AUTH=open KURA_BIND=127.0.0.1 KURA_PUBLIC_URL=http://127.0.0.1:8080 \
  KURA_REPO_DIR="$PWD/vault" KURA_DB="$PWD/data/kura.sqlite3" $PY app/kura.py > data/kura.log 2>&1 &
echo $! > data/kura.pid
```

<!-- quickstart: native:check -->
```bash
for i in $(seq 60); do curl -s http://127.0.0.1:8080/api/status | grep -q '"ready": true' && break; sleep 1; done
curl -s http://127.0.0.1:8080/api/status | grep -E '^ "(notes|ready|error)": '
curl -s 'http://127.0.0.1:8080/api/search?q=title:bamboo' | grep '"path"'
```

The output matches the container's:

<!-- quickstart-expect: native:check -->
```text
 "notes": 27,
 "ready": true,
 "error": null,
   "path": "Projects/Bamboo frame jig.md",
   "path": "Notes/Sourcing bamboo.md",
   "path": "Notes/Bamboo frames.md",
```

Stop it with:

<!-- quickstart: native:stop -->
```bash
kill "$(cat data/kura.pid)"
```

To run it as a service (rc.d scripts, a user of its own, `tailscale serve` in front), see
[`docs/install/bsd.md`](https://github.com/machiya-kobo/machiya/blob/main/docs/install/bsd.md) in the Machiya repository.

## Your own vault

**With Docker.** Kura clones the vault itself and keeps its state in `/data`:

```sh
docker build -t kura app
docker run --init -p 127.0.0.1:8080:8080 -v kura-data:/data \
  -v "$PWD/token:/run/secrets/token:ro" \
  -e KURA_REPO_URL=https://example.com/you/vault.git -e KURA_REPO_TOKEN_FILE=/run/secrets/token \
  -e KURA_AUTH=open kura
```

`token` is a file holding the git host's access token. Drop the `-v` and `KURA_REPO_TOKEN_FILE` lines for a public
repository; `ssh://` and `file://` URLs work too. `KURA_AUTH=open` turns the identity check off: anyone who can reach
port 8080 reads every note, so the port is published on `127.0.0.1` only. Open <http://127.0.0.1:8080/>; Ctrl-C stops it.

**On a checkout you already have** (read-only, no clone):

```sh
python3 -m venv .venv && .venv/bin/pip install 'markdown>=3.11' pyyaml
mkdir -p data
KURA_AUTH=open KURA_BIND=127.0.0.1 KURA_REPO_DIR=/path/to/vault KURA_DB="$PWD/data/kura.sqlite3" .venv/bin/python app/kura.py
```

To let other people in, see [who can use it](access.md). Every setting is in [settings](settings.md), and the vault's
own conventions (frontmatter, tags, folders) are in Machiya's [`docs/frontmatter.md`](https://github.com/machiya-kobo/machiya/blob/main/docs/frontmatter.md).

## As part of the Machiya stack

To run Kura with the other Machiya apps, follow the [Quickstart in the Machiya README](https://github.com/machiya-kobo/machiya#quickstart). The stack's own settings are in
its compose files and `.env.example`. What changes for Kura:

- **The port:** the stack publishes Kura on `127.0.0.1:8083` (`KURA_PORT`), not 8080.
- **The vault** comes from the stack's `VAULT_REPO_URL` (Kura clones it) or, with the shared copy, from a read-only
  checkout at `/vault` (`KURA_REPO_DIR=/vault`, no `KURA_REPO_URL`). `VAULT_SUBDIR` (`KURA_REPO_SUBDIR`) names the notes
  folder.
- **The other apps:** `MACHIYA_ROOMS` (the Rooms switcher in the header), `KURA_NIWA_URL` and `KURA_KONBINI_URL`
  ("View in Niwa" and "View Card in Konbini"), and `KURA_HISTER_URL` (push every note into Hister; once Hister has
  sign-in on, also `KURA_HISTER_TOKEN_FILE`, the owner's Hister token, which the reference compose doesn't pass yet).
- **Who may read it:** the stack's compose sets `KURA_AUTH=open`, safe only because every published port binds
  `127.0.0.1`. [Who can use it](access.md) says how to let others in.
- **One look across apps:** with the apps on hostnames of one domain, `MACHIYA_COOKIE_DOMAIN` shares the theme and
  text size between them.
- **Links:** `KURA_PUBLIC_URL` (the base of every note's URL), `MACHIYA_SOURCE_URL` (a source-code link in the footer)
  and `KURA_SHIORI_LINKS=1` ("Save Links in Shiori").

## Testing these steps

`tools/quickstart-test` runs every block on this page and in the README's Quickstart from a fresh clone and checks the
output shown. `--dry-run` lists the steps; `--ssh HOST` runs them on a clean BSD or Debian
machine.
