#!/usr/bin/env python3
"""Fail when the dependency locks, their inputs, the venv and the image disagree.

requirements.txt / requirements-runtime.txt are the hand-edited inputs (ranges,
the policy). requirements.lock / requirements-runtime.lock are compiled from
them by uv and pin every package, transitive ones included. The Docker image
installs the runtime lock, the dev venv is synced from the dev lock, and the
runtime lock is compiled WITH the dev lock as a constraint. This gate checks
that all of that still holds:

  - the venv's interpreter is the image's major.minor (Dockerfile
    `FROM python:X.Y.Z`); a patch difference is printed as a note;
  - every runtime pin is also a dev pin at the same version;
  - for every package the image installs, the version the image selects is
    the version the venv selects — so a green suite is evidence about
    production;
  - every input requirement is pinned, by a version that still satisfies it;
  - the venv holds exactly the dev lock's pins that apply to it, and nothing
    else;
  - each lock is current: re-running the command in its own header reproduces
    it (catches an added extra or a removed input, which pin checks miss).

Why it exists: on 2026-10-08 a rebuild re-resolved the image's ranges to
fastapi 0.143.0 while the tests ran on 0.136.1, and the new version's built-in
tracing took over every trace root. Nothing in the gate could see it.

Run with `uv run python scripts/check_locks.py` (scripts/check.sh does).
"""
from __future__ import annotations

import platform
import re
import shlex
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)\s*(?:;\s*(.+?))?\s*$")
_IMAGE_PYTHON_RE = re.compile(r"^FROM python:(\d+\.\d+\.\d+)", re.MULTILINE)
_SYNC = "run `uv pip sync requirements.lock`"

Entries = dict[str, list[tuple[str, str]]]


class LockError(ValueError):
    """A lock line this checker cannot read — never skipped silently."""


def parse_lock(text: str) -> Entries:
    """{normalised name: [(version, marker or ""), ...]}.

    A list, because a universal lock can pin one package twice under disjoint
    markers (`--python-version 3.12` is a lower bound: numpy 1.x for < 3.13,
    2.x for >= 3.13). Keyed by name alone, the second pin silently replaced the
    first.
    """
    pins: Entries = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _PIN_RE.match(line)
        if not m:
            raise LockError(f"line {number}: cannot read {stripped!r}")
        pins.setdefault(canonicalize_name(m.group(1)), []).append((m.group(2), m.group(3) or ""))
    return pins


def image_environment(python_full_version: str) -> dict[str, str]:
    """Marker environment of the production image: CPython on linux/x86_64."""
    return {
        "sys_platform": "linux", "platform_system": "Linux", "platform_machine": "x86_64",
        "os_name": "posix", "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "python_version": ".".join(python_full_version.split(".")[:2]),
        "python_full_version": python_full_version,
        "implementation_version": python_full_version,
    }


def _select(entries: list[tuple[str, str]], environment: dict | None) -> list[str]:
    """Distinct versions whose marker applies in `environment` (None = this interpreter)."""
    return sorted({v for v, marker in entries if not marker or Marker(marker).evaluate(environment)})


def _parse_inputs(text: str, inputs_name: str) -> tuple[list[Requirement], list[str]]:
    reqs, problems = [], []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            problems.append(f"{inputs_name}: unsupported line {line!r} — keep inputs to plain requirements")
            continue
        try:
            reqs.append(Requirement(line))
        except InvalidRequirement as e:
            problems.append(f"{inputs_name}: cannot read {line!r} ({e})")
    return reqs, problems


def _input_problems(inputs: str, inputs_name: str, lock: Entries, lock_name: str) -> list[str]:
    reqs, problems = _parse_inputs(inputs, inputs_name)
    for req in reqs:
        entries = lock.get(canonicalize_name(req.name))
        if not entries:
            problems.append(f"{inputs_name} declares {req.name} but {lock_name} has no pin for it — re-lock")
            continue
        for version, _ in entries:
            if not req.specifier.contains(version, prereleases=True):
                problems.append(
                    f"{inputs_name} wants {req.name}{req.specifier} but {lock_name} pins "
                    f"{req.name}=={version} — re-lock"
                )
                break
    return problems


