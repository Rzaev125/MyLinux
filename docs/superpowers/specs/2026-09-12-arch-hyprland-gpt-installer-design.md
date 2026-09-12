# Arch + Hyprland + GPT Installer — Design Specification

**Date:** 2026-09-12  
**Status:** Approved design  
**Initial audience:** The author and a small group of friends  
**Potential future audience:** Public users after the MVP is proven on real hardware

## 1. Goal

Build a reproducible installer for an opinionated Arch Linux desktop based on Hyprland, plus an integrated GPT assistant that can diagnose and configure the installed system without receiving unrestricted root access.

The first release prioritizes safety, recoverability, and maintainability over broad hardware support or extensive customization. It runs from the official Arch Linux ISO and installs onto an entire selected disk.

## 2. MVP Scope

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

## 3. Selected Technical Approach

The implementation uses Python, the `archinstall` library, and GTK4.

Python provides the installer, profile engine, assistant core, and initial privileged helper implementation. GTK4 provides the assistant interface. The privileged boundary remains small and isolated so it can later be rewritten in Rust without changing the installer, UI, or assistant protocol.

Alternatives rejected for the MVP:

- Shell plus a terminal-only assistant would be quicker initially but difficult to maintain and less approachable for friends.
- A Rust-first installer and assistant would provide a strong long-term foundation but delay the first usable release.

## 4. Component Architecture

### 4.1 `installer`

Runs only in the official Arch ISO environment. It collects installation choices, validates the environment and target disk, generates `archinstall` configuration, runs the installation, invokes the post-install profile step, and performs final checks.

It has no responsibility after the installed system boots.

### 4.2 `system-profile`

Describes packages, services, hardware-specific settings, and desktop configuration declaratively. Reapplying the same profile is idempotent: it converges the machine on the expected state without reinstalling the OS or overwriting user-owned overrides.

### 4.3 `assistant-ui`

A GTK4 application opened with `Super+G`. It displays chat, diagnostic context, proposed changes, diffs, snapshots, execution status, history, privacy controls, and rollback actions.

### 4.4 `assistant-core`

A non-privileged systemd user service. It communicates with the OpenAI Responses API, gathers approved diagnostic information, redacts sensitive values, validates model tool calls against strict schemas, and turns valid calls into a local execution plan.

### 4.5 `privileged-helper`

A minimal system service that contains no model client and never accepts arbitrary command strings. It exposes a fixed, versioned set of operations, validates all arguments and paths, and requests authorization through polkit when elevated privileges are required.

### 4.6 `rollback`

Coordinates Btrfs snapshots for system changes and Git-backed history for managed user configuration. It records the relationship between an assistant action, the files or packages changed, the verification result, and the recovery point.

## 5. Installation Flow

1. The user boots the official Arch Linux ISO in UEFI mode and establishes network access.
2. The user downloads a versioned installer artifact and its signature or checksum. The documented flow never uses `curl | bash`.
3. The installer verifies its own artifact before executing.
4. A TUI collects language, keyboard layout, time zone, hostname, username, passwords, target disk, LUKS preference, LUKS passphrase when applicable, and software profile.
5. The installer checks x86-64, UEFI mode, network access, available disk space, and the target disk identity.
6. The final confirmation page displays the device path, model, capacity, partitioning scheme, encryption state, and an explicit warning that all data will be destroyed. The user must type the target device name to continue.
7. The installer creates a 1 GiB FAT32 EFI System Partition. The remainder becomes either a LUKS2 container or an unencrypted Btrfs partition when encryption was explicitly disabled.
8. Btrfs uses `@`, `@home`, `@snapshots`, and `@var_log` subvolumes. zram provides compressed swap in memory.
9. The installer generates an `archinstall` configuration. Credentials and passphrases are kept separate from the normal configuration and exist only in temporary live-environment storage.
10. `archinstall` installs the base system and systemd-boot.
11. `system-profile` installs the desktop, hardware layer, selected software profile, assistant components, and required services.
12. Final checks verify the bootloader entry, user account, NetworkManager, display session, assistant services, and presence of the expected profile version.
13. The installer reboots only after explicit user confirmation. A failed install remains in the live environment with logs and recovery guidance visible.
14. On first login, onboarding verifies monitor detection and asks the user to configure appearance preferences and their own OpenAI API key.

