#!/bin/bash
# Proves that verify-rename.sh fails when the rename would delete a bucket: runs its
# assertion (ASSERT_ONLY mode) on the committed trimmed plan (must pass) and on a doctored
# copy in which the renamed bucket has actions ["delete","create"] (must exit non-zero).
# Needs no Floci. Nothing is written to logs/. The plan is fixtures/rename-plan.json.
set -euo pipefail
. "$(dirname "$0")/env.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
"$VENV_PY" - "$HERE/fixtures/rename-plan.json" "$T/doctored.json" <<'PY'
import json, sys
changes = json.load(open(sys.argv[1]))
for c in changes:
    if c["type"] == "aws_s3_bucket" and c.get("previous_address"):
        c["actions"] = ["delete", "create"]
json.dump(changes, open(sys.argv[2], "w"))
PY
echo "== original plan: expect exit 0"
ASSERT_ONLY="$HERE/fixtures/rename-plan.json" "$HERE/verify-rename.sh" | tail -n 2
echo "== doctored plan (bucket replaced): expect non-zero"
if ASSERT_ONLY="$T/doctored.json" "$HERE/verify-rename.sh"; then
  echo "SELFTEST FAIL: verify-rename.sh exited 0 on a plan that deletes a bucket"; exit 1
else
  echo "exit status $?: SELFTEST PASS"
fi
