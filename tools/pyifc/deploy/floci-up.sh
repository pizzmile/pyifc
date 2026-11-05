#!/bin/bash
# Starts the Floci container for this deployment: name pyifc-floci, host port 4572.
# Documented Floci settings only:
#  - IAM enforcement (FLOCI_SERVICES_IAM_*) so iam_probe.py can observe denials,
#  - a user-defined Docker network shared by Floci and the Lambda containers it starts
#    (FLOCI_SERVICES_LAMBDA_DOCKER_NETWORK), with unique names so no other Floci
#    instance on this machine is touched.
set -euo pipefail
. "$(dirname "$0")/env.sh"
IMAGE="${FLOCI_IMAGE:-floci/floci:latest}"
if lsof -iTCP:"$FLOCI_PORT" -sTCP:LISTEN >/dev/null 2>&1 && ! docker ps --format '{{.Names}}' | grep -x "$FLOCI_NAME" >/dev/null; then
  echo "port $FLOCI_PORT is in use by something else" >&2; exit 1
fi
docker network inspect "$FLOCI_NET" >/dev/null 2>&1 || docker network create "$FLOCI_NET" >/dev/null
# Remove the previous Floci container and its leftover Lambda containers (pyifc-floci--*).
owned_containers | xargs docker rm -f >/dev/null 2>&1 || true
# Floci is memory-backed: Terraform state from an earlier container is stale.
rm -f "$GEN"/terraform.tfstate "$GEN"/terraform.tfstate.backup 2>/dev/null || true
docker run -d --name "$FLOCI_NAME" --network "$FLOCI_NET" -p "$FLOCI_PORT":4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e FLOCI_HOSTNAME="$FLOCI_NAME" \
  -e FLOCI_STORAGE_MODE=memory \
  -e FLOCI_SERVICES_IAM_ENABLED=true \
  -e FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true \
  -e FLOCI_SERVICES_IAM_SEED_DEPLOYER_PRINCIPAL=true \
  -e FLOCI_SERVICES_LAMBDA_DOCKER_NETWORK="$FLOCI_NET" \
  -e FLOCI_SERVICES_LAMBDA_CONTAINER_NAME_PREFIX="$FLOCI_NAME-" \
  "$IMAGE" >/dev/null
ok=
for i in $(seq 1 60); do
  if curl -fs "$ENDPOINT/_floci/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
[ -n "$ok" ] || { echo "Floci did not become ready at $ENDPOINT" >&2; docker logs "$FLOCI_NAME" | tail -n 20 >&2; exit 1; }
echo "answers: $(curl -s "$ENDPOINT/_floci/health" | head -c 200)"
docker image inspect "$IMAGE" --format 'image {{.RepoTags}} version label {{index .Config.Labels "version"}}'
