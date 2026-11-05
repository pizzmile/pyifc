# Deploying the pyifc sensor hub on Floci

Run on 2026-11-05. pyifc (`pyifc generate`, `pyifc.backends.aws.package`), port in
`tools/pyifc/sensorhub/`, Terraform 1.16.4 on the host (AWS provider 6.72.0, constraint
`~> 6.0`), Docker 28.5.1 on arm64. **Floci `floci/floci:latest` = version 2.4.0**. No other
AWS stand-in was used; the credentials are the dummy values `floci`/`floci` that
`pyifc generate --endpoint` writes into the provider block.

## Result

- **Deployment:** `up.sh` applies 28 resources (4 Lambdas, 4 roles with inline policies,
  2 buckets, 2 topics with Lambda subscriptions, HTTP API with 2 routes and a stage) with
  **1 workaround** (01, Lambda endpoint). About 90 s including the Lambda image start when
  the Lambda base image is cached. Floci IAM enforcement is on during the whole run.
- **Smoke (`verify.sh`):** POST speed -> 202, POST line voltage -> 202, malformed JSON -> 400,
  `GET /readings` returns both records.
- **Rename (`verify-rename.sh`):** renaming `sensorhub-staging` to `sensorhub-staging-v2`
  gives `Plan: 0 to add, 4 to change, 0 to destroy`; the bucket is `no-op` with
  `previous_address aws_s3_bucket.sensorhub_staging`; no bucket has a `delete` action.
  With `APPLY=1` the apply succeeds and an object put before the rename is still in the
  bucket.
- **IAM probe (`iam_probe.py`):** 9 allowed calls succeed, 11 forbidden calls return HTTP
  403, taken from the deployed role policies.

## Files

| Path | What |
| --- | --- |
| `env.sh` | names, port 4572, paths, `PYTHONPATH`; sourced by the scripts |
| `floci-up.sh` | starts container `pyifc-floci` (`floci/floci:latest`, host port 4572) |
| `package.py`, `requirements-lambda.txt` | Lambda package with `pyifc.backends.aws.package.build_package` |
| `generate.sh` | package, `pyifc generate`, workarounds |
| `up.sh` | `floci-up.sh`, `generate.sh`, `terraform init` and `apply`, `verify.sh` |
| `verify.sh` | smoke against the deployed API, writes `logs/verify-output.txt` |
| `verify-rename.sh` | T0 -> T6 rename, plan assertions |
| `iam_probe.py` | allow/deny probe, writes `logs/iam-probe.txt` |
| `down.sh` | stops and removes only what these scripts started (container names matched exactly) |
| `assert_plan.py`, `selftest-assert.sh`, `fixtures/` | rename-plan assertion; the self-test shows `verify-rename.sh` exits non-zero on a plan that deletes a bucket |
| `workarounds/` | numbered workarounds |
| `logs/` | git-ignored: output of the scripts, paths replaced by `<repo>` and `<home>` |
| `.build/` | git-ignored: Terraform state and provider, package, lock file, scratch copy |

## How to run

Needs Docker, Terraform on the host, and a venv with the port's dependencies:

```sh
python3 -m venv pyifc/.venv && pyifc/.venv/bin/pip install -e 'pyifc[dev]'   # repository root

tools/pyifc/deploy/up.sh              # Floci, package, generate, apply, smoke
pyifc/.venv/bin/python tools/pyifc/deploy/iam_probe.py
tools/pyifc/deploy/verify-rename.sh   # APPLY=1 also applies it (on a copy of the state)
tools/pyifc/deploy/selftest-assert.sh # assertion fails on a doctored plan
tools/pyifc/deploy/down.sh
```

The scripts run with `set -o pipefail`: a failing `terraform init`/`apply`/`plan`, smoke test or
rename assertion makes the script exit non-zero. The scripts that write logs replace the
repository root by `<repo>` and any other home directory by `<home>` before saving.

