#!/usr/bin/env python
"""Assert the declared degradation vocabulary and the one actually used agree.

Checked BOTH ways, like evals/baseline.json:
  - declared but never used  -> a site someone forgot to wire
  - used but not declared    -> a typo silently creating a new category

Also rejects string literals at call sites: a literal cannot be checked either
way, so it is a hole in both directions at once.

A unit test structurally cannot do this. test_record_degradation_adds_event
passes with 21 of 22 codes unwired.
"""
from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import observability.degradations as D  # noqa: E402

PACKAGES = ("api", "graph", "skills", "memory")
SEAM_CALLS = {"mark_failed", "record_degradation"}


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _reason_arg(node: ast.Call) -> ast.expr | None:
    """The AST node for the `reason` argument, positional OR keyword.

    `reason` is positional-or-keyword in both seam functions (no `/` marker),
    so a call site can legally write either `record_degradation(X, ...)` or
    `record_degradation(reason=X, ...)`. Checking only node.args[0] makes the
    keyword form invisible to every check at once: not counted as used, not
    flagged as a literal, not flagged as undeclared — the exact hole this
    checker exists to close, in the checker itself. Only one form can legally
    appear on a given call (Python raises "multiple values for argument"
    otherwise), so positional-if-present-else-keyword is an unambiguous
    precedence, not a guess.
    """
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg == "reason":
            return kw.value
    return None


def main() -> int:
    used: set[str] = set()
    undeclared: list[str] = []
    literals: list[str] = []

    for package in PACKAGES:
        for path in (REPO / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _called_name(node) not in SEAM_CALLS:
                    continue
                arg = _reason_arg(node)
                if arg is None:
                    continue
                where = f"{path.relative_to(REPO)}:{arg.lineno}"
                if isinstance(arg, ast.Constant):
                    literals.append(f"{where}: string literal {arg.value!r}")
                    continue
                name = arg.attr if isinstance(arg, ast.Attribute) else getattr(arg, "id", None)
                if name is None:
                    literals.append(f"{where}: reason is not a named constant")
                    continue
                value = getattr(D, name, None)
                if value is None:
                    undeclared.append(f"{where}: {name} is not declared in observability/degradations.py")
                else:
                    used.add(value)

    never_used = sorted(D.ALL_REASONS - used)
    ok = True
    if literals:
        ok = False
        print("FAIL: reason codes must be constants, not literals:", file=sys.stderr)
        for line in literals:
            print(f"  {line}", file=sys.stderr)
    if undeclared:
        ok = False
        print("FAIL: reason codes used but not declared (typo?):", file=sys.stderr)
        for line in undeclared:
            print(f"  {line}", file=sys.stderr)
    if never_used:
        ok = False
        print("FAIL: reason codes declared but wired at no call site:", file=sys.stderr)
        for code in never_used:
            print(f"  {code}", file=sys.stderr)
        print("      (a site someone forgot to wire — or a code that should be removed)", file=sys.stderr)
    if ok:
        print(f"==> degradation vocabulary: {len(used)}/{len(D.ALL_REASONS)} codes wired")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
