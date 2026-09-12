# Safe Installer and Hyprland Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a testable Python installer that runs from the official Arch ISO, safely prepares the approved full-disk Arch configuration, and installs an idempotent Hyprland base profile.

**Architecture:** Keep environment inspection, disk selection, profile composition, `archinstall` serialization, and target-root application behind small typed interfaces. Pure functions produce plans and JSON; a single orchestrator performs subprocess and filesystem effects only after preflight and exact device-name confirmation.

**Tech Stack:** Python 3.12+, standard library, `archinstall` CLI from the official ISO, GTK-independent console UI, pytest, JSON profile manifests, Hyprland Lua configuration.

**Spec:** `docs/superpowers/specs/2026-09-12-arch-hyprland-gpt-installer-design.md`

## Global Constraints

- Target only x86-64 systems booted in UEFI mode.
- Install only to an entire selected disk; dual boot and partition preservation are unsupported.
- Require the user to type the exact selected device path immediately before destructive execution.
- Use systemd-boot, a 1 GiB FAT32 ESP, and Btrfs for the remaining space.
- Enable LUKS2 by default and permit an explicit unencrypted choice.
- Create Btrfs subvolumes `@`, `@home`, `@snapshots`, and `@var_log`.
- Configure zram and no disk swap.
- Treat Intel and AMD as supported; mark NVIDIA experimental in every summary and log.
- Install packages only from official Arch repositories.
- Keep credentials outside the normal installer configuration, out of logs, and out of Git.
- Runtime installer code uses no third-party Python dependency beyond the `archinstall` executable already supplied by the ISO.
- Every subprocess call uses an argument vector with `shell=False`.
- A dry run performs no disk, mount, chroot, service, or reboot mutation.

---

## Planned File Structure

```text
pyproject.toml
src/arch_hypr/
  __init__.py                 package version
  cli.py                      argument parsing and process exit codes
  commands.py                 shell-free subprocess abstraction
  domain.py                   immutable choices, secrets, disks, and plans
  preflight.py                x86-64/UEFI/root/network/tool checks
  disks.py                    lsblk parsing and destructive confirmation
  profiles.py                 manifest loading and deterministic merging
  archinstall_adapter.py      config/creds generation and invocation argv
  staging.py                  build the /run payload used inside arch-chroot
  orchestrator.py             dry-run, validation, and install state machine
  ui.py                       console questions and final confirmation
  resources/
    profiles/base.json
    profiles/hardware/{intel,amd,nvidia}.json
    profiles/software/{minimal,developer,gaming}.json
    rootfs/etc/greetd/config.toml
    rootfs/etc/systemd/zram-generator.conf
    home/.config/hypr/hyprland.lua
    home/.config/hypr/modules/*.lua
    home/.config/hypr/local/overrides.lua
    post_install.py
tests/
  fixtures/lsblk.json
  fixtures/answers-{amd-encrypted,intel-plain}.json
  test_domain.py
  test_preflight.py
  test_disks.py
  test_profiles.py
  test_archinstall_adapter.py
  test_staging.py
  test_orchestrator.py
  test_cli.py
scripts/archiso-smoke.sh
README.md
```

The package is named `arch_hypr` as a neutral internal identifier; public branding is not encoded in module or protocol names.

---

### Task 1: Package Foundation and Domain Types

**Files:**
- Create: `pyproject.toml`
- Create: `src/arch_hypr/__init__.py`
- Create: `src/arch_hypr/domain.py`
- Create: `tests/test_domain.py`

**Interfaces:**
- Consumes: none
- Produces: `HardwareProfile`, `SoftwareProfile`, `InstallChoices`, `PlainSecrets`, `HashedSecrets`, `Disk`, `ProfilePlan`, and `DomainError`

- [ ] **Step 1: Write failing validation tests**

```python
# tests/test_domain.py
from pathlib import Path
import pytest

from arch_hypr.domain import (
    DomainError,
    HardwareProfile,
    InstallChoices,
    SoftwareProfile,
)


def valid_choices(**overrides):
    values = {
        "device": Path("/dev/nvme0n1"),
        "disk_size_bytes": 128 * 1024**3,
        "hostname": "hyprbox",
        "username": "alex",
        "timezone": "Europe/Warsaw",
        "keymap": "us",
        "locale": "en_US.UTF-8",
        "hardware": HardwareProfile.AMD,
        "software": SoftwareProfile.MINIMAL,
        "encryption": True,
    }
    values.update(overrides)
    return InstallChoices(**values)


def test_valid_choices_are_immutable():
    choices = valid_choices()
    with pytest.raises(AttributeError):
        choices.hostname = "changed"


@pytest.mark.parametrize("username", ["Root", "two words", "-dash", ""])
def test_invalid_username_is_rejected(username):
    with pytest.raises(DomainError, match="username"):
        valid_choices(username=username)


def test_non_device_path_is_rejected():
    with pytest.raises(DomainError, match="device"):
        valid_choices(device=Path("nvme0n1"))
```

- [ ] **Step 2: Run the tests and confirm the import failure**

Run: `python -m pytest tests/test_domain.py -v`

Expected: FAIL because `arch_hypr.domain` does not exist.

- [ ] **Step 3: Add packaging metadata and immutable domain types**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "arch-hypr-installer"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-cov>=6.0"]

[project.scripts]
arch-hypr-installer = "arch_hypr.cli:main"

