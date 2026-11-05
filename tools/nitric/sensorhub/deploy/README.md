# Deploying the Nitric sensor hub on Floci

Run on 2026-11-04. Nitric CLI 1.61.1, Python SDK 1.2.4, provider `nitric/awstf@1.28.4`
and `nitric/aws@1.28.4`, Terraform 1.16.4 (AWS provider 5.88.0), Pulumi 3.268.0,
Docker Desktop on arm64. **Floci image `floci/floci:latest` = version 2.4.0**
(label `version=2.4.0`, built 2026-10-31, revision `9be236e`). Nothing else was
used as an AWS stand-in; the credentials are the dummy values `test`/`test`.

The two routes from the brief are reported separately.

## Result

- **Route 1, IaC (Terraform): class B.** `nitric up -s tf` writes Terraform; applied to
  Floci with `AWS_ENDPOINT_URL` it deploys all 65 resources and the smoke test passes,
  with **2 workarounds** (01 app import path, 02 remove Resource Group).
- **Route 1, IaC (Pulumi): class C.** `nitric up -s pulumi` reaches Floci (it honours
  `AWS_ENDPOINT_URL`) but stops at the same Resource Group call. There is no generated
  code to patch, so the only fix would be a change to the provider binary.
- **Route 2, Docker/Compose: class C.** Nitric emits no Dockerfile-for-deploy, script or
  Compose file. `nitric run` only runs the service containers as a local runtime for as
  long as the CLI process lives. No Floci involved.

## Providers the CLI offers

`nitric stack new` (interactive only; refuses non-TTY, I drove it through a pty) lists
exactly six providers. The docs (nitric.io/docs/providers) also name "Local" (the
runtime behind `nitric start` / `nitric run`) and "Custom" (user-built); neither is
offered as a deploy target by the CLI. Kubernetes appears nowhere.

| Provider (CLI label) | Stack-file provider | Kind | Can target Floci? |
| --- | --- | --- | --- |
| Pulumi AWS | `nitric/aws@1.28.4` | direct deploy via Pulumi | Reaches Floci (env `AWS_ENDPOINT_URL`), fails at Resource Group. Observed. |
| Pulumi Google Cloud | (not shown) | direct deploy via Pulumi | No (GCP). Not tried. |
| Pulumi Azure | (not shown) | direct deploy via Pulumi | No (Azure). Not tried. |
| Terraform AWS (Preview) | `nitric/awstf@1.28.4` | writes Terraform, you apply | Yes, with workarounds 01 and 02. Observed. |
| Terraform Google Cloud (Preview) | (not shown) | writes Terraform | No (GCP). Not tried. |
| Terraform Azure (Preview) | (not shown) | writes Terraform | No (Azure). Not tried. |
| Local (docs only) | n/a | runtime, no export | No Floci; see Route 2. |

`nitric up` with the Terraform provider only generates; it never applies. `nitric down`
works only for the Pulumi providers (docs: "tear down with your IaC tooling").

## What was run

Stack files, both created by `nitric stack new` (only `region: us-east-1` filled in):
`../nitric.tf.yaml` and `../nitric.pulumi.yaml`.

Route 1 (Terraform), scripted in `up.sh` (about 7 min when image layers are cached,
about 12 min cold under amd64 emulation):

```sh
deploy/floci-up.sh                       # Floci 2.4.0 on :4570, container floci-nitric
deploy/workarounds/01-service-import-path.sh   # WORKAROUND 01 (copy of app in deploy/.work/app)
cd deploy/.work/app && nitric up -s tf --ci    # builds 4 amd64 images, writes cdktf.out/stacks/sensorhub-tf
python3 deploy/workarounds/02-remove-resource-group.py cdktf.out/stacks/sensorhub-tf   # WORKAROUND 02
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1 \
       AWS_ENDPOINT_URL=http://localhost:4570 AWS_S3_USE_PATH_STYLE=true
cd cdktf.out/stacks/sensorhub-tf && terraform init && terraform apply -auto-approve
```

