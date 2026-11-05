#!/usr/bin/env sh
# Stop the stack. Pass --volumes to also delete the stored readings.
set -eu
cd "$(dirname "$0")"
docker compose -f compose.yaml down "$@"
