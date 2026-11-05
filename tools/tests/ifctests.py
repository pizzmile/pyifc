#!/usr/bin/env python3
"""Change tests: read scenarios from tools/tests.md, analyze captured run data, write the results block.

Usage:
  ifctests.py spec                       validate tests.md and print the scenarios as JSON
  ifctests.py oracle ID                  expected resources and uses of one scenario
  ifctests.py analyze RUN/TOOL           metrics of every scenario in one tool folder of a run
  ifctests.py report [--check]           rewrite (or check) the results block of tests.md

Standard library only. Spec: tools/tests.md. Data: tools/tests/runs/<run-id>/<tool>/<ID>/.
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE.parent / "tests.md"
RUNS = HERE / "runs"

STAGES = ["edit", "generation", "plan", "deploy", "runtime"]
# Stages where a signal can be captured. `deploy` is not built (tests.md section 7), so a result never claims "no
# signal at deploy".
IN_SCOPE = ["edit", "generation", "plan", "runtime"]
# Which recorded stages (stages.json) show that a signal stage was observed.
OBSERVED_BY = {
    "edit": {"check"},
    "generation": {"spec", "generate", "model", "inventory", "export"},
    "plan": {"plan"},
    "runtime": {"run"},
}
STATEFUL = {"bucket"}
KINDS = {"follow", "detect", "protect", "control"}
STATUSES = {"defined", "proposed"}
PHASE_FILES = {
    "P2": "p2_model.json",
    "P3": "p3_export.json",
    "P4": "p4_plan.json",
    "P5": "p5_deployed.json",
    "P6": "p6_behavior.json",
}
CAPS = {"bucket": {"read", "write"}, "topic": {"publish", "subscribe"}}
USE_RE = re.compile(r"^([a-z][a-z0-9-]*)>((bucket|topic):[a-z0-9-]+):([a-z]+)$")
RES_RE = re.compile(r"^(bucket|topic):[a-z0-9-]+$")
START, END = "<!-- results:start -->", "<!-- results:end -->"


class SpecError(Exception):
    pass


# ---------------------------------------------------------------- spec parsing

def _clean(cell):
    return cell.replace("`", "").strip()


def read_table(text, name):
    m = re.search(rf"<!-- table: {re.escape(name)} -->[ \t]*\n((?:\|.*(?:\n|$))+)", text)
    if not m:
        raise SpecError(f"table '{name}' not found")
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in m.group(1).strip().splitlines()]
    header, body = rows[0], rows[2:]
    out = []
    for r in body:
        if len(r) != len(header):
            raise SpecError(f"table '{name}': row has {len(r)} cells, header has {len(header)}: {r[:2]}")
        out.append(dict(zip(header, r)))
    return out


def check_res(r):
    if not RES_RE.match(r):
        raise SpecError(f"bad resource id '{r}'")


def check_use(u):
    m = USE_RE.match(u)
    if not m:
        raise SpecError(f"bad use '{u}' (component>kind:name:capability)")
    kind, cap = m.group(3), m.group(4)
    if cap not in CAPS[kind]:
        raise SpecError(f"capability '{cap}' is not valid for a {kind} in '{u}'")
    return m.group(2)


def parse_delta(cell):
    cell = _clean(cell)
    if cell in ("", "-", "none"):
        return []
    ops = []
    for item in cell.split(";"):
        m = re.match(r"^([+-])(res|use) (\S+)$", item.strip())
        if not m:
            raise SpecError(f"bad delta item '{item.strip()}'")
        ops.append(m.groups())
    return ops


@dataclass
class Scenario:
    id: str
    kind: str
    status: str
    change: str
    delta: list
    phases: list


@dataclass
class Spec:
    base_res: set
    base_uses: set
    scenarios: dict
    edits: dict


def parse_spec(text):
    base_res, base_uses = set(), set()
    for row in read_table(text, "baseline"):
        item = _clean(row["Item"])
        if row["Type"] == "res":
            check_res(item)
            base_res.add(item)
        elif row["Type"] == "use":
            check_use(item)
            base_uses.add(item)
        else:
            raise SpecError(f"baseline: unknown type '{row['Type']}'")
    scenarios = {}
    for row in read_table(text, "scenarios"):
        sid = row["ID"]
        if sid in scenarios:
            raise SpecError(f"duplicate scenario {sid}")
        if row["Kind"] not in KINDS:
            raise SpecError(f"{sid}: unknown kind '{row['Kind']}'")
        if row["Status"] not in STATUSES:
            raise SpecError(f"{sid}: unknown status '{row['Status']}'")
        phases = row["Phases"].split()
        for p in phases:
            if p not in PHASE_FILES:
                raise SpecError(f"{sid}: unknown phase '{p}'")
        scenarios[sid] = Scenario(sid, row["Kind"], row["Status"], row["Change"], parse_delta(row["Oracle delta"]), phases)
    kinds_doc = {r["Kind"] for r in read_table(text, "kinds")}
    if kinds_doc != KINDS:
        raise SpecError(f"kinds table {sorted(kinds_doc)} differs from script {sorted(KINDS)}")
    edits = {r["ID"]: r for r in read_table(text, "edits")}
    spec = Spec(base_res, base_uses, scenarios, edits)
    for sid, sc in scenarios.items():
        oracle(spec, sid)  # validates the delta
        if sc.status == "defined" and sid not in edits:
            raise SpecError(f"{sid} is defined but has no row in the edits table")
    return spec


def load_spec(path=DOC):
    return parse_spec(Path(path).read_text())


def oracle(spec, sid):
    sc = spec.scenarios[sid]
    res, uses = set(spec.base_res), set(spec.base_uses)
    for op, typ, item in sc.delta:
        target = res if typ == "res" else uses
        (check_res if typ == "res" else check_use)(item)
        if op == "+":
            if item in target:
                raise SpecError(f"{sid}: '+{typ} {item}' already present")
            target.add(item)
        else:
            if item not in target:
                raise SpecError(f"{sid}: '-{typ} {item}' not present")
            target.remove(item)
    for u in uses:
        r = check_use(u)
        if r not in res:
            raise SpecError(f"{sid}: use '{u}' refers to resource '{r}' that does not exist")
    return res, uses


# ------------------------------------------------------------------- analysis

def _load_json(path):
    return json.loads(Path(path).read_text())


def load_snapshot(path):
    d = _load_json(path)
    res, uses = set(d.get("resources", [])), set(d.get("uses", []))
    for r in res:
        check_res(r)
    for u in uses:
        check_use(u)
    return res, uses


def _items(res, uses):
    return {"res:" + r for r in res} | {"use:" + u for u in uses}


def _lost_gained(a, b):
    """Items in snapshot a that b lacks, and items in b that a lacks."""
    ia, ib = _items(*a), _items(*b)
    return sorted(ia - ib), sorted(ib - ia)


def dangling_subscriptions(res, uses):
    pubs, subs = set(), set()
    for u in uses:
        m = USE_RE.match(u)
        if m.group(4) == "publish":
            pubs.add(m.group(2))
        elif m.group(4) == "subscribe":
            subs.add(m.group(2))
    return sorted(subs - pubs)


def unused_resources(res, uses):
    used = {USE_RE.match(u).group(2) for u in uses}
    return sorted(res - used)


METRIC_KEYS = [
    "model_missing_uses", "model_extra_uses", "model_missing_resources", "model_extra_resources",
    "lost_in_export", "gained_in_export", "lost_in_deploy", "gained_in_deploy",
    "dangling_subscriptions", "unused_resources", "destructive", "first_signal", "unobserved",
    "behavior_ok", "outcome", "verdict",
]


def observed_stages(case_dir):
    """Signal stages whose capture ran (stages.json): a skipped or missing stage observed nothing."""
    p = Path(case_dir) / "stages.json"
    ran = {r["stage"] for r in _load_json(p) if r.get("status") in ("ok", "failed")} if p.exists() else set()
    return [st for st in IN_SCOPE if OBSERVED_BY[st] & ran]


def refused_at_generation(case_dir):
    """True if the tool refused to generate: the `generate` stage exited 1 (analyzer errors) or 2 (identity or target
    refusal) and raw/diagnostics.json names an E_ code. A refusal is the tool's signal at generation, not a missing
    capture: there is no export or plan after it, and no graph when the analyzer refused."""
    d = Path(case_dir)
    stages, diagnostics = d / "stages.json", d / "raw" / "diagnostics.json"
    if not stages.exists() or not diagnostics.exists():
        return False
    if not any(r["stage"] == "generate" and r.get("exit") in (1, 2) for r in _load_json(stages)):
        return False
    return any(str(item.get("code", "")).startswith("E_") for item in _load_json(diagnostics))


def analyze_case(spec, sid, case_dir):
    sc = spec.scenarios[sid]
    ores, ouses = oracle(spec, sid)
    d = Path(case_dir)
    refused = refused_at_generation(d)
    required = [p for p in sc.phases if not (refused and p in ("P2", "P3", "P4"))]
    missing = [p for p in required if not (d / PHASE_FILES[p]).exists()]
    snaps = {p: load_snapshot(d / f) for p, f in PHASE_FILES.items() if p in ("P2", "P3", "P5") and (d / f).exists()}
    m = {"id": sid, "kind": sc.kind, "missing_phases": missing}

    p2 = snaps.get("P2")
    m["model_missing_uses"] = sorted(ouses - p2[1]) if p2 else None
    m["model_extra_uses"] = sorted(p2[1] - ouses) if p2 else None
    m["model_missing_resources"] = sorted(ores - p2[0]) if p2 else None
    m["model_extra_resources"] = sorted(p2[0] - ores) if p2 else None
    p3, p5 = snaps.get("P3"), snaps.get("P5")
    m["lost_in_export"], m["gained_in_export"] = _lost_gained(p2, p3) if p2 and p3 else (None, None)
    m["lost_in_deploy"], m["gained_in_deploy"] = _lost_gained(p3, p5) if p3 and p5 else (None, None)
    m["dangling_subscriptions"] = dangling_subscriptions(*p2) if p2 else None  # from the tool's model, not the oracle
    m["unused_resources"] = unused_resources(*p2) if p2 else None

    plan = _load_json(d / PHASE_FILES["P4"]) if (d / PHASE_FILES["P4"]).exists() else None
    if plan is not None:
        m["destructive"] = [
            f"{c['resource']} {c['action']}" for c in plan.get("changes", [])
            if c["action"] in ("destroy", "replace") and c["resource"].split(":")[0] in STATEFUL
        ]
    else:
        m["destructive"] = None
    sigs = _load_json(d / "signals.json") if (d / "signals.json").exists() else []
    stages = [STAGES.index(s["stage"]) for s in sigs] + ([STAGES.index("generation")] if refused else [])
    m["first_signal"] = STAGES[min(stages)] if stages else "never"
    seen = observed_stages(d)
    m["unobserved"] = [st for st in IN_SCOPE if st not in seen]
    beh = _load_json(d / PHASE_FILES["P6"]) if (d / PHASE_FILES["P6"]).exists() else None
    m["behavior_ok"] = beh.get("ok") if beh else None

    model_clean = p2 is not None and not (
        m["model_missing_uses"] or m["model_extra_uses"] or m["model_missing_resources"] or m["model_extra_resources"])
    export_clean = not (m["lost_in_export"] or m["gained_in_export"])
    signalled = m["first_signal"] != "never"
    divergent = (p2 is not None and not model_clean) or bool(m["dangling_subscriptions"])
    gaps = m["unobserved"]
    if sc.kind == "detect":
        if divergent:
            m["outcome"] = "divergent and signalled" if signalled else (
                "divergent, stages not observed" if gaps else "divergent and silent")
        else:
            m["outcome"] = "tracked and signalled" if signalled else "tracked"
    else:
        m["outcome"] = "n/a"
    if refused:  # the refusal is the signal at generation (detect, protect); follow and control expect output
        m["outcome"] = "refused at generation" if sc.kind in ("follow", "control") else "signalled at generation"

    # Verdicts: pass; fail (follow, control: the output is wrong); missed (detect, protect: a signal was
    # expected, every stage that could show one was observed, none came); inconclusive (the same, but a stage
    # was not observed, so the experiment cannot tell silence from absence); incomplete (a phase file is missing).
    if missing:
        m["verdict"] = "incomplete"
    elif sc.kind == "follow":
        m["verdict"] = "pass" if model_clean and export_clean and not refused else "fail"
    elif sc.kind == "control":
        m["verdict"] = "pass" if model_clean and plan is not None and not plan.get("changes") and not refused else "fail"
    elif sc.kind == "detect":
        m["verdict"] = "pass" if (not divergent or signalled) else ("inconclusive" if gaps else "missed")
    else:  # protect: only a signal before the plan counts
        ok = not m["destructive"] or m["first_signal"] in ("edit", "generation")
        early_gaps = [st for st in gaps if st in ("edit", "generation")]
        m["verdict"] = "pass" if ok else ("inconclusive" if early_gaps else "missed")
    return m


def analyze_tool_dir(spec, tool_dir):
    out = {}
    for sid in spec.scenarios:
        case = Path(tool_dir) / sid
        if case.is_dir():
            out[sid] = analyze_case(spec, sid, case)
    return out


# --------------------------------------------------------------------- report

def latest_runs(runs_dir):
    """Newest run folder per tool: {tool: (run_id, tool_dir)}."""
    latest = {}
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return latest
    for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        for tool in sorted(p for p in run.iterdir() if p.is_dir()):
            latest[tool.name] = (run.name, tool)
    return latest


def _cell(m):
    if m is None:
        return "-"
    v = m["verdict"]
    if v == "incomplete":
        return "incomplete (" + " ".join(m["missing_phases"]) + ")"
    if v == "pass":
        return "pass" + (f" ({m['outcome']})" if m["outcome"] not in ("n/a",) else "")
    if v == "inconclusive":
        return "NOT OBSERVED (" + ", ".join(m["unobserved"]) + ")"
    if v == "missed":
        # protect: the tools gave no signal, but Terraform's plan shows the destroy (m["destructive"] is not empty)
        return "MISSED (no tool signal; destroy visible in plan)" if m["kind"] == "protect" else f"MISSED ({m['outcome']})"
    if m["outcome"] == "refused at generation":
        return "FAIL (refused at generation)"
    return "FAIL (not a no-op)" if m["kind"] == "control" else "FAIL (model differs)"


def _bullets(tool, m):
    sid = f"{m['id']} {tool}"
    lines = []
    for key, label in [
        ("model_missing_uses", "model lacks uses"), ("model_extra_uses", "model has extra uses"),
        ("model_missing_resources", "model lacks resources"), ("model_extra_resources", "model has extra resources"),
        ("lost_in_export", "export lost"), ("gained_in_export", "export gained"),
        ("lost_in_deploy", "deploy lost"), ("gained_in_deploy", "deploy gained"),
        ("dangling_subscriptions", "dangling subscriptions"), ("destructive", "destructive plan"),
    ]:
        v = m.get(key)
        if v:
            lines.append(f"- {sid}: {label}: " + ", ".join(f"`{x}`" for x in v))
    if m["first_signal"] != "never":
        lines.append(f"- {sid}: first signal at {m['first_signal']}")
    if m.get("unobserved"):
        lines.append(f"- {sid}: stages not observed: {', '.join(m['unobserved'])}")
    if m["outcome"] == "refused at generation":
        lines.append(f"- {sid}: tool refused to generate")
    if m["behavior_ok"] is False:
        lines.append(f"- {sid}: changed app did not run correctly")
    return lines


def render_results(spec, runs_dir):
    latest = latest_runs(runs_dir)
    if not latest:
        return "No runs recorded in `tools/tests/runs/`."
    tools = sorted(latest)
    results = {t: analyze_tool_dir(spec, latest[t][1]) for t in tools}
    out = []
    out.append("Latest run per tool: " + ", ".join(f"{t} `{latest[t][0]}`" for t in tools) + ".")
    for run_id in sorted({latest[t][0] for t in tools}):
        meta = run_meta(runs_dir, run_id)
        if meta:
            out.append(f"Run `{run_id}`: " + ", ".join(f"{k} {v}" for k, v in meta.items()) + ".")
    out.append("")
    out.append("| ID | Kind | Change | " + " | ".join(t.capitalize() for t in tools) + " |")
    out.append("|---|---|---|" + "---|" * len(tools))
    for sid, sc in spec.scenarios.items():
        if sc.status != "defined" and not any(sid in results[t] for t in tools):
            continue
        out.append(f"| {sid} | {sc.kind} | {sc.change} | " + " | ".join(_cell(results[t].get(sid)) for t in tools) + " |")
    out.append("")
    out.append("| Tool | pass | fail | missed | inconclusive | incomplete | scenarios not run |")
    out.append("|---|---|---|---|---|---|---|")
    for t in tools:
        c = {"pass": 0, "fail": 0, "missed": 0, "inconclusive": 0, "incomplete": 0}
        for m in results[t].values():
            c[m["verdict"]] += 1
        defined = [s for s, sc in spec.scenarios.items() if sc.status == "defined"]
        not_run = len([s for s in defined if s not in results[t]])
        out.append(f"| {t} | {c['pass']} | {c['fail']} | {c['missed']} | {c['inconclusive']} | {c['incomplete']} | {not_run} |")
    bullets = [b for t in tools for m in results[t].values() for b in _bullets(t, m)]
    if bullets:
        out.append("")
        out.append("Findings:")
        out.append("")
        out.extend(bullets)
    return "\n".join(out)


def run_meta(runs_dir, run_id):
    p = Path(runs_dir) / run_id / "run.json"
    return _load_json(p) if p.exists() else {}


def write_results(doc_path, block):
    text = Path(doc_path).read_text()
    if START not in text or END not in text:
        raise SpecError("results markers not found in " + str(doc_path))
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    return head + START + "\n" + block + "\n" + END + tail


# ------------------------------------------------------------------------ cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("spec")
    o = sub.add_parser("oracle")
    o.add_argument("id")
    a = sub.add_parser("analyze")
    a.add_argument("tool_dir")
    r = sub.add_parser("report")
    r.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    try:
        spec = load_spec()
        if args.cmd == "spec":
            print(json.dumps({
                "baseline": {"resources": sorted(spec.base_res), "uses": sorted(spec.base_uses)},
                "scenarios": {s: {"kind": c.kind, "status": c.status, "phases": c.phases,
                                  "delta": [" ".join(x) for x in c.delta]} for s, c in spec.scenarios.items()},
            }, indent=2))
        elif args.cmd == "oracle":
            res, uses = oracle(spec, args.id)
            print(json.dumps({"resources": sorted(res), "uses": sorted(uses)}, indent=2))
        elif args.cmd == "analyze":
            print(json.dumps(analyze_tool_dir(spec, args.tool_dir), indent=2))
        else:
            new = write_results(DOC, render_results(spec, RUNS))
            if args.check:
                if new != DOC.read_text():
                    print("results block of tests.md is out of date; run: ifctests.py report", file=sys.stderr)
                    return 1
            else:
                DOC.write_text(new)
    except (SpecError, KeyError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