The generated Terraform has no endpoint settings; pointing the AWS provider at Floci
is done only with the documented provider environment variables (`env.sh`). The
Terraform Docker provider builds the push to Floci's ECR from the generated code; that
is a normal resource of the stack, not a workaround. `AWS_S3_USE_PATH_STYLE` was set
from the start; I did not test whether it is needed.

Floci start (`floci-up.sh`, documented Floci settings, not a workaround): a user-defined
Docker network shared by Floci, its ECR registry and the Lambda containers
(`FLOCI_SERVICES_ECR_DOCKER_NETWORK`, `FLOCI_SERVICES_LAMBDA_DOCKER_NETWORK`,
`FLOCI_HOSTNAME`), plus unique names and ECR port 5110 so other Floci stacks on this
machine are untouched. Floci's own 503 message asks for this network. The first
attempt without it (plain `docker run -p 4570:4566`) failed at the image push (503 from
`000000000000.dkr.ecr.us-east-1.localhost:4570/v2/...`). Floci defaults otherwise:
memory storage, IAM enforcement off.

Route 1 (Pulumi), time-boxed single try:

```sh
export PULUMI_BACKEND_URL=file:///<tmp>/pulumi-state PULUMI_CONFIG_PASSPHRASE="" \
       AWS_ENDPOINT_URL=http://localhost:4570 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
cd deploy/.work/app && nitric up -s pulumi --ci     # workaround 01 already applied to this copy
```

Route 2: `nitric run --no-browser --ci` in the project, then `API_URL=http://localhost:4004 ./smoke.sh`.

## Zero-workaround attempts (in order)

| Step | Command | Outcome |
| --- | --- | --- |
| 1 | `nitric up -s tf` on the app as it is | stops during resource collection: `ModuleNotFoundError: No module named 'handlers'`, nothing generated |
| 2 | after workaround 01: `terraform apply`, Floci without the shared network | image push 503 (Floci config, fixed by `floci-up.sh`) and Resource Group 400 |
| 3 | Floci with shared network, `terraform apply` | everything created except `aws_resourcegroups_group` (400) |
| 4 | after workaround 02 | `Apply complete! Resources: 65 added` |

## Workarounds

| Id | What | Why | Fault |
| --- | --- | --- | --- |
| 01 `workarounds/01-service-import-path.sh` | Works on a copy of the app in `deploy/.work/app`; prepends `sys.path.insert(<project root>)` to each `services/*.py`. Originals untouched. | `nitric up` and `nitric run` build `python -u services/x.py` from `/` and ignore the `start:` line of `nitric.yaml` (`PYTHONPATH=.`, only used by `nitric start`). `from handlers import ...` then fails. | App layout plus Nitric (its Python image cannot take PYTHONPATH from `nitric.yaml`). Not Floci. Needed for every route. |
| 02 `workarounds/02-remove-resource-group.py` | Deletes the `aws_resourcegroups_group` block from generated `modules/stack/main.tf`. Nothing references it. | Floci 2.4.0 `CreateGroup` returns 400 "Only service-configured capacity reservation pools are supported". | Floci. Nitric's output is valid AWS. |
| 03 `workarounds/03-empty-buckets-before-destroy.sh` (teardown only) | `aws s3 rm --recursive` on the two buckets before `terraform destroy`. | Generated `aws_s3_bucket` has no `force_destroy`; DeleteBucket gives 409 BucketNotEmpty once data exists. Real S3 behaves the same. | Neither; module design. |

Not needed with Floci 2.4.0, although an earlier run on Floci 2.1.0 needed
them: the ECR `:4566` to registry-port rewrite (replaced by Floci's shared-network
setting), the `x86_64` architecture patch and `HONOUR_ARCHITECTURES` (the amd64 Lambda
images ran on this arm64 host with Floci 2.4.0 defaults; observed, cause not
investigated), avoiding `--no-builder` (not used), the Terraform state path redirect
(the generated backend path points into `deploy/.work/app`), and the SNS resource
widening (IAM enforcement was off here, so that case was **not** exercised; it may
return with `FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true`).

