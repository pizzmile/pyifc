#!/usr/bin/env python3
"""Build the Lambda package for the sensor-hub port with pyifc.backends.aws.package.build_package.

Usage: package.py ROOT OUT_ZIP [PORT_DIR]

Layout inside the zip: app.py, sensorhub_logic/ (from tools/pyifc/sensorhub), pyifc/
(from pyifc/pyifc) and the pinned requirements installed for python3.12 on linux arm64
(the Lambda runtime and architecture the generated Terraform asks for), so native
wheels such as rpds-py are the Linux ones, not those of the build host.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pyifc.backends.aws.package import build_package


def main(root: Path, out_zip: Path, port: Path | None = None) -> int:
    port = port or root / "tools" / "pyifc" / "sensorhub"
    requirements = Path(__file__).resolve().parent / "requirements-lambda.txt"
    with tempfile.TemporaryDirectory(prefix="pyifc-package-") as scratch:
        stage = Path(scratch) / "stage"
        stage.mkdir()
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
                "--disable-pip-version-check", "--only-binary=:all:",
                "--platform", "manylinux2014_aarch64", "--implementation", "cp",
                "--python-version", "3.12", "--abi", "cp312",
                "--requirement", str(requirements), "--target", str(stage),
            ],
            check=True,
        )
        shutil.copy2(port / "app.py", stage / "app.py")
        shutil.copytree(port / "sensorhub_logic", stage / "sensorhub_logic")
        shutil.copytree(root / "pyifc" / "pyifc", stage / "pyifc")
        result = build_package([stage], out_zip)
    print(f"{result.path} sha256(base64)={result.base64sha256}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    extra = Path(sys.argv[3]).resolve() if len(sys.argv) == 4 else None
    raise SystemExit(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), extra))
