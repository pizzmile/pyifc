"""Command line: ``pyifc check|graph|generate|run``.

Exit codes: 0 ok, 1 analyzer errors, 2 identity or generation refusal. Diagnostics go to
stderr as ``<file>:<line>: <severity> <code>: <message>``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import tomllib
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pyifc.analysis import AnalysisResult, analyze_file
from pyifc.backends.aws import (
    KIND_LAMBDA_ENDPOINT_VARIABLES,
    UnsupportedByTarget,
    default_physical_name,
    generate_terraform,
)
from pyifc.backends.iam_mode import DEFAULT_IAM_MODE, IAM_MODES, split_capabilities
from pyifc.core.diagnostics import Diagnostic
from pyifc.identity import (
    LOCK_FILE_NAME,
    LockError,
    load_lock,
    resolve_identity,
    write_lock,
)

CONFIG_FILE_NAME = "pyifc.toml"
CONFIG_KEYS = ("name_prefix", "region", "endpoint")
DEFAULT_REGION = "us-east-1"


class InputError(Exception):
    """The application file cannot be read."""


class ConfigError(ValueError):
    """``pyifc.toml`` is unreadable or holds an unusable value."""


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.command(args)
    except ConfigError as error:
        print(f"{CONFIG_FILE_NAME}: error E_CONFIG: {error}", file=sys.stderr)
        return 2
    except InputError as error:
        print(f"{error.args[0]}: error E_APP_UNREADABLE: {error.args[1]}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyifc", description="Infrastructure-from-code for pyifc applications.")
    commands = parser.add_subparsers(dest="name", required=True)

    check = commands.add_parser("check", help="analyze the application and print diagnostics")
    check.add_argument("app", type=Path)
    check.set_defaults(command=_check)

    graph = commands.add_parser("graph", help="write the capability graph as JSON")
    graph.add_argument("app", type=Path)
    graph.add_argument("-o", "--output", type=Path, required=True)
    graph.set_defaults(command=_graph)

    generate = commands.add_parser("generate", help="write Terraform JSON for the application")
    generate.add_argument("app", type=Path)
    generate.add_argument("--out", type=Path, required=True, help="directory for app.tf.json and diagnostics.json")
    generate.add_argument("--lock", type=Path, help=f"lock file (default: {LOCK_FILE_NAME} next to the app)")
    generate.add_argument("--init-lock", action="store_true", help="create the lock file on the first generation")
    generate.add_argument("--destroy", action="append", default=[], metavar="ID", help="resource id that may be destroyed")
    generate.add_argument("--iam-mode", choices=IAM_MODES, default=DEFAULT_IAM_MODE)
    generate.add_argument("--package", type=Path, help="Lambda package; its hash becomes source_code_hash")
    generate.add_argument("--endpoint", help="AWS-compatible endpoint, such as Floci")
    generate.set_defaults(command=_generate)

    run = commands.add_parser("run", help="run a smoke script against the local runner")
    run.add_argument("app", type=Path)
    run.add_argument("--state", type=Path, required=True, help="directory for local resource state")
    run.add_argument("--no-check", action="store_true", help="skip the analyzer")
    run.add_argument("--smoke", type=Path, required=True, help="Python file defining main(app) -> int")
    run.set_defaults(command=_run)
    return parser


def _analyze(app: Path) -> AnalysisResult:
    try:
        return analyze_file(app)
    except (OSError, ValueError) as error:
        raise InputError(str(app), getattr(error, "strerror", None) or str(error)) from error


# --- commands ---------------------------------------------------------------------------


def _check(args: argparse.Namespace) -> int:
    result = _analyze(args.app)
    _print(result.diagnostics)
    return 1 if result.errors else 0


def _graph(args: argparse.Namespace) -> int:
    result = _analyze(args.app)
    _print(result.diagnostics)
    if result.errors or result.specification is None:
        return 1
    _atomic_write(args.output, json.dumps(result.specification, indent=2, sort_keys=True) + "\n")
    return 0


def _generate(args: argparse.Namespace) -> int:
    config = _load_config(args.app.parent)
    result = _analyze(args.app)
    _print(result.diagnostics)
    if result.errors or result.specification is None:
        return 1
    specification = result.specification
    diagnostics: list[Diagnostic] = list(result.diagnostics)

    lock_path = args.lock if args.lock is not None else args.app.parent / LOCK_FILE_NAME
    try:
        lock = load_lock(lock_path)
    except LockError as error:
        return _refuse(args.out, diagnostics, [Diagnostic("E_LOCK_INVALID", "error", str(error), str(lock_path), 1, 0)])

    identity = resolve_identity(
        specification,
        lock,
        physical_name_for=lambda resource_id: default_physical_name(resource_id, specification["name"]),
        destroy=frozenset(args.destroy),
        init=args.init_lock,
    )
    if identity.errors:
        return _refuse(args.out, diagnostics, list(identity.diagnostics))
    diagnostics.extend(identity.diagnostics)

    _, dropped = split_capabilities(specification, args.iam_mode)
    for edge in dropped:
        source = edge.get("source") or {}
        diagnostics.append(
            Diagnostic(
                "W_GRANT_DROPPED",
                "warning",
                f"component '{edge['component_id']}' {edge['operation']} on '{edge['resource_id']}' "
                "is declared but not seen in use; no IAM grant is generated",
                source.get("file", args.app.name),
                source.get("line", 1),
                source.get("column", 0),
            )
        )

    package_arguments: dict[str, Any] = {}
    if args.package is not None:
        try:
            package_bytes = args.package.read_bytes()
        except OSError as error:
            return _refuse(args.out, diagnostics, [Diagnostic("E_PACKAGE", "error", f"cannot read package: {error}", str(args.package), 1, 0)])
        package_arguments = {
            "lambda_package": str(args.package),
            "package_hash": base64.b64encode(hashlib.sha256(package_bytes).digest()).decode("ascii"),
        }
    else:
        package_arguments = {"lambda_package": f"{specification['name']}.zip"}

    region = config.get("region", DEFAULT_REGION)
    endpoint = args.endpoint if args.endpoint is not None else config.get("endpoint")
    try:
        document = generate_terraform(
            specification,
            region=region,
            endpoint=endpoint,
            lambda_environment=_lambda_environment(specification, region, endpoint),
            iam_mode=args.iam_mode,
            physical_names=identity.physical_names,
            moved=identity.moved,
            **package_arguments,
        )
    except UnsupportedByTarget as error:
        refusals = [Diagnostic("E_UNSUPPORTED_BY_TARGET", "error", problem, args.app.name, 1, 0) for problem in error.problems]
        return _refuse(args.out, diagnostics, refusals)
    if "name_prefix" in config:
        document["variable"]["name_prefix"]["default"] = config["name_prefix"]

    args.out.mkdir(parents=True, exist_ok=True)
    _atomic_write(args.out / "app.tf.json", json.dumps(document, indent=2, sort_keys=True) + "\n")
    _write_diagnostics(args.out, diagnostics)
    _print(diagnostics[len(result.diagnostics):])
    write_lock(lock_path, identity.lock)  # only after the Terraform file exists
    return 0


def _run(args: argparse.Namespace) -> int:
    if not args.app.is_file():
        raise InputError(str(args.app), "no such file")
    if not args.no_check:
        result = _analyze(args.app)
        _print(result.diagnostics)
        if result.errors:
            return 1
    app_directory = str(args.app.resolve().parent)
    added_to_path = app_directory not in sys.path
    if added_to_path:
        sys.path.insert(0, app_directory)  # the application imports its sibling packages
    try:
        from pyifc.runtime.local import LocalApp

        smoke = _load_script(args.smoke)
        main_function = getattr(smoke, "main", None)
        if not callable(main_function):
            raise RuntimeError(f"{args.smoke} does not define main(app)")
        args.state.mkdir(parents=True, exist_ok=True)
        local = LocalApp(args.app, args.state, enforce=True)
        return int(main_function(local))
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        if added_to_path and app_directory in sys.path:
            sys.path.remove(app_directory)


# --- helpers ----------------------------------------------------------------------------


def _refuse(out: Path, diagnostics: Sequence[Diagnostic], refusals: Sequence[Diagnostic]) -> int:
    """Record a refusal: diagnostics.json is written, app.tf.json and the lock stay as they are."""
    everything = [*diagnostics, *refusals]
    out.mkdir(parents=True, exist_ok=True)
    _write_diagnostics(out, everything)
    _print(refusals)
    return 2


def _write_diagnostics(out: Path, diagnostics: Sequence[Diagnostic]) -> None:
    records = [
        {"code": item.code, "severity": item.severity, "message": item.message, "file": item.file, "line": item.line}
        for item in diagnostics
    ]
    _atomic_write(out / "diagnostics.json", json.dumps(records, indent=2, sort_keys=True) + "\n")


def _print(diagnostics: Sequence[Diagnostic]) -> None:
    for item in diagnostics:
        print(f"{item.file}:{item.line}: {item.severity} {item.code}: {item.message}", file=sys.stderr)


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(descriptor, 0o666 & ~_umask())  # mkstemp creates the file with mode 0600
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def _load_config(directory: Path) -> dict[str, str]:
    path = directory / CONFIG_FILE_NAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error
    unknown = sorted(set(data) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError(f"{path}: unknown keys {unknown}; expected {list(CONFIG_KEYS)}")
    for key, value in data.items():
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{path}: '{key}' must be a non-empty string")
    return data


def _lambda_environment(specification: dict[str, Any], region: str, endpoint: str | None) -> dict[str, str]:
    environment = {"AWS_DEFAULT_REGION": region}
    if endpoint:
        environment.update(S3_ENDPOINT_URL=endpoint, SQS_ENDPOINT_URL=endpoint)
        for kind in sorted({resource["kind"] for resource in specification["resources"]}):
            if kind in KIND_LAMBDA_ENDPOINT_VARIABLES:
                environment[KIND_LAMBDA_ENDPOINT_VARIABLES[kind]] = endpoint
    return environment


def _load_script(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("pyifc_smoke_script", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load smoke script from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(main())
