#!/usr/bin/env python3
"""WORKAROUND 01: point the Lambda environment at Floci's address on the Docker network.

Usage: 01-lambda-endpoint.py APP_TF_JSON HOST_ENDPOINT LAMBDA_ENDPOINT

`pyifc generate --endpoint http://localhost:4572` writes that URL into the provider block
(correct: Terraform runs on the host) and into every Lambda's *_ENDPOINT_URL variables
(wrong inside a Lambda container, where localhost is the container itself: the boto3
clients fail with EndpointConnectionError). This replaces the value in the
aws_lambda_function environments only; the provider block is left alone.
"""

import json
import sys
from pathlib import Path

path, host_endpoint, lambda_endpoint = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
document = json.loads(path.read_text())
changed = 0
for function in document["resource"].get("aws_lambda_function", {}).values():
    variables = function.get("environment", {}).get("variables", {})
    for key, value in variables.items():
        if value == host_endpoint:
            variables[key] = lambda_endpoint
            changed += 1
path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
print(f"workaround 01: {changed} Lambda endpoint variables -> {lambda_endpoint}")
