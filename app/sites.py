"""Kura's vaults ("sites"): the default vault and the work vaults (docs/contracts/kura-api.md, "Vaults").

KURA_VAULTS="personal=/vault#personal, work:Work=/vault#work, notes=https://host/owner/notes.git#"
  name[:Title]=source#subdir, comma-separated. The first is the default vault; name is [a-z0-9-]+ and not "v" (the
  URL prefix). source is a local directory (a mounted checkout, read-only) or a git URL (Kura clones it under
  KURA_REPO_DIR, once per distinct URL). Unset: KURA_REPO_* describe one vault, called
  notes (DEFAULT_NAME), whose folder is KURA_REPO_SUBDIR (default: the repo root).
Options per vault: KURA_VAULT_<NAME>_OBSIDIAN (the vault's name in Obsidian; default: its folder, else its name).

The default vault lives at /n/… as before; every other vault at /v/<name>/… and is private: no Niwa or Konbini links,
never pushed to Hister, never stored on a device (Cache-Control: no-store), API-visible only when asked for.
"""
import hashlib
import os
import re

from vaultkit import Git, Mirror, Vault, read_secret

NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*$")


DEFAULT_NAME, DEFAULT_TITLE = "notes", "Notes"      # the one vault of an install without KURA_VAULTS


class Site(Vault):
    """A vaultkit.Vault plus what Kura knows about it. `prefix` goes before /n/, /f/, /t/, /a/ in every link."""

    def __init__(self, source, subdir, name, title, default, obsidian=""):
        super().__init__(source.dir, subdir, git=source.git.run)
        self.checkout, self.name, self.title, self.default = source, name, title, default
        self.private = not default
        self.prefix = "" if default else "/v/" + name
        self.obsidian = obsidian or os.path.basename(subdir) or name
        self.head, self.synced_at, self.error, self.ready = "", None, None, False


class Source:
    """One git checkout, shared by the vaults inside it (an update fetches it once)."""

    def __init__(self, git, dir, mirror):
        self.git, self.dir, self.mirror = git, dir, mirror
        self.head, self.error = "", None

    def update(self):
        self.head = self.git.update()[0] if self.mirror else self.git.head()
        return self.head


def parse(raw):
    """KURA_VAULTS -> [(name, title, source, subdir)]. Refuses to start on anything it can't read."""
    out = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        head, sep, rest = part.partition("=")
        if not sep or not rest.strip():
            raise SystemExit("kura: KURA_VAULTS entry %r is not name[:Title]=source#subdir" % part.strip())
        name, _, title = head.strip().partition(":")
        name, title = name.strip(), title.strip()
        if not NAME_RE.match(name) or name == "v":
            raise SystemExit("kura: KURA_VAULTS: %r is not a vault name (lowercase letters, digits, -; not \"v\")" % name)
        if any(name == o[0] for o in out):
            raise SystemExit("kura: KURA_VAULTS: %r twice" % name)
        source, _, subdir = rest.strip().partition("#")
        out.append((name, title or name.replace("-", " ").title(), source.strip(), subdir.strip().strip("/")))
    return out


def build(env, repo_url, repo_dir, subdir, branch, token_file, user):
    """-> ([Site], [Source]) from the environment; the first site is the default vault."""
    entries = parse(env.get("KURA_VAULTS"))
    token = read_secret(token_file)
    sources = {}

    def source_of(spec):
        if spec not in sources:
            if re.match(r"^[a-z][a-z0-9+.-]*://|^git@", spec):        # a git URL: clone it under repo_dir
                dir = os.path.join(repo_dir, hashlib.sha1(spec.encode()).hexdigest()[:10])
                sources[spec] = Source(Mirror(spec, dir, branch, token, user), dir, True)
            else:                                                       # a directory, used as it is
                sources[spec] = Source(Git(spec), spec, False)
        return sources[spec]

    if not entries:                                                    # KURA_REPO_* describe one vault
        if repo_url:
            src = Source(Mirror(repo_url, repo_dir, branch, token, user), repo_dir, True)
        else:
            src = Source(Git(repo_dir), repo_dir, False)
        sources[repo_url or repo_dir] = src
        entries = [(DEFAULT_NAME, DEFAULT_TITLE, None, subdir)]
        made = [(e, src) for e in entries]
    else:
        made = [(e, source_of(e[2])) for e in entries]
    sites = []
    for i, ((name, title, _, sub), src) in enumerate(made):
        key = "KURA_VAULT_%s_OBSIDIAN" % name.upper().replace("-", "_")
        sites.append(Site(src, sub, name, title, i == 0, env.get(key, "").strip()))
    return sites, list(sources.values())
