"""Catch unsafe input coercion, missing gates, disclosure, and wrong exit codes."""
import json
from pathlib import Path

import pytest

from arch_hypr import cli
from arch_hypr.cli import build_parser
from arch_hypr.domain import Disk
from arch_hypr.ui import collect_interactive, load_answers, read_destructive_confirmation
from test_orchestrator import InstallRunner

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parents[1]


def test_archiso_smoke_validates_both_fixtures_without_installing():
    script = (REPO_ROOT / "scripts" / "archiso-smoke.sh").read_text()
    assert "set -euo pipefail" in script
    assert "answers-amd-encrypted.json" in script
    assert "answers-intel-plain.json" in script
    assert "--validate-upstream" in script
    assert "--install" not in script


def test_readme_requires_archiso_preflight_and_a_writable_smoke_copy():
    readme = (REPO_ROOT / "README.md").read_text()
    dry_run = readme.split("## Arch live-environment dry-run", 1)[1].split(
        "## Official Arch ISO upstream validation", 1)[0]
    smoke = readme.split("## Official Arch ISO upstream validation", 1)[1].split(
        "## Real installation", 1)[0]
    assert "/run/archiso/bootmnt" in dry_run
    assert all(command in dry_run for command in ("archinstall", "findmnt", "lsblk", "openssl"))
    assert "eligible target disk" in dry_run
    assert "Host-side development" in dry_run
    assert 'work_dir="$(mktemp -d /tmp/arch-hypr-source.XXXXXX)"' in smoke
    assert 'cp -R -- "$source_repo" "$work_dir/repo"' in smoke
    assert 'chmod -R u+w "$work_dir/repo"' in smoke
    assert 'cd "$work_dir/repo"' in smoke


@pytest.mark.parametrize("args", [[], ["--dry-run", "--install"], ["--install", "--output-dir", "out"], ["--dry-run"]])
def test_invalid_cli_modes_exit_two(args):
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code == 2


def test_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--dry-run", "--install", "--output-dir", "out"])


@pytest.mark.parametrize("raises", [False, True])
def test_upstream_password_hash_is_not_disclosed(raises, monkeypatch, tmp_path, capsys):
    runner = InstallRunner(upstream_error="failed: $6$test-hash")
    if raises:
        def fail():
            raise RuntimeError("failed: $6$test-hash")
        runner.on_install = fail
    configure(monkeypatch, tmp_path, runner=runner)
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    captured = capsys.readouterr()
    assert "$6$test-hash" not in captured.out + captured.err
    assert "archinstall" in captured.err and "local" in captured.err
    assert not (tmp_path / "runtime").exists()


def test_existing_runtime_is_never_removed(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "creds.json").write_text("existing user data")
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    assert (runtime / "creds.json").read_text() == "existing user data"


@pytest.mark.parametrize("encoding", [str, json.dumps, repr])
@pytest.mark.parametrize("exception", [None, RuntimeError, ValueError, OSError])
def test_upstream_diagnostics_never_echo_untrusted_text(encoding, exception, monkeypatch, tmp_path, capsys):
    login, luks = "login\\secret\t'\"", "luks\\secret\n'\""
    diagnostic = "UNTRUSTED " + encoding([login, luks, "$6$test-hash"])
    runner = InstallRunner(upstream_error=diagnostic)
    if exception is not None:
        def fail():
            raise exception(diagnostic)
        runner.on_install = fail
    configure(monkeypatch, tmp_path, runner=runner, passwords=[login, login, luks])
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "UNTRUSTED" not in text
    assert "secret" not in text and "$6$test-hash" not in text
    assert "archinstall" in captured.err and "local" in captured.err
    if exception is None:
        assert "1" in captured.err
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("changes", [{"login_password": "secret"}, {"encryption": "false"}, {"disk_size_bytes": True}, {"hardware": "bogus"}, {"device": 12}])
def test_answer_schema_rejects_secrets_and_wrong_types(changes, tmp_path):
    data = json.loads((FIXTURES / "answers-amd-encrypted.json").read_text()) | changes
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_answers(path)


