import argparse
import logging
import os
import time

import boto3
import uvicorn
from botocore.config import Config
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .container_adapters import S3ObjectStore, SQSQueues
from .ingestion import handle_post_readings
from .processing import process_line_voltage_job, process_speed_job
from .query import handle_get_readings


logger = logging.getLogger("sensor_hub")


def create_app(staging, publisher, results) -> FastAPI:
    app = FastAPI(title="Train Sensor Hub")

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/readings")
    async def post_readings(request: Request):
        response = handle_post_readings(
            await request.body(),
            request.headers.get("content-type"),
            staging,
            publisher,
        )
        return JSONResponse(status_code=response.status_code, content=response.body)

    @app.get("/readings")
    async def get_readings(request: Request):
        response = handle_get_readings(
            results,
            request.query_params.get("limit", 50),
            request.query_params.get("fleet_id"),
            request.query_params.get("asset_id"),
        )
        return JSONResponse(status_code=response.status_code, content=response.body)

    return app


app = create_app(
    S3ObjectStore(os.environ.get("STAGING_BUCKET", "sensor-hub-staging")),
    SQSQueues(),
    S3ObjectStore(os.environ.get("RESULTS_BUCKET", "sensor-hub-results")),
)


def initialize_resources() -> None:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://floci:4566"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket_names = {
        os.environ.get("STAGING_BUCKET", "sensor-hub-staging"),
        os.environ.get("RESULTS_BUCKET", "sensor-hub-results"),
    }
    queues = SQSQueues(create_missing=True)
    for attempt in range(30):
        try:
            existing = {
                bucket["Name"] for bucket in client.list_buckets()["Buckets"]
            }
            for bucket_name in sorted(bucket_names - existing):
                client.create_bucket(Bucket=bucket_name)
            queues.ensure_queue("speed")
            queues.ensure_queue("line_voltage")
            return
        except Exception:
            if attempt == 29:
                raise
            logger.info("Waiting for Floci resources API (%s/30)", attempt + 1)
            time.sleep(2)


def run_worker(kind: str) -> None:
    processor = {
        "speed": process_speed_job,
        "line_voltage": process_line_voltage_job,
    }[kind]
    queues = SQSQueues()
    staging = S3ObjectStore(os.environ.get("STAGING_BUCKET", "sensor-hub-staging"))
    results = S3ObjectStore(os.environ.get("RESULTS_BUCKET", "sensor-hub-results"))

    logger.info("Starting %s worker", kind)
    while True:
        message = queues.reserve(kind)
        if message is None:
            continue
        try:
            processor(message.job, staging, results)
            queues.acknowledge(kind, message)
        except Exception:
            logger.exception(
                "Failed %s job id=%s; retrying",
                kind,
                message.job.get("id"),
            )
            queues.retry(kind, message)
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("api", "worker", "init-resources"))
    parser.add_argument("kind", nargs="?", choices=("speed", "line_voltage"))
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "api":
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    elif args.command == "worker":
        if args.kind is None:
            parser.error("worker requires a reading kind")
        run_worker(args.kind)
    else:
        initialize_resources()


if __name__ == "__main__":
    main()