[tool.setuptools.package-data]
arch_hypr = ["resources/**/*.json", "resources/**/*.lua", "resources/**/*.toml", "resources/**/*.py"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```python
# src/arch_hypr/domain.py
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import re


class DomainError(ValueError):
    pass


class HardwareProfile(StrEnum):
    INTEL = "intel"
    AMD = "amd"
    NVIDIA = "nvidia"


class SoftwareProfile(StrEnum):
    MINIMAL = "minimal"
    DEVELOPER = "developer"
    GAMING = "gaming"


@dataclass(frozen=True)
class Disk:
    path: Path
    model: str
    size_bytes: int
    removable: bool
    read_only: bool
    live_media: bool = False


@dataclass(frozen=True)
class InstallChoices:
    device: Path
    disk_size_bytes: int
    hostname: str
    username: str
    timezone: str
    keymap: str
    locale: str
    hardware: HardwareProfile
    software: SoftwareProfile
    encryption: bool = True

    def __post_init__(self) -> None:
        if not str(self.device).startswith("/dev/"):
            raise DomainError("device must be an absolute /dev path")
        if self.disk_size_bytes < 8 * 1024**3:
            raise DomainError("device must be at least 8 GiB")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.username):
            raise DomainError("username is invalid")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}", self.hostname):
            raise DomainError("hostname is invalid")


@dataclass(frozen=True)
class PlainSecrets:
    login_password: str = field(repr=False)
    luks_passphrase: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class HashedSecrets:
    password_hash: str = field(repr=False)
    luks_passphrase: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ProfilePlan:
    version: int
    packages: tuple[str, ...]
    services: tuple[str, ...]
    repositories: tuple[str, ...]
    experimental: bool = False
```

Set `__version__ = "0.1.0"` in `src/arch_hypr/__init__.py`.

- [ ] **Step 4: Run the domain tests**

Run: `python -m pytest tests/test_domain.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the foundation**

```bash
git add pyproject.toml src/arch_hypr/__init__.py src/arch_hypr/domain.py tests/test_domain.py
git commit -m "build: add installer package foundation"
```

---

### Task 2: Shell-Free Commands and Preflight Validation

**Files:**
- Create: `src/arch_hypr/commands.py`
- Create: `src/arch_hypr/preflight.py`
- Create: `tests/test_preflight.py`

**Interfaces:**
- Consumes: `DomainError`
- Produces: `CommandResult`, `CommandRunner`, `SubprocessRunner`, `PreflightFacts`, `collect_preflight()`, and `validate_preflight()`

- [ ] **Step 1: Write failing preflight tests**

```python
# tests/test_preflight.py
from arch_hypr.preflight import PreflightFacts, validate_preflight


def test_supported_environment_has_no_errors():
    facts = PreflightFacts(
        machine="x86_64", efi=True, root=True, online=True,
        commands=frozenset({"archinstall", "lsblk", "findmnt", "openssl"}),
    )
    assert validate_preflight(facts) == ()


def test_all_unsupported_conditions_are_reported_together():
    facts = PreflightFacts(
        machine="aarch64", efi=False, root=False, online=False,
        commands=frozenset({"lsblk"}),
    )
    assert validate_preflight(facts) == (
        "x86-64 is required",
        "UEFI boot is required",
        "root privileges are required",
        "network access is required",
        "missing commands: archinstall, findmnt, openssl",
    )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_preflight.py -v`

Expected: FAIL because `arch_hypr.preflight` does not exist.

- [ ] **Step 3: Implement the command boundary and pure validator**

```python
# src/arch_hypr/commands.py
from dataclasses import dataclass
from typing import Protocol, Sequence
import subprocess


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, input_text: str | None = None) -> CommandResult: ...


