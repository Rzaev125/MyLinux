from pathlib import Path, PurePosixPath
import json
import re

from .commands import CommandRunner
from .domain import Disk, DomainError


FINDMNT_ARGS = (
    "findmnt", "--noheadings", "--output", "SOURCE", "/run/archiso/bootmnt",
)
LSBLK_ARGS = (
    "lsblk", "--bytes", "--json", "-o",
    "NAME,PATH,TYPE,SIZE,MODEL,RO,RM,MOUNTPOINTS,PKNAME",
)


def _walk_devices(devices: list[dict], disk_ancestor: dict | None = None):
    for item in devices:
        ancestor = item if item.get("type") == "disk" else disk_ancestor
        yield item, ancestor
        yield from _walk_devices(item.get("children") or [], ancestor)


def parse_disks(payload: dict, live_source: Path | None) -> tuple[Disk, ...]:
    devices = tuple(_walk_devices(payload.get("blockdevices", [])))
    live_disk_path = None
    if live_source is not None:
        source = live_source.as_posix()
        for item, disk_ancestor in devices:
            if item.get("path") == source:
                if disk_ancestor is None or not disk_ancestor.get("path"):
                    raise DomainError("live ISO source has no disk ancestor")
                live_disk_path = disk_ancestor["path"]
                break
        if live_disk_path is None:
            raise DomainError("live ISO source was not found in lsblk")

    result = []
    for item, _ in devices:
        if item.get("type") != "disk":
            continue
        result.append(Disk(
            path=PurePosixPath(item["path"]),
            model=(item.get("model") or "Unknown disk").strip(),
            size_bytes=int(item["size"]),
            removable=bool(item.get("rm")),
            read_only=bool(item.get("ro")),
            live_media=item.get("path") == live_disk_path,
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

    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise DomainError("findmnt returned an invalid live ISO source")
    source = lines[0].strip()
    if not re.fullmatch(r"/dev/[^\s/]+(?:/[^\s/]+)*", source):
        raise DomainError("findmnt returned an invalid live ISO source")
    return PurePosixPath(source)


def discover_disks(runner: CommandRunner) -> tuple[Disk, ...]:
    live_source = _find_live_source(runner)
    result = runner.run(LSBLK_ARGS)
    if result.returncode != 0:
        raise DomainError(f"lsblk failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise DomainError("lsblk returned invalid JSON") from error
    return parse_disks(payload, live_source)


def require_exact_confirmation(device: Path, typed: str) -> None:
    if typed != device.as_posix():
        raise DomainError("confirmation did not match the selected device path")
