import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ifctests as t  # noqa: E402

SPEC = t.load_spec()


ALL_OBSERVED = [{"stage": "check", "status": "ok"}, {"stage": "spec", "status": "ok"}, {"stage": "plan", "status": "ok"},
                {"stage": "run", "status": "ok"}]


def write_case(root, tool, sid, p2=None, p3=None, p4=None, p5=None, p6=None, signals=None, stages=ALL_OBSERVED):
    d = Path(root) / tool / sid
    d.mkdir(parents=True, exist_ok=True)

    def snap(name, v):
        if v is not None:
            (d / name).write_text(json.dumps({"resources": sorted(v[0]), "uses": sorted(v[1])}))
    snap("p2_model.json", p2)
    snap("p3_export.json", p3)
    snap("p5_deployed.json", p5)
    if p4 is not None:
        (d / "p4_plan.json").write_text(json.dumps(p4))
    if p6 is not None:
        (d / "p6_behavior.json").write_text(json.dumps(p6))
    if signals is not None:
        (d / "signals.json").write_text(json.dumps(signals))
    if stages is not None:
        (d / "stages.json").write_text(json.dumps(stages))
    return d


class SpecTests(unittest.TestCase):
    def test_every_oracle_is_valid(self):
        for sid in SPEC.scenarios:
            res, uses = t.oracle(SPEC, sid)
            self.assertTrue(res and uses, sid)

    def test_defined_scenarios_have_edits(self):
        for sid, sc in SPEC.scenarios.items():
            if sc.status == "defined":
                self.assertIn(sid, SPEC.edits)

    def test_baseline_counts(self):
        self.assertEqual((len(SPEC.base_res), len(SPEC.base_uses)), (4, 10))

    def test_t1_adds_bucket_and_write(self):
        res, uses = t.oracle(SPEC, "T1")
        self.assertIn("bucket:archive", res)
        self.assertIn("ingestion>bucket:archive:write", uses)

    def test_t3_and_t4_share_an_oracle(self):
        self.assertEqual(t.oracle(SPEC, "T3"), t.oracle(SPEC, "T4"))

    def test_t5_dangles(self):
        res, uses = t.oracle(SPEC, "T5")
        self.assertEqual(t.dangling_subscriptions(res, uses), ["topic:audit"])

    def test_t6_renames_everywhere(self):
        res, uses = t.oracle(SPEC, "T6")
        self.assertNotIn("bucket:staging", res)
        self.assertFalse([u for u in uses if "bucket:staging:" in u])

    def test_bad_delta_is_rejected(self):
        with self.assertRaises(t.SpecError):
            t.parse_delta("+use ingestion>bucket:x:fly")  # capability check happens in oracle
            t.check_use("ingestion>bucket:x:fly")
        with self.assertRaises(t.SpecError):
            t.parse_delta("add bucket:x")

    def test_removing_absent_item_is_rejected(self):
        text = t.DOC.read_text().replace("`+res bucket:archive` | P2 P3 P4 P6 |\n| T5", "`-res bucket:nope` | P2 P3 P4 P6 |\n| T5", 1)
        with self.assertRaises(t.SpecError):
            t.parse_spec(text)

    def test_measures_table_matches_script(self):
        names = [r["Name"] for r in t.read_table(t.DOC.read_text(), "measures")]
        self.assertEqual(names, t.METRIC_KEYS)
        with tempfile.TemporaryDirectory() as tmp:
            d = write_case(tmp, "x", "T0", p2=t.oracle(SPEC, "T0"), p3=t.oracle(SPEC, "T0"), p4={"changes": []})
            m = t.analyze_case(SPEC, "T0", d)
        self.assertEqual([k for k in m if k not in ("id", "kind", "missing_phases")], t.METRIC_KEYS)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self, sid, **kw):
        kw.setdefault("p6", {"ok": True})
        d = write_case(self.root, "tool", sid, **kw)
        return t.analyze_case(SPEC, sid, d)

    def test_control_passes_without_change(self):
        o = t.oracle(SPEC, "T0")
        m = self.run_case("T0", p2=o, p3=o, p4={"changes": []})
        self.assertEqual(m["verdict"], "pass")

    def test_control_fails_on_plan_change(self):
        o = t.oracle(SPEC, "T0")
        m = self.run_case("T0", p2=o, p3=o, p4={"changes": [{"resource": "topic:speed", "action": "update"}]})
        self.assertEqual(m["verdict"], "fail")

    def test_follow_passes_when_model_equals_oracle(self):
        o = t.oracle(SPEC, "T1")
        m = self.run_case("T1", p2=o, p3=o, p4={"changes": []})
        self.assertEqual(m["verdict"], "pass")

    def test_follow_fails_on_missing_grant(self):
        res, uses = t.oracle(SPEC, "T1")
        short = (res, uses - {"ingestion>bucket:archive:write"})
        m = self.run_case("T1", p2=short, p3=short, p4={"changes": []})
        self.assertEqual(m["verdict"], "fail")
        self.assertEqual(m["model_missing_uses"], ["ingestion>bucket:archive:write"])

    def test_follow_fails_when_export_drops_a_grant(self):
        o = t.oracle(SPEC, "T1")
        lossy = (o[0], o[1] - {"ingestion>bucket:archive:write"})
        m = self.run_case("T1", p2=o, p3=lossy, p4={"changes": []})
        self.assertEqual(m["verdict"], "fail")
        self.assertEqual(m["lost_in_export"], ["use:ingestion>bucket:archive:write"])

    def test_detect_silent_overgrant_fails(self):
        res, uses = t.oracle(SPEC, "T4")
        over = (res, uses | {"ingestion>bucket:archive:write"})
        m = self.run_case("T4", p2=over, p3=over, p4={"changes": []})
        self.assertEqual((m["outcome"], m["verdict"]), ("divergent and silent", "missed"))
        self.assertEqual(m["model_extra_uses"], ["ingestion>bucket:archive:write"])

    def test_detect_signalled_overgrant_passes(self):
        res, uses = t.oracle(SPEC, "T4")
        over = (res, uses | {"ingestion>bucket:archive:write"})
        m = self.run_case("T4", p2=over, p3=over, p4={"changes": []},
                          signals=[{"stage": "plan", "text": "unused grant"}, {"stage": "edit", "text": "x"}])
        self.assertEqual((m["outcome"], m["verdict"], m["first_signal"]), ("divergent and signalled", "pass", "edit"))

    def test_detect_derived_tool_tracks_undeclared_use(self):
        o = t.oracle(SPEC, "T2")
        m = self.run_case("T2", p2=o, p3=o, p4={"changes": []}, p6={"ok": True})
        self.assertEqual((m["outcome"], m["verdict"]), ("tracked", "pass"))

    def test_detect_dangling_subscription_is_divergent_even_if_model_matches(self):
        o = t.oracle(SPEC, "T5")
        m = self.run_case("T5", p2=o, p3=o, p4={"changes": []})
        self.assertEqual((m["outcome"], m["verdict"]), ("divergent and silent", "missed"))
        self.assertEqual(m["dangling_subscriptions"], ["topic:audit"])

    def test_protect_destroy_without_signal_is_missed(self):
        o = t.oracle(SPEC, "T6")
        plan = {"changes": [{"resource": "bucket:staging", "action": "destroy"},
                            {"resource": "bucket:staging-v2", "action": "create"}]}
        m = self.run_case("T6", p2=o, p3=o, p4=plan)
        self.assertEqual((m["verdict"], m["destructive"]), ("missed", ["bucket:staging destroy"]))

    def test_protect_generation_signal_passes_but_plan_signal_does_not(self):
        o = t.oracle(SPEC, "T6")
        plan = {"changes": [{"resource": "bucket:staging", "action": "replace"}]}
        ok = self.run_case("T6", p2=o, p3=o, p4=plan, signals=[{"stage": "generation", "text": "guard"}])
        late = self.run_case("T6", p2=o, p3=o, p4=plan, signals=[{"stage": "plan", "text": "x"}])
        self.assertEqual((ok["verdict"], late["verdict"]), ("pass", "missed"))

    def test_protect_missed_cell_says_the_plan_shows_the_destroy(self):
        o = t.oracle(SPEC, "T6")
        plan = {"changes": [{"resource": "bucket:staging", "action": "destroy"}]}
        m = self.run_case("T6", p2=o, p3=o, p4=plan)
        self.assertEqual(t._cell(m), "MISSED (no tool signal; destroy visible in plan)")

    def test_protect_non_stateful_destroy_is_ignored(self):
        o = t.oracle(SPEC, "T6")
        plan = {"changes": [{"resource": "topic:speed", "action": "replace"}]}
        self.assertEqual(self.run_case("T6", p2=o, p3=o, p4=plan)["verdict"], "pass")

    def test_missing_required_phase_is_incomplete(self):
        o = t.oracle(SPEC, "T2")
        m = self.run_case("T2", p2=o, p3=o, p4={"changes": []}, p6=None)  # P6 required
        self.assertEqual((m["verdict"], m["missing_phases"]), ("incomplete", ["P6"]))

    def test_unobserved_stage_makes_silence_inconclusive(self):
        res, uses = t.oracle(SPEC, "T4")
        over = (res, uses | {"ingestion>bucket:archive:write"})
        no_check = [r for r in ALL_OBSERVED if r["stage"] != "check"]
        skipped = no_check + [{"stage": "check", "status": "skipped"}]
        m = self.run_case("T4", p2=over, p3=over, p4={"changes": []}, stages=skipped)
        self.assertEqual((m["unobserved"], m["outcome"], m["verdict"]), (["edit"], "divergent, stages not observed", "inconclusive"))

    def test_signal_in_an_observed_stage_passes_even_if_others_were_not_observed(self):
        res, uses = t.oracle(SPEC, "T4")
        over = (res, uses | {"ingestion>bucket:archive:write"})
        m = self.run_case("T4", p2=over, p3=over, p4={"changes": []}, stages=[{"stage": "plan", "status": "ok"}],
                          signals=[{"stage": "plan", "text": "x"}])
        self.assertEqual((m["verdict"], m["unobserved"]), ("pass", ["edit", "generation", "runtime"]))

    def test_protect_without_early_stages_observed_is_inconclusive(self):
        o = t.oracle(SPEC, "T6")
        plan = {"changes": [{"resource": "bucket:staging", "action": "destroy"}]}
        m = self.run_case("T6", p2=o, p3=o, p4=plan, stages=[{"stage": "plan", "status": "ok"}])
        self.assertEqual(m["verdict"], "inconclusive")

    def test_follow_and_control_never_become_missed_or_inconclusive(self):
        res, uses = t.oracle(SPEC, "T1")
        m = self.run_case("T1", p2=(res, uses - {"ingestion>bucket:archive:write"}), p3=(res, uses), p4={"changes": []},
                          stages=[])
        self.assertEqual(m["verdict"], "fail")

    def test_deploy_transition(self):
        o = t.oracle(SPEC, "N4")
        deployed = (o[0], o[1] - {"query>bucket:staging:write"})
        m = self.run_case("N4", p2=o, p3=o, p5=deployed, p6={"ok": True})
        self.assertEqual(m["lost_in_deploy"], ["use:query>bucket:staging:write"])


