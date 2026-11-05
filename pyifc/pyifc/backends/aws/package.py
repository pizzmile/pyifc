"""Deterministic Lambda deployment package: same inputs, same bytes, same hash."""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
_FILE_MODE = 0o644 << 16
_UNIX = 3
_SKIPPED_DIRECTORIES = {"__pycache__"}
_SKIPPED_SUFFIXES = {".pyc", ".pyo"}
_SKIPPED_NAMES = {".DS_Store"}


@dataclass(frozen=True)
class PackageResult:
    path: Path
    base64sha256: str  # base64 of the SHA-256 of the zip bytes, as Terraform filebase64sha256


def build_package(
    source_dirs: Sequence[Path],
    out_zip: Path,
    *,
    requirements: Path | None = None,
) -> PackageResult:
    """Zip ``source_dirs`` (and the installed ``requirements``) into ``out_zip``.

    Entries are sorted, carry a fixed timestamp and mode, and have no extra fields, so the
    bytes depend only on file names and contents. When two directories hold the same path,
    the later directory wins; installed requirements come first and so can be overridden.
    """
    out_zip = Path(out_zip)
    for directory in source_dirs:
        directory = Path(directory)
        if not directory.is_dir():
            raise FileNotFoundError(f"Source directory does not exist: {directory}")
        if directory.resolve() in out_zip.resolve().parents:
            raise ValueError(f"Output zip {out_zip} lies inside source directory {directory}")
    with tempfile.TemporaryDirectory(prefix="pyifc-requirements-") as scratch:
        roots = [Path(directory) for directory in source_dirs]
        if requirements is not None:
            _install(Path(requirements), Path(scratch))
            roots.insert(0, Path(scratch))
        files: dict[str, Path] = {}
        for root in roots:
            for path in root.rglob("*"):
                if path.is_file() and not _skipped(path.relative_to(root)):
                    files[path.relative_to(root).as_posix()] = path

        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, _TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = _UNIX
                info.external_attr = _FILE_MODE
                archive.writestr(info, files[name].read_bytes(), compresslevel=9)

    digest = hashlib.sha256(out_zip.read_bytes()).digest()
    return PackageResult(out_zip, base64.b64encode(digest).decode("ascii"))


def _skipped(relative: Path) -> bool:
    return (
        any(part in _SKIPPED_DIRECTORIES for part in relative.parts)
        or relative.suffix in _SKIPPED_SUFFIXES
        or relative.name in _SKIPPED_NAMES
    )


def _install(requirements: Path, target: Path) -> None:
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--quiet", "--no-compile", "--disable-pip-version-check",
            "--requirement", str(requirements), "--target", str(target),
        ],
        check=True,
    )
