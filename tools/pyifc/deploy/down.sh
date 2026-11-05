#!/bin/bash
# Tear down: remove the pyifc-floci container, the Lambda containers it started (names
# are named pyifc-floci--<function>-<id>), the pyifc-floci-net network and .build/ (Terraform state, plugin
# download, package). Floci keeps everything in memory, so removing the container removes the
# deployed stack; no `terraform destroy` is run (the buckets carry prevent_destroy).
# Nothing else is touched: other containers, the shared Lambda base image and logs/ stay.
set -u
. "$(dirname "$0")/env.sh"
owned_containers | xargs docker rm -f >/dev/null 2>&1
docker network rm "$FLOCI_NET" >/dev/null 2>&1
rm -rf "$BUILD"
echo "left over:"
owned_containers
docker network ls --format '{{.Name}}' | grep -E "^$FLOCI_NET"
lsof -iTCP:"$FLOCI_PORT" -sTCP:LISTEN
true
