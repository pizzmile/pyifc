# Sourced by up.sh / down.sh / verify.sh. Documented AWS provider settings only:
# dummy credentials and AWS_ENDPOINT_URL (all AWS SDK calls of the Terraform AWS
# provider go to Floci). Not real credentials.
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1
export AWS_ENDPOINT_URL=http://localhost:4570
export AWS_S3_USE_PATH_STYLE=true
# NITRIC_VENV_BIN: bin folder of a Python 3.12 venv with the Nitric SDK (see ../README.md).
if [ -n "${NITRIC_VENV_BIN:-}" ]; then export PATH="$NITRIC_VENV_BIN:$PATH"; fi
unset AWS_PROFILE
HERE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE:-$0}")" && pwd)
WORK="$HERE/.work/app"
GEN="$WORK/cdktf.out/stacks/sensorhub-tf"