Floci start (`floci-up.sh`): `FLOCI_SERVICES_IAM_ENABLED=true`,
`FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true`, `FLOCI_SERVICES_IAM_SEED_DEPLOYER_PRINCIPAL=true`
(the seeded deployer principal is what Terraform and the probe's admin calls use), memory
storage, and a user-defined network `pyifc-floci-net` that Floci's Lambda containers join
(`FLOCI_SERVICES_LAMBDA_DOCKER_NETWORK`, container name prefix `pyifc-floci-`). Port 4572
and the names differ from the Nitric (4570) and Encore (4571) setups. A re-run of `up.sh`
recreates the container and removes the Lambda containers (`pyifc-floci--*`) left by a previous run,
so every run starts from an empty Floci.

What `up.sh` does:

```sh
pyifc/.venv/bin/python -m pyifc.cli generate tools/pyifc/sensorhub/app.py \
    --out deploy/.build/tf --lock deploy/.build/pyifc.lock.json --init-lock \
    --endpoint http://localhost:4572 --package deploy/.build/sensorhub.zip
python workarounds/01-lambda-endpoint.py ...           # WORKAROUND 01
cd deploy/.build/tf && terraform init && terraform apply -auto-approve
```

`--init-lock` is passed only while the lock file does not exist; the lock is kept in
`.build/` so the port directory stays clean. `package.py` installs the pinned
requirements for python3.12 on linux arm64 into a staging directory next to `app.py`,
`sensorhub_logic/` and `pyifc/` and zips it with `build_package` (same inputs, same bytes;
hash `/XtpJavMLGgzTEOVd826E5VKy2fZXBpb5754X1WnybQ=`, passed to `--package` so it becomes
`source_code_hash`). `boto3` comes with the Lambda runtime.

## Workarounds

| Id | What | Why | Fault |
| --- | --- | --- | --- |
| 01 `workarounds/01-lambda-endpoint.py` | Rewrites the 12 `*_ENDPOINT_URL` values in the `aws_lambda_function` environments of `app.tf.json` from `http://localhost:4572` to `http://pyifc-floci:4566` (Floci on the shared Docker network). The provider block keeps `localhost:4572`. | `--endpoint` sets one URL for the provider (Terraform on the host) and for the Lambda environments. Inside a Lambda container `localhost` is the container, so every boto3 call fails with `EndpointConnectionError` (`POST` -> 503 `staging_unavailable`, `GET` -> 500). `SKIP=01 ./up.sh` shows it: apply succeeds, smoke fails. | pyifc generator (one endpoint for two places). Not Floci. |

Not needed: any change to the generated Terraform for Floci 2.4.0 (all 28 resource types
are accepted), and `floci_sns_publish_wildcard` (an ARN-scoped `sns:Publish` grant works
under enforcement, see the IAM probe).

## Rename check (T6)

`verify-rename.sh` copies `app.py`, `pyifc.toml` and `sensorhub_logic/` to
`.build/rename/port`, replaces `"sensorhub-staging"` by `"sensorhub-staging-v2"` there
(a `sed`), regenerates with a copy of the lock, packages the copy, copies the T0 Terraform
state and runs `terraform plan -out` and `terraform show -json`. The assertions are in the
script: no `aws_s3_bucket` change has `delete` among its actions (a replace counts) and
at least one `aws_s3_bucket` change has a `previous_address`. The lock keeps the physical
name `sensorhub-staging` for the new id, and the generator writes

```json
"moved": [{"from": "aws_s3_bucket.sensorhub_staging", "to": "aws_s3_bucket.sensorhub_staging_v2"}]
```

(`logs/rename-moved.json`). The four Lambdas change in place: the new package hash and
the environment variable `PYIFC_BUCKET_SENSORHUB_STAGING` becoming
`PYIFC_BUCKET_SENSORHUB_STAGING_V2` for the two parsers and the ingestion function
(`logs/rename-plan.txt`). The `filename` change on the four Lambdas is only the path of the
scratch package of the check (`.build/rename/sensorhub.zip`), and `query` differs by package hash only.
`logs/rename-plan.json` (and `fixtures/rename-plan.json`, from the original run) holds the `resource_changes` of the plan (address, type, actions,
`previous_address`); `assert_plan.py` runs on that file. After the `APPLY=1` run, `verify.sh` passes again on the
renamed stack (`logs/verify-after-rename.txt`).

## IAM probe

For each role (`sensorhub-ingestion`, `-speed_parser`, `-line_voltage_parser`, `-query`) the
probe reads the inline policy that Terraform attached (`GetRolePolicy`), creates a temporary
IAM user with that document, creates an access key and makes real S3 and SNS calls. Cases
(`logs/iam-probe.txt` has the policies and the table):

| Role | Allowed | Forbidden (403) |
| --- | --- | --- |
| ingestion | PutObject staging; Publish speed topic; Publish line-voltage topic | GetObject results; PutObject results |
| speed_parser | GetObject staging; PutObject results | Publish line-voltage topic; Publish speed topic; PutObject staging |
| line_voltage_parser | GetObject staging; PutObject results | Publish speed topic; Publish line-voltage topic; PutObject staging |
| query | ListBucket results; GetObject results | PutObject results; GetObject staging; ListBucket staging |

S3 denials come as `AccessDenied`, SNS denials as `AuthorizationError`, both HTTP 403.
The users, access keys and probe objects are removed at the end. The allowed publishes
deliver a body that is not a job to the subscribed parsers, which reject it.

## Findings

- Terraform JSON `moved` blocks work: Terraform 1.16.4 reports `aws_s3_bucket.sensorhub_staging
  has moved to aws_s3_bucket.sensorhub_staging_v2` and plans it as a no-op; Floci 2.4.0 does not
  see a change. The `--state-mv` fallback in the plan was not needed.
- `pyifc generate --endpoint` uses one URL for the provider and for the Lambda environments
  (workaround 01). A separate option for the Lambda-side endpoint would remove it.
- `pyifc.backends.aws.package.build_package(requirements=...)` runs `pip install` for the build host.
  On macOS that would put macOS wheels (`rpds-py`, a dependency of `jsonschema`) into the
  Lambda package. `package.py` installs for `manylinux2014_aarch64`, cp312, itself and passes the
  directory as a source. pip evaluates environment markers for the build Python (3.14), so
  `typing_extensions`, which `referencing` needs on 3.12, was not installed and the
  function failed on import (`TypeError: 'default' is an invalid keyword argument for
  typevar()`); it is listed explicitly in `requirements-lambda.txt`.
- HTTP API URL on Floci 2.4.0: `http://localhost:4572/restapis/<id>/$default/_user_request_/`.
  `<id>.execute-api.localhost.floci.io:4572` did not connect here and
  `<id>.execute-api.localhost:4572` was routed to S3 (`NoSuchBucket` `readings`).
  Terraform's `api_endpoint` output (`https://<id>.execute-api.us-east-1.amazonaws.com`)
  is not reachable.
- Floci starts one container per Lambda concurrency use (several `pyifc-floci--<function>-<id>`
  containers per function appeared); `down.sh` removes them by name prefix.

## Limits

- One clean run of each script. Timings are from this machine, Lambda base image cached
  (`public.ecr.aws/lambda/python:3.12` was pulled on the first run and is left in place by `down.sh`).
- The probe tests the policy documents with temporary users; it does not show that Floci
  applies the Lambda function's role to calls made inside the function. The smoke test passes with
  enforcement on, which shows the used-only grants are sufficient for the paths it exercises
  (ingestion, both parsers, query), not for error paths.
- Floci does not check that an access key was issued by it; not re-tested here.
- `APPLY=1` changes the deployed Lambdas but not `.build/tf` state; use `down.sh` and
  `up.sh` afterwards.
- `down.sh` does not run `terraform destroy`: Floci is memory-backed, the buckets carry
  `prevent_destroy`, and removing the container removes the stack.
- The pinned requirements and Lambda runtime (python3.12, arm64) match the generated Terraform; Floci
  ran the arm64 image natively on this arm64 host.
