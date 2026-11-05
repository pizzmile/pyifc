#!/usr/bin/env python3
"""Run change-test scenarios on a tool's sensor hub port and keep the raw output.

For each scenario: copy the port to a scratch folder, apply the scenario's edit, run the stages
its phases need, and store every log and artifact. Nothing is parsed here; turning the raw output
into the p2..p6 files that ifctests.py reads is a separate step.

Usage:
  run.py TOOL ID [ID ...]   [--run RUN_ID] [--stages a,b]    TOOL = nitric | encore | pyifc; ID = T0.. or 'all'
  run.py TOOL --list                                          show the stages each scenario would run

Output: tools/tests/runs/<run-id>/<tool>/<ID>/   stages.json, source.patch, raw/ (logs, artifacts)
Scratch: tools/tests/.build/<tool>/<ID>/app      (the port itself is never modified)

Environment: NITRIC_VENV_BIN  bin folder of a Python 3.12 venv with the Nitric SDK (default: none, so the
nitric stages use whatever `python` is on PATH). A baseline state for the `plan` stage comes from the tool's own
deploy/up.sh (see BASELINE below); without it `plan` is skipped and says so.
PYIFC_PYTHON  Python for the pyifc stages (default: pyifc/.venv/bin/python if it exists, else python3); the
library is used from <repo>/pyifc through PYTHONPATH. The pyifc baseline (`--baseline`) needs Floci on 4572
(tools/pyifc/deploy/floci-up.sh) and leaves terraform.tfstate, the package and pyifc.lock.json in .baseline/pyifc;
every pyifc scenario copy receives that lock, and the generate stage is skipped without it.
"""
import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ifctests  # noqa: E402
from edits import encore as encore_edits  # noqa: E402
from edits import nitric as nitric_edits  # noqa: E402
from edits import pyifc as pyifc_edits  # noqa: E402
from edits.lib import EditError  # noqa: E402

TOOLS_DIR = HERE.parent
REPO = TOOLS_DIR.parent
PORTS = {"nitric": TOOLS_DIR / "nitric/sensorhub", "encore": TOOLS_DIR / "encore/sensorhub",
         "pyifc": TOOLS_DIR / "pyifc/sensorhub"}
EDITS = {"nitric": nitric_edits.EDITS, "encore": encore_edits.EDITS, "pyifc": pyifc_edits.EDITS}
BUILD = HERE / ".build"
RUNS = HERE / "runs"
VENV_BIN = Path(os.environ["NITRIC_VENV_BIN"]) if os.environ.get("NITRIC_VENV_BIN") else None
# State left by each tool's own deploy script; the plan stage compares against it.
BASELINE = {
    "nitric": HERE / ".baseline/nitric",  # T0 export applied to Floci by hand (see tests.md section 6)
    "encore": TOOLS_DIR / "encore/sensorhub/deploy/generated",
    "pyifc": HERE / ".baseline/pyifc",  # T0 generated with --init-lock, applied to Floci by --baseline
}
FLOCI = {"nitric": "http://localhost:4570", "encore": "http://localhost:4571", "pyifc": "http://localhost:4572"}
COPY_IGNORE = shutil.ignore_patterns("deploy", ".nitric", "cdktf.out", "__pycache__", "*.pyc", ".encore",
                                     ".venv", ".mypy_cache", "node_modules", "generated")

# (stage, phases it produces, stages it needs)
STAGES = {
    "encore": [("check", set(), []), ("model", {"P2"}, []), ("inventory", {"P2"}, []),
               ("export", {"P3"}, ["model"]), ("plan", {"P4"}, ["export"]), ("run", {"P6"}, []),
               ("deploy", {"P5"}, [])],
    "nitric": [("check", set(), []), ("spec", {"P2"}, []), ("generate", {"P3"}, []),
               ("plan", {"P4"}, ["generate"]), ("run", {"P6"}, []), ("deploy", {"P5"}, [])],
    "pyifc": [("check", set(), []), ("model", {"P2"}, []), ("generate", {"P3"}, []),
              ("plan", {"P4"}, ["generate"]), ("run", {"P6"}, []), ("deploy", {"P5"}, [])],
}


