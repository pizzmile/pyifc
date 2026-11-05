#!/bin/sh
# Deploy the Encore sensor hub on Floci: model -> compile.py -> encore build docker -> terraform apply.
# Env: WORKAROUNDS="all" (default) | "none" | "01 03" (numbers of workarounds/NN-*), APP_PORT=8080,
#      FLOCI_IMAGE=floci/floci:latest
set -eu
here=$(cd "$(dirname "$0")" && pwd)
app_dir=$(cd "$here/.." && pwd)
gen="$here/generated"
net=encore-sensorhub-net
floci=floci-encore
floci_port=4571
app_port=${APP_PORT:-8080}
image=sensorhub-encore:floci
workarounds=${WORKAROUNDS:-all}

# 1. Floci on host port 4571 (only if our container is not already there)
if ! docker ps --format '{{.Names}}' | grep -qx "$floci"; then
	if lsof -iTCP:$floci_port -sTCP:LISTEN >/dev/null 2>&1; then
		echo "port $floci_port is in use by something else" >&2; exit 1
	fi
	docker network inspect $net >/dev/null 2>&1 || docker network create $net >/dev/null
	docker run -d --name $floci --network $net --network-alias $floci -p $floci_port:4566 \
		-e FLOCI_HOSTNAME=$floci -e FLOCI_STORAGE_MODE=memory \
		-e FLOCI_SERVICES_IAM_ENABLED=true -e FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true \
		-e FLOCI_SERVICES_IAM_SEED_DEPLOYER_PRINCIPAL=true \
		"${FLOCI_IMAGE:-floci/floci:latest}" >/dev/null
fi
until curl -fsS "http://localhost:$floci_port/_floci/health" >/dev/null 2>&1; do sleep 1; done
curl -s "http://localhost:$floci_port/_floci/health" | sed 's/.*"version":"\([^"]*\)".*/Floci version: \1/'

# 2. export the model, compile it
mkdir -p "$gen"
(cd "$app_dir" && encore debug meta -f json) >"$gen/model.json"
python3 -I "$here/compile.py" --model "$gen/model.json" --out "$gen" --host-port "$app_port" --network $net --image $image
python3 -I "$here/compile.py" --model "$gen/model.json" --out "$gen" --host-port "$app_port" --network $net --image $image --check

# 3. Encore builds the image, bound to the generated infra config
(cd "$app_dir" && encore build docker --arch arm64 --config "$gen/infra-config.json" $image)

# 4. workarounds (each is a Terraform override file), then apply
rm -f "$gen"/[0-9][0-9]-*.tf.json
for f in "$here"/workarounds/*.tf.json; do
	n=$(basename "$f" | cut -c1-2)
	case " $workarounds " in *" all "*|*" $n "*) cp "$f" "$gen/"; echo "workaround applied: $(basename "$f")" ;; esac
done
cd "$gen"
terraform init -input=false >/dev/null
terraform apply -input=false -auto-approve

# 5. the infra config's predicted ARNs/URLs must equal what Terraform created
terraform output -json >outputs.json
python3 -I - <<'PY'
import json
cfg = json.load(open("infra-config.json")); out = json.load(open("outputs.json"))
want = {v["arn"] for v in cfg["pubsub"][0]["topics"].values()} | \
       {s["url"] for v in cfg["pubsub"][0]["topics"].values() for s in v["subscriptions"].values()}
got = {v["value"] for k, v in out.items() if k.startswith(("topic_arn_", "queue_url_"))}
assert want == got, (want ^ got)
print("infra-config ARNs/URLs match terraform outputs")
PY

# 6. wait for the API
until curl -fs "http://localhost:$app_port/readings" >/dev/null 2>&1; do sleep 1; done
echo "API ready: http://localhost:$app_port   (smoke test: ../smoke.sh http://localhost:$app_port)"
