#!/usr/bin/env python3
"""Turn the raw output of a run into the files ifctests.py analyses.

    python3 -I tools/tests/extract.py [RUN_ID]      # default: the newest folder under tools/tests/runs/

For every tools/tests/runs/<run>/<tool>/<ID>/ it writes, next to stages.json:
  p2_model.json     resources and uses in the tool's model (Nitric spec.json, Encore model.json, pyifc graph.json)
  p3_export.json    resources and uses in the generated Terraform
  p4_plan.json      changes of the Terraform plan, minus the provider noise listed in NOISE
  p6_behavior.json  result of the native run (T2, T2b)
  signals.json      diagnostics about the change, by stage
Mapping rules: tests.md sections 1 and 4. Names are made logical (Nitric `speed-readings` and Encore
`sensorhub-speed` are both `topic:speed`). A file is written only when its source exists.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

READ = {"BucketFileList", "BucketFileGet", "LIST_OBJECTS", "READ_OBJECT_CONTENTS", "s3:ListBucket", "s3:GetObject"}
WRITE = {"BucketFilePut", "WRITE_OBJECT", "s3:PutObject"}
PUBLISH = {"TopicPublish", "sns:Publish", "sqs:SendMessage"}
SUBSCRIBE = {"sqs:ReceiveMessage"}

# Changes every plan shows, also when the unchanged baseline is planned against its own state (tests.md section 6).
NOISE = {"docker_registry_image", "docker_tag", "aws_lambda_function", "docker_container"}
# pyifc builds its own package, so a Lambda update in its plan is a real change and stays visible.
NOISE_BY_TOOL = {"pyifc": NOISE - {"aws_lambda_function"}}


# --------------------------------------------------------------------- names

def bucket_name(name):
    return name.removeprefix("sensorhub-")


def topic_name(name):
    name = name.removeprefix("sensorhub-")
    for suffix in ("-readings", "-events"):
        name = name.removesuffix(suffix)
    return name


def component(name):
    name = name.removeprefix("sensorhub_services-").replace("_", "-")
    return {"speedparser": "speed-parser", "voltageparser": "line-voltage-parser", "audit": "audit-parser"}.get(name, name)


def capability(actions):
    """Capabilities a list of tool actions grants."""
    caps = set()
    for a in actions:
        for cap, names in (("read", READ), ("write", WRITE), ("publish", PUBLISH), ("subscribe", SUBSCRIBE)):
            if a in names:
                caps.add(cap)
    return caps


def snapshot(res, uses):
    return {"resources": sorted(res), "uses": sorted(uses)}


# --------------------------------------------------------------------- models

def nitric_model(spec):
    res, uses = set(), set()
    for r in spec["resources"]:
        kind, name = r["id"].get("type"), r["id"].get("name")
        if kind == "Bucket":
            res.add(f"bucket:{bucket_name(name)}")
        elif kind == "Topic":
            res.add(f"topic:{topic_name(name)}")
            for sub in r["topic"].get("subscriptions", []):
                uses.add(f"{component(sub['service'])}>topic:{topic_name(name)}:subscribe")
        elif kind == "Policy":
            p = r["policy"]
            for principal in p["principals"]:
                for target in p["resources"]:
                    t = target["id"]
                    prefix = ("bucket", bucket_name) if t["type"] == "Bucket" else ("topic", topic_name)
                    for cap in capability(p["actions"]):
                        uses.add(f"{component(principal['id']['name'])}>{prefix[0]}:{prefix[1](t['name'])}:{cap}")
    return res, uses


def encore_model(model):
    res, uses = set(), set()
    for b in model["buckets"]:
        res.add(f"bucket:{bucket_name(b['name'])}")
    for t in model["pubsub_topics"]:
        name = topic_name(t["name"])
        res.add(f"topic:{name}")
        for p in t.get("publishers", []):
            uses.add(f"{component(p['service_name'])}>topic:{name}:publish")
        for s in t.get("subscriptions", []):
            uses.add(f"{component(s['service_name'])}>topic:{name}:subscribe")
    for svc in model["svcs"]:
        for b in svc.get("buckets", []):
            for cap in capability(b["operations"]):
                uses.add(f"{component(svc['name'])}>bucket:{bucket_name(b['bucket'])}:{cap}")
    return res, uses


# -------------------------------------------------------------------- exports

def nitric_export(tf):
    mods = tf["module"]
    res, uses = set(), set()
    names = {}
    for key, m in mods.items():
        if key.startswith("bucket_"):
            names[key] = ("bucket", bucket_name(m["bucket_name"]))
        elif key.startswith("topic_"):
            names[key] = ("topic", topic_name(m["topic_name"]))
            for service in m.get("lambda_subscribers", {}):
                uses.add(f"{component(service)}>topic:{topic_name(m['topic_name'])}:subscribe")
    res |= {f"{kind}:{name}" for kind, name in names.values()}
    for key, m in mods.items():
        if not key.startswith("policy_"):
            continue
        caps = capability(m["actions"])
        targets = {names[ref] for r in m["resources"] for ref in re.findall(r"module\.([\w-]+)\.(?:bucket_arn|topic_arn)", r)
                   if ref in names}
        for principal in m["principals"]:
            service = principal.split(":")[0]
            for kind, name in targets:
                for cap in caps:
                    if (kind, cap) in (("bucket", "read"), ("bucket", "write"), ("topic", "publish")):
                        uses.add(f"{component(service)}>{kind}:{name}:{cap}")
    return res, uses


def encore_export(tf):
    resource = tf.get("resource", {})
    buckets = {k: bucket_name(v["bucket"]) for k, v in resource.get("aws_s3_bucket", {}).items()}
    topics = {k: topic_name(v["name"]) for k, v in resource.get("aws_sns_topic", {}).items()}
    queue_topic = {}
    for key, sub in resource.get("aws_sns_topic_subscription", {}).items():
        topic_key = re.search(r"aws_sns_topic\.(\w+)\.arn", sub["topic_arn"]).group(1)
        queue_topic[key] = topics[topic_key]
    res = {f"bucket:{n}" for n in buckets.values()} | {f"topic:{n}" for n in topics.values()}
    uses = set()
    for doc, body in tf.get("data", {}).get("aws_iam_policy_document", {}).items():
        if doc in ("policy_all_services",) or not doc.startswith("policy_"):
            continue
        who = component(doc.removeprefix("policy_"))
        for st in body["statement"]:
            caps = capability(st["actions"])
            for r in st["resources"]:
                if m := re.search(r"aws_s3_bucket\.(\w+)\.arn", r):
                    uses |= {f"{who}>bucket:{buckets[m.group(1)]}:{c}" for c in caps if c in ("read", "write")}
                elif m := re.search(r"aws_sns_topic\.(\w+)\.arn", r):
                    uses |= {f"{who}>topic:{topics[m.group(1)]}:publish" for c in caps if c == "publish"}
                elif m := re.search(r"aws_sqs_queue\.(\w+)\.arn", r):
                    if "subscribe" in caps and m.group(1) in queue_topic:
                        uses.add(f"{who}>topic:{queue_topic[m.group(1)]}:subscribe")
    return res, uses


def pyifc_name(name):
    return name.removeprefix("sensorhub-").removeprefix("sensorhub_")


PYIFC_KINDS = {"object_store": "bucket", "topic": "topic", "queue": "topic"}
PYIFC_OPERATIONS = {"read": "read", "list": "read", "write": "write", "publish": "publish", "subscribe": "subscribe"}


def pyifc_model(graph):
    """Common form of a `pyifc graph` document (schema 1.3): object stores are buckets, queues are topics, list is read.

    Not filtered by provenance: the graph keeps an edge that is declared but never used (T4: `@app.write(archive)`
    without a put), like the Nitric and Encore models keep the permission. The generator drops that grant in its
    default IAM mode (W_GRANT_DROPPED), so `pyifc_export` does not carry the use: the model/export difference is
    the tool's own handling of an unused grant, recorded as `lost_in_export`."""
    kinds, res, uses = {}, set(), set()
    for r in graph["resources"]:
        kind = PYIFC_KINDS.get(r["kind"])
        if kind:
            kinds[r["id"]] = kind
            res.add(f"{kind}:{pyifc_name(r['id'])}")
    for c in graph["capabilities"]:
        kind, cap = kinds.get(c["resource_id"]), PYIFC_OPERATIONS.get(c["operation"])
        if kind and cap:
            uses.add(f"{c['component_id']}>{kind}:{pyifc_name(c['resource_id'])}:{cap}")
    return snapshot(res, uses)


