#!/usr/bin/env python3
"""The agent-guides tool that every carrier runs. Python 3.11+, standard library only.

A carrier is a repository that holds this bundle in `.agents/`. This tool checks that copy against its
release (`SHA256SUMS`), checks what the carrier may write itself (its carrier file, its harvest outbox),
and mints the ids its records carry. The release itself is built and carried by the home repository's
own tool, never by this one.

    python3 .agents/tools/bundle.py verify [TREE]               everything a carrier's gate should fail on:
                                                                checksums, links, reachability, session reads,
                                                                privacy, invisible characters, outbox, incoming
    python3 .agents/tools/bundle.py check-local [REPO...]       the carrier changed only what it owns
    python3 .agents/tools/bundle.py privacy [TREE] [--paths FILE...] [--terms FILE]
                                                                nothing that identifies a private repository,
                                                                its people or its infrastructure
    python3 .agents/tools/bundle.py carrier-id [REPO] [--mint]  the carrier's stored random id; --mint writes one
    python3 .agents/tools/bundle.py id d|i|s TEXT... [--repo R] a record id: decision, roadmap item, session
    python3 .agents/tools/bundle.py ids [--carrier REPO] FILE... record ids in files: malformed, defined twice,
                                                                or defined under another carrier's id
    python3 .agents/tools/bundle.py report [TREE] [--json] [--check]   size per folder and session; budgets
    python3 .agents/tools/bundle.py changelog --since X.Y.Z     what changed after the version this carrier holds
    python3 .agents/tools/bundle.py export DEST                 the shipped files and SHA256SUMS: a release as it travels
    python3 .agents/tools/bundle.py outbox --reset              the harvest outbox back to its empty templates
    python3 .agents/tools/bundle.py digest [TREE] --check       deprecated alias of `verify` (0.0.x only)

With no REPO, the repositories are this session's workspace: `AGENT_WORKSPACE` if it is set, else the
local manifest (see `MANIFEST`), which lists this machine's paths and never travels with the bundle.
"""

from __future__ import annotations

import argparse
import builtins
import datetime
import functools
import hashlib
import io
import json
import math
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote


HERE = Path(__file__).resolve().parent
OWN_BUNDLE = HERE.parent
OWN_REPO = OWN_BUNDLE.parent

# Where this machine lists its carriers. Paths are per machine and never belong in the bundle: the
# bundle names a carrier only by its stored random id, in the home repository's carriers table.
MANIFEST = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agent-guides/carriers.toml"

# How a session says which repositories it has open. A machine carries more of this bundle than any
# session works on: the workspace is **all** the repositories open in this session and **only** those,
# because a carrier nobody is looking at must not receive a release, and an open one must not be left
# behind on an older version. Declared by argument, or by `AGENT_WORKSPACE` (paths separated as this
# platform separates them); the manifest is the fallback and says so.
WORKSPACE_ENV = "AGENT_WORKSPACE"


class NotACarrierError(RuntimeError):
    """A path given as a repository of the workspace holds no `.agents` bundle."""


class OutsideWorkspaceError(RuntimeError):
    """A carrier that this session does not have open, and therefore does not write to."""


class UndeclaredScopeError(RuntimeError):
    """Raised when a command that writes was given no scope, and would otherwise take every carrier."""


class DirtyTreeError(RuntimeError):
    """A carrier's bundle holds uncommitted changes that a write would overwrite."""


class RefusedError(RuntimeError):
    """A request the tool will not carry out as given: an empty set, an unknown name, an ambiguous key.

    Each of these used to surface as a traceback (`IndexError`, `KeyError`, `CalledProcessError`) or,
    worse, as silence; a refusal says what was asked and why it is not done, and exits 2.
    """


# --- documents -------------------------------------------------------------------------------------


def _hidden(rel: str) -> bool:
    """A dotfile or anything under a dot-folder: `.DS_Store`, an editor's swap file, a cache."""
    return any(part.startswith(".") for part in rel.split("/"))


def _header_span(text: str, suffix: str) -> tuple[int, int] | None:
    """Where a markdown document's frontmatter starts and ends, in characters, or None when it has none."""
    if suffix != ".md" or not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    return (0, end + 5) if end != -1 else None


def header(path: Path) -> str:
    """A document's frontmatter block, fences included, or the empty string."""
    text = path.read_text(encoding="utf-8")
    span = _header_span(text, path.suffix)
    return "" if span is None else text[span[0] : span[1]]


def field(front: str, name: str) -> str | None:
    """One top-level field's raw value, trailing comment dropped: how headers before 0.0.22 are read."""
    match = re.search(rf"^{name}:[ \t]*(.*?)[ \t]*(?:#.*)?$", front, re.MULTILINE)
    return match.group(1) if match else None


def _markdown(tree: Path) -> list[str]:
    """The shipped documents: what links, headings and sessions are read from."""
    return [rel for rel in shipped(tree) if rel.endswith(".md")]


# The note lifecycle: the folder a note sits in is its state. A carrier holds `active` and `review`;
# `retired` stays in the home repository, which is what keeps a withdrawn note from costing a carrier
# anything.
NOTE_STATES = ("active", "review", "retired")
NOTES = "knowledge/notes"


def _a_bundle(tree: Path) -> Path:
    """The tree, or a refusal: a check run over a folder that is not a bundle passes on nothing."""
    if not (tree / "README.md").exists() or not (tree / "method").is_dir():
        raise NotACarrierError(f"{tree} is not a bundle: expected `README.md` and `method/` in it")
    return tree


def _rels(tree: Path, pattern: str) -> list[str]:
    return [p.relative_to(tree).as_posix() for p in tree.glob(pattern) if p.is_file() and not _hidden(p.relative_to(tree).as_posix())]

# --- links -----------------------------------------------------------------------------------------
# A link is `](target)`, optionally `](<target> "title")`. Only relative ones are the bundle's to keep
# true: a scheme (`https:`, `mailto:`), an absolute path and a bare `#anchor` are skipped. Fenced
# blocks and inline code are examples, not pointers, and are skipped too.

LINK = re.compile(r"\]\(\s*<?([^()\s<>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
INLINE_CODE = re.compile(r"(`+).+?\1")
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _prose(lines: list[str]) -> list[bool]:
    """For each line, whether it is prose: outside every fenced block, fence lines included."""
    out, fence = [], None
    for line in lines:
        m = FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
            out.append(False)
        elif fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            out.append(False)
        else:
            out.append(True)
    return out


def _each_link(text: str, visit) -> tuple[str, int]:  # noqa: ANN001 -- visit: (target) -> str | None
    """Calls `visit` on every link target in prose; a string it returns replaces that target.

    Returns:
        The text with the replacements made, and how many were made.
    """
    lines = text.split("\n")
    count = 0
    for i, (line, prose) in enumerate(zip(lines, _prose(lines))):
        if not prose or "](" not in line:
            continue
        code = [m.span() for m in INLINE_CODE.finditer(line)]
        pieces, at = [], 0
        for m in LINK.finditer(line):
            if any(a <= m.start() < b for a, b in code):
                continue
            new = visit(m.group(1))
            if new is not None and new != m.group(1):
                pieces += [line[at : m.start(1)], new]
                at = m.end(1)
                count += 1
        lines[i] = "".join(pieces) + line[at:]
    return "\n".join(lines), count


def _relative(target: str) -> tuple[str, str] | None:
    """(path, `#anchor` or ""), or None for a link that is not a relative path."""
    if target.startswith(("#", "/")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    path, _, anchor = target.partition("#")
    return unquote(path), ("#" + anchor if anchor else "")


def _resolve(rel: str, target: str) -> str | None:
    """A link in document `rel`, as a path relative to the folder `rel` is relative to; None for a
    link that is not a relative path. A link that leaves that folder resolves to `../…`."""
    parts = _relative(target)
    if parts is None or not parts[0]:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(rel), parts[0]))


def _outside(resolved: str) -> bool:
    return resolved == ".." or resolved.startswith("../")


def _links(tree: Path, rel: str) -> list[tuple[str, str]]:
    """(target as written, bundle-relative path it resolves to) for every checkable link in `rel`."""
    found: list[tuple[str, str]] = []

    def visit(target: str) -> None:
        resolved = _resolve(rel, target)
        if resolved is not None:
            found.append((target, resolved))

    _each_link((tree / rel).read_text(encoding="utf-8", errors="replace"), visit)
    return found


def link_problems(tree: Path) -> list[str]:
    """Every relative link in a shipped document that is dead, or that leaves the bundle.

    A dead pointer reads as a reference until somebody follows it. A link out of `.agents/` points into
    whatever repository holds the copy, which is a different one in every carrier: in the home it finds
    the full notes and the release records, and in a carrier it finds nothing. The carrier's own
    files (`incoming/` contents, evaluation reports, the outbox) are not read.
    """
    problems = []
    for rel in _markdown(tree):
        for target, resolved in _links(tree, rel):
            if _outside(resolved):
                problems.append(f"{rel}: links to {target}, outside the bundle, which a carrier does not have")
            elif not (tree / resolved).exists():
                problems.append(f"{rel}: links to {target}, which does not exist")
    return problems


def reachability_problems(tree: Path) -> list[str]:
    """Every active or under-review note that neither `knowledge/INDEX.md` nor an area index links to.

    The index is the mechanism: a note it does not route to is never read at the moment it applies.
    A retired note is exempt, because being out of the routing tables is what retiring it means.
    """
    indexes = [rel for rel in ("knowledge/INDEX.md",) if (tree / rel).exists()] + sorted(_rels(tree, "knowledge/areas/*.md"))
    linked = {resolved for index in indexes for _, resolved in _links(tree, index)}
    return [
        f"{rel}: an {rel.split('/')[2]} note that no index links to (knowledge/INDEX.md or knowledge/areas/*.md)"
        for state in ("active", "review")
        for rel in sorted(_rels(tree, f"{NOTES}/{state}/**/*.md"), key=str.encode)
        if rel not in linked
    ]

# --- privacy ---------------------------------------------------------------------------------------
# Nothing in the bundle may let a reader identify, directly or by putting details together, a private
# repository, its owner, organisation, customers, users or infrastructure, or any person who uses or
# iterates the bundle: it travels through public carriers. Until 2026-09-24 that was a sentence in the
# method, and a review that day found the bundle full of what it forbids -- figures, exact constants,
# quoted code, field names, time zones, and carrier ids that could be reversed -- each one written by a
# session that had the sentence in front of it. A rule that is only remembered is not kept.
#
# The rules are generic and need no private word to run: they are the shapes a leak takes. What only
# this machine knows -- the names of its private repositories, owners, customers -- lives in a local
# terms file that never travels (`default_terms_path`), and every term in it is a failure too.
#
# A FAIL is a leak. A WARN is advisory: a large exact count or a long quote is sometimes the evidence
# itself, and only a reader tells a fingerprint from a round number. `privacy-allow: <reason>` on a line
# (in markdown, inside an HTML comment) waives that line, only on the user's explicit instruction, and
# every waiver is printed on every run, so none is silent.

# Where the evidence of a note is written. What a carrier's own code said is quoted and named there,
# so there, and in all of `tracking/`, code names and long quotes are read as what they usually are.
EVIDENCE_HEADINGS = ("where it came from", "evidence")
# Where published work is cited. A version, a count or a title quoted from a paper names the paper, not
# a carrier, so these sections and `references.md` are exempt from the quote, count and version rules.
LITERATURE_HEADINGS = ("literature",)
LITERATURE_FILES = ("references.md",)

# Mail domains the IETF reserves for examples and tests: an address there reaches nobody.
EXAMPLE_MAIL_DOMAINS = ("example.com", "example.org", "example.net", "example", "test", "invalid", "localhost")
EMAIL = re.compile(r"(?<![\w.%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-])")

