# Deploying the Encore sensor hub on Floci

Date: 2026-11-04. Encore CLI 1.58.6 (SDK `encore.dev` 1.57.13), Terraform 1.16.4, AWS provider 5.88.0,
Docker provider (`kreuzwerker/docker`) 3.0.2, **Floci 2.4.0** (`floci/floci:latest`; the earlier run used 2.1.0).
Host: macOS arm64, image built with `--arch arm64`.

## Result

Two routes, each classed on its own.

| Route | Class | Why |
|---|---|---|
| 1. IaC: Encore's exported model, our compiler, one `terraform apply` onto Floci | **B** | Deploys and passes `smoke.sh`. Needs `compile.py` plus 3 workarounds (SNS/SQS endpoint, one extra IAM action, bucket DNS aliases). Zero workarounds fails at startup. |
| 2. Docker: whatever Encore emits to run the app in Docker alone | **C** | Encore emits only an image, no script, no Compose file. Built without an infra config the container exits (`no encore runtime or infra config provided`), and with one it needs real S3/SNS/SQS-compatible services behind it. |

The four services build into **one image** (`encore build docker` default). It runs as one container with one
set of AWS credentials.

## What Encore exports natively

| Command / output | What it gives | Usable for deployment? |
|---|---|---|
| `encore debug meta -f json` | The application model: services, endpoints, buckets, topics, subscriptions, publishers, per-service bucket operations, retry and ack settings. Works **offline**, no `encore run` needed (observed). 15 KB for this app: `model.json`. | As input only. It is data, not infrastructure. |
| `encore build docker [--config infra-config.json] [--services] [--gateways] IMAGE` | A Docker image (all services, listens on 8080). Without `--config` it refuses and lists the missing topics, buckets and subscriptions. | Yes, the one deployable artifact. |
| Infra config (`https://encore.dev/schemas/infra.schema.json`, docs "configure infra") | JSON that binds each declared bucket, topic and subscription by name to existing infrastructure. S3 section has `endpoint`, `region`, keys. `aws_sns_sqs` section has only ARNs and queue URLs, **no endpoint field**. | Yes, but you write it. Encore does not generate it. |
| `encore build docker --skip-config` | Image with no bindings. | No. Container exits at start (observed). |
| `encore run`, `encore test`, `encore check`, `encore exec`, `encore gen`, `encore debug build` | Local run, tests, client/wrapper code, compile check. | No. |
| `encore kubernetes configure`, `encore app`, `encore logs`, `encore secret` | Work against Encore Cloud environments. | No (no cloud). Not run. |
| Terraform output | None. Encore has no command that emits Terraform, Compose or a script. The Encore Terraform provider is data sources over Encore Cloud resources only (not re-checked). | No. |
| Encore Cloud provisioning (SNS/SQS/S3, per-service IAM) | Out of scope: no cloud. | Not usable. |

Docs and schema disagree on a subscription entry: docs say `{"url": <queue URL>}`, the schema says `arn`. `url` was
accepted at build time and worked at run time.

## What the compiler does

`compile.py` (Python 3 stdlib, run `python3 -I compile.py --model model.json --out generated`). Input: the model JSON only.
Output: `main.tf.json` (Terraform JSON) and `infra-config.json`. Sorted keys, deterministic. Environment values
(endpoints, region, account id, image tag, network, host port) are command line options. Unsupported things in the
model (databases, caches, cron, public or ordered topics) stop it with an error instead of being skipped.

