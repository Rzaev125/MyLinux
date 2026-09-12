from pathlib import Path

import pytest

from arch_hypr.commands import CommandResult
from arch_hypr.domain import (
    HardwareProfile,
    InstallChoices,
    PlainSecrets,
    ProfilePlan,
    SoftwareProfile,
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
        device=Path("/dev/sda"),
        disk_size_bytes=64 * 1024**3,
        hostname="hyprbox",
        username="alex",
        timezone="UTC",
        keymap="us",
        locale="en_US.UTF-8",
        hardware=HardwareProfile.AMD,
        software=SoftwareProfile.MINIMAL,
        encryption=True,
    )


@pytest.fixture
def profile():
    return ProfilePlan(
        version=1,
        packages=("base", "linux", "hyprland"),
        services=("NetworkManager.service", "greetd.service"),
        repositories=(),
    )


@pytest.fixture
def plain_secrets():
    return PlainSecrets("login-secret", "luks-secret")


@pytest.fixture
def fake_runner():
    return FakeRunner()


@pytest.fixture(autouse=True)
def target_root(tmp_path_factory, monkeypatch):
    # No test may write to the host /mnt; only the external argv keeps that literal.
    root = tmp_path_factory.mktemp("installer-target")
    monkeypatch.setattr("arch_hypr.staging.TARGET_ROOT", root, raising=False)
    return root