# ------------------------------------------------------------------- helpers

class Ctx:
    def __init__(self, tool, sid, app, raw):
        self.tool, self.sid, self.app, self.raw = tool, sid, app, raw
        self.env = dict(os.environ)
        if tool == "nitric" and VENV_BIN is not None and VENV_BIN.is_dir():
            self.env["PATH"] = f"{VENV_BIN}:{self.env['PATH']}"
        if tool == "pyifc":
            self.env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO / "pyifc"), self.env.get("PYTHONPATH")]))


def sh(ctx, name, cmd, cwd=None, env=None, timeout=900, stdout_to=None):
    """Run a command; its output goes to raw/<name>.log (stdout to `stdout_to` if given)."""
    log = ctx.raw / f"{name}.log"
    with open(log, "w") as lf:
        lf.write("$ " + " ".join(map(str, cmd)) + "\n")
        lf.flush()
        kw = dict(cwd=cwd or ctx.app, env=env or ctx.env, stdin=subprocess.DEVNULL, timeout=timeout)
        if stdout_to:
            with open(stdout_to, "w") as out:
                p = subprocess.run(cmd, stdout=out, stderr=lf, **kw)
        else:
            p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, **kw)
    return p.returncode


def http_status(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def api_alive(port):
    """The API answers /readings with anything but "not found" or a gateway error (a broken handler counts)."""
    return http_status(f"http://localhost:{port}/readings") not in (0, 404, 502, 503)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listening_ports(pid):
    """TCP ports a process (and no other) listens on, via lsof."""
    out = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
                         capture_output=True, text=True).stdout
    return sorted({int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line.startswith("n") and ":" in line})


def native_run(ctx, start_cmd, smoke_cmd_for, find_port, after=None):
    """Start the tool's native runtime, wait for the API, run the smoke script, stop everything."""
    started = time.time()
    with open(ctx.raw / "run-server.log", "w") as lf:
        proc = subprocess.Popen(start_cmd, cwd=ctx.app, env=ctx.env, stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        port = None
        while time.time() - started < 180 and port is None and proc.poll() is None:
            port = find_port(proc)
            if port is None:
                time.sleep(2)
        if port is None:
            return {"status": "failed", "note": "API did not come up within 180 s (see run-server.log)"}
        code = sh(ctx, "run-smoke", smoke_cmd_for(port), cwd=ctx.app, env={**ctx.env, "API_URL": f"http://localhost:{port}"})
        try:  # what the API returns after the smoke test, and tool-specific state
            with urllib.request.urlopen(f"http://localhost:{port}/readings?limit=100", timeout=5) as r:
                (ctx.raw / "run-readings.json").write_bytes(r.read())
        except urllib.error.HTTPError as e:
            (ctx.raw / "run-readings.json").write_text(f'{{"http_status": {e.code}}}')
        except Exception as e:
            (ctx.raw / "run-readings.json").write_text(f'{{"error": "{e}"}}')
        evidence = after(ctx, port) if after else None
        return {"exit": code, "command": " ".join(smoke_cmd_for(port)),
                "note": f"API on port {port}; smoke script exit {code} (non-zero is a result, not a runner error)"
                        + (f"; {evidence}" if evidence else "")}
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=20)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass


def terraform_plan(ctx, tf_dir):
    base = BASELINE[ctx.tool]
    if not (base / "terraform.tfstate").exists():
        return {"status": "skipped", "note": f"no baseline state at {base}; "
                f"run tools/{ctx.tool}/sensorhub/deploy/up.sh once"}
    for f in ("terraform.tfstate", ".terraform.lock.hcl"):
        if (base / f).exists():
            shutil.copy2(base / f, tf_dir / f)
    if ctx.tool == "pyifc":  # the baseline's package: same bytes, so only real changes show up as Lambda updates
        for f in base.glob("*.zip"):
            shutil.copy2(f, tf_dir / f.name)
    env = {**ctx.env, "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": "us-east-1",
           "AWS_ENDPOINT_URL": FLOCI[ctx.tool], "AWS_S3_USE_PATH_STYLE": "true"}
    tf = ["terraform", f"-chdir={tf_dir}"]
    if sh(ctx, "plan-init", tf + ["init", "-input=false", "-no-color"], env=env) != 0:
        return {"exit": 1, "command": "terraform init", "note": "init failed (plan-init.log)"}
    code = sh(ctx, "plan", tf + ["plan", "-input=false", "-no-color", "-refresh=false", "-lock=false", "-out=plan.bin"],
              env=env, timeout=1800)
    if code not in (0, 2):
        return {"exit": code, "command": "terraform plan", "note": "plan failed (plan.log)"}
    sh(ctx, "plan-show", tf + ["show", "-json", "plan.bin"], env=env, stdout_to=ctx.raw / "plan.json")
    changes = plan_summary(ctx.raw / "plan.json")
    (ctx.raw / "plan-summary.json").write_text(json.dumps(changes, indent=1))
    return {"exit": 0, "command": "terraform plan",
            "note": f"{len(changes['changes'])} resources change, {changes['no_op']} unchanged"}


