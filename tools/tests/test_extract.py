import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as x  # noqa: E402
import ifctests  # noqa: E402
import run  # noqa: E402

SPEC = ifctests.load_spec()
RUN = x.RUNS / "2026-11-04"


def load(tool, sid, name):
    return json.loads((RUN / tool / sid / "raw" / name).read_text())


class NameTests(unittest.TestCase):
    def test_logical_names(self):
        self.assertEqual(x.bucket_name("sensorhub-staging-v2"), "staging-v2")
        self.assertEqual((x.topic_name("speed-readings"), x.topic_name("sensorhub-line-voltage")), ("speed", "line-voltage"))
        self.assertEqual((x.topic_name("audit-events"), x.topic_name("sensorhub-audit")), ("audit", "audit"))
        self.assertEqual([x.component(n) for n in ("sensorhub_services-line_voltage_parser", "speedparser", "voltageparser", "audit")],
                         ["line-voltage-parser", "speed-parser", "line-voltage-parser", "audit-parser"])

    def test_capabilities(self):
        self.assertEqual(x.capability(["BucketFileList", "BucketFileGet"]), {"read"})
        self.assertEqual(x.capability(["WRITE_OBJECT"]), {"write"})
        self.assertEqual(x.capability(["s3:GetObject", "s3:PutObject"]), {"read", "write"})


