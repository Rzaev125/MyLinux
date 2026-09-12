"""Catch unsafe execution branches, stale targets, and leaked credentials."""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from arch_hypr.commands import CommandResult
from arch_hypr.domain import HardwareProfile
from arch_hypr.orchestrator import InstallMode, InstallerOrchestrator


class InstallRunner:
    """External process boundary; all profile/IO code remains real."""
    def __init__(self, *, size=68719476736, upstream_error=None):
        self.calls = []
        self.size = size
        self.upstream_error = upstream_error
        self.on_install = None

    def run(self, argv, *, input_text=None):
        argv = tuple(argv)
        self.calls.append((argv, input_text))
        if argv[0] == "findmnt":
            return CommandResult(0, "/dev/sdb1\n", "")
        if argv[0] == "lsblk":
            return CommandResult(0, json.dumps({"blockdevices": [
                {"name": "sda", "path": "/dev/sda", "type": "disk", "size": self.size,
                 "model": "Target", "ro": False, "rm": False, "mountpoints": [None], "pkname": None},
                {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 16000000000,
                 "model": "Live", "ro": False, "rm": True, "mountpoints": [None], "pkname": None,
                 "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part",
                               "size": 15000000000, "model": None, "ro": False, "rm": True,
                               "mountpoints": ["/run/archiso/bootmnt"], "pkname": "sdb"}]}
            ]}), "")
        if argv == ("openssl", "passwd", "-6", "-stdin"):
            return CommandResult(0, "$6$test-hash\n", "")
        if argv[0] == "archinstall":
            config = json.loads(Path(argv[2]).read_text())
            creds = json.loads(Path(argv[4]).read_text())
            assert config["disk_config"]["device_modifications"][0]["device"] == "/dev/sda"
            assert creds["users"][0]["enc_password"] == "$6$test-hash"
            if self.on_install:
                self.on_install()
            return CommandResult(1 if self.upstream_error else 0, "", self.upstream_error or "")
        raise AssertionError(f"unexpected external command: {argv}")


def app_at(tmp_path, runner=None, preflight=lambda: ()):
    return InstallerOrchestrator(runner or InstallRunner(), runtime_dir=tmp_path, preflight=preflight)


def test_prepare_stages_separate_credentials_and_progress(choices, plain_secrets, tmp_path, capsys):
    app = app_at(tmp_path)
    result = app.prepare(choices, plain_secrets)
    assert json.loads(result.creds_path.read_text())["!encryption-password"] == "luks-secret"
    assert (result.payload_dir / "post_install.py").is_file()
    assert (result.payload_dir / "profile-plan.json").is_file()
    assert {call[0][0] for call in app.runner.calls} == {"openssl"}
    text = capsys.readouterr().out + result.config_path.read_text()
    assert "prepar" in text.lower()
    assert "login-secret" not in text and "luks-secret" not in text


@pytest.mark.parametrize("mode,tail", [(InstallMode.DRY_RUN, None),
    (InstallMode.VALIDATE_UPSTREAM, "--dry-run"), (InstallMode.INSTALL, "--silent")])
def test_modes_limit_effects_and_remove_credentials(mode, tail, choices, plain_secrets, tmp_path):
    app = app_at(tmp_path)
    result = app.execute(choices, plain_secrets, mode, confirmation="/dev/sda" if tail == "--silent" else None)
    assert result.config_path.is_file()
    assert not result.creds_path.exists()
    commands = [argv for argv, _ in app.runner.calls]
    upstream = [argv for argv in commands if argv[0] == "archinstall"]
    assert [argv[-1] for argv in upstream] == ([] if tail is None else [tail])
    assert all(argv[0] in {"findmnt", "lsblk", "openssl", "archinstall"} for argv in commands)
    assert all("login-secret" not in str(argv) and "luks-secret" not in str(argv) for argv in commands)