class SubprocessRunner:
    def run(self, argv: Sequence[str], *, input_text: str | None = None) -> CommandResult:
        completed = subprocess.run(
            list(argv), input=input_text, text=True, capture_output=True,
            check=False, shell=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
```

```python
# src/arch_hypr/preflight.py
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
import os
import platform
import shutil


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


def collect_preflight(online_probe: Callable[[], bool]) -> PreflightFacts:
    present = frozenset(name for name in REQUIRED_COMMANDS if shutil.which(name))
    return PreflightFacts(
        machine=platform.machine(),
        efi=Path("/sys/firmware/efi").is_dir(),
        root=hasattr(os, "geteuid") and os.geteuid() == 0,
        online=online_probe(),
        commands=present,
    )
```

Implement `online_probe()` in `preflight.py` with `urllib.request.urlopen("https://archlinux.org/", timeout=5)` and return `False` on `OSError`. Do not disable TLS verification.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_preflight.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit preflight support**

```bash
git add src/arch_hypr/commands.py src/arch_hypr/preflight.py tests/test_preflight.py
git commit -m "feat: add installer preflight validation"
```

---

### Task 3: Disk Discovery and Destructive Confirmation

**Files:**
- Create: `src/arch_hypr/disks.py`
- Create: `tests/fixtures/lsblk.json`
- Create: `tests/test_disks.py`

**Interfaces:**
- Consumes: `CommandRunner`, `Disk`, `DomainError`
- Produces: `parse_disks()`, `discover_disks()`, `eligible_disks()`, and `require_exact_confirmation()`

- [ ] **Step 1: Add a realistic lsblk fixture and failing tests**

Use this fixture shape in `tests/fixtures/lsblk.json`:

```json
{
  "blockdevices": [
    {"name":"nvme0n1","path":"/dev/nvme0n1","type":"disk","size":512110190592,"model":"Work SSD","ro":false,"rm":false,"mountpoints":[null],"pkname":null},
    {"name":"sda","path":"/dev/sda","type":"disk","size":128035676160,"model":"Target SSD","ro":false,"rm":false,"mountpoints":[null],"pkname":null},
    {"name":"sdb","path":"/dev/sdb","type":"disk","size":34359738368,"model":"ARCHISO","ro":false,"rm":true,"mountpoints":[null],"pkname":null},
    {"name":"sdb1","path":"/dev/sdb1","type":"part","size":2147483648,"model":null,"ro":false,"rm":true,"mountpoints":["/run/archiso/bootmnt"],"pkname":"sdb"}
  ]
}
```

```python
# tests/test_disks.py
from pathlib import Path
import json
import pytest

from arch_hypr.disks import eligible_disks, parse_disks, require_exact_confirmation
from arch_hypr.domain import DomainError


def fixture_payload():
    return json.loads(Path("tests/fixtures/lsblk.json").read_text())


def test_live_media_and_read_only_devices_are_not_eligible():
    disks = parse_disks(fixture_payload(), live_source=Path("/dev/sdb1"))
    assert [str(d.path) for d in eligible_disks(disks)] == ["/dev/nvme0n1", "/dev/sda"]
    assert next(d for d in disks if str(d.path) == "/dev/sdb").live_media is True


def test_confirmation_must_equal_full_device_path():
    require_exact_confirmation(Path("/dev/sda"), "/dev/sda")
    with pytest.raises(DomainError, match="confirmation"):
        require_exact_confirmation(Path("/dev/sda"), "sda")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_disks.py -v`

Expected: FAIL because `arch_hypr.disks` does not exist.

- [ ] **Step 3: Implement disk parsing and discovery**

```python
# src/arch_hypr/disks.py
from pathlib import Path
import json

from .commands import CommandRunner
from .domain import Disk, DomainError


LSBLK_ARGS = (
    "lsblk", "--bytes", "--json", "-o",
    "NAME,PATH,TYPE,SIZE,MODEL,RO,RM,MOUNTPOINTS,PKNAME",
)


def parse_disks(payload: dict, live_source: Path | None) -> tuple[Disk, ...]:
    devices = payload.get("blockdevices", [])
    live_parent = None
    for item in devices:
        if item.get("path") == str(live_source):
            live_parent = item.get("pkname") or item.get("name")
            break
    result = []
    for item in devices:
        if item.get("type") != "disk":
            continue
        result.append(Disk(
            path=Path(item["path"]),
            model=(item.get("model") or "Unknown disk").strip(),
            size_bytes=int(item["size"]),
            removable=bool(item.get("rm")),
            read_only=bool(item.get("ro")),
            live_media=item.get("name") == live_parent,
        ))
    return tuple(result)


def eligible_disks(disks: tuple[Disk, ...]) -> tuple[Disk, ...]:
    return tuple(d for d in disks if not d.read_only and not d.live_media and d.size_bytes >= 8 * 1024**3)


def discover_disks(runner: CommandRunner, live_source: Path | None) -> tuple[Disk, ...]:
    result = runner.run(LSBLK_ARGS)
    if result.returncode != 0:
        raise DomainError(f"lsblk failed: {result.stderr.strip()}")
    return parse_disks(json.loads(result.stdout), live_source)


def require_exact_confirmation(device: Path, typed: str) -> None:
    if typed != str(device):
        raise DomainError("confirmation did not match the selected device path")
```

Resolve `live_source` by running `findmnt --noheadings --output SOURCE /run/archiso/bootmnt`; never infer it from `removable=True` alone.

- [ ] **Step 4: Run disk tests**

Run: `python -m pytest tests/test_disks.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit disk safety**

```bash
git add src/arch_hypr/disks.py tests/fixtures/lsblk.json tests/test_disks.py
git commit -m "feat: add safe target disk selection"
```

---

### Task 4: Versioned Profile Manifests

**Files:**
- Create: `src/arch_hypr/profiles.py`
- Create: `src/arch_hypr/resources/profiles/base.json`
- Create: `src/arch_hypr/resources/profiles/hardware/intel.json`
- Create: `src/arch_hypr/resources/profiles/hardware/amd.json`
- Create: `src/arch_hypr/resources/profiles/hardware/nvidia.json`
- Create: `src/arch_hypr/resources/profiles/software/minimal.json`
- Create: `src/arch_hypr/resources/profiles/software/developer.json`
- Create: `src/arch_hypr/resources/profiles/software/gaming.json`
- Create: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `HardwareProfile`, `SoftwareProfile`, `ProfilePlan`
- Produces: `load_profile()` and `compose_profile()`

- [ ] **Step 1: Write failing composition tests**

```python
# tests/test_profiles.py
from arch_hypr.domain import HardwareProfile, SoftwareProfile
from arch_hypr.profiles import compose_profile


def test_amd_developer_profile_is_stable_and_unique():
    plan = compose_profile(HardwareProfile.AMD, SoftwareProfile.DEVELOPER)
    assert plan.version == 1
    assert "hyprland" in plan.packages
    assert "vulkan-radeon" in plan.packages
    assert "podman" in plan.packages
    assert len(plan.packages) == len(set(plan.packages))
    assert plan.experimental is False


def test_gaming_enables_multilib():
    plan = compose_profile(HardwareProfile.INTEL, SoftwareProfile.GAMING)
    assert plan.repositories == ("multilib",)
    assert {"steam", "gamescope", "mangohud"} <= set(plan.packages)


def test_nvidia_is_always_experimental():
    plan = compose_profile(HardwareProfile.NVIDIA, SoftwareProfile.MINIMAL)
    assert plan.experimental is True
    assert {"nvidia-dkms", "nvidia-utils", "linux-headers"} <= set(plan.packages)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_profiles.py -v`

Expected: FAIL because profile resources and loader do not exist.

- [ ] **Step 3: Add exact version-1 manifests**

Use this schema for every manifest:

```json
{"version":1,"packages":[],"services":[],"repositories":[],"experimental":false}
```

Populate the lists exactly as follows:

- `base.json` packages: `base`, `linux`, `linux-firmware`, `btrfs-progs`, `cryptsetup`, `networkmanager`, `sudo`, `zram-generator`, `hyprland`, `xdg-desktop-portal-hyprland`, `xdg-desktop-portal-gtk`, `waybar`, `kitty`, `pipewire`, `pipewire-audio`, `pipewire-pulse`, `wireplumber`, `greetd`, `greetd-tuigreet`, `polkit`, `gnome-keyring`, `git`, `python`, `python-gobject`, `gtk4`, `gtk4-layer-shell`, `mesa`, `vulkan-icd-loader`, `noto-fonts`, `noto-fonts-emoji`; services: `NetworkManager.service`, `greetd.service`.
- `hardware/intel.json` packages: `vulkan-intel`, `intel-media-driver`.
- `hardware/amd.json` packages: `vulkan-radeon`, `libva-mesa-driver`.
- `hardware/nvidia.json` packages: `nvidia-dkms`, `nvidia-utils`, `linux-headers`, `egl-wayland2`; set `experimental` to `true`.
- `software/minimal.json`: no additions.
- `software/developer.json` packages: `base-devel`, `podman`.
- `software/gaming.json` packages: `steam`, `gamescope`, `mangohud`; repositories: `multilib`.

Keep every manifest at version `1` and keep all unlisted arrays empty.

- [ ] **Step 4: Implement deterministic loading and merging**

```python
# src/arch_hypr/profiles.py
from importlib.resources import files
import json

from .domain import HardwareProfile, ProfilePlan, SoftwareProfile


def load_profile(relative: str) -> dict:
    resource = files("arch_hypr").joinpath("resources", "profiles", relative)
    return json.loads(resource.read_text(encoding="utf-8"))


def _unique(values):
    return tuple(dict.fromkeys(values))


def compose_profile(hardware: HardwareProfile, software: SoftwareProfile) -> ProfilePlan:
    layers = (
        load_profile("base.json"),
        load_profile(f"hardware/{hardware.value}.json"),
        load_profile(f"software/{software.value}.json"),
    )
    versions = {layer["version"] for layer in layers}
    if versions != {1}:
        raise ValueError(f"incompatible profile versions: {sorted(versions)}")
    return ProfilePlan(
        version=1,
        packages=_unique(pkg for layer in layers for pkg in layer["packages"]),
        services=_unique(service for layer in layers for service in layer["services"]),
        repositories=_unique(repo for layer in layers for repo in layer["repositories"]),
        experimental=any(layer["experimental"] for layer in layers),
    )
```

- [ ] **Step 5: Run profile tests and commit**

Run: `python -m pytest tests/test_profiles.py -v`

Expected: all tests PASS.

```bash
git add src/arch_hypr/profiles.py src/arch_hypr/resources/profiles tests/test_profiles.py
git commit -m "feat: add versioned system profiles"
```

---

### Task 5: Archinstall Config, Credentials, and Password Hashing

**Files:**
- Create: `src/arch_hypr/archinstall_adapter.py`
- Create: `tests/test_archinstall_adapter.py`

**Interfaces:**
- Consumes: `InstallChoices`, `PlainSecrets`, `HashedSecrets`, `ProfilePlan`, `CommandRunner`
- Produces: `ArchinstallPayload`, `hash_secrets()`, `build_payload()`, `write_secure_payload()`, and `archinstall_argv()`

- [ ] **Step 1: Write failing encrypted and unencrypted payload tests**

```python
# tests/test_archinstall_adapter.py
from pathlib import Path

from arch_hypr.archinstall_adapter import build_payload, archinstall_argv
from arch_hypr.domain import (
    HardwareProfile, HashedSecrets, InstallChoices, ProfilePlan, SoftwareProfile,
)


def choices(encryption=True):
    return InstallChoices(
        Path("/dev/sda"), 64 * 1024**3, "hyprbox", "alex", "UTC", "us",
        "en_US.UTF-8", HardwareProfile.AMD, SoftwareProfile.MINIMAL, encryption,
    )


def profile():
    return ProfilePlan(1, ("base", "linux", "hyprland"), ("NetworkManager.service",), ())


def test_encrypted_payload_has_expected_layout_and_separate_secret():
    payload = build_payload(choices(True), HashedSecrets("$6$hash", "luks-secret"), profile())
    partitions = payload.config["disk_config"]["device_modifications"][0]["partitions"]
    assert partitions[0]["mountpoint"] == "/boot"
    assert {item["name"] for item in partitions[1]["btrfs"]} == {"@", "@home", "@snapshots", "@var_log"}
    assert payload.config["disk_encryption"]["partitions"] == [partitions[1]["obj_id"]]
    assert payload.creds["!encryption-password"] == "luks-secret"
    assert "luks-secret" not in repr(payload)


def test_plain_payload_omits_disk_encryption_and_luks_secret():
    payload = build_payload(choices(False), HashedSecrets("$6$hash", None), profile())
    assert "disk_encryption" not in payload.config
    assert "!encryption-password" not in payload.creds


def test_archinstall_command_is_an_argument_vector():
    assert archinstall_argv(Path("/run/i/config.json"), Path("/run/i/creds.json"), dry_run=True) == (
        "archinstall", "--config", "/run/i/config.json", "--creds", "/run/i/creds.json", "--dry-run",
    )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_archinstall_adapter.py -v`

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Implement the schema adapter**

```python
# core of src/arch_hypr/archinstall_adapter.py
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


def build_payload(choices: InstallChoices, secrets: HashedSecrets, profile: ProfilePlan) -> ArchinstallPayload:
    if choices.encryption and not secrets.luks_passphrase:
        raise ValueError("LUKS passphrase is required when encryption is enabled")
    if not choices.encryption and secrets.luks_passphrase:
        raise ValueError("LUKS passphrase must be absent when encryption is disabled")

    esp_id = str(uuid5(ID_NAMESPACE, f"{choices.device}:esp"))
    root_id = str(uuid5(ID_NAMESPACE, f"{choices.device}:root"))
    root_start = GIB + MIB
    root_length = choices.disk_size_bytes - root_start - MIB
    partitions = [
        {
            "btrfs": [], "flags": ["boot"], "fs_type": "fat32",
            "mount_options": [], "mountpoint": "/boot", "obj_id": esp_id,
            "start": _size(MIB), "length": _size(GIB), "status": "create", "type": "primary",
        },
        {
            "btrfs": [
                {"name": "@", "mountpoint": "/"},
                {"name": "@home", "mountpoint": "/home"},
                {"name": "@snapshots", "mountpoint": "/.snapshots"},
                {"name": "@var_log", "mountpoint": "/var/log"},
            ],
            "flags": [], "fs_type": "btrfs", "mount_options": ["compress=zstd"],
            "mountpoint": None, "obj_id": root_id, "start": _size(root_start),
            "length": _size(root_length), "status": "create", "type": "primary",
        },
    ]
    config = {
        "additional-repositories": list(profile.repositories),
        "bootloader_config": {"bootloader": "Systemd-boot", "uki": False, "removable": False},
        "disk_config": {"config_type": "manual_partitioning", "device_modifications": [
            {"device": str(choices.device), "wipe": True, "partitions": partitions}
        ]},
        "hostname": choices.hostname,
        "kernels": ["linux"],
        "locale_config": {"kb_layout": choices.keymap, "sys_enc": "UTF-8", "sys_lang": choices.locale},
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
    creds = {"users": [{"username": choices.username, "enc_password": secrets.password_hash, "sudo": True}]}
    if choices.encryption:
        config["disk_encryption"] = {"encryption_type": "luks", "partitions": [root_id]}
        creds["!encryption-password"] = secrets.luks_passphrase
    return ArchinstallPayload(config, creds)
```

Add the credential helpers and argv builder exactly at the end of the adapter:

```python
def hash_secrets(plain, runner) -> HashedSecrets:
    result = runner.run(("openssl", "passwd", "-6", "-stdin"), input_text=plain.login_password + "\n")
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


def write_secure_payload(payload: ArchinstallPayload, config: Path, creds: Path) -> None:
    _write_private_json(config, payload.config)
    _write_private_json(creds, payload.creds)


def archinstall_argv(config: Path, creds: Path, *, dry_run: bool) -> tuple[str, ...]:
    args = ["archinstall", "--config", str(config), "--creds", str(creds)]
    args.append("--dry-run" if dry_run else "--silent")
    return tuple(args)
```

- [ ] **Step 4: Run adapter tests, including file-mode and password-stdin cases**

Append these tests to `tests/test_archinstall_adapter.py`:

```python
import stat
import pytest

from arch_hypr.archinstall_adapter import hash_secrets, write_secure_payload
from arch_hypr.commands import CommandResult
from arch_hypr.domain import PlainSecrets


class RecordingRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, argv, *, input_text=None):
        self.calls.append((tuple(argv), input_text))
        return self.result


def test_password_is_passed_on_stdin_not_argv():
    runner = RecordingRunner(CommandResult(0, "$6$hash\n", ""))
    hashed = hash_secrets(PlainSecrets("login-secret", "luks-secret"), runner)
    argv, input_text = runner.calls[0]
    assert "login-secret" not in " ".join(argv)
    assert input_text == "login-secret\n"
    assert hashed.password_hash == "$6$hash"


def test_payload_files_are_owner_only(tmp_path):
    payload = build_payload(choices(True), HashedSecrets("$6$hash", "luks-secret"), profile())
    config_path, creds_path = tmp_path / "config.json", tmp_path / "creds.json"
    write_secure_payload(payload, config_path, creds_path)
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(creds_path.stat().st_mode) == 0o600


def test_hash_failure_raises_without_a_hash():
    runner = RecordingRunner(CommandResult(1, "", "openssl failed"))
    with pytest.raises(RuntimeError, match="hashing failed"):
        hash_secrets(PlainSecrets("login-secret", None), runner)
```

Run: `python -m pytest tests/test_archinstall_adapter.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the archinstall boundary**

```bash
git add src/arch_hypr/archinstall_adapter.py tests/test_archinstall_adapter.py
git commit -m "feat: generate secure archinstall payloads"
```

---

### Task 6: Hyprland Resources and Chroot Payload Staging

**Files:**
- Create: `src/arch_hypr/staging.py`
- Create: `src/arch_hypr/resources/post_install.py`
- Create: `src/arch_hypr/resources/rootfs/etc/greetd/config.toml`
- Create: `src/arch_hypr/resources/rootfs/etc/systemd/zram-generator.conf`
- Create: `src/arch_hypr/resources/home/.config/hypr/hyprland.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/modules/monitors.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/modules/input.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/modules/keybinds.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/modules/appearance.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/modules/autostart.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/modules/rules.lua`
- Create: `src/arch_hypr/resources/home/.config/hypr/local/overrides.lua`
- Create: `tests/conftest.py`
- Create: `tests/test_staging.py`

**Interfaces:**
- Consumes: `InstallChoices`, `ProfilePlan`
- Produces: `stage_payload(destination, choices, profile)` and standalone `post_install.py`

- [ ] **Step 1: Write a failing staging test**

```python
# tests/test_staging.py
import json
from pathlib import Path

from arch_hypr.staging import stage_payload


def test_stage_contains_versioned_plan_and_modular_hypr_config(tmp_path, choices, profile):
    destination = tmp_path / "payload"
    stage_payload(destination, choices, profile)
    plan = json.loads((destination / "profile-plan.json").read_text())
    assert plan["profile_version"] == 1
    assert plan["username"] == choices.username
    assert (destination / "home/.config/hypr/hyprland.lua").is_file()
    assert (destination / "home/.config/hypr/modules/keybinds.lua").is_file()
    assert (destination / "home/.config/hypr/local/overrides.lua").is_file()
    assert (destination / "post_install.py").is_file()
```

Create the shared fixtures explicitly:

```python
# tests/conftest.py
from pathlib import Path
import pytest

from arch_hypr.commands import CommandResult
from arch_hypr.domain import (
    HardwareProfile, InstallChoices, PlainSecrets, ProfilePlan, SoftwareProfile,
)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, *, input_text=None):
        argv = tuple(argv)
        self.calls.append((argv, input_text))
        if argv[0] == "openssl":
            return CommandResult(0, "$6$test-hash\n", "")
        return CommandResult(0, "", "")


