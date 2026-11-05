# Sourced by the scripts in this directory. Floci "pyifc-floci" on host port 4572;
# Terraform runs on the host. The generated provider block carries the dummy
# credentials floci/floci and the endpoint, so no AWS_* variable is needed for it;
# iam_probe.py and verify.sh use the same dummy credentials.
HERE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE:-$0}")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
FLOCI_NAME=pyifc-floci
FLOCI_NET=pyifc-floci-net
FLOCI_PORT="${FLOCI_PORT:-4572}"
ENDPOINT="http://localhost:$FLOCI_PORT"
# Address of Floci from inside the Lambda containers (same Docker network).
LAMBDA_ENDPOINT="http://$FLOCI_NAME:4566"
BUILD="$HERE/.build"
GEN="$BUILD/tf"
LOCK="$BUILD/pyifc.lock.json"
PKG="$BUILD/sensorhub.zip"
APP="$ROOT/tools/pyifc/sensorhub/app.py"
VENV_PY="${VENV_PY:-$ROOT/pyifc/.venv/bin/python}"
export PYTHONPATH="$ROOT/pyifc"
export AWS_ACCESS_KEY_ID=floci AWS_SECRET_ACCESS_KEY=floci AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
export AWS_ENDPOINT_URL="$ENDPOINT"
unset AWS_PROFILE
export TF_IN_AUTOMATION=1
EV="$HERE/logs"; mkdir -p "$EV"
# Log files must not carry personal absolute paths: repo root -> <repo>, any other home path -> <home>.
sanitize() { sed -e "s#$ROOT#<repo>#g" -e 's#/Users/[^/ "]*#<home>#g'; }
# Names of the containers these scripts own: the Floci container and its Lambda containers.
owned_containers() { docker ps -a --format '{{.Names}}' | grep -E "^$FLOCI_NAME(--.*)?\$" || true; }
