"""IAM policies: one role per component, with a statement per capability it keeps."""

from __future__ import annotations

from typing import Any

from pyifc.backends.aws.implied import implied_actions
from pyifc.backends.aws.mapping import OPERATION_ACTIONS, TERRAFORM_TYPES
from pyifc.backends.aws.validation import UnsupportedByTarget


def component_policy(
    component_id: str,
    capabilities: list[dict[str, Any]],
    resources: dict[str, dict[str, Any]],
    names: dict[str, str],
    floci_sns_publish_wildcard: bool = False,
) -> dict[str, Any]:
    statements: list[dict[str, Any]] = []
    for capability in capabilities:
        if capability["component_id"] != component_id:
            continue
        resource = resources[capability["resource_id"]]
        operation = OPERATION_ACTIONS.get(resource["kind"], {}).get(capability["operation"])
        if operation is None:  # unreachable: rejected by unsupported_problems()
            raise UnsupportedByTarget([
                f"component '{component_id}': capability '{capability['operation']}' "
                f"on {resource['kind']} '{capability['resource_id']}' is not supported"
            ])
        action, scope = operation
        arn = _resource_arn(resource["kind"], names[capability["resource_id"]], scope)
        extra = implied_actions(resource["kind"], capability["operation"])
        if action is None:  # authorised by a resource-based policy, not the role
            if extra:
                statements.append({"Action": list(extra), "Effect": "Allow", "Resource": [arn]})
            continue
        if floci_sns_publish_wildcard and action == "sns:Publish":
            # Emulator workaround only: Floci cannot match the topic ARN that
            # sns:Publish carries in the request body, so it denies every
            # ARN-scoped grant. Never use for a real deployment.
            arn = "*"
        statements.append({"Action": [action], "Effect": "Allow", "Resource": [arn]})
        if resource["kind"] == "queue" and capability["operation"] == "subscribe":
            statements[-1]["Action"] = [
                "sqs:ChangeMessageVisibility",
                "sqs:DeleteMessage",
                "sqs:GetQueueAttributes",
                "sqs:ReceiveMessage",
            ]
        if extra:
            statements.append({"Action": list(extra), "Effect": "Allow", "Resource": [arn]})
    statements.append({
        "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        "Effect": "Allow",
        "Resource": ["*"],
    })
    return {"Version": "2012-10-17", "Statement": statements}


def _resource_arn(kind: str, terraform_name: str, scope: str) -> str:
    arn = f"${{{TERRAFORM_TYPES[kind]}.{terraform_name}.arn}}"
    return f"{arn}/*" if scope == "object" else arn


def lambda_trust_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
        }],
    }