@pytest.mark.parametrize("confirmation", [None, "sda", "/dev/sdb", " /dev/sda", "/dev/sda\n"])
def test_exact_confirmation_precedes_preparation(confirmation, choices, plain_secrets, tmp_path):
    app = app_at(tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        app.execute(choices, plain_secrets, InstallMode.INSTALL, confirmation)
    assert not list(tmp_path.iterdir())
    assert not app.runner.calls


@pytest.mark.parametrize("mode", [InstallMode.INSTALL, InstallMode.VALIDATE_UPSTREAM])
def test_preflight_errors_aggregate_before_preparation(mode, choices, plain_secrets, tmp_path):
    app = app_at(tmp_path, preflight=lambda: ("UEFI boot is required", "network access is required"))
    with pytest.raises(ValueError) as caught:
        app.execute(choices, plain_secrets, mode, "/dev/sda")
    assert "UEFI" in str(caught.value) and "network" in str(caught.value)
    assert not list(tmp_path.iterdir())
    assert not app.runner.calls


@pytest.mark.parametrize("device,size", [("/dev/sdb",68719476736), ("/dev/missing",68719476736), ("/dev/sda",34359738368)])
def test_discovery_rejects_live_missing_or_resized_disk(device, size, choices, plain_secrets, tmp_path):
    selected = replace(choices, device=Path(device))
    app = app_at(tmp_path, InstallRunner(size=size))
    with pytest.raises(ValueError):
        app.execute(selected, plain_secrets, InstallMode.INSTALL, device)
    assert not list(tmp_path.iterdir())
    assert not any(argv[0] in {"openssl", "archinstall"} for argv, _ in app.runner.calls)


def test_failed_upstream_removes_real_credentials(choices, plain_secrets, tmp_path):
    app = app_at(tmp_path, InstallRunner(upstream_error="failed"))
    with pytest.raises(RuntimeError):
        app.execute(choices, plain_secrets, InstallMode.INSTALL, "/dev/sda")
    assert not (tmp_path / "creds.json").exists()


def test_existing_runtime_payload_is_preserved(choices, plain_secrets, tmp_path):
    creds = tmp_path / "creds.json"
    creds.write_text("existing user data")
    app = app_at(tmp_path)
    with pytest.raises(FileExistsError):
        app.execute(choices, plain_secrets, InstallMode.DRY_RUN, None)
    assert creds.read_text() == "existing user data"


def test_experimental_nvidia_is_recorded(choices, plain_secrets, tmp_path, capsys):
    prepared = app_at(tmp_path).prepare(replace(choices, hardware=HardwareProfile.NVIDIA), plain_secrets)
    assert prepared.experimental
    assert json.loads((prepared.payload_dir / "profile-plan.json").read_text())["experimental"] is True
    assert "EXPERIMENTAL" in capsys.readouterr().out


def test_disk_changed_during_hashing_blocks_upstream(choices, plain_secrets, tmp_path):
    class SwappedRunner(InstallRunner):
        def run(self, argv, *, input_text=None):
            result = super().run(argv, input_text=input_text)
            if argv[0] == "openssl":
                self.size = 34359738368
            return result
    runner = SwappedRunner()
    with pytest.raises(ValueError, match="disk"):
        app_at(tmp_path, runner).execute(choices, plain_secrets, InstallMode.INSTALL, "/dev/sda")
    assert not any(argv[0] == "archinstall" for argv, _ in runner.calls)
    assert not (tmp_path / "creds.json").exists()


def test_foreign_runtime_owner_rejected_before_writes(choices, plain_secrets, tmp_path, monkeypatch):
    # Keep real filesystem metadata; model a process with a different effective UID.
    before = tmp_path.stat()
    monkeypatch.setattr(os, "geteuid", lambda: before.st_uid + 1, raising=False)
    app = app_at(tmp_path)
    with pytest.raises(ValueError, match="owner"):
        app.prepare(choices, plain_secrets)
    assert not app.runner.calls
    assert not list(tmp_path.iterdir())
    assert tmp_path.stat().st_mode == before.st_mode


@pytest.mark.skipif(os.name != "posix", reason="POSIX runtime ownership/mode contract")
def test_current_posix_owner_runtime_remains_private(choices, plain_secrets, tmp_path):
    prepared = app_at(tmp_path).prepare(choices, plain_secrets)
    assert tmp_path.stat().st_uid == os.geteuid()
    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert prepared.creds_path.stat().st_uid == os.geteuid()
    assert prepared.creds_path.stat().st_mode & 0o777 == 0o600
