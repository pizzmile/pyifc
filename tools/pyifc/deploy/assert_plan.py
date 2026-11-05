#!/usr/bin/env python3
"""Assertions on the trimmed rename plan (logs/rename-plan.json).

Usage: assert_plan.py TRIMMED_PLAN_JSON [APP_TF_JSON]

TRIMMED_PLAN_JSON is a list of {address, type, name, actions, previous_address} (the
resource_changes of `terraform show -json`). Exit 0 only if no aws_s3_bucket change has a
`delete` action (a replace counts) and at least one aws_s3_bucket change has a
previous_address (a Terraform `moved`). APP_TF_JSON, if given, supplies the number of
`moved` entries declared by the generator.
"""

import json
import sys

changes = json.load(open(sys.argv[1]))
declared = len((json.load(open(sys.argv[2])).get("moved") or [])) if len(sys.argv) > 2 else None
print(f"{'address':50} {'actions':22} previous_address")
for c in changes:
    actions = ",".join(c["actions"])
    if actions != "no-op" or c.get("previous_address"):
        print(f"{c['address']:50} {actions:22} {c.get('previous_address') or ''}")
buckets = [c for c in changes if c["type"] == "aws_s3_bucket"]
deleted = [c["address"] for c in buckets if "delete" in c["actions"]]
moved = [c for c in changes if c.get("previous_address")]
print(f"buckets in plan: {[c['address'] for c in buckets]}")
print(f"moved entries declared: {declared}; resource_changes with previous_address: {len(moved)}")
ok = True
if deleted:
    print(f"FAIL: bucket(s) with a delete action: {deleted}")
    ok = False
if not any(c["type"] == "aws_s3_bucket" for c in moved):
    print("FAIL: no moved aws_s3_bucket in the plan")
    ok = False
print("PASS: no aws_s3_bucket is deleted or replaced; moved entries are in the plan" if ok else "FAIL")
sys.exit(0 if ok else 1)