# The forges whose paths are `owner/repo`. A path on one names somebody, and the owner is the part a
# reader follows to the rest of their work.
FORGE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "gitea.com", "git.sr.ht",
               "gist.github.com", "raw.githubusercontent.com")
FORGE = re.compile(
    r"(?<![\w.-])(?:[a-z+]+://)?(?:[\w.-]+@)?(" + "|".join(map(re.escape, FORGE_HOSTS)) + r")(?::\d+)?[/:]~?([\w.-]+)(?:/([\w.-]+))?",
    re.IGNORECASE)
# Owners that are placeholders in examples (`owner/repo`), and first path segments of a forge that are
# its own pages rather than an account.
FORGE_PLACEHOLDER_OWNERS = frozenset({"owner", "user", "you", "your-org", "your-name", "your-user", "org", "example", "me", "someone", "name"})
FORGE_PAGES = frozenset({"about", "features", "pricing", "marketplace", "topics", "settings", "login", "apps", "enterprise",
                         "security", "site", "explore", "collections", "readme", "en", "docs", "solutions", "resources", "trending"})
# Public projects the bundle cites as literature, as `owner/repo`, lowercase. A citation names a project
# the world already knows; any other forge path names somebody. None is cited on a forge today.
PUBLISHED_PROJECTS: frozenset[str] = frozenset()

# A home folder carries its owner's login name. Placeholders written in examples are not anybody's.
HOME_PATH = re.compile(r"(?<![\w/.~-])(?:/Us(?:ers)/|/ho(?:me)/|[A-Za-z]:\\Us(?:ers)\\)([^\s/\\`'\"<>)\]]+)|(?<=/)-Users-([A-Za-z0-9._]+)-")
HOME_PLACEHOLDERS = frozenset({"you", "user", "username", "me", "name", "example", "runner", "shared", "someone", "$user", "${user}", "<you>"})

# An id somebody chose, `[A-Z]{2,}-\d{2,}`, is a tracker's key: it names the project it was chosen in.
CHOSEN_ID = re.compile(r"(?<![\w-])([A-Z]{2,})-(\d{2,})(?![\w-])")
# Prefixes of the same shape that name a published standard, report or primitive, never a project.
CITATION_ID_PREFIXES = frozenset({"RFC", "CVE", "CWE", "ISO", "IEC", "IEEE", "ECMA", "ES", "PEP", "UTF", "UCS", "SHA", "AES", "RSA",
                                  "CRC", "HMAC", "ECDSA", "FIPS", "NIST", "SP", "ANSI", "HTTP", "TLS", "WCAG", "COVID"})
# Whole citation ids of that shape, for a report number that holds a prefix-and-number pair inside it.
CITATION_IDS = frozenset({"ESD-TR-73-51"})

CURRENCY_CODES = "USD|EUR|GBP|JPY|CNY|CHF|CAD|AUD|NZD|MXN|BRL|ARS|CLP|COP|PEN|UYU|INR|KRW|SGD|HKD|SEK|NOK|DKK|PLN|ZAR"
_AMOUNT = r"\d{1,3}(?:[,.\u00a0\u202f ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
CURRENCY = re.compile(
    rf"(?<![\w$\\{{])(?:US|CA|AU|NZ|HK|MX|R)?[$€£¥₹₩]\s?(?:{_AMOUNT})(?:\s?(?:k|K|M|MM|bn|million|thousand|billion)\b)?"
    rf"|\b(?:{CURRENCY_CODES})\s?\$?\s?(?:{_AMOUNT})"
    rf"|(?<![\w.])(?:{_AMOUNT})\s?(?:{CURRENCY_CODES}|(?i:dollars?|euros?|pesos?|yen))\b")
# `$1` is a shell's first argument far more often than a price, so a symbol before one lone digit is
# not read as an amount. A price that small fingerprints nothing.
SHELL_ARGUMENT = re.compile(r"^\$\d$")

# An offset or a zone name places a person on the map.
TIMEZONE = re.compile(
    r"\b(?:UTC|GMT)\s?[+\-−–]\s?\d{1,2}(?::?\d{2})?\b"
    r"|\b\d\d:\d\d(?::\d\d(?:\.\d+)?)?[+\-−]\d\d:?\d\d\b"
    r"|\bEtc/GMT[+-]\d+"
    r"|\b(?:Africa|America|Antarctica|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Z][A-Za-z_]+(?:/[A-Z][A-Za-z_]+)?")

# A pinned library version is the dependency list of one codebase, which a search engine matches.
_VERSION = r"v?\d+(?:\.\d+)+(?:[-+.]?(?:a|b|rc|f|p|post|dev)\d+)*"
VERSION_PIN = re.compile(
    rf"[\w.\[\]-]+\s?(?:===?|~=|>=|<=|!=|~>)\s?{_VERSION}"
    r"|(?<![\w.])@?[A-Za-z][\w./-]*@[\^~]?\d+\.\d+(?:\.\d+)*"
    r"|\"[@\w./-]+\"\s*:\s*\"[\^~>=<]*\s*\d+\.\d+(?:\.\d+)*[^\"]*\""
    r"|\b[A-Za-z][\w+.-]*\s+v?\d+\.\d+\.\d+(?:[-+][\w.]+)?(?![.\d])"
    r"|\b\d{4}\.\d+\.\d+[abfp]\d+\b")

# A carrier or record id next to what the carrier does ties the id to a kind of business. The list is
# broad on purpose: a list of only the domains this bundle's own carriers work in would name them.
CARRIER_OR_RECORD_ID = re.compile(r"(?<![\w-])(?:r-([0-9a-f]{6})|[dis]-([0-9a-f]{6})-[0-9a-f]{3,6})(?![\w-])")
# The hex of ids written as examples (`r-abcdef`, `d-abcdef-123456`): obviously nobody's.
EXAMPLE_ID_HEX = frozenset({"abcdef", "aaaaaa", "bbbbbb", "cccccc", "000000", "fedcba"})
DOMAIN_NOUNS = (
    "payment", "bank", "banking", "loan", "credit", "debt", "fraud", "card", "wallet", "invoice", "billing", "tax", "payroll",
    "insurance", "trading", "crypto", "national id", "passport", "citizen", "election", "voter", "court", "patient", "clinic",
    "hospital", "medical", "pharmacy", "student", "school", "university", "song", "music", "playlist", "album", "game",
    "character", "scene", "video", "podcast", "movie", "phone", "mobile", "tablet", "firmware", "iot", "controller", "sensor",
    "updater", "device fleet", "fleet", "vehicle", "drone", "robot", "printer", "cart", "checkout", "merchant", "retail",
    "restaurant", "booking", "hotel", "flight", "dating", "social network")
DOMAIN_NOUN = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in DOMAIN_NOUNS) + r")(?:e?s)?\b", re.IGNORECASE)

# Names in code: `camelCase`, `PascalCase` with two humps or more, `snake_case`, `SCREAMING_CASE`.
CODE_NAME_FORMS = (
    re.compile(r"^_*[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*$"),
    re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+$"),
    re.compile(r"^_*[a-z0-9]+(?:_[a-z0-9]+)+_*$"),
    re.compile(r"^_*[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+_*$"),
)
# Keys and names that vendors publish: an API's parameters, a CI's variables, a product's spelling.
# Naming one says which tool was used, which thousands of repositories share, not which repository.
VENDOR_KEYS = frozenset({
    "max_tokens", "stop_reason", "stop_sequences", "tool_use", "tool_result", "tool_choice", "cache_control", "input_schema",
    "pull_request", "workflow_dispatch", "GITHUB_TOKEN", "GITHUB_OUTPUT", "GITHUB_ENV", "XDG_CONFIG_HOME", "LC_ALL",
    "PYTHONPATH", "node_modules", "site_packages", "iOS", "macOS", "iPadOS", "tvOS", "watchOS", "GitHub", "GitLab",
    "JavaScript", "TypeScript", "PostgreSQL", "MySQL", "SQLite", "MongoDB", "OpenAPI", "WebSocket", "PowerShell", "DuckDB",
    "BigQuery", "PyPI", "YouTube", "LaTeX", "NumPy", "SciPy", "PyTorch", "TensorFlow", "FastAPI", "OpenAI", "DeepMind",
    "WebAssembly", "GraphQL", "DynamoDB", "CloudFormation", "PyInstaller", "OAuth",
})
# The bundle's own vocabulary, which every carrier already holds: header fields, the report's keys, the
# workspace variable. The tool's own names and Python's are added in `_public_names`.
BUNDLE_NAMES = frozenset({"forked_at", "retired_because", "superseded_by", "tokens_estimate", "context_share", "base_rule",
                          "base_from", "AGENT_WORKSPACE", "CLAUDE_md", "AGENTS_md"})

QUOTE = re.compile(r"\"([^\"\n]+)\"|“([^”\n]+)”")
BLOCKQUOTE = re.compile(r"^\s*>\s+(.*\S)")
# A large number, with or without thousands separators; years, dates, ids and decimals are not read.
LARGE_NUMBER = re.compile(r"(?<![\w.,:/#@$€£¥-])(\d{1,3}(?:[,\u00a0\u202f ]\d{3})+|\d{4,})(?![\w/:@%-]|[.,]\d)")
N_OF_M = re.compile(r"\b\d+\s+(?:of|out of)\s+(?:the\s+)?\d+\b")
# The waiver. Assembled, so that this file's own source does not read as a waiver of this line.
PRIVACY_ALLOW = re.compile("privacy-" + r"allow:[ \t]*([^`\n]*?)[ \t]*(?:-->|$)")
# A waiver written as documentation (`<reason>`) is an example of the syntax, not a waiver.
PLACEHOLDER_REASON = re.compile(r"^<[^>]*>$")
# What makes a fenced block an example rather than a record: a placeholder a reader has to replace.
PLACEHOLDER = re.compile(r"<[A-Za-z][\w .-]*>|\bexample\.(?:com|org|net)\b|\b(?:owner|OWNER)/(?:repo|REPO)\b|\b(?:YOUR|your)[-_]")


def default_terms_path() -> Path:
    """The local private terms file: one term per line, case-insensitive, never committed anywhere."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-guides/private-terms.txt"


def read_terms(path: Path) -> list[str]:
    """The terms of a terms file; blank lines and `#` comments skipped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


@functools.lru_cache(maxsize=None)
def _public_names() -> frozenset[str]:
    """Names that are public wherever they appear: vendor keys, the bundle's own, this tool's, Python's.

    This tool travels in every carrier, so its own names are already public; so are Python's builtins
    and the standard library modules it imports, which an incident about the tool names in its evidence.
    """
    names = set(VENDOR_KEYS) | set(BUNDLE_NAMES) | set(globals()) | set(dir(builtins))
    for module in (os, re, sys, io, json, math, hashlib, shutil, subprocess, tarfile, tempfile, argparse, datetime, posixpath,
                   secrets, functools, tomllib, unicodedata, Path, str, dict, list):
        names |= set(dir(module))
    return frozenset(names)


def _emails(text: str) -> list[str]:
    found = []
    for m in EMAIL.finditer(text):
        domain = m.group(2).lower()
        if any(domain == d or domain.endswith("." + d) for d in EXAMPLE_MAIL_DOMAINS):
            continue
        if m.group(1) == "git" and domain in FORGE_HOSTS:
            continue  # the ssh user of a public forge; the forge rule judges the path after it
        found.append(m.group(0))
    return found


def _home_paths(text: str) -> list[str]:
    return [m.group(0) for m in HOME_PATH.finditer(text) if (m.group(1) or m.group(2) or "").lower() not in HOME_PLACEHOLDERS]


