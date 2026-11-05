# Infrastructure from Code: common commands. `make` or `make help` lists them.
# Needs GNU make 3.81 or newer. Heavy targets (Docker, Floci, Nitric, Encore, pyifc baseline) are never run by `test`.

.DEFAULT_GOAL := help

PY := python3 -I
RUN ?= $(shell date +%Y-%m-%d)
ID ?=
TOOL ?=
NITRIC_VENV_BIN ?=
HUB_PY ?=
PYIFC_PYTHON ?= $(abspath pyifc/.venv/bin/python)
# run.py reads PYIFC_PYTHON itself (same name); export it only when that interpreter exists, run.py falls back to python3.
ifneq ($(wildcard $(PYIFC_PYTHON)),)
export PYIFC_PYTHON
endif
COMMA := ,

# Nitric starts its services with plain `python`: put the venv first on PATH.
# run.py reads NITRIC_VENV_BIN itself (export it only when set).
ifneq ($(NITRIC_VENV_BIN),)
export NITRIC_VENV_BIN
NPATH := PATH="$(NITRIC_VENV_BIN):$$PATH"
endif

CHECK_TOOL = @case "$(TOOL)" in nitric|encore|pyifc) ;; *) echo "set TOOL=nitric|encore|pyifc"; exit 1;; esac
CHECK_ID = @test -n "$(ID)" || { echo "set ID=<scenario id>, e.g. ID=T6"; exit 1; }
# Nitric exports name image ids that exist only briefly, and pyifc plans from the output of its own generate stage,
# so for both `plan` regenerates right before planning.
PLAN_STAGES = $(if $(filter nitric pyifc,$(TOOL)),generate$(COMMA)plan,plan)

.PHONY: help test typecheck hub-test hub-up hub-smoke hub-down \
	nitric-start nitric-smoke nitric-floci-up nitric-deploy nitric-down \
	encore-run encore-test encore-smoke encore-deploy encore-down \
	pyifc-venv pyifc-test pyifc-smoke pyifc-floci-up \
	scenarios-list scenarios scenario baseline plan extract report report-check clean

help: ## Show this list
	@awk 'BEGIN{FS=":.*## "} /^##@ /{printf "\n%s\n", substr($$0,5)} /^[a-z-]+:.*## /{printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\nVariables: RUN=%s  TOOL=nitric|encore|pyifc  ID=<scenario>  NITRIC_VENV_BIN=<venv/bin>  HUB_PY=<venv python>  PYIFC_PYTHON=<pyifc venv python>\n' "$(RUN)"

##@ Tests
test: ## Fast unit tests: tools/tests, Encore compiler, pyifc library (if its venv exists), sensor hub (if HUB_PY is set)
	$(PY) -m unittest discover -s tools/tests
	$(PY) -m unittest discover -s tools/encore/sensorhub/deploy -p 'test_compile.py'
	@if [ -x $(PYIFC_PYTHON) ]; then $(MAKE) --no-print-directory pyifc-test; else echo "pyifc library tests skipped (run: make pyifc-venv)"; fi
	@if [ -n "$(HUB_PY)" ]; then $(MAKE) --no-print-directory hub-test; else echo "sensor hub tests skipped (set HUB_PY to a venv python with sensorhub/src/backend/requirements-dev.txt)"; fi

hub-test: ## Sensor hub backend tests (needs HUB_PY: a venv python with sensorhub/src/backend/requirements-dev.txt)
	@test -n "$(HUB_PY)" || { echo "set HUB_PY=<venv>/bin/python"; exit 1; }
	$(HUB_PY) -I -c "import sys, unittest; sys.path.insert(0, 'sensorhub/src'); r = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('sensorhub/src/backend/tests')); sys.exit(not r.wasSuccessful())"

typecheck: ## mypy on tools/tests (skipped if mypy is not installed)
	@if $(PY) -c "import mypy" 2>/dev/null; then $(PY) -m mypy tools/tests; else echo "mypy not installed, skipping"; fi

##@ Sensor hub (Docker)
hub-up: ## Build and start the Docker stack
	sensorhub/docker/up.sh

hub-smoke: ## Post one reading of each kind and wait for both
	sensorhub/docker/smoke.sh

hub-down: ## Stop the stack (keeps stored readings)
	sensorhub/docker/down.sh