@pytest.fixture
def choices():
    return InstallChoices(
        device=Path("/dev/sda"), disk_size_bytes=64 * 1024**3,
        hostname="hyprbox", username="alex", timezone="UTC", keymap="us",
        locale="en_US.UTF-8", hardware=HardwareProfile.AMD,
        software=SoftwareProfile.MINIMAL, encryption=True,
    )


@pytest.fixture
def profile():
    return ProfilePlan(
        version=1, packages=("base", "linux", "hyprland"),
        services=("NetworkManager.service", "greetd.service"), repositories=(),
    )


@pytest.fixture
def plain_secrets():
    return PlainSecrets("login-secret", "luks-secret")


@pytest.fixture
def fake_runner():
    return FakeRunner()
```

- [ ] **Step 2: Run the staging test and confirm it fails**

Run: `python -m pytest tests/test_staging.py -v`

Expected: FAIL because `stage_payload` does not exist.

- [ ] **Step 3: Add exact base configuration resources**

```lua
-- resources/home/.config/hypr/hyprland.lua
require("modules.monitors")
require("modules.input")
require("modules.appearance")
require("modules.autostart")
require("modules.keybinds")
require("modules.rules")
require("local.overrides")
```

```lua
-- modules/monitors.lua
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })
```

```lua
-- modules/input.lua
hl.config({
  input = {
    kb_layout = "us",
    follow_mouse = 1,
    sensitivity = 0,
    touchpad = { natural_scroll = false },
  },
})
```

```lua
-- modules/appearance.lua
hl.config({
  general = { gaps_in = 5, gaps_out = 12, border_size = 2, layout = "dwindle" },
  decoration = { rounding = 10, active_opacity = 1.0, inactive_opacity = 0.96 },
  animations = { enabled = true },
  misc = { disable_hyprland_logo = true, force_default_wallpaper = 0 },
})
```

```lua
-- modules/autostart.lua
hl.on("hyprland.start", function()
  hl.exec_cmd("waybar")
end)
```

```lua
-- modules/keybinds.lua
local main = "SUPER"
hl.bind(main .. " + RETURN", hl.dsp.exec_cmd("kitty"))
hl.bind(main .. " + C", hl.dsp.window.close())
hl.bind(main .. " + G", hl.dsp.exec_cmd("arch-hypr-assistant"))
hl.bind(main .. " + left", hl.dsp.focus({ direction = "left" }))
hl.bind(main .. " + right", hl.dsp.focus({ direction = "right" }))
hl.bind(main .. " + up", hl.dsp.focus({ direction = "up" }))
hl.bind(main .. " + down", hl.dsp.focus({ direction = "down" }))
for i = 1, 10 do
  local key = i % 10
  hl.bind(main .. " + " .. key, hl.dsp.focus({ workspace = i }))
  hl.bind(main .. " + SHIFT + " .. key, hl.dsp.window.move({ workspace = i }))