def _forge_paths(text: str) -> list[str]:
    found = []
    for m in FORGE.finditer(text):
        owner, repo = m.group(2).lower(), (m.group(3) or "").lower().removesuffix(".git")
        if owner in FORGE_PLACEHOLDER_OWNERS or owner in FORGE_PAGES or f"{owner}/{repo}" in PUBLISHED_PROJECTS:
            continue
        found.append(m.group(0))
    return found


def _chosen_ids(text: str) -> list[str]:
    for citation in CITATION_IDS:
        text = text.replace(citation, " ")
    return [m.group(0) for m in CHOSEN_ID.finditer(text) if m.group(1) not in CITATION_ID_PREFIXES]


def _amounts(text: str) -> list[str]:
    return [m.group(0).strip() for m in CURRENCY.finditer(text) if not SHELL_ARGUMENT.match(m.group(0).strip())]


def _offsets(text: str) -> list[str]:
    return [m.group(0) for m in TIMEZONE.finditer(text)]


# Versions that name no codebase: this bundle's own (0.0.x while the format settles), and the published
# standards it follows, which every reader can look up.
OWN_VERSION = re.compile(r"(?:^|[\s\"])v?0\.0\.\d+\"?$")
NAMED_STANDARDS = re.compile(r"(?i)(?:semantic versioning|semver|keep a changelog|changelog|versioning)\s+v?\d+\.\d+\.\d+$")


def _version_pins(text: str) -> list[str]:
    return [m.group(0) for m in VERSION_PIN.finditer(text)
            if not OWN_VERSION.search(m.group(0)) and not NAMED_STANDARDS.search(m.group(0))]


def _ids_beside_domains(text: str) -> list[str]:
    ids = [m.group(0) for m in CARRIER_OR_RECORD_ID.finditer(text) if (m.group(1) or m.group(2)) not in EXAMPLE_ID_HEX]
    nouns = [m.group(0) for m in DOMAIN_NOUN.finditer(text)]
    return [f"{ids[0]} beside {nouns[0]!r}"] if ids and nouns else []


def _code_names(text: str) -> list[str]:
    found = []
    for span in INLINE_CODE.finditer(text):
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", span.group(0)):
            if token not in _public_names() and any(form.match(token) for form in CODE_NAME_FORMS):
                found.append(token)
    return found


def _large_counts(text: str) -> list[str]:
    found = []
    for m in LARGE_NUMBER.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(m.group(1)) == 4 and 1900 <= int(digits) <= 2100:
            continue  # a year
        if len(digits.rstrip("0")) < 2:
            continue  # one significant figure: an order of magnitude, which is what the rule asks for
        found.append(m.group(1))
    return found


def _n_of_m(text: str) -> list[str]:
    return [m.group(0) for m in N_OF_M.finditer(text)]


def _quotes(text: str) -> list[str]:
    quoted = [q for m in QUOTE.finditer(text) if len((q := m.group(1) or m.group(2)).split()) >= 5]
    block = BLOCKQUOTE.match(text)
    return quoted + ([block.group(1)] if block and len(block.group(1).split()) >= 5 else [])


@dataclass(frozen=True)
class PrivacyRule:
    """One shape of leak: how bad it is, what finds it, and where it is not read.

    Attributes:
        level: "FAIL" (a leak; `privacy` exits 1 and `digest --check` fails) or "WARN" (advisory).
        find: The offending pieces of one line.
        evidence_only: Read only in *Where it came from* and *Evidence* sections and in `tracking/`.
        literature_exempt: Not read in *Literature* sections nor in `references.md`.
        placeholder_exempt: Not read in a fenced block that shows an obvious placeholder.
    """

    level: str
    find: Callable[[str], list[str]]
    evidence_only: bool = False
    literature_exempt: bool = False
    placeholder_exempt: bool = False


PRIVACY_RULES: dict[str, PrivacyRule] = {
    "email": PrivacyRule("FAIL", _emails, placeholder_exempt=True),
    "home-path": PrivacyRule("FAIL", _home_paths, placeholder_exempt=True),
    "forge-url": PrivacyRule("FAIL", _forge_paths, placeholder_exempt=True),
    "chosen-id": PrivacyRule("FAIL", _chosen_ids),
    "currency": PrivacyRule("FAIL", _amounts),
    "timezone": PrivacyRule("FAIL", _offsets),
    "version-pin": PrivacyRule("FAIL", _version_pins, literature_exempt=True),
    "id-beside-domain": PrivacyRule("FAIL", _ids_beside_domains),
    "code-identifier": PrivacyRule("FAIL", _code_names, evidence_only=True),
    "exact-count": PrivacyRule("WARN", _large_counts, literature_exempt=True),
    "n-of-m": PrivacyRule("WARN", _n_of_m, literature_exempt=True),
    "quote": PrivacyRule("WARN", _quotes, evidence_only=True, literature_exempt=True),
}


def term_hits(text: str, terms: list[str]) -> list[int]:
    """The positions, in the terms file, of every private term the line holds."""
    return [i for i, term in enumerate(terms, 1)
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", text, re.IGNORECASE)]


@dataclass(frozen=True)
class PrivacyLine:
    """One line to read, and the scope it sits in."""

    shown: str
    number: int
    text: str
    evidence: bool
    literature: bool
    placeholder_fence: bool


@dataclass(frozen=True)
class Finding:
    level: str
    rule: str
    where: str
    match: str


@dataclass
class PrivacyReport:
    """What one `privacy` run found, what it was told to let pass, and which private terms it used."""

    findings: list[Finding]
    allowances: list[tuple[str, str]]
    files: int
    terms: str

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "FAIL"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "WARN"]

    def notes(self) -> list[str]:
        """The warnings, every waiver, and which terms were checked: printed whether or not anything failed."""
        return ([f"  ! WARN {f.where} {f.rule}: {f.match}" for f in self.warnings]
                + [f"  . allowed {where}: {reason}" for where, reason in self.allowances] + [f"  . {self.terms}"])

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f"{f.level} {f.rule}"] = counts.get(f"{f.level} {f.rule}", 0) + 1
        per_rule = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        return (f"privacy over {self.files} files: {len(self.failures)} FAIL, {len(self.warnings)} WARN, "
                f"{len(self.allowances)} allowed" + (f" ({per_rule})" if per_rule else ""))


def _bundle_rel(path: Path) -> str:
    """A file's path for scoping: inside the nearest `.agents` folder above it; from `meta/` or `sources/`
    for the home repository's own records (so `meta/tracking/` reads as evidence and `sources/references.md`
    as literature); else its name."""
    parts = path.resolve().parts
    for folder, keep in ((".agents", False), ("meta", True), ("sources", True)):
        if folder in parts:
            at = len(parts) - 1 - parts[::-1].index(folder)
            return "/".join(parts[at if keep else at + 1 :])
    return path.name


def _evidence_file(rel: str) -> bool:
    """`tracking/` in a bundle and in the home's records: where what a carrier's own code said is quoted."""
    return rel.startswith(("tracking/", "meta/tracking/"))


def _never_travels(rel: str) -> bool:  # noqa: D103
    """An evaluation report or material offered in `incoming/`: this repository's own, never published by
    the bundle, so not what the privacy check guards (an evaluation report names its repository)."""
    parts = rel.split("/")
    return (len(parts) == 1 and parts[0].startswith("evaluation-")) or (parts[0] == "incoming" and rel != "incoming/README.md")


def all_files(tree: Path) -> list[str]:
    """Every file of a bundle, the carrier's own and hidden ones included: what privacy, invisible-text and
    stray-file checks read. Only caches and a file browser's `.DS_Store` are left out."""
    return sorted((rel for p in tree.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                   and (rel := p.relative_to(tree).as_posix()).rsplit("/", 1)[-1] != ".DS_Store"), key=str.encode)


def stray_problems(tree: Path) -> list[str]:
    """Files no release ships and no carrier writes: hidden files, and anything in the carrier's own space
    that is not its carrier file, its two outbox files or a markdown evaluation report."""
    problems = []
    for rel in all_files(tree):
        top = rel.split("/")
        if top[0] == "incoming":
            continue
        if _hidden(rel):
            problems.append(f"{rel}: a hidden file in the bundle; no release ships one and no check reads it")
        elif is_carrier_owned(rel) and rel != CARRIER_FILE and rel not in OUTBOX and not (
                len(top) == 1 and rel.startswith("evaluation-") and rel.endswith(".md")):
            problems.append(f"{rel}: not a file this repository owns in the bundle (carrier.toml, the outbox, evaluation-*.md)")
    return problems


