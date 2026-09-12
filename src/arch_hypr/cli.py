"""Explicit console modes with private runtime ownership and redacted exports."""
from argparse import ArgumentParser
from getpass import getpass
from pathlib import Path
import json
import shutil
import sys
import tempfile

from .commands import SubprocessRunner
from .disks import discover_disks, eligible_disks, require_exact_confirmation
from .orchestrator import InstallMode, InstallerOrchestrator, preflight_errors
from .profiles import compose_profile
from .staging import is_link
from .ui import collect_interactive, collect_secrets, load_answers, read_destructive_confirmation, reconcile_answer_disk

RUNTIME_DIR = Path("/run/arch-hypr-installer")


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="arch-hypr-installer")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--output-dir", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--validate-upstream", action="store_true")
    modes.add_argument("--install", action="store_true")
    return parser


def _export(prepared, output: Path):
    for path in (output, *output.parents):
        if is_link(path):
            raise ValueError("output directory must not contain links")
    output.mkdir(mode=0o700, parents=True)
    shutil.copy2(prepared.config_path, output / "config.json")
    shutil.copytree(prepared.payload_dir, output / "payload")
    shutil.copy2(prepared.payload_dir / "profile-plan.json", output / "profile-plan.json")
    example = {"users": [{"username": "<redacted>", "enc_password": "<redacted>", "sudo": True}]}
    (output / "creds.example.json").write_text(json.dumps(example) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.output_dir is not None and not args.dry_run:
        parser.error("--output-dir is only available with --dry-run")
    if args.dry_run and args.output_dir is None:
        parser.error("--dry-run requires --output-dir")
    mode = InstallMode.DRY_RUN if args.dry_run else (InstallMode.INSTALL if args.install else InstallMode.VALIDATE_UPSTREAM)
    plain = None
    runtime = None
    temporary = None
    owned_runtime = False
    try:
        if mode is not InstallMode.DRY_RUN:
            errors = preflight_errors()
            if errors:
                raise ValueError("; ".join(errors))
        runner = SubprocessRunner()
        disks = eligible_disks(discover_disks(runner))
        if args.answers is not None:
            choices = reconcile_answer_disk(load_answers(args.answers), disks)
            plain = collect_secrets(choices.encryption, getpass_fn=getpass)
        else:
            choices, plain = collect_interactive(disks, input_fn=input, getpass_fn=getpass)
        profile = compose_profile(choices.hardware, choices.software)
        print(f"Plan: {mode.value}; {choices.device.as_posix()}; {choices.disk_size_bytes} bytes; "
              f"{choices.hostname}; user {choices.username}; {choices.hardware.value}/{choices.software.value}; "
              f"LUKS2 {'enabled' if choices.encryption else 'disabled'}")
        print(f"Timezone: {choices.timezone}; keymap: {choices.keymap}; locale: {choices.locale}")
        print("Packages: " + ", ".join(profile.packages))
        print("Services: " + ", ".join(profile.services))
        if profile.experimental:
            print("EXPERIMENTAL — NVIDIA boot may require manual recovery")
        confirmation = None
        if mode is InstallMode.INSTALL:
            confirmation = read_destructive_confirmation(choices.device, input_fn=input)
            require_exact_confirmation(choices.device, confirmation)
        if mode is InstallMode.DRY_RUN:
            temporary = tempfile.TemporaryDirectory(prefix="arch-hypr-installer-")
            runtime = Path(temporary.name)
        else:
            runtime = RUNTIME_DIR
            for path in (runtime, *runtime.parents):
                if is_link(path):
                    raise ValueError("runtime directory must not contain links")
            runtime.mkdir(mode=0o700, parents=True)
            owned_runtime = True
        prepared = InstallerOrchestrator(runner, runtime_dir=runtime).execute(choices, plain, mode, confirmation)
        if mode is InstallMode.DRY_RUN:
            _export(prepared, args.output_dir)
        print("Completed. Reboot manually when ready." if mode is InstallMode.INSTALL else "Completed.")
        return 0
    except (ValueError, RuntimeError, OSError, EOFError) as error:
        message = str(error)
        if plain is not None:
            for secret in sorted(filter(None, (plain.login_password, plain.luks_passphrase)), key=len, reverse=True):
                message = message.replace(secret, "<redacted>")
        print(f"Error: {message}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.cleanup()
        elif owned_runtime:
            shutil.rmtree(runtime)


if __name__ == "__main__":
    raise SystemExit(main())
