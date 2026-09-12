import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

import arch_hypr.staging as staging
from arch_hypr.staging import stage_payload


def load_post_install(path: Path):
    spec = importlib.util.spec_from_file_location("staged_post_install", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original_pwd = sys.modules.get("pwd")
    if original_pwd is None:
        sys.modules["pwd"] = ModuleType("pwd")
    try:
        spec.loader.exec_module(module)
    finally:
        if original_pwd is None:
            sys.modules.pop("pwd", None)
    return module


def make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if sys.platform != "win32":
            pytest.skip(f"directory links are unavailable: {error}")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_stage_contains_versioned_plan_and_modular_hypr_config(tmp_path, choices, profile):
    destination = tmp_path / "payload"
    stage_payload(destination, choices, profile)

    plan = json.loads((destination / "profile-plan.json").read_text(encoding="utf-8"))
    assert plan == {
        "experimental": False,
        "hardware": "amd",
        "packages": ["base", "linux", "hyprland"],
        "profile_version": 1,
        "repositories": [],
        "services": ["NetworkManager.service", "greetd.service"],
        "software": "minimal",
        "username": "alex",
    }
    assert (destination / "home/.config/hypr/hyprland.lua").is_file()
    assert (destination / "home/.config/hypr/modules/keybinds.lua").is_file()
    assert (destination / "home/.config/hypr/local/overrides.lua").is_file()
    assert (destination / "rootfs/etc/greetd/config.toml").is_file()
    assert (destination / "rootfs/etc/systemd/zram-generator.conf").is_file()
    assert (destination / "post_install.py").is_file()
    assert load_post_install(destination / "post_install.py").PAYLOAD.as_posix() == "/root/.arch-hypr-installer/payload"


def test_stage_rejects_an_existing_destination(tmp_path, choices, profile):
    destination = tmp_path / "payload"
    destination.mkdir()

    with pytest.raises(FileExistsError, match="payload destination already exists"):
        stage_payload(destination, choices, profile)


def test_post_install_applies_profile_idempotently_and_preserves_user_overrides(
    tmp_path, choices, profile, monkeypatch
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    target.mkdir()
    account = SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234)
    ownership = []
    systemctl_calls = []

    monkeypatch.setattr(
        post_install.os,
        "chown",
        lambda path, uid, gid, follow_symlinks=False: ownership.append(
            (Path(path), uid, gid, follow_symlinks)
        ),
        raising=False,
    )

    def run_systemctl(argv, *, check, shell):
        systemctl_calls.append((argv, check, shell))

    post_install.apply_payload(
        payload=payload,
        root=target,
        username="alex",
        account=account,
        run_command=run_systemctl,
    )

    override = target / "home/alex/.config/hypr/local/overrides.lua"
    keybinds = target / "home/alex/.config/hypr/modules/keybinds.lua"
    assert "SUPER" in keybinds.read_text(encoding="utf-8")
    override.write_text("-- alex owns this\n", encoding="utf-8")
    (payload / "home/.config/hypr/modules/keybinds.lua").write_text(
        "-- profile update\n", encoding="utf-8"
    )

    post_install.apply_payload(
        payload=payload,
        root=target,
        username="alex",
        account=account,
        run_command=run_systemctl,
    )

    assert override.read_text(encoding="utf-8") == "-- alex owns this\n"
    assert keybinds.read_text(encoding="utf-8") == "-- profile update\n"
    assert (target / "etc/greetd/config.toml").is_file()
    assert (target / "etc/systemd/zram-generator.conf").is_file()
    assert (target / "etc/arch-hypr/profile-version").read_text(encoding="utf-8") == "1\n"
    assert systemctl_calls == [
        (
            ["systemctl", "enable", "--", "NetworkManager.service", "greetd.service"],
            True,
            False,
        ),
        (
            ["systemctl", "enable", "--", "NetworkManager.service", "greetd.service"],
            True,
            False,
        ),
    ]
    assert ownership
    assert all(uid == 1234 and gid == 1234 and follow is False for _, uid, gid, follow in ownership)
    assert all(path.is_relative_to(target / "home/alex/.config") for path, *_ in ownership)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"username": "mallory"}, "profile plan identity mismatch"),
        ({"profile_version": True}, "profile plan identity mismatch"),
        ({"profile_version": 1.0}, "profile plan identity mismatch"),
        ({"services": ["greetd.service; reboot"]}, "invalid service list"),
        ({"services": "greetd.service"}, "invalid service list"),
    ],
)
def test_post_install_rejects_invalid_typed_plan(
    tmp_path, choices, profile, change, message, monkeypatch
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    plan_path = payload / "profile-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update(change)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    post_install = load_post_install(payload / "post_install.py")

    (tmp_path / "target").mkdir()
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match=message):
        post_install.apply_payload(
            payload=payload,
            root=tmp_path / "target",
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: None,
        )


