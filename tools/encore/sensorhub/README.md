# Sensor hub on Encore (Go, native local runtime)

The sensor hub from `../../../sensorhub/` rebuilt as an
[Encore](https://encore.dev) application. Encore has no Python SDK, so this port is in Go.
It runs with `encore run`, which emulates the platform locally: API gateway on one port,
object storage on disk, in-memory Pub/Sub. Nothing is deployed and no infrastructure files
exist; it is a normal Encore app (`encore.app`) that a later phase can build or export.

## Run

Needs the Encore CLI (tested with v1.58.6) and Go (tested with 1.26.5). No login and no app
id are needed: `encore.app` has `"id": ""` and `encore run` works offline.

```sh
cd tools/encore/sensorhub
encore run --browser never          # API on :4000, dashboard on :9400
./smoke.sh                          # in a second terminal; optional arg: base URL
                                    # wait for "listening for incoming HTTP requests" first: the gateway answers 502 while the app is still compiling
encore test ./...                   # unit tests
encore debug meta -f json           # application model (with `encore run` going)
```

Ports observed: API `http://127.0.0.1:4000`, Encore dashboard `http://127.0.0.1:9400/<app>`,
MCP `:9900`. Port 8000 (used by another project) is not touched; use `encore run --port N`
if 4000 is taken. `encore run` also starts an `encore daemon` that keeps running afterwards;
stop it yourself if you started it (it holds port 9400).

## Layout

| Package | Kind | Role |
|---|---|---|
| `ingestion/` | service | `POST /readings` (raw endpoint). Validates, stages, publishes. |
| `speedparser/` | service | Subscription `speed-parser` on topic `sensorhub-speed`. |
| `voltageparser/` | service | Subscription `line-voltage-parser` on topic `sensorhub-line-voltage`. |
| `query/` | service | `GET /readings` (raw endpoint). Lists and reads results. |
| `resources/` | library | The two buckets and two topics (package-level declarations). Has no API, so it is not a service. |
| `reading/` | library | Types, embedded JSON schemas, validation and normalization; no resources. |

## Resources

- Buckets: `sensorhub-staging` (raw uploads, key `raw/<id>.json`), `sensorhub-results`
  (normalized records, key `<id>.json`, so a retry replaces the same object).
- Topics: `sensorhub-speed`, `sensorhub-line-voltage`, message `{id, staging_key}`, at-least-once.
  One subscription each. **Encore has no raw queue primitive**; a topic with exactly one
  subscription is the nearest equivalent (locally in-memory, on AWS SNS+SQS). Parsers
  retry failed jobs 3 times with 1-5 s backoff, then Encore dead-letters the message.

## Access matrix (inferred by Encore)

Encore has no permission syntax. Access follows usage, and the unit of isolation is the
service. Resources live in `resources/` and each service takes a typed reference with only
the permissions it needs (`objects.BucketRef[objects.Uploader](...)`,
`pubsub.TopicRef[pubsub.Publisher[T]](...)`). Passing the reference to shared code
(`reading.Kind.Process`) keeps the access visible in the service's own package.

| Service | `sensorhub-staging` | `sensorhub-results` | topic `-speed` | topic `-line-voltage` |
|---|---|---|---|---|
| `ingestion` | write | - | publish | publish |
| `speedparser` | read | write | subscribe | - |
| `voltageparser` | read | write | - | subscribe |
| `query` | - | list, read | - | - |

Check against the model Encore built (`encore debug meta -f json`, run with the app up):
`svcs[].buckets` and `pubsub_topics[].publishers/subscriptions` give exactly this table.
Excerpt: `ingestion: sensorhub-staging [WRITE_OBJECT]`; `query: sensorhub-results [LIST_OBJECTS,
READ_OBJECT_CONTENTS]`; each parser: `sensorhub-staging [READ_OBJECT_CONTENTS]`,
`sensorhub-results [WRITE_OBJECT]`; `sensorhub-speed` publishers `[ingestion]`, subscription
`speed-parser` in `speedparser`; same for line voltage. Locally nothing enforces this; it is
the input for IAM when deployed with Encore Cloud.

### Why buckets are shared this way

Docs: a bucket can be used "from any service by referencing the package-level variable",
and `BucketRef` (called from within a service) declares the permissions that service uses.
Alternatives were an internal API on an owning service (extra hop, and it hides the access
from the model) or one bucket-owning package per service (would duplicate buckets). A shared
`resources` package plus per-service refs gives the matrix above with the least code.

## Differences from the vanilla version

- Go instead of Python. Same schemas, same status codes, same error body
  `{"error":{"code","message"}}`.
- Topics instead of queues; Encore delivers to the subscription handler (push) instead of a
  worker polling a queue. Delivery, retries and dead-letter handling are Encore's.
- Services instead of Lambda/containers; isolation is per service, but only enforced after
  deployment, not by `encore run`.
- Validation uses the real JSON schemas (`santhosh-tekuri/jsonschema/v6`, formats asserted) plus
  the two checks the spec asks for in code: finite value and a real UTC calendar date.
  Go's JSON decoder already rejects `NaN`/`Infinity` tokens.
- Status codes: 202 `{"id"}`, 400 malformed JSON (or not an object), 413 over 4096 bytes,
  415 not `application/json`, 422 schema violation or unknown kind, 503 staging/publish
  failure. `GET`: 400 `invalid_limit` / `invalid_train_filter`, 503 on bucket errors.
- No dashboard (see limitations). No Terraform generator, Docker, Floci or exports.

## Observed results (2026-11-04, macOS, Encore v1.58.6)

`./smoke.sh` against `encore run` on port 4000:

```
ok   POST speed/reading-001.json -> 202 id=6302fdf9-79de-4be3-a0db-7131b79b81c0
ok   POST line-voltage/reading-001.json -> 202 id=0ea365b7-e34b-472b-b3b1-2dbaad4ab2ff
ok   malformed JSON -> 400
ok   unknown kind -> 422
ok   oversized payload (>4 KiB) -> 413
ok   both readings listed by GET /readings after 0s
PASS
```

A second run gave PASS as well. `GET /readings?limit=1`, `limit=abc` (400), `fleet_id` alone
(400), and an unknown train (`{"readings":[]}`) were checked by hand with curl.

`encore test ./...`:

```
ok  	encore.app/ingestion
ok  	encore.app/query
ok  	encore.app/reading
?   	encore.app/resources	[no test files]
?   	encore.app/speedparser	[no test files]
?   	encore.app/voltageparser	[no test files]
```

Unit tests cover upload validation (schema, NaN/Infinity, overflow, impossible dates,
non-UTC, length limits), normalization and statuses, a reading of the wrong kind failing in
each parser, request checks (415/413/400), query parsing, ordering, filter and limit cap.

## Known limitations

- Wrong-queue and failing-job behavior (retry, dead letter) is covered by unit tests of the
  parser logic, not observed live: nothing in the app can publish a mismatched job, and
  Encore's local Pub/Sub retry/DLQ behavior was not examined.
- Pub/Sub is in-memory locally: queued jobs are lost when `encore run` stops. Objects are on disk.
- Access rules are not enforced locally; they are visible only in the application model.
- `GET /readings` lists and reads every result object (as allowed by the spec for the demo).
- The dashboard (`sensorhub/src/frontend`, AngularJS, needs `npm run build`) is not served.
  It could be added as an embedded static raw endpoint, but it would need its build output
  copied here, which was out of scope.
- `go.mod` uses `encore.dev v1.57.13`; the CLI is v1.58.6 (works; `go get` picked the
  version the module proxy offered).
- Encore's parser rejects numeric literals with `_` in subscription config; durations use
  `time.Second`.

## Copied from where

- `reading/schema/{speed-reading,line-voltage-reading,reading-job,processed-reading}.schema.json`:
  unchanged copies of `sensorhub/src/backend/schema/`.
- Logic ported from `sensorhub/src/backend/src/{ingestion,processing,query,schemas}.py`
  (status codes, limits, key naming, statuses and units, query rules).
- Sample data is read by `smoke.sh` from `../../../sensorhub/src/data` (not copied).
