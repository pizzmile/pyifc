#!/bin/bash
# Route 1 (IaC): Nitric awstf provider -> generated Terraform -> Floci.
# Steps: Floci, workaround 01, `nitric up -s tf`, workaround 02, terraform apply.
# Each workaround is a separate script in workarounds/; skip one with SKIP="01 02".
set -eu
. "$(dirname "$0")/env.sh"
SKIP="${SKIP:-}"
step() { printf '\n== %s\n' "$*"; }

step "floci"; "$HERE/floci-up.sh"

if [[ " $SKIP " == *" 01 "* ]]; then
  step "workaround 01 SKIPPED: using an unpatched copy"
  rm -rf "$WORK"; mkdir -p "$WORK"
  tar -C "$HERE/.." --exclude=./deploy --exclude=./.nitric --exclude=./cdktf.out -cf - . | tar -C "$WORK" -xf -
else
  step "workaround 01 (service import path)"; "$HERE/workarounds/01-service-import-path.sh"
fi

step "nitric up -s tf (builds 4 linux/amd64 images, writes Terraform; does not apply)"
( cd "$WORK" && nitric up -s tf --ci ) 2>&1 | grep -v 'In Progress' | grep -v '!!' | tail -n 15
test -f "$GEN/cdk.tf.json"

if [[ " $SKIP " != *" 02 "* ]]; then
  step "workaround 02 (remove resource group)"; python3 "$HERE/workarounds/02-remove-resource-group.py" "$GEN"
fi

step "terraform apply (provider pointed at Floci via AWS_ENDPOINT_URL)"
cd "$GEN"
terraform init -input=false -no-color | tail -n 3
terraform apply -input=false -auto-approve -no-color 2>&1 | grep -vE 'Still creating|Refreshing|Creating\.\.\.|: Creation complete|Modifications complete' | tail -n 25

step "API"
"$HERE/verify.sh" url