def plan_summary(plan_json):
    """Resources a plan creates, updates, replaces or deletes (everything else is counted as unchanged)."""
    data = json.loads(Path(plan_json).read_text() or "{}")
    changes, no_op = [], 0
    for rc in data.get("resource_changes", []):
        actions = rc["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            no_op += 1
            continue
        kind = "replace" if actions in (["delete", "create"], ["create", "delete"]) else actions[0]
        changes.append({"address": rc["address"], "type": rc["type"], "action": kind})
    return {"changes": sorted(changes, key=lambda c: c["address"]), "no_op": no_op}


# --------------------------------------------------------------------- stages

def encore_check(ctx):
    return {"exit": sh(ctx, "check", ["encore", "test", "./..."]), "command": "encore test ./..."}


def encore_model(ctx):
    code = sh(ctx, "model", ["encore", "debug", "meta", "-f", "json"], stdout_to=ctx.raw / "model.json")
    return {"exit": code, "command": "encore debug meta -f json"}


def encore_inventory(ctx):
    """What Encore says it needs bound when no infra config is given (exit 1 is the normal result)."""
    code = sh(ctx, "inventory", ["encore", "build", "docker", "--arch", "arm64", "sensorhub-inventory:tmp"])
    text = (ctx.raw / "inventory.log").read_text()
    return {"exit": 0 if "missing resource configurations" in text.lower() else code,
            "command": "encore build docker (no --config)", "note": f"tool exit {code}"}


def encore_export(ctx):
    code = sh(ctx, "export", ["python3", "-I", str(TOOLS_DIR / "encore/sensorhub/deploy/compile.py"),
                              "--model", str(ctx.raw / "model.json"), "--out", str(ctx.raw / "generated")])
    return {"exit": code, "command": "compile.py --model model.json"}


def encore_plan(ctx):
    gen = ctx.raw / "generated"
    declared = set(json.loads((gen / "main.tf.json").read_text()).get("resource", {}).get("aws_s3_bucket", {}))
    for f in sorted((PORTS["encore"] / "deploy/workarounds").glob("*.tf.json")):  # same files up.sh applies
        text = f.read_text()
        # Workaround 03 names the buckets of the baseline app. After a bucket rename (T6) it must name the new
        # resource, or Terraform stops with "Reference to undeclared resource".
        for name in set(re.findall(r"aws_s3_bucket\.(\w+)", text)):
            if name not in declared and name + "_v2" in declared:
                text = re.sub(rf"aws_s3_bucket\.{name}\b", f"aws_s3_bucket.{name}_v2", text)
        (gen / f.name).write_text(text)
    return terraform_plan(ctx, gen)


def _audit_evidence(staging_dir):
    """Did the T2/T2b write reach the staging bucket? Only meaningful for those scenarios."""
    return "query-audit object: " + ("written" if (staging_dir / "query-audit/last.json").exists() else "absent")


def _encore_objects_root():
    for root in (Path.home() / "Library/Caches/encore/objects", Path.home() / ".cache/encore/objects"):
        if root.is_dir():
            return root
    return None


def encore_run(ctx):
    """Run in a fresh Encore namespace so objects from earlier runs are not listed; keep a copy of its buckets."""
    port = free_port()
    ns = f"ifc-{ctx.sid.lower()}-{int(time.time())}"
    root = _encore_objects_root()
    before = set(p.name for p in root.iterdir()) if root else set()
    subprocess.run(["encore", "namespace", "create", ns], cwd=ctx.app, env=ctx.env, capture_output=True)

    def after(ctx, port):
        new = [p for p in root.iterdir() if p.name not in before] if root else []
        if len(new) != 1:
            return f"objects: {len(new)} new object directories, none copied"
        shutil.copytree(new[0], ctx.raw / "objects", dirs_exist_ok=True)
        return _audit_evidence(ctx.raw / "objects/sensorhub-staging")

    try:
        return native_run(
            ctx, ["encore", "run", "--namespace", ns, "--port", str(port), "--browser", "never", "--watch=false"],
            lambda p: [str(PORTS["encore"] / "smoke.sh"), f"http://localhost:{p}"],
            lambda proc: port if api_alive(port) else None, after=after)
    finally:
        subprocess.run(["encore", "namespace", "delete", ns], cwd=ctx.app, env=ctx.env, capture_output=True)


def nitric_check(ctx):
    mypy = shutil.which("mypy", path=ctx.env["PATH"])
    if not mypy:
        return {"status": "skipped", "note": "mypy not installed"}
    return {"exit": sh(ctx, "check", [mypy, "--ignore-missing-imports", "--explicit-package-bases", "."]),
            "command": "mypy ."}


CACHED_DOCKERFILE = """\
# Same steps as the Dockerfile Nitric generates for Python services, except that requirements.txt is
# installed BEFORE the sources are copied, so a source edit does not rebuild the pip layer (under amd64
# emulation that layer costs minutes). Scratch copies only; the port keeps Nitric's own Dockerfile.
FROM python:3.11-slim
ARG HANDLER
ENV HANDLER=${HANDLER}
ENV PYTHONUNBUFFERED=TRUE
RUN apt-get update -y && apt-get install -y ca-certificates git && update-ca-certificates
RUN pip install --upgrade pip pipenv
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENTRYPOINT python -u $HANDLER
"""
NITRIC_YAML = """\
name: sensorhub
services:
  - match: services/*.py
    runtime: cached
    start: env PYTHONPATH=. python $SERVICE_PATH
runtimes:
  cached:
    dockerfile: ./cached.dockerfile
"""


def nitric_prepare(ctx):
    """Scratch-only changes so that `nitric spec/up` work and are fast; done once per scenario."""
    marker = ctx.app / ".prepared"
    if marker.exists():
        return
    # workaround 01 (deploy/workarounds/01-service-import-path.sh): put the project root on sys.path
    for f in (ctx.app / "services").glob("*.py"):
        f.write_text("import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))"
                     "  # workaround 01\n" + f.read_text())
    (ctx.app / "cached.dockerfile").write_text(CACHED_DOCKERFILE)
    (ctx.app / "nitric.yaml").write_text(NITRIC_YAML)
    marker.write_text("")


def nitric_spec(ctx):
    nitric_prepare(ctx)
    code = sh(ctx, "spec", ["nitric", "spec", "--no-builder", "--ci", "-o", str(ctx.raw / "spec.json")], timeout=3600)
    return {"exit": code, "command": "nitric spec --no-builder (workaround 01, cached Dockerfile)"}


def nitric_generate(ctx):
    nitric_prepare(ctx)
    code = sh(ctx, "generate", ["nitric", "up", "-s", "tf", "--no-builder", "--ci"], timeout=3600)
    stack = ctx.app / "cdktf.out/stacks/sensorhub-tf"
    if (stack / "cdk.tf.json").exists():
        shutil.rmtree(ctx.raw / "generated", ignore_errors=True)  # a rerun with --add replaces the earlier export
        shutil.copytree(stack, ctx.raw / "generated", ignore=shutil.ignore_patterns(".terraform", "terraform.tfstate*"))
    return {"exit": code, "command": "nitric up -s tf --no-builder (workaround 01, cached Dockerfile)"}


def patch_backend(tf_dir):
    """Nitric writes an absolute local-backend path (its build directory) into cdk.tf.json. Point it at
    terraform.tfstate in tf_dir so the plan reads the baseline state that was copied there."""
    path = Path(tf_dir) / "cdk.tf.json"
    data = json.loads(path.read_text())
    data["terraform"]["backend"]["local"]["path"] = "terraform.tfstate"
    path.write_text(json.dumps(data, indent=2))


def nitric_plan(ctx):
    src, dst = ctx.raw / "generated", ctx.raw / "plan-input"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    # workaround 02: Floci rejects Resource Groups; the baseline state was made without them
    subprocess.run(["python3", "-I", str(TOOLS_DIR / "nitric/sensorhub/deploy/workarounds/02-remove-resource-group.py"),
                    str(dst)], capture_output=True)
    patch_backend(dst)  # harness patch: the generated state path is absolute and per scenario
    return terraform_plan(ctx, dst)


def _nitric_port(proc):
    for port in listening_ports(proc.pid):
        if 4000 <= port < 4100 and api_alive(port):
            return port
    return None


def _nitric_state(ctx, port):
    """Request history, logs and bucket contents that `nitric start` keeps in .nitric/."""
    shutil.copytree(ctx.app / ".nitric", ctx.raw / "nitric-state", dirs_exist_ok=True)
    return _audit_evidence(ctx.raw / "nitric-state/run/buckets/staging")


def nitric_run(ctx):
    nitric_prepare(ctx)
    return native_run(ctx, ["nitric", "start"], lambda p: [str(PORTS["nitric"] / "smoke.sh")], _nitric_port,
                      after=_nitric_state)


def pyifc_python():
    """Interpreter for the pyifc stages: $PYIFC_PYTHON, else the repo's pyifc/.venv, else python3."""
    if os.environ.get("PYIFC_PYTHON"):
        return os.environ["PYIFC_PYTHON"]
    venv = REPO / "pyifc/.venv/bin/python"
    return str(venv) if venv.exists() else "python3"


def pyifc_cli(*args):
    return [pyifc_python(), "-m", "pyifc.cli", *args]


def pyifc_check(ctx):
    return {"exit": sh(ctx, "check", pyifc_cli("check", "app.py")), "command": "pyifc check app.py"}


def pyifc_model(ctx):
    code = sh(ctx, "model", pyifc_cli("graph", "app.py", "-o", str(ctx.raw / "graph.json")))
    return {"exit": code, "command": "pyifc graph app.py"}


# `<file>:<line>: <severity> <code>: <message>`; E_CONFIG and E_APP_UNREADABLE print `<file>: ...` without a line
DIAGNOSTIC_LINE = re.compile(r"^(\S+?)(?::(\d+))?: (error|warning) ([EW]_[A-Z0-9_]+): (.*)$")


def diagnostics_from_log(text):
    """Diagnostics a pyifc command printed to stderr, in the shape of diagnostics.json. The analyzer's refusal
    (exit 1) prints them but writes no file."""
    out = []
    for line in text.splitlines():
        m = DIAGNOSTIC_LINE.match(line)
        if m:
            out.append({"code": m.group(4), "severity": m.group(3), "message": m.group(5), "file": m.group(1), "line": int(m.group(2) or 0)})
    return out


def pyifc_generate(ctx):
    """Generate against Floci's endpoint with the baseline's lock. Exit 1 or 2 with an E_ diagnostic is the tool
    refusing (a result); raw/diagnostics.json holds the diagnostics either way."""
    lock = ctx.app / "pyifc.lock.json"
    if not lock.exists():
        return {"status": "skipped", "note": "no baseline lock; run `make baseline TOOL=pyifc` (needs Floci on 4572)"}
    out = ctx.raw / "generated"
    shutil.rmtree(out, ignore_errors=True)
    cmd = pyifc_cli("generate", "app.py", "--out", str(out), "--lock", str(lock), "--endpoint", FLOCI["pyifc"])
    code = sh(ctx, "generate", cmd)
    written = out / "diagnostics.json"
    if written.exists():
        shutil.copy2(written, ctx.raw / "diagnostics.json")
    else:
        (ctx.raw / "diagnostics.json").write_text(json.dumps(diagnostics_from_log((ctx.raw / "generate.log").read_text()), indent=1) + "\n")
    codes = sorted({d["code"] for d in json.loads((ctx.raw / "diagnostics.json").read_text()) if d["code"].startswith("E_")})
    note = f"exit {code}" + (f"; refused: {', '.join(codes)}" if code in (1, 2) and codes else "")
    return {"exit": code, "command": "pyifc generate app.py --lock pyifc.lock.json --endpoint " + FLOCI["pyifc"], "note": note}


def pyifc_plan(ctx):
    return terraform_plan(ctx, ctx.raw / "generated")


def pyifc_run(ctx):
    """The local runner with enforcement and the port's smoke script (it finds the sample data from the port's
    own location, so it is taken from the port, not the scratch copy). No server: the runner calls the handlers."""
    state = ctx.raw / "state"
    shutil.rmtree(state, ignore_errors=True)
    code = sh(ctx, "run-server", pyifc_cli("run", "app.py", "--state", str(state), "--no-check", "--smoke", str(PORTS["pyifc"] / "smoke.py")))
    readings = state / "readings.json"
    (ctx.raw / "run-readings.json").write_text(readings.read_text() if readings.exists() else '{"error": "no readings.json"}')
    return {"exit": code, "command": "pyifc run app.py --no-check --smoke smoke.py",
            "note": f"smoke script exit {code} (non-zero is a result, not a runner error); "
                    + _audit_evidence(state / "buckets/sensorhub-staging")}


def not_built(ctx):
    return {"status": "skipped", "note": "deployed-state capture (P5) is not implemented yet"}


STAGE_FUNCS = {
    "encore": {"check": encore_check, "model": encore_model, "inventory": encore_inventory, "export": encore_export, "plan": encore_plan,
               "run": encore_run, "deploy": not_built},
    "nitric": {"check": nitric_check, "spec": nitric_spec, "generate": nitric_generate, "plan": nitric_plan, "run": nitric_run,
               "deploy": not_built},
    "pyifc": {"check": pyifc_check, "model": pyifc_model, "generate": pyifc_generate, "plan": pyifc_plan, "run": pyifc_run,
              "deploy": not_built},
}


def stages_for(tool, phases, only=None):
    """Stages a scenario needs, dependencies first, in execution order."""
    table = STAGES[tool]
    needed = {name for name, ph, _ in table if ph & set(phases)} | {"check"}
    if only:
        needed = set(only)
    deps = {name: d for name, _, d in table}
    for name in list(needed):
        needed.update(deps[name])
    return [name for name, _, _ in table if name in needed]


def run_scenario(tool, sid, spec, run_id, only=None, add=False):
    """Run a scenario into runs/<run_id>/<tool>/<sid>. With add=True, run only the stages in `only` on an
    existing case: its raw/ files and earlier stage records stay, and the stages are not re-derived."""
    sc = spec.scenarios[sid]
    case = RUNS / run_id / tool / sid
    raw = case / "raw"
    scratch = BUILD / tool / sid
    previous = []
    if add:
        previous = json.loads((case / "stages.json").read_text())
        previous = [r for r in previous if r["stage"] not in only]
        shutil.rmtree(scratch, ignore_errors=True)
    else:
        for d in (case, scratch):
            shutil.rmtree(d, ignore_errors=True)
        raw.mkdir(parents=True)
    app = scratch / "app"
    shutil.copytree(PORTS[tool], app, ignore=COPY_IGNORE)
    if tool == "pyifc" and (BASELINE[tool] / "pyifc.lock.json").exists():
        shutil.copy2(BASELINE[tool] / "pyifc.lock.json", app / "pyifc.lock.json")  # the identity of the baseline's resources
    ctx = Ctx(tool, sid, app, raw)
    records = list(previous)
    try:
        EDITS[tool][sid](app)
    except EditError as e:
        records.append({"stage": "edit", "status": "failed", "note": str(e)})
        (case / "stages.json").write_text(json.dumps(records, indent=2))
        return records
    if not add:
        diff = subprocess.run(["diff", "-ruN", "-x", "deploy", "-x", ".nitric", "-x", "cdktf.out", "-x", "__pycache__",
                               "-x", "generated", "-x", ".encore", "-x", "pyifc.lock.json", str(PORTS[tool]), str(app)], capture_output=True, text=True).stdout
        (case / "source.patch").write_text(diff)
        records.append({"stage": "edit", "status": "ok", "note": f"{len(diff.splitlines())} diff lines"})
    done = {r["stage"]: r["status"] for r in previous}
    deps = {name: d for name, _, d in STAGES[tool]}
    wanted = [n for n, _, _ in STAGES[tool] if n in only] if add else stages_for(tool, sc.phases, only)
    for name in wanted:
        bad = [d for d in deps[name] if done.get(d) != "ok"]
        t0 = time.time()
        if bad:
            res = {"status": "skipped", "note": f"dependency {bad[0]} did not succeed"}
        else:
            try:
                res = STAGE_FUNCS[tool][name](ctx)
            except subprocess.TimeoutExpired:
                res = {"status": "failed", "note": "timeout"}
        status = res.get("status") or ("ok" if res.get("exit") == 0 else "failed")
        done[name] = status
        records.append({"stage": name, "status": status, "exit": res.get("exit"), "command": res.get("command"),
                        "seconds": round(time.time() - t0, 1), "note": res.get("note", "")})
        print(f"  {sid} {name}: {status} {res.get('note', '')}", flush=True)
    (case / "stages.json").write_text(json.dumps(records, indent=2))
    return records


def ecr_call(endpoint, target, body):
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": f"AmazonEC2ContainerRegistry_V20150921.{target}",
        "Authorization": "AWS4-HMAC-SHA256 Credential=test/20260101/us-east-1/ecr/aws4_request, SignedHeaders=host, Signature=x"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read() or b"{}")


