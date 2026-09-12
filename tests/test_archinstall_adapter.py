from pathlib import Path
import os
import stat

import pytest

from arch_hypr.archinstall_adapter import (
    archinstall_argv,
    build_payload,
    hash_secrets,
    write_secure_payload,
)
from arch_hypr.commands import CommandResult
from arch_hypr.domain import (
    HardwareProfile,
    HashedSecrets,
    InstallChoices,
    PlainSecrets,
    ProfilePlan,
    SoftwareProfile,
)


def choices(encryption=True):
    return InstallChoices(
        Path("/dev/sda"),
        64 * 1024**3,
        "hyprbox",
        "alex",
        "UTC",
        "us",
        "en_US.UTF-8",
        HardwareProfile.AMD,
        SoftwareProfile.MINIMAL,
        encryption,
    )


def profile():
    return ProfilePlan(
        1,
        ("base", "linux", "hyprland"),
        ("NetworkManager.service",),
        (),
    )


def test_encrypted_payload_has_expected_layout_and_separate_secret():
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    partitions = payload.config["disk_config"]["device_modifications"][0][
        "partitions"
    ]
    assert partitions[0]["mountpoint"] == "/boot"
    assert {item["name"] for item in partitions[1]["btrfs"]} == {
        "@",
        "@home",
        "@snapshots",
        "@var_log",
    }
    assert payload.config["disk_encryption"]["partitions"] == [
        partitions[1]["obj_id"]
    ]
    assert payload.creds["!encryption-password"] == "luks-secret"
    assert "luks-secret" not in repr(payload)


def test_plain_payload_omits_disk_encryption_and_luks_secret():
    payload = build_payload(choices(False), HashedSecrets("$6$hash", None), profile())
    assert "disk_encryption" not in payload.config
    assert "!encryption-password" not in payload.creds


def test_archinstall_command_is_an_argument_vector():
    assert archinstall_argv(
        Path("/run/i/config.json"), Path("/run/i/creds.json"), dry_run=True
    ) == (
        "archinstall",
        "--config",
        "/run/i/config.json",
        "--creds",
        "/run/i/creds.json",
        "--dry-run",
    )


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are unavailable")
def test_payload_files_are_owner_only(tmp_path):
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    config_path, creds_path = tmp_path / "config.json", tmp_path / "creds.json"
    write_secure_payload(payload, config_path, creds_path)
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(creds_path.stat().st_mode) == 0o600


def test_hash_failure_raises_without_a_hash():
    runner = RecordingRunner(CommandResult(1, "", "openssl failed"))
    with pytest.raises(RuntimeError, match="hashing failed"):
        hash_secrets(PlainSecrets("login-secret", None), runner)


def test_existing_destination_is_rejected_without_partial_write(tmp_path):
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    config_path, creds_path = tmp_path / "config.json", tmp_path / "creds.json"
    creds_path.write_text("sentinel", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_secure_payload(payload, config_path, creds_path)

    assert creds_path.read_text(encoding="utf-8") == "sentinel"
    assert not config_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are unavailable")
def test_broad_existing_credentials_file_is_not_overwritten(tmp_path):
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    config_path, creds_path = tmp_path / "config.json", tmp_path / "creds.json"
    creds_path.write_text("sentinel", encoding="utf-8")
    creds_path.chmod(0o644)

    with pytest.raises(FileExistsError):
        write_secure_payload(payload, config_path, creds_path)

    assert creds_path.read_text(encoding="utf-8") == "sentinel"
    assert stat.S_IMODE(creds_path.stat().st_mode) == 0o644
    assert not config_path.exists()


def test_symlink_destination_is_rejected_without_following(tmp_path):
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    config_path = tmp_path / "config.json"
    target_path, creds_path = tmp_path / "target.json", tmp_path / "creds.json"
    target_path.write_text("sentinel", encoding="utf-8")
    try:
        creds_path.symlink_to(target_path)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this host")

    with pytest.raises(FileExistsError):
        write_secure_payload(payload, config_path, creds_path)

    assert target_path.read_text(encoding="utf-8") == "sentinel"
    assert not config_path.exists()


def test_identical_config_and_credentials_paths_are_rejected_before_write(tmp_path):
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    shared_path = tmp_path / "payload.json"

    with pytest.raises(ValueError, match="distinct"):
        write_secure_payload(payload, shared_path, shared_path)

    assert not shared_path.exists()


def test_equivalent_config_and_credentials_paths_are_rejected_before_write(tmp_path):
    payload = build_payload(
        choices(True), HashedSecrets("$6$hash", "luks-secret"), profile()
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    config_path = tmp_path / "payload.json"
    equivalent_creds_path = nested / ".." / "payload.json"

    with pytest.raises(ValueError, match="distinct"):
        write_secure_payload(payload, config_path, equivalent_creds_path)

    assert not config_path.exists()
