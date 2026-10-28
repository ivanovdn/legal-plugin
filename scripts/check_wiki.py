#!/usr/bin/env python
"""Assert the wiki's reference sections list what the code has.

The 2026-10-28 audit found four live routes, seven state fields and the app's
own database service missing from docs/wiki.md: the rule to update the wiki
named only the Shipped Log, so these sections were never touched. Four lists
can be checked mechanically, and are:

  - API routes        both ways: every route is in the API Endpoints table (not
                      as "Not built"); every row not marked "Not built" has a route
  - state fields      both ways: graph/state.py's LegalAgentState vs the wiki block
  - compose services  both ways for docker-compose.yml vs the Docker Services
                      table; the VM overlay's services must be named in that section
  - store tables      one way: every table memory/db.py creates is named in the
                      Docker Services section (the app-db row)

Prose can't be checked like this, and isn't.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from collections.abc import Iterable

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}
_NOT_BUILT = "not built"


def code_routes(sources: Iterable[str]) -> set[tuple[str, str]]:
    """(METHOD, path) for every @router.<method>(path) in the given modules,
    joined to the prefix of that module's APIRouter."""
    routes: set[tuple[str, str]] = set()
    for source in sources:
        tree = ast.parse(source)
        prefix = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter":
                for kw in node.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in _HTTP_METHODS
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    routes.add((dec.func.attr.upper(), prefix + dec.args[0].value))
    return routes


def state_fields(source: str) -> set[str]:
    """The keys LegalAgentState declares."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == "LegalAgentState":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    return set()


def compose_services(text: str) -> set[str]:
    return set((yaml.safe_load(text) or {}).get("services") or {})


def store_tables(source: str) -> set[str]:
    # Anchored to the start of a line, so the phrase inside a comment is not a table.
    return set(re.findall(r"^\s*CREATE TABLE IF NOT EXISTS (\w+)", source, re.M))


def _section(wiki: str, heading: str) -> str | None:
    """The text under `heading` up to the next heading of the same level."""
    level = heading.split(" ", 1)[0]
    lines = wiki.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith(heading)]
    if not starts:
        return None
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith(level + " "):
            break
        body.append(line)
    return "\n".join(body)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))]


def wiki_problems(
    wiki: str,
    *,
    routes: set[tuple[str, str]],
    fields: set[str],
    base_services: set[str],
    overlay_services: set[str],
    tables: set[str],
) -> list[str]:
    problems: list[str] = []

    api = _section(wiki, "## API Endpoints")
    if api is None:
        problems.append("the wiki has no '## API Endpoints' section")
    else:
        listed: set[tuple[str, str]] = set()
        for row in api.split("\n"):
            if not row.startswith("|") or row.startswith("|-"):
                continue
            cells = _cells(row)
            path = re.fullmatch(r"`([^`]+)`", cells[1]) if len(cells) > 2 else None
            if path is None:
                continue
            built = cells[2].strip("*").lower() != _NOT_BUILT
            for method in cells[0].upper().split("/"):
                key = (method.strip(), path.group(1))
                if built:
                    listed.add(key)
                    if key not in routes:
                        problems.append(f"the wiki lists {key[0]} {key[1]} as built, but no route serves it")
        for method, path in sorted(routes - listed, key=lambda r: (r[1], r[0])):
            problems.append(f"route {method} {path} is not in the wiki's API Endpoints table")

    state = _section(wiki, "### LegalAgentState")
    if state is None:
        problems.append("the wiki has no '### LegalAgentState' section")
    else:
        block = re.search(r"```python\n(.*?)```", state, re.S)
        names = set()
        for line in (block.group(1) if block else "").split("\n"):
            names.update(re.findall(r"\b([a-z_][a-z0-9_]*)\b", line.split("#", 1)[0]))
        for name in sorted(fields - names):
            problems.append(f"state field {name} is not in the wiki's LegalAgentState block")
        for name in sorted(names - fields):
            problems.append(f"the wiki's LegalAgentState block lists {name}, which graph/state.py does not have")

    docker = _section(wiki, "## Docker Services")
    if docker is None:
        problems.append("the wiki has no '## Docker Services' section")
    else:
        rows = set(re.findall(r"^\| \*\*([\w-]+)\*\* \|", docker, re.M))
        for name in sorted(base_services - rows):
            problems.append(f"compose service {name} is not in the wiki's Docker Services table")
        for name in sorted(rows - base_services):
            problems.append(f"the wiki's Docker Services table lists {name}, which docker-compose.yml does not define")
        named = set(re.findall(r"`([\w-]+)`", docker)) | rows
        for name in sorted(overlay_services - base_services - named):
            problems.append(f"VM overlay service {name} is not named in the wiki's Docker Services section")
        for name in sorted(tables - named):
            problems.append(
                f"store table {name} is not named in the wiki's Docker Services section (the app-db row)"
            )

    return problems


def main() -> int:
    problems = wiki_problems(
        (REPO / "docs/wiki.md").read_text(),
        routes=code_routes(p.read_text() for p in sorted((REPO / "api/routes").glob("*.py"))),
        fields=state_fields((REPO / "graph/state.py").read_text()),
        base_services=compose_services((REPO / "docker-compose.yml").read_text()),
        overlay_services=compose_services((REPO / "docker-compose.remote.yml").read_text()),
        tables=store_tables((REPO / "memory/db.py").read_text()),
    )
    if problems:
        print("FAIL: docs/wiki.md is out of step with the code:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print("      (update the wiki section in the same change — CLAUDE.md, *When making changes*)", file=sys.stderr)
        return 1
    print("==> wiki reference sections match the code (routes, state fields, services, tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
