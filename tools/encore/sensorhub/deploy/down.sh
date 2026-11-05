#!/bin/sh
# Destroy what up.sh created: terraform destroy, then Floci, network, image and generated state.
set -u
here=$(cd "$(dirname "$0")" && pwd)
gen="$here/generated"
if [ -f "$gen/terraform.tfstate" ]; then
	(cd "$gen" && terraform destroy -input=false -auto-approve)
fi
docker rm -f floci-encore >/dev/null 2>&1 || true
docker network rm encore-sensorhub-net >/dev/null 2>&1 || true
docker rmi sensorhub-encore:floci >/dev/null 2>&1 || true
rm -rf "$gen/.terraform" "$gen"/terraform.tfstate* "$gen/.terraform.lock.hcl"
echo "left over (should be empty):"
docker ps -a --format '{{.Names}}' | grep -E '^(floci-encore|sensorhub-encore-app)$' || true
docker network ls --format '{{.Name}}' | grep -x encore-sensorhub-net || true
docker images --format '{{.Repository}}:{{.Tag}}' | grep -x 'sensorhub-encore:floci' || true
