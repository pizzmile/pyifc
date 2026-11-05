# Sensor Hub Frontend

The AngularJS 1.8.3 interface is served from a standalone Nginx container. It is at `http://localhost:8000/ui/` in the Docker stack. Nginx serves the static assets and proxies same-origin API requests to the selected backend.

The upload view accepts one JSON object per request. A selected file is loaded into an editable text area; the page posts it to `POST /readings`. Processing is asynchronous. The fleet view groups the latest global readings by `(fleet_id, asset_id)` and fetches the latest 100 records for a selected train with `GET /readings?limit=100&fleet_id=...&asset_id=...`.

## Local asset build

Node.js 24 and npm are used by the frontend image build. To build assets directly from this directory (`sensorhub/src/frontend`):

```sh
npm ci
npm run build
```

The build writes `dist/`, which is ignored by Git. The Dockerfile is [../../docker/Dockerfile.frontend](../../docker/Dockerfile.frontend) (build context `sensorhub/`); it builds the assets in a Node.js stage and serves them from a separate Nginx container. Run the full stack with `sensorhub/docker/up.sh`; see [../../docker/README.md](../../docker/README.md).

AngularJS 1.8.3 is end-of-life and has known high-severity security advisories. It is retained here because the demo explicitly uses AngularJS; do not use this dependency choice as a production recommendation or expose this demo to untrusted networks.