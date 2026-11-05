"""AWS target: Terraform JSON, IAM policies and Lambda packaging for a checked specification."""

from pyifc.backends.aws.generator import generate_terraform, write_terraform
from pyifc.backends.aws.mapping import (
    KIND_LAMBDA_ENDPOINT_VARIABLES,
    SUPPORTED_CAPABILITIES,
    SUPPORTED_OVERRIDES,
)
from pyifc.backends.aws.naming import default_physical_name
from pyifc.backends.aws.validation import UnsupportedByTarget

__all__ = [
    "KIND_LAMBDA_ENDPOINT_VARIABLES",
    "SUPPORTED_CAPABILITIES",
    "SUPPORTED_OVERRIDES",
    "UnsupportedByTarget",
    "default_physical_name",
    "generate_terraform",
    "write_terraform",
]
