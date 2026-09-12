# Arch + Hyprland + GPT Installer Delivery Roadmap

**Spec:** `docs/superpowers/specs/2026-09-12-arch-hyprland-gpt-installer-design.md`

The approved specification contains four independently testable subsystems. Each receives its own implementation plan so discoveries in the installer do not invalidate assistant or UI work.

## Phase 1 — Safe installer and Hyprland profile

Deliver a Python package that runs from the official Arch ISO, validates the environment and target disk, produces versioned `archinstall` config/creds files, installs the approved Btrfs/LUKS layout, and applies an idempotent base Hyprland profile.

Exit gate: unit tests pass, upstream `archinstall --silent --dry-run` accepts both encrypted and unencrypted fixtures, and a disposable UEFI VM reaches the greetd login screen.

Detailed plan: `docs/superpowers/plans/2026-09-12-installer-profile-foundation.md`

## Phase 1 Evidence - 2026-09-12

Phase 1 exit status: **PENDING**. Local automated evidence is recorded below,
but this Windows host has no usable Bash environment and no QEMU, VirtualBox,
VMware, or Hyper-V runtime. No suitable, explicitly disposable VM target is
available, so no ISO boot or destructive installation was attempted.

### ISO artifact

- File: `archlinux-2026.09.01-x86_64.iso`
- Local size: `1608286208` bytes
- Local SHA-256: `BE8458032F8105E60EE2A3067F950B6E3C007EE51B38DAC50E8B48E765561C91`
- Git state: ignored by `.gitignore` and not tracked.
- Provenance/authenticity: not established by the local hash alone; verify the
  ISO against official Arch Linux checksum/signature material before VM use.

### Local automated checks

- Final fix-wave: `python -m pytest -v`: `199 passed, 4 skipped` on Windows
  with Python 3.14.3 (203 collected). Three POSIX permission/ownership checks
  and one unavailable file-symlink check were skipped; directory-junction
  containment tests ran.
- `python -m pytest --cov=arch_hypr --cov-report=term-missing --cov-fail-under=90`:
  `199 passed, 4 skipped`; overall coverage `96.17%` (`602/626` statements).
  Device confirmation, preflight rejection, secret redaction, profile
  composition, and mode-selection behavior are exercised by the suite.
- `git diff --check`: exit 0; only Git's existing LF-to-CRLF working-copy
  warnings were emitted.
- Wheel built from a temporary writable copy with `pip wheel --no-deps
  --no-build-isolation`. All 18 packaged resources were verified byte-for-byte;
  staging directly from the wheel included hidden Hyprland Lua and zram config.
- Schema checked against official archinstall tag `4.4`, commit
  `3ece182d31dda7b14abd56d13abf3ff79a5717ae`. Exact partition geometry, optional
  repositories, credentials and argv are covered. A separate read-only probe
  ran tagged `MirrorConfiguration.parse_args` and `Repository` on four generated
  configurations with inert empty custom-repository dependencies; normalized
  minimal/gaming repositories matched. This is not full ISO parser acceptance.
- ISO manifest: `archinstall 4.4-1`, `arch-install-scripts 31-2`,
  `python 3.14.7-1`. Validation/install reject another upstream archinstall version.
- `bash -n scripts/archiso-smoke.sh`: unavailable on this host. The only
  `bash.exe` is a WSL relay and reports that `/bin/bash` does not exist;
  ShellCheck is also not installed. The pytest smoke contract confirms both
  fixture names, `set -euo pipefail`, `--validate-upstream`, and absence of
  `--install`, but the script still requires execution in the Arch ISO gate.

### Required ISO and disposable-VM gates

- **PENDING - upstream validation:** boot the ISO in a disposable UEFI VM,
  attach this repository read-only, copy it into a writable temporary workspace
  using the README procedure, and run `bash scripts/archiso-smoke.sh` there.
  Configure the VM disk with a stable serial; adjust both answer files to the
  independently verified path and exact byte size. Mismatched sizes are rejected.
  Both `answers-amd-encrypted.json` and `answers-intel-plain.json` must be
  accepted by upstream `archinstall --silent --dry-run`; no virtual disk may be modified.
- **PENDING - target profile execution:** verify the explicit transfer to
  `/mnt/root/.arch-hypr-installer/payload`, argv-only `arch-chroot /mnt /usr/bin/python
  /root/.arch-hypr-installer/payload/post_install.py --username USERNAME`, and
  removal of `/mnt/root/.arch-hypr-installer` after success/failure. The official
  31-2 arch-chroot package binds live `/run` over target `/run`; that discovery
  ruled out a target `/mnt/run` copy. No archinstall custom command is used.
- **PENDING - encrypted full-disk install:** planned fixture
  `answers-amd-encrypted.json`; VM firmware and virtual disk size are not
  recorded because no VM target was created or selected.
- **PENDING - installed-system checks:** Btrfs root and `compress=zstd`,
  expected subvolumes, NetworkManager and greetd enablement, profile version,
  systemd-boot, LUKS unlock, greetd login, modular Hyprland Lua launch, and
  idempotent profile re-application.
- **Target identity:** the selected path, exact byte size, model, serial and WWN
  are bound to confirmation and compared on final rediscovery. Installation
  refuses disks missing both stable identifiers. Independently verify the VM
  attachment and do not hotplug after confirmation; a final check/open race
  remains inherent in invoking upstream against a device path.

The Phase 2 implementation gate remains closed until every pending item above
has real, dated evidence from the official ISO and the explicitly disposable VM.

## Phase 2 — Assistant core and privileged protocol

Deliver the non-privileged assistant service, strict action schemas, diagnostic collectors, redaction pipeline, policy validator, action journal, Btrfs/Git recovery coordination, and a minimal polkit-protected helper. The model is tested with recorded responses; no graphical interface is required for this phase.

Exit gate: hostile tool calls are rejected without side effects, allowed actions require a current approval token, secrets are removed from payloads and logs, and every mutating test action has a verified rollback path.

## Phase 3 — GTK4 assistant experience

Deliver the `Super+G` panel with Chat, Change Plan, History, and Settings views; Secret Service integration for BYOK; streaming Responses API interaction; exact diagnostic-payload preview; approval controls; progress states; and rollback UX.

Exit gate: the UI can complete diagnosis, preview, confirmation, execution, verification, and rollback against the Phase 2 service without exposing a raw command field.

## Phase 4 — Distribution and end-to-end qualification

Deliver signed immutable releases, a checksum/signature-verifying bootstrap flow, automated UEFI QEMU installation tests, failure-log collection and redaction, release upgrade checks, and the real-hardware Intel/AMD matrix. NVIDIA remains a separate experimental result.

Exit gate: every acceptance criterion in section 14 of the specification has evidence attached to a release candidate.

## Ordering Rule

Phases execute in order. A phase begins only after the previous exit gate passes. Phase 2 may define its service protocol while Phase 1 is under review, but it must not depend on undocumented installer internals. Detailed plans for Phases 2–4 are written after Phase 1 confirms the actual installed filesystem, package set, and service behavior.
