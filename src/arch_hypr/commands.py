from dataclasses import dataclass
from typing import Protocol, Sequence
import subprocess


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, input_text: str | None = None) -> CommandResult: ...


class SubprocessRunner:
    def run(self, argv: Sequence[str], *, input_text: str | None = None) -> CommandResult:
        completed = subprocess.run(
            list(argv), input=input_text, text=True, capture_output=True,
            check=False, shell=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