## 6. Disk and Recovery Model

The disk layout is intentionally fixed for the MVP. Supporting a single predictable layout reduces destructive edge cases and makes recovery testable.

LUKS2 is the default. The TUI permits disabling it before installation, but encryption cannot be enabled or removed through the GPT assistant after installation.

Btrfs snapshots cover assistant-initiated system changes. Managed user configuration is tracked through Git as well. Snapshot creation must succeed before a system-level action can proceed. If no recovery point can be created, the operation is cancelled.

Snapshots are not treated as backups against disk failure. Public documentation must state this distinction.

## 7. Profiles and Configuration Ownership

Configuration is layered in this order:

```text
base
  -> hardware: intel | amd | nvidia
       -> profile: minimal | developer | gaming
            -> user overrides
```

Later layers may override earlier managed values. User overrides are stored separately and are never replaced by a profile update.

The Hyprland configuration is modular:

```text
hyprland.lua
modules/
  monitors.lua
  input.lua
  keybinds.lua
  appearance.lua
  autostart.lua
  rules.lua
local/
  overrides.lua
```

Each system profile release has a schema and version. An update computes a diff, creates a recovery point, runs explicit migrations, preserves `local/overrides.lua`, and validates the resulting configuration before making it current.

The `minimal` profile contains the complete supported desktop. `developer` and `gaming` add packages and services but do not replace the base or hardware layers.

## 8. Assistant Interaction Model

The panel contains four primary views:

- **Chat:** questions, diagnostics, and configuration requests.
- **Change plan:** explanation, affected files and services, exact typed operations, diff, snapshot identifier, and Apply/Cancel controls.
- **History:** previous actions, verification results, and available rollback actions.
- **Settings:** API key setup, model configuration, privacy controls, and diagnostic payload preview.

The UI always exposes the current state: analyzing, awaiting approval, applying, verifying, completed, failed, or rollback available.

Closing the panel does not approve a pending action. The assistant never performs a mutation merely because a conversational response suggested it.

## 9. Assistant Data and Action Flow

```text
User request
  -> local context collection
  -> secret redaction and payload preview
  -> OpenAI Responses API
  -> structured tool calls
  -> schema and policy validation
  -> human-readable plan and diff
  -> recovery point creation
  -> explicit user approval
  -> local or privileged execution
  -> deterministic verification
  -> action journal
```

Read-only tools may run without confirmation. They include hardware summaries, monitor discovery, package inventory, bounded service status, bounded log excerpts, and managed configuration reads.

Mutating tools always require explicit confirmation. MVP operations include:

- patching files inside an allowlisted managed configuration area;
- enabling or restarting allowlisted user services;
- installing or removing packages from official Arch repositories;
- enabling or restarting a small allowlist of system services;
- applying a versioned, locally implemented repair recipe;
- rolling back a recorded assistant action.

The model never submits a root shell command. The execution plan contains operation names and typed arguments only. Unknown operations, unknown fields, non-canonical paths, symlink escapes, invalid package names, and arguments outside an operation's policy cause rejection.

## 10. Explicit Security Boundaries

The assistant cannot:

- execute arbitrary shell input as root;
- change disk partitions, filesystems, LUKS configuration, boot entries, kernel command lines, or user accounts;
- install AUR packages;
- execute instructions copied from logs, files, web pages, or package metadata;
- read SSH private keys, browser profiles, `.env` files, password stores, or unrelated home-directory content;
- run a mutating action when the UI is closed or approval has expired.

Diagnostic content is treated as untrusted data, not instructions. The local redactor removes tokens, credentials, user names, home-directory prefixes, and network addresses before an API request. The UI lets the user inspect the exact diagnostic payload.