def delete_leftover_repositories(endpoint, prefix="sensorhub_services-"):
    """Floci's ECR registry keeps its repositories in the Docker volume floci-aws-ecr-registry-data, which every
    Floci instance on the machine shares. After a failed baseline the repositories are still there and the next
    apply fails with RepositoryAlreadyExists. Delete only the ones this app creates; leave the volume alone.
    Floci reads the registry's catalog only when the first CreateRepository starts or adopts the registry
    container, so create one throwaway repository before listing."""
    ecr_call(endpoint, "CreateRepository", {"repositoryName": "warmup"})
    for repo in ecr_call(endpoint, "DescribeRepositories", {}).get("repositories", []):
        if repo["repositoryName"].startswith(prefix) or repo["repositoryName"] == "warmup":
            ecr_call(endpoint, "DeleteRepository", {"repositoryName": repo["repositoryName"], "force": True})


PACKAGE_SCRIPT = ("import sys; from pathlib import Path; from pyifc.backends.aws.package import build_package; "
                  "build_package([Path(sys.argv[1])], Path(sys.argv[2]))")


def pyifc_baseline(env):
    """T0 generated with --init-lock next to its own package (no requirements: the plan stage compares resources, the
    Lambdas are never invoked), applied to Floci on 4572. The folder keeps what every scenario needs: pyifc.lock.json
    (copied into each scratch app), sensorhub.zip (copied next to each plan, so the Lambda hash does not move),
    terraform.tfstate and .terraform.lock.hcl."""
    base = BASELINE["pyifc"]
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    env = {**env, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO / "pyifc"), env.get("PYTHONPATH")]))}
    subprocess.run(["sh", str(TOOLS_DIR / "pyifc/deploy/floci-up.sh")], check=True)
    subprocess.run([pyifc_python(), "-c", PACKAGE_SCRIPT, str(PORTS["pyifc"]), str(base / "sensorhub.zip")], env=env, check=True)
    subprocess.run(pyifc_cli("generate", str(PORTS["pyifc"] / "app.py"), "--out", str(base), "--lock", str(base / "pyifc.lock.json"),
                             "--init-lock", "--endpoint", FLOCI["pyifc"]), env=env, check=True)
    tf = ["terraform", f"-chdir={base}"]
    subprocess.run(tf + ["init", "-input=false", "-no-color"], env=env, check=True)
    return subprocess.run(tf + ["apply", "-input=false", "-auto-approve", "-no-color"], env=env).returncode


