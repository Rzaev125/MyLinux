import pytest

from arch_hypr.domain import HardwareProfile, SoftwareProfile
from arch_hypr.profiles import compose_profile, load_profile


def test_amd_developer_profile_is_stable_and_unique():
    plan = compose_profile(HardwareProfile.AMD, SoftwareProfile.DEVELOPER)
    assert plan.version == 1
    assert "hyprland" in plan.packages
    assert "vulkan-radeon" in plan.packages
    assert "podman" in plan.packages
    assert len(plan.packages) == len(set(plan.packages))
    assert plan.experimental is False


def test_gaming_enables_multilib():
    plan = compose_profile(HardwareProfile.INTEL, SoftwareProfile.GAMING)
    assert plan.repositories == ("multilib",)
    assert {"steam", "gamescope", "mangohud"} <= set(plan.packages)


def test_nvidia_is_always_experimental():
    plan = compose_profile(HardwareProfile.NVIDIA, SoftwareProfile.MINIMAL)
    assert plan.experimental is True
    assert {"nvidia-dkms", "nvidia-utils", "linux-headers"} <= set(plan.packages)


def test_composition_preserves_first_occurrence_order():
    plan = compose_profile(HardwareProfile.AMD, SoftwareProfile.DEVELOPER)
    assert plan.packages[:3] == ("base", "linux", "linux-firmware")
    assert plan.packages[-2:] == ("base-devel", "podman")


def test_incompatible_profile_version_is_rejected(monkeypatch):
    original_loader = load_profile

    def load_with_incompatible_base(relative):
        profile = original_loader(relative)
        if relative == "base.json":
            profile = {**profile, "version": 2}
        return profile

    monkeypatch.setattr("arch_hypr.profiles.load_profile", load_with_incompatible_base)
    with pytest.raises(ValueError, match="incompatible profile versions"):
        compose_profile(HardwareProfile.INTEL, SoftwareProfile.MINIMAL)


def test_manifest_has_versioned_schema():
    manifest = load_profile("base.json")
    assert set(manifest) == {
        "version",
        "packages",
        "services",
        "repositories",
        "experimental",
    }
    assert manifest["version"] == 1


@pytest.mark.parametrize("version", [True, 1.0])
def test_version_one_requires_an_actual_integer(version, monkeypatch):
    def invalid_version(relative):
        return load_profile(relative) | {"version": version}
    monkeypatch.setattr("arch_hypr.profiles.load_profile", invalid_version)
    with pytest.raises(ValueError, match="incompatible profile versions"):
        compose_profile(HardwareProfile.AMD, SoftwareProfile.MINIMAL)
