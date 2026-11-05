import filecmp
import json
import os
import shutil
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ifctests  # noqa: E402
import run  # noqa: E402

SPEC = ifctests.load_spec()
DEFINED = [s for s, sc in SPEC.scenarios.items() if sc.status == "defined"]


def differs(a, b):
    """True if any file under b differs from, or is missing in, a (or b has new files)."""
    cmp = filecmp.dircmp(a, b)
    if cmp.diff_files or cmp.left_only or cmp.right_only:
        return True
    return any(differs(Path(a) / d, Path(b) / d) for d in cmp.common_dirs if d != "__pycache__")


class EditTests(unittest.TestCase):
    def test_every_defined_scenario_has_an_edit_for_all_three_tools(self):
        self.assertEqual(sorted(run.EDITS), ["encore", "nitric", "pyifc"])
        for tool, edits in run.EDITS.items():
            self.assertEqual(sorted(edits), sorted(DEFINED), tool)

    def test_pyifc_is_registered_everywhere_a_tool_must_be(self):
        for table in (run.PORTS, run.EDITS, run.BASELINE, run.FLOCI, run.STAGES, run.STAGE_FUNCS):
            self.assertIn("pyifc", table)
        self.assertEqual(run.FLOCI["pyifc"], "http://localhost:4572")
        self.assertEqual(sorted(n for n, _, _ in run.STAGES["pyifc"]), sorted(run.STAGE_FUNCS["pyifc"]))
        self.assertIn("pyifc", run.tool_versions())

    def test_edits_apply_to_a_clean_copy_and_change_it(self):
        for tool in run.PORTS:
            for sid in DEFINED:
                with self.subTest(tool=tool, scenario=sid), tempfile.TemporaryDirectory() as tmp:
                    app = Path(tmp) / "app"
                    shutil.copytree(run.PORTS[tool], app, ignore=run.COPY_IGNORE)
                    run.EDITS[tool][sid](app)
                    # compare with a second copy: the ignore list hides deploy/ and similar
                    clean = Path(tmp) / "clean"
                    shutil.copytree(run.PORTS[tool], clean, ignore=run.COPY_IGNORE)
                    self.assertEqual(differs(clean, app), sid != "T0")
                    if tool in ("nitric", "pyifc"):
                        for f in list(app.glob("*.py")) + list(app.glob("services/*.py")):
                            py_compile.compile(str(f), doraise=True, cfile=str(Path(tmp) / "x.pyc"))


class StagePlanTests(unittest.TestCase):
    def test_t2_edits_write_the_same_object_in_both_tools(self):
        """The two ports must do the same thing: one write to staging/query-audit/last.json, failing the request on error."""
        for sid in ("T2", "T2b"):
            for tool, mod, rel in (("encore", run.encore_edits, "query/query.go"), ("nitric", run.nitric_edits, "services/query.py"),
                                   ("pyifc", run.pyifc_edits, "app.py")):
                with tempfile.TemporaryDirectory() as tmp:
                    app = Path(tmp) / "app"
                    shutil.copytree(run.PORTS[tool], app, ignore=shutil.ignore_patterns("deploy", ".nitric", ".encore", "__pycache__"))
                    mod.EDITS[sid](app)
                    self.assertIn("query-audit/last.json", (app / rel).read_text(), f"{tool} {sid}")

    def test_encore_plan_pulls_in_dependencies(self):
        self.assertEqual(run.stages_for("encore", ["P2", "P3", "P4"]), ["check", "model", "inventory", "export", "plan"])

    def test_native_run_only_where_the_scenario_asks_for_it(self):
        for tool in ("nitric", "encore", "pyifc"):
            self.assertIn("run", run.stages_for(tool, SPEC.scenarios["T1"].phases))
            self.assertNotIn("run", run.stages_for(tool, SPEC.scenarios["N1"].phases))

    def test_stages_override_keeps_dependencies(self):
        self.assertEqual(run.stages_for("encore", ["P2", "P3", "P4"], only=["export"]), ["model", "export"])

    def test_pyifc_stages(self):
        self.assertEqual(run.stages_for("pyifc", ["P2", "P3", "P4", "P6"]), ["check", "model", "generate", "plan", "run"])
        self.assertEqual(run.stages_for("pyifc", ["P2", "P3", "P4"], only=["plan"]), ["generate", "plan"])

    def test_deployed_state_is_a_declared_gap(self):
        self.assertIn("deploy", run.stages_for("nitric", SPEC.scenarios["N4"].phases))