@pytest.mark.parametrize("contents", ["[]", "{}", '{"device":'])
def test_answer_schema_requires_complete_object(contents, tmp_path):
    path = tmp_path / "answers.json"
    path.write_text(contents)
    with pytest.raises(ValueError):
        load_answers(path)


def test_install_confirmation_requires_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ValueError, match="TTY"):
        read_destructive_confirmation(Path("/dev/sda"), input_fn=lambda _: pytest.fail("must not ask"))


@pytest.mark.parametrize("selection", ["0", "-1", "2", "no"])
def test_interactive_disk_selection_cannot_wrap(selection):
    disks = (Disk(Path("/dev/sda"), "Target", 68719476736, False, False),)
    with pytest.raises(ValueError):
        collect_interactive(disks, input_fn=lambda _: selection, getpass_fn=lambda _: pytest.fail("no secrets before selection"))


def test_interactive_plain_profile_uses_hidden_passwords():
    disks = (Disk(Path("/dev/sda"), "Target", 68719476736, False, False),)
    answers = iter(["1", "", "alex", "", "", "", "intel", "developer", "n"])
    secrets = iter(["login-secret", "login-secret"])
    choices, plain = collect_interactive(disks, input_fn=lambda _: next(answers), getpass_fn=lambda _: next(secrets))
    assert choices.hostname == "hyprbox" and not choices.encryption
    assert plain.login_password == "login-secret" and plain.luks_passphrase is None


def configure(monkeypatch, tmp_path, *, runner=None, passwords=None):
    runner = runner or InstallRunner()
    monkeypatch.setattr(cli, "SubprocessRunner", lambda: runner)
    monkeypatch.setattr(cli, "preflight_errors", lambda: ())
    monkeypatch.setattr("arch_hypr.orchestrator.preflight_errors", lambda: ())
    monkeypatch.setattr(cli, "RUNTIME_DIR", tmp_path / "runtime")
    values = iter(passwords or ["login-secret", "login-secret", "luks-secret"])
    monkeypatch.setattr(cli, "getpass", lambda _: next(values))
    return runner


@pytest.mark.parametrize("fixture", ["answers-amd-encrypted.json", "answers-intel-plain.json"])
def test_cli_dry_run_exports_only_redacted_artifacts(fixture, monkeypatch, tmp_path, capsys):
    runner = configure(monkeypatch, tmp_path)
    output = tmp_path / "out"
    assert cli.main(["--answers", str(FIXTURES / fixture), "--dry-run", "--output-dir", str(output)]) == 0
    assert (output / "config.json").is_file()
    assert (output / "profile-plan.json").is_file()
    assert (output / "payload" / "post_install.py").is_file()
    assert json.loads((output / "creds.example.json").read_text()) == {"users": [{"username": "<redacted>", "enc_password": "<redacted>", "sudo": True}]}
    text = capsys.readouterr().out + "".join(p.read_text() for p in output.rglob("*") if p.is_file())
    assert all(secret not in text for secret in ("login-secret", "luks-secret", "$6$test-hash"))
    assert not list(tmp_path.rglob("creds.json"))
    assert not any(argv[0] == "archinstall" for argv, _ in runner.calls)


