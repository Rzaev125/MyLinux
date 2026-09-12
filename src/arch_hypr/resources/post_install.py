from argparse import ArgumentParser
from pathlib import Path, PurePosixPath
import json
import os
import pwd
import re
import shutil
import subprocess


PAYLOAD = Path("/run/arch-hypr-installer/payload")
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:-]*[.]service")
USER_OWNED = frozenset({Path(".config/hypr/local/overrides.lua")})


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def reject_link_chain(path: Path, label: str) -> None:
    for candidate in reversed((path, *path.parents)):
        if is_link(candidate):
            raise RuntimeError(f"{label} link is not allowed: {candidate}")


def absolute_path(path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"invalid {label}: {path}")
    return path


def validate_root(path, label: str) -> Path:
    path = absolute_path(path, label)
    reject_link_chain(path, label)
    if not path.is_dir():
        raise RuntimeError(f"{label} is not a directory: {path}")
    path.resolve(strict=True)
    return path


def validate_contained(
    path, boundary: Path, label: str, *, require_exists: bool = False
) -> Path:
    path = absolute_path(path, label)
    try:
        relative = path.relative_to(boundary)
    except ValueError as error:
        raise RuntimeError(f"{label} escapes boundary: {path}") from error
    reject_link_chain(path, label)
    if require_exists and not path.exists():
        raise RuntimeError(f"{label} does not exist: {path}")
    try:
        resolved = path.resolve(strict=require_exists)
    except OSError as error:
        raise RuntimeError(f"cannot resolve {label}: {path}") from error
    if not resolved.is_relative_to(boundary.resolve(strict=True)):
        raise RuntimeError(f"{label} escapes boundary: {path}")
    return relative


def scan_tree(source: Path, boundary: Path, label: str):
    validate_contained(source, boundary, label, require_exists=True)
    if not source.is_dir():
        raise RuntimeError(f"{label} is not a directory: {source}")
    items = []
    pending = [source]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        directories = []
        for entry in children:
            item = Path(entry.path)
            validate_contained(item, boundary, label, require_exists=True)
            items.append(item)
            if entry.is_dir(follow_symlinks=False):
                directories.append(item)
        pending.extend(reversed(directories))
    return tuple(items)


def prepare_copy_tree(
    source: Path,
    destination: Path,
    source_boundary: Path,
    destination_boundary: Path,
    *,
    preserve_existing: frozenset[Path] = frozenset(),
) -> tuple:
    prepared = []
    validate_contained(destination, destination_boundary, "destination")
    for item in scan_tree(source, source_boundary, "source"):
        relative = item.relative_to(source)
        target = destination / relative
        validate_contained(target, destination_boundary, "destination")
        item_is_directory = item.is_dir()
        if target.exists() and item_is_directory != target.is_dir():
            raise RuntimeError(f"destination type mismatch: {target}")
        prepared.append((item, target, relative, item_is_directory, preserve_existing))
    return tuple(prepared)


def copy_prepared_tree(prepared: tuple) -> None:
    for item, target, relative, item_is_directory, preserve_existing in prepared:
        if item_is_directory:
            target.mkdir(parents=True, exist_ok=True)
        elif relative not in preserve_existing or not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target, follow_symlinks=False)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    boundary = validate_root(path, "home config")
    items = scan_tree(path, boundary, "home")
    os.chown(path, uid, gid, follow_symlinks=False)
    for item in items:
        os.chown(item, uid, gid, follow_symlinks=False)


def account_home(root: Path, value) -> Path:
    if not isinstance(value, str):
        raise RuntimeError("invalid account home")
    home = PurePosixPath(value)
    if home.anchor != "/" or len(home.parts) < 2 or ".." in home.parts:
        raise RuntimeError("invalid account home")
    destination = root.joinpath(*home.parts[1:])
    validate_contained(destination, root, "home")
    return destination


def apply_payload(*, payload, root, username, account, run_command) -> None:
    if not USERNAME.fullmatch(username):
        raise RuntimeError("invalid username")
    payload = validate_root(payload, "payload")
    plan_path = payload / "profile-plan.json"
    validate_contained(plan_path, payload, "profile plan", require_exists=True)
    if not plan_path.is_file():
        raise RuntimeError("profile plan is not a file")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan["username"] != username or plan["profile_version"] != 1:
        raise RuntimeError("profile plan identity mismatch")
    services = plan["services"]
    if not isinstance(services, list) or not all(
        isinstance(service, str) and SERVICE.fullmatch(service) for service in services
    ):
        raise RuntimeError("invalid service list")

    root = validate_root(root, "target root")
    home = account_home(root, account.pw_dir)
    config = home / ".config"
    validate_contained(config, root, "home config")
    if config.exists():
        if not config.is_dir():
            raise RuntimeError(f"home config is not a directory: {config}")
        scan_tree(config, root, "home")

    version = root / "etc/arch-hypr/profile-version"
    validate_contained(version.parent, root, "profile version directory")
    validate_contained(version, root, "profile version")
    if version.parent.exists() and not version.parent.is_dir():
        raise RuntimeError(f"profile version parent is not a directory: {version.parent}")
    if version.exists() and not version.is_file():
        raise RuntimeError(f"profile version is not a file: {version}")

    rootfs_copy = prepare_copy_tree(
        payload / "rootfs", root, payload, root
    )
    home_copy = prepare_copy_tree(
        payload / "home",
        home,
        payload,
        root,
        preserve_existing=USER_OWNED,
    )

    copy_prepared_tree(rootfs_copy)
    copy_prepared_tree(home_copy)
    chown_tree(home / ".config", account.pw_uid, account.pw_gid)
    if services:
        run_command(["systemctl", "enable", "--", *services], check=True, shell=False)
    version.parent.mkdir(parents=True, exist_ok=True)
    version.write_text("1\n", encoding="utf-8")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--username", required=True)
    username = parser.parse_args().username
    if not USERNAME.fullmatch(username):
        raise RuntimeError("invalid username")
    account = pwd.getpwnam(username)
    apply_payload(
        payload=PAYLOAD,
        root=Path("/"),
        username=username,
        account=account,
        run_command=subprocess.run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