def make_baseline(tool, run_id):
    """Deploy the T0 export on Floci once; the plan stage compares every scenario with the state this leaves.
    Nitric: a fresh T0 export in run `run_id`, with workaround 02 and the backend patch. Encore: deploy/up.sh.
    pyifc: pyifc_baseline."""
    env = {**os.environ, "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": "us-east-1",
           "AWS_ENDPOINT_URL": FLOCI[tool], "AWS_S3_USE_PATH_STYLE": "true"}
    env.pop("AWS_PROFILE", None)
    if tool == "encore":
        return subprocess.run(["sh", str(PORTS["encore"] / "deploy/up.sh")]).returncode
    if tool == "pyifc":
        return pyifc_baseline(env)
    # Nitric's export names the exact image ids it was built from, so it has to be applied while they exist:
    # generate T0 now (this replaces the T0 export of the run) and apply that one.
    run_scenario("nitric", "T0", ifctests.load_spec(), run_id, ["generate"], add=True)
    src = RUNS / run_id / "nitric/T0/raw/generated"
    subprocess.run(["sh", str(PORTS["nitric"] / "deploy/floci-up.sh")], check=True)
    delete_leftover_repositories(FLOCI["nitric"])
    shutil.rmtree(BASELINE["nitric"], ignore_errors=True)
    shutil.copytree(src, BASELINE["nitric"], ignore=shutil.ignore_patterns(".terraform", "terraform.tfstate*"))
    subprocess.run(["python3", "-I", str(PORTS["nitric"] / "deploy/workarounds/02-remove-resource-group.py"),
                    str(BASELINE["nitric"])], check=True)
    patch_backend(BASELINE["nitric"])
    tf = ["terraform", f"-chdir={BASELINE['nitric']}"]
    subprocess.run(tf + ["init", "-input=false", "-no-color"], env=env, check=True)
    return subprocess.run(tf + ["apply", "-input=false", "-auto-approve", "-no-color"], env=env).returncode