##@ Nitric
nitric-start: ## Run the port on the native runtime (blocks; the API port is found by nitric-smoke)
	cd tools/nitric/sensorhub && $(NPATH) nitric start

nitric-smoke: ## Smoke test against `nitric start` (API_URL=... to change the port)
	tools/nitric/sensorhub/smoke.sh

nitric-floci-up: ## Start the Floci container (floci-nitric, :4570)
	tools/nitric/sensorhub/deploy/floci-up.sh

nitric-deploy: ## Export to Terraform and apply on Floci (7 to 12 min)
	$(NPATH) tools/nitric/sensorhub/deploy/up.sh

nitric-down: ## Destroy the Floci deployment and remove its containers and images
	$(NPATH) tools/nitric/sensorhub/deploy/down.sh

##@ Encore
encore-run: ## Run the port locally (blocks; API on :4000)
	cd tools/encore/sensorhub && encore run --browser never

encore-test: ## encore test ./... in the port
	cd tools/encore/sensorhub && encore test ./...

encore-smoke: ## Smoke test against `encore run` (BASE_URL=... to change the port)
	tools/encore/sensorhub/smoke.sh

encore-deploy: ## Compile the model to Terraform and apply on Floci
	tools/encore/sensorhub/deploy/up.sh

encore-down: ## Destroy the Floci deployment and remove its state
	tools/encore/sensorhub/deploy/down.sh

##@ pyifc
pyifc-venv: ## Create pyifc/.venv and install pyifc[dev]
	python3 -m venv pyifc/.venv
	pyifc/.venv/bin/pip install -e "pyifc[dev]"

pyifc-test: ## pyifc library tests (needs pyifc-venv)
	@test -x $(PYIFC_PYTHON) || { echo "run: make pyifc-venv"; exit 1; }
	cd pyifc && $(abspath $(PYIFC_PYTHON)) -m pytest -q

pyifc-smoke: ## Run the sensor hub port on the local runner with enforcement (state in tools/tests/.build/pyifc-smoke)
	@test -x $(PYIFC_PYTHON) || { echo "run: make pyifc-venv"; exit 1; }
	PYTHONPATH=pyifc $(PYIFC_PYTHON) -m pyifc.cli run tools/pyifc/sensorhub/app.py --state tools/tests/.build/pyifc-smoke --smoke tools/pyifc/sensorhub/smoke.py

pyifc-floci-up: ## Start the Floci container for pyifc (:4572)
	tools/pyifc/deploy/floci-up.sh

##@ Scenarios (tools/tests/run.py, output under tools/tests/runs/RUN)
scenarios-list: ## List the stages each scenario would run, all three tools
	$(PY) tools/tests/run.py nitric --list
	$(PY) tools/tests/run.py encore --list
	$(PY) tools/tests/run.py pyifc --list

scenarios: ## All scenarios of TOOL (long)
	$(CHECK_TOOL)
	$(NPATH) $(PY) tools/tests/run.py $(TOOL) all --run $(RUN)

scenario: ## One scenario: TOOL=... ID=...
	$(CHECK_TOOL)
	$(CHECK_ID)
	$(NPATH) $(PY) tools/tests/run.py $(TOOL) $(ID) --run $(RUN)

baseline: ## Deploy T0 on Floci and keep the state for plan (TOOL=...)
	$(CHECK_TOOL)
	$(NPATH) $(PY) tools/tests/run.py $(TOOL) --baseline --run $(RUN)

plan: ## Add the plan stage to an existing run (TOOL=... ID=..., needs baseline)
	$(CHECK_TOOL)
	$(CHECK_ID)
	$(NPATH) $(PY) tools/tests/run.py $(TOOL) $(ID) --run $(RUN) --stages $(PLAN_STAGES) --add

extract: ## Turn the raw output of run RUN into analysis files
	$(PY) tools/tests/extract.py $(RUN)

report: ## Rewrite the results block in tools/tests.md
	$(PY) tools/tests/ifctests.py report

report-check: ## Fail if the results block is out of date
	$(PY) tools/tests/ifctests.py report --check

##@ Cleanup
clean: ## Remove tools/tests/.build, tools/tests/.baseline and __pycache__ (keeps runs/)
	rm -rf tools/tests/.build tools/tests/.baseline
	find tools -path tools/tests/runs -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
