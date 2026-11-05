#!/bin/sh
# Starts the Floci container for this deployment: name floci-nitric, host port 4570.
# Only documented Floci settings are used (no change to Nitric output):
#  - a user-defined Docker network shared by Floci, its ECR registry sidecar and
#    the Lambda containers (Floci's own error message asks for this when it runs
#    in Docker: FLOCI_SERVICES_ECR_DOCKER_NETWORK),
#  - names/ports made unique so other Floci instances on this machine are not touched.
set -eu
PORT="${FLOCI_PORT:-4570}"
NET=floci-nitric-net
IMAGE="${FLOCI_IMAGE:-floci/floci:latest}"
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && ! docker ps --format '{{.Names}}' | grep -qx floci-nitric; then
  echo "port $PORT is in use by something else" >&2; exit 1
fi
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker rm -f floci-nitric >/dev/null 2>&1 || true
docker run -d --name floci-nitric --network "$NET" -p "$PORT":4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e FLOCI_HOSTNAME=floci-nitric \
  -e FLOCI_STORAGE_MODE=memory \
  -e FLOCI_SERVICES_ECR_DOCKER_NETWORK="$NET" \
  -e FLOCI_SERVICES_LAMBDA_DOCKER_NETWORK="$NET" \
  -e FLOCI_SERVICES_ECR_REGISTRY_CONTAINER_NAME=floci-nitric-ecr-registry \
  -e FLOCI_SERVICES_ECR_REGISTRY_BASE_PORT=5110 -e FLOCI_SERVICES_ECR_REGISTRY_MAX_PORT=5110 \
  -e FLOCI_SERVICES_LAMBDA_CONTAINER_NAME_PREFIX=floci-nitric- \
  "$IMAGE" >/dev/null
for i in $(seq 1 30); do curl -fs "http://localhost:$PORT/_floci/health" >/dev/null 2>&1 && break; sleep 1; done
docker exec floci-nitric env | grep '^FLOCI_VERSION'
docker image inspect "$IMAGE" --format 'image {{.RepoTags}} version label {{index .Config.Labels "version"}}'