def test_post_install_rejects_invalid_username_before_account_lookup(
    tmp_path, choices, profile, monkeypatch
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    post_install = load_post_install(payload / "post_install.py")
    account_lookups = []
    monkeypatch.setattr(
        post_install.pwd,
        "getpwnam",
        lambda username: account_lookups.append(username),
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["post_install.py", "--username", "bad;name"])

    with pytest.raises(RuntimeError, match="invalid username"):
        post_install.main()

    assert account_lookups == []


def test_post_install_rejects_linked_payload_root_before_any_write(
    tmp_path, choices, profile, monkeypatch
):
    real_payload = tmp_path / "real-payload"
    stage_payload(real_payload, choices, profile)
    linked_payload = tmp_path / "linked-payload"
    make_directory_link(linked_payload, real_payload)
    post_install = load_post_install(real_payload / "post_install.py")
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="link is not allowed"):
        post_install.apply_payload(
            payload=linked_payload,
            root=target,
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: None,
        )

    assert list(target.iterdir()) == []


def test_post_install_rejects_linked_payload_home_before_any_write(
    tmp_path, choices, profile, monkeypatch
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    external_home = tmp_path / "external-home"
    (payload / "home").rename(external_home)
    make_directory_link(payload / "home", external_home)
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="link is not allowed"):
        post_install.apply_payload(
            payload=payload,
            root=target,
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: None,
        )

    assert list(target.iterdir()) == []


@pytest.mark.parametrize("linked_component", ["home", "user"])
def test_post_install_rejects_linked_home_ancestor_before_any_write(
    tmp_path, choices, profile, monkeypatch, linked_component
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    if linked_component == "home":
        make_directory_link(target / "home", outside)
    else:
        (target / "home").mkdir()
        make_directory_link(target / "home/alex", outside)
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="link is not allowed"):
        post_install.apply_payload(
            payload=payload,
            root=target,
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: None,
        )

    assert list(outside.iterdir()) == []
    assert not (target / "etc/greetd/config.toml").exists()


def test_post_install_rejects_traversal_in_passwd_home_before_any_write(
    tmp_path, choices, profile, monkeypatch
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    target.mkdir()
    escaped_home = tmp_path / "escaped-home"
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="invalid account home"):
        post_install.apply_payload(
            payload=payload,
            root=target,
            username="alex",
            account=SimpleNamespace(
                pw_dir="/home/alex/../../../escaped-home", pw_uid=1234, pw_gid=1234
            ),
            run_command=lambda *args, **kwargs: None,
        )

    assert not escaped_home.exists()
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("linked_component", ["directory", "file"])
def test_post_install_rejects_linked_profile_version_path_before_any_write(
    tmp_path, choices, profile, monkeypatch, linked_component
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    (target / "etc").mkdir(parents=True)
    outside.mkdir()
    if linked_component == "directory":
        make_directory_link(target / "etc/arch-hypr", outside)
    else:
        (target / "etc/arch-hypr").mkdir()
        make_directory_link(target / "etc/arch-hypr/profile-version", outside)
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="link is not allowed"):
        post_install.apply_payload(
            payload=payload,
            root=target,
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: None,
        )

    assert list(outside.iterdir()) == []
    assert not (target / "etc/greetd/config.toml").exists()


