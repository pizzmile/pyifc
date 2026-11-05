#!/bin/bash
# verify.sh [url|smoke]
#  url   -> print the API base URL on Floci
#  smoke -> (default) POST one speed and one line-voltage sample from sensorhub/src/data to
#           the deployed API, wait for the two topic deliveries, GET /readings; prints the
#           HTTP codes and the records, exits 1 unless both POSTs gave 202 and two records
#           came back. Output is also written, sanitised, to logs/$VERIFY_LOG (default verify-output.txt).
set -euo pipefail
. "$(dirname "$0")/env.sh"
ID=$(terraform -chdir="$GEN" output -raw api_id)
# Floci's path form for an HTTP API stage; the *.execute-api.<host> form does not resolve here.
API_URL="$ENDPOINT/restapis/$ID/\$default/_user_request_"
if [ "${1:-smoke}" = url ]; then echo "API_URL=$API_URL"; exit 0; fi
exec > >(sanitize | tee "$EV/${VERIFY_LOG:-verify-output.txt}") 2>&1
D="$ROOT/sensorhub/src/data"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "API id $ID"
fail=0
for kind in speed line-voltage; do
  f=$(ls "$D/$kind"/reading-001.json)
  echo "\$ curl -X POST \$API_URL/readings --data @$kind/reading-001.json"
  out=$(sed "s/\"timestamp\": *\"[^\"]*\"/\"timestamp\": \"$NOW\"/" "$f" |
        curl -s -m 60 -w '\nHTTP %{http_code}' -X POST "$API_URL/readings" -H 'Content-Type: application/json' --data-binary @- || true)
  echo "$out"
  case "$out" in *"HTTP 202") ;; *) fail=1 ;; esac
done
echo "\$ curl -X POST \$API_URL/readings --data '{'   (malformed; expect 400)"
curl -s -m 60 -w '\nHTTP %{http_code}\n' -X POST "$API_URL/readings" -H 'Content-Type: application/json' --data-binary '{' || true
n=0
for i in $(seq 1 10); do
  R=$(curl -s -m 30 "$API_URL/readings?limit=10" || true)
  n=$(printf '%s' "$R" | { grep -o '"unit"' || true; } | wc -l | tr -d ' ')  # grep exits 1 on no match
  [ "$n" -ge 2 ] && break
  sleep 2
done
echo "\$ curl \$API_URL/readings?limit=10"; echo "$R"
echo "records: $n"
if [ "$fail" = 0 ] && [ "$n" -ge 2 ]; then echo PASS; else echo FAIL; exit 1; fi
