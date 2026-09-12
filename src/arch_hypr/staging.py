from importlib.resources import as_file, files
from contextlib import contextmanager
from pathlib import Path
import json
import os
import shutil

TARGET_ROOT = Path("/mnt")


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def validate_directory_chain(path: Path, label: str) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"invalid {label} path")
    for candidate in reversed((path, *path.parents)):
        if is_link(candidate):
            raise RuntimeError(f"{label} link is not allowed: {candidate}")
        if candidate.exists() and not candidate.is_dir():
            raise RuntimeError(f"{label} ancestor is not a directory: {candidate}")


def reject_resource_links(root: Path) -> None:
    for candidate in reversed((root, *root.parents)):
        if is_link(candidate):
            raise RuntimeError(f"resource link is not allowed: {candidate}")
    if not root.is_dir():
        raise RuntimeError(f"resource root is not a directory: {root}")
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        directories = []
        for entry in children:
            item = Path(entry.path)
            if is_link(item):
                raise RuntimeError(f"resource link is not allowed: {item}")
            if entry.is_dir(follow_symlinks=False):
                directories.append(item)
        pending.extend(reversed(directories))


def stage_payload(destination, choices, profile) -> None:
    destination = Path(destination)
    validate_directory_chain(destination.parent, "payload destination")
    if is_link(destination):
        raise RuntimeError("payload destination link is not allowed")
    if os.path.lexists(destination):
        raise FileExistsError(f"payload destination already exists: {destination}")
    resources = files("arch_hypr").joinpath("resources")
    with as_file(resources) as resource_root:
        reject_resource_links(resource_root)
        destination.mkdir(mode=0o700, parents=True)
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


@contextmanager
def installed_payload(source: Path):
    """Own a fresh fixed target payload only for the post-install invocation."""
    root = TARGET_ROOT
    validate_directory_chain(root, "target root")
    if root == Path(root.anchor) or not root.is_dir():
        raise RuntimeError("target root must be an existing installation directory")
    # arch-chroot 31 binds live /run over target /run. Use a target-owned path
    # outside that mount so post-install consumes the explicitly transferred copy.
    runtime = root / "root/.arch-hypr-installer"
    destination = runtime / "payload"
    validate_directory_chain(destination, "target payload")
    if os.path.lexists(runtime):
        raise FileExistsError("target payload runtime already exists")
    # Validate every staged source before creating anything in the installed root.
    reject_resource_links(source)
    for name in ("post_install.py", "profile-plan.json"):
        if not (source / name).is_file():
            raise RuntimeError("staged payload entry is not a regular file")
    for name in ("rootfs", "home"):
        if not (source / name).is_dir():
            raise RuntimeError("staged payload tree is not a directory")
    items = sorted(source.rglob("*"))
    if any(not item.is_dir() and not item.is_file() for item in items):
        raise RuntimeError("staged payload contains a non-regular file")
    runtime.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.mkdir(mode=0o700)  # Exclusive ownership; never merge an existing runtime.
    identity = runtime.stat()
    try:
        destination.mkdir(mode=0o700)
        for item in items:
            target = destination / item.relative_to(source)
            if item.is_dir():
                target.mkdir()
            else:
                with item.open("rb") as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
        yield destination
    finally:
        try:
            validate_directory_chain(runtime, "target payload cleanup")
            if runtime.exists():
                current = runtime.stat()
                if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                    raise RuntimeError("target payload identity changed")
                shutil.rmtree(runtime)
        except (Exception, KeyboardInterrupt):
            raise RuntimeError("target payload cleanup failed; inspect the installed runtime") from None
