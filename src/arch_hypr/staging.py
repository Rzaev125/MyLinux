from importlib.resources import as_file, files
from pathlib import Path
import json
import os
import shutil


def stage_payload(destination, choices, profile) -> None:
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"payload destination already exists: {destination}")
    destination.mkdir(mode=0o700, parents=True)
    resources = files("arch_hypr").joinpath("resources")
    with as_file(resources) as resource_root:
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
