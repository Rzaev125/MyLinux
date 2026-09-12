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
