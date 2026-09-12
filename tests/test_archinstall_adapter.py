from pathlib import Path
from dataclasses import replace
import json
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
    assert payload.creds["encryption_password"] == "luks-secret"
    assert "luks-secret" not in repr(payload)


def test_plain_payload_omits_disk_encryption_and_luks_secret():
    payload = build_payload(choices(False), HashedSecrets("$6$hash", None), profile())
    assert "disk_encryption" not in payload.config
    assert "encryption_password" not in payload.creds


def test_archinstall_command_is_an_argument_vector():
    assert archinstall_argv(
        Path("/run/i/config.json"), Path("/run/i/creds.json"), dry_run=True
    ) == (
        "archinstall",
        "--config",
        "/run/i/config.json",
        "--creds",
        "/run/i/creds.json",
        "--silent",
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


@pytest.mark.parametrize("encrypted", [True, False])
def test_partition_shape_consumed_by_archinstall_44_parser(encrypted):
    # archlinux/archinstall tag 4.4, lib/models/device.py:
    # DiskLayoutConfiguration.parse_arg uses size and dev_path (not length).
    payload = build_payload(choices(encrypted), HashedSecrets("$6$hash", "luks-secret" if encrypted else None), profile())
    disk = payload.config["disk_config"]
    assert disk["config_type"] == "manual_partitioning"
    device, = disk["device_modifications"]
    assert device["device"] == "/dev/sda" and device["wipe"] is True
    esp, root = device["partitions"]
    keys = {"btrfs", "flags", "fs_type", "mount_options", "mountpoint", "obj_id", "start", "size", "status", "type", "dev_path"}
    for part in (esp, root):
        assert set(part) == keys
        assert part["dev_path"] is None
        assert part["status"] == "create" and part["type"] == "primary"
        for size in (part["start"], part["size"]):
            assert size["unit"] == "B" and size["sector_size"] == {"unit": "B", "value": 512}
    assert esp["start"]["value"] == 1048576
    assert esp["size"]["value"] == 1073741824
    assert root["start"]["value"] == 1074790400
    assert root["size"]["value"] == 67643637760
    assert esp["flags"] == ["boot"] and esp["fs_type"] == "fat32"
    assert root["fs_type"] == "btrfs" and root["mount_options"] == ["compress=zstd"]
    assert root["btrfs"] == [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}, {"name": "@snapshots", "mountpoint": "/.snapshots"}, {"name": "@var_log", "mountpoint": "/var/log"}]
    assert payload.config["bootloader_config"] == {"bootloader": "Systemd-boot", "uki": False, "removable": False}
    assert payload.config["swap"] is False
    assert payload.creds["users"] == [{"username": "alex", "enc_password": "$6$hash", "sudo": True}]
    assert set(payload.creds) == ({"users", "encryption_password"} if encrypted else {"users"})
    if encrypted:
        assert payload.config["disk_encryption"] == {"encryption_type": "luks", "partitions": [root["obj_id"]]}
    for secret in ("luks-secret", "$6$hash"):
        assert secret not in json.dumps(payload.config) + repr(payload)


@pytest.mark.parametrize("repositories", [(), ("multilib",)])
def test_repository_configuration_survives_44_normalization(repositories):
    # args.py enters MirrorConfiguration.parse_args only for a truthy mirror_config.
    payload = build_payload(choices(), HashedSecrets("$6$hash", "luks"), replace(profile(), repositories=repositories))
    mirror = payload.config.get("mirror_config")
    normalized = []
    if mirror:
        assert mirror == {"mirror_regions": {}, "custom_servers": [], "optional_repositories": list(repositories), "custom_repositories": []}
        normalized = mirror.get("optional_repositories", [])
    assert normalized == list(repositories)
    assert "additional-repositories" not in payload.config


def test_real_install_argv_is_explicitly_silent():
    assert archinstall_argv(Path("/run/i/config.json"), Path("/run/i/creds.json"), dry_run=False) == (
        "archinstall", "--config", "/run/i/config.json", "--creds", "/run/i/creds.json", "--silent",
    )


