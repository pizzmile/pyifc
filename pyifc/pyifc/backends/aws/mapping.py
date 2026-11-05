"""What the AWS backend can express and how pyifc kinds map to AWS: the single source of truth for the matrix, IAM actions and Terraform types."""

from __future__ import annotations

# Single source of truth for what the AWS generator can emit. Anything
# outside this matrix is rejected by generate_terraform() with
# UnsupportedByTarget; it is never silently dropped.
SUPPORTED_CAPABILITIES: dict[str, frozenset[str]] = {
    "object_store": frozenset({"read", "write", "list", "delete"}),
    "queue": frozenset({"publish", "subscribe"}),
    "key_value_store": frozenset({"read", "write", "delete"}),
    "topic": frozenset({"publish", "subscribe"}),
    "secret": frozenset({"read"}),
    # A schedule is a trigger, not something a component holds capabilities on.
    "schedule": frozenset(),
}


SUPPORTED_COMPONENT_KINDS = frozenset({"http_handler", "worker", "scheduled_job"})


SUPPORTED_WORKER_TRIGGER_PREFIX = "queue:"


# A worker is triggered by a queue (event source mapping) or a topic (SNS
# subscription); the prefix fixes which resource kind the trigger must name.
WORKER_TRIGGER_KINDS = {"queue:": "queue", "topic:": "topic"}


SCHEDULE_TRIGGER_PREFIX = "schedule:"


# Allowlist of per-resource "aws" overrides (see override_problems()). Identity,
# lifecycle and anything IAM-related are deliberately absent. ``versioning`` is
# a bool that the generator turns into a separate aws_s3_bucket_versioning
# resource; every other entry is merged into the resource block as-is.
SUPPORTED_OVERRIDES: dict[str, frozenset[str]] = {
    "object_store": frozenset({"tags", "versioning"}),
    "queue": frozenset({
        "tags",
        "visibility_timeout_seconds",
        "message_retention_seconds",
        "delay_seconds",
        "receive_wait_time_seconds",
        "max_message_size",
    }),
    "key_value_store": frozenset({"tags"}),
    "topic": frozenset({"tags"}),
    "secret": frozenset({"tags"}),
    "schedule": frozenset(),
}


# Worker Lambdas use this timeout; AWS rejects an SQS event source mapping whose
# queue visibility timeout is shorter than the function timeout.
LAMBDA_TIMEOUT_SECONDS = 30


# Every Lambda runs the same entry point; PYIFC_COMPONENT picks the component and
# PYIFC_APP_MODULE the module that holds the application.
ENTRY_HANDLER = "pyifc.runtime.entry.handler"


OPERATION_ACTIONS: dict[str, dict[str, tuple[str | None, str]]] = {
    "object_store": {
        "read": ("s3:GetObject", "object"),
        "write": ("s3:PutObject", "object"),
        "list": ("s3:ListBucket", "bucket"),
        "delete": ("s3:DeleteObject", "object"),
    },
    "queue": {
        "publish": ("sqs:SendMessage", "queue"),
        "subscribe": ("sqs:ReceiveMessage", "queue"),
    },
    # Item-level actions only, matching the adapter's get/put/delete. There is
    # no list capability for key-value stores, so Query/Scan are never granted.
    "key_value_store": {
        "read": ("dynamodb:GetItem", "table"),
        "write": ("dynamodb:PutItem", "table"),
        "delete": ("dynamodb:DeleteItem", "table"),
    },
    "topic": {
        "publish": ("sns:Publish", "topic"),
        # Delivery to a subscribed Lambda is authorised by the resource-based
        # aws_lambda_permission, so the subscriber's role needs no SNS action.
        "subscribe": (None, "topic"),
    },
    "secret": {
        "read": ("secretsmanager:GetSecretValue", "secret"),
    },
}


# Terraform resource type per kind, used for lifecycle protection and ARNs.
TERRAFORM_TYPES = {
    "object_store": "aws_s3_bucket",
    "queue": "aws_sqs_queue",
    "key_value_store": "aws_dynamodb_table",
    "topic": "aws_sns_topic",
    "secret": "aws_secretsmanager_secret",
    "schedule": "aws_cloudwatch_event_rule",
}


# Extra provider endpoints (Floci) per new kind. Emitted only when the
# specification uses the kind, so specifications that only use buckets and
# queues keep their exact previous output.
KIND_PROVIDER_ENDPOINTS = {
    "key_value_store": "dynamodb",
    "topic": "sns",
    "secret": "secretsmanager",
    "schedule": "events",
}


# Lambda endpoint variable (read by pyifc.runtime.aws_adapters) per kind with a runtime
# adapter; scripts/pyifc/pyifc-tf-gen.py sets these when an endpoint is given.
KIND_LAMBDA_ENDPOINT_VARIABLES = {
    "key_value_store": "DYNAMODB_ENDPOINT_URL",
    "topic": "SNS_ENDPOINT_URL",
    "secret": "SECRETSMANAGER_ENDPOINT_URL",
}


# Lambda environment variable prefix and Terraform attribute per new kind.
KIND_ENVIRONMENT = {
    "key_value_store": ("PYIFC_TABLE_", "name"),
    "topic": ("PYIFC_TOPIC_", "arn"),
    "secret": ("PYIFC_SECRET_", "arn"),
}