def _privacy_lines(path: Path, rel: str, shown: str) -> list[PrivacyLine]:
    """Every line of a file with its scope: evidence, literature, and fenced with a placeholder.

    Headings are read in markdown only, outside its frontmatter and its fenced blocks, as `section`
    reads them; a section runs to the next heading of the same or a higher level.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    evidence_file, literature_file = _evidence_file(rel), rel.rsplit("/", 1)[-1] in LITERATURE_FILES
    if path.suffix != ".md":
        return [PrivacyLine(shown, n, line, evidence_file, literature_file, False) for n, line in enumerate(lines, 1)]
    span = _header_span(text, ".md")
    skip = text[: span[1]].count("\n") if span else 0
    prose = _prose(lines)
    placeholder = [False] * len(lines)
    i = 0
    while i < len(lines):
        if prose[i]:
            i += 1
            continue
        j = i
        while j < len(lines) and not prose[j]:
            j += 1
        shows = bool(PLACEHOLDER.search("\n".join(lines[i:j])))
        placeholder[i:j] = [shows] * (j - i)
        i = j
    out, stack = [], []  # stack: (level, heading text, lowercased)
    for n, line in enumerate(lines):
        m = HEADING.match(line) if prose[n] and n >= skip else None
        if m:
            level = len(m.group(1))
            stack = [h for h in stack if h[0] < level] + [(level, m.group(2).strip("*_ ").lower())]
        titles = [t for _, t in stack]
        evidence = evidence_file or any(t.startswith(EVIDENCE_HEADINGS) for t in titles)
        literature = literature_file or any(t.startswith(LITERATURE_HEADINGS) for t in titles)
        out.append(PrivacyLine(shown, n + 1, line, evidence, literature, placeholder[n] and not prose[n]))
    return out


def privacy_check(tree: Path | None = None, paths: list[Path] | None = None, terms_file: Path | None = None) -> PrivacyReport:
    """Reads every travelling file of a bundle, or the given files, for what could identify somebody.

    Args:
        tree: The `.agents` folder whose travelling files are read. Ignored when `paths` is given.
        paths: Files to read instead, anywhere: a hook checks a repository's README with the same rules.
        terms_file: The private terms; default `default_terms_path()`. A default that is missing means
            no terms, said in the report; a file named here that is missing is refused, because a typo
            would otherwise check nothing and pass.

    Returns:
        The findings, the waivers honoured, the number of files read, and a line about the terms.
    """
    if terms_file is not None and not terms_file.is_file():
        raise RefusedError(f"--terms {terms_file}: no such file")
    source = terms_file or default_terms_path()
    terms = read_terms(source) if source.is_file() else []
    # Printed with `~` for the home folder: this line is pasted into sessions, and a home path is one
    # of the things this check exists to catch.
    shown = str(source).replace(str(Path.home()), "~", 1)
    note = f"private terms: {len(terms)} from {shown}" if source.is_file() else f"no private terms checked: {shown} does not exist"
    if paths:
        targets = [(p, _bundle_rel(p), str(p)) for p in paths]
    else:
        tree = _a_bundle(tree if tree is not None else OWN_BUNDLE)
        targets = [(tree / rel, rel, rel) for rel in all_files(tree) if not _never_travels(rel)]
    findings: list[Finding] = []
    allowances: list[tuple[str, str]] = []
    for path, rel, shown in targets:
        for line in _privacy_lines(path, rel, shown):
            where = f"{line.shown}:{line.number}"
            found: list[Finding] = []
            for name, rule in PRIVACY_RULES.items():
                if (rule.evidence_only and not line.evidence) or (rule.literature_exempt and line.literature) \
                        or (rule.placeholder_exempt and line.placeholder_fence):
                    continue
                found += [Finding(rule.level, name, where, hit) for hit in rule.find(line.text)]
            # The term itself is not printed: this output is pasted into sessions and changelogs, and
            # the one thing it must not carry is the word it caught.
            found += [Finding("FAIL", "private-term", where, f"term on line {i} of the terms file")
                      for i in term_hits(line.text, terms)]
            code = [m.span() for m in INLINE_CODE.finditer(line.text)]
            marker = next((m for m in PRIVACY_ALLOW.finditer(line.text)
                           if not any(a <= m.start() < b for a, b in code)), None)
            reason = marker.group(1).strip() if marker else ""
            if marker and PLACEHOLDER_REASON.match(reason):
                marker = None  # the syntax, written as an example of itself
            if marker and not reason:
                found.append(Finding("FAIL", "allow-without-reason", where, "a waiver must say why, and who asked"))
            elif marker:
                allowances.append((where, reason))
                found = []
            findings += found
    return PrivacyReport(findings, allowances, len(targets), note)


# --- sessions and their size ------------------------------------------------------------------------
# What each type of session loads is declared once, in the method: every invocation carries a `Reads:`
# list, one line per file, `- <bundle-relative path>` followed by ` §<heading>` for each section it
# needs (none: the whole file). A heading means only that section, from its line to the next heading
# of the same or a higher level. The tool reads those lists rather than keeping a copy of them: a table
# here would be a second source of truth, and the first draft of this command was exactly that, marked
# provisional until somebody reconciled it by hand. `SESSION_SOURCES` only says where each list is:
# the file, and which `Reads:` list in it counting from 0, because the bootstrap document carries two
# (the bootstrap itself, and the working invocation it installs for every coding session).
SESSION_SOURCES: dict[str, tuple[str, int]] = {
    "coding": ("method/prompt-bootstrap.md", 1),
    "consult": ("README.md", 0),
    "evaluate": ("method/prompt-evaluate.md", 0),
    "bootstrap": ("method/prompt-bootstrap.md", 0),
    "update": ("method/prompt-update.md", 0),
    "harvest": ("method/prompt-harvest.md", 0),
}
Sessions = "dict[str, list[tuple[str, str | None]]]"


def reads_lists(text: str) -> list[list[tuple[str, str | None]]]:
    """Every `Reads:` list in a document, in order, as (path, heading or None) parts."""
    lists, lines = [], text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() != "Reads:":
            continue
        parts: list[tuple[str, str | None]] = []
        for item in lines[i + 1 :]:
            if not item.startswith("- "):
                break
            path, *headings = [piece.strip() for piece in item[2:].split(" §")]
            parts += [(path, h) for h in headings] if headings else [(path, None)]
        lists.append(parts)
    return lists


def sessions_of(tree: Path) -> tuple[dict[str, list[tuple[str, str | None]]], list[str]]:
    """The load set of every session type, read from the method's `Reads:` lists, and what is missing."""
    sessions, missing = {}, []
    for name, (rel, index) in SESSION_SOURCES.items():
        path = tree / rel
        found = reads_lists(path.read_text(encoding="utf-8")) if path.is_file() else []
        if index < len(found):
            sessions[name] = found[index]
        else:
            missing.append(f"session {name!r}: {rel} has no `Reads:` list number {index}")
    return sessions, missing


CONTEXT_WINDOW = 200_000
HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")


def section(path: Path, heading: str | None) -> str | None:
    """A document, or one section of it: the heading line to the next heading of the same or higher level.

    Headings are read outside the provenance header and outside fenced blocks, where `# CLAUDE.md`
    in a template is an example and not a section of the method. None when the heading is absent.
    """
    text = path.read_text(encoding="utf-8")
    if heading is None:
        return text
    span = _header_span(text, path.suffix)
    skip = text[: span[1]].count("\n") if span else 0
    lines = text.split("\n")
    prose = _prose(lines)
    start = level = None
    for i in range(skip, len(lines)):
        m = HEADING.match(lines[i]) if prose[i] else None
        if not m:
            continue
        if start is None and m.group(2) == heading:
            start, level = i, len(m.group(1))
        elif start is not None and len(m.group(1)) <= level:
            return "\n".join(lines[start:i]) + "\n"
    return None if start is None else "\n".join(lines[start:])


def _session_parts(tree: Path, entries: list[tuple[str, str | None]]) -> tuple[list[tuple[str, str]], list[str]]:
    """(label, text) for every part of one session that is found, and a problem for every part that is not."""
    parts, missing = [], []
    for pattern, heading in entries:
        rels = sorted(_rels(tree, pattern), key=str.encode) if any(c in pattern for c in "*?[") else [pattern]
        if not rels:
            missing.append(f"{pattern}: matches no file")
        for rel in rels:
            label = rel + (f" § {heading}" if heading else "")
            if not (tree / rel).is_file():
                missing.append(f"{rel}: no such file")
                continue
            text = section(tree / rel, heading)
            if text is None:
                missing.append(f"{rel}: no heading {heading!r}")
            else:
                parts.append((label, text))
    return parts, missing


def session_problems(tree: Path, sessions: dict[str, list[tuple[str, str | None]]] | None = None) -> list[str]:
    """Every session whose `Reads:` list is missing, or names a file or a heading the tree does not have."""
    missing: list[str] = []
    if sessions is None:
        sessions, missing = sessions_of(tree)
    return missing + [f"session {name!r}: {problem}" for name, entries in sessions.items() for problem in _session_parts(tree, entries)[1]]


def _tokens(chars: int) -> int:
    return math.ceil(chars / 4)


def _folder(rel: str) -> str:
    """The folder a file is counted under: its first segment, two more under `knowledge/notes/` so states show."""
    parts = rel.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[:2] == ["knowledge", "notes"]:
        return "/".join(parts[:3]) if len(parts) > 3 else NOTES
    return parts[0]


def report(tree: Path, sessions: dict[str, list[tuple[str, str | None]]] | None = None) -> dict:
    """The size of what travels, per folder and per session type.

    Tokens are an **estimate**, `ceil(characters / 4)`, not a tokenizer's count: close enough to
    compare one release with the last and one session type with another, and labelled so nobody
    quotes it as a measurement.
    """
    sessions = sessions_of(tree)[0] if sessions is None else sessions
    folders: dict[str, dict[str, int]] = {}
    total = {"files": 0, "bytes": 0, "chars": 0}
    for rel in shipped(_a_bundle(tree)):
        data = (tree / rel).read_bytes()
        chars = len(data.decode("utf-8", errors="replace"))
        for row in (folders.setdefault(_folder(rel), {"files": 0, "bytes": 0, "chars": 0}), total):
            row["files"] += 1
            row["bytes"] += len(data)
            row["chars"] += chars
    for row in (*folders.values(), total):
        row["tokens_estimate"] = _tokens(row["chars"])
    by_session = {}
    for name, entries in sessions.items():
        parts, missing = _session_parts(tree, entries)
        chars = sum(len(text) for _, text in parts)
        tokens = _tokens(chars)
        by_session[name] = {
            "parts": [label for label, _ in parts],
            "missing": missing,
            "bytes": sum(len(text.encode()) for _, text in parts),
            "chars": chars,
            "tokens_estimate": tokens,
            "context_share": round(tokens / CONTEXT_WINDOW, 4),
        }
    return {
        "tree": str(tree),
        "tokens": "estimate: ceil(characters / 4)",
        "context_window": CONTEXT_WINDOW,
        "total": total,
        "folders": dict(sorted(folders.items(), key=lambda item: item[0].encode())),
        "sessions": by_session,
    }


def report_markdown(data: dict) -> str:
    """The report as two markdown tables, the estimate labelled in both."""
    lines = [
        f"# Bundle size — {data['tree']}",
        "",
        f"Tokens are an {data['tokens']}, not a tokenizer's count.",
        "",
        "| Folder | Files | Bytes | Tokens (est.) |",
        "|---|---:|---:|---:|",
        *[f"| {name} | {r['files']} | {r['bytes']} | {r['tokens_estimate']} |" for name, r in data["folders"].items()],
        f"| **total** | {data['total']['files']} | {data['total']['bytes']} | {data['total']['tokens_estimate']} |",
        "",
        f"| Session | Parts | Bytes | Tokens (est.) | Share of {data['context_window'] // 1000}k |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, s in data["sessions"].items():
        flag = f" (missing: {'; '.join(s['missing'])})" if s["missing"] else ""
        lines.append(f"| {name}{flag} | {len(s['parts'])} | {s['bytes']} | {s['tokens_estimate']} | {s['context_share']:.1%} |")
    return "\n".join(lines) + "\n"

# --- ids -------------------------------------------------------------------------------------------


# A carrier's id is random, minted once, and stored in the carrier's own file, `.agents/carrier.toml`,
# which no release writes, so every release keeps it.
#
# Until 2026-09-24 it was derived from the repository's remote, on the claim that a hash names the
# repository without naming it. It did not: a hash of an input somebody can guess is reversed by
# guessing, and one carrier was reversed from its owner's public repositories in a few dozen guesses.
# Six random hex say nothing about the repository; what ties them to it is only that it stores them.
CARRIER_FIELD = "carrier"
CARRIER_ID = re.compile(r"^r-[0-9a-f]{6}$")


def git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=not binary)
    return result.stdout


def stored_carrier_id(repo: Path) -> str | None:
    """The id a repository stores in `.agents/carrier.toml`, or None when it stores none.

    Raises:
        NotACarrierError: If the repository holds no bundle.
        RefusedError: If the stored value is not `r-` and six hex: a hand edit, and not an id to build on.
    """
    if not (repo / ".agents").is_dir():
        raise NotACarrierError(f"{repo}: no .agents folder, so no carrier id to read")
    value = (read_carrier(repo / ".agents") or {}).get(CARRIER_FIELD)
    if value is None:
        return None
    if not CARRIER_ID.match(value):
        raise RefusedError(f"{repo / '.agents' / CARRIER_FILE}: `{CARRIER_FIELD} = {value!r}` is not `r-` and six hex")
    return value


def repo_carrier_id(repo: Path) -> str:
    """A repository's stored id, or a refusal that names the command minting one.

    Never computed as a fallback: an id worked out when none is stored would have to come from
    something about the repository, which is the reversible id this replaced.
    """
    value = stored_carrier_id(repo)
    if value is None:
        raise RefusedError(
            f"{repo}: its .agents/{CARRIER_FILE} stores no `{CARRIER_FIELD}` id; mint one with "
            f"`bundle.py carrier-id --mint {repo}`, once, and commit it")
    return value


def mint_carrier_id(repo: Path, today: str | None = None) -> str:
    """Writes a new random id into a repository's carrier file, creating the file if needed, and returns it.

    Refused when one is already stored, never replaced: records carry the id as their prefix
    (`d-<repo6>-...`) and the home's carriers table keys its rows by it, so a second id would orphan both.
    A new carrier file also records today as the day the repository adopted the bundle.
    """
    present = stored_carrier_id(repo)
    if present is not None:
        raise RefusedError(f"{repo}: already stores {present}; an id is minted once and never replaced")
    tree = repo / ".agents"
    data = read_carrier(tree) or {"adopted": today or datetime.date.today().isoformat(), "upstream": "",
                                   "adapted": [], "declined": []}
    minted = "r-" + secrets.token_hex(3)
    write_carrier(tree, {CARRIER_FIELD: minted, **data})
    return minted