@pytest.mark.parametrize("service", ["--now", "--force", "--global"])
def test_post_install_rejects_systemctl_option_tokens(
    tmp_path, choices, profile, monkeypatch, service
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    plan_path = payload / "profile-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["services"] = [service]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    target.mkdir()
    commands = []
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="invalid service list"):
        post_install.apply_payload(
            payload=payload,
            root=target,
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: commands.append((args, kwargs)),
        )

    assert commands == []
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("linked_source", ["root", "descendant"])
def test_stage_rejects_package_resource_links_before_creating_destination(
    tmp_path, choices, profile, monkeypatch, linked_source
):
    package = tmp_path / "package"
    resources = package / "resources"
    (resources / "home").mkdir(parents=True)
    (resources / "post_install.py").write_text("pass\n", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    (external / "escaped.conf").write_text("external\n", encoding="utf-8")
    if linked_source == "root":
        make_directory_link(resources / "rootfs", external)
    else:
        (resources / "rootfs").mkdir()
        make_directory_link(resources / "rootfs/linked", external)
    monkeypatch.setattr(staging, "files", lambda package_name: package)
    destination = tmp_path / "payload"

    with pytest.raises(RuntimeError, match="resource link is not allowed"):
        stage_payload(destination, choices, profile)

    assert not destination.exists()


def test_post_install_rejects_a_symlink_in_the_destination(
    tmp_path, choices, profile, monkeypatch
):
    payload = tmp_path / "payload"
    stage_payload(payload, choices, profile)
    post_install = load_post_install(payload / "post_install.py")
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    make_directory_link(target / "etc", outside)
    monkeypatch.setattr(post_install.os, "chown", lambda *args, **kwargs: None, raising=False)

    with pytest.raises(RuntimeError, match="link is not allowed"):
        post_install.apply_payload(
            payload=payload,
            root=target,
            username="alex",
            account=SimpleNamespace(pw_dir="/home/alex", pw_uid=1234, pw_gid=1234),
            run_command=lambda *args, **kwargs: None,
        )

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("nested", [False, True])
def test_public_stage_rejects_linked_destination_ancestors(tmp_path, choices, profile, nested):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    make_directory_link(linked, outside)
    destination = linked / "nested/payload" if nested else linked / "payload"
    with pytest.raises(RuntimeError, match="link"):
        stage_payload(destination, choices, profile)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("linked_part", ["root", "home", "runtime", "payload"])
def test_target_transfer_rejects_linked_ancestor_chain_before_writes(linked_part, tmp_path, choices, profile, monkeypatch):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    root = tmp_path / "target"
    outside = tmp_path / "outside"
    outside.mkdir()
    parts = {"root": root, "home": root / "root", "runtime": root / "root/.arch-hypr-installer", "payload": root / "root/.arch-hypr-installer/payload"}
    link = parts[linked_part]
    link.parent.mkdir(parents=True, exist_ok=True)
    make_directory_link(link, outside)
    monkeypatch.setattr(staging, "TARGET_ROOT", root, raising=False)
    with pytest.raises(RuntimeError, match="link"):
        with staging.installed_payload(source):
            pytest.fail("must not reach chroot")
    assert not list(outside.iterdir())


def test_target_transfer_preserves_preexisting_payload(tmp_path, choices, profile, monkeypatch):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    root = tmp_path / "target"
    destination = root / "root/.arch-hypr-installer/payload"
    destination.mkdir(parents=True)
    (destination / "sentinel").write_text("user data")
    monkeypatch.setattr(staging, "TARGET_ROOT", root, raising=False)
    with pytest.raises(FileExistsError):
        with staging.installed_payload(source):
            pytest.fail("existing payload must not be overwritten")
    assert (destination / "sentinel").read_text() == "user data"


@pytest.mark.parametrize("unsafe_root", ["missing", "file", "traversal"])
def test_target_transfer_requires_a_real_canonical_root(unsafe_root, tmp_path, choices, profile, monkeypatch):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    root = tmp_path / "target"
    if unsafe_root == "file":
        root.write_text("sentinel")
    elif unsafe_root == "traversal":
        root.mkdir()
        root = root / ".." / "escaped"
    monkeypatch.setattr(staging, "TARGET_ROOT", root, raising=False)
    with pytest.raises(RuntimeError, match="target"):
        with staging.installed_payload(source):
            pytest.fail("unsafe root")
    assert not (tmp_path / "escaped").exists()


def test_target_transfer_rejects_linked_staged_source(tmp_path, choices, profile):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    outside = tmp_path / "outside"
    outside.mkdir()
    make_directory_link(source / "home/linked", outside)
    with pytest.raises(RuntimeError, match="link"):
        with staging.installed_payload(source):
            pytest.fail("unsafe source")


def test_partial_transfer_failure_cleans_target_payload(tmp_path, choices, profile, monkeypatch, target_root):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    def fail_copy(source_stream, target_stream, *args, **kwargs):
        target_stream.write(b"partial")
        raise OSError("simulated transfer failure")
    monkeypatch.setattr(staging.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="transfer failure"):
        with staging.installed_payload(source):
            pytest.fail("copy failed")
    assert not (target_root / "root/.arch-hypr-installer").exists()


@pytest.mark.parametrize("entry", ["post_install.py", "profile-plan.json"])
def test_transfer_requires_regular_staged_entry_files(entry, tmp_path, choices, profile, target_root):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    (source / entry).unlink()
    (source / entry).mkdir()
    with pytest.raises(RuntimeError, match="payload"):
        with staging.installed_payload(source):
            pytest.fail("invalid staged entry")
    assert not list(target_root.iterdir())


def test_transfer_cleanup_failure_is_safe(tmp_path, choices, profile, monkeypatch, target_root):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    runtime = target_root / "root/.arch-hypr-installer"
    remove = staging.shutil.rmtree
    def denied(path, *args, **kwargs):
        if path == runtime:
            raise PermissionError("UNTRUSTED secret")
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(staging.shutil, "rmtree", denied)
    with pytest.raises(RuntimeError, match="^target payload cleanup failed; inspect the installed runtime$"):
        with staging.installed_payload(source):
            assert runtime.is_dir()


def test_transfer_cleanup_refuses_replaced_directory(tmp_path, choices, profile, target_root):
    source = tmp_path / "source"
    stage_payload(source, choices, profile)
    runtime = target_root / "root/.arch-hypr-installer"
    with pytest.raises(RuntimeError, match="cleanup failed"):
        with staging.installed_payload(source):
            runtime.rename(target_root / "moved-payload")
            runtime.mkdir()
            (runtime / "sentinel").write_text("foreign replacement")
    assert (runtime / "sentinel").read_text() == "foreign replacement"
