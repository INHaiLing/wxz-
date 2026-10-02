"""Fail when the installed application environment differs from the full lock."""

import re
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;]+)$")


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError(f"{path.name}: dependencies must use exact version pins.")
        name, version = match.groups()
        key = canonical(name)
        if key in pins:
            raise ValueError(f"{path.name}: duplicate dependency {name}.")
        pins[key] = version
    return pins


def main():
    direct = read_pins(ROOT / "requirements.in")
    locked = read_pins(ROOT / "requirements.txt")
    installed = {canonical(dist.metadata["Name"]): dist.version for dist in metadata.distributions()}
    for name, version in direct.items():
        if locked.get(name) != version:
            raise ValueError(f"Direct requirement {name} differs from requirements.txt.")
    for name, version in locked.items():
        if installed.get(name) != version:
            raise ValueError(f"Locked requirement {name} is absent or has the wrong version.")
    extra = installed.keys() - locked.keys() - {"pip", "setuptools", "wheel"}
    if extra:
        raise ValueError("Untracked environment dependencies: " + ", ".join(sorted(extra)))
    print(f"PASS: {len(direct)} direct dependencies and {len(locked)} complete locked dependencies.")


if __name__ == "__main__":
    main()
