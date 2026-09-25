# Changelog

Every release of the agent-guides bundle, newest first. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the bundle follows
[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). While the version is `0.0.z` every
release may change the format a carrier depends on (SemVer §4); a carrier reads what changed since the
version it holds with `bundle.py changelog --since <its version>`.

## [Unreleased]

## [0.0.22] - 2026-09-25

The first release under Semantic Versioning. Releases before it were numbered `v20`, `v21`; the home
repository tags them `v0.0.20` and `v0.0.21`, and keeps their method changelog.

### Changed

- **`.agents/` holds only what a carrier runs.** Coding sessions, harvests, updates, bootstrap and
  evaluation read from it; the full notes, the literature, the roadmap, the release records and the
  procedures that build and carry a release stay in the home repository.
- **The knowledge is generated.** Each note ships short (claim, mechanism, boundary, cost), and every index
  table is built from the notes' own fields. The area indexes gained a *Not when* column: a card is the
  claim, where it stops applying, and the check. Rows are ordered by topic, then by note, no longer by
  hand.
- **Versions and integrity use industry formats.** One Semantic Versioning version for the whole bundle, in
  the README's frontmatter; `SHA256SUMS` in the GNU coreutils format, which `sha256sum -c` verifies without
  this tool; this changelog in Keep a Changelog format.
- **A carrier's own fields live in `carrier.toml`**, which no release writes: its id, when it adopted the
  bundle, where it pulls from, what it adapted and what it declined.
- **`tracking/` is this carrier's outbox**: only what its harvest learned since the last release, refusals
  included (a row whose *Lacks* is `refused: <reason>`). The home reads it at the next release;
  `knowledge/OPEN.md` lists what the home is still waiting for.
- **The coding session consults the knowledge only when a change touches state, a contract, data, security
  or verification**, applies the card before opening a note, and follows the repository where it states an
  invariant that contradicts a note. `knowledge/INDEX.md` left the session's fixed load.
- A release arrives in `incoming/release/`, written by `bundle.py export` in a repository that holds it.
- `bundle.py` needs Python 3.11 or newer.

### Added

- `bundle.py verify`: checksums, links, routing, session reads, privacy, invisible characters, hidden and
  stray files, the outbox, the carrier file and `incoming/`, in one command; `--release` checks a release
  as it arrives, with the same refusals as `incoming/` applied to every file it carries.
- `bundle.py export DEST`: this bundle's shipped files and `SHA256SUMS`, never this repository's own.
- `bundle.py changelog --since X.Y.Z`, `bundle.py outbox --reset`, `bundle.py report --check` (static budgets).
- `incoming/` is refused when it holds invisible or bidirectional Unicode (in text or names), text that is
  not UTF-8, symbolic links, executable files, assistant, git or editor configuration and instruction files,
  or scripts other than the bundle's tool.
- Frontmatter is read as a documented subset of YAML: a plain value that YAML parsers would read as another
  type or refuse (`yes`, `1.0`, a date, `a: b`) must be quoted.

### Deprecated

- `bundle.py digest --check`: an alias of `verify` through 0.0.x.

### Removed

- Lineages, ancestries, fork points, per-document versions and the body digest recipe; `bundle.py stamp`
  and `bundle.py id g|m|k`. A fork is a git fork.
- The merge and sync procedures, the roadmap, the literature and the layout survey, which the home keeps.
- Retired notes: the home keeps them, so a carrier never pays for a note that was withdrawn.
