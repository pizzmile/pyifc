"""Terraform blocks for individual resource kinds and for trigger wiring (SNS subscriptions, EventBridge targets)."""

from __future__ import annotations

from pyifc.backends.aws.mapping import SCHEDULE_TRIGGER_PREFIX
from pyifc.backends.aws.schedule import aws_schedule_expression


def _emit_key_value_store(tf, name, physical, resource, overrides, protected):
    # One hash key "pk" (string); the runtime adapter stores the value bytes in
    # the binary attribute "value". No range key: the vocabulary has no
    # list/query capability for key-value stores.
    tf.setdefault("aws_dynamodb_table", {})[name] = {
        "name": physical,
        "billing_mode": "PAY_PER_REQUEST",
        "hash_key": "pk",
        "attribute": [{"name": "pk", "type": "S"}],
        **overrides,
    }


def _emit_topic(tf, name, physical, resource, overrides, protected):
    tf.setdefault("aws_sns_topic", {})[name] = {
        "name": physical,
        **overrides,
    }


def _emit_secret(tf, name, physical, resource, overrides, protected):
    # Only the secret container is declared; its value is never in Terraform
    # and must be set out of band. Without deletion protection (the explicit
    # destructive path) it is deleted immediately instead of after the default
    # 30-day recovery window, which would otherwise block re-creating the name.
    tf.setdefault("aws_secretsmanager_secret", {})[name] = {
        "name": physical,
        **({} if protected else {"recovery_window_in_days": 0}),
        **overrides,
    }


def _emit_schedule(tf, name, physical, resource, overrides, protected):
    tf.setdefault("aws_cloudwatch_event_rule", {})[name] = {
        "name": physical,
        "schedule_expression": aws_schedule_expression(resource["properties"]["cron"]),
    }


NEW_KIND_EMITTERS = {
    "key_value_store": _emit_key_value_store,
    "topic": _emit_topic,
    "secret": _emit_secret,
    "schedule": _emit_schedule,
}


def emit_topic_subscriptions(tf, components, component_names, names) -> None:
    for component in components:
        trigger = component.get("trigger") or ""
        if component["kind"] != "worker" or not trigger.startswith("topic:"):
            continue
        name = component_names[component["id"]]
        topic = names[trigger.removeprefix("topic:")]
        tf.setdefault("aws_lambda_permission", {})[name] = {
            "statement_id": f"AllowSns{name.title().replace('_', '')}",
            "action": "lambda:InvokeFunction",
            "function_name": f"${{aws_lambda_function.{name}.function_name}}",
            "principal": "sns.amazonaws.com",
            "source_arn": f"${{aws_sns_topic.{topic}.arn}}",
        }
        tf.setdefault("aws_sns_topic_subscription", {})[name] = {
            "topic_arn": f"${{aws_sns_topic.{topic}.arn}}",
            "protocol": "lambda",
            "endpoint": f"${{aws_lambda_function.{name}.arn}}",
            "depends_on": [f"aws_lambda_permission.{name}"],
        }


def emit_schedule_targets(tf, components, component_names, names) -> None:
    for component in components:
        trigger = component.get("trigger") or ""
        if component["kind"] != "scheduled_job" or not trigger.startswith(SCHEDULE_TRIGGER_PREFIX):
            continue
        name = component_names[component["id"]]
        rule = names[trigger.removeprefix(SCHEDULE_TRIGGER_PREFIX)]
        tf.setdefault("aws_cloudwatch_event_target", {})[name] = {
            "rule": f"${{aws_cloudwatch_event_rule.{rule}.name}}",
            "target_id": name,
            "arn": f"${{aws_lambda_function.{name}.arn}}",
        }
        tf.setdefault("aws_lambda_permission", {})[name] = {
            "statement_id": f"AllowEventBridge{name.title().replace('_', '')}",
            "action": "lambda:InvokeFunction",
            "function_name": f"${{aws_lambda_function.{name}.function_name}}",
            "principal": "events.amazonaws.com",
            "source_arn": f"${{aws_cloudwatch_event_rule.{rule}.arn}}",
        }
