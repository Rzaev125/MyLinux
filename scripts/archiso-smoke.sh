#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="$(mktemp -d /tmp/arch-hypr-smoke.XXXXXX)"
trap 'rm -rf -- "$venv_dir"' EXIT

python -m venv "$venv_dir/venv"
"$venv_dir/venv/bin/pip" install --no-deps "$repo_root"

for fixture in answers-amd-encrypted.json answers-intel-plain.json; do
  "$venv_dir/venv/bin/arch-hypr-installer" \
    --answers "$repo_root/tests/fixtures/$fixture" \
    --validate-upstream
done
