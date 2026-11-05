#!/usr/bin/env sh
# Build and start the sensor hub on Docker. UI: http://localhost:${FRONTEND_PORT:-8000}/ui/
# Host ports can be changed with FRONTEND_PORT, STORE_PORT, STORE_CONSOLE_PORT, QUEUE_PORT.
set -eu
cd "$(dirname "$0")"
docker compose -f compose.yaml build api
docker compose -f compose.yaml up --build -d --wait
echo "Frontend:  http://localhost:${FRONTEND_PORT:-8000}/ui/"
echo "API:       http://localhost:${FRONTEND_PORT:-8000}/readings"
echo "RustFS UI: http://localhost:${STORE_CONSOLE_PORT:-9001} (sensorhub-admin / sensorhub-admin-secret)"