def test_cli_preflight_fails_before_discovery_and_secrets(monkeypatch, tmp_path, capsys):
    runner = configure(monkeypatch, tmp_path, passwords=[])
    monkeypatch.setattr(cli, "preflight_errors", lambda: ("UEFI boot is required", "network access is required"))
    assert cli.main(["--install"]) == 2
    text = capsys.readouterr().err
    assert "UEFI" in text and "network" in text
    assert not runner.calls
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("secret", ["login-secret", "luks-secret"])
def test_cli_errors_redact_known_secrets_and_clean_runtime(secret, monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path, runner=InstallRunner(upstream_error=f"error: {secret}"))
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "archinstall" in text and "local" in text
    assert "login-secret" not in text and "luks-secret" not in text
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("typed,expected", [("/dev/sda", 0), ("sda", 2)])
def test_cli_install_confirmation_after_summary_before_hashing(typed, expected, monkeypatch, tmp_path, capsys):
    runner = configure(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    def confirm(prompt):
        assert "ALL DATA" in capsys.readouterr().out
        assert not any(argv[0] in {"openssl", "archinstall"} for argv, _ in runner.calls)
        return typed
    monkeypatch.setattr("builtins.input", confirm)
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--install"]) == expected
    assert any(argv[0] == "archinstall" for argv, _ in runner.calls) == (expected == 0)
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("upstream_fails", [False, True])
def test_runtime_cleanup_failure_returns_two_without_completion(upstream_fails, monkeypatch, tmp_path, capsys):
    runner = InstallRunner(upstream_error="failed" if upstream_fails else None)
    configure(monkeypatch, tmp_path, runner=runner)
    remove = cli.shutil.rmtree
    output_before_cleanup = []
    def denied(path, *args, **kwargs):
        if Path(path) == tmp_path / "runtime":
            output_before_cleanup.append(capsys.readouterr().out)
            raise PermissionError("UNTRUSTED login-secret luks-secret $6$test-hash")
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(cli.shutil, "rmtree", denied)
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    captured = capsys.readouterr()
    assert output_before_cleanup and all("Completed" not in text for text in output_before_cleanup)
    assert "cleanup" in captured.err.lower()
    assert "UNTRUSTED" not in captured.err and "secret" not in captured.err
    assert "Completed" not in captured.out
    assert not (tmp_path / "runtime" / "creds.json").exists()


def test_credential_cleanup_failure_is_a_safe_cli_error(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path)
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path.name == "creds.json":
            raise PermissionError("UNTRUSTED $6$test-hash")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    captured = capsys.readouterr()
    assert "cleanup" in captured.err.lower()
    assert "UNTRUSTED" not in captured.err and "$6$test-hash" not in captured.err
    assert "Completed" not in captured.out
    assert not (tmp_path / "runtime").exists()


def test_dry_run_cleanup_failure_returns_two(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path)
    cleanup = cli.tempfile.TemporaryDirectory.cleanup
    output_before_cleanup = []
    def fail_after_removal(directory):
        output_before_cleanup.append(capsys.readouterr().out)
        cleanup(directory)
        raise PermissionError("UNTRUSTED escaped-login-secret")
    monkeypatch.setattr(cli.tempfile.TemporaryDirectory, "cleanup", fail_after_removal)
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--dry-run", "--output-dir", str(tmp_path / "out")]) == 2
    captured = capsys.readouterr()
    assert output_before_cleanup and all("Completed" not in text for text in output_before_cleanup)
    assert "cleanup" in captured.err.lower()
    assert "UNTRUSTED" not in captured.err
    assert "Completed" not in captured.out


def test_upstream_keyboard_interrupt_cleans_without_completion(monkeypatch, tmp_path, capsys):
    runner = configure(monkeypatch, tmp_path)
    def interrupt():
        raise KeyboardInterrupt("UNTRUSTED login-secret")
    runner.on_install = interrupt
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    captured = capsys.readouterr()
    assert "interrupt" in captured.err.lower()
    assert "UNTRUSTED" not in captured.err
    assert "Completed" not in captured.out
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("hardware,encryption", [("amd", True), ("nvidia", False)])
def test_destructive_summary_has_disk_and_partition_details_before_confirmation(hardware, encryption, monkeypatch, tmp_path, capsys):
    data = json.loads((FIXTURES / "answers-amd-encrypted.json").read_text())
    data.update(hardware=hardware, encryption=encryption)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(data))
    configure(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    summaries = []
    def confirm(prompt):
        summaries.append(capsys.readouterr().out)
        return "/dev/sda"
    monkeypatch.setattr("builtins.input", confirm)
    assert cli.main(["--answers", str(answers), "--install"]) == 0
    assert len(summaries) == 1
    summary = summaries[0]
    assert "/dev/sda" in summary and "Target" in summary
    assert "68719476736 bytes" in summary
    assert "1 GiB FAT32 ESP + Btrfs remainder" in summary
    assert ("LUKS2 enabled" if encryption else "LUKS2 disabled") in summary
    assert ("EXPERIMENTAL" in summary) == (hardware == "nvidia")