| Model element | Terraform | infra-config |
|---|---|---|
| `buckets[]` | `aws_s3_bucket` (name = bucket name, `force_destroy`), versioning if `versioned` | `object_storage[s3].buckets[name]`, with Floci `endpoint` |
| `pubsub_topics[]` | `aws_sns_topic` | `pubsub[aws_sns_sqs].topics[name].arn` |
| `subscriptions[]` | `aws_sqs_queue` `<topic>-<sub>` (visibility = `ack_deadline`, retention = `message_retention`), a dead-letter queue with redrive after `max_retries + 1` receives, queue policy for SNS, `aws_sns_topic_subscription` (no raw delivery) | `subscriptions[name].url` |
| `svcs[].buckets[].operations` | one IAM user per service, policy built from the operations: `WRITE_OBJECT` to `s3:PutObject`, `READ_OBJECT_CONTENTS` to `s3:GetObject`, `LIST_OBJECTS` to `s3:ListBucket`, etc. | - |
| `topics[].publishers` | `sns:Publish` on that topic | - |
| `topics[].subscriptions[].service_name` | SQS receive/delete/visibility/attributes actions on that queue | - |
| (all services) | user `all-services` with the union, because one image is one process with one identity | - |
| the image | `docker_container.app` on the Floci network, port 8080, env `AWS_REGION` and the `all-services` access key from Terraform | `metadata` block |

The infra config needs no Terraform output: Floci ARNs and queue URLs follow from names. `up.sh` checks them against the
real Terraform outputs and fails if they differ. Order: export model, compile, `encore build docker --config`, then
`terraform apply` (the container resource references the built image tag). Floci itself is started by `up.sh`, outside
Terraform, since the AWS provider needs it first. `compile.py` also adds an empty `terraform_data.before_app` hook and two
empty locals (`workaround_env`, `workaround_deps`); the workarounds fill them.

Verifying the compiler: `python3 -I compile.py ... --check` (regenerates and diffs the files) and
`python3 -I test_compile.py` (5 tests: determinism, resource counts equal the model, infra-config names equal the model,
IAM actions come only from the model, unsupported models rejected). Both pass.

## What was run

```sh
cd tools/encore/sensorhub
encore debug meta -f json > deploy/model.json            # offline, 15 KB
deploy/up.sh                                             # everything below
#   docker network create encore-sensorhub-net
#   docker run -d --name floci-encore --network encore-sensorhub-net -p 4571:4566 \
#       -e FLOCI_HOSTNAME=floci-encore -e FLOCI_STORAGE_MODE=memory \
#       -e FLOCI_SERVICES_IAM_ENABLED=true -e FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true \
#       -e FLOCI_SERVICES_IAM_SEED_DEPLOYER_PRINCIPAL=true floci/floci:latest
#   python3 -I deploy/compile.py --model deploy/generated/model.json --out deploy/generated  (+ --check)
#   encore build docker --arch arm64 --config deploy/generated/infra-config.json sensorhub-encore:floci
#   cp deploy/workarounds/*.tf.json deploy/generated/ ; terraform init && terraform apply   # 30 resources
./smoke.sh http://localhost:8080
deploy/down.sh
```

Floci runs with IAM enforcement on (as in the earlier run), so the generated policies are actually checked.
`WORKAROUNDS=none deploy/up.sh` runs without any workaround. Everything generated stays in
`generated/`, which is git-ignored.

## Workarounds

All are Terraform files in `workarounds/`, copied next to the generated Terraform by `up.sh`. The order is the order in
which each was needed. Each was added only after the previous state failed.

| Id | What | Why (observed) | Whose fault |
|---|---|---|---|
| 01 `sns-sqs-endpoint` | Container env `AWS_ENDPOINT_URL_SNS` and `AWS_ENDPOINT_URL_SQS` pointing at Floci (standard AWS SDK variables). | The infra config has an `endpoint` for S3 but none for `aws_sns_sqs`. Without it the container panicked at start: `SNS GetTopicAttributes ... InvalidClientTokenId`, because the request went to the real SNS endpoint. That one rejected request left this machine with a Floci-issued fake key. | Encore (config schema gap). The SDK variables are documented AWS behavior, not an Encore option. |
| 02 `sns-get-topic-attributes` | One extra IAM policy on `all-services`: `sns:GetTopicAttributes` on the two topics. | Startup panic `User is not authorized to perform: sns:GetTopicAttributes`. Encore's runtime checks every declared topic at init. The model lists no such access. | Encore (the model under-reports what the runtime needs). Floci enforces correctly. |
| 03 `bucket-dns-aliases` | `local-exec` on `terraform_data.before_app`: reconnect the Floci container to the network with aliases `<bucket>.floci-encore`. | Encore's S3 client uses virtual-host addressing (`http://sensorhub-staging.floci-encore:4566/...`) and the config has no path-style option. POST returned 503 `staging_unavailable`, log: `lookup sensorhub-staging.floci-encore: no such host`. | Encore (no path-style switch). Not a Floci bug; Floci serves virtual-host S3 once the name resolves. |