end
```

Use these exact remaining resource files:

```lua
-- modules/rules.lua
-- Version 1 intentionally defines no global window rules.
```

```lua
-- local/overrides.lua
-- User-owned overrides are loaded last and are never replaced by profile updates.
```

```toml
# rootfs/etc/greetd/config.toml
[terminal]
vt = 1

[default_session]
command = "tuigreet --time --remember --cmd start-hyprland"
user = "greeter"
```

```ini
# rootfs/etc/systemd/zram-generator.conf
[zram0]
zram-size = ram / 2
compression-algorithm = zstd
```

- [ ] **Step 4: Implement staging and the standalone post-install script**

Implement staging with a single package-resource boundary:

```python
# src/arch_hypr/staging.py
from importlib.resources import as_file, files
from pathlib import Path
import json
import os
import shutil


def stage_payload(destination, choices, profile) -> None:
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"payload destination already exists: {destination}")
    destination.mkdir(mode=0o700, parents=True)
    resources = files("arch_hypr").joinpath("resources")
    with as_file(resources) as resource_root:
        shutil.copytree(resource_root / "rootfs", destination / "rootfs")
        shutil.copytree(resource_root / "home", destination / "home")
        shutil.copy2(resource_root / "post_install.py", destination / "post_install.py")
    plan = {
        "profile_version": profile.version,
        "username": choices.username,
        "hardware": choices.hardware.value,
        "software": choices.software.value,
        "experimental": profile.experimental,
        "packages": list(profile.packages),
        "services": list(profile.services),
        "repositories": list(profile.repositories),
    }
    temporary = destination / "profile-plan.json.tmp"
    temporary.write_text(json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination / "profile-plan.json")
