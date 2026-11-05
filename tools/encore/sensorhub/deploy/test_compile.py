"""Tests for compile.py. Run: python3 -I -m unittest deploy/test_compile.py  (from tools/encore/sensorhub)"""
import json
import sys
import unittest
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import compile as c  # noqa: E402

MODEL = json.loads((here / "model.json").read_text())


class CompileTest(unittest.TestCase):
    def setUp(self):
        self.tf = c.build_terraform(MODEL, "http://x:1", "us-east-1", "img:t", "net", 8080)
        self.cfg = c.build_infra_config(MODEL, "http://x:1", "us-east-1", "1", "a", "e", "http://b")

    def test_deterministic(self):
        again = c.build_terraform(MODEL, "http://x:1", "us-east-1", "img:t", "net", 8080)
        self.assertEqual(c.dump(self.tf), c.dump(again))

    def test_resources_match_model(self):
        r = self.tf["resource"]
        self.assertEqual(len(r["aws_s3_bucket"]), len(MODEL["buckets"]))
        self.assertEqual(len(r["aws_sns_topic"]), len(MODEL["pubsub_topics"]))
        subs = sum(len(t["subscriptions"]) for t in MODEL["pubsub_topics"])
        self.assertEqual(len(r["aws_sns_topic_subscription"]), subs)
        self.assertEqual(len(r["aws_sqs_queue"]), 2 * subs)  # queue + dead-letter queue
        self.assertEqual(len(r["aws_iam_user"]), len(MODEL["svcs"]) + 1)  # + all-services

    def test_infra_config_names_match_model(self):
        self.assertEqual(set(self.cfg["object_storage"][0]["buckets"]), {b["name"] for b in MODEL["buckets"]})
        topics = self.cfg["pubsub"][0]["topics"]
        self.assertEqual(set(topics), {t["name"] for t in MODEL["pubsub_topics"]})
        for t in MODEL["pubsub_topics"]:
            self.assertEqual(set(topics[t["name"]]["subscriptions"]), {s["name"] for s in t["subscriptions"]})

    def test_actions_only_from_model(self):
        acc = c.service_access(MODEL)
        doc = c.policy_document(acc["query"])
        actions = {a for s in doc["statement"] for a in s["actions"]}
        self.assertEqual(actions, {"s3:ListBucket", "s3:GetObject"})  # query: LIST_OBJECTS, READ_OBJECT_CONTENTS
        doc = c.policy_document(acc["ingestion"])
        self.assertEqual({a for s in doc["statement"] for a in s["actions"]}, {"s3:PutObject", "sns:Publish"})

    def test_unsupported_is_rejected(self):
        bad = dict(MODEL, sql_databases=[{"name": "x"}])
        with self.assertRaises(c.ModelError):
            c.read_model(bad)


if __name__ == "__main__":
    unittest.main()
