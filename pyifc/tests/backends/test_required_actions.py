import inspect

import pytest

from pyifc.core.models import METHOD_CAPABILITY
from pyifc.runtime import aws_adapters as aws_runtime

KIND_OF = {
    aws_runtime.S3Bucket: "object_store",
    aws_runtime.SQSQueue: "queue",
    aws_runtime.DynamoDBTable: "key_value_store",
    aws_runtime.SNSTopic: "topic",
    aws_runtime.SecretsManagerSecret: "secret",
}


def test_every_adapter_class_is_covered_by_the_table():
    adapters = {
        cls for _, cls in inspect.getmembers(aws_runtime, inspect.isclass)
        if cls.__module__ == aws_runtime.__name__
    }
    assert adapters == set(KIND_OF)


@pytest.mark.parametrize("cls,kind", list(KIND_OF.items()))
def test_required_actions_keys_are_capabilities_of_the_kind(cls, kind):
    required = cls.REQUIRED_ACTIONS

    assert isinstance(required, dict)
    valid = {capability.value for (k, _), capability in METHOD_CAPABILITY.items() if k == kind}
    assert set(required) <= valid
    for actions in required.values():
        assert isinstance(actions, tuple)
        assert all(isinstance(a, str) and ":" in a for a in actions)
