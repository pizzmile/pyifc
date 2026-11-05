# Sensor hub on pyifc

The sensor hub from `sensorhub/` as a pyifc
application. `app.py` is the only declaration: resources are objects, components
are decorated functions, permissions are decorators. The graph, the Terraform
JSON and the IAM policies are derived from it; no infrastructure file is
written by hand.

Python 3.11 or newer. pyifc is installed from `pyifc/` at the repository root.

## Run

```sh
python3 -m venv pyifc/.venv && pyifc/.venv/bin/pip install -e 'pyifc[dev]'
export PATH=$PWD/pyifc/.venv/bin:$PATH

cd tools/pyifc/sensorhub
pyifc check app.py                                   # diagnostics only
pyifc graph app.py -o /tmp/sensorhub-graph.json      # capability graph
pyifc run app.py --state /tmp/sensorhub-state --smoke smoke.py
pyifc generate app.py --out /tmp/sensorhub-tf --init-lock
python -m pytest tests -q
```

`pyifc run` starts nothing: it loads `app.py` in-process and calls the
components through `LocalApp`. Buckets are files under `--state`, topics are
in memory and `app.deliver()` drains them. Each component receives only the
resources it declared, wrapped so that a call outside its capabilities raises
`CapabilityError`. `smoke.py` posts one speed and one line-voltage sample
(`sensorhub/src/data/*/reading-001.json`), delivers, reads `GET /readings` and
writes `<state>/readings.json` with `{"http_status": 200}`.

`pyifc generate` writes `app.tf.json` and `diagnostics.json` to `--out` and the
lock file `pyifc.lock.json` next to `app.py` (`--lock` overrides). The first
generation needs `--init-lock`. `pyifc.toml` holds `name_prefix`, `region` and
`endpoint`; flags override it. `--package ZIP` sets `source_code_hash` from the
file; `--iam-mode declared` grants every declared capability instead of the
used ones. An `overlay.tf.json` in `--out` is never modified.

## Resources

| Resource id | Declaration | Physical name |
| --- | --- | --- |
| `sensorhub-staging` | `Bucket` | `${var.name_prefix}-staging` |
| `sensorhub-results` | `Bucket` | `${var.name_prefix}-results` |
| `sensorhub-speed` | `Topic` | `${var.name_prefix}-speed` |
| `sensorhub-line-voltage` | `Topic` | `${var.name_prefix}-line-voltage` |

The physical name is chosen at the first generation and recorded in the lock
file; a later rename of the id keeps it.

## Components and permissions

Every component has an explicit `id=`. Each capability is declared with a
decorator and checked against the calls in the function body (`pyifc check`).

| Component | Trigger | staging | results | speed topic | line-voltage topic |
| --- | --- | --- | --- | --- | --- |
| `ingestion` | `POST /readings` | write | - | publish | publish |
| `speed-parser` | speed topic | read | write | subscribe | - |
| `line-voltage-parser` | line-voltage topic | read | write | - | subscribe |
| `query` | `GET /readings` | - | list, read | - | - |

That is 10 uses in the common form. The graph keeps `list` apart from `read`,
so `query` has two edges on `sensorhub-results` (11 edges). A worker's
`subscribe` on its own trigger has provenance `both`. Every declared capability
here is also used, so `used` and `declared` IAM modes give the same policies.

## Differences from the Nitric and Encore ports

- The permissions are checked statically before anything runs: an undeclared
  use is `E_UNDECLARED_USE`, a declared capability without a use is a
  warning.
- The local runner enforces the declared capabilities at call time.
  `nitric start` did not stop a write through a read-only declaration (T2b,
  run 2026-11-04).
- Terraform is generated from the graph, with per-component IAM roles, event
  source wiring and API routes.
- Resource ids are tracked in a lock file, so renaming an id keeps the
  physical name and adds a Terraform `moved` entry instead of recreating the
  bucket.
- The business logic (`sensorhub_logic/`, schemas included) is the vanilla
  Python, not a rewrite. Messages are published to `Topic`s; subscribers
  receive the message bytes.
- `Bucket.put` stores objects as `application/json` (the adapter has no
  content-type argument).

## Limitations

- Local delivery is synchronous and in order, without retry, visibility
  timeout or dead-letter queue.
- The analyzer accepts a subset of Python: resources must be module-level
  names used directly in the component body, with no aliases or helper
  functions that receive a resource.
- Query lists and reads every object in the results bucket, as in the
  vanilla version.
- No dashboard.
- Not run for this port in this directory: `terraform plan`/`apply`, Floci,
  deployed IAM.

## Files and sources

| Path | Origin |
| --- | --- |
| `app.py` | the pyifc application: resources, components and capabilities; queues replaced by topics, oracle names |
| `sensorhub_logic/` | the sensor hub logic that `app.py` calls: ingestion, processing, query, schemas, HTTP types and the JSON schemas |
| `smoke.py` | new; samples are read from `../../../sensorhub/src/data` |
| `pyifc.toml` | new; `name_prefix`, `region` |
| `tests/test_port.py` | new; graph and smoke checks |
