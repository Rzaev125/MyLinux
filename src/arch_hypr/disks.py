from pathlib import Path, PurePosixPath
import json

from .commands import CommandRunner
from .domain import Disk, DomainError


FINDMNT_ARGS = (
    "findmnt", "--noheadings", "--output", "SOURCE", "/run/archiso/bootmnt",
)
LSBLK_ARGS = (
    "lsblk", "--bytes", "--json", "-o",
    "NAME,PATH,TYPE,SIZE,MODEL,RO,RM,MOUNTPOINTS,PKNAME",
)


def parse_disks(payload: dict, live_source: Path | None) -> tuple[Disk, ...]:
    devices = payload.get("blockdevices", [])
    live_parent = None
    for item in devices:
        if item.get("path") == (live_source.as_posix() if live_source else None):
            live_parent = item.get("pkname") or item.get("name")
            break

    result = []
    for item in devices:
        if item.get("type") != "disk":
            continue
        result.append(Disk(
            path=PurePosixPath(item["path"]),
            model=(item.get("model") or "Unknown disk").strip(),
            size_bytes=int(item["size"]),
            removable=bool(item.get("rm")),
            read_only=bool(item.get("ro")),
            live_media=item.get("name") == live_parent,
        ))
    return tuple(result)


def eligible_disks(disks: tuple[Disk, ...]) -> tuple[Disk, ...]:
    return tuple(
        disk for disk in disks
        if not disk.read_only and not disk.live_media and disk.size_bytes >= 8 * 1024**3
    )


def _find_live_source(runner: CommandRunner) -> Path:
    result = runner.run(FINDMNT_ARGS)
    if result.returncode != 0:
        raise DomainError(f"findmnt failed: {result.stderr.strip()}")

    source = result.stdout.strip()
    if not source:
        raise DomainError("findmnt did not identify the live ISO source")
    return PurePosixPath(source)


def discover_disks(
    runner: CommandRunner, live_source: Path | None = None,
) -> tuple[Disk, ...]:
    resolved_live_source = live_source or _find_live_source(runner)
    result = runner.run(LSBLK_ARGS)
    if result.returncode != 0:
        raise DomainError(f"lsblk failed: {result.stderr.strip()}")
    return parse_disks(json.loads(result.stdout), resolved_live_source)


def require_exact_confirmation(device: Path, typed: str) -> None:
    if typed != device.as_posix():
        raise DomainError("confirmation did not match the selected device path")
