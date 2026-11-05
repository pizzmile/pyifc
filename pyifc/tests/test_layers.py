"""The package is organised in layers; a layer may import only from the layers below it."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "pyifc"

# layer -> the layers it may import from (besides itself)
ALLOWED = {
    "core": set(),
    "authoring": {"core"},
    "analysis": {"core"},
    "identity": {"core"},
    "runtime": {"core", "authoring"},
    "backends": {"core", "runtime"},
}

# Known violations, to be removed rather than extended. With PYIFC_TARGET=aws the decorators wrap a
# component in a Lambda handler, a second path next to pyifc.runtime.entry, which unwraps it again.
KNOWN_EXCEPTIONS = {("authoring/decorators.py", "pyifc.runtime.aws_adapters")}


def _imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno


def test_every_layer_imports_only_from_the_layers_below():
    violations = []
    for layer, allowed in ALLOWED.items():
        for path in (PACKAGE / layer).rglob("*.py"):
            for module, line in _imports(path):
                parts = module.split(".")
                if parts[0] != "pyifc" or len(parts) < 2 or parts[1] == layer:
                    continue
                if parts[1] not in allowed and (path.relative_to(PACKAGE).as_posix(), module) not in KNOWN_EXCEPTIONS:
                    violations.append(f"{path.relative_to(PACKAGE)}:{line} imports {module}")
    assert not violations, "\n".join(violations)


def test_every_module_belongs_to_a_layer():
    top_level = {p.name for p in PACKAGE.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert top_level == set(ALLOWED)
