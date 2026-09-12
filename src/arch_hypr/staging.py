from importlib.resources import as_file, files
from pathlib import Path
import json
import os
import shutil


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


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
    if destination.exists():
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
