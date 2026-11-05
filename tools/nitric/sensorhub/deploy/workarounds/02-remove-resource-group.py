#!/usr/bin/env python3
"""WORKAROUND 02: delete the aws_resourcegroups_group resource from the generated
Terraform (modules/stack/main.tf).

Why : Nitric's `stack` module always creates a Resource Group with a tag query.
      Floci 2.4.0 answers CreateGroup with 400 "Only service-configured capacity
      reservation pools are supported", so `terraform apply` ends with one error.
      Nothing else in the generated code references the group (checked with grep).
Fault: Floci (does not implement tag-query resource groups). Nitric's output is valid AWS.
Usage: 02-remove-resource-group.py <generated stack dir>
"""
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
f = next(root.glob("assets/*/nitric_modules/stack/main.tf"))
t = f.read_text()
new = re.sub(r'resource "aws_resourcegroups_group" "group" \{.*?\nJSON\n  \}\n\}\n', '# workaround 02: aws_resourcegroups_group removed (Floci cannot create it)\n', t, flags=re.S)
if new == t and "workaround 02" not in t:
    sys.exit("resource group block not found")
f.write_text(new)
print("workaround 02 applied to", f)
