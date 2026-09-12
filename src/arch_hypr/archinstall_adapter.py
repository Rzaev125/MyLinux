from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid5
import json
import os

from .domain import HashedSecrets, InstallChoices, ProfilePlan

MIB = 1024**2
GIB = 1024**3
ID_NAMESPACE = UUID("0b46aa56-ad54-4ac2-90c7-413040898e33")


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
    if not choices.encryption and secrets.luks_passphrase:
        raise ValueError("LUKS passphrase must be absent when encryption is disabled")

    device = choices.device.as_posix()
    esp_id = str(uuid5(ID_NAMESPACE, f"{device}:esp"))
    root_id = str(uuid5(ID_NAMESPACE, f"{device}:root"))
    root_start = GIB + MIB
    root_length = choices.disk_size_bytes - root_start - MIB
    partitions = [
        {
            "btrfs": [],
            "flags": ["boot"],
            "fs_type": "fat32",
            "mount_options": [],
            "mountpoint": "/boot",
            "obj_id": esp_id,
            "start": _size(MIB),
            "length": _size(GIB),
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
            "length": _size(root_length),
            "status": "create",
            "type": "primary",
        },
    ]
    config = {
        "additional-repositories": list(profile.repositories),
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
        "custom_commands": [
            f"/usr/bin/python /run/arch-hypr-installer/payload/post_install.py --username {choices.username}"
        ],
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
        creds["!encryption-password"] = secrets.luks_passphrase
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


def _write_private_json(path: Path, value: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")


def write_secure_payload(
    payload: ArchinstallPayload, config: Path, creds: Path
) -> None:
    _write_private_json(config, payload.config)
    _write_private_json(creds, payload.creds)


def archinstall_argv(
    config: Path, creds: Path, *, dry_run: bool
) -> tuple[str, ...]:
    args = [
        "archinstall",
        "--config",
        config.as_posix(),
        "--creds",
        creds.as_posix(),
    ]
    args.append("--dry-run" if dry_run else "--silent")
    return tuple(args)
