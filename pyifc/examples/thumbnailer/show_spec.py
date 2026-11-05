from __future__ import annotations

import json
from pathlib import Path
import sys

from pyifc import analyze_file


def main() -> int:
    app_path = Path(__file__).with_name("app.py")
    result = analyze_file(app_path)

    for diagnostic in result.diagnostics:
        print(
            f"{diagnostic.file}:{diagnostic.line}:{diagnostic.column}: "
            f"{diagnostic.severity} {diagnostic.code}: {diagnostic.message}",
            file=sys.stderr,
        )

    if result.specification is None:
        return 1

    print(
        json.dumps(
            result.specification,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())