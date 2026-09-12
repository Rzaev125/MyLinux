from arch_hypr.preflight import PreflightFacts, validate_preflight


def test_supported_environment_has_no_errors():
    facts = PreflightFacts(
        machine="x86_64", efi=True, root=True, online=True,
        commands=frozenset({"archinstall", "lsblk", "findmnt", "openssl"}),
    )
    assert validate_preflight(facts) == ()


def test_all_unsupported_conditions_are_reported_together():
    facts = PreflightFacts(
        machine="aarch64", efi=False, root=False, online=False,
        commands=frozenset({"lsblk"}),
    )
    assert validate_preflight(facts) == (
        "x86-64 is required",
        "UEFI boot is required",
        "root privileges are required",
        "network access is required",
        "missing commands: archinstall, findmnt, openssl",
    )
