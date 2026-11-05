"""IAM grant mode: which capability edges feed IAM policy derivation.

"declared" (default): every edge in the specification becomes a grant, exactly
what the developer declared.

"used": an edge becomes a grant unless its provenance is exactly "declared",
that is, declared in the code but never seen in use. Edges with provenance
"used", "both" or "implied" are kept, and so is any edge with a missing or
unknown provenance, because "not seen in use" can only be claimed for a
specification the analyzer produced.

There are no special cases by operation or component kind. The analyzer already
records what the runtime needs without a visible call: a worker's subscribe on
its own trigger is "both" (the event source mapping polls the queue with the
function's execution role, so the trigger is the use). A publish is "both" only
when the body calls ``publish``; an unused publish stays "declared", gets
W_UNUSED_CAPABILITY and, in this mode, no grant.

Not capability-derived, hence not affected by the mode at all: the fixed
CloudWatch Logs statement every role gets, the event source mapping and API
Gateway invoke permission (topology, not IAM policy), and the
PYIFC_BUCKET_*/PYIFC_QUEUE_* environment variables (these still cover every
declared capability so the runtime can bind each declared resource).

"Not seen in use" is only sound because the analyzer rejects everything it
cannot follow (aliases, getattr, resources passed to calls, nested or helper
functions, indirect method use) as an error. Inside the accepted subset a
resource operation is either a direct, visible call or absent. Inferred-but-
undeclared stays an analyzer error (E_UNDECLARED_USE) in both modes.
"""
from __future__ import annotations

from typing import Any

IAM_MODES = ("declared", "used")
DEFAULT_IAM_MODE = "used"


def validate_iam_mode(mode: str) -> str:
    if mode not in IAM_MODES:
        raise ValueError(f"Unknown iam_mode {mode!r}; expected one of {list(IAM_MODES)}.")
    return mode


def split_capabilities(
    specification: dict[str, Any], mode: str = DEFAULT_IAM_MODE
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, dropped) capability edges for ``mode``; order is preserved."""
    validate_iam_mode(mode)
    capabilities = list(specification["capabilities"])
    if mode == "declared":
        return capabilities, []
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for capability in capabilities:
        if capability.get("provenance") == "declared":
            dropped.append(capability)
        else:
            kept.append(capability)
    return kept, dropped


def dropped_grants_report(specification: dict[str, Any], mode: str) -> list[str]:
    """Sorted, human-readable lines for the grants ``mode`` drops (empty for "declared")."""
    _, dropped = split_capabilities(specification, mode)
    return sorted(
        f"dropped grant: component '{item['component_id']}' {item['operation']} "
        f"on '{item['resource_id']}' (declared, not seen in use)"
        for item in dropped
    )
