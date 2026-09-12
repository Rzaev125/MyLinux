# Arch + Hyprland + GPT Installer Delivery Roadmap

**Spec:** `docs/superpowers/specs/2026-09-12-arch-hyprland-gpt-installer-design.md`

The approved specification contains four independently testable subsystems. Each receives its own implementation plan so discoveries in the installer do not invalidate assistant or UI work.

## Phase 1 — Safe installer and Hyprland profile

Deliver a Python package that runs from the official Arch ISO, validates the environment and target disk, produces versioned `archinstall` config/creds files, installs the approved Btrfs/LUKS layout, and applies an idempotent base Hyprland profile.

Exit gate: unit tests pass, upstream `archinstall --dry-run` accepts both encrypted and unencrypted fixtures, and a disposable UEFI VM reaches the greetd login screen.

Detailed plan: `docs/superpowers/plans/2026-09-12-installer-profile-foundation.md`

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

