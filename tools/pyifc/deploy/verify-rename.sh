#!/bin/bash
# T0 -> T6 rename on the deployed stack: renaming the id `sensorhub-staging` must not
# replace the bucket. Steps:
#  1. T0: apply the port as it is (up.sh) unless it is already deployed on Floci.
#  2. copy the port to .build/rename, sed `sensorhub-staging` -> `sensorhub-staging-v2`
#     in its app.py (T6), regenerate with a copy of the lock, package and Terraform state.
#  3. terraform plan -out, terraform show -json; assert_plan.py checks that no
#     aws_s3_bucket has a `delete` action (replace counts) and that moved entries are
#     reported (resource_changes with previous_address).
#  4. APPLY=1 only: apply the renamed plan to the copy of the state and check that an object
#     put in the staging bucket before the rename is still there. The main state in
#     .build/tf is then stale; run down.sh and up.sh afterwards.
# ASSERT_ONLY=<trimmed plan json> runs step 3's assertion on that file and nothing else
# (used by selftest-assert.sh). A failing plan, assertion or apply exits non-zero (pipefail).
# Logs (sanitised): logs/rename-*; log in logs/rename-run.log, or
# rename-apply-run.log with APPLY=1.
set -euo pipefail
. "$(dirname "$0")/env.sh"
assert_step() { "$VENV_PY" "$HERE/assert_plan.py" "$1" ${2:+"$2"} 2>&1 | sanitize | tee "$3"; }
if [ -n "${ASSERT_ONLY:-}" ]; then assert_step "$ASSERT_ONLY" "" /dev/null; exit; fi

exec > >(sanitize | tee "$EV/$([ "${APPLY:-}" = 1 ] && echo rename-apply-run.log || echo rename-run.log)") 2>&1
step() { printf '\n== %s\n' "$*"; }
R="$BUILD/rename"
step "T0 deployed?"
if curl -fs "$ENDPOINT/_floci/health" >/dev/null 2>&1 && [ -f "$GEN/terraform.tfstate" ] \
   && [ "$(terraform -chdir="$GEN" state list 2>/dev/null | wc -l)" -gt 0 ]; then
  echo "T0 state present"
else
  UP_LOG=up-run.log "$HERE/up.sh"
fi
BUCKET=$("$VENV_PY" - "$GEN/terraform.tfstate" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
print(next(i["attributes"]["bucket"] for r in state["resources"] if r["type"] == "aws_s3_bucket"
           and r["name"] == "sensorhub_staging" for i in r["instances"]))
PY
)
echo "staging bucket in T0: $BUCKET"
if [ "${APPLY:-}" = 1 ]; then
  "$VENV_PY" -c "
import boto3
boto3.client('s3', endpoint_url='$ENDPOINT').put_object(Bucket='$BUCKET', Key='rename-canary.txt', Body=b'T0')"
  echo "canary object put in $BUCKET"
fi

step "T6: rename sensorhub-staging -> sensorhub-staging-v2 in a scratch copy"
rm -rf "$R"; mkdir -p "$R/port"
cp -R "$ROOT/tools/pyifc/sensorhub/app.py" "$ROOT/tools/pyifc/sensorhub/pyifc.toml" \
      "$ROOT/tools/pyifc/sensorhub/sensorhub_logic" "$R/port/"
sed -i.bak 's/"sensorhub-staging"/"sensorhub-staging-v2"/' "$R/port/app.py"; rm "$R/port/app.py.bak"
diff "$ROOT/tools/pyifc/sensorhub/app.py" "$R/port/app.py" || true
cp "$LOCK" "$R/pyifc.lock.json"
"$HERE/generate.sh" "$R/port/app.py" "$R/tf" "$R/pyifc.lock.json" "$R/sensorhub.zip"
cp "$GEN/terraform.tfstate" "$R/tf/"; cp "$GEN/.terraform.lock.hcl" "$R/tf/"
cp -R "$GEN/.terraform" "$R/tf/.terraform"
echo "--- lock after the rename"; cat "$R/pyifc.lock.json"
echo "--- moved in app.tf.json"
"$VENV_PY" -c "import json;print(json.dumps(json.load(open('$R/tf/app.tf.json')).get('moved'),indent=1))" | tee "$EV/rename-moved.json"

step "terraform plan"
cd "$R/tf"
export TF_DATA_DIR="$R/tf/.terraform"
# The packaged copy sits at a different path than the T0 package, so `filename` shows as changed.
terraform plan -input=false -no-color -out=plan.bin 2>&1 | sanitize | tee "$EV/rename-plan.txt" | tail -n 40
terraform show -json plan.bin > "$R/plan.json"
"$VENV_PY" - "$R/plan.json" "$EV/rename-plan.json" <<'PY'
import json, sys
changes = json.load(open(sys.argv[1]))["resource_changes"]
trimmed = [
    {"address": c["address"], "type": c["type"], "name": c["name"],
     "actions": c["change"]["actions"], "previous_address": c.get("previous_address")}
    for c in changes
]
json.dump(trimmed, open(sys.argv[2], "w"), indent=1)
open(sys.argv[2], "a").write("\n")
PY

step "assertions"
assert_step "$EV/rename-plan.json" "$R/tf/app.tf.json" "$EV/rename-assert.txt"

if [ "${APPLY:-}" = 1 ]; then
  step "terraform apply of the renamed plan (scratch state)"
  terraform apply -input=false -no-color plan.bin 2>&1 | { grep -vE 'Still modifying|Modifying\.\.\.' || true; } | tail -n 15 | sanitize | tee "$EV/rename-apply.txt"
  "$VENV_PY" - "$BUCKET" <<PY | tee -a "$EV/rename-apply.txt"
import sys, boto3
s3 = boto3.client('s3', endpoint_url='$ENDPOINT')
body = s3.get_object(Bucket=sys.argv[1], Key='rename-canary.txt')['Body'].read()
print(f"canary object still in {sys.argv[1]} after the rename apply: {body!r}")
PY
fi