def pyifc_ctx(root, sid, edit=None, lock=True):
    """A stage context on a scratch copy of the pyifc port, with the lock a baseline would have left."""
    app, raw = Path(root) / "app", Path(root) / "raw"
    shutil.copytree(run.PORTS["pyifc"], app, ignore=run.COPY_IGNORE)
    raw.mkdir()
    ctx = run.Ctx("pyifc", sid, app, raw)
    if lock:
        code = subprocess.run(run.pyifc_cli("generate", "app.py", "--out", str(Path(root) / "init"), "--lock", str(app / "pyifc.lock.json"),
                                            "--init-lock"), cwd=app, env=ctx.env, capture_output=True).returncode
        assert code == 0
    if edit:
        run.EDITS["pyifc"][edit](app)
    return ctx


class PyifcCommandTests(unittest.TestCase):
    def test_interpreter_comes_from_the_environment_then_the_venv_then_python3(self):
        with mock.patch.dict(os.environ, {"PYIFC_PYTHON": "/x/python"}):
            self.assertEqual(run.pyifc_python(), "/x/python")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYIFC_PYTHON", None)
            venv = Path(tmp) / "pyifc/.venv/bin"
            with mock.patch.object(run, "REPO", Path(tmp)):
                self.assertEqual(run.pyifc_python(), "python3")
                venv.mkdir(parents=True)
                (venv / "python").write_text("")
                self.assertEqual(run.pyifc_python(), str(venv / "python"))

    def test_command_runs_the_cli_module_with_the_library_on_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = run.Ctx("pyifc", "T0", Path(tmp), Path(tmp))
        self.assertEqual(run.pyifc_cli("check", "app.py")[1:], ["-m", "pyifc.cli", "check", "app.py"])
        self.assertEqual(ctx.env["PYTHONPATH"].split(os.pathsep)[0], str(run.REPO / "pyifc"))

    def test_refusal_diagnostics_are_read_from_the_log(self):
        log = ("$ python -m pyifc.cli generate app.py\n"
               "app.py:96: error E_UNDECLARED_USE: component 'query' writes 'sensorhub-staging'\n"
               "app.py:5: warning W_UNUSED_CAPABILITY: unused\nnot a diagnostic\n")
        self.assertEqual(run.diagnostics_from_log(log), [
            {"code": "E_UNDECLARED_USE", "severity": "error", "message": "component 'query' writes 'sensorhub-staging'", "file": "app.py", "line": 96},
            {"code": "W_UNUSED_CAPABILITY", "severity": "warning", "message": "unused", "file": "app.py", "line": 5}])


    def test_lines_without_a_line_number_are_diagnostics_too(self):
        log = ("pyifc.toml: error E_CONFIG: /x/pyifc.toml: unknown keys ['a']\n"
               "/x/app.py: error E_APP_UNREADABLE: No such file or directory\n")
        got = run.diagnostics_from_log(log)
        self.assertEqual([(d["code"], d["file"], d["line"]) for d in got],
                         [("E_CONFIG", "pyifc.toml", 0), ("E_APP_UNREADABLE", "/x/app.py", 0)])