def pyifc_export(tf):
    """Common form of the generated Terraform document: inline role policies and subscriptions/event source mappings."""
    resource = tf.get("resource", {})

    def logical(key):
        return pyifc_name(key).replace("_", "-")

    components = {key: f.get("environment", {}).get("variables", {}).get("PYIFC_COMPONENT", key.replace("_", "-"))
                  for key, f in resource.get("aws_lambda_function", {}).items()}

    def component_of(key):
        return components.get(key, key.replace("_", "-"))

    buckets = {k: logical(k) for k in resource.get("aws_s3_bucket", {})}
    topics = {k: logical(k) for k in resource.get("aws_sns_topic", {})}
    queues = {k: logical(k) for k in resource.get("aws_sqs_queue", {})}
    res = {f"bucket:{n}" for n in buckets.values()} | {f"topic:{n}" for n in (*topics.values(), *queues.values())}
    uses = set()
    for key, role_policy in resource.get("aws_iam_role_policy", {}).items():
        who = component_of(key)
        for st in json.loads(role_policy["policy"]).get("Statement", []):
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            caps = capability(actions)
            for arn in st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]:
                if m := re.search(r"aws_s3_bucket\.(\w+)\.arn", arn):
                    uses |= {f"{who}>bucket:{buckets[m.group(1)]}:{c}" for c in caps if c in ("read", "write")}
                elif m := re.search(r"aws_sns_topic\.(\w+)\.arn", arn):
                    uses |= {f"{who}>topic:{topics[m.group(1)]}:publish" for c in caps if c == "publish"}
                elif m := re.search(r"aws_sqs_queue\.(\w+)\.arn", arn):
                    uses |= {f"{who}>topic:{queues[m.group(1)]}:publish" for c in caps if c == "publish"}
    for sub in resource.get("aws_sns_topic_subscription", {}).values():
        topic, function = (re.search(rf"{kind}\.(\w+)\.arn", sub[field]) for kind, field in (
            ("aws_sns_topic", "topic_arn"), ("aws_lambda_function", "endpoint")))
        if topic and function:
            uses.add(f"{component_of(function.group(1))}>topic:{topics[topic.group(1)]}:subscribe")
    for mapping in resource.get("aws_lambda_event_source_mapping", {}).values():
        queue, function = (re.search(rf"{kind}\.(\w+)\.", mapping[field]) for kind, field in (
            ("aws_sqs_queue", "event_source_arn"), ("aws_lambda_function", "function_name")))
        if queue and function:
            uses.add(f"{component_of(function.group(1))}>topic:{queues[queue.group(1)]}:subscribe")
    return snapshot(res, uses)