def carrier_ids(repos: list[Path]) -> dict[Path, str]:
    """Every repository's stored id, or a refusal when two of them store the same one.

    A bundle copied whole into a new repository brings the old one's carrier file, id included, and the
    two would then be one row of the carriers table and one prefix of records.
    """
    ids = {repo: repo_carrier_id(repo) for repo in repos}
    seen: dict[str, Path] = {}
    for repo, value in ids.items():
        if value in seen:
            raise RefusedError(
                f"{seen[value]} and {repo} both store {value}: a bundle copied from one repository carries its id; "
                f"delete `{CARRIER_FIELD}` from the copy's {CARRIER_FILE} and run `bundle.py carrier-id --mint` there")
        seen[value] = repo
    return ids


# Records that parallel sessions write in one carrier: a decision, a roadmap item, a session entry.
# A counter (`D-017`) is what two sessions pick at once, and what one carrier's log shares with the
# next carrier's; an id derived from the carrier and the text is neither.
RECORD_KINDS = {"d": "decision", "i": "roadmap item", "s": "session entry"}


def record_id(kind: str, text: str, carrier: str) -> str:
    """`<kind>-<repo6>-<content6>`: the carrier's six hex, then six hex of the text, whitespace collapsed.

    Minted once, when the record is written, and frozen: a later edit to the record does not move
    its id, so an audit checks the form, the prefix and uniqueness, never the hash against the text.
    """
    if kind not in RECORD_KINDS:
        raise RefusedError(f"record kind {kind!r}: one of {', '.join(RECORD_KINDS)}")
    content = " ".join(text.split())
    if not content:
        raise RefusedError("a record id is minted from the record's text, and none was given")
    return f"{kind}-{carrier.removeprefix('r-')}-{hashlib.sha256(content.encode()).hexdigest()[:6]}"


# How a record id is found in a document, and what counts as writing one down rather than citing it.
# A record is defined once, where it is written: a row whose first cell is the id (`| d-... |`), or a
# heading that carries it after a `·` (`## 2026-01-01 · s-... — title`). Anywhere else the id is a
# citation, and a citation may repeat. Fenced blocks are examples and are not read.
RECORD_ID = re.compile(r"\b[dis]-[0-9a-f]{6}-[0-9a-f]{6}\b")
# The first scheme ended in a sequence number (`d-abcdef-017`). Ids written under it stay valid as
# written and are never rewritten, so they are recognised, not reported as malformed.
LEGACY_RECORD_ID = re.compile(r"\b[dis]-[0-9a-f]{6}-\d{3}\b")
# What looks like an attempt at one: the kind letter, two parts, a digit somewhere. `d-abcdef-12345`
# (a digit dropped) and `D-ABCDEF-123456` (a case changed) are ids somebody meant, and a check that
# only reads well-formed ids never sees the ones that went wrong.
RECORD_ID_ATTEMPT = re.compile(r"(?<![\w-])[disDIS]-([0-9A-Za-z]{2,12})-([0-9A-Za-z]{2,12})(?![\w-])")
RECORD_DEFINITION = re.compile(r"^\s*\|\s*([dis]-[0-9a-f]{6}-[0-9a-f]{3,6})\s*\||^#{1,6}\s.*·\s*([dis]-[0-9a-f]{6}-[0-9a-f]{3,6})\b")


def record_id_check(files: list[Path], carrier: str | None) -> tuple[list[str], list[str], dict[str, int]]:
    """The record ids of some files: what is wrong with them, what is suspect, and how many there are.

    An id is minted once and frozen, so an audit checks its form, its prefix and its uniqueness, never
    its hash against the text (`record_id`). Uniqueness is across every file given: the same record
    written down twice is two records that will be cited as one.

    The prefix is only a warning. A record this carrier writes carries this carrier's id, but the tool
    cannot tell this carrier's own records from a file that keeps another carrier's, so a definition
    under another prefix is named, with its file, and left to the reader.

    Args:
        files: The documents to read.
        carrier: This carrier's id (`r-...`), or None when it has none: then no prefix is checked.

    Returns:
        (errors: duplicates and malformed ids, warnings: another carrier's prefix, counts).
    """
    errors: list[str] = []
    warnings: list[str] = []
    counts = {"ids": 0, "definitions": 0, "citations": 0, "legacy": 0}
    defined: dict[str, str] = {}
    own = carrier.removeprefix("r-") if carrier else None
    for path in files:
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        for number, (line, prose) in enumerate(zip(lines, _prose(lines)), 1):
            if not prose:
                continue
            where = f"{path}:{number}"
            found_here = sorted([*RECORD_ID.finditer(line), *LEGACY_RECORD_ID.finditer(line)], key=lambda m: m.start())
            well_formed = {m.group(0) for m in found_here}
            for m in RECORD_ID_ATTEMPT.finditer(line):
                if m.group(0) not in well_formed and any(c.isdigit() for c in m.group(1) + m.group(2)):
                    errors.append(f"{where}: malformed record id {m.group(0)!r} (expected <d|i|s>-<6 hex>-<6 hex>)")
            definition = RECORD_DEFINITION.match(line)
            name = definition and (definition.group(1) or definition.group(2))
            if name and name not in well_formed:
                name = None  # a malformed id in a definition slot, reported above
            for index, occurrence in enumerate(found_here):
                found = occurrence.group(0)
                counts["ids"] += 1
                counts["legacy"] += bool(LEGACY_RECORD_ID.fullmatch(found))
                if found != name or index != [m.group(0) for m in found_here].index(name):
                    counts["citations"] += 1
                    continue
                counts["definitions"] += 1
                if found in defined:
                    errors.append(f"{where}: {found} defined twice (first at {defined[found]})")
                else:
                    defined[found] = where
                if own and found.split("-")[1] != own:
                    warnings.append(f"{where}: {found} is defined here under r-{found.split('-')[1]}, "
                                    f"not this carrier's {carrier}; fine only if this file keeps another carrier's records")
    return errors, warnings, counts

# --- formats: frontmatter, carrier file, checksums, versions -----------------------------------------
# Every format here is an industry one, read by a documented subset so the tool stays standard library
# only: YAML frontmatter, TOML (`tomllib` reads it; a small writer writes the one file the tool owns),
# the `SHA256SUMS` file of GNU coreutils (`sha256sum -c` / `shasum -a 256 -c` verify it without this
# tool), Semantic Versioning 2.0.0 and Keep a Changelog 1.1.0.


class FrontmatterError(ValueError):
    """Frontmatter outside the YAML subset the tool reads: refused rather than guessed at."""


FRONT_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?: +|$)")
# A plain (unquoted) scalar is kept only when every YAML parser reads it as this same string: no `: ` or
# trailing `:`, no tab or control character, no line separator, and not a word or number YAML types.
PLAIN_REFUSED = re.compile(r":\s|:$|[\t\x00-\x08\x0b-\x1f\x7f\x85\u2028\u2029]| #")
PLAIN_TYPED = re.compile(  # PyYAML's implicit resolvers (bool, int, float, null, timestamp, merge, value), and y/n
    r"(?i)y|n|yes|no|on|off|true|false|null|~|<<|="
    r"|[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-f_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+"
    r"|[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:e[-+][0-9]+)?|\.[0-9_]+(?:e[-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*"
    r"|[-+]?\.inf|\.nan|[-+]?[0-9_]+(?:\.[0-9_]*)?(?:e[-+]?[0-9]+)?"
    r"|[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:t|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?(?:[ \t]*(?:z|[-+][0-9]{1,2}(?::[0-9]{2})?))?")
_DQ_ESCAPES = {"\\": "\\", '"': '"', "/": "/", "n": "\n", "t": "\t", "r": "\r", "0": "\0", " ": " "}


class _Scanner:
    """Reads YAML scalars and flow collections from one frontmatter block, position by position."""

    def __init__(self, text: str, where: str) -> None:
        self.text, self.at, self.where = text, 0, where

    def fail(self, message: str) -> FrontmatterError:
        line = self.text.count("\n", 0, self.at) + 1
        return FrontmatterError(f"{self.where}: frontmatter line {line}: {message}")

    def peek(self) -> str:
        return self.text[self.at] if self.at < len(self.text) else ""

    def skip_spaces(self, newlines: bool = False) -> None:
        while self.peek() in (" \t\n" if newlines else " \t") and self.peek():
            self.at += 1

    def quoted(self) -> str:
        quote = self.text[self.at]
        self.at += 1
        out: list[str] = []
        while True:
            if self.at >= len(self.text):
                raise self.fail("unterminated quoted string")
            ch = self.text[self.at]
            if ch == quote:
                if quote == "'" and self.text[self.at + 1 : self.at + 2] == "'":
                    out.append("'")
                    self.at += 2
                    continue
                self.at += 1
                return "".join(out)
            if ch == "\\" and quote == '"':
                code = self.text[self.at + 1 : self.at + 2]
                if code in _DQ_ESCAPES:
                    out.append(_DQ_ESCAPES[code])
                    self.at += 2
                elif code in ("u", "U", "x"):
                    width = {"u": 4, "U": 8, "x": 2}[code]
                    digits = self.text[self.at + 2 : self.at + 2 + width]
                    if not re.fullmatch(r"[0-9A-Fa-f]+", digits) or len(digits) != width:
                        raise self.fail(f"bad \\{code} escape")
                    out.append(chr(int(digits, 16)))
                    self.at += 2 + width
                else:
                    raise self.fail(f"unknown escape \\{code}")
                continue
            if ch == "\n":
                # A line break inside a quoted scalar folds: to a space, or to one newline per empty line.
                while out and out[-1] in " \t":
                    out.pop()
                breaks = 0
                while self.at < len(self.text) and self.text[self.at] in " \t\n":
                    breaks += self.text[self.at] == "\n"
                    self.at += 1
                out.append(" " if breaks == 1 else "\n" * (breaks - 1))
                continue
            out.append(ch)
            self.at += 1

    def plain(self, flow: bool) -> str | None:
        start = self.at
        stops = ",]}" if flow else ""
        while self.at < len(self.text):
            ch = self.text[self.at]
            if ch == "\n" or ch in stops or (ch == "#" and self.text[self.at - 1] in " \t"):
                break
            self.at += 1
        value = self.text[start : self.at].strip(" ")
        if value in ("", "~", "null"):
            return None
        if value[:1] in "&*!|>@`%-?:,=<[]{}#'\"" or PLAIN_REFUSED.search(value) or PLAIN_TYPED.fullmatch(value):
            raise self.fail(f"the plain scalar {value[:30]!r} is read differently by YAML parsers; quote it")
        return value

    def value(self, flow: bool = False) -> object:
        self.skip_spaces(newlines=flow)
        ch = self.peek()
        if ch in ('"', "'"):
            return self.quoted()
        if ch == "[":
            return self.sequence()
        if ch == "{":
            return self.mapping()
        return self.plain(flow)

    def sequence(self) -> list:
        self.at += 1
        items: list = []
        while True:
            self.skip_spaces(newlines=True)
            if self.peek() == "]":
                self.at += 1
                return items
            if self.peek() == ",":
                raise self.fail("an empty item in a flow sequence")
            items.append(self.value(flow=True))
            self.skip_spaces(newlines=True)
            if self.peek() == ",":
                self.at += 1
            elif self.peek() != "]":
                raise self.fail("expected `,` or `]` in a flow sequence")

    def mapping(self) -> dict:
        self.at += 1
        items: dict = {}
        while True:
            self.skip_spaces(newlines=True)
            if self.peek() == "}":
                self.at += 1
                return items
            key = re.match(r"[A-Za-z_][\w-]*", self.text[self.at :])
            if not key or self.text[self.at + key.end() : self.at + key.end() + 1] != ":":
                raise self.fail("expected `key:` in a flow mapping")
            self.at += key.end() + 1
            items[key.group(0)] = self.value(flow=True)
            self.skip_spaces(newlines=True)
            if self.peek() == ",":
                self.at += 1
            elif self.peek() != "}":
                raise self.fail("expected `,` or `}` in a flow mapping")

    def rest_of_line(self) -> None:
        self.skip_spaces()
        if self.peek() == "#":
            while self.peek() and self.peek() != "\n":
                self.at += 1
        if self.peek() not in ("\n", ""):
            raise self.fail(f"unexpected {self.text[self.at:self.at + 20]!r} after a value")
        if self.peek() == "\n":
            self.at += 1


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(frontmatter block without its fences, body), or (None, text) when the file has none."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end == -1:
        return None, text
    return text[4 : end + 1], text[end + 5 :]


