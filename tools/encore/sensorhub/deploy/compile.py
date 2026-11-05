#!/usr/bin/env python3
"""Compile Encore's exported application model into Terraform (for Floci) and an infra config.

Input : the JSON printed by `encore debug meta -f json` (no source parsing here).
Output: <out>/main.tf.json         Terraform JSON: S3, SNS, SQS, IAM (AWS provider -> Floci) + the app container
        <out>/infra-config.json    the file `encore build docker --config` expects

Every resource name, access right and queue setting comes from the model. Only the
environment (endpoints, region, account id, env name) comes from command line options.

Usage:  python3 -I compile.py --model model.json --out generated [--check]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# What the model calls a bucket operation -> (S3 action, applies to bucket or objects).
BUCKET_ACTIONS = {
    "LIST_OBJECTS": ("s3:ListBucket", "bucket"),
    "WRITE_OBJECT": ("s3:PutObject", "objects"),
    "UPDATE_OBJECT_METADATA": ("s3:PutObject", "objects"),
    "READ_OBJECT_CONTENTS": ("s3:GetObject", "objects"),
    "GET_OBJECT_METADATA": ("s3:GetObject", "objects"),
    "DELETE_OBJECT": ("s3:DeleteObject", "objects"),
}
CONSUMER_ACTIONS = ["sqs:ChangeMessageVisibility", "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ReceiveMessage"]
UNSUPPORTED = ["sql_databases", "cache_clusters", "cron_jobs"]  # not in this app; fail loudly


class ModelError(Exception):
    pass


def ident(name):
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def seconds(nanos):
    return int(nanos) // 10**9


def queue_name(topic, sub):
    return f"{topic['name']}-{sub['name']}"


def read_model(model):
    for key in UNSUPPORTED:
        if model.get(key):
            raise ModelError(f"model has {key}; this compiler handles buckets and Pub/Sub only")
    for b in model.get("buckets", []):
        if b.get("public"):
            raise ModelError(f"bucket {b['name']} is public; not supported")
    for t in model.get("pubsub_topics", []):
        if t.get("ordering_key"):
            raise ModelError(f"topic {t['name']} is ordered (FIFO); not supported")
    return model


def service_access(model):
    """service -> {'buckets': {bucket: set(actions)}, 'publish': set(topics), 'consume': set(queues)}"""
    access = {s["name"]: {"buckets": {}, "publish": set(), "consume": set()} for s in model["svcs"]}
    for s in model["svcs"]:
        for use in s.get("buckets", []):
            acts = access[s["name"]]["buckets"].setdefault(use["bucket"], set())
            for op in use["operations"]:
                if op not in BUCKET_ACTIONS:
                    raise ModelError(f"unknown bucket operation {op}")
                acts.add(BUCKET_ACTIONS[op])
    for t in model.get("pubsub_topics", []):
        for p in t.get("publishers", []):
            access[p["service_name"]]["publish"].add(t["name"])
        for sub in t.get("subscriptions", []):
            access[sub["service_name"]]["consume"].add(queue_name(t, sub))
    return access


def policy_document(acc):
    """aws_iam_policy_document body (Terraform JSON) for one access set."""
    stmts = []
    for bucket, actions in sorted(acc["buckets"].items()):
        for scope in ("bucket", "objects"):
            acts = sorted(a for a, s in actions if s == scope)
            if acts:
                arn = "${aws_s3_bucket.%s.arn}" % ident(bucket)
                stmts.append({"sid": f"{scope}_{ident(bucket)}", "effect": "Allow", "actions": acts,
                              "resources": [arn if scope == "bucket" else arn + "/*"]})
    for topic in sorted(acc["publish"]):
        stmts.append({"sid": f"publish_{ident(topic)}", "effect": "Allow", "actions": ["sns:Publish"],
                      "resources": ["${aws_sns_topic.%s.arn}" % ident(topic)]})
    for queue in sorted(acc["consume"]):
        stmts.append({"sid": f"consume_{ident(queue)}", "effect": "Allow", "actions": CONSUMER_ACTIONS,
                      "resources": ["${aws_sqs_queue.%s.arn}" % ident(queue)]})
    return {"statement": stmts}


def union(accesses):
    out = {"buckets": {}, "publish": set(), "consume": set()}
    for a in accesses:
        for b, acts in a["buckets"].items():
            out["buckets"].setdefault(b, set()).update(acts)
        out["publish"] |= a["publish"]
        out["consume"] |= a["consume"]
    return out


def build_terraform(model, endpoint, region, image, network, host_port):
    res, data, out = {}, {}, {}

    def add(kind, name, body):
        res.setdefault(kind, {})[name] = body

    for b in sorted(model.get("buckets", []), key=lambda b: b["name"]):
        add("aws_s3_bucket", ident(b["name"]), {"bucket": b["name"], "force_destroy": True})
        if b.get("versioned"):
            add("aws_s3_bucket_versioning", ident(b["name"]), {
                "bucket": "${aws_s3_bucket.%s.id}" % ident(b["name"]),
                "versioning_configuration": {"status": "Enabled"}})

    for t in sorted(model.get("pubsub_topics", []), key=lambda t: t["name"]):
        tid = ident(t["name"])
        add("aws_sns_topic", tid, {"name": t["name"]})
        out[f"topic_arn_{tid}"] = {"value": "${aws_sns_topic.%s.arn}" % tid}
        for sub in sorted(t.get("subscriptions", []), key=lambda s: s["name"]):
            q = queue_name(t, sub)
            qid = ident(q)
            retry = sub.get("retry_policy", {})
            # Dead-letter queue after max_retries receives, as the model's retry policy says.
            add("aws_sqs_queue", qid + "_dlq", {
                "name": q + "-dlq",
                "message_retention_seconds": seconds(sub["message_retention"])})
            add("aws_sqs_queue", qid, {
                "name": q,
                "visibility_timeout_seconds": seconds(sub["ack_deadline"]),
                "message_retention_seconds": seconds(sub["message_retention"]),
                "redrive_policy": json.dumps({
                    "deadLetterTargetArn": "${aws_sqs_queue.%s_dlq.arn}" % qid,
                    "maxReceiveCount": int(retry.get("max_retries", 3)) + 1}, sort_keys=True)})
            data.setdefault("aws_iam_policy_document", {})[f"sns_to_{qid}"] = {"statement": [{
                "effect": "Allow", "actions": ["sqs:SendMessage"],
                "resources": ["${aws_sqs_queue.%s.arn}" % qid],
                "principals": [{"type": "Service", "identifiers": ["sns.amazonaws.com"]}],
                "condition": [{"test": "ArnEquals", "variable": "aws:SourceArn",
                               "values": ["${aws_sns_topic.%s.arn}" % tid]}]}]}
            add("aws_sqs_queue_policy", qid, {
                "queue_url": "${aws_sqs_queue.%s.url}" % qid,
                "policy": "${data.aws_iam_policy_document.sns_to_%s.json}" % qid})
            add("aws_sns_topic_subscription", qid, {
                "topic_arn": "${aws_sns_topic.%s.arn}" % tid, "protocol": "sqs",
                "endpoint": "${aws_sqs_queue.%s.arn}" % qid,
                "raw_message_delivery": False})  # Encore's consumer reads the SNS envelope
            out[f"queue_url_{qid}"] = {"value": "${aws_sqs_queue.%s.url}" % qid}

    # One IAM user per service, plus "all-services" (the union) for the one-image deployment,
    # where a single process runs every service and can only hold one identity.
    access = service_access(model)
    principals = dict(access)
    principals["all-services"] = union(access.values())
    for name, acc in sorted(principals.items()):
        pid = ident(name)
        data.setdefault("aws_iam_policy_document", {})[f"policy_{pid}"] = policy_document(acc)
        add("aws_iam_user", pid, {"name": f"sensorhub-{name}"})
        add("aws_iam_user_policy", pid, {
            "name": f"sensorhub-{name}", "user": "${aws_iam_user.%s.name}" % pid,
            "policy": "${data.aws_iam_policy_document.policy_%s.json}" % pid})
        add("aws_iam_access_key", pid, {"user": "${aws_iam_user.%s.name}" % pid})
        out[f"access_key_id_{pid}"] = {"value": "${aws_iam_access_key.%s.id}" % pid}
        out[f"secret_access_key_{pid}"] = {"value": "${aws_iam_access_key.%s.secret}" % pid,
                                           "sensitive": True}

    # The app itself: the image `encore build docker --config infra-config.json` produced, run as a
    # container on the Floci network with the "all-services" identity (the image holds every service).
    # It starts after all infrastructure above exists. Floci's own container is not managed here:
    # Terraform needs it running first.
    infra = sorted(f"{kind}.{name}" for kind in res for name in res[kind] if kind != "aws_iam_access_key")
    # Empty hook the app waits for; a workaround may fill it (see workarounds/03-*).
    add("terraform_data", "before_app", {"input": '${join(",", local.workaround_deps)}', "depends_on": infra})
    data["docker_network"] = {"floci": {"name": "${var.network}"}}
    data["docker_image"] = {"app": {"name": "${var.image}"}}
    add("docker_container", "app", {
        "name": "sensorhub-encore-app",
        "image": "${data.docker_image.app.id}",
        "networks_advanced": [{"name": "${data.docker_network.floci.name}"}],
        "ports": [{"internal": 8080, "external": "${var.host_port}"}],
        "env": "${concat(%s, local.workaround_env)}" % json.dumps([
            "AWS_REGION=" + region,
            "AWS_ACCESS_KEY_ID=${aws_iam_access_key.all_services.id}",
            "AWS_SECRET_ACCESS_KEY=${aws_iam_access_key.all_services.secret}"]),
        "depends_on": ["terraform_data.before_app"]})
    out["app_url"] = {"value": "http://localhost:${var.host_port}"}

    return {
        "terraform": {"required_providers": {"aws": {"source": "hashicorp/aws", "version": "5.88.0"},
                                              "docker": {"source": "kreuzwerker/docker", "version": "3.0.2"}}},
        "variable": {"image": {"type": "string", "default": image},
                     "network": {"type": "string", "default": network},
                     "host_port": {"type": "number", "default": host_port}},
        "locals": {"workaround_env": [], "workaround_deps": []},  # workarounds/*_override.tf.json may replace these
        "provider": {"docker": {}, "aws": {
            "region": region, "access_key": "test", "secret_key": "test",
            "skip_credentials_validation": True, "skip_metadata_api_check": True,
            "skip_requesting_account_id": True, "s3_use_path_style": True,
            "endpoints": {svc: endpoint for svc in ("iam", "s3", "sns", "sqs", "sts")}}},
        "data": data, "resource": res, "output": out}


def build_infra_config(model, app_endpoint, region, account, app_id, env_name, base_url):
    """Names and ARNs are predictable (Floci: account id + name), so no Terraform output is needed;
    up.sh checks them against the real Terraform outputs."""
    topics = {}
    for t in sorted(model.get("pubsub_topics", []), key=lambda t: t["name"]):
        topics[t["name"]] = {
            "arn": f"arn:aws:sns:{region}:{account}:{t['name']}",
            "subscriptions": {s["name"]: {"url": f"{app_endpoint}/{account}/{queue_name(t, s)}"}
                              for s in sorted(t.get("subscriptions", []), key=lambda s: s["name"])}}
    return {
        "$schema": "https://encore.dev/schemas/infra.schema.json",
        "metadata": {"app_id": app_id, "env_name": env_name, "env_type": "development",
                     "cloud": "aws", "base_url": base_url},
        "object_storage": [{"type": "s3", "region": region, "endpoint": app_endpoint,
                            "buckets": {b["name"]: {"name": b["name"]}
                                        for b in sorted(model.get("buckets", []), key=lambda b: b["name"])}}],
        "pubsub": [{"type": "aws_sns_sqs", "topics": topics}],
    }


def dump(obj):
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--endpoint", default="http://localhost:4571", help="Floci URL as seen by Terraform")
    p.add_argument("--app-endpoint", default="http://floci-encore:4566", help="Floci URL as seen by the app")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--account", default="000000000000")
    p.add_argument("--app-id", default="sensorhub")
    p.add_argument("--env-name", default="floci")
    p.add_argument("--base-url", default="http://localhost:8080")
    p.add_argument("--image", default="sensorhub-encore:floci", help="image built by encore build docker")
    p.add_argument("--network", default="encore-sensorhub-net", help="existing Docker network that Floci is on")
    p.add_argument("--host-port", type=int, default=8080, help="host port for the app's API")
    p.add_argument("--check", action="store_true", help="do not write; fail if files in --out differ")
    a = p.parse_args(argv)

    model = read_model(json.loads(a.model.read_text()))
    files = {
        "main.tf.json": dump(build_terraform(model, a.endpoint, a.region, a.image, a.network, a.host_port)),
        "infra-config.json": dump(build_infra_config(model, a.app_endpoint, a.region, a.account,
                                                     a.app_id, a.env_name, a.base_url)),
    }
    if a.check:
        bad = [n for n, text in files.items()
               if not (a.out / n).exists() or (a.out / n).read_text() != text]
        if bad:
            print("differs from model: " + ", ".join(bad), file=sys.stderr)
            return 1
        print("ok: generated files match the model")
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    for n, text in files.items():
        (a.out / n).write_text(text)
        print(f"wrote {a.out / n}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModelError as e:
        sys.exit(f"error: {e}")
