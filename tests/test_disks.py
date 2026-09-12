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

    disks = discover_disks(runner, live_source=None)

    assert [str(d.path) for d in eligible_disks(disks)] == ["/dev/nvme0n1", "/dev/sda"]
    assert runner.calls == [
        ("findmnt", "--noheadings", "--output", "SOURCE", "/run/archiso/bootmnt"),
        ("lsblk", "--bytes", "--json", "-o", "NAME,PATH,TYPE,SIZE,MODEL,RO,RM,MOUNTPOINTS,PKNAME"),
    ]
