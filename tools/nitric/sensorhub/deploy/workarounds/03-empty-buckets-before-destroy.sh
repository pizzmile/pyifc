#!/bin/sh
# WORKAROUND 03 (teardown only): empty the two S3 buckets before `terraform destroy`.
# Why  : the generated aws_s3_bucket has no force_destroy, so DeleteBucket fails with
#        409 BucketNotEmpty once the app has stored readings. Real S3 behaves the same.
# Fault: neither; it is how Nitric's Terraform module is written (Nitric `down` exists
#        only for Pulumi providers, for Terraform you destroy with Terraform).
# Needs: the aws CLI (v2) and Floci running; uses only AWS_ENDPOINT_URL and dummy keys.
set -eu
for b in $(aws s3api list-buckets --query 'Buckets[].Name' --output text); do
  case "$b" in staging-*|results-*) echo "emptying $b"; aws s3 rm "s3://$b" --recursive --quiet ;; esac
done
