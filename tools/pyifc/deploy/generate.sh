#!/bin/bash
# generate.sh [APP_PY [OUT_DIR [LOCK_FILE [PACKAGE_ZIP]]]]
# Package the port that holds APP_PY, run `pyifc generate`, then the workarounds.
# Defaults: the port in tools/pyifc/sensorhub, $GEN, $LOCK, $PKG. The lock file lives in
# $BUILD so the port directory stays clean; it is created on the first run and reused.
# SKIP="01" leaves a workaround out (to reproduce the failure it fixes).
set -euo pipefail
. "$(dirname "$0")/env.sh"
APP_PY="${1:-$APP}"; OUT="${2:-$GEN}"; LOCK_FILE="${3:-$LOCK}"; PKG_FILE="${4:-$PKG}"
SKIP="${SKIP:-}"
mkdir -p "$BUILD" "$OUT"
"$VENV_PY" "$HERE/package.py" "$ROOT" "$PKG_FILE" "$(dirname "$APP_PY")"
INIT=; [ -f "$LOCK_FILE" ] || INIT=--init-lock
"$VENV_PY" -m pyifc.cli generate "$APP_PY" --out "$OUT" --lock "$LOCK_FILE" $INIT \
  --endpoint "$ENDPOINT" --package "$PKG_FILE"
for w in "$HERE"/workarounds/*.py; do
  [ -e "$w" ] || continue
  id=$(basename "$w" | cut -c1-2)
  if [[ " $SKIP " == *" $id "* ]]; then echo "workaround $id SKIPPED"; continue; fi
  "$VENV_PY" "$w" "$OUT/app.tf.json" "$ENDPOINT" "$LAMBDA_ENDPOINT"
done
