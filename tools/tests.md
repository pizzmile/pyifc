# Change tests: scenarios, captures, measures, results

> This file is the source of the tests. `tools/tests/ifctests.py` reads the tables marked `<!-- table: name -->`, builds the expected outcome of every scenario from them, analyzes the captured data of a run against it, and writes the results block at the end of this file. Edit scenarios here, not in the script.

## 1. Baseline: what the sensor hub does

Ground truth for every scenario. Names are logical ids; each tool port uses its own names (Nitric `speed-readings`, Encore `sensorhub-speed`) and the extractors map them. Capabilities: `read`, `write` (buckets), `publish`, `subscribe` (topics). `read` includes listing.

<!-- table: baseline -->
| Type | Item |
|---|---|
| res | `bucket:staging` |
| res | `bucket:results` |
| res | `topic:speed` |
| res | `topic:line-voltage` |
| use | `ingestion>bucket:staging:write` |
| use | `ingestion>topic:speed:publish` |
| use | `ingestion>topic:line-voltage:publish` |
| use | `speed-parser>topic:speed:subscribe` |
| use | `speed-parser>bucket:staging:read` |
| use | `speed-parser>bucket:results:write` |
| use | `line-voltage-parser>topic:line-voltage:subscribe` |
| use | `line-voltage-parser>bucket:staging:read` |
| use | `line-voltage-parser>bucket:results:write` |
| use | `query>bucket:results:read` |

A use is `component>resource:capability`. Four components, four resources, ten uses.

## 2. Scenarios

The oracle of a scenario is the baseline plus the delta: what the code uses and which resources it needs after the change. It states the truth of the code, not what a tool should print. Delta syntax: `+res X`, `-res X`, `+use U`, `-use U`, separated by `;`.

Kinds, and the rule each is judged by:

<!-- table: kinds -->
| Kind | Question | Pass rule |
|---|---|---|
| follow | Does the tool's model change by exactly the edit? | Model equals the oracle, and so does the export when captured |
| detect | Does the tool notice code and declaration disagreeing? | Model equals the oracle and nothing dangles, or a signal is raised at any stage |
| protect | Is state kept when a name changes? | No destroy or replace of a bucket in the plan, or a signal at edit or generation |
| control | Is a rerun without change a no-op? | Model equals the oracle and the plan has no changes |

Status `defined` means the oracle, the edits and the required phases are complete. `proposed` means the oracle is written, the edits are not.

<!-- table: scenarios -->
| ID | Kind | Status | Change | Oracle delta | Phases |
|---|---|---|---|---|---|
| T0 | control | defined | No change, generate again | none | P2 P3 P4 P6 |
| T1 | follow | defined | Add a third bucket `archive`; ingestion writes to it | `+res bucket:archive; +use ingestion>bucket:archive:write` | P2 P3 P4 P6 |
| T2 | detect | defined | Query writes to staging without having declared that access | `+use query>bucket:staging:write` | P2 P3 P4 P6 |
| T2b | detect | defined | Query writes to staging through a handle declared read-only | `+use query>bucket:staging:write` | P2 P3 P4 P6 |
| T3 | follow | defined | Add `archive`, then drop the permission and the write together; the bucket stays declared | `+res bucket:archive` | P2 P3 P4 P6 |
| T4 | detect | defined | Add `archive`, drop only the write call, keep the permission | `+res bucket:archive` | P2 P3 P4 P6 |
| T5 | detect | defined | New topic `audit` with a subscriber and no publisher | `+res topic:audit; +use audit-parser>topic:audit:subscribe` | P2 P3 P4 P6 |
| T6 | protect | defined | Rename the staging bucket to `staging-v2` | `-res bucket:staging; +res bucket:staging-v2; -use ingestion>bucket:staging:write; +use ingestion>bucket:staging-v2:write; -use speed-parser>bucket:staging:read; +use speed-parser>bucket:staging-v2:read; -use line-voltage-parser>bucket:staging:read; +use line-voltage-parser>bucket:staging-v2:read` | P2 P3 P4 P6 |
| N1 | detect | proposed | Query reads staging through a helper in a package shared with ingestion | `+use query>bucket:staging:read` | P2 P3 P4 |
| N2 | detect | proposed | Declare `archive` inside an `if` on an environment variable, set at run time; ingestion writes to it | `+res bucket:archive; +use ingestion>bucket:archive:write` | P2 P3 P4 |
| N3 | detect | proposed | Query writes to staging with a direct cloud SDK call, not the tool's handle | `+use query>bucket:staging:write` | P2 P3 P4 |
| N4 | detect | proposed | T2b deployed on Floci with IAM enforcement on (Nitric) | `+use query>bucket:staging:write` | P2 P3 P5 P6 |
| N5 | detect | proposed | T2b with one image per service (Encore `--services`) | `+use query>bucket:staging:write` | P2 P3 P5 P6 |

