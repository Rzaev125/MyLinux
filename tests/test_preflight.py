from arch_hypr.preflight import PreflightFacts, validate_preflight


def test_supported_environment_has_no_errors():
    facts = PreflightFacts(
        machine="x86_64", efi=True, root=True, online=True,
        commands=frozenset({"archinstall", "arch-chroot", "lsblk", "findmnt", "openssl"}),
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
        "missing commands: arch-chroot, archinstall, findmnt, openssl",
    )


def test_install_preflight_requires_arch_chroot():
    facts = PreflightFacts("x86_64", True, True, True, frozenset({"archinstall", "lsblk", "findmnt", "openssl"}))
    assert validate_preflight(facts) == ("missing commands: arch-chroot",)


def test_online_probe_closes_its_response(monkeypatch):
    import io
    from arch_hypr.preflight import online_probe
    response = io.BytesIO(b"online")
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: response)
    assert online_probe() is True
    assert response.closed
