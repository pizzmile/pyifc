#!/usr/bin/env sh
# Post one sample reading of each kind and wait until both appear in GET /readings.
set -eu
cd "$(dirname "$0")"
BASE="${BASE:-http://localhost:${FRONTEND_PORT:-8000}}"
DATA=../src/data

post() {
  code=$(curl -s -o /tmp/sensorhub-post.json -w '%{http_code}' -X POST "$BASE/readings" \
    -H 'Content-Type: application/json' --data-binary "@$1")
  [ "$code" = 202 ] || { echo "POST $1 returned $code: $(cat /tmp/sensorhub-post.json)"; exit 1; }
  echo "accepted $1 -> $(cat /tmp/sensorhub-post.json)"
}

post "$DATA/speed/reading-002.json"
post "$DATA/line-voltage/reading-002.json"

for _ in $(seq 1 30); do
  body=$(curl -s "$BASE/readings?limit=100")
  speed=$(printf '%s' "$body" | grep -c '"kind":"speed"' || true)
  volt=$(printf '%s' "$body" | grep -c '"kind":"line_voltage"' || true)
  if [ "$speed" -ge 1 ] && [ "$volt" -ge 1 ]; then
    echo "PASS: both kinds processed"
    printf '%s\n' "$body"
    exit 0
  fi
  sleep 1
done
echo "FAIL: processed readings did not appear within 30 s"
exit 1