T3 and T4 have the same oracle. The code is identical; only the leftover permission differs, and that is what T4 measures.

## 3. Edits per tool

What is changed in the tool's sensor hub port (`tools/nitric/sensorhub`, `tools/encore/sensorhub`) for each defined scenario. Each edit is applied to a scratch copy; the port itself is never changed. The edits are code: `tools/tests/edits/nitric.py` and `tools/tests/edits/encore.py`, one function per row, exact string replacements that stop the run if an anchor is missing. A unit test applies every edit to a clean copy of both ports.

<!-- table: edits -->
| ID | Nitric | Encore |
|---|---|---|
| T0 | none | none |
| T1 | `bucket("archive").allow("write")` helper in `resources.py`, called in `services/ingestion.py` | `NewBucket("sensorhub-archive")` in `resources/`; ingestion takes a `BucketRef[Uploader]` and uploads one object |
| T2 | `services/query.py` writes `query-audit/last.json` to `bucket("staging")` created without `.allow()`; the SDK has no `.file()` on such a handle, so the request fails with a 500 | `query/` takes a staging `BucketRef[Uploader]` and uploads `query-audit/last.json` before listing; a failed upload returns 503 |
| T2b | same write through `bucket("staging").allow("read")`; `nitric start` does not enforce the read-only grant, so the object is written | same as T2 (the read-only handle case does not exist in Encore) |
| T3 | `bucket("archive")` declared in `resources.py` and created in `services/ingestion.py`; no `.allow()`, no write | `NewBucket("sensorhub-archive")` in `resources/`; nothing references it |
| T4 | `bucket("archive").allow("write")` helper called in `services/ingestion.py`; no write call | as T3, plus a `BucketRef[Uploader]` for it in `ingestion/` that nothing uses |
| T5 | `topic("audit-events")` and a new subscriber service `services/audit.py`; no `.allow("publish")` anywhere | topic `sensorhub-audit` in `resources/` and a subscription in a new `audit/` package; no `Publish` call |
| T6 | rename `staging` to `staging-v2` in `resources.py` (two helpers) | rename `sensorhub-staging` to `sensorhub-staging-v2` in `resources/` |

## 4. Phases and captures

Every scenario passes through the same phases. For each phase one normalized file is stored (format in section 6), so the same comparison code runs for both tools.

