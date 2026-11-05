#!/bin/bash
# Floci, package, pyifc generate, terraform init + apply, smoke test. Re-runnable:
# floci-up.sh recreates the container (memory storage), so a run starts from nothing.
# The output is also written, sanitised, to logs/$UP_LOG (default up-run.log).
# A failing init, apply or smoke test makes the script exit non-zero (pipefail).
set -euo pipefail
. "$(dirname "$0")/env.sh"
exec > >(sanitize | tee "$EV/${UP_LOG:-up-run.log}") 2>&1
step() { printf '\n== %s\n' "$*"; }
step "floci"; "$HERE/floci-up.sh"
step "package + generate"; "$HERE/generate.sh"
step "terraform init + apply"
cd "$GEN"
export TF_DATA_DIR="$GEN/.terraform"
terraform init -input=false -no-color | tail -n 3
terraform apply -input=false -auto-approve -no-color 2>&1 \
  | { grep -vE 'Still creating|Refreshing|Creating\.\.\.|: Creation complete|Modifications complete' || true; } | tail -n 25
step "verify"
"$HERE/verify.sh"
