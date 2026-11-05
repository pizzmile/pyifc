#!/bin/sh
# WORKAROUND 01: make the service files importable inside the container image.
#
# What : copies the app to deploy/.work/app and prepends one line to each
#        services/*.py that puts the project root on sys.path.
# Why  : `nitric up` builds each service with `python -u services/<name>.py`
#        from "/" and ignores the `start:` command of nitric.yaml (which sets
#        PYTHONPATH=. for `nitric start`). `from handlers import ...` then fails
#        with ModuleNotFoundError during resource collection, and `nitric up`
#        stops before generating anything.
# Fault: the app layout (handlers.py outside services/, relying on PYTHONPATH
#        from nitric.yaml) combined with Nitric's Python image, which has no
#        way to carry PYTHONPATH from nitric.yaml. Not Floci. The original
#        files in ../ are not touched.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
proj=$(CDPATH= cd -- "$here/../.." && pwd)
work="$here/../.work/app"
rm -rf "$work"; mkdir -p "$work"
tar -C "$proj" --exclude=./deploy --exclude=./.nitric --exclude=./cdktf.out \
    --exclude=__pycache__ -cf - . | tar -C "$work" -xf -
for f in "$work"/services/*.py; do
  { printf '%s\n' 'import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # workaround 01'; cat "$f"; } > "$f.new"
  mv "$f.new" "$f"
done
echo "workaround 01 applied in $work"
