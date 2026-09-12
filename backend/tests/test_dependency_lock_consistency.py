from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
LOCKED_COMPONENTS = ("backend", "watchdog")

_DECLARED = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s;#]+)"
)
_LOCKED = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s\\]+)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path: Path, pattern: re.Pattern[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        pins[_canonical(match["name"])] = match["version"]
    return pins


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_declared_dependencies_are_pinned(component: str) -> None:
    declared = REPO / component / "requirements.txt"
    for line in declared.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert _DECLARED.match(stripped), f"{declared} must pin '{stripped}' with =="


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_lock_matches_declared_dependency_versions(component: str) -> None:
    declared = _pins(REPO / component / "requirements.txt", _DECLARED)
    locked = _pins(REPO / component / "requirements.lock", _LOCKED)

    assert declared, f"{component}/requirements.txt must declare dependencies"

    drift = {
        name: (version, locked.get(name))
        for name, version in declared.items()
        if locked.get(name) != version
    }
    assert not drift, (
        f"{component}/requirements.lock is out of sync with requirements.txt "
        f"(declared, locked): {drift}. Regenerate the lock with pip-compile."
    )


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_every_locked_dependency_carries_hashes(component: str) -> None:
    lock = REPO / component / "requirements.lock"
    current: str | None = None
    hashed: set[str] = set()
    pinned: set[str] = set()
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = _LOCKED.match(line)
        if match is not None:
            current = _canonical(match["name"])
            pinned.add(current)
        elif current is not None and "--hash=sha256:" in line:
            hashed.add(current)
    assert pinned, f"{lock} must pin dependencies"
    assert pinned == hashed, f"{lock} entries without hashes: {sorted(pinned - hashed)}"
