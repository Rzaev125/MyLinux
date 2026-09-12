from importlib.resources import files
import json

from .domain import HardwareProfile, ProfilePlan, SoftwareProfile


def load_profile(relative: str) -> dict:
    resource = files("arch_hypr").joinpath("resources", "profiles", relative)
    return json.loads(resource.read_text(encoding="utf-8"))


def _unique(values):
    return tuple(dict.fromkeys(values))


def compose_profile(hardware: HardwareProfile, software: SoftwareProfile) -> ProfilePlan:
    layers = (
        load_profile("base.json"),
        load_profile(f"hardware/{hardware.value}.json"),
        load_profile(f"software/{software.value}.json"),
    )
    versions = {layer["version"] for layer in layers}
    if versions != {1}:
        raise ValueError(f"incompatible profile versions: {sorted(versions)}")
    return ProfilePlan(
        version=1,
        packages=_unique(pkg for layer in layers for pkg in layer["packages"]),
        services=_unique(service for layer in layers for service in layer["services"]),
        repositories=_unique(repo for layer in layers for repo in layer["repositories"]),
        experimental=any(layer["experimental"] for layer in layers),
    )