```

Use this standalone chroot entry point; it accepts no destination root or service list from CLI:

```python
# src/arch_hypr/resources/post_install.py
from argparse import ArgumentParser
from pathlib import Path
import json
import os
import pwd
import re
import shutil
import subprocess

PAYLOAD = Path("/run/arch-hypr-installer/payload")
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SERVICE = re.compile(r"[A-Za-z0-9@_.-]+")


def copy_tree(source: Path, destination: Path) -> None:
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise RuntimeError(f"source symlink is not allowed: {item}")
        target = destination / item.relative_to(source)
        if target.is_symlink():
            raise RuntimeError(f"destination symlink is not allowed: {target}")
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target, follow_symlinks=False)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)
    for item in path.rglob("*"):
        if item.is_symlink():
            raise RuntimeError(f"home symlink is not allowed: {item}")
        os.chown(item, uid, gid, follow_symlinks=False)


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--username", required=True)
    username = parser.parse_args().username
    if not USERNAME.fullmatch(username):
        raise RuntimeError("invalid username")
    plan = json.loads((PAYLOAD / "profile-plan.json").read_text(encoding="utf-8"))
    if plan["username"] != username or plan["profile_version"] != 1:
        raise RuntimeError("profile plan identity mismatch")
    services = plan["services"]
    if not isinstance(services, list) or not all(isinstance(x, str) and SERVICE.fullmatch(x) for x in services):
        raise RuntimeError("invalid service list")
    copy_tree(PAYLOAD / "rootfs", Path("/"))
    account = pwd.getpwnam(username)
    home = Path(account.pw_dir)
    copy_tree(PAYLOAD / "home", home)
    chown_tree(home / ".config", account.pw_uid, account.pw_gid)
    if services:
        subprocess.run(["systemctl", "enable", *services], check=True, shell=False)
    version = Path("/etc/arch-hypr/profile-version")
    version.parent.mkdir(parents=True, exist_ok=True)
    version.write_text("1\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Test and commit staging**

Run: `python -m pytest tests/test_staging.py -v`

Expected: all tests PASS, including rejection of a username mismatch and a symlink destination fixture.

```bash
git add src/arch_hypr/staging.py src/arch_hypr/resources tests/conftest.py tests/test_staging.py
git commit -m "feat: stage the base Hyprland system profile"
```

---

### Task 7: Console UI and Install Orchestrator

**Files:**
- Create: `src/arch_hypr/ui.py`
- Create: `src/arch_hypr/orchestrator.py`
- Create: `src/arch_hypr/cli.py`
- Create: `tests/fixtures/answers-amd-encrypted.json`
- Create: `tests/fixtures/answers-intel-plain.json`
- Create: `tests/test_orchestrator.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: all Phase 1 interfaces from Tasks 1–6
- Produces: `InstallMode`, `InstallerOrchestrator.prepare()`, `InstallerOrchestrator.execute()`, `collect_interactive()`, `load_answers()`, and console entry point `main()`

- [ ] **Step 1: Write failing safety-state tests**

```python
# tests/test_orchestrator.py
from pathlib import Path
import pytest

from arch_hypr.orchestrator import InstallMode, InstallerOrchestrator


def test_dry_run_never_invokes_archinstall(fake_runner, choices, plain_secrets, tmp_path):
    app = InstallerOrchestrator(fake_runner, runtime_dir=tmp_path)
    result = app.execute(choices, plain_secrets, InstallMode.DRY_RUN, confirmation=None)
    assert result.config_path.is_file()
    assert all(call[0][0] != "archinstall" for call in fake_runner.calls)


def test_install_requires_exact_confirmation(fake_runner, choices, plain_secrets, tmp_path):
    app = InstallerOrchestrator(fake_runner, runtime_dir=tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        app.execute(choices, plain_secrets, InstallMode.INSTALL, confirmation="nvme0n1")


def test_upstream_validation_uses_dry_run(fake_runner, choices, plain_secrets, tmp_path):
    app = InstallerOrchestrator(fake_runner, runtime_dir=tmp_path)
    app.execute(choices, plain_secrets, InstallMode.VALIDATE_UPSTREAM, confirmation=None)
    call = next(argv for argv, _ in fake_runner.calls if argv[0] == "archinstall")
    assert call[-1] == "--dry-run"
```

The fake runner records `(tuple(argv), input_text)` and returns an OpenSSL hash for `openssl` plus success for `archinstall`.

- [ ] **Step 2: Run orchestrator tests and confirm they fail**

Run: `python -m pytest tests/test_orchestrator.py tests/test_cli.py -v`

Expected: FAIL because UI, orchestrator, and CLI modules do not exist.

- [ ] **Step 3: Implement the state machine**

```python
# public structure of src/arch_hypr/orchestrator.py
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .archinstall_adapter import archinstall_argv, build_payload, hash_secrets, write_secure_payload
from .disks import require_exact_confirmation
from .profiles import compose_profile
from .staging import stage_payload


class InstallMode(StrEnum):
    DRY_RUN = "dry-run"
    VALIDATE_UPSTREAM = "validate-upstream"
    INSTALL = "install"


@dataclass(frozen=True)
class PreparedInstall:
    config_path: Path
    creds_path: Path
    payload_dir: Path
    experimental: bool


class InstallerOrchestrator:
    def __init__(self, runner, runtime_dir=Path("/run/arch-hypr-installer")):
        self.runner = runner
        self.runtime_dir = Path(runtime_dir)

    def prepare(self, choices, plain_secrets) -> PreparedInstall:
        profile = compose_profile(choices.hardware, choices.software)
        hashed = hash_secrets(plain_secrets, self.runner)
        payload = build_payload(choices, hashed, profile)
        self.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload_dir = self.runtime_dir / "payload"
        stage_payload(payload_dir, choices, profile)
        config_path = self.runtime_dir / "config.json"
        creds_path = self.runtime_dir / "creds.json"
        write_secure_payload(payload, config_path, creds_path)
        return PreparedInstall(config_path, creds_path, payload_dir, profile.experimental)

    def execute(self, choices, plain_secrets, mode: InstallMode, confirmation: str | None):
        if mode is InstallMode.INSTALL:
            require_exact_confirmation(choices.device, confirmation or "")
        prepared = self.prepare(choices, plain_secrets)
        if mode is not InstallMode.DRY_RUN:
            result = self.runner.run(archinstall_argv(
                prepared.config_path, prepared.creds_path,
                dry_run=mode is InstallMode.VALIDATE_UPSTREAM,
            ))
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "archinstall failed")
        return prepared
```

`prepare()` must write progress states without serializing the secrets object. Do not catch exceptions inside `prepare()`; `main()` maps known failures to exit codes and a concise message.

- [ ] **Step 4: Implement interactive and fixture-driven input**

Implement the input boundary with an explicit answer schema and injected input functions:

```python
# src/arch_hypr/ui.py
from dataclasses import replace
from getpass import getpass as system_getpass
from pathlib import Path
import json
import sys

from .domain import HardwareProfile, InstallChoices, PlainSecrets, SoftwareProfile

ANSWER_KEYS = frozenset({
    "device", "disk_size_bytes", "hostname", "username", "timezone",
    "keymap", "locale", "hardware", "software", "encryption",
})


def load_answers(path: Path) -> InstallChoices:
    data = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - ANSWER_KEYS
    missing = ANSWER_KEYS - set(data)
    if unknown or missing:
        raise ValueError(f"invalid answer keys; unknown={sorted(unknown)}, missing={sorted(missing)}")
    return InstallChoices(
        device=Path(data["device"]), disk_size_bytes=int(data["disk_size_bytes"]),
        hostname=data["hostname"], username=data["username"], timezone=data["timezone"],
        keymap=data["keymap"], locale=data["locale"],
        hardware=HardwareProfile(data["hardware"]), software=SoftwareProfile(data["software"]),
        encryption=bool(data["encryption"]),
    )


def collect_interactive(disks, *, input_fn=input, getpass_fn=system_getpass):
    for index, disk in enumerate(disks, start=1):
        print(f"{index}. {disk.path} | {disk.model} | {disk.size_bytes // 1024**3} GiB")
    selected = disks[int(input_fn("Target disk number: ")) - 1]
    hostname = input_fn("Hostname [hyprbox]: ").strip() or "hyprbox"
    username = input_fn("Username: ").strip()
    timezone = input_fn("Timezone [UTC]: ").strip() or "UTC"
    keymap = input_fn("Keymap [us]: ").strip() or "us"
    locale = input_fn("Locale [en_US.UTF-8]: ").strip() or "en_US.UTF-8"
    hardware = HardwareProfile(input_fn("Hardware [intel/amd/nvidia]: ").strip())
    software = SoftwareProfile(input_fn("Profile [minimal/developer/gaming]: ").strip())
    encryption = (input_fn("Enable LUKS2 encryption? [Y/n]: ").strip().lower() or "y") in {"y", "yes"}
    if hardware is HardwareProfile.NVIDIA:
        print("EXPERIMENTAL — boot may require manual recovery")
    login_password = getpass_fn("Login password: ")
    login_repeat = getpass_fn("Repeat login password: ")
    if login_password != login_repeat or not login_password:
        raise ValueError("login passwords do not match")
    luks = getpass_fn("LUKS passphrase: ") if encryption else None
    if encryption and not luks:
        raise ValueError("LUKS passphrase is required")
    choices = InstallChoices(
        selected.path, selected.size_bytes, hostname, username, timezone, keymap,
        locale, hardware, software, encryption,
    )
    return choices, PlainSecrets(login_password, luks)


def reconcile_answer_disk(choices: InstallChoices, eligible) -> InstallChoices:
    matches = [disk for disk in eligible if disk.path == choices.device]
    if len(matches) != 1:
        raise ValueError("answer device is not an eligible physical disk")
    return replace(choices, disk_size_bytes=matches[0].size_bytes)


def read_destructive_confirmation(device: Path, *, input_fn=input) -> str:
    if not sys.stdin.isatty():
        raise ValueError("interactive TTY is required for installation")
    print(f"ALL DATA ON {device} WILL BE ERASED")
    return input_fn(f"Type {device} to continue: ")
```

`load_answers(path)` accepts only this exact shape:

```json
{
  "device": "/dev/sda",
  "disk_size_bytes": 68719476736,
  "hostname": "hyprbox",
  "username": "alex",
  "timezone": "UTC",
  "keymap": "us",
  "locale": "en_US.UTF-8",
  "hardware": "amd",
  "software": "minimal",
  "encryption": true
}
```

Passwords are never accepted in the answers file. Even with `--answers`, obtain secrets through `getpass`; `--install` always obtains device confirmation interactively.

The CLI exposes exactly one mode:

```text
arch-hypr-installer [--answers PATH] --dry-run --output-dir PATH
arch-hypr-installer [--answers PATH] --validate-upstream
arch-hypr-installer [--answers PATH] --install
```

Reject `--output-dir` outside dry-run mode. Dry-run output contains `config.json`, a redacted `creds.example.json`, `profile-plan.json`, and staged non-secret resources; it never writes the real credentials file to the user-selected output directory.

Build the CLI parser exactly around a required mutually exclusive mode:

```python
# parser portion of src/arch_hypr/cli.py
from argparse import ArgumentParser
from pathlib import Path


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="arch-hypr-installer")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--output-dir", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--validate-upstream", action="store_true")
    modes.add_argument("--install", action="store_true")
    return parser