def parse_frontmatter(block: str, where: str = "frontmatter") -> dict:
    """Reads the YAML subset the bundle writes, and refuses the rest.

    Top-level `key: value` pairs, where a value is a double- or single-quoted scalar (folded across
    lines as YAML folds it), a plain scalar (a string; `null`, `~` and nothing are None), a flow
    sequence `[a, "b"]`, a flow mapping `{k: "v"}`, or, after an empty `key:`, a block sequence of
    `  - value` items. Comments and blank lines are skipped. Anchors, tags, block scalars and nested
    block collections are outside the subset and refused: a reader that guessed at them would read
    something else than the YAML parser a carrier's own tooling uses.
    """
    scan = _Scanner(block, where)
    data: dict = {}
    while scan.at < len(block):
        line_end = block.find("\n", scan.at)
        line = block[scan.at : line_end if line_end != -1 else len(block)]
        if not line.strip() or line.lstrip().startswith("#"):
            scan.at += len(line) + 1
            continue
        key = FRONT_KEY.match(line)
        if not key:
            raise scan.fail(f"expected a top-level `key:`, got {line[:40]!r}")
        if key.group(1) in data:
            raise scan.fail(f"`{key.group(1)}` is given twice")
        scan.at += key.end()
        scan.skip_spaces()
        if scan.peek() in ("\n", "#", ""):
            scan.rest_of_line()
            items = []
            indent = "  - " if block.startswith("  - ", scan.at) else "- "
            while block.startswith(indent, scan.at):
                scan.at += len(indent)
                items.append(scan.value())
                scan.rest_of_line()
            if block.startswith(("  -", "- ", "    -"), scan.at):
                raise scan.fail("a block sequence item at another indentation")
            data[key.group(1)] = items if items else None
            continue
        data[key.group(1)] = scan.value()
        scan.rest_of_line()
    return data


def read_frontmatter(text: str, where: str = "frontmatter") -> tuple[dict, str]:
    """(fields, body) of a document; ({}, text) when it has no frontmatter."""
    block, body_text = split_frontmatter(text)
    return ({} if block is None else parse_frontmatter(block, where)), body_text


def _yaml_str(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or unicodedata.category(ch) == "Cf":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out) + '"'


def _yaml_flow(value: object) -> str:
    if isinstance(value, str):
        return _yaml_str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_flow(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_yaml_flow(v)}" for k, v in value.items()) + "}"
    raise FrontmatterError(f"cannot write {type(value).__name__} in the frontmatter subset")


def dump_frontmatter(data: dict, comment: str | None = None) -> str:
    """The frontmatter block for `data`, fences included, every string double-quoted.

    A list of mappings is written as a block sequence, one flow mapping per line; any other list as a
    flow sequence. Keys keep the order they have in `data`, and None values are left out.
    """
    lines = ["---"] + ([f"# {comment}"] if comment else [])
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            lines.append(f"{key}:")
            lines += [f"  - {_yaml_flow(v)}" for v in value]
        else:
            lines.append(f"{key}: {_yaml_flow(value)}")
    return "\n".join(lines + ["---"]) + "\n"


# The carrier file: the one file of `.agents/` that belongs to the carrier and not to the bundle. It is
# never listed in `SHA256SUMS`, a release never writes it, and it holds everything that used to be the
# "repository's own fields" of several headers. TOML, read by `tomllib`.
CARRIER_FILE = "carrier.toml"
CARRIER_KEYS = ("carrier", "adopted", "upstream", "harvested_through", "adapted", "declined")


def _toml_str(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F or unicodedata.category(ch) == "Cf":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out) + '"'


def dump_carrier(data: dict) -> str:
    """The carrier file's TOML: known keys first in their order, strings and arrays of strings only."""
    lines = ["# This repository's own fields. Carrier-owned: never listed in SHA256SUMS, never written by a release."]
    for key in [*[k for k in CARRIER_KEYS if k in data], *sorted(k for k in data if k not in CARRIER_KEYS)]:
        value = data[key]
        if isinstance(value, str):
            lines.append(f"{key} = {_toml_str(value)}")
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            lines.append(f"{key} = []" if not value else f"{key} = [\n" + "".join(f"  {_toml_str(v)},\n" for v in value) + "]")
        else:
            raise RefusedError(f"{CARRIER_FILE}: `{key}` must be a string or a list of strings")
    return "\n".join(lines) + "\n"


def read_carrier(tree: Path) -> dict | None:
    """The carrier file of a bundle, or None when there is none."""
    path = tree / CARRIER_FILE
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise RefusedError(f"{path}: not valid TOML ({error})") from error
    bad = [k for k, v in data.items() if not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(i, str) for i in v)))]
    if bad:
        raise RefusedError(f"{path}: {', '.join(bad)} must be strings or lists of strings")
    return data


def write_carrier(tree: Path, data: dict) -> None:
    (tree / CARRIER_FILE).write_text(dump_carrier(data), encoding="utf-8")


# What belongs to the carrier and not to the bundle: its carrier file, its harvest outbox, whatever is
# offered in `incoming/`, and evaluation reports. None of it is in `SHA256SUMS`, and a release neither
# writes it nor removes it.
CHECKSUMS = "SHA256SUMS"
OUTBOX = ("tracking/candidates.md", "tracking/experiments.md")
# The header row each outbox table must keep: the home reads the rows by these columns.
OUTBOX_COLUMNS = {
    "tracking/candidates.md": "| Candidate | Kind | Lacks | Evidence | First seen |",
    "tracking/experiments.md": "| Date | Note | Where | What was run | Result | Verdict |",
}
OUTBOX_TEMPLATES = {
    "tracking/candidates.md": (
        "# Candidates this repository offers\n\n"
        "What this repository's harvest learned since the last release, in the form admission needs "
        "(`../knowledge/README.md`, *What a candidate carries*). The home repository reads these rows at "
        "the next release and empties the table; nothing here is followed as guidance.\n\n"
        + "| Candidate | Kind | Lacks | Evidence | First seen |\n|---|---|---|---|---|\n"),
    "tracking/experiments.md": (
        "# Experiments run in this repository\n\n"
        "Each experiment this repository ran against a note since the last release, with its verdict. "
        "The queue of experiments waiting to be run is `../knowledge/OPEN.md`.\n\n"
        + "| Date | Note | Where | What was run | Result | Verdict |\n|---|---|---|---|---|---|\n"),
}


def outbox_problems(tree: Path) -> list[str]:
    """Every outbox file that is missing or no longer carries the columns the home reads it by."""
    problems = []
    for rel, columns in OUTBOX_COLUMNS.items():
        path = tree / rel
        if not path.is_file():
            problems.append(f"{rel}: missing; restore it from the template (`bundle.py outbox --reset`)")
        elif columns not in path.read_text(encoding="utf-8").splitlines():
            problems.append(f"{rel}: its table no longer has the columns {columns}")
    return problems


def is_carrier_owned(rel: str) -> bool:
    parts = rel.split("/")
    return (
        rel == CARRIER_FILE
        or parts[0] == "tracking"
        or (parts[0] == "incoming" and rel != "incoming/README.md")
        or (len(parts) == 1 and parts[0].startswith("evaluation-"))
    )


def shipped(tree: Path) -> list[str]:
    """Every file a release ships, relative to the bundle, in byte order: what `SHA256SUMS` lists.

    Dotfiles, caches, the carrier's own files and the checksum file itself are not shipped.
    """
    return sorted(
        (
            rel
            for p in tree.rglob("*")
            if (p.is_file() or p.is_symlink())
            and "__pycache__" not in p.parts
            and not _hidden(rel := p.relative_to(tree).as_posix())
            and not is_carrier_owned(rel)
            and rel != CHECKSUMS
        ),
        key=str.encode,
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checksums_text(tree: Path) -> str:
    """The `SHA256SUMS` content for a tree: GNU coreutils text format, `<hex>  <path>`, byte order."""
    lines = []
    for rel in shipped(tree):
        if "\\" in rel or len(rel.splitlines()) != 1 or rel != rel.strip("\n"):
            raise RefusedError(f"{rel!r}: a path GNU sha256sum would escape or split; rename it")
        lines.append(f"{sha256_file(tree / rel)}  {rel}\n")
    return "".join(lines)


def write_checksums(tree: Path) -> None:
    (tree / CHECKSUMS).write_text(checksums_text(tree), encoding="utf-8")


CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


def read_checksums(tree: Path) -> dict[str, str]:
    """{path: hex} from a tree's `SHA256SUMS`; a malformed line is refused."""
    listed: dict[str, str] = {}
    for number, line in enumerate((tree / CHECKSUMS).read_text(encoding="utf-8").splitlines(), 1):
        m = CHECKSUM_LINE.match(line)
        if not m:
            raise RefusedError(f"{CHECKSUMS}:{number}: not `<64 hex>  <path>`")
        path = m.group(2)
        if path in listed:
            raise RefusedError(f"{CHECKSUMS}:{number}: {path} is listed twice")
        if path.startswith("/") or ".." in path.split("/"):
            raise RefusedError(f"{CHECKSUMS}:{number}: {path} is outside the bundle")
        if posixpath.normpath(path) != path or _hidden(path) or "__pycache__" in path.split("/"):
            raise RefusedError(f"{CHECKSUMS}:{number}: {path} is not a shipped path in normal form")
        listed[path] = m.group(1)
    return listed


def checksum_problems(tree: Path) -> list[str]:
    """Every shipped file that is missing, changed, or not listed; `sha256sum -c` sees only the first two.

    A file that differs only by CRLF line endings gets a hint: a checkout that rewrote them changed the
    bytes, which is exactly what a checksum is for.
    """
    if not (tree / CHECKSUMS).is_file():
        return [f"{CHECKSUMS}: missing, so nothing in this bundle can be verified"]
    try:
        listed = read_checksums(tree)
    except RefusedError as error:
        return [str(error)]
    problems = []
    for rel, digest in listed.items():
        path = tree / rel
        if not path.is_file():
            problems.append(f"{rel}: listed in {CHECKSUMS} and missing")
        elif sha256_file(path) != digest:
            data = path.read_bytes()
            hint = " (CRLF line endings: check out with eol=lf)" if b"\r\n" in data and hashlib.sha256(
                data.replace(b"\r\n", b"\n")).hexdigest() == digest else ""
            problems.append(f"{rel}: changed since the release{hint}; the release writes it, not this carrier")
    problems += [f"{rel}: not in {CHECKSUMS}; a file only a release may add" for rel in shipped(tree) if rel not in listed]
    return problems


SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")


def semver_key(version: str) -> tuple:
    """A sort key with Semantic Versioning 2.0.0 precedence; build metadata is ignored.

    Raises:
        RefusedError: If the string is not a SemVer version.
    """
    m = SEMVER.match(version.removeprefix("v"))
    if not m:
        raise RefusedError(f"{version!r} is not a Semantic Versioning 2.0.0 version (MAJOR.MINOR.PATCH)")
    core = tuple(int(g) for g in m.groups()[:3])
    if m.group(4) is None:
        return (*core, 1, ())
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(4).split("."))
    return (*core, 0, ids)


