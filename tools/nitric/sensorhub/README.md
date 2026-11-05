# Sensor hub on Nitric

The sensor hub from `sensorhub/` as a Nitric
Python application, run on Nitric's native local runtime (`nitric start`, no
Docker). It is a normal Nitric project: `nitric.yaml`, four services, resources
and permissions declared in code. No infrastructure files are written by hand.

Tested with Nitric CLI 1.61.1, Python SDK `nitric==1.2.4`, Python 3.12.

## Run

Create a virtualenv (outside the repo is fine) and put it first on `PATH`,
because `nitric.yaml` starts services with plain `python`:

```sh
python3.12 -m venv /path/to/nitric-venv
/path/to/nitric-venv/bin/pip install -r requirements.txt
export PATH=/path/to/nitric-venv/bin:$PATH

cd tools/nitric/sensorhub
nitric start          # terminal 1
./smoke.sh            # terminal 2
```

Python 3.14 does not work with SDK 1.2.4 (protobuf import fails); use 3.12.

### Ports

`nitric start` binds 4000 upward and 49152 for its dashboard. The API gets the
port after the service gateways, and which port that is varies between starts: with the
4 services here it was **4004** on two starts and **4000** on a later one. `smoke.sh` looks
for it among 4000-4010.
Dashboard: `http://localhost:49152`. The CLI output is not printed when stdout
is not a terminal; find the API port with `lsof -nP -iTCP -sTCP:LISTEN | grep nitric`
and set `API_URL=http://localhost:PORT ./smoke.sh` if it differs. Make sure
4000-4004 and 49152 are free first.

State is kept in `.nitric/` (git-ignored). Delete it for a clean start.

## Resources

| Resource | Nitric declaration | Note |
| --- | --- | --- |
| API `sensorhub` | `api("sensorhub")` | `POST /readings` (ingestion), `GET /readings` (query) |
| Staging bucket | `bucket("staging")` | raw JSON at `raw/<id>.json` |
| Results bucket | `bucket("results")` | processed record at `<id>.json` |
| Topic `speed-readings` | `topic(...)` | one subscriber: speed parser |
| Topic `line-voltage-readings` | `topic(...)` | one subscriber: line-voltage parser |

**Topics, not queues.** Nitric 1.2.4 queues are pull-only (`enqueue`/`dequeue`
called from code); they have no worker trigger. Topics have native subscribers,
so each kind gets its own topic and one subscriber service. Delivery semantics
differ from SQS queues (see limitations).

## Services and permissions

One service per component, so each is its own permission principal.

| Service (`services/`) | Trigger | staging | results | speed topic | line-voltage topic |
| --- | --- | --- | --- | --- | --- |
| `ingestion.py` | `POST /readings` | write | - | publish | publish |
| `speed_parser.py` | speed topic | read | write | subscribe | - |
| `line_voltage_parser.py` | line-voltage topic | read | write | - | subscribe |
| `query.py` | `GET /readings` | - | read | - | - |

Each service calls only the helpers in `resources.py` that carry the
`.allow(...)` it needs. Subscribing needs no `.allow`; Nitric adds the implied
invoke permission. `nitric.yaml` has no per-service permissions.

## Differences from the vanilla version

- Two topics with subscribers instead of two SQS-style queues; no visibility
  timeout, retry count or dead-letter queue is configured.
- Staged objects have no stored content type (Nitric buckets only read/write
  bytes); the parser treats staged objects as `application/json`.
- No ports/adapters layer and no Lambda or container entrypoints: each service
  is a few lines around the shared handlers.
- Query lists and reads every object in the results bucket, as in vanilla.
- No dashboard (see limitations).

## Limitations (observed)

- **Permissions are not enforced by `nitric start`.** A probe service declared
  with `bucket("results").allow("read")` and wrote to the bucket; the write
  succeeded. The `.allow` calls only become IAM policy at deployment (not
  tested here). So the table above is declared access, not verified denial.
  The probe was removed afterwards.
- **No retry on handler failure locally.** A line-voltage reading published to
  the speed topic was rejected by the speed parser's schema validation, logged
  as an error in `.nitric/services.log`, produced no result, and was delivered
  once only. The "wrong queue must fail validation" rule holds; retry/DLQ
  behaviour is a deployment-provider matter and not observed.
- **No dashboard.** The frontend needs `npm run build` and same-origin
  `/readings`; Nitric's API is on a different port and the build output must
  not live in this folder. Skipped.
- API port is not fixed (see Ports). The smoke script does not auto-detect it.
- The smoke test replaces the sample `timestamp` with the current time so the
  new records sort first even if `.nitric/` holds older records.
- Not run: `nitric up`, Terraform/Pulumi export, Docker, deployed IAM.

## Observed smoke output

`nitric start`, API on port 4004, run twice in a row:

```
ok:   speed POST -> 202
ok:   line_voltage POST -> 202
ok:   both readings listed after 0s
ok:   speed record has unit km/h
ok:   line_voltage record has unit V
ok:   malformed JSON -> 400
ok:   unknown kind -> 422
ok:   oversized payload -> 413
PASS
```

Also checked by hand: `limit=abc` -> 400, `fleet_id` without `asset_id` -> 400,
unknown fleet/asset pair -> 200 with empty list, missing `Content-Type` -> 415.

## Files and sources

| Path | Origin |
| --- | --- |
| `core/schemas.py`, `core/ingestion.py`, `core/processing.py`, `core/query.py`, `core/http_types.py` | copied unchanged from `sensorhub/src/backend/src/` (Nitric builds need them inside the project) |
| `schema/*.json` | copied from `sensorhub/src/backend/schema/` |
| `handlers.py` | new; reuses the vanilla `core/` functions instead of duplicating the logic |
| `resources.py`, `services/*.py` | new; topic names, one `.allow` helper per access, services split per component |
| `nitric.yaml`, `requirements.txt` | new; no `boto3`, plain `python` |
| `smoke.sh` | new; reads samples from `../../../sensorhub/src/data` |

If `sensorhub/src/backend/` changes, re-copy `core/` and `schema/`.