@pytest.mark.parametrize("encrypted,luks", [(False, ""), (False, "luks"), (True, None), (True, "")])
def test_encryption_secret_mismatch_is_rejected(encrypted, luks):
    with pytest.raises(ValueError, match="LUKS"):
        build_payload(choices(encrypted), HashedSecrets("$6$hash", luks), profile())


def test_success_with_empty_hash_is_still_failure():
    runner = RecordingRunner(CommandResult(0, " \n", "UNTRUSTED login-secret"))
    with pytest.raises(RuntimeError, match="^password hashing failed$"):
        hash_secrets(PlainSecrets("login-secret"), runner)
    assert runner.calls == [(("openssl", "passwd", "-6", "-stdin"), "login-secret\n")]


@pytest.mark.parametrize("version,status,accepted", [("archinstall 4.4\n", 0, True), ("archinstall 4.3\n", 0, False), ("archinstall 4.4.1\n", 0, False), ("UNTRUSTED", 0, False), ("archinstall 4.4\n", 1, False)])
def test_only_supported_upstream_version_is_accepted(version, status, accepted):
    from arch_hypr import archinstall_adapter as adapter
    runner = RecordingRunner(CommandResult(status, version, "UNTRUSTED"))
    if accepted:
        adapter.require_supported_archinstall(runner)
    else:
        with pytest.raises(RuntimeError, match="supported archinstall 4.4") as caught:
            adapter.require_supported_archinstall(runner)
        assert "UNTRUSTED" not in str(caught.value)
    assert runner.calls == [(("archinstall", "--version"), None)]


@pytest.mark.parametrize("failing_part", ["config", "creds"])
def test_partial_json_write_failure_removes_only_owned_pair(failing_part, tmp_path):
    from arch_hypr.archinstall_adapter import ArchinstallPayload
    payload = ArchinstallPayload({"ok": True}, {"users": []})
    getattr(payload, failing_part)["bad"] = object()
    config, creds = tmp_path / "config.json", tmp_path / "creds.json"
    with pytest.raises(TypeError):
        write_secure_payload(payload, config, creds)
    assert not config.exists() and not creds.exists()


def test_second_exclusive_create_failure_cleans_config_but_preserves_foreign_creds(tmp_path, monkeypatch):
    import arch_hypr.archinstall_adapter as adapter
    config, creds = tmp_path / "config.json", tmp_path / "creds.json"
    real_open = adapter.os.open
    def race(path, flags, mode):
        if path == creds:
            creds.write_text("foreign sentinel")
        return real_open(path, flags, mode)
    monkeypatch.setattr(adapter.os, "open", race)
    payload = build_payload(choices(), HashedSecrets("$6$hash", "luks"), profile())
    with pytest.raises(FileExistsError):
        write_secure_payload(payload, config, creds)
    assert not config.exists() and creds.read_text() == "foreign sentinel"


def test_non_mib_disk_size_produces_aligned_root_inside_gpt_boundary():
    selected = replace(choices(), disk_size_bytes=128035676160)
    payload = build_payload(selected, HashedSecrets("$6$hash", "luks"), profile())
    root = payload.config["disk_config"]["device_modifications"][0]["partitions"][1]
    assert root["size"]["value"] == 126959484928
    assert root["start"]["value"] + root["size"]["value"] <= 128034627584


def test_version_probe_error_is_secret_safe():
    from arch_hypr.archinstall_adapter import require_supported_archinstall
    class FailedRunner:
        def run(self, argv, *, input_text=None):
            raise OSError("UNTRUSTED secret")
    with pytest.raises(RuntimeError, match="^supported archinstall 4.4 is required$"):
        require_supported_archinstall(FailedRunner())


def test_partial_write_cleanup_failure_is_safe_and_attempts_both_paths(tmp_path, monkeypatch):
    from arch_hypr.archinstall_adapter import ArchinstallPayload
    config, creds = tmp_path / "config.json", tmp_path / "creds.json"
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path == creds:
            raise PermissionError("UNTRUSTED secret")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(RuntimeError, match="^payload cleanup failed; private files may remain$"):
        write_secure_payload(ArchinstallPayload({}, {"bad": object()}), config, creds)
    assert not config.exists()
