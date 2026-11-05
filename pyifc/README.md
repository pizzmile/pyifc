# pyifc

A small Infrastructure from Code library for Python. The developer declares resources and the
capabilities each component uses; pyifc checks the declarations against the code with `ast`,
and generates Terraform for AWS with one IAM role per component.

```sh
pip install -e '.[dev]'        # Python 3.11 or newer
pyifc check app.py             # analyze, print diagnostics
pyifc graph app.py -o g.json   # capability graph
pyifc generate app.py --out out --init-lock
pyifc run app.py --smoke smoke.py
python -m pytest
```

## Layout

The package is split in layers. A layer imports only from the layers listed after it, and
`tests/test_layers.py` enforces that.

| Layer | Holds | Imports from |
| --- | --- | --- |
| `core` | kinds, capabilities, specification, diagnostics, naming rules, adapter binding | none |
| `authoring` | `Application`, resources, decorators, registry | core |
| `analysis` | the static analyzer: one module per kind of check | core |
| `identity` | lock file and rename inference | core |
| `runtime` | application loader, capability enforcement, local runner, Lambda entry and adapters | core, authoring |
| `backends` | AWS: Terraform generator, IAM policies, Lambda package | core, runtime |
| `cli.py` | the `pyifc` command | all |

`pyifc/__init__.py` re-exports what an application or a script needs: `Application`, the
resource classes, `analyze_file`, `generate_terraform`.

`tests/` mirrors the layers.