CHANGELOG = "CHANGELOG.md"
CHANGELOG_SECTION = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$", re.MULTILINE)


def changelog_since(tree: Path, since: str) -> str:
    """The sections of the bundle's Keep a Changelog file for every version newer than `since`."""
    if not (tree / CHANGELOG).is_file():
        raise RefusedError(f"{tree / CHANGELOG}: no such file; is the release where you said?")
    text = (tree / CHANGELOG).read_text(encoding="utf-8")
    heads = list(CHANGELOG_SECTION.finditer(text))
    floor = semver_key(since)
    out = []
    for i, m in enumerate(heads):
        if m.group(1) == "Unreleased" or semver_key(m.group(1)) <= floor:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append(text[m.start() : end].rstrip() + "\n")
    return "\n".join(out)


def bundle_version(tree: Path) -> str | None:
    """The version a bundle declares in its README frontmatter, or None (a legacy or broken README)."""
    readme = tree / "README.md"
    if not readme.is_file():
        return None
    try:
        data, _ = read_frontmatter(readme.read_text(encoding="utf-8"), str(readme))
    except FrontmatterError:
        return None
    version = data.get("version")
    return version if isinstance(version, str) and SEMVER.match(version) else None


def is_legacy(tree: Path) -> bool:
    """A bundle from before 0.0.22: its README header names a `lineage`."""
    readme = tree / "README.md"
    return readme.is_file() and field(header(readme), "lineage") is not None


def invisible_characters(tree: Path, rels: list[str]) -> list[str]:
    """Every format character (Unicode category Cf) in the given files: zero-width, bidirectional, tags.

    They change what a reader sees, or the order in which a reviewer reads it, without changing what an
    agent is given: the published attack on instruction files (CVE-2021-42574 names the bidirectional
    form). The bundle needs none of them.
    """
    found = []
    for rel in rels:
        path = tree / rel
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.split("\n"), 1):
            for column, ch in enumerate(line, 1):
                if unicodedata.category(ch) == "Cf" or ch in INVISIBLE_OTHER:
                    found.append(f"{rel}:{number}:{column}: invisible U+{ord(ch):04X} {unicodedata.name(ch, 'format character')}")
    return found


# What may never arrive through `incoming/`: configuration a coding assistant or git would execute in
# the receiving repository, and scripts. The bundle's own tool is the one script a release carries.
INCOMING_REFUSED_NAMES = re.compile(
    r"(?i)(^|/)(settings[^/]*\.json|hooks|\.claude|\.git[^/]*|\.githooks|\.github|\.vscode|\.idea|\.cursor|\.mcp\.json|"
    r"\.envrc|\.env|makefile|gnumakefile|justfile|package\.json|\.pre-commit-config\.yaml|\.windsurfrules|"
    r"claude\.md|agents\.md|gemini\.md|\.cursorrules|copilot-instructions\.md)(/|$)")
INCOMING_SCRIPT = re.compile(r"(?i)\.(sh|bash|zsh|fish|py|pyc|pyw|pyz|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|pl|php|ps1|psm1|bat|cmd|com|command|"
                             r"exe|dll|so|dylib|jar|app|vbs|scpt|applescript)$")
# The one script a release carries, at its place in the release: `tools/bundle.py`, or under one folder
# the release was exported into.
INCOMING_TOOL = re.compile(r"^(?:[^/]+/)?tools/bundle\.py$")
# Characters that render as nothing and are not format characters (Cf): fillers and a grapheme joiner.
INVISIBLE_OTHER = frozenset("\u034f\u115f\u1160\u3164\uffa0\u2800\u180e")


def material_problems(root: Path, shown: str = "") -> list[str]:
    """Everything under `root` a triage must not even open: invisible text, links, executables, config.

    Offered material is data. A symlink can point outside it, an executable bit or a settings file is
    run by whatever reads the folder next, an instruction file is loaded by an assistant on sight, and
    a format character hides text from the reviewer. The bundle's own tool, at `tools/bundle.py` (or
    under one folder the release was exported into), is the one script a release carries.
    """
    problems = []
    for path in sorted(root.rglob("*")):
        inner = path.relative_to(root).as_posix()
        rel = shown + inner
        if path.is_symlink():
            problems.append(f"{rel}: a symbolic link; offered material is copied, never linked")
            continue
        if any(unicodedata.category(ch) == "Cf" or ch in INVISIBLE_OTHER for ch in inner):
            problems.append(f"{rel!r}: an invisible character in the name")
        if INCOMING_REFUSED_NAMES.search(inner) or "__pycache__" in inner.split("/"):
            problems.append(f"{rel}: assistant, git, editor or build configuration, or a cache, which would run or load here")
        if path.is_file():
            if os.stat(path).st_mode & 0o111:
                problems.append(f"{rel}: has an executable bit")
            if INCOMING_SCRIPT.search(inner) and not INCOMING_TOOL.match(inner):
                problems.append(f"{rel}: a script; the only one a release carries is tools/bundle.py")
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{rel}: not UTF-8 text, so no check can read it")
                continue
            for number, line in enumerate(text.split("\n"), 1):
                hits = [ch for ch in line if unicodedata.category(ch) == "Cf" or ch in INVISIBLE_OTHER]
                if hits:
                    problems.append(f"{rel}:{number}: invisible U+{ord(hits[0]):04X} {unicodedata.name(hits[0], 'format character')}")
    return problems


def incoming_problems(tree: Path) -> list[str]:
    """What `incoming/` holds besides its README, checked as offered material."""
    folder = tree / "incoming"
    if not folder.is_dir():
        return []
    return [p for p in material_problems(folder, "incoming/") if not p.startswith("incoming/README.md")]


@dataclass(frozen=True)
class Scope:
    """The repositories this session works on, and where that list came from.

    The source is carried because the report about what was *left out* depends on it. A scope the
    session declared can be compared with what the machine knows, so `outside` names the carriers
    nobody is looking at. A scope taken from the manifest **is** what the machine knows, so that
    comparison is empty however wide the operation was — and an empty list reads exactly like a
    narrow scope carefully respected. Whoever prints it has to say which of the two it is.
    """

    repos: list[Path]
    source: str  # "argument", "environment" or "manifest"

    @property
    def declared(self) -> bool:
        """Whether this session said what it works on, rather than inheriting every carrier."""
        return self.source != "manifest"

    def __iter__(self):  # noqa: ANN204 -- an iterator of Path; the annotation needs typing.Iterator
        return iter(self.repos)

    def __len__(self) -> int:
        return len(self.repos)


def workspace(args: list[str], manifest: Path | None = MANIFEST, *, writing: bool = False) -> Scope:
    """The repositories this session works on: the ones given, else `AGENT_WORKSPACE`, else the manifest.

    Every path has to carry a bundle, so a mistyped one stops the session instead of being skipped.

    **A command that writes never falls back to the manifest.** The manifest is this machine's list
    of carriers; it says which repositories exist, never which ones a session may edit. Inferring
    the second from the first hands every carrier to whoever forgot the argument, and the guard
    that would have named the ones left out is derived from the same list, so it stays silent.

    Raises:
        NotACarrierError: If a path holds no `.agents` folder.
        UndeclaredScopeError: If a writing command was given no scope at all.
    """
    if args:
        given, source = args, "argument"
    elif environment := [p for p in os.environ.get(WORKSPACE_ENV, "").split(os.pathsep) if p]:
        given, source = environment, "environment"
    elif writing:
        raise UndeclaredScopeError(
            f"no repositories given and no {WORKSPACE_ENV}: the manifest lists this machine's carriers, "
            "it does not say which ones this session may write. Name the repositories this session has open.")
    elif manifest is None or not manifest.exists():
        sys.exit(f"no repositories given, no {WORKSPACE_ENV}, and no manifest at {manifest}")
    else:
        given, source = manifest_carriers(manifest), "manifest"
    repos = [Path(p).expanduser().resolve() for p in given]
    missing = [str(r) for r in repos if not (r / ".agents").is_dir()]
    if missing:
        raise NotACarrierError(f"no bundle in {missing}")
    if not repos:
        # A manifest with `carriers = []` gave an empty scope, and every command then failed on its
        # first carrier: `repos[0]` in gather, `next(iter(...))` in align.
        raise RefusedError(f"the scope is empty (from the {source}): name the carriers this session has open")
    return Scope(repos, source)


def manifest_carriers(manifest: Path) -> list[str]:
    """The `carriers` list of the local manifest (TOML)."""
    return list(tomllib.loads(manifest.read_text(encoding="utf-8")).get("carriers", []))


def outside(scope: Scope, manifest: Path | None = MANIFEST) -> list[str]:
    """The carriers this machine knows that the scope does not hold: never written, always named.

    Empty for a scope that came from the manifest, because there the two lists are the same one.
    `Scope.declared` is what tells the caller whether an empty answer means anything.
    """
    if not scope.declared or manifest is None or not manifest.exists():
        return []
    listed = [Path(p).expanduser().resolve() for p in manifest_carriers(manifest)]
    return [str(p) for p in listed if p not in scope.repos]


def _scope_report(scope: Scope, verb: str) -> list[str]:
    """One line per carrier this session leaves alone — or one line saying why it can name none."""
    if not scope.declared:
        return [f"  . scope taken from the manifest: every carrier this machine knows is in it, so none is named as {verb}"]
    return [f"  . outside this workspace, not {verb}: {name}" for name in outside(scope)]


# Every refusal the tool raises on purpose. Caught in `main`, printed as one line, exit 2: a refusal

# --- verify ----------------------------------------------------------------------------------------


def verify_problems(tree: Path, privacy: PrivacyReport | None = None, release: bool = False) -> list[str]:
    """Everything a carrier's gate fails on: the copy is the release, it links and routes, it leaks nothing.

    Privacy warnings are advisory and printed by the caller, not failed on. A bundle from before 0.0.22
    fails with one line that says how to update it, instead of every check failing on the old layout.
    With `release`, the tree is a release as it arrives (in `incoming/`, say): it has no carrier file and
    no outbox yet, and holding either would be another repository's own files.
    """
    _a_bundle(tree)
    if is_legacy(tree):
        return [f"{tree}: a bundle from before 0.0.22 (its README header names a `lineage`); update it with "
                "`method/prompt-update.md` from a release, whose layout this tool checks"]
    if release:
        unreadable = [p for p in material_problems(tree) if "not UTF-8" in p]
        if unreadable:
            return unreadable
    problems = [] if bundle_version(tree) else ["README.md: its frontmatter has no Semantic Versioning `version`"]
    sessions = [p for p in session_problems(tree) if not (release and re.search(r": tracking/[^:]*: no such file", p))]
    problems += checksum_problems(tree) + link_problems(tree) + reachability_problems(tree) + sessions
    privacy = privacy if privacy is not None else privacy_check(tree)
    problems += [f"privacy: {f.where} {f.rule}: {f.match}" for f in privacy.failures]
    problems += invisible_characters(tree, [r for r in all_files(tree) if not r.startswith("incoming/")])
    problems += stray_problems(tree) if not release else [p for p in stray_problems(tree) if "hidden" in p]
    if release:
        foreign = [r for r in all_files(tree) if is_carrier_owned(r) and not r.startswith("incoming/")]
        return problems + [f"{r}: another repository's own file, which a release never carries" for r in foreign] \
            + material_problems(tree)
    problems += outbox_problems(tree)
    try:
        carrier = read_carrier(tree)
    except RefusedError as error:
        carrier, problems = {}, [*problems, str(error)]
    if carrier is None:
        problems.append(f"{CARRIER_FILE}: missing; this repository's own fields live there (`bundle.py carrier-id --mint`)")
    else:
        if not CARRIER_ID.match(str(carrier.get(CARRIER_FIELD, ""))):
            problems.append(f"{CARRIER_FILE}: `{CARRIER_FIELD}` is missing or not `r-` and six hex (`bundle.py carrier-id --mint`)")
        problems += [f"{CARRIER_FILE}: unknown key `{k}`" for k in carrier if k not in CARRIER_KEYS]
        problems += [f"{CARRIER_FILE}: `{k}` must be a list of strings" for k in ("adapted", "declined")
                     if k in carrier and not isinstance(carrier[k], list)]
    return problems + incoming_problems(tree)


