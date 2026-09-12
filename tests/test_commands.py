import subprocess
import sys

from arch_hypr.commands import SubprocessRunner


def test_real_command_boundary_preserves_literal_arguments_and_stdin(monkeypatch):
    actual_run = subprocess.run
    def observed_run(argv, **kwargs):
        assert isinstance(argv, list)
        assert kwargs["shell"] is False
        return actual_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", observed_run)
    result = SubprocessRunner().run((sys.executable, "-c", "import sys; print(sys.argv[1]); print(sys.stdin.read())", "literal; argument"), input_text="private input")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["literal; argument", "private input"]
