# Sensor hub on Docker (vanilla)

Hand-written reference deployment of the sensor hub: no generated infrastructure. It runs entirely on the developer's machine.

| Service | Image | Role |
| --- | --- | --- |
| `store` | RustFS (pinned by digest) | S3-compatible object store; staging and results buckets |
| `elasticmq` | ElasticMQ native 1.6.12 | SQS-compatible queues `speed` and `line_voltage` |
| `resources-init` | `Dockerfile.backend` | Creates the buckets and queues, then exits |
| `api` | `Dockerfile.backend` | FastAPI: `POST /readings`, `GET /readings`, `/healthz` |
| `speed-worker`, `line-voltage-worker` | `Dockerfile.backend` | One parser per queue |
| `frontend` | `Dockerfile.frontend` | Nginx: dashboard at `/ui/`, proxies `/readings` to the API |

## Run

Needs Docker with Compose v2. From the repository root:

```sh
sensorhub/docker/up.sh        # build and start; dashboard at http://localhost:8000/ui/
sensorhub/docker/smoke.sh     # post one speed and one line-voltage sample, wait for both results
sensorhub/docker/down.sh      # stop; add --volumes to also delete the stored readings
```

If port 8000 (or 9000, 9001, 9324) is taken, set `FRONTEND_PORT`, `STORE_PORT`, `STORE_CONSOLE_PORT` or `QUEUE_PORT` for all three scripts, e.g. `FRONTEND_PORT=8088 sensorhub/docker/up.sh`.

Sample readings are in `../src/data`. The RustFS console is at `http://localhost:9001` (`sensorhub-admin` / `sensorhub-admin-secret`, local demo credentials).

## Notes

- Build context for both images is `sensorhub/`; the Dockerfiles copy only `src/backend` and `src/frontend`.
- Verified 2026-11-04 on Docker Desktop (Compose v2.40.3): stack starts, smoke passes twice in a row, dashboard returns 200. Not verified: restart persistence, retries, clean-machine image pulls.
- AngularJS 1.8.3 is end-of-life; keep the demo off untrusted networks (see `../src/frontend/README.md`).
