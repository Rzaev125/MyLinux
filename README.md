# MyLinux

An opinionated Arch Linux installer for a Hyprland desktop. The installer is
designed to run from the official Arch Linux ISO. Phase 1 is not complete until
the pending ISO and disposable-VM gates in the roadmap have been performed.

## Supported and unsupported matrix

The MVP supports:

- x86-64 systems booted in UEFI mode;
- installation to an entire disk, with all existing data on that disk erased;
- systemd-boot;
- LUKS2 encryption enabled by default and optionally disabled before installation;
- Btrfs with snapshots;
- zram instead of a disk swap partition or swap file;
- Intel and AMD as the primary supported hardware paths;
- a separate experimental NVIDIA hardware profile;
- `minimal`, `developer`, and `gaming` software profiles;
- a GTK4 GPT assistant opened with `Super+G`;
- BYOK access, where every user supplies their own OpenAI API key;
- preview, confirmation, verification, logging, and rollback for assistant-initiated changes.

The MVP does not support:

- BIOS/legacy boot;
- dual boot or preservation of existing disk partitions;
- ARM or 32-bit systems;
- arbitrary root shell access for the model;
- GPT-managed disk partitioning, LUKS, bootloader, or user accounts after installation;
- GPT-driven AUR installation;
- unattended background modification of the system;
- a custom bootable ISO or a private package repository.

Btrfs snapshots are recovery points, not backups. Keep independent backups on
separate media. NVIDIA support is experimental and may require manual boot or
graphics recovery; Intel and AMD are the primary supported paths.

## Obtain and verify a release

Use a named, immutable release, never an unversioned branch archive. From the
[GitHub Releases page](https://github.com/Rzaev125/MyLinux/releases), download
the versioned source archive, `SHA256SUMS`, and `SHA256SUMS.sig` as separate
files. Obtain the maintainer signing-key fingerprint through a trusted channel,
import that key, and verify before extracting or running anything:

    gpg --verify SHA256SUMS.sig SHA256SUMS
    sha256sum --check --ignore-missing SHA256SUMS
    tar -xf arch-hypr-installer-VERSION.tar.gz

Do not use `curl | bash` or any equivalent download-and-execute pipeline. If a
signed release and its verification material are not available, stop; a source
checkout is suitable for development and review, not a verified release install.

## Development setup

Python 3.12 or newer is required.

    python -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[dev]"
    python -m pytest -v

On PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.

## Arch live-environment dry-run

No installer CLI mode is a generic host-side configuration command. Every mode
runs preflight and disk discovery first. Use `--dry-run` only in the official
Arch live environment when all of these prerequisites are true:

- the machine is x86-64, booted in UEFI mode, and has network access;
- `/run/archiso/bootmnt` exists and its source is discoverable with `findmnt`;
- `archinstall`, `findmnt`, `lsblk`, and `openssl` are installed;
- an eligible target disk is visible and its device path and exact byte size
  match the answer file.

The mode does not invoke `archinstall` or modify the target disk, but it still
requires disk discovery and answer-to-device reconciliation. From an activated
environment installed from the writable repository copy described below, run:

    arch-hypr-installer --answers tests/fixtures/answers-amd-encrypted.json --dry-run --output-dir ./dry-run-output

Review `config.json`, `creds.example.json`, `profile-plan.json`, and the staged
payload in `dry-run-output/`. The answer fixtures contain no passwords; secrets
are requested interactively and must never be committed.

Host-side development on ordinary Linux or Windows should use the pytest
behavior tests (`python -m pytest -v`). They replace real preflight and disk
discovery with controlled test boundaries and do not invoke a real target.

## Official Arch ISO upstream validation

Boot the official Arch ISO in UEFI mode with networking and attach this
repository read-only. Do not run the smoke script directly from that attachment:
pip/setuptools may create in-place build metadata such as `*.egg-info`. Copy the
source to a writable temporary workspace first (adjust `source_repo` to the
actual read-only mount), then run the non-destructive smoke procedure:

    source_repo=/mnt/readonly/MyLinux
    work_dir="$(mktemp -d /tmp/arch-hypr-source.XXXXXX)"
    trap 'rm -rf -- "$work_dir"' EXIT
    cp -R -- "$source_repo" "$work_dir/repo"
    chmod -R u+w "$work_dir/repo"
    cd "$work_dir/repo"

    bash scripts/archiso-smoke.sh

It creates a temporary virtual environment and submits both fixtures to
`archinstall --dry-run` via the installer `--validate-upstream` mode. It never
uses the installer `--install` mode.

A mandatory ISO/VM validation is to prove that the staged
`/run/arch-hypr-installer/payload` directory, especially `post_install.py`,
remains visible and readable when archinstall runs the configured custom
command. Host unit tests and exported dry-run artifacts do not prove this
runtime mount-namespace boundary.

Target identity is not fully established by device path and byte size alone.
Paths such as `/dev/sda` can be reassigned, and two disks can have the same
capacity. This risk is not resolved: immediately before any real installation,
re-check the selected path, model, serial/WWN, exact size, and disposable status
against the VM definition or physical hardware.

For a single fixture without the wrapper:

    arch-hypr-installer --answers tests/fixtures/answers-amd-encrypted.json --validate-upstream

## Real installation

Run this only inside the official Arch ISO after the preflight summary matches
the intended disposable target. The command prompts for secrets and requires
typing the exact target device path.

> **DANGER: WHOLE-DISK INSTALL. This erases every partition and all data on the selected disk. There is no dual-boot or partition-preservation path.**

    arch-hypr-installer --answers tests/fixtures/answers-amd-encrypted.json --install

Do not use a fixture unchanged unless its device path and exact size describe
the disposable target you independently verified.

## Logs and recovery evidence

Archinstall logs are expected under `/var/log/archinstall/`; start with
`/var/log/archinstall/install.log`. Inspect the directory for additional
run-specific files. Treat configuration or credential artifacts there as
sensitive and redact them before sharing. A failed installation must remain in
the live environment for log collection and must not reboot automatically.

After a disposable encrypted VM install, record firmware, disk identity and
size, fixture, commands, and results, then verify the Btrfs filesystem and
compression, enabled NetworkManager and greetd services, profile version,
systemd-boot/LUKS unlock, and a Hyprland login. The authoritative pending gates
and evidence are in the roadmap.

## References

- [Approved design specification](docs/superpowers/specs/2026-09-12-arch-hyprland-gpt-installer-design.md)
- [Delivery roadmap](docs/superpowers/plans/2026-09-12-arch-hyprland-gpt-roadmap.md)
- [Archinstall guided configuration and dry-run](https://archinstall.archlinux.page/installing/guided.html)
- [Archinstall disk and Btrfs schema](https://archinstall.archlinux.page/cli_parameters/config/disk_config.html)
- [Hyprland Lua configuration entry point](https://wiki.hypr.land/Configuring/Start/)
- [Official Arch Hyprland package](https://archlinux.org/packages/extra/x86_64/hyprland/)
- [Official Arch greetd-tuigreet package](https://archlinux.org/packages/extra/x86_64/greetd-tuigreet/)
