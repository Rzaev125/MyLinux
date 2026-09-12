"""Console input boundaries. Answers contain choices, never credentials."""
from dataclasses import replace
from getpass import getpass as system_getpass
from pathlib import Path, PurePosixPath
import json
import sys

from .domain import HardwareProfile, InstallChoices, PlainSecrets, SoftwareProfile

ANSWER_KEYS = frozenset({
    "device", "disk_size_bytes", "hostname", "username", "timezone", "keymap",
    "locale", "hardware", "software", "encryption",
})


def load_answers(path: Path) -> InstallChoices:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("answers must be an object")
    unknown, missing = set(data) - ANSWER_KEYS, ANSWER_KEYS - set(data)
    if unknown or missing:
        raise ValueError(f"invalid answer keys; unknown={sorted(unknown)}, missing={sorted(missing)}")
    for key in ANSWER_KEYS - {"disk_size_bytes", "encryption"}:
        if not isinstance(data[key], str):
            raise ValueError(f"{key} must be a string")
    if type(data["disk_size_bytes"]) is not int:
        raise ValueError("disk_size_bytes must be an integer")
    if type(data["encryption"]) is not bool:
        raise ValueError("encryption must be a boolean")
    return InstallChoices(
        device=PurePosixPath(data["device"]), disk_size_bytes=data["disk_size_bytes"],
        hostname=data["hostname"], username=data["username"], timezone=data["timezone"],
        keymap=data["keymap"], locale=data["locale"], hardware=HardwareProfile(data["hardware"]),
        software=SoftwareProfile(data["software"]), encryption=data["encryption"],
    )


def collect_secrets(encryption: bool, *, getpass_fn=system_getpass) -> PlainSecrets:
    password = getpass_fn("Login password: ")
    repeated = getpass_fn("Repeat login password: ")
    if not password or password != repeated:
        raise ValueError("login passwords do not match")
    luks = getpass_fn("LUKS passphrase: ") if encryption else None
    if encryption and not luks:
        raise ValueError("LUKS passphrase is required")
    return PlainSecrets(password, luks)


def collect_interactive(disks, *, input_fn=input, getpass_fn=system_getpass):
    if not disks:
        raise ValueError("no eligible physical disks")
    for index, disk in enumerate(disks, start=1):
        print(f"{index}. {disk.path.as_posix()} | {disk.model} | {disk.size_bytes // 1024**3} GiB")
    index = int(input_fn("Target disk number: "))
    if not 1 <= index <= len(disks):
        raise ValueError("invalid target disk number")
    selected = disks[index - 1]
    hostname = input_fn("Hostname [hyprbox]: ").strip() or "hyprbox"
    username = input_fn("Username: ").strip()
    timezone = input_fn("Timezone [UTC]: ").strip() or "UTC"
    keymap = input_fn("Keymap [us]: ").strip() or "us"
    locale = input_fn("Locale [en_US.UTF-8]: ").strip() or "en_US.UTF-8"
    hardware = HardwareProfile(input_fn("Hardware [intel/amd/nvidia]: ").strip())
    software = SoftwareProfile(input_fn("Profile [minimal/developer/gaming]: ").strip())
    encryption_answer = input_fn("Enable LUKS2 encryption? [Y/n]: ").strip().lower() or "y"
    if encryption_answer not in {"y", "yes", "n", "no"}:
        raise ValueError("encryption answer must be yes or no")
    encryption = encryption_answer in {"y", "yes"}
    if hardware is HardwareProfile.NVIDIA:
        print("EXPERIMENTAL — boot may require manual recovery")
    choices = InstallChoices(selected.path, selected.size_bytes, hostname, username,
                             timezone, keymap, locale, hardware, software, encryption)
    return choices, collect_secrets(encryption, getpass_fn=getpass_fn)


def reconcile_answer_disk(choices: InstallChoices, eligible) -> InstallChoices:
    matches = [disk for disk in eligible if disk.path.as_posix() == choices.device.as_posix()]
    if len(matches) != 1:
        raise ValueError("answer device is not an eligible physical disk")
    if choices.disk_size_bytes != matches[0].size_bytes:
        raise ValueError("answer disk size does not match the discovered disk")
    return replace(choices, device=matches[0].path)


def read_destructive_confirmation(device: Path, *, input_fn=input) -> str:
    if not sys.stdin.isatty():
        raise ValueError("interactive TTY is required for installation")
    print(f"ALL DATA ON {device.as_posix()} WILL BE ERASED")
    return input_fn(f"Type {device.as_posix()} to continue: ")
