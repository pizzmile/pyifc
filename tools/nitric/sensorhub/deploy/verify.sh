#!/bin/bash
# verify.sh url   -> print the API base URL on Floci
# verify.sh smoke -> run ../smoke.sh against it
# verify.sh manual -> post one speed + one line_voltage sample, read both back with GET /readings
set -eu
. "$(dirname "$0")/env.sh"
ID=$(cd "$GEN" && terraform show -json | python3 -c "
import json,sys
def walk(m):
    for r in m.get('resources',[]):
        if r['type']=='aws_apigatewayv2_api': print(r['values']['id']); return True
    return any(walk(c) for c in m.get('child_modules',[]))
walk(json.load(sys.stdin)['values']['root_module'])")
API_URL="http://localhost:4570/restapis/$ID/\$default/_user_request_"
case "${1:-url}" in
  url) echo "Terraform output of the API gateway id: $ID"; echo "API_URL=$API_URL" ;;
  smoke) API_URL="$API_URL" "$HERE/../smoke.sh" ;;
  manual)
    D="$HERE/../../../../sensorhub/src/data"
    S=$(ls "$D"/speed/*.json | head -1); V=$(ls "$D"/line-voltage/*.json | head -1)
    NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    for f in "$S" "$V"; do
      echo "\$ curl -X POST $API_URL/readings --data @$(basename "$(dirname "$f")")/$(basename "$f")"
      sed "s/\"timestamp\": *\"[^\"]*\"/\"timestamp\": \"$NOW\"/" "$f" | curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_URL/readings" -H 'Content-Type: application/json' --data-binary @-
    done
    for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
      R=$(curl -s "$API_URL/readings?limit=10"); n=$(printf '%s' "$R" | grep -o '"unit"' | wc -l); [ "$n" -ge 2 ] && break; sleep 2
    done
    echo "\$ curl $API_URL/readings?limit=10"; echo "$R" ;;
esac
