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
    assert "<redacted>" in captured.err
    assert not (tmp_path / "runtime").exists()


def test_existing_runtime_is_never_removed(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "creds.json").write_text("existing user data")
    assert cli.main(["--answers", str(FIXTURES / "answers-amd-encrypted.json"), "--validate-upstream"]) == 2
    assert (runtime / "creds.json").read_text() == "existing user data"


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
    assert "<redacted>" in text
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
