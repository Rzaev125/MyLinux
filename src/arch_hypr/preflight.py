from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
import os
import platform
import shutil
import urllib.request


REQUIRED_COMMANDS = frozenset({"archinstall", "lsblk", "findmnt", "openssl"})


@dataclass(frozen=True)
class PreflightFacts:
    machine: str
    efi: bool
    root: bool
    online: bool
    commands: frozenset[str]


def validate_preflight(facts: PreflightFacts) -> tuple[str, ...]:
    errors: list[str] = []
    if facts.machine != "x86_64":
        errors.append("x86-64 is required")
    if not facts.efi:
        errors.append("UEFI boot is required")
    if not facts.root:
        errors.append("root privileges are required")
    if not facts.online:
        errors.append("network access is required")
    missing = sorted(REQUIRED_COMMANDS - facts.commands)
    if missing:
        errors.append(f"missing commands: {', '.join(missing)}")
    return tuple(errors)


def online_probe() -> bool:
    try:
        urllib.request.urlopen("https://archlinux.org/", timeout=5)
    except OSError:
        return False
    return True


def collect_preflight(online_probe: Callable[[], bool]) -> PreflightFacts:
    present = frozenset(name for name in REQUIRED_COMMANDS if shutil.which(name))
    return PreflightFacts(
        machine=platform.machine(),
        efi=Path("/sys/firmware/efi").is_dir(),
        root=hasattr(os, "geteuid") and os.geteuid() == 0,
        online=online_probe(),
        commands=present,
    )
