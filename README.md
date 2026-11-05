# pyifc

A comparison of common Infrastructure from Code (IfC) tools and an experiment on building one for Python. The full write-up is in [`report.pdf`](report.pdf) (*Infrastructure from Code: Nitric, Encore and a Python Prototype*, Giacomo Pizzamiglio).

## Executive summary

**Problem.** A cloud application is described twice: the code calls resources, and separate infrastructure files declare them and grant permissions. Nothing checks that the two agree, so they drift. A new call without a permission fails at run time. A removed call with its permission left behind goes unnoticed and breaks least privilege. IfC derives the infrastructure from the application code to avoid this.

**Question.** Does IfC keep code and infrastructure aligned when the application changes?

**Method.**
- Of five tools considered, Nitric and Encore are the active ones. Wing is pre-release, Klotho is discontinued and Shuttle has ceased operations.
- I compared Nitric and Encore on one application, a train sensor hub (HTTP ingestion, two topics, two parsers, two buckets, a query endpoint), with one port per tool.
- Eight change scenarios cover the two directions of drift: a use without a permission, and a permission or subscription with no counterpart in the code. They also include a bucket rename and consistent changes that should pass.
- I built pyifc, a minimal annotation-based IfC tool for Python, and ran it through the same scenarios.
- Nothing was deployed to AWS. Baselines were applied to Floci (an AWS emulator) and each change was planned with Terraform against them.

**Findings on Nitric and Encore.**
- Both carry a change from code to infrastructure when code and declarations change together.
- When the two diverge, neither tool reports it. Of the five detect and protect scenarios, Nitric passes one and Encore two.
- Missed in both tools:
  - a leftover permission after the call is removed;
  - a subscriber with no publisher;
  - a bucket rename, which shows up in the plan as a deletion and re-creation.
- In Encore, access is the type of a reference in the code. A new reference with a write silently widens access, so alignment with the code does not give least privilege.
- In Nitric, nothing compares declarations with calls. A write through a read-only handle passes in the local run.

**pyifc.**
- The developer declares resources and the capabilities each component uses. The analyzer parses the source with `ast`, without running it, and compares declared with used capabilities.
- Errors stop generation: an undeclared use, a subscriber without a publisher.
- Warnings continue with the narrower result: an unused capability gets no permission.
- Each component gets its own IAM role.
- A lock file keeps resource identity, so a rename becomes a Terraform `moved` entry instead of a deletion. Stateful resources are protected with `prevent_destroy`.
- It passes all eight scenarios, with a signal before the plan wherever Nitric or Encore missed one.
- The cost is about 15 decorator lines for the four sensor hub components, and the analyzer accepts only a subset of Python.

**Limitations.** One small application, single-edit scenarios run once by one evaluator, no real AWS deployment, and the Encore export comes from a compiler I built on its model. pyifc targets AWS through Terraform only.

**Conclusion.** The gaps in Nitric and Encore can be caught by checks run before generation. Further work: deploy to AWS, cover queues, databases and shared helpers, add targets for other clouds, use language models to propose declarations that the analyzer then verifies, and try the same checks on the Nitric and Encore models.

## Repository layout

| Path | What |
| --- | --- |
| `report.pdf` | the report |
| `pyifc/` | the pyifc library (`pyifc/pyifc/`), its tests and a small example; see `pyifc/README.md` |
| `sensorhub/` | the sensor hub reference app: Python backend, static frontend, Docker stack |
| `tools/nitric/sensorhub/` | the sensor hub on Nitric (Python), with a Terraform deploy to Floci |
| `tools/encore/sensorhub/` | the sensor hub on Encore (Go), with a compiler from its model to Terraform and a deploy to Floci |
| `tools/pyifc/sensorhub/` | the sensor hub on pyifc, with a deploy to Floci in `tools/pyifc/deploy/` |
| `tools/tests/`, `tools/tests.md` | the change scenarios T0 to T6: definitions, runner, analysis and unit tests |
| `Makefile` | one entry point for all of the above; `make help` lists the targets |

Floci is an AWS emulator that runs in Docker. Nothing in this repository is deployed to AWS.

## Running the code

Everything is run from the repository root. `make help` lists all targets.

### Requirements

- Python 3.11 or newer, GNU make 3.81 or newer.
- For the Docker stack and for anything on Floci: Docker, and Terraform 1.10 or newer.
- For the Nitric port: the Nitric CLI (tested with 1.61.1) and a Python 3.12 virtualenv with `tools/nitric/sensorhub/requirements.txt`.
- For the Encore port: the Encore CLI (tested with 1.58.6) and Go.

### pyifc

```sh
make pyifc-venv           # creates pyifc/.venv and installs pyifc with its test dependencies
make pyifc-test           # the library's tests
make pyifc-smoke          # runs the sensor hub port locally, with capability enforcement

source pyifc/.venv/bin/activate
cd tools/pyifc/sensorhub
pyifc check app.py                              # diagnostics only
pyifc graph app.py -o graph.json                # capability graph
pyifc generate app.py --out out --init-lock     # Terraform JSON, IAM policies, lock file
pyifc run app.py --state state --smoke smoke.py
```

To deploy the pyifc port to Floci, build its Lambda package and run the smoke test against it:
`tools/pyifc/deploy/up.sh`, and `tools/pyifc/deploy/down.sh` to remove it. Details are in
`tools/pyifc/deploy/README.md`.

### Sensor hub reference app

```sh
make hub-up       # builds and starts the Docker stack; dashboard at http://localhost:8000/ui/
make hub-smoke    # posts one reading of each kind and waits for both results
make hub-down
```

The backend unit tests need a virtualenv with `sensorhub/src/backend/requirements-dev.txt`:
`make hub-test HUB_PY=<venv>/bin/python`.

### Nitric and Encore ports

```sh
# Nitric: put the Python 3.12 venv first on PATH, the services start with plain `python`
make nitric-start NITRIC_VENV_BIN=<venv>/bin      # terminal 1
make nitric-smoke                                 # terminal 2
make nitric-floci-up nitric-deploy                # Terraform export applied to Floci (7 to 12 min)
make nitric-down

# Encore
make encore-run                                   # terminal 1, API on :4000
make encore-smoke                                 # terminal 2
make encore-test
make encore-deploy                                # model compiled to Terraform, applied to Floci
make encore-down
```

### Change scenarios

The eight scenarios T0 to T6 are defined in `tools/tests.md`. The runner applies each change to a
copy of a port, records the tool's model, its Terraform export, the plan and the run, and checks
them against the expected outcome.

```sh
make scenarios-list                       # the stages each scenario runs, for the three tools
make pyifc-floci-up                       # Floci for pyifc
make baseline TOOL=pyifc RUN=my-run       # deploy T0 to Floci; the plan stage compares against it
make scenarios TOOL=pyifc RUN=my-run      # all scenarios (about 2 minutes)
make extract RUN=my-run                   # turn the raw output into analysis files
make report                               # rewrite the results block in tools/tests.md
```

`make report` replaces the results block that is in `tools/tests.md` with the one computed from `tools/tests/runs/`.

Output goes to `tools/tests/runs/<run>/` and is not committed. `TOOL=nitric` and `TOOL=encore` work the
same way; their baselines are described in section 6 of `tools/tests.md`. Run one tool at a time, they
share local ports and Docker. The recorded results of the report are in section 8 of `tools/tests.md`.

### Tests

```sh
make test                                 # harness tests, Encore compiler tests, pyifc tests
make test HUB_PY=<venv>/bin/python        # and the sensor hub backend tests
```