The OpenAI API key belongs to the user, is entered after installation, and is stored through the desktop Secret Service. It is absent from installer configuration, logs, profile files, and Git.

## 11. Failure Handling

Every multi-step operation has a recorded state and a final verification step.

- An unavailable API produces an explanatory read-only error.
- A malformed or schema-invalid model response performs no action.
- A rejected policy check explains which requested capability is unavailable.
- A failed snapshot cancels the mutation.
- A failed package or configuration action records command output, runs verification, and offers rollback.
- A failed rollback stops further operations and presents manual recovery information; it does not repeatedly retry destructive steps.
- A failed installation does not reboot automatically and preserves the installation log and last successful stage.

Logs intended for sharing must pass the same redaction layer as API payloads.

## 12. Updates and Distribution

Installer and profile releases are immutable, versioned, and signed. The bootstrap documentation downloads a named release plus its verification material; it does not execute an unversioned branch head.

Installed components may check for release metadata but do not update automatically. The user sees the target version, release notes, profile diff, and available recovery point before approving an update.

The MVP uses official Arch repositories. AUR integration and a dedicated package repository remain outside scope. NVIDIA stays explicitly experimental until the real-hardware test matrix demonstrates repeatability.

## 13. Test Strategy

### Unit tests

Cover installer configuration generation, hardware-profile selection, profile merging, idempotency decisions, path validation, secret redaction, tool schemas, policy enforcement, and verification-result handling.

### Integration tests

Apply profiles to disposable filesystem roots, validate generated configuration, exercise Git history, create and roll back test snapshots, and verify privileged-helper request handling without exposing a general shell interface.

### QEMU tests

Automate installation on a blank virtual disk with UEFI firmware. Verify partitioning, LUKS unlock, Btrfs mounts, systemd-boot, user login, networking services, Hyprland session configuration, and assistant startup.

The destructive path is tested only against disposable virtual disks whose identity and size are asserted before each run.

### Assistant tests

Use recorded API responses for normal and hostile cases: malformed JSON, unknown tools, extra fields, unsafe paths, prompt injection in logs, secrets in diagnostic input, API timeouts, and interrupted execution. A separate optional smoke test uses a live API key.

### Real-hardware tests

Before MVP release, complete at least one successful install on Intel graphics and one on AMD graphics. NVIDIA receives a separate experimental result and must not alter the Intel/AMD path.

## 14. MVP Acceptance Criteria

The MVP is ready for use by friends when all of the following are true:

- a blank QEMU machine installs and boots repeatedly through the supported path;
- full-disk selection requires explicit device-name confirmation;
- LUKS-on and explicitly selected LUKS-off installations pass boot tests;
- reapplying the same system profile produces no unintended changes;
- Hyprland starts with the expected modular configuration;
- `Super+G` opens the assistant panel;
- no mutating assistant action succeeds without current explicit approval;
- secrets do not appear in normal configuration, Git, logs intended for sharing, or API payloads;
- malformed and hostile model outputs are rejected without side effects;
- a failed configuration change can be rolled back to its recorded recovery point;
- Intel and AMD real-hardware installations pass; and
- documentation clearly labels whole-disk erasure, unsupported dual boot, snapshot limitations, and experimental NVIDIA support.

## 15. Evolution Toward a Public Product

The MVP architecture preserves several later options without implementing them now:

- replace the privileged helper with Rust while keeping the typed protocol;
- add a custom signed package repository;
- produce an `archiso`-based branded image;
- expand hardware detection and automated testing;
- add dual boot only as a separately designed and heavily tested feature;
- add an optional service proxy for managed billing without changing BYOK support.

None of these are prerequisites for the initial private release.

## 16. Official References

- Archinstall guided installation and configuration: <https://archinstall.archlinux.page/installing/guided.html>
- Hyprland configuration: <https://wiki.hypr.land/Configuring/Start/>
- OpenAI Responses API and function tools: <https://developers.openai.com/api/reference/cli/resources/responses/methods/create>
