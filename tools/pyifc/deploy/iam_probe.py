#!/usr/bin/env python3
"""Allow/deny probe of the deployed per-component roles on Floci (IAM enforcement on).

For each component role of the deployed stack the probe reads the inline policy that
Terraform attached (IAM GetRolePolicy), creates a temporary IAM user on Floci with exactly
that policy document, issues real S3 and SNS calls with the user's access key and checks
the outcome against what the sensor-hub declarations imply: calls inside the declared
capabilities succeed, calls outside return HTTP 403. The temporary users and the probe
objects are removed afterwards. Table written to logs/iam-probe.txt; exit status 1 if
any observation differs from the expectation.

Environment: FLOCI_PORT (default 4572), NAME_PREFIX (default sensorhub).
Allowed SNS publishes reach the subscribed parser Lambdas with a body that is not a job;
the parsers reject it (visible in their logs) and write nothing.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

PORT = int(os.environ.get("FLOCI_PORT", "4572"))
ENDPOINT = f"http://localhost:{PORT}"
PREFIX = os.environ.get("NAME_PREFIX", "sensorhub")
REGION = "us-east-1"
ADMIN = ("floci", "floci")  # the seeded deployer principal
STAGING, RESULTS = f"{PREFIX}-staging", f"{PREFIX}-results"
OUT = Path(__file__).resolve().parent / "logs" / "iam-probe.txt"


def session(key: str, secret: str):
    return boto3.Session(aws_access_key_id=key, aws_secret_access_key=secret, region_name=REGION)


def client(identity, service: str):
    return identity.client(service, endpoint_url=ENDPOINT, config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}))


def call(operation) -> tuple[int, str]:
    """(HTTP status, error code or 'OK')."""
    try:
        response = operation()
        return response["ResponseMetadata"]["HTTPStatusCode"], "OK"
    except ClientError as error:
        return (
            error.response["ResponseMetadata"]["HTTPStatusCode"],
            error.response.get("Error", {}).get("Code", "Unknown"),
        )


def main() -> int:
    run = uuid.uuid4().hex[:10]
    admin = session(*ADMIN)
    iam, s3, sns = client(admin, "iam"), client(admin, "s3"), client(admin, "sns")
    topics = {
        topic["TopicArn"].rsplit(":", 1)[1]: topic["TopicArn"] for topic in sns.list_topics()["Topics"]
    }
    speed, voltage = topics[f"{PREFIX}-speed"], topics[f"{PREFIX}-line-voltage"]
    key_staging, key_results = f"_pyifc-iam-probe/{run}/staging.json", f"_pyifc-iam-probe/{run}/results.json"
    s3.put_object(Bucket=STAGING, Key=key_staging, Body=b"{}")
    s3.put_object(Bucket=RESULTS, Key=key_results, Body=b"{}")
    fixtures = [(STAGING, key_staging), (RESULTS, key_results)]
    message = json.dumps({"probe": run})

    # (role suffix, label, expected status class, operation factory taking the user's clients)
    # "allow" -> 2xx, "deny" -> HTTP 403.
    def matrix(c_s3, c_sns, role: str):
        put = lambda bucket: (lambda: c_s3.put_object(Bucket=bucket, Key=f"_pyifc-iam-probe/{run}/{role}-{bucket}.json", Body=b"{}"))  # noqa: E731
        get = lambda bucket, key: (lambda: c_s3.get_object(Bucket=bucket, Key=key))  # noqa: E731
        publish = lambda arn: (lambda: c_sns.publish(TopicArn=arn, Message=message))  # noqa: E731
        return {
            "ingestion": [
                ("s3:PutObject staging", "allow", put(STAGING)),
                ("sns:Publish speed topic", "allow", publish(speed)),
                ("sns:Publish line-voltage topic", "allow", publish(voltage)),
                ("s3:GetObject results", "deny", get(RESULTS, key_results)),
                ("s3:PutObject results", "deny", put(RESULTS)),
            ],
            "speed_parser": [
                ("s3:GetObject staging", "allow", get(STAGING, key_staging)),
                ("s3:PutObject results", "allow", put(RESULTS)),
                ("sns:Publish line-voltage topic", "deny", publish(voltage)),
                ("sns:Publish speed topic", "deny", publish(speed)),
                ("s3:PutObject staging", "deny", put(STAGING)),
            ],
            "line_voltage_parser": [
                ("s3:GetObject staging", "allow", get(STAGING, key_staging)),
                ("s3:PutObject results", "allow", put(RESULTS)),
                ("sns:Publish speed topic", "deny", publish(speed)),
                ("sns:Publish line-voltage topic", "deny", publish(voltage)),
                ("s3:PutObject staging", "deny", put(STAGING)),
            ],
            "query": [
                ("s3:ListBucket results", "allow", lambda: c_s3.list_objects_v2(Bucket=RESULTS, MaxKeys=5)),
                ("s3:GetObject results", "allow", get(RESULTS, key_results)),
                ("s3:PutObject results", "deny", put(RESULTS)),
                ("s3:GetObject staging", "deny", get(STAGING, key_staging)),
                ("s3:ListBucket staging", "deny", lambda: c_s3.list_objects_v2(Bucket=STAGING, MaxKeys=5)),
            ],
        }[role]

    lines = [
        "pyifc deployed-role IAM probe (Floci, IAM enforcement on)",
        f"time {datetime.now(timezone.utc).isoformat(timespec='seconds')}  run {run}  endpoint {ENDPOINT}",
        "",
    ]
    rows, users, mismatches = [], [], []
    try:
        for role in ("ingestion", "speed_parser", "line_voltage_parser", "query"):
            role_name = f"{PREFIX}-{role}"
            policy_name = iam.list_role_policies(RoleName=role_name)["PolicyNames"][0]
            document = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)["PolicyDocument"]
            lines += [f"role {role_name} policy {policy_name}:", json.dumps(document, indent=1, sort_keys=True), ""]
            user = f"pyifc-probe-{run}-{role}".replace("_", "-")
            iam.create_user(UserName=user)
            users.append(user)
            iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=json.dumps(document))
            access = iam.create_access_key(UserName=user)["AccessKey"]
            identity = session(access["AccessKeyId"], access["SecretAccessKey"])
            user_s3, user_sns = client(identity, "s3"), client(identity, "sns")
            for label, expected, operation in matrix(user_s3, user_sns, role):
                status, code = call(operation)
                observed = "allow" if 200 <= status < 300 else ("deny" if status == 403 else f"other({status})")
                verdict = "ok" if observed == expected else "MISMATCH"
                if verdict != "ok":
                    mismatches.append(f"{role}: {label}: expected {expected}, observed {observed} (HTTP {status} {code})")
                rows.append((role, label, expected, status, code, verdict))
    finally:
        for user in users:
            for item in iam.list_access_keys(UserName=user)["AccessKeyMetadata"]:
                iam.delete_access_key(UserName=user, AccessKeyId=item["AccessKeyId"])
            for name in iam.list_user_policies(UserName=user)["PolicyNames"]:
                iam.delete_user_policy(UserName=user, PolicyName=name)
            iam.delete_user(UserName=user)
        for bucket in (STAGING, RESULTS):
            for item in s3.list_objects_v2(Bucket=bucket, Prefix=f"_pyifc-iam-probe/{run}/").get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=item["Key"])

    width = max(len(row[1]) for row in rows)
    lines.append(f"{'role':20} {'call':{width}} {'expected':8} {'HTTP':4} {'code':18} verdict")
    for role, label, expected, status, code, verdict in rows:
        lines.append(f"{role:20} {label:{width}} {expected:8} {status:<4} {code:18} {verdict}")
    lines.append("")
    lines.append(
        f"{sum(1 for r in rows if r[2] == 'allow')} allowed and {sum(1 for r in rows if r[2] == 'deny')} forbidden calls; "
        + ("all as declared: PASS" if not mismatches else f"{len(mismatches)} mismatches: FAIL")
    )
    lines += [f"  {item}" for item in mismatches]
    text = "\n".join(lines) + "\n"
    print(text)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