```

In `main()`, apply this order: parse arguments; reject `--output-dir` outside dry-run; run preflight for upstream validation/install; discover live media and eligible disks; load answers and reconcile their device against discovery or collect interactively; obtain passwords with `getpass`; print the final plan; obtain a TTY-only exact confirmation for install; call the orchestrator; redact known secret values from any displayed exception; return `0` on success and `2` on validation failure. For dry-run, create a private temporary runtime directory, call the orchestrator there, copy only `config.json`, staged resources, and `{"users": [{"username": "<redacted>", "enc_password": "<redacted>", "sudo": true}]}` to `--output-dir`, then delete the temporary directory.

- [ ] **Step 5: Add and run safety-focused CLI tests**

Add these direct boundary tests to `tests/test_cli.py`:

```python
import json
from pathlib import Path
import pytest

from arch_hypr.cli import build_parser
from arch_hypr.ui import load_answers, read_destructive_confirmation


def valid_answers():
    return {
        "device": "/dev/sda", "disk_size_bytes": 64 * 1024**3,
        "hostname": "hyprbox", "username": "alex", "timezone": "UTC",
        "keymap": "us", "locale": "en_US.UTF-8", "hardware": "amd",
        "software": "minimal", "encryption": True,
    }


def test_modes_are_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--dry-run", "--install", "--output-dir", "out"])


