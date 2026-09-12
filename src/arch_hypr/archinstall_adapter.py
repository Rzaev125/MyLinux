from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid5
import errno
import json
import os

from .domain import HashedSecrets, InstallChoices, ProfilePlan

MIB = 1024**2
GIB = 1024**3
ID_NAMESPACE = UUID("0b46aa56-ad54-4ac2-90c7-413040898e33")
# Official ISO 2026.09.01 ships archinstall 4.4-1 (Python metadata: 4.4).
# Schema: https://github.com/archlinux/archinstall/tree/4.4
SUPPORTED_ARCHINSTALL_VERSION = "4.4"


@dataclass(frozen=True)
class ArchinstallPayload:
    config: dict
    creds: dict = field(repr=False)


def _size(value: int) -> dict:
    return {"unit": "B", "value": value, "sector_size": {"unit": "B", "value": 512}}


def build_payload(
    choices: InstallChoices, secrets: HashedSecrets, profile: ProfilePlan
) -> ArchinstallPayload:
    if choices.encryption and not secrets.luks_passphrase:
        raise ValueError("LUKS passphrase is required when encryption is enabled")
    if not choices.encryption and secrets.luks_passphrase is not None:
        raise ValueError("LUKS passphrase must be absent when encryption is disabled")

    device = choices.device.as_posix()
    esp_id = str(uuid5(ID_NAMESPACE, f"{device}:esp"))
    root_id = str(uuid5(ID_NAMESPACE, f"{device}:root"))
    root_start = GIB + MIB
    root_length = ((choices.disk_size_bytes - root_start - MIB) // MIB) * MIB
    partitions = [
        {
            "btrfs": [],
            "flags": ["boot", "esp"],
            "fs_type": "fat32",
            "mount_options": [],
            "mountpoint": "/boot",
            "obj_id": esp_id,
            "start": _size(MIB),
            "size": _size(GIB),
            "dev_path": None,
            "status": "create",
            "type": "primary",
        },
        {
            "btrfs": [
                {"name": "@", "mountpoint": "/"},
                {"name": "@home", "mountpoint": "/home"},
                {"name": "@snapshots", "mountpoint": "/.snapshots"},
                {"name": "@var_log", "mountpoint": "/var/log"},
            ],
            "flags": [],
            "fs_type": "btrfs",
            "mount_options": ["compress=zstd"],
            "mountpoint": None,
            "obj_id": root_id,
            "start": _size(root_start),
            "size": _size(root_length),
            "dev_path": None,
            "status": "create",
            "type": "primary",
        },
    ]
    config = {
        "mirror_config": {
            "mirror_regions": {},
            "custom_servers": [],
            "optional_repositories": list(profile.repositories),
            "custom_repositories": [],
        },
        "bootloader_config": {
            "bootloader": "Systemd-boot",
            "uki": False,
            "removable": False,
        },
        "disk_config": {
            "config_type": "manual_partitioning",
            "device_modifications": [
                {
                    "device": device,
                    "wipe": True,
                    "partitions": partitions,
                }
            ],
        },
        "hostname": choices.hostname,
        "kernels": ["linux"],
        "locale_config": {
            "kb_layout": choices.keymap,
            "sys_enc": "UTF-8",
            "sys_lang": choices.locale,
        },
        "ntp": True,
        "packages": list(profile.packages),
        "profile_config": None,
        "script": "guided",
        "silent": True,
        "swap": False,
        "timezone": choices.timezone,
    }
    creds = {
        "users": [
            {
                "username": choices.username,
                "enc_password": secrets.password_hash,
                "sudo": True,
            }
        ]
    }
    if choices.encryption:
        config["disk_encryption"] = {
            "encryption_type": "luks",
            "partitions": [root_id],
        }
        creds["encryption_password"] = secrets.luks_passphrase
    return ArchinstallPayload(config, creds)


def hash_secrets(plain, runner) -> HashedSecrets:
    result = runner.run(
        ("openssl", "passwd", "-6", "-stdin"),
        input_text=plain.login_password + "\n",
    )
    password_hash = result.stdout.strip()
    if result.returncode != 0 or not password_hash:
        raise RuntimeError("password hashing failed")
    return HashedSecrets(password_hash, plain.luks_passphrase)


def _write_private_json(path: Path, value: dict, created: list[Path]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    created.append(path)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")


def _canonical_destination(path: Path) -> str:
    return os.path.normcase(os.fspath(path.resolve(strict=False)))


def _validate_destinations(config: Path, creds: Path) -> None:
    if _canonical_destination(config) == _canonical_destination(creds):
        raise ValueError("config and credentials paths must be distinct")
    for path in (config, creds):
        if os.path.lexists(path):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)


def write_secure_payload(
    payload: ArchinstallPayload, config: Path, creds: Path
) -> None:
    _validate_destinations(config, creds)
    created: list[Path] = []
    try:
        _write_private_json(config, payload.config, created)
        _write_private_json(creds, payload.creds, created)
    except BaseException:
        cleanup_failed = False
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except (OSError, KeyboardInterrupt):
                cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError("payload cleanup failed; private files may remain") from None
        raise


def require_supported_archinstall(runner) -> None:
    message = f"supported archinstall {SUPPORTED_ARCHINSTALL_VERSION} is required"
    try:
        result = runner.run(("archinstall", "--version"))
    except Exception:
        raise RuntimeError(message) from None
    if result.returncode != 0 or result.stdout.strip() != f"archinstall {SUPPORTED_ARCHINSTALL_VERSION}":
        raise RuntimeError(message)


def archinstall_argv(
    config: Path, creds: Path, *, dry_run: bool
) -> tuple[str, ...]:
    args = [
        "archinstall",
        "--config",
        config.as_posix(),
        "--creds",
        creds.as_posix(),
        "--silent",
    ]
    if dry_run:
        args.append("--dry-run")
    return tuple(args)
