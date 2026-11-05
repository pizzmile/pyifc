#!/bin/bash
# Tear down: terraform destroy on Floci, then remove every container/network/image this deployment created.
set -u
. "$(dirname "$0")/env.sh"
if [ -d "$GEN/.terraform" ] && curl -fs "$AWS_ENDPOINT_URL/_floci/health" >/dev/null 2>&1; then
  echo "== workaround 03 (empty buckets, teardown only)"; "$HERE/workarounds/03-empty-buckets-before-destroy.sh"
  ( cd "$GEN" && terraform destroy -input=false -auto-approve -no-color 2>&1 | tail -n 8 )
fi
docker ps -a --format '{{.Names}}' | grep -E '^floci-nitric' | xargs -r docker rm -f >/dev/null 2>&1
docker ps -a --format '{{.Names}}' | grep -E '^floci-aws-nitric-ecr-registry$' | xargs -r docker rm -f >/dev/null 2>&1
docker network rm floci-nitric-net >/dev/null 2>&1
docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^sensorhub_services-|localhost:4570|floci-nitric' | xargs -r docker rmi -f >/dev/null 2>&1
rm -rf "$HERE/.work"
echo "left over:"; docker ps -a --format '{{.Names}}' | grep -iE 'floci-nitric|floci-aws-nitric|nitric-sensorhub'; docker network ls --format '{{.Name}}' | grep floci-nitric; true
