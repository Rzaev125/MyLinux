from pathlib import Path
import json

import pytest

from arch_hypr.commands import CommandResult
from arch_hypr.disks import discover_disks, eligible_disks, parse_disks, require_exact_confirmation
from arch_hypr.domain import DomainError


def fixture_payload():
    return json.loads(Path("tests/fixtures/lsblk.json").read_text())


def test_live_media_and_read_only_devices_are_not_eligible():
    disks = parse_disks(fixture_payload(), live_source=Path("/dev/sdb1"))
    assert [str(d.path) for d in eligible_disks(disks)] == ["/dev/nvme0n1", "/dev/sda"]
    assert next(d for d in disks if str(d.path) == "/dev/sdb").live_media is True
    assert next(d for d in disks if str(d.path) == "/dev/sdc").read_only is True


def test_confirmation_must_equal_full_device_path():
    require_exact_confirmation(Path("/dev/sda"), "/dev/sda")
    with pytest.raises(DomainError, match="confirmation"):
        require_exact_confirmation(Path("/dev/sda"), "sda")


def test_discovery_resolves_archiso_source_with_findmnt_before_parsing_disks():
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, *, input_text=None):
            self.calls.append(tuple(argv))
            if argv[0] == "findmnt":
                return CommandResult(0, "/dev/sdb1\n", "")
            return CommandResult(0, json.dumps(fixture_payload()), "")

    runner = Runner()

    disks = discover_disks(runner)

    assert [str(d.path) for d in eligible_disks(disks)] == ["/dev/nvme0n1", "/dev/sda"]
    assert runner.calls == [
        ("findmnt", "--noheadings", "--output", "SOURCE", "/run/archiso/bootmnt"),
        ("lsblk", "--bytes", "--json", "-o", "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,RO,RM,MOUNTPOINTS,PKNAME"),
    ]


def test_discovery_does_not_accept_a_caller_supplied_live_source():
    class Runner:
        def run(self, argv, *, input_text=None):
            raise AssertionError("discovery must reject the bypass before running commands")

    with pytest.raises(TypeError):
        discover_disks(Runner(), Path("/dev/sdb1"))


@pytest.mark.parametrize("source", ["", "sdb1", "/tmp/archiso", "/dev/sdb1\n/dev/sdc1"])
def test_discovery_rejects_malformed_findmnt_source(source):
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, *, input_text=None):
            self.calls.append(tuple(argv))
            return CommandResult(0, source, "")

    runner = Runner()

    with pytest.raises(DomainError, match="findmnt"):
        discover_disks(runner)

    assert runner.calls == [
        ("findmnt", "--noheadings", "--output", "SOURCE", "/run/archiso/bootmnt"),
    ]


def test_discovery_rejects_failed_findmnt():
    class Runner:
        def run(self, argv, *, input_text=None):
            return CommandResult(1, "", "not mounted")

    with pytest.raises(DomainError, match="findmnt failed: not mounted"):
        discover_disks(Runner())


def test_discovery_fails_closed_when_live_source_is_not_in_lsblk():
    class Runner:
        def run(self, argv, *, input_text=None):
            if argv[0] == "findmnt":
                return CommandResult(0, "/dev/sdz1\n", "")
            return CommandResult(0, json.dumps(fixture_payload()), "")

    with pytest.raises(DomainError, match="live ISO source"):
        discover_disks(Runner())


def test_discovery_rejects_failed_lsblk():
    class Runner:
        def run(self, argv, *, input_text=None):
            if argv[0] == "findmnt":
                return CommandResult(0, "/dev/sdb1\n", "")
            return CommandResult(1, "", "device query failed")

    with pytest.raises(DomainError, match="lsblk failed: device query failed"):
        discover_disks(Runner())


def test_discovery_rejects_malformed_lsblk_json():
    class Runner:
        def run(self, argv, *, input_text=None):
            if argv[0] == "findmnt":
                return CommandResult(0, "/dev/sdb1\n", "")
            return CommandResult(0, "{", "")

    with pytest.raises(DomainError, match="invalid JSON"):
        discover_disks(Runner())


def test_optical_archiso_excludes_rom_without_blocking_target_discovery():
    payload = json.loads(Path("tests/fixtures/lsblk-optical.json").read_text())
    class OpticalRunner:
        def run(self, argv, *, input_text=None):
            if argv[0] == "findmnt":
                return CommandResult(0, "/dev/sr0\n", "")
            return CommandResult(0, json.dumps(payload), "")
    disks = eligible_disks(discover_disks(OpticalRunner()))
    assert [disk.path.as_posix() for disk in disks] == ["/dev/vda"]
    assert disks[0].serial == "ARCH-HYPR-VM-001"
    assert disks[0].wwn == "0x5000000000000001"
    assert disks[0].fingerprint == ("/dev/vda", 68719476736, "Virtio Block Device", "ARCH-HYPR-VM-001", "0x5000000000000001")


@pytest.mark.parametrize("source,kind", [("/dev/sr9", "rom"), ("/dev/sr0", "part"), ("/dev/sr0", "loop"), ("/dev/sr0", "unknown")])
def test_optical_exception_does_not_allow_unknown_or_non_rom_live_sources(source, kind):
    payload = json.loads(Path("tests/fixtures/lsblk-optical.json").read_text())
    payload["blockdevices"][1]["type"] = kind
    with pytest.raises(DomainError, match="live ISO source"):
        parse_disks(payload, Path(source))


def test_missing_live_source_is_fail_closed_even_at_parser_boundary():
    with pytest.raises(DomainError, match="live ISO source"):
        parse_disks(fixture_payload(), None)