# ----------------------------------------------------------------------- plan

def plan_changes(summary, noise=NOISE):
    """Changes of plan-summary.json as {resource, action} items; provider noise is counted, not listed."""
    changes, filtered = [], 0
    for c in summary["changes"]:
        if c["type"] in noise:
            filtered += 1
            continue
        address = c["address"]
        if c["type"] == "aws_s3_bucket":
            m = re.match(r"module\.bucket_([\w-]+)\.", address) or re.match(r"aws_s3_bucket\.(\w+)$", address)
            name = m.group(1).replace("_", "-")
            resource = f"bucket:{bucket_name(name)}"
        else:
            resource = f"other:{address}"
        changes.append({"resource": resource, "action": "destroy" if c["action"] == "delete" else c["action"]})
    return {"filtered": filtered, "changes": sorted(changes, key=lambda c: (c["resource"], c["action"]))}


# ------------------------------------------------------------- run and signals

def behavior(stages):
    run = next((s for s in stages if s["stage"] == "run"), None)
    if run is None:
        return None
    return {"ok": run.get("exit") == 0, "note": run.get("note", "")}


LOG_STAGE = {"check": "edit", "spec": "generation", "generate": "generation", "model": "generation",
             "inventory": "generation", "export": "generation", "plan": "plan"}