REFUSED = [{"stage": "check", "status": "failed", "exit": 1}, {"stage": "model", "status": "failed", "exit": 1},
           {"stage": "generate", "status": "failed", "exit": 2}, {"stage": "plan", "status": "skipped"},
           {"stage": "run", "status": "ok", "exit": 0}]


class RefusalTests(unittest.TestCase):
    """pyifc refuses to generate (exit 1 or 2 and an E_ code in raw/diagnostics.json): that is a signal at generation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def refused_case(self, sid, code="E_UNDECLARED_USE", exit_code=2, with_p2=False, **kw):
        stages = [dict(r, exit=exit_code) if r["stage"] == "generate" else r for r in REFUSED]
        o = t.oracle(SPEC, sid)
        d = write_case(self.root, "pyifc", sid, p2=o if with_p2 else None, p6={"ok": True}, signals=[], stages=stages, **kw)
        (d / "raw").mkdir(exist_ok=True)
        (d / "raw" / "diagnostics.json").write_text(json.dumps([{"code": code, "severity": "error", "message": "m", "file": "app.py", "line": 1}]))
        return t.analyze_case(SPEC, sid, d)

    def test_a_refusal_needs_neither_p3_nor_p4_nor_an_absent_graph(self):
        for sid in ("T2", "T6"):
            m = self.refused_case(sid)
            self.assertEqual(m["missing_phases"], [], sid)
            self.assertNotEqual(m["verdict"], "incomplete", sid)

    def test_exit_1_with_an_e_code_is_a_refusal_too(self):
        self.assertNotEqual(self.refused_case("T2", exit_code=1)["verdict"], "incomplete")

    def test_detect_refused_at_generation_passes(self):
        m = self.refused_case("T2")
        self.assertEqual((m["verdict"], m["outcome"], m["first_signal"]), ("pass", "signalled at generation", "generation"))
        self.assertEqual(t._cell(m), "pass (signalled at generation)")

    def test_detect_refusal_wins_over_a_later_missing_signal(self):
        # the dangling subscription of T5 is divergent; a refusal still counts as the signal
        self.assertEqual(self.refused_case("T5", with_p2=True)["verdict"], "pass")

    def test_follow_refused_fails(self):
        m = self.refused_case("T1")
        self.assertEqual(m["verdict"], "fail")
        self.assertEqual(t._cell(m), "FAIL (refused at generation)")

    def test_control_refused_fails(self):
        m = self.refused_case("T0")
        self.assertEqual((m["verdict"], t._cell(m)), ("fail", "FAIL (refused at generation)"))

    def test_protect_refused_at_generation_passes(self):
        m = self.refused_case("T6", code="E_IDENTITY_DESTROY")
        self.assertEqual((m["verdict"], m["first_signal"]), ("pass", "generation"))

    def test_generate_exit_1_without_an_e_code_is_not_a_refusal(self):
        d = write_case(self.root, "pyifc", "T2", p2=t.oracle(SPEC, "T2"), p6={"ok": True}, signals=[],
                       stages=[{"stage": "generate", "status": "failed", "exit": 1}])
        (d / "raw").mkdir()
        (d / "raw" / "diagnostics.json").write_text(json.dumps([{"code": "W_GRANT_DROPPED", "severity": "warning"}]))
        self.assertEqual(t.analyze_case(SPEC, "T2", d)["verdict"], "incomplete")

    def test_a_generate_that_succeeded_does_not_excuse_missing_phases(self):
        d = write_case(self.root, "pyifc", "T1", p2=t.oracle(SPEC, "T1"), p6={"ok": True},
                       stages=[{"stage": "generate", "status": "ok", "exit": 0}])
        self.assertEqual(t.analyze_case(SPEC, "T1", d)["missing_phases"], ["P3", "P4"])

    def test_p6_is_still_required_after_a_refusal(self):
        d = write_case(self.root, "pyifc", "T2", signals=[], stages=REFUSED)
        (d / "raw").mkdir()
        (d / "raw" / "diagnostics.json").write_text(json.dumps([{"code": "E_X"}]))
        m = t.analyze_case(SPEC, "T2", d)
        self.assertEqual((m["verdict"], m["missing_phases"]), ("incomplete", ["P6"]))


class ReportTests(unittest.TestCase):
    def test_no_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("No runs", t.render_results(SPEC, tmp))

    def test_report_table_and_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "2026-11-05"
            run.mkdir()
            (run / "run.json").write_text(json.dumps({"floci": "2.4.0"}))
            o = t.oracle(SPEC, "T4")
            over = (o[0], o[1] | {"ingestion>bucket:archive:write"})
            write_case(run, "nitric", "T4", p2=over, p3=over, p4={"changes": []}, p6={"ok": True})
            write_case(run, "encore", "T4", p2=o, p3=o, p4={"changes": []}, p6={"ok": True})
            block = t.render_results(SPEC, tmp)
        self.assertIn("| T4 | detect |", block)
        self.assertIn("MISSED (divergent and silent)", block)
        self.assertIn("pass (tracked)", block)
        self.assertIn("floci 2.4.0", block)
        self.assertIn("T4 nitric: model has extra uses", block)

    def test_write_results_replaces_only_the_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "d.md"
            doc.write_text(f"before\n{t.START}\nold\n{t.END}\nafter\n")
            self.assertEqual(t.write_results(doc, "new"), f"before\n{t.START}\nnew\n{t.END}\nafter\n")

    @unittest.skipUnless(t.RUNS.is_dir(), "run evidence is not part of this repository")
    def test_committed_results_block_is_current(self):
        self.assertEqual(t.write_results(t.DOC, t.render_results(SPEC, t.RUNS)), t.DOC.read_text())


if __name__ == "__main__":
    unittest.main()