def test_answer_file_rejects_passwords(tmp_path):
    path = tmp_path / "answers.json"
    data = valid_answers() | {"login_password": "secret"}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="login_password"):
        load_answers(path)


def test_install_confirmation_requires_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ValueError, match="TTY"):
        read_destructive_confirmation(Path("/dev/sda"))
```

Add orchestrator-level tests with a fake preflight returning `("UEFI boot is required",)` and assert `prepare()` is not called and no `config.json` or `creds.json` exists. Add a parameterized error-redaction test whose exception contains each of `login-secret` and `luks-secret`; captured CLI output must contain `<redacted>` and neither secret.

Run: `python -m pytest tests/test_orchestrator.py tests/test_cli.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the executable installer flow**

```bash
git add src/arch_hypr/ui.py src/arch_hypr/orchestrator.py src/arch_hypr/cli.py tests/fixtures/answers-*.json tests/test_orchestrator.py tests/test_cli.py
git commit -m "feat: add confirmed installer execution flow"
```

---

### Task 8: Arch ISO Smoke Validation and Operator Documentation

**Files:**
- Create: `scripts/archiso-smoke.sh`
- Create: `README.md`
- Modify: `docs/superpowers/plans/2026-09-12-arch-hyprland-gpt-roadmap.md`

**Interfaces:**
- Consumes: console entry point and the two fixture answer files
- Produces: repeatable upstream validation procedure and Phase 1 exit evidence

- [ ] **Step 1: Add the smoke script test contract**

Create a test in `tests/test_cli.py` that reads `scripts/archiso-smoke.sh` and asserts it contains both fixture names, both `--validate-upstream` invocations, `set -euo pipefail`, and no `--install` invocation.

Run: `python -m pytest tests/test_cli.py -v`

Expected: FAIL because the script does not exist.

- [ ] **Step 2: Create the non-destructive Arch ISO smoke script**

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="$(mktemp -d /tmp/arch-hypr-smoke.XXXXXX)"
trap 'rm -rf -- "$venv_dir"' EXIT

python -m venv "$venv_dir/venv"
"$venv_dir/venv/bin/pip" install --no-deps "$repo_root"

for fixture in answers-amd-encrypted.json answers-intel-plain.json; do
  "$venv_dir/venv/bin/arch-hypr-installer" \
    --answers "$repo_root/tests/fixtures/$fixture" \
    --validate-upstream
done
```

The script deliberately excludes `--install`; destructive VM installation is a separate manual gate in this phase and becomes automated in Phase 4.

- [ ] **Step 3: Write operator documentation**

`README.md` must include:

- the exact supported/unsupported matrix from Global Constraints;
- verification-first download instructions without `curl | bash`;
- development setup: `python -m venv .venv`, install editable dev dependencies, run pytest;
- safe local dry-run command using `answers-amd-encrypted.json`;
- Arch ISO upstream validation command;
- whole-disk warning immediately above the real install command;
- expected log locations under `/var/log/archinstall/`;
- explicit statements that snapshots are not backups and NVIDIA is experimental;
- links to the approved spec, roadmap, Archinstall docs, Hyprland Lua docs, and official Arch package pages.

- [ ] **Step 4: Run the complete automated suite**

Run: `python -m pytest -v`

Expected: all tests PASS.

Run: `python -m pytest --cov=arch_hypr --cov-report=term-missing`

Expected: no untested branch in device confirmation, preflight rejection, secret redaction, profile composition, or mode selection. Overall coverage is at least 90%.

- [ ] **Step 5: Validate against the official Arch ISO**

Boot the checked-in `archlinux-2026.09.01-x86_64.iso` in a disposable UEFI VM, attach the repository read-only, and run:

```bash
bash scripts/archiso-smoke.sh
```

Expected: both encrypted and plain configurations are accepted by upstream `archinstall --dry-run`; no virtual disk is modified.

Then attach a new empty virtual disk, run the interactive `--install` path, and verify:

```bash
findmnt -no FSTYPE /
findmnt -no OPTIONS /
systemctl is-enabled NetworkManager.service greetd.service
test -f /etc/arch-hypr/profile-version
```

Expected: root is `btrfs`, mount options include `compress=zstd`, both services are enabled, profile version exists, systemd-boot starts, LUKS unlock succeeds, and greetd presents a login that launches Hyprland.

Record the ISO checksum, VM firmware, virtual disk size, selected fixture, test commands, and result in a dated section titled `Phase 1 Evidence` in the roadmap.

- [ ] **Step 6: Commit Phase 1 evidence and documentation**

```bash
git add scripts/archiso-smoke.sh README.md tests/test_cli.py docs/superpowers/plans/2026-09-12-arch-hyprland-gpt-roadmap.md
git commit -m "test: validate installer against Arch ISO"
```

---

## Phase 1 Completion Gate

Before starting the assistant-core plan, verify all items:

- [ ] `python -m pytest -v` passes.
- [ ] Safety-critical branches have coverage and overall coverage is at least 90%.
- [ ] Both fixture configurations pass upstream `archinstall --dry-run` in the official ISO.
- [ ] A disposable UEFI VM completes one encrypted full-disk installation.
- [ ] The installed Btrfs subvolumes and compression options match the specification.
- [ ] systemd-boot unlocks LUKS and reaches greetd.
- [ ] greetd launches the modular Hyprland Lua configuration.
- [ ] Re-running the profile staging/apply operation does not duplicate files, services, or package entries.
- [ ] The ISO file, real credentials, generated config/creds, and smoke artifacts remain untracked by Git.
- [ ] Phase 1 evidence is recorded in the roadmap.

## Primary References

- Archinstall guided config, creds, and dry-run: <https://archinstall.archlinux.page/installing/guided.html>
- Archinstall disk and Btrfs schema: <https://archinstall.archlinux.page/cli_parameters/config/disk_config.html>
- Hyprland Lua configuration entry point: <https://wiki.hypr.land/Configuring/Start/>
- Hyprland monitor syntax: <https://wiki.hypr.land/configuring/core/monitors/>
- Hyprland binds and dispatchers: <https://wiki.hypr.land/configuring/core/binds/> and <https://wiki.hypr.land/configuring/core/dispatchers/>
- Official Hyprland package: <https://archlinux.org/packages/extra/x86_64/hyprland/>
- Official greetd-tuigreet package: <https://archlinux.org/packages/extra/x86_64/greetd-tuigreet/>
