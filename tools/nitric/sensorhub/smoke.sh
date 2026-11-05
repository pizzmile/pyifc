#!/bin/sh
# Smoke test for the Nitric sensor hub on `nitric start`.
# Usage: ./smoke.sh            (API_URL defaults to the first of ports 4000-4010 that serves GET /readings)
#        API_URL=http://localhost:PORT ./smoke.sh
# The port is the one `nitric start` assigned to the API; see README.
set -u

DIR=$(cd "$(dirname "$0")" && pwd)
DATA="$DIR/../../../sensorhub/src/data"
if [ -z "${API_URL:-}" ]; then
  # `nitric start` gives the API a port from 4000 upward; which one depends on the start.
  for port in 4000 4001 4002 4003 4004 4005 4006 4007 4008 4009 4010; do
    if curl -s -m 2 "http://localhost:$port/readings" | grep -q '"readings"'; then
      API_URL=http://localhost:$port
      break
    fi
  done
fi
API_URL=${API_URL:-http://localhost:4004}
FAILS=0

fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }
ok() { echo "ok:   $1"; }

# Send a sample with a fresh timestamp so it sorts newest-first even when the
# results bucket still holds records from earlier runs.
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
sample() {
    sed "s/\"timestamp\": *\"[^\"]*\"/\"timestamp\": \"$NOW\"/" "$1"
}

post() { # post <file> -> sets CODE and BODY
    RESP=$(sample "$1" | curl -s -w '\n%{http_code}' -X POST "$API_URL/readings" \
        -H 'Content-Type: application/json' --data-binary @-)
    CODE=$(printf '%s\n' "$RESP" | tail -n 1)
    BODY=$(printf '%s\n' "$RESP" | sed '$d')
}

post "$DATA/speed/reading-001.json"
[ "$CODE" = 202 ] && ok "speed POST -> 202" || fail "speed POST returned $CODE: $BODY"
SPEED_ID=$(printf '%s' "$BODY" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')

post "$DATA/line-voltage/reading-001.json"
[ "$CODE" = 202 ] && ok "line_voltage POST -> 202" || fail "line_voltage POST returned $CODE: $BODY"
VOLT_ID=$(printf '%s' "$BODY" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')

# Poll GET /readings until both ids show up (max 30 s).
if [ -n "$SPEED_ID" ] && [ -n "$VOLT_ID" ]; then
    i=0
    while [ $i -lt 30 ]; do
        LIST=$(curl -s "$API_URL/readings?limit=100")
        if printf '%s' "$LIST" | grep -q "$SPEED_ID" && printf '%s' "$LIST" | grep -q "$VOLT_ID"; then
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    if [ $i -lt 30 ]; then
        ok "both readings listed after ${i}s"
        printf '%s' "$LIST" | grep -q '"unit":"km/h"' && ok "speed record has unit km/h" ||
            fail "speed record unit missing"
        printf '%s' "$LIST" | grep -q '"unit":"V"' && ok "line_voltage record has unit V" ||
            fail "line_voltage record unit missing"
    else
        fail "readings not listed within 30 s"
    fi
else
    fail "no ids returned, skipping poll"
fi

# Negative checks: all must be 4xx.
expect4xx() { # expect4xx <label> <code>
    case "$2" in
        4??) ok "$1 -> $2" ;;
        *) fail "$1 returned $2 (expected 4xx)" ;;
    esac
}
send() { # send <body> -> CODE
    CODE=$(printf '%s' "$1" | curl -s -o /dev/null -w '%{http_code}' -X POST "$API_URL/readings" \
        -H 'Content-Type: application/json' --data-binary @-)
}

send '{"kind":"speed",'
expect4xx "malformed JSON" "$CODE"

send '{"kind":"humidity","fleet_id":"f","asset_id":"a","timestamp":"2026-09-24T08:00:00Z","value":1}'
expect4xx "unknown kind" "$CODE"

BIG=$(head -c 5000 /dev/zero | tr '\0' 'a')
send "{\"kind\":\"speed\",\"fleet_id\":\"$BIG\",\"asset_id\":\"a\",\"timestamp\":\"2026-09-24T08:00:00Z\",\"value\":1}"
expect4xx "oversized payload" "$CODE"

if [ "$FAILS" -eq 0 ]; then
    echo "PASS"
else
    echo "FAIL ($FAILS check(s) failed)"
    exit 1
fi
