#!/usr/bin/env python3
"""Fail when the dependency locks, their inputs and the installed venv disagree.

requirements.txt / requirements-runtime.txt are the hand-edited inputs (ranges,
the policy). requirements.lock / requirements-runtime.lock are compiled from
them by uv and pin every package, transitive ones included. The Docker image
installs the runtime lock, the dev venv is synced from the dev lock, and the
runtime lock is compiled WITH the dev lock as a constraint. This gate checks
that all of that still holds:

  - every runtime pin exists in the dev lock at the same version, so a green
    test suite is evidence about the versions production runs;
  - every input requirement has a pin in its lock that still satisfies it, so
    nobody edits an input and forgets to re-lock;
  - the venv this runs in holds exactly the dev lock's pins for this platform.

Why it exists: on 2026-10-08 a rebuild re-resolved the image's ranges to
fastapi 0.143.0 while the tests ran on 0.136.1, and the new version's built-in
tracing took over every trace root. Nothing in the gate could see it.

Run with `uv run python scripts/check_locks.py` (scripts/check.sh does).
"""
from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)\s*(?:;\s*(.+?))?\s*$")
_SYNC = "run `uv pip sync requirements.lock`"


def parse_lock(text: str) -> dict[str, tuple[str, str]]:
    """{normalised name: (version, marker or "")} from a uv-compiled lock."""
    pins: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        m = _PIN_RE.match(line)
        if m:
            pins[canonicalize_name(m.group(1))] = (m.group(2), m.group(3) or "")
    return pins


def _parse_inputs(text: str) -> list[Requirement]:
    reqs = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            reqs.append(Requirement(line))
    return reqs


def _input_problems(inputs: str, inputs_name: str, lock: dict, lock_name: str) -> list[str]:
    problems = []
    for req in _parse_inputs(inputs):
        name = canonicalize_name(req.name)
        if name not in lock:
            problems.append(f"{inputs_name} declares {req.name} but {lock_name} has no pin for it — re-lock")
        elif not req.specifier.contains(lock[name][0], prereleases=True):
            problems.append(
                f"{inputs_name} wants {req.name}{req.specifier} but {lock_name} pins "
                f"{req.name}=={lock[name][0]} — re-lock"
            )
    return problems


def lock_problems(
    dev_lock: str,
    runtime_lock: str,
    dev_inputs: str,
    runtime_inputs: str,
    installed: dict[str, str],
    environment: dict | None = None,
) -> list[str]:
    dev, runtime = parse_lock(dev_lock), parse_lock(runtime_lock)
    problems: list[str] = []

    for name, (version, _) in runtime.items():
        if name not in dev:
            problems.append(
                f"requirements-runtime.lock pins {name}=={version}, which requirements.lock lacks "
                "— the tests never ran with it"
            )
        elif Version(dev[name][0]) != Version(version):
            problems.append(
                f"requirements-runtime.lock pins {name}=={version} but requirements.lock pins "
                f"{name}=={dev[name][0]} — production would run a version the tests did not"
            )

    problems += _input_problems(dev_inputs, "requirements.txt", dev, "requirements.lock")
    problems += _input_problems(runtime_inputs, "requirements-runtime.txt", runtime, "requirements-runtime.lock")

    have = {canonicalize_name(n): v for n, v in installed.items()}
    for name, (version, marker) in dev.items():
        if marker and not Marker(marker).evaluate(environment):
            continue
        if name not in have:
            problems.append(f"requirements.lock pins {name}=={version} but it is not installed — {_SYNC}")
        elif Version(have[name]) != Version(version):
            problems.append(
                f"requirements.lock pins {name}=={version} but {have[name]} is installed — {_SYNC}"
            )
    for name in sorted(have.keys() - dev.keys()):
        problems.append(
            f"{name}=={have[name]} is installed but not in requirements.lock — declare it in "
            f"requirements.txt and re-lock, or {_SYNC} to remove it"
        )
    return problems


def main() -> int:
    installed = {d.metadata["Name"]: d.version for d in metadata.distributions()}
    problems = lock_problems(
        dev_lock=(ROOT / "requirements.lock").read_text(),
        runtime_lock=(ROOT / "requirements-runtime.lock").read_text(),
        dev_inputs=(ROOT / "requirements.txt").read_text(),
        runtime_inputs=(ROOT / "requirements-runtime.txt").read_text(),
        installed=installed,
    )
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    runtime_pins = len(parse_lock((ROOT / "requirements-runtime.lock").read_text()))
    if problems:
        print(f"==> dependency locks: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"==> dependency locks: venv == requirements.lock; {runtime_pins} runtime pins all match it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
