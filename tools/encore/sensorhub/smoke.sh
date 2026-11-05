#!/bin/sh
# Smoke test for the running sensor hub (`encore run`).
# Usage: ./smoke.sh [base-url]      default: http://localhost:4000 (or $BASE_URL)
set -u
here=$(cd "$(dirname "$0")" && pwd)
data="$here/../../../sensorhub/src/data"
base=${1:-${BASE_URL:-http://localhost:4000}}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

# post <file> -> prints the HTTP status, body goes to $tmp/body
post() {
	curl -s -o "$tmp/body" -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
		--data-binary "@$1" "$base/readings"
}

# expect4xx <name> <file>
expect4xx() {
	code=$(post "$2")
	case $code in
	4??) echo "ok   $1 -> $code" ;;
	*) echo "FAIL $1 -> $code (want 4xx)"; fail=1 ;;
	esac
}

id_of() { sed -n 's/.*"id" *: *"\([^"]*\)".*/\1/p' "$tmp/body"; }

# 1. one speed and one line-voltage sample
ids=""
for f in "$data/speed/reading-001.json" "$data/line-voltage/reading-001.json"; do
	code=$(post "$f")
	id=$(id_of)
	if [ "$code" = 202 ] && [ -n "$id" ]; then
		echo "ok   POST $(basename "$(dirname "$f")")/$(basename "$f") -> 202 id=$id"
		ids="$ids $id"
	else
		echo "FAIL POST $f -> $code $(cat "$tmp/body")"; fail=1
	fi
done

# 2. negative checks
printf '{"kind":"speed",' >"$tmp/malformed.json"
expect4xx "malformed JSON" "$tmp/malformed.json"
printf '{"kind":"temperature","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1}' >"$tmp/unknown.json"
expect4xx "unknown kind" "$tmp/unknown.json"
{ printf '{"kind":"speed","fleet_id":"'; head -c 5000 /dev/zero | tr '\0' x; printf '","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1}'; } >"$tmp/big.json"
expect4xx "oversized payload (>4 KiB)" "$tmp/big.json"

# 3. poll GET /readings until both ids are listed (max 30 s)
if [ -n "$ids" ]; then
	n=0
	while [ $n -lt 30 ]; do
		curl -s "$base/readings" >"$tmp/list" || true
		missing=""
		for id in $ids; do grep -q "\"$id\"" "$tmp/list" || missing="$missing $id"; done
		[ -z "$missing" ] && break
		n=$((n + 1))
		sleep 1
	done
	if [ -z "$missing" ]; then
		echo "ok   both readings listed by GET /readings after ${n}s"
	else
		echo "FAIL not listed after 30 s:$missing"; fail=1
	fi
fi

[ $fail -eq 0 ] && echo PASS || echo FAIL
exit $fail
