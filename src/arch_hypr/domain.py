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
        if not self.device.as_posix().startswith("/dev/"):
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