class PyifcStageTests(unittest.TestCase):
    """The stage functions against the real CLI (no Floci needed up to generation)."""

    def stage(self, name, sid, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = pyifc_ctx(tmp.name, sid, **kw)
        return ctx, run.STAGE_FUNCS["pyifc"][name](ctx)

    def test_model_stage_writes_the_graph(self):
        ctx, res = self.stage("model", "T0")
        self.assertEqual(res["exit"], 0)
        self.assertEqual(json.loads((ctx.raw / "graph.json").read_text())["schema_version"], "1.3")

    def test_generate_stage_keeps_the_exit_code_and_both_diagnostics_files(self):
        ctx, res = self.stage("generate", "T0", edit="T0")
        self.assertEqual(res["exit"], 0)
        self.assertTrue((ctx.raw / "generated/app.tf.json").exists())
        self.assertEqual(json.loads((ctx.raw / "diagnostics.json").read_text()), [])

    def test_analyzer_refusal_is_exit_1_with_diagnostics_taken_from_the_log(self):
        ctx, res = self.stage("generate", "T2", edit="T2")
        self.assertEqual(res["exit"], 1)
        self.assertIn("E_", (ctx.raw / "diagnostics.json").read_text())
        self.assertFalse((ctx.raw / "generated/app.tf.json").exists())

    def test_lock_refusal_is_exit_2(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = pyifc_ctx(tmp.name, "T0")
        (ctx.app / "pyifc.lock.json").write_text("{not json")
        res = run.STAGE_FUNCS["pyifc"]["generate"](ctx)
        self.assertEqual(res["exit"], 2)
        self.assertIn("E_LOCK_INVALID", res["note"])

    def test_a_rename_is_inferred_with_a_warning_and_not_refused(self):
        ctx, res = self.stage("generate", "T6", edit="T6")
        self.assertEqual(res["exit"], 0)
        codes = [d["code"] for d in json.loads((ctx.raw / "diagnostics.json").read_text())]
        self.assertIn("W_RENAME_INFERRED", codes)

    def test_config_refusal_is_exit_2_and_a_refusal_for_the_analysis(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = pyifc_ctx(tmp.name, "T0")
        (ctx.app / "pyifc.toml").write_text('bogus = "x"\n')
        res = run.STAGE_FUNCS["pyifc"]["generate"](ctx)
        self.assertEqual(res["exit"], 2)
        self.assertIn("E_CONFIG", res["note"])

    def test_missing_app_is_exit_1_with_a_diagnostic(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = pyifc_ctx(tmp.name, "T0")
        (ctx.app / "app.py").unlink()
        res = run.STAGE_FUNCS["pyifc"]["generate"](ctx)
        self.assertEqual(res["exit"], 1)
        self.assertIn("E_APP_UNREADABLE", res["note"])

    def test_generate_without_a_baseline_lock_is_skipped_not_a_refusal(self):
        ctx, res = self.stage("generate", "T0", lock=False)
        self.assertEqual(res["status"], "skipped")
        self.assertFalse((ctx.raw / "diagnostics.json").exists())

    def test_run_stage_exercises_the_app_and_keeps_the_readings(self):
        ctx, res = self.stage("run", "T0")
        self.assertEqual(res["exit"], 0, (ctx.raw / "run-server.log").read_text())
        self.assertIn("http_status", (ctx.raw / "run-readings.json").read_text())

    def test_run_stage_reports_the_undeclared_write_of_t2(self):
        ctx, res = self.stage("run", "T2", edit="T2")
        self.assertNotEqual(res["exit"], 0)
        self.assertIn("query-audit object: absent", res["note"])  # the enforced run must not have written it
        self.assertFalse((ctx.raw / "state/buckets/sensorhub-staging/query-audit/last.json").exists())

    def test_plan_without_a_baseline_state_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(run.BASELINE, {"pyifc": Path(tmp) / "none"}):
            case = Path(tmp) / "case"
            case.mkdir()
            ctx = pyifc_ctx(case, "T0")
            (ctx.raw / "generated").mkdir()
            self.assertEqual(run.STAGE_FUNCS["pyifc"]["plan"](ctx)["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
