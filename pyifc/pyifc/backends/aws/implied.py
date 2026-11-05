"""Extra IAM actions an adapter needs beyond the one its capability names.

Runtime adapters (pyifc.runtime.aws_adapters) declare ``REQUIRED_ACTIONS``, a dict of
capability name to extra IAM actions. The generator appends them, for the same
resource, whenever it keeps the (kind, capability) edge as a grant.
"""
from __future__ import annotations

# (resource kind, capability) -> extra IAM actions. Filled on first use from the
# adapter classes; entries already present (for example set by a test) win.
IMPLIED_ACTIONS: dict[tuple[str, str], tuple[str, ...]] = {}
_loaded = False

# Resource kind -> adapter class name in pyifc.runtime.aws_adapters.
_ADAPTER_CLASSES = {
    "object_store": "S3Bucket",
    "queue": "SQSQueue",
    "key_value_store": "DynamoDBTable",
    "topic": "SNSTopic",
    "secret": "SecretsManagerSecret",
}


def _load() -> None:
    global _loaded
    from pyifc.runtime import aws_adapters

    for kind, class_name in _ADAPTER_CLASSES.items():
        adapter = getattr(aws_adapters, class_name, None)
        for capability, actions in getattr(adapter, "REQUIRED_ACTIONS", {}).items():
            IMPLIED_ACTIONS.setdefault((kind, capability), tuple(actions))
    _loaded = True


def implied_actions(kind: str, capability: str) -> tuple[str, ...]:
    """Extra IAM actions for ``capability`` on a resource of ``kind`` (possibly empty)."""
    if not _loaded:
        _load()
    return tuple(IMPLIED_ACTIONS.get((kind, capability), ()))