def tool_versions():
    def first(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.strip().splitlines()[0]
        except Exception:
            return "?"
    try:
        import tomllib
        pyifc = tomllib.loads((REPO / "pyifc/pyproject.toml").read_text())["project"]["version"]
    except Exception:
        pyifc = "?"
    return {"date": date.today().isoformat(), "encore": first(["encore", "version"]),
            "nitric": first(["nitric", "version"]), "pyifc": pyifc, "terraform": first(["terraform", "version"])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tool", choices=sorted(PORTS))
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--run", default=date.today().isoformat(), help="run id (folder under runs/)")
    ap.add_argument("--stages", help="comma list; overrides the stages derived from the scenario's phases")
    ap.add_argument("--add", action="store_true",
                    help="with --stages: run only those stages on an existing run, keeping its other results")
    ap.add_argument("--baseline", action="store_true",
                    help="deploy the T0 export on Floci to create the state the plan stage compares with")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    if args.baseline:
        return make_baseline(args.tool, args.run)
    spec = ifctests.load_spec()
    runnable = [s for s, sc in spec.scenarios.items() if sc.status == "defined" and s in EDITS[args.tool]]
    ids = runnable if args.ids == ["all"] else args.ids
    only = args.stages.split(",") if args.stages else None
    if args.add and not only:
        ap.error("--add needs --stages")
    if args.list or not ids:
        for sid in runnable:
            print(f"{sid}: {' -> '.join(stages_for(args.tool, spec.scenarios[sid].phases, only))}")
        return 0
    for sid in ids:
        if sid not in runnable:
            print(f"error: {sid} is not a defined scenario with an edit for {args.tool}", file=sys.stderr)
            return 2
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update(tool_versions())
    meta_path.write_text(json.dumps(meta, indent=2))
    failed = False
    for sid in ids:
        print(f"{args.tool} {sid}", flush=True)
        records = run_scenario(args.tool, sid, spec, args.run, only, add=args.add)
        failed |= any(r["status"] == "failed" and r["stage"] == "edit" for r in records)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