def diagnostics(text):
    """Warning, error and failure lines of a stage log, without colours and numbers. Docker/pip build output is
    left out: it varies with the layer cache, not with the change."""
    out = set()
    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if "[In Progress]" in line or "docker --debug" in line or re.match(r"\S*\s*#\d+ ", line):
            continue
        if re.search(r"warn|error|deprecat|fail", line, re.I):
            out.add(re.sub(r"\d+", "#", line.strip())[:160])
    return out


def signals(case, tool, stages):
    """Diagnostics about the change, by stage:
    - edit: the `check` stage failed where the baseline's check passed (Nitric's mypy already reports 3 errors on the
      unchanged baseline, so a failed check alone says nothing there);
    - generation: a spec, generate, model or export stage failed;
    - edit, generation, plan: a warning/error line in that stage's log that the unchanged baseline (T0 of the same
      tool and run) does not print;
    - runtime: a traceback or 5xx in the run log."""
    out = []
    base_file = case.parent / "T0" / "stages.json"
    base_check_failed = case.name == "T0" or (
        base_file.exists() and any(r["stage"] == "check" and r["status"] == "failed" for r in json.loads(base_file.read_text())))
    for s in stages:
        if s["status"] == "failed" and s["stage"] == "check" and not base_check_failed:
            out.append({"stage": "edit", "text": f"check failed: {s.get('command')}"})
        elif s["status"] == "failed" and s["stage"] in ("spec", "generate", "model", "export"):
            out.append({"stage": "generation", "text": f"{s['stage']} failed"})
    reference = case.parent / "T0" / "raw"
    if case.name != "T0" and reference.is_dir():
        for log, stage in LOG_STAGE.items():
            mine, base = case / "raw" / f"{log}.log", reference / f"{log}.log"
            if mine.exists() and base.exists():
                new = diagnostics(mine.read_text(errors="replace")) - diagnostics(base.read_text(errors="replace"))
                if new:
                    out.append({"stage": stage, "text": f"new line in {log}.log: {sorted(new)[0]}"})
    log = case / "raw" / "run-server.log"
    if log.exists():
        text = log.read_text(errors="replace")
        line = next((l for l in text.splitlines() if "unhandled error" in l or "http_code=5" in l or "panic" in l
                     or l.startswith("Traceback") or re.match(r"(POST|GET)\b.*: 5\d\d$", l)), None)
        if line:
            out.append({"stage": "runtime", "text": re.sub(r"\x1b\[[0-9;]*m", "", line)[:200]})
    return out


# ----------------------------------------------------------------------- main

def extract_case(tool, case):
    case = Path(case)
    raw = case / "raw"
    stages = json.loads((case / "stages.json").read_text()) if (case / "stages.json").exists() else []
    written = []

    def put(name, data):
        (case / name).write_text(json.dumps(data, indent=1) + "\n")
        written.append(name)

    # tool -> (model file, export file, model reader, export reader); the readers return the common form
    def pair(fn):
        return lambda doc: snapshot(*fn(doc))
    model_file, export_file, model_fn, export_fn = {
        "nitric": ("spec.json", "generated/cdk.tf.json", pair(nitric_model), pair(nitric_export)),
        "encore": ("model.json", "generated/main.tf.json", pair(encore_model), pair(encore_export)),
        "pyifc": ("graph.json", "generated/app.tf.json", pyifc_model, pyifc_export),
    }[tool]
    if (raw / model_file).exists():
        put("p2_model.json", model_fn(json.loads((raw / model_file).read_text())))
    if (raw / export_file).exists():
        put("p3_export.json", export_fn(json.loads((raw / export_file).read_text())))
    if (raw / "plan-summary.json").exists():
        put("p4_plan.json", plan_changes(json.loads((raw / "plan-summary.json").read_text()), NOISE_BY_TOOL.get(tool, NOISE)))
    b = behavior(stages)
    if b is not None:
        put("p6_behavior.json", b)
    put("signals.json", signals(case, tool, stages))
    return written


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    runs = sorted(p for p in RUNS.iterdir() if p.is_dir())
    run = RUNS / argv[0] if argv else runs[-1]
    for tool_dir in sorted(p for p in run.iterdir() if p.is_dir()):
        for case in sorted(p for p in tool_dir.iterdir() if p.is_dir()):
            written = extract_case(tool_dir.name, case)
            print(f"{tool_dir.name}/{case.name}: {' '.join(w.split('.')[0] for w in written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