@unittest.skipUnless(RUN.is_dir(), "run evidence is not part of this repository")
class BaselineTests(unittest.TestCase):
    """The T0 model and export of each tool equal the baseline of tests.md section 1."""

    def test_models_equal_baseline(self):
        oracle = ifctests.oracle(SPEC, "T0")
        self.assertEqual(x.nitric_model(load("nitric", "T0", "spec.json")), oracle)
        self.assertEqual(x.encore_model(load("encore", "T0", "model.json")), oracle)

    def test_exports_equal_baseline(self):
        oracle = ifctests.oracle(SPEC, "T0")
        self.assertEqual(x.nitric_export(load("nitric", "T0", "generated/cdk.tf.json")), oracle)
        self.assertEqual(x.encore_export(load("encore", "T0", "generated/main.tf.json")), oracle)

    def test_t4_models_carry_the_unused_write(self):
        for tool, fn, name in (("nitric", x.nitric_model, "spec.json"), ("encore", x.encore_model, "model.json")):
            _, uses = fn(load(tool, "T4", name))
            self.assertIn("ingestion>bucket:archive:write", uses, tool)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pyifc"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def tf_with_policy(actions, resource="${aws_s3_bucket.sensorhub_archive.arn}/*"):
    """A generated document with one bucket and one role policy, as the generator writes it."""
    policy = {"Version": "2012-10-17", "Statement": [{"Action": actions, "Effect": "Allow", "Resource": [resource]}]}
    return {"resource": {"aws_s3_bucket": {"sensorhub_archive": {"bucket": "${var.name_prefix}-archive"}},
                         "aws_iam_role_policy": {"ingestion": {"policy": json.dumps(policy)}}}}


class PyifcTests(unittest.TestCase):
    """The pyifc graph and Terraform export of T0 equal the baseline of tests.md section 1 (fixtures made with the CLI)."""

    def oracle(self):
        return x.snapshot(*ifctests.oracle(SPEC, "T0"))

    def test_model_equals_baseline(self):
        got = x.pyifc_model(fixture("graph.json"))
        self.assertEqual((len(got["resources"]), len(got["uses"])), (4, 10))
        self.assertEqual(got, self.oracle())

    def test_export_equals_baseline(self):
        self.assertEqual(x.pyifc_export(fixture("app.tf.json")), self.oracle())

    def test_list_and_read_are_one_read_and_object_store_is_a_bucket(self):
        graph = {"resources": [{"id": "sensorhub-results", "kind": "object_store"}, {"id": "sensorhub-q", "kind": "queue"}],
                 "capabilities": [{"component_id": "query", "operation": op, "resource_id": "sensorhub-results"} for op in ("list", "read")]
                 + [{"component_id": "a", "operation": "publish", "resource_id": "sensorhub-q"},
                    {"component_id": "b", "operation": "subscribe", "resource_id": "sensorhub-q"}]}
        self.assertEqual(x.pyifc_model(graph), {"resources": ["bucket:results", "topic:q"],
                                                "uses": ["a>topic:q:publish", "b>topic:q:subscribe", "query>bucket:results:read"]})

    def test_t4_model_keeps_the_declared_unused_write_and_the_export_drops_the_grant(self):
        """Real CLI output for the T4 edit: the graph carries the declared-only edge, the generated policy has no grant."""
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            shutil.copytree(run.PORTS["pyifc"], app, ignore=run.COPY_IGNORE)
            env = run.Ctx("pyifc", "T4", app, Path(tmp)).env
            lock = str(app / "pyifc.lock.json")

            def cli(*args):
                done = subprocess.run(run.pyifc_cli(*args), cwd=app, env=env, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                return done.stderr

            cli("generate", "app.py", "--out", str(Path(tmp) / "init"), "--lock", lock, "--init-lock")
            run.EDITS["pyifc"]["T4"](app)
            cli("graph", "app.py", "-o", str(Path(tmp) / "graph.json"))
            stderr = cli("generate", "app.py", "--out", str(Path(tmp) / "gen"), "--lock", lock)
            model = x.pyifc_model(json.loads((Path(tmp) / "graph.json").read_text()))
            export = x.pyifc_export(json.loads((Path(tmp) / "gen" / "app.tf.json").read_text()))
        self.assertIn("ingestion>bucket:archive:write", model["uses"])
        res, uses = ifctests.oracle(SPEC, "T4")  # T4's oracle has the bucket only; the model adds the declared write
        self.assertEqual(model, x.snapshot(res, uses | {"ingestion>bucket:archive:write"}))
        self.assertNotIn("ingestion>bucket:archive:write", export["uses"])
        self.assertIn("bucket:archive", export["resources"])
        self.assertIn("W_GRANT_DROPPED", stderr)

    def test_a_put_only_policy_is_a_write_use(self):
        got = x.pyifc_export(tf_with_policy(["s3:PutObject"]))
        self.assertEqual(got, {"resources": ["bucket:archive"], "uses": ["ingestion>bucket:archive:write"]})

    def test_policy_with_list_and_get_is_one_read_use(self):
        got = x.pyifc_export(tf_with_policy(["s3:ListBucket", "s3:GetObject"]))
        self.assertEqual(got["uses"], ["ingestion>bucket:archive:read"])

    def test_sqs_send_message_is_a_publish(self):
        self.assertEqual(x.capability(["sqs:SendMessage"]), {"publish"})
        tf = {"resource": {"aws_sqs_queue": {"sensorhub_jobs": {}},
                           "aws_iam_role_policy": {"ingestion": {"policy": json.dumps({"Statement": [
                               {"Action": ["sqs:SendMessage"], "Effect": "Allow", "Resource": ["${aws_sqs_queue.sensorhub_jobs.arn}"]}]})}},
                           "aws_lambda_event_source_mapping": {"worker": {"event_source_arn": "${aws_sqs_queue.sensorhub_jobs.arn}",
                                                                          "function_name": "${aws_lambda_function.worker.arn}"}}}}
        self.assertEqual(x.pyifc_export(tf), {"resources": ["topic:jobs"],
                                              "uses": ["ingestion>topic:jobs:publish", "worker>topic:jobs:subscribe"]})

    def test_component_id_comes_from_the_function_environment(self):
        tf = tf_with_policy(["s3:PutObject"])
        tf["resource"]["aws_lambda_function"] = {"ingestion": {"environment": {"variables": {"PYIFC_COMPONENT": "ingest-all"}}}}
        self.assertEqual(x.pyifc_export(tf)["uses"], ["ingest-all>bucket:archive:write"])

    def test_pyifc_noise_keeps_lambda_updates(self):
        summary = {"no_op": 0, "changes": [
            {"address": "aws_lambda_function.query", "type": "aws_lambda_function", "action": "update"},
            {"address": "docker_container.app", "type": "docker_container", "action": "replace"}]}
        self.assertEqual(x.plan_changes(summary)["filtered"], 2)
        got = x.plan_changes(summary, x.NOISE_BY_TOOL["pyifc"])
        self.assertEqual(got["filtered"], 1)
        self.assertEqual(got["changes"], [{"resource": "other:aws_lambda_function.query", "action": "update"}])

    def test_extract_case_writes_the_common_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "T0"
            (case / "raw" / "generated").mkdir(parents=True)
            (case / "raw" / "graph.json").write_text((FIXTURES / "graph.json").read_text())
            (case / "raw" / "generated" / "app.tf.json").write_text((FIXTURES / "app.tf.json").read_text())
            written = x.extract_case("pyifc", case)
            self.assertEqual(sorted(written), ["p2_model.json", "p3_export.json", "signals.json"])
            self.assertEqual(json.loads((case / "p2_model.json").read_text()), self.oracle())

    def test_run_log_with_a_5xx_status_or_traceback_is_a_runtime_signal(self):
        for text in ("POST speed: 500\n", "Traceback (most recent call last):\n  File x\n"):
            with tempfile.TemporaryDirectory() as tmp:
                case = Path(tmp)
                (case / "raw").mkdir()
                (case / "raw" / "run-server.log").write_text(text)
                self.assertEqual([s["stage"] for s in x.signals(case, "pyifc", [])], ["runtime"], text)


class PlanTests(unittest.TestCase):
    def test_noise_is_counted_and_buckets_are_named(self):
        summary = {"no_op": 3, "changes": [
            {"address": "docker_container.app", "type": "docker_container", "action": "replace"},
            {"address": "module.service_a.aws_lambda_function.function", "type": "aws_lambda_function", "action": "update"},
            {"address": "module.bucket_staging.aws_s3_bucket.bucket", "type": "aws_s3_bucket", "action": "delete"},
            {"address": "aws_s3_bucket.sensorhub_staging_v2", "type": "aws_s3_bucket", "action": "create"},
            {"address": "aws_iam_user_policy.ingestion", "type": "aws_iam_user_policy", "action": "update"},
        ]}
        got = x.plan_changes(summary)
        self.assertEqual(got["filtered"], 2)
        self.assertIn({"resource": "bucket:staging", "action": "destroy"}, got["changes"])
        self.assertIn({"resource": "bucket:staging-v2", "action": "create"}, got["changes"])
        self.assertIn({"resource": "other:aws_iam_user_policy.ingestion", "action": "update"}, got["changes"])


class SignalTests(unittest.TestCase):
    def test_traceback_in_run_log_is_a_runtime_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "raw").mkdir()
            (case / "raw" / "run-server.log").write_text("ok\nERROR:root:An unhandled error occurred in an api route handler: x\n")
            got = x.signals(case, "nitric", [])
            self.assertEqual([s["stage"] for s in got], ["runtime"])

    def test_expected_4xx_is_not_a_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "raw").mkdir()
            (case / "raw" / "run-server.log").write_text('ERR request failed error="Bad Request" http_code=400\n')
            self.assertEqual(x.signals(case, "encore", []), [])

    def make_run(self, tmp, base_log, case_log, base_stages=(), log="plan"):
        root = Path(tmp)
        for name, text in (("T0", base_log), ("T1", case_log)):
            (root / name / "raw").mkdir(parents=True)
            (root / name / "raw" / f"{log}.log").write_text(text)
        (root / "T0" / "stages.json").write_text(json.dumps(list(base_stages)))
        return root / "T1"

    def test_new_warning_against_the_baseline_is_a_signal_at_that_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_run(tmp, "Warning: old 1\n", "Warning: old 2\nWarning: new thing\n")
            got = x.signals(case, "nitric", [])
        self.assertEqual([s["stage"] for s in got], ["plan"])
        self.assertIn("new thing", got[0]["text"])

    def test_same_warnings_and_build_noise_are_not_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_run(tmp, "Warning: old 1\n", "Warning: old 7\nsvc [In Progress]: #6 3.1 WARNING: pip\n")
            self.assertEqual(x.signals(case, "nitric", []), [])

    def test_check_that_already_failed_on_the_baseline_is_not_a_signal(self):
        failed = [{"stage": "check", "status": "failed"}]
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_run(tmp, "", "", base_stages=failed)
            self.assertEqual(x.signals(case, "nitric", failed), [])
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_run(tmp, "", "", base_stages=[{"stage": "check", "status": "ok"}])
            self.assertEqual([s["stage"] for s in x.signals(case, "nitric", failed)], ["edit"])

    def test_failed_stages_map_to_edit_and_generation(self):
        stages = [{"stage": "check", "status": "failed", "command": "encore test"}, {"stage": "export", "status": "failed"}]
        with tempfile.TemporaryDirectory() as tmp:
            got = x.signals(Path(tmp), "encore", stages)
        self.assertEqual([s["stage"] for s in got], ["edit", "generation"])


if __name__ == "__main__":
    unittest.main()