def check_local(repo: Path) -> list[str]:
    """Every change to a carrier's bundle that the carrier itself may not make.

    `incoming/` is read as it is offered: a release copied in for an update is not a change of ours.

    Read from the checksums, not from git: a note edited and committed in a carrier is as much a fork of
    the release as one left uncommitted, and git status saw only the second. The carrier's own files
    (its carrier file, its outbox, `incoming/`, evaluation reports) are never in the checksums.
    """
    return [p for p in checksum_problems(repo / ".agents") if not p.startswith("incoming/")]


def check_local_all(repos: list[Path]) -> list[tuple[str, list[str]]]:
    """The local-step check over every repository of the workspace, keeping only the ones with problems."""
    return [(repo.name, problems) for repo in repos if (problems := check_local(repo))]


# Static budgets, in estimated tokens: what a coding session loads before its task, and the largest card
# it may read. They stand in for the cost caps (a normal session at most 1.5 times a session without the
# bundle, one that consults the knowledge at most twice), which only a measured run can check; a budget
# crossed is a release that grew what every session pays for. Set at 0.0.22 to the measured size plus a
# tenth.
BUDGETS = {"coding": 9_900, "card": 240}
CARD_ROW = re.compile(r"^\| \[[\w-]+\]\(\.\./notes/(?:active|review)/[\w-]+\.md\)")


def largest_card(tree: Path) -> tuple[str, int]:
    """(note, estimated tokens) of the largest card row in the area indexes."""
    best = ("", 0)
    for rel in _rels(tree, "knowledge/areas/*.md"):
        for line in (tree / rel).read_text(encoding="utf-8").split("\n"):
            if CARD_ROW.match(line) and len(line.split(" | ")) == 4 and _tokens(len(line)) > best[1]:
                best = (line[3 : line.index("]")], _tokens(len(line)))
    return best


def budget_problems(tree: Path, data: dict | None = None) -> list[str]:
    data = data if data is not None else report(tree)
    problems = []
    coding = data["sessions"].get("coding", {}).get("tokens_estimate", 0)
    if coding > BUDGETS["coding"]:
        problems.append(f"the coding session loads about {coding} tokens, over its budget of {BUDGETS['coding']}")
    note, card = largest_card(tree)
    if card > BUDGETS["card"]:
        problems.append(f"the card of {note} is about {card} tokens, over the card budget of {BUDGETS['card']}")
    return problems


def export(tree: Path, dest: Path) -> list[str]:
    """Copies this bundle's shipped files and `SHA256SUMS` into `dest`: a release as it travels.

    Never the carrier's own files (its carrier file, its outbox, `incoming/` contents, evaluation reports),
    which another repository would otherwise take as its own. Refused unless the copy verifies first.
    """
    problems = checksum_problems(tree)
    if problems:
        raise RefusedError(f"{tree}: does not match its SHA256SUMS ({problems[0]}); export only a verified release")
    if dest.exists() and any(dest.iterdir()):
        raise RefusedError(f"{dest}: not empty; export into a new folder")
    rels = [*shipped(tree), CHECKSUMS]
    for rel in rels:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tree / rel, dest / rel)
    return rels


def reset_outbox(tree: Path) -> list[str]:
    """Writes the empty outbox templates; returns the files that held rows and were emptied."""
    changed = []
    for rel, text in OUTBOX_TEMPLATES.items():
        path = tree / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed.append(rel)
    return changed


# Every refusal the tool raises on purpose. Caught in `main`, printed as one line, exit 2: a refusal
# is an answer, not a crash.
REFUSALS = (NotACarrierError, OutsideWorkspaceError, DirtyTreeError, UndeclaredScopeError, RefusedError, FrontmatterError)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bundle.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, help_text in (("verify", "everything a carrier's gate fails on (exit 1)"),
                            ("digest", "deprecated alias of verify, kept through 0.0.x for carriers' audits")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("tree", nargs="?", default=str(OWN_BUNDLE))
        p.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
        p.add_argument("--release", action="store_true", help="the tree is a release as it arrives: no carrier file, no outbox")
    p = sub.add_parser("check-local", help="the carrier changed only what it owns (exit 1)")
    p.add_argument("repos", nargs="*")
    p = sub.add_parser("carrier-id", help="a repository's stored random id; --mint writes one where there is none")
    p.add_argument("repo", nargs="?", default=str(OWN_REPO))
    p.add_argument("--mint", action="store_true", help=f"write a new random id into .agents/{CARRIER_FILE}; refused if one is stored")
    p = sub.add_parser("id", help="a record id: decision, roadmap item or session entry (d|i|s TEXT...)")
    p.add_argument("kind", choices=list(RECORD_KINDS))
    p.add_argument("parts", nargs="+", metavar="TEXT")
    p.add_argument("--repo", help="the carrier the record belongs to, by its stored id (default: this tool's repository)")
    p = sub.add_parser("ids", help="record ids in files: malformed or defined twice (exit 1), defined under another carrier's id (warned)")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--carrier", metavar="REPO", help="the carrier whose prefix a definition should carry (default: this tool's repository, if it stores an id)")
    p = sub.add_parser("privacy", help="nothing that identifies a private repository, its people or its infrastructure (exit 1 on a FAIL)")
    p.add_argument("tree", nargs="?", default=str(OWN_BUNDLE), help="the bundle whose files are read")
    p.add_argument("--paths", nargs="+", metavar="FILE", help="read these files instead of the tree, anywhere (a repository's README, a staged file)")
    p.add_argument("--terms", metavar="FILE", help="the private terms file (default: $XDG_CONFIG_HOME or ~/.config, agent-guides/private-terms.txt)")
    p = sub.add_parser("report", help="files, bytes and estimated tokens per folder and per session type")
    p.add_argument("tree", nargs="?", default=str(OWN_BUNDLE))
    p.add_argument("--json", action="store_true", help="the report as JSON instead of markdown tables")
    p.add_argument("--check", action="store_true", help="also fail (exit 1) when a static budget is crossed")
    p = sub.add_parser("changelog", help="the CHANGELOG sections newer than a version")
    p.add_argument("--since", required=True, metavar="X.Y.Z")
    p.add_argument("tree", nargs="?", default=str(OWN_BUNDLE))
    p = sub.add_parser("export", help="this bundle's shipped files and SHA256SUMS into a new folder: a release as it travels")
    p.add_argument("dest")
    p.add_argument("--tree", default=str(OWN_BUNDLE))
    p = sub.add_parser("outbox", help="the harvest outbox")
    p.add_argument("--reset", action="store_true", required=True, help="write the empty templates back")
    p.add_argument("tree", nargs="?", default=str(OWN_BUNDLE))
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        sys.exit(f"bundle.py needs Python 3.11 or newer (this is {sys.version.split()[0]}); run it with python3.11+")
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except REFUSALS as refusal:
        print(f"  x {refusal}")
        return 2


def _run(args: argparse.Namespace) -> int:  # noqa: C901, PLR0911, PLR0912 -- one branch per command
    if args.command in ("verify", "digest"):
        if args.command == "digest":
            print("  . `digest` is deprecated: the bundle is verified by SHA256SUMS now; running `verify`")
        tree = Path(args.tree)
        privacy = privacy_check(tree) if not is_legacy(_a_bundle(tree)) else None
        problems = verify_problems(tree, privacy, release=args.release)
        for note in privacy.notes() if privacy else []:
            print(note)
        for problem in problems:
            print("  x " + problem)
        print(f"verify {tree}: " + (f"{bundle_version(tree)} verified" if not problems else f"{len(problems)} problems"))
        return 1 if problems else 0
    if args.command == "check-local":
        declared = args.repos or os.environ.get(WORKSPACE_ENV) or MANIFEST.exists()
        repos = workspace(args.repos).repos if declared else [OWN_REPO]
        found = check_local_all(repos)
        for name, problems in found:
            for problem in problems:
                print(f"  x {name}: {problem}")
        print(f"local step over {len(repos)} repositories: " + ("only their own files changed" if not found
                                                                 else f"{sum(len(p) for _, p in found)} files they may not write"))
        return 1 if found else 0
    if args.command == "carrier-id":
        if args.mint:
            print(f"minted {mint_carrier_id(Path(args.repo))} in {Path(args.repo) / '.agents' / CARRIER_FILE}; commit it")
        else:
            print(repo_carrier_id(Path(args.repo)))
        return 0
    if args.command == "id":
        print(record_id(args.kind, " ".join(args.parts), repo_carrier_id(Path(args.repo) if args.repo else OWN_REPO)))
        return 0
    if args.command == "ids":
        if args.carrier:
            carrier = repo_carrier_id(Path(args.carrier))
        else:
            carrier = stored_carrier_id(OWN_REPO) if (OWN_BUNDLE / CARRIER_FILE).is_file() else None
            if carrier is None:
                print("  . no carrier id stored here: prefixes not checked (give --carrier REPO)")
        errors, warnings, counts = record_id_check([Path(f) for f in args.files], carrier)
        for line in [f"  x {e}" for e in errors] + [f"  ! {w}" for w in warnings]:
            print(line)
        print(f"{counts['ids']} record ids in {len(args.files)} files: {counts['definitions']} defined, {counts['citations']} cited, "
              f"{counts['legacy']} of the first scheme; {len(errors)} errors, {len(warnings)} warnings")
        return 1 if errors else 0
    if args.command == "privacy":
        result = privacy_check(Path(args.tree), [Path(f) for f in args.paths] if args.paths else None,
                               Path(args.terms) if args.terms else None)
        for f in result.failures:
            print(f"  x FAIL {f.where} {f.rule}: {f.match}")
        for note in result.notes():
            print(note)
        print(result.summary())
        return 1 if result.failures else 0
    if args.command == "report":
        tree = Path(args.tree)
        data = report(tree)
        print(json.dumps(data, indent=2) if args.json else report_markdown(data), end="\n" if args.json else "")
        if args.check:
            problems = budget_problems(tree, data)
            for problem in problems:
                print("  x " + problem)
            return 1 if problems else 0
        return 0
    if args.command == "changelog":
        print(changelog_since(Path(args.tree), args.since), end="")
        return 0
    if args.command == "export":
        print(f"exported {len(export(Path(args.tree), Path(args.dest)))} files to {args.dest}")
        return 0
    if args.command == "outbox":
        changed = reset_outbox(Path(args.tree))
        print(f"outbox reset: {', '.join(changed) if changed else 'already empty'}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