<!-- table: captured -->
| Phase | What is captured | Nitric source | Encore source | File |
|---|---|---|---|---|
| P1 code | Oracle of the scenario | this file | this file | none, built from section 2 |
| P2 model | Resources and uses as the tool sees them | `nitric spec`: resources, topic subscriptions and one policy per principal, action and resource | `encore debug meta -f json` (buckets, topics, subscriptions, per-service bucket operations) and the bindings `encore build docker` asks for without an infra config | `p2_model.json` |
| P3 export | Resources and uses as written into infrastructure code | `cdk.tf.json` and the policy modules in `assets/`, from `nitric up -s tf` | `main.tf.json` and `infra-config.json` from `deploy/compile.py` (ours, not Encore's) | `p3_export.json` |
| P4 plan | Changes against the baseline state | `terraform plan -json` | `terraform plan -json` | `p4_plan.json` |
| P5 deployed | Resources and policies attached on Floci | IAM and resource listing of the Floci account | same | `p5_deployed.json` |
| P6 behavior | Does the changed app still run; is a forbidden call refused | `smoke.sh` on `nitric start`; `GET /readings` after it; `.nitric/` state (request history, logs, the spec the runtime used) | `smoke.sh` on `encore run`; `GET /readings` after it; server log | `p6_behavior.json` |
| signals | Every diagnostic about the change, with the stage it appeared in | CLI output per stage | CLI output per stage | `signals.json` |

Mapping used by the extractors: S3 `GetObject` and `ListBucket` to `read`, `PutObject` to `write`, SNS `Publish` to `publish`, a subscription or queue consumer to `subscribe`. Encore `READ_OBJECT_CONTENTS` and `LIST_OBJECTS` to `read`, `WRITE_OBJECT` to `write`. Image digests and role-list order are filtered from P4 before it is stored, and the number filtered is recorded.

Signals are extracted by `extract.py` with fixed rules (docstring of `signals()`): a failed `check` stage where the baseline's check passed; a failed spec, generate, model or export stage; a warning or error line in a stage log that the same tool's T0 does not print (Docker/pip build output excluded); a traceback or 5xx in the run log. Stages, in order: `edit`, `generation`, `plan`, `deploy`, `runtime`. Every case also records which of these stages ran (`unobserved` in section 5).

## 5. Measures

Computed by the script from the captured files and the oracle. Every name below is a key of the metrics output, and a unit test fails if the table and the script disagree.

<!-- table: measures -->
| Name | Definition | Phases |
|---|---|---|
| model_missing_uses | Uses in the oracle that the model lacks (the code will fail at run time) | P1, P2 |
| model_extra_uses | Uses in the model that the oracle lacks (over-grant) | P1, P2 |
| model_missing_resources | Resources in the oracle that the model lacks | P1, P2 |
| model_extra_resources | Resources in the model that the oracle lacks | P1, P2 |
| lost_in_export | Items in the model that the export lacks | P2, P3 |
| gained_in_export | Items in the export that the model lacks | P2, P3 |
| lost_in_deploy | Items in the export that the deployed state lacks | P3, P5 |
| gained_in_deploy | Items in the deployed state that the export lacks | P3, P5 |
| dangling_subscriptions | Topics in the oracle with a subscriber and no publisher | P1 |
| unused_resources | Resources in the oracle that no use refers to | P1 |
| destructive | Destroy or replace of a bucket in the plan | P4 |
| first_signal | Earliest stage with a signal, or `never` | signals |
| unobserved | Signal stages (`edit`, `generation`, `plan`, `runtime`) whose capture did not run, from `stages.json` | stages |
| behavior_ok | Whether the changed app ran correctly, `null` if not captured | P6 |
| outcome | For detect scenarios: tracked, tracked and signalled, divergent and signalled, divergent and silent, divergent with stages not observed | all |
| verdict | pass, fail, missed, inconclusive or incomplete, by the rule of the scenario's kind | all |

Divergent means the model differs from the oracle or a topic dangles. Verdicts:

- `pass`: the expected behavior was observed.
- `fail` (follow, control): the tool's output is wrong against the oracle. This is a result about the tool.
- `missed` (detect, protect): a signal was expected, every stage that could show one was observed, and none came. For protect only a signal from the tool at `edit` or `generation` counts. A destroy that only Terraform's plan shows does not pass, because any tool that exports Terraform has it, and for Encore the Terraform is ours; the result cell says "destroy visible in plan" and `destructive` lists it. This is a result about the tool relative to our criterion, not a defect claim.
- `inconclusive` (detect, protect): the same silence, but a stage that could have signalled was not observed (skipped or never run). The experiment cannot tell silence from absence, so nothing is claimed about the tool.
- `incomplete`: a phase file listed in the scenario's Phases column is missing.

`deploy` is not built, so every result is limited to the stages up to `plan` and `runtime`.

## 6. Running scenarios, data format and commands

`tools/tests/run.py` copies the tool's port to `tools/tests/.build/<tool>/<ID>/app`, applies the edit, runs the stages the scenario's Phases column needs, and stores every log and artifact under `tools/tests/runs/<run-id>/<tool>/<ID>/` (`stages.json`, `source.patch`, `raw/`). The run data is committed except `plan-input/`, `generated/assets/`, the Terraform working files in `generated/` and `plan.json` (it holds Floci's generated keys); `.build/` and `.baseline/` are not. It parses nothing; section 4 says what is later extracted from it.

| Stage | Phase | Nitric | Encore |
|---|---|---|---|
| check | edit-time signal | `mypy` found on PATH (the harness does not install it), else skipped; the unchanged baseline already reports 3 errors in `core/query.py`, so only new error lines count | `encore test ./...` |
| spec / model | P2 | `nitric spec -o raw/spec.json` | `encore debug meta -f json` to `raw/model.json` |
| inventory | P2 | none | `encore build docker` without an infra config: its log lists the buckets, topics and subscriptions Encore wants bound (exit 1 is the normal result) |
| generate / export | P3 | `nitric up -s tf`; the stack goes to `raw/generated/` | `compile.py` to `raw/generated/` |
| plan | P4 | workaround 02 and a backend patch on a copy, `terraform plan -refresh=false` against the baseline state; writes `plan.json` and `plan-summary.json` | the workaround files of `deploy/workarounds` copied in (bucket names follow a rename), `terraform plan -refresh=false` against the baseline state; same outputs |
| run | P6 | `nitric start`, API port found with `lsof`, then `smoke.sh` and a `GET /readings` | `encore run` on a free port, then `smoke.sh` and a `GET /readings` |
| deploy | P5 | not implemented | not implemented |

Nitric needs two scratch-only changes in the copy, applied by the runner: workaround 01 (import path) and a Dockerfile that installs `requirements.txt` before copying the sources. `--no-builder` does not skip image builds; the Dockerfile is what makes later builds fast (first scenario about 4.5 minutes, the rest 7 to 40 seconds per step). The model of T0 built this way equals the model built with Nitric's own Dockerfile. Nitric image builds leave images in the local Docker store.

The baseline state for `plan` is the T0 export deployed once on Floci: `run.py nitric --baseline` generates T0, applies it (workaround 02, backend patch) and keeps the state in `tools/tests/.baseline/nitric`; `run.py encore --baseline` runs `deploy/up.sh`, whose state stays in `deploy/generated`. Without a baseline `plan` is skipped and `stages.json` says so. Three things the plan stage had to deal with:

- Nitric's `cdk.tf.json` hard-codes an absolute local backend path in the scenario's build directory, so the runner points it at `terraform.tfstate` in the plan directory (the stored export is unchanged).
- Nitric's export names the exact image ids it was built from, so a Nitric export can only be planned while its images exist: the plan stage runs `generate` again right before `plan` (`--stages generate,plan --add`).
- Floci's ECR registry keeps repositories in a Docker volume shared by every Floci on the machine; leftovers of a failed baseline make the next apply fail with `RepositoryAlreadyExists`. The baseline command deletes only the repositories this app creates.

The plan has a noise floor. Planning the unchanged baseline directory against its own state already shows changes: Encore replaces `docker_container.app` (`network_mode "bridge" -> null`), Nitric replaces four `docker_registry_image` and four `docker_tag` resources and updates four Lambdas (`runs/2026-11-04/<tool>/T0/raw/plan-baseline-self.txt`). A scenario's own changes are what its plan shows beyond that set.

A stage whose dependency failed is skipped. A non-zero smoke result is recorded, not treated as a runner error.

Format of the files the analysis reads:

A run is a folder `tools/tests/runs/<run-id>/<tool>/<ID>/` holding the files named in section 4. An optional `tools/tests/runs/<run-id>/run.json` records dates and tool versions. The newest run per tool is used in the results.

```
p2_model.json, p3_export.json, p5_deployed.json
  {"resources": ["bucket:staging", ...], "uses": ["query>bucket:results:read", ...]}
p4_plan.json
  {"filtered": 4, "changes": [{"resource": "bucket:staging", "action": "destroy"}]}
  actions: create, update, destroy, replace
p6_behavior.json
  {"ok": true, "note": "POST 202 twice, GET 200"}
signals.json
  [{"stage": "generation", "text": "E_UNDECLARED_USE ..."}]
```

Run scenarios and keep the raw output (nothing is parsed yet):

```
python3 -I tools/tests/run.py encore all --run 2026-11-05          # every defined scenario
python3 -I tools/tests/run.py nitric T2 T6 --run 2026-11-05        # selected scenarios
python3 -I tools/tests/run.py nitric --list                        # stages each scenario would run
python3 -I tools/tests/run.py encore T1 --stages model,export      # only some stages
python3 -I tools/tests/run.py nitric --baseline --run 2026-11-04     # deploy T0 on Floci, keep the state for plan
python3 -I tools/tests/run.py nitric T6 --run 2026-11-04 --stages generate,plan --add   # add stages to an existing run
```

Analyze and report:

```
python3 -I tools/tests/ifctests.py spec               # validate this file, print scenarios as JSON
python3 -I tools/tests/ifctests.py oracle T6          # expected resources and uses of one scenario
python3 -I tools/tests/ifctests.py analyze <run>/<tool>   # metrics of every scenario in a run
python3 -I tools/tests/extract.py 2026-11-04          # raw output of a run -> p2/p3/p4/p6/signals files
python3 -I tools/tests/ifctests.py report             # rewrite the results block below
python3 -I tools/tests/ifctests.py report --check     # fail if the results block is out of date
python3 -m unittest discover -s tools/tests
```

## 7. What is not built yet

- Extractors for the deployed state (`p5`). `tools/tests/extract.py` writes `p2`, `p3`, `p4`, `p6` and `signals.json` from a run.
- Signal rules are textual (warning and error lines compared with T0, numbers ignored): a new error that differs from a baseline one only by digits is not seen.
- The common form keeps four capabilities per bucket or topic and drops IAM detail that does not change them (for example `ListBucket` next to `GetObject`, SQS `DeleteMessage`, Nitric's `states:*` on topics). A new action such as `DELETE_OBJECT` or `s3:*` would be ignored by `capability()`; none occurs in the 16 cases (checked by hand).
- The `deploy` stage (P5) and the scenarios that need it (N4, N5).
- Plans cover the scenarios that ask for P4 (all eight). The noise floor is filtered by resource type in `extract.py` (`NOISE`); a different Docker provider version may need other types.
- Scenario runs share the machine's local ports and Docker; run one tool at a time. Nitric `up` leaves its images in the local Docker image store.
- Floci request logs may show the calling identity of each call; if they do, they can check the oracle for P6. Not verified.

## 8. Results

Generated by `ifctests.py report`. Do not edit between the markers.

<!-- results:start -->
Latest run per tool: encore `2026-11-04`, nitric `2026-11-04`, pyifc `2026-11-05`.
Run `2026-11-04`: date 2026-11-04, encore encore version v1.58.6, nitric 1.61.1, terraform Terraform v1.16.4.
Run `2026-11-05`: date 2026-11-05, encore encore version v1.58.6, nitric 1.61.1, pyifc 0.1.0, terraform Terraform v1.16.4.

| ID | Kind | Change | Encore | Nitric | Pyifc |
|---|---|---|---|---|---|
| T0 | control | No change, generate again | pass | pass | pass |
| T1 | follow | Add a third bucket `archive`; ingestion writes to it | pass | pass | pass |
| T2 | detect | Query writes to staging without having declared that access | pass (tracked) | pass (divergent and signalled) | pass (signalled at generation) |
| T2b | detect | Query writes to staging through a handle declared read-only | pass (tracked) | MISSED (divergent and silent) | pass (signalled at generation) |
| T3 | follow | Add `archive`, then drop the permission and the write together; the bucket stays declared | pass | pass | pass |
| T4 | detect | Add `archive`, drop only the write call, keep the permission | MISSED (divergent and silent) | MISSED (divergent and silent) | pass (divergent and signalled) |
| T5 | detect | New topic `audit` with a subscriber and no publisher | MISSED (divergent and silent) | MISSED (divergent and silent) | pass (signalled at generation) |
| T6 | protect | Rename the staging bucket to `staging-v2` | MISSED (no tool signal; destroy visible in plan) | MISSED (no tool signal; destroy visible in plan) | pass |

| Tool | pass | fail | missed | inconclusive | incomplete | scenarios not run |
|---|---|---|---|---|---|---|
| encore | 5 | 0 | 3 | 0 | 0 | 0 |
| nitric | 4 | 0 | 4 | 0 | 0 | 0 |
| pyifc | 8 | 0 | 0 | 0 | 0 | 0 |

Findings:

- T4 encore: model has extra uses: `ingestion>bucket:archive:write`
- T5 encore: dangling subscriptions: `topic:audit`
- T6 encore: destructive plan: `bucket:staging destroy`
- T2 nitric: model lacks uses: `query>bucket:staging:write`
- T2 nitric: first signal at runtime
- T2 nitric: changed app did not run correctly
- T2b nitric: model lacks uses: `query>bucket:staging:write`
- T2b nitric: model has extra uses: `query>bucket:staging:read`
- T4 nitric: model has extra uses: `ingestion>bucket:archive:write`
- T5 nitric: dangling subscriptions: `topic:audit`
- T6 nitric: destructive plan: `bucket:staging destroy`
- T2 pyifc: first signal at edit
- T2 pyifc: stages not observed: plan
- T2 pyifc: changed app did not run correctly
- T2b pyifc: first signal at edit
- T2b pyifc: stages not observed: plan
- T2b pyifc: changed app did not run correctly
- T3 pyifc: first signal at edit
- T4 pyifc: model has extra uses: `ingestion>bucket:archive:write`
- T4 pyifc: export lost: `use:ingestion>bucket:archive:write`
- T4 pyifc: first signal at edit
- T5 pyifc: first signal at edit
- T5 pyifc: stages not observed: plan
- T6 pyifc: first signal at generation
<!-- results:end -->
