"""Prepare an installation and gate every upstream execution."""
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import os

from .archinstall_adapter import archinstall_argv, build_payload, hash_secrets, write_secure_payload
from .disks import discover_disks, eligible_disks, require_exact_confirmation
from .preflight import collect_preflight, online_probe, validate_preflight
from .profiles import compose_profile
from .staging import is_link, stage_payload


def preflight_errors() -> tuple[str, ...]:
    return validate_preflight(collect_preflight(online_probe))


class InstallMode(StrEnum):
    DRY_RUN = "dry-run"
    VALIDATE_UPSTREAM = "validate-upstream"
    INSTALL = "install"


@dataclass(frozen=True)
class PreparedInstall:
    config_path: Path
    creds_path: Path
    payload_dir: Path
    experimental: bool


class InstallerOrchestrator:
    def __init__(self, runner, runtime_dir=Path("/run/arch-hypr-installer"), *, preflight=None):
        self.runner = runner
        self.runtime_dir = Path(runtime_dir)
        self.preflight = preflight if preflight is not None else preflight_errors

    def _verify_disk(self, choices):
        disks = eligible_disks(discover_disks(self.runner))
        matches = [disk for disk in disks if disk.path.as_posix() == choices.device.as_posix()]
        if len(matches) != 1 or matches[0].size_bytes != choices.disk_size_bytes:
            raise ValueError("selected disk identity or size no longer matches an eligible physical disk")

    def _check_fresh_paths(self):
        for path in (self.runtime_dir, *self.runtime_dir.parents):
            if is_link(path):
                raise ValueError("runtime directory must not contain links")
        if self.runtime_dir.exists():
            if not self.runtime_dir.is_dir():
                raise ValueError("runtime path must be a directory")
            if hasattr(os, "geteuid") and self.runtime_dir.stat().st_uid != os.geteuid():
                raise ValueError("runtime directory owner must be the current user")
        for name in ("config.json", "creds.json", "payload"):
            if os.path.lexists(self.runtime_dir / name):
                raise FileExistsError(f"runtime destination already exists: {name}")

    def prepare(self, choices, plain_secrets) -> PreparedInstall:
        self._check_fresh_paths()
        print("Preparing installation profile")
        profile = compose_profile(choices.hardware, choices.software)
        if profile.experimental:
            print("EXPERIMENTAL — NVIDIA boot may require manual recovery")
        hashed = hash_secrets(plain_secrets, self.runner)
        payload = build_payload(choices, hashed, profile)
        self.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.runtime_dir.chmod(0o700)
        payload_dir = self.runtime_dir / "payload"
        print("Staging profile resources")
        stage_payload(payload_dir, choices, profile)
        config_path, creds_path = self.runtime_dir / "config.json", self.runtime_dir / "creds.json"
        write_secure_payload(payload, config_path, creds_path)
        print("Prepared configuration and private credentials")
        return PreparedInstall(config_path, creds_path, payload_dir, profile.experimental)

    def execute(self, choices, plain_secrets, mode: InstallMode, confirmation: str | None):
        mode = InstallMode(mode)
        if mode is InstallMode.INSTALL:
            require_exact_confirmation(choices.device, confirmation or "")
        if mode is not InstallMode.DRY_RUN:
            errors = self.preflight()
            if errors:
                raise ValueError("; ".join(errors))
        self._verify_disk(choices)
        self._check_fresh_paths()
        try:
            prepared = self.prepare(choices, plain_secrets)
            if mode is not InstallMode.DRY_RUN:
                self._verify_disk(choices)
                print("Validating upstream configuration" if mode is InstallMode.VALIDATE_UPSTREAM else "Installing selected disk")
                try:
                    result = self.runner.run(archinstall_argv(
                        prepared.config_path, prepared.creds_path,
                        dry_run=mode is InstallMode.VALIDATE_UPSTREAM,
                    ))
                except Exception:
                    # Diagnostics can contain serialized credentials in arbitrary encodings.
                    raise RuntimeError("archinstall execution failed; inspect local archinstall logs") from None
                if result.returncode != 0:
                    code = result.returncode if type(result.returncode) is int else "unknown"
                    raise RuntimeError(f"archinstall failed (exit {code}); inspect local archinstall logs")
            return prepared
        finally:
            try:
                (self.runtime_dir / "creds.json").unlink(missing_ok=True)
            except (OSError, KeyboardInterrupt):
                raise RuntimeError("credential cleanup failed; private runtime may remain") from None
