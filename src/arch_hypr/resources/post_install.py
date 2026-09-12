from argparse import ArgumentParser
from pathlib import Path
import json
import os
import pwd
import re
import shutil
import subprocess


PAYLOAD = Path("/run/arch-hypr-installer/payload")
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SERVICE = re.compile(r"[A-Za-z0-9@_.-]+")
USER_OWNED = frozenset({Path(".config/hypr/local/overrides.lua")})


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def copy_tree(
    source: Path,
    destination: Path,
    *,
    preserve_existing: frozenset[Path] = frozenset(),
) -> None:
    for item in sorted(source.rglob("*")):
        if is_link(item):
            raise RuntimeError(f"source symlink is not allowed: {item}")
        relative = item.relative_to(source)
        target = destination / relative
        if is_link(target):
            raise RuntimeError(f"destination symlink is not allowed: {target}")
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif relative not in preserve_existing or not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target, follow_symlinks=False)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)
    for item in path.rglob("*"):
        if is_link(item):
            raise RuntimeError(f"home symlink is not allowed: {item}")
        os.chown(item, uid, gid, follow_symlinks=False)


def apply_payload(*, payload, root, username, account, run_command) -> None:
    payload = Path(payload)
    root = Path(root)
    if not USERNAME.fullmatch(username):
        raise RuntimeError("invalid username")
    plan = json.loads((payload / "profile-plan.json").read_text(encoding="utf-8"))
    if plan["username"] != username or plan["profile_version"] != 1:
        raise RuntimeError("profile plan identity mismatch")
    services = plan["services"]
    if not isinstance(services, list) or not all(
        isinstance(service, str) and SERVICE.fullmatch(service) for service in services
    ):
        raise RuntimeError("invalid service list")

    copy_tree(payload / "rootfs", root)
    home = root / Path(account.pw_dir).relative_to("/")
    copy_tree(payload / "home", home, preserve_existing=USER_OWNED)
    chown_tree(home / ".config", account.pw_uid, account.pw_gid)
    if services:
        run_command(["systemctl", "enable", *services], check=True, shell=False)
    version = root / "etc/arch-hypr/profile-version"
    version.parent.mkdir(parents=True, exist_ok=True)
    version.write_text("1\n", encoding="utf-8")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--username", required=True)
    username = parser.parse_args().username
    if not USERNAME.fullmatch(username):
        raise RuntimeError("invalid username")
    account = pwd.getpwnam(username)
    apply_payload(
        payload=PAYLOAD,
        root=Path("/"),
        username=username,
        account=account,
        run_command=subprocess.run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
