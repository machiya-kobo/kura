"""Kura's vaults ("sites"): the default vault, shared vaults and private vaults (docs/contracts/kura-api.md, "Vaults").

KURA_VAULTS="personal=/vault#personal, team+shared:Team=/vault#team, work:Work=/vault#work, notes=https://host/owner/notes.git#"
  name[+flag][:Title]=source#subdir, comma-separated. The first is the default vault; name is [a-z0-9-]+ and not "v"
  (the URL prefix). The one flag is +shared, on any vault but the default (FLAGS); it goes on the name, not the title,
  because a title is free text. source is a local directory (a mounted checkout, read-only) or a git URL (Kura
  clones it under KURA_REPO_DIR, once per distinct URL). Unset: KURA_REPO_* describe one vault, called
  notes (DEFAULT_NAME), whose folder is KURA_REPO_SUBDIR (default: the repo root).
Options per vault: KURA_VAULT_<NAME>_OBSIDIAN (the vault's name in Obsidian; default: its folder, else its name).

The default vault lives at /n/… as before; every other vault at /v/<name>/…. A vault without +shared is private (fail
closed: that is every vault of a KURA_VAULTS written before the flag): never pushed to Hister, never stored on a device
(Cache-Control: no-store), no external links, no feed, API-visible only when asked for. A shared vault is treated like
the default vault in all of that, at its /v/<name>/ addresses. Niwa and Konbini read one vault each (the default), so
no other vault gets their links, shared or not.
"""
import hashlib
import os
import re

from vaultkit import Git, Mirror, Vault, read_secret

NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*$")
FLAGS = ("shared",)
# "v" is the URL prefix; "default" and "shared" are words in an identity grant's vaults (vaultkit.identity), so a
# vault with either name would be granted to every principal that may read the default or the shared vaults.
RESERVED = ("v", "default", "shared")


DEFAULT_NAME, DEFAULT_TITLE = "notes", "Notes"      # the one vault of an install without KURA_VAULTS


class Site(Vault):
    """A vaultkit.Vault plus what Kura knows about it. `prefix` goes before /n/, /f/, /t/, /a/ in every link."""

    def __init__(self, source, subdir, name, title, default, obsidian="", shared=False):
        super().__init__(source.dir, subdir, git=source.git.run)
        self.checkout, self.name, self.title, self.default = source, name, title, default
        self.shared = bool(shared) and not default
        self.private = not default and not self.shared
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
    """KURA_VAULTS -> [(name, title, source, subdir, shared)]. Refuses to start on anything it can't read, and on
    a flag it doesn't know or +shared on the default vault (which is never private, so the flag would say nothing)."""
    out = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        head, sep, rest = part.partition("=")
        if not sep or not rest.strip():
            raise SystemExit("kura: KURA_VAULTS entry %r is not name[+shared][:Title]=source#subdir" % part.strip())
        name, _, title = head.strip().partition(":")
        name, *flags = name.strip().split("+")
        name, title, flags = name.strip(), title.strip(), [f.strip() for f in flags]
        for flag in flags:
            if flag not in FLAGS:
                raise SystemExit("kura: KURA_VAULTS: %r: unknown flag +%s (there is only +shared)" % (name, flag))
        if any(title.endswith("+" + f) for f in FLAGS):         # name:Title+shared would quietly be a private "Title+shared"
            raise SystemExit("kura: KURA_VAULTS: %r: a flag goes on the name: %s+shared:Title" % (name, name))
        if flags and not out:
            raise SystemExit("kura: KURA_VAULTS: %r is the default vault, which is never private: no +shared" % name)
        if not NAME_RE.match(name) or name in RESERVED:
            raise SystemExit("kura: KURA_VAULTS: %r is not a vault name (lowercase letters, digits, -; not v, default "
                             "or shared)" % name)
        if any(name == o[0] for o in out):
            raise SystemExit("kura: KURA_VAULTS: %r twice" % name)
        source, _, subdir = rest.strip().partition("#")
        out.append((name, title or name.replace("-", " ").title(), source.strip(), subdir.strip().strip("/"),
                    "shared" in flags))
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
        entries = [(DEFAULT_NAME, DEFAULT_TITLE, None, subdir, False)]
        made = [(e, src) for e in entries]
    else:
        made = [(e, source_of(e[2])) for e in entries]
    sites = []
    for i, ((name, title, _, sub, shared), src) in enumerate(made):
        key = "KURA_VAULT_%s_OBSIDIAN" % name.upper().replace("-", "_")
        sites.append(Site(src, sub, name, title, i == 0, env.get(key, "").strip(), shared))
    return sites, list(sources.values())