def lock_problems(
    dev_lock: str,
    runtime_lock: str,
    dev_inputs: str,
    runtime_inputs: str,
    installed: dict[str, str],
    environment: dict | None = None,
    image_environment: dict | None = None,
) -> list[str]:
    """Every disagreement between the locks, their inputs and the venv.

    `environment` is the venv's marker environment (None = this interpreter);
    `image_environment` the production image's (None = skip that comparison).
    """
    try:
        dev = parse_lock(dev_lock)
    except LockError as e:
        return [f"requirements.lock {e}"]
    try:
        runtime = parse_lock(runtime_lock)
    except LockError as e:
        return [f"requirements-runtime.lock {e}"]
    problems: list[str] = []
    disagree: set[str] = set()   # reported once here, not again as a venv/image split

    for name, entries in runtime.items():
        if name not in dev:
            disagree.add(name)
            problems.append(
                f"requirements-runtime.lock pins {name}=={entries[0][0]}, which requirements.lock lacks "
                "— the tests never ran with it"
            )
            continue
        for version, marker in entries:
            # Same version, not same marker: the dev set's other dependents can
            # widen a marker (colorama: Windows-only for the runtime set,
            # unconditional for the dev set). What each environment actually
            # installs is compared below, per environment.
            if not any(Version(v) == Version(version) for v, _ in dev[name]):
                disagree.add(name)
                where = f" ; {marker}" if marker else ""
                problems.append(
                    f"requirements-runtime.lock pins {name}=={version}{where} but requirements.lock "
                    f"pins {', '.join(v for v, _ in dev[name])} — production would run what the tests did not"
                )

    problems += _input_problems(dev_inputs, "requirements.txt", dev, "requirements.lock")
    problems += _input_problems(runtime_inputs, "requirements-runtime.txt", runtime, "requirements-runtime.lock")

    have = {canonicalize_name(n): v for n, v in installed.items()}
    for name, entries in dev.items():
        wanted = _select(entries, environment)
        if len(wanted) > 1:
            problems.append(f"requirements.lock pins {name} as {' and '.join(wanted)} for this environment — ambiguous")
        elif wanted and name not in have:
            problems.append(f"requirements.lock pins {name}=={wanted[0]} but it is not installed — {_SYNC}")
        elif wanted and Version(have[name]) != Version(wanted[0]):
            problems.append(
                f"requirements.lock pins {name}=={wanted[0]} for this environment but {have[name]} "
                f"is installed — {_SYNC}"
            )
        elif not wanted and name in have:
            only = " or ".join(m for _, m in entries)
            problems.append(
                f"{name}=={have[name]} is installed, but requirements.lock pins it only where {only} "
                f"— the lock vouches for nothing here; {_SYNC}"
            )
    for name in sorted(have.keys() - dev.keys()):
        problems.append(
            f"{name}=={have[name]} is installed but not in requirements.lock — declare it in "
            f"requirements.txt and re-lock, or {_SYNC} to remove it"
        )

    if image_environment is not None:
        for name, entries in runtime.items():
            if name in disagree:
                continue
            image_versions = _select(entries, image_environment)
            venv_versions = _select(dev.get(name, []), environment)
            if image_versions and venv_versions and image_versions != venv_versions:
                problems.append(
                    f"the tests run {name}=={', '.join(venv_versions)} here but the image installs "
                    f"{name}=={', '.join(image_versions)} — a marker splits them"
                )
    return problems


def interpreter_problems(venv_version: str, image_version: str) -> tuple[list[str], list[str]]:
    """(problems, notes). A different major.minor fails; a different patch is a
    note — uv's interpreter builds can lag the image's (2026-10-08: uv offered
    3.12.14, the image ran 3.12.15), and that must not block a push."""
    if venv_version == image_version:
        return [], []
    fix = f"`uv venv --python {image_version}` then `uv pip sync requirements.lock`"
    if venv_version.split(".")[:2] != image_version.split(".")[:2]:
        return [f"the venv runs Python {venv_version} but the image runs {image_version} — recreate it: {fix}"], []
    return [], [f"the venv runs Python {venv_version}, the image {image_version} — when uv offers it: {fix}"]


def stale_lock_problems(lock_name: str, committed: str, fresh: str) -> list[str]:
    """Compare the committed lock with a fresh re-lock, pin for pin."""
    def flat(text: str) -> set[tuple[str, str, str]]:
        return {(n, v, m) for n, entries in parse_lock(text).items() for v, m in entries}

    def show(entries: set) -> str:
        return ", ".join(f"{n}=={v}" + (f" ; {m}" if m else "") for n, v, m in sorted(entries))

    now, then = flat(fresh), flat(committed)
    if now == then:
        return []
    changes = []
    if now - then:
        changes.append(f"adds {show(now - then)}")
    if then - now:
        changes.append(f"removes {show(then - now)}")
    return [f"{lock_name} is stale — re-locking {'; '.join(changes)}. Re-run the command in its header."]


def _relock(lock_name: str) -> str:
    """Re-run the lock's own header command into a scratch copy and return it.

    Starting from a copy keeps uv's preference for the existing pins, so the
    only differences are the ones the inputs force. --offline first: uv's cache
    normally holds everything, and the gate should not need PyPI.
    """
    text = (ROOT / lock_name).read_text()
    header = next(line for line in text.splitlines() if line.startswith("#    uv pip compile"))
    argv = shlex.split(header.lstrip("#").strip())
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / lock_name
        scratch.write_text(text)
        argv[argv.index("-o") + 1] = str(scratch)
        for extra in (["--offline"], []):
            result = subprocess.run([*argv, *extra, "--quiet"], cwd=ROOT, capture_output=True, text=True)
            if result.returncode == 0:
                return scratch.read_text()
        raise RuntimeError(result.stderr.strip()[-400:])


def main() -> int:
    image_python = _IMAGE_PYTHON_RE.search((ROOT / "Dockerfile").read_text()).group(1)
    problems, notes = interpreter_problems(platform.python_version(), image_python)
    problems += lock_problems(
        dev_lock=(ROOT / "requirements.lock").read_text(),
        runtime_lock=(ROOT / "requirements-runtime.lock").read_text(),
        dev_inputs=(ROOT / "requirements.txt").read_text(),
        runtime_inputs=(ROOT / "requirements-runtime.txt").read_text(),
        installed={d.metadata["Name"]: d.version for d in metadata.distributions()},
        image_environment=image_environment(image_python),
    )
    for lock_name in ("requirements.lock", "requirements-runtime.lock"):
        try:
            fresh = _relock(lock_name)
        except RuntimeError as e:
            problems.append(f"could not re-lock {lock_name} to check it is current: {e}")
            continue
        problems += stale_lock_problems(lock_name, (ROOT / lock_name).read_text(), fresh)

    for n in notes:
        print(f"  note: {n}", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    if problems:
        print(f"==> dependency locks: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    runtime_pins = sum(len(e) for e in parse_lock((ROOT / "requirements-runtime.lock").read_text()).values())
    print(
        f"==> dependency locks: image Python {image_python}, venv {platform.python_version()}; venv == requirements.lock; "
        f"{runtime_pins} runtime pins match it; both locks current"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
