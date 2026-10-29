#!/usr/bin/env python
"""Check that docs/ is one linked graph whose links all resolve.

docs/ is read in GitHub, in Obsidian and in the IDE, so it links with standard
relative markdown links. Every markdown file under docs/ (tracked, or new and
not git-ignored) is scanned for four kinds of problem:

  missing file   a relative link to a path that is not in the repo: absent,
                 outside the repo, git-ignored (it exists on this machine and
                 nowhere else), or a different case (macOS finds `Wiki.md` for
                 `wiki.md`; GitHub does not)
  bad anchor     a `#fragment` on a link to a markdown file that matches none
                 of its headings, slugged the way GitHub slugs them
  orphan         a doc that no other doc links to
  wikilink       `[[...]]`, which only Obsidian reads; use [text](path.md)

Code is text, not links: fenced blocks and inline code spans are skipped, for
links, headings and wikilinks alike. Anchors are checked only on links to
markdown files (`word.ts#L18` is a line, not a heading), and only ATX headings
(`## Title`) are read.

Exits 1 on any problem, so scripts/check.sh can run it.
"""
from __future__ import annotations

import html
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import unquote

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCS = "docs"

MISSING_FILE = "missing file"
BAD_ANCHOR = "bad anchor"
ORPHAN = "orphan"
WIKILINK = "wikilink"
KINDS = (MISSING_FILE, BAD_ANCHOR, ORPHAN, WIKILINK)

_ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_LINK_OPEN = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(")
_WIKILINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_INLINE_LINK = re.compile(r"!?\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_UNDERSCORE_EMPHASIS = re.compile(r"(?<![\\\w])(__?)(?=\S)(.+?)(?<=\S)\1(?!\w)")


@dataclass(frozen=True)
class Problem:
    kind: str
    path: str  # repo-relative
    line: int  # 0 for a problem with the whole file (an orphan)
    detail: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.detail}"


@dataclass
class Report:
    files: list[str]
    links: int = 0
    problems: list[Problem] = field(default_factory=list)


def github_slug(text: str) -> str:
    """GitHub's id for a heading with this text: lowercased, everything but
    letters, marks, numbers, `_`, `-` and spaces dropped, then each space made
    a hyphen. Spaces are not collapsed, so "A — B" is "a--b"."""
    kept = (
        c for c in text.strip().lower()
        if c in "-_ " or unicodedata.category(c)[0] in "LMN" or unicodedata.category(c) == "Pc"
    )
    return "".join(kept).replace(" ", "-")


def _segments(line: str) -> list[tuple[bool, str]]:
    """Split a line into (is_code, text) runs. A code run holds the span's
    content; a backslash-escaped backtick does not open one."""
    out: list[tuple[bool, str]] = []
    text: list[str] = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\" and i + 1 < n and line[i + 1] in _ASCII_PUNCT:
            text.append(line[i:i + 2])
            i += 2
            continue
        if c == "`":
            j = i
            while j < n and line[j] == "`":
                j += 1
            close = _closing_run(line, j, j - i)
            if close is None:
                text.append(line[i:j])
                i = j
                continue
            if text:
                out.append((False, "".join(text)))
                text = []
            content = line[j:close]
            if len(content) > 2 and content[0] == content[-1] == " " and content.strip():
                content = content[1:-1]
            out.append((True, content))
            i = close + (j - i)
            continue
        text.append(c)
        i += 1
    if text:
        out.append((False, "".join(text)))
    return out


def _closing_run(line: str, start: int, length: int) -> int | None:
    """Index of the next run of exactly `length` backticks at or after `start`."""
    i = start
    while (i := line.find("`", i)) != -1:
        j = i
        while j < len(line) and line[j] == "`":
            j += 1
        if j - i == length:
            return i
        i = j
    return None


def _prose(line: str) -> str:
    """The line with code spans and backslash escapes blanked: what is left is
    where links and wikilinks can be."""
    return "".join(
        " " * (len(seg) + 2) if is_code else _ESCAPE.sub("  ", seg)
        for is_code, seg in _segments(line)
    )


def _heading_text(raw: str) -> str:
    """The text GitHub renders for a heading: code content kept, links reduced
    to their text, tags and emphasis markers dropped, escapes resolved."""
    parts = []
    for is_code, seg in _segments(raw):
        if not is_code:
            seg = _INLINE_LINK.sub(r"\1", seg)
            seg = _HTML_TAG.sub("", seg)
            seg = _UNDERSCORE_EMPHASIS.sub(r"\2", seg)
            seg = html.unescape(_ESCAPE.sub(r"\1", seg))
        parts.append(seg)
    return "".join(parts)


def _outside_fences(text: str):
    """(line number, line) for every line outside a fenced code block."""
    fence: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not line[m.end():].strip():
                fence = None
            continue
        if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
            fence = (m.group(1)[0], len(m.group(1)))
            continue
        yield number, line


def heading_slugs(text: str) -> set[str]:
    """Every heading anchor GitHub generates for this markdown, repeats
    numbered -1, -2, … in document order."""
    seen: dict[str, int] = defaultdict(int)
    slugs = set()
    for _, line in _outside_fences(text):
        m = _ATX.match(line)
        if not m:
            continue
        base = github_slug(_heading_text(_CLOSING_HASHES.sub("", m.group(1) or "")))
        count = seen[base]
        seen[base] += 1
        slugs.add(f"{base}-{count}" if count else base)
    return slugs


def _destination(line: str, i: int) -> str | None:
    """The link destination starting at line[i] (just past "]("), or None if
    what follows does not close as a link."""
    n = len(line)
    while i < n and line[i] in " \t":
        i += 1
    if i < n and line[i] == "<":
        end = line.find(">", i + 1)
        if end == -1:
            return None
        dest, i = line[i + 1:end], end + 1
    else:
        start, depth = i, 0
        while i < n and not line[i].isspace():
            if line[i] == "(":
                depth += 1
            elif line[i] == ")":
                if depth == 0:
                    break
                depth -= 1
            i += 1
        dest = line[start:i]
    while i < n and line[i] in " \t":
        i += 1
    if i < n and line[i] in "\"'(":
        end = line.find(")" if line[i] == "(" else line[i], i + 1)
        if end == -1:
            return None
        i = end + 1
        while i < n and line[i] in " \t":
            i += 1
    return dest if i < n and line[i] == ")" else None


def _links(prose: str) -> list[str]:
    targets = []
    for m in _LINK_OPEN.finditer(prose):
        dest = _destination(prose, m.end())
        if dest is not None:
            targets.append(dest)
    return targets


def _exists_exact(repo: pathlib.Path, rel: str, listings: dict) -> bool:
    """Whether rel exists under repo with exactly this case, on any filesystem."""
    current = repo
    for part in pathlib.PurePosixPath(rel).parts:
        if current not in listings:
            try:
                listings[current] = set(os.listdir(current))
            except OSError:
                listings[current] = set()
        if part not in listings[current]:
            return False
        current = current / part
    return True


def _git(repo: pathlib.Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess | None:
    """Run git in repo; None when repo is not a git work tree."""
    result = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True)
    return None if result.returncode > 1 else result


def _doc_files(repo: pathlib.Path, docs: str) -> list[str]:
    listed = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", docs)
    if listed is not None and listed.returncode == 0:
        names = {p for p in listed.stdout.split("\0") if p.endswith(".md")}
    else:
        names = {p.relative_to(repo).as_posix() for p in (repo / docs).rglob("*.md")}
    return sorted(p for p in names if (repo / p).is_file())


def _git_ignored(repo: pathlib.Path, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    result = _git(repo, "check-ignore", "-z", "--stdin", stdin="\0".join(paths) + "\0")
    return set() if result is None else {p for p in result.stdout.split("\0") if p}


def check_docs(repo: pathlib.Path = REPO, docs: str = DOCS) -> Report:
    report = Report(files=_doc_files(repo, docs))
    inbound: dict[str, set[str]] = {f: set() for f in report.files}
    slugs: dict[str, set[str]] = {}
    listings: dict = {}
    resolved_links: list[tuple[str, int, str, str]] = []

    for source in report.files:
        for number, line in _outside_fences((repo / source).read_text(encoding="utf-8")):
            prose = _prose(line)
            for m in _WIKILINK.finditer(prose):
                report.problems.append(Problem(
                    WIKILINK, source, number, f"[[{m.group(1)}]]: a wikilink; use [text](path.md)"))
            for target in _links(prose):
                if _SCHEME.match(target) or target.startswith("//"):
                    continue
                report.links += 1
                path, _, anchor = target.partition("#")
                path, anchor = unquote(path.split("?", 1)[0]), unquote(anchor)
                if not path:
                    resolved = source
                elif path.startswith("/"):
                    resolved = posixpath.normpath(path.lstrip("/"))
                else:
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
                if resolved == ".." or resolved.startswith("../"):
                    report.problems.append(Problem(MISSING_FILE, source, number, f"{target}: outside the repo"))
                    continue
                if not _exists_exact(repo, resolved, listings):
                    reason = "differs in case from the file on disk" if (repo / resolved).exists() else "no such file"
                    report.problems.append(Problem(MISSING_FILE, source, number, f"{target}: {reason}"))
                    continue
                resolved_links.append((source, number, target, resolved))
                if resolved != source and resolved in inbound:
                    inbound[resolved].add(source)
                if anchor and resolved.lower().endswith((".md", ".markdown")) and (repo / resolved).is_file():
                    if resolved not in slugs:
                        slugs[resolved] = heading_slugs((repo / resolved).read_text(encoding="utf-8"))
                    if anchor not in slugs[resolved]:
                        report.problems.append(Problem(
                            BAD_ANCHOR, source, number, f"{target}: no heading in {resolved} has the anchor #{anchor}"))

    ignored = _git_ignored(repo, sorted({resolved for *_, resolved in resolved_links}))
    for source, number, target, resolved in resolved_links:
        if resolved in ignored:
            report.problems.append(Problem(
                MISSING_FILE, source, number, f"{target}: git-ignored, so it exists only on this machine"))

    for doc in report.files:
        if not inbound[doc]:
            report.problems.append(Problem(ORPHAN, doc, 0, "no other doc links here"))

    report.problems.sort(key=lambda p: (KINDS.index(p.kind), p.path, p.line))
    return report


def main(repo: pathlib.Path = REPO) -> int:
    report = check_docs(repo)
    print(f"==> docs links: {len(report.files)} docs under {DOCS}/, {report.links} relative links")
    for kind in KINDS:
        found = [p for p in report.problems if p.kind == kind]
        print(f"    {kind + ':':<14}{len(found)}")
        for problem in found:
            print(f"      {problem}")
    if report.problems:
        print(f"FAIL: {len(report.problems)} problem(s) in the docs' links (listed above)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