## Verification output

From `verify.sh`, Terraform deployment on Floci, API id
`aaeaefd9ea`. The URL form is Floci's REST-style path for the HTTP API
(`http://<id>.execute-api.localhost:4570/` did not work: it was routed to S3).

```
$ ../smoke.sh    (API_URL=http://localhost:4570/restapis/aaeaefd9ea/$default/_user_request_)
ok: speed POST -> 202 / line_voltage POST -> 202 / both readings listed after 0s
ok: speed unit km/h / line_voltage unit V / malformed JSON -> 400 / unknown kind -> 422 / oversized -> 413
PASS

$ curl -X POST $API_URL/readings --data @speed/reading-001.json         -> {"id":"c0104b6e-..."}  HTTP 202
$ curl -X POST $API_URL/readings --data @line-voltage/reading-001.json  -> {"id":"b872d4e7-..."}  HTTP 202
$ curl $API_URL/readings?limit=10
{"readings":[{"fleet_id":"fleet-demo","id":"c0104b6e-...","kind":"speed","unit":"km/h","value":0.0,"status":"stopped",...},
             {"fleet_id":"fleet-demo","id":"b872d4e7-...","kind":"line_voltage","unit":"V","value":229.8,"status":"voltage_observed",...}, ...]}
```

(the timestamp is replaced with the current time, as in `smoke.sh`). Route 2: `nitric run` with workaround 01 also passed the same smoke on
`http://localhost:4004` (containers started by the CLI);
on the unpatched app the containers crash with the same `ModuleNotFoundError`.

## Route 2 detail: why C

`nitric run` builds the four images (amd64), starts them as containers with
`SERVICE_ADDRESS=host.docker.internal:<port>`, and talks to a membrane server inside the
CLI process, which also hosts the local API (4004), topics and file-backed buckets
(`.nitric/run/`). It also started a `postgres:17.6` container (`nitric-sensorhub-local-sql`,
host port 5432) that the app does not use. Stopping the CLI stops everything. It writes
no Compose file or script to run elsewhere, and the docs list none. `nitric start` is the
same without Docker. So no deployable Docker artefact exists to apply.

## Teardown

`down.sh`: workaround 03, `terraform destroy` (`Destroy complete! Resources: 65
destroyed`), then removes `floci-nitric`, the ECR registry
container, any Lambda containers, the `floci-nitric-net` network, the `sensorhub_services-*`
images and `deploy/.work`. After the final run no container, network or listener of
mine remained (checked with `docker ps -a`, `docker network ls`, `lsof`). Two leftovers
had to be handled by hand: the Pulumi provider plugin process
(`~/.nitric/providers/nitric/aws-1.28.4`) stayed alive after `nitric up -s pulumi`
and held port 4000, so I killed it; and the Pulumi file backend was deleted. Left in place as shared tool
state: the `nitric` buildx builder container and the Pulumi plugin downloads under
`~/.pulumi` (I did not check which of them predate this run).

## Limits

- One clean end-to-end run of the Terraform route (plus two partial runs); no repeat
  of the Pulumi route beyond the first failure. Pulumi stops at its first error, so a
  second failure behind the Resource Group is not ruled out.
- IAM enforcement was off in Floci, so the generated IAM policies were created but not
  tested for denial or sufficiency.
- The API path form is Floci-specific; the real `execute-api` host cannot be used.
- Generated Terraform and the Nitric modules are not kept (about 240 KB, regenerated by
  `up.sh`; paths are absolute in `cdk.tf.json`). `.gitignore` covers `.nitric`,
  `cdktf.out`, state, `deploy/.work`.
- Nitric's Terraform providers are labelled Preview. Terraform warns about
  `aws_ssm_parameter.value` (write-only alternative); harmless.
- Timings: first cold `nitric up` about 5 to 6 min to build plus about 5 min to synth
  on this machine; the clean scripted run took 7 min. The 9 to 31 min of the earlier run
  were not reproduced.