Not workarounds: credentials come from `AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY` env set by Terraform (standard
SDK chain, keeps keys out of the image); the Floci start flags; `force_destroy` on buckets (works on Floci 2.4.0; the
earlier run reported a hang on 2.1.0).

Workaround 02 and the `Publish` policy: an `sns:Publish` policy scoped to the topic ARN worked on Floci 2.4.0, so the
`Resource: "*"` widening the earlier run needed on 2.1.0 is gone.

## Verification output

From the final clean `up.sh` run (Floci 2.4.0; 30 resources applied; `infra-config ARNs/URLs match terraform outputs`):

```
$ curl -X POST -H 'Content-Type: application/json' --data-binary @../../../sensorhub/src/data/speed/reading-001.json localhost:8080/readings
{"id":"f49750f4-87c1-4916-ae5f-ef6f264bda92"}  [202]
$ curl -X POST ... line-voltage/reading-001.json localhost:8080/readings
{"id":"4b48ae9f-2f05-4b1c-bc0e-fae8979794e3"}  [202]
$ curl localhost:8080/readings
{"readings":[{"id":"f49750f4-...","kind":"speed","fleet_id":"fleet-demo","asset_id":"train-001","timestamp":"2026-09-24T08:00:00Z","value":0,"unit":"km/h","status":"stopped"},
             {"id":"4b48ae9f-...","kind":"line_voltage","fleet_id":"fleet-demo","asset_id":"train-001","timestamp":"2026-09-24T08:00:00Z","value":229.8,"unit":"V","status":"voltage_observed"}]}
$ ./smoke.sh http://localhost:8080
ok   POST speed/reading-001.json -> 202 id=17440d75-...
ok   POST line-voltage/reading-001.json -> 202 id=7e9298fc-...
ok   malformed JSON -> 400
ok   unknown kind -> 422
ok   oversized payload (>4 KiB) -> 413
ok   both readings listed by GET /readings after 0s
PASS
```

Path taken: POST to the container, S3 put to `sensorhub-staging`, SNS publish, SQS delivery, the parser subscription,
S3 put to `sensorhub-results`, GET lists. All on Floci. Route 2 evidence: `encore build docker --skip-config`, then
`docker run` prints `encore runtime: fatal error: no encore runtime or infra config provided`.

Other observations: `aws_sqs_queue`/`aws_sqs_queue_policy` took 25-28 s each to create on Floci (a full apply is about 1 minute);
the Encore dev daemon (`encore daemon`, port 9400) that `encore debug meta` starts was stopped afterwards.

## Teardown

`deploy/down.sh`: `terraform destroy` (30 destroyed, app container included), `docker rm -f floci-encore`, remove network
`encore-sensorhub-net` and image `sensorhub-encore:floci`, delete Terraform state. Afterwards no container, network, image, listener on
4571/8080, or Terraform/Encore process of ours remained. Containers of other projects were not touched.

## Limits

- Per-service IAM users exist and their policies are derived from the model, but only `all-services` is used at run time
  (one image, one process). Least privilege per service was not exercised. A split deployment (`--services`, one image
  per service, service discovery in the infra config) was not tried.
- Dead-letter behavior (`max_retries + 1` receives is our reading of the model's retry policy) was created but not triggered.
- Floci is not AWS; nothing was checked against real AWS.
- Only buckets and Pub/Sub are compiled; the sensor hub has no database, cache, cron or secrets.
- The machine's Docker provider uses the default socket; the S3/SNS/SQS endpoint values and the container name `floci-encore`
  are fixed by `up.sh` and the workaround files.
- `deploy/model.json` records `uncommitted_changes: true` and an `app_revision` because the working tree was dirty; the compiler ignores both.
