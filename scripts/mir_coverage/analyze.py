"""Aggregate `programs.jsonl` into the four-count report, `bodies.csv` and
`summary.json`, and compare first blockers against a previous run.

Pure post-processing: imports nothing from tpyc, installs no hooks.
"""
import ast
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from families import family

# `frame_close` fires for every resumable body, so it says nothing about loans.
LOAN_PREFIX = ("borrow:", "view_var:", "provenance:", "param_returned", "loop_frame_hold",
               "with_exit_hold", "lambda_ref_capture")
CORPORA = ("tests", "examples", "stdlib")
LOWERED = "(lowered)"

CSV_COLUMNS = ["corpus", "program", "module", "qualname", "user", "position", "generic", "overloaded",
               "features", "status", "reason_cat", "reason_raw", "node_kind", "blocker_line", "blocker_type",
               "lowered", "analyses", "analysis_gap", "analysis_conflicts", "storage", "storage_gap",
               "conflict", "certified", "blocker", "loan_kinds", "check_events", "lifetime_diags"]


def load_programs(out_dir: Path) -> list[dict]:
    path = out_dir / "programs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def loan_kinds(b: dict) -> set[str]:
    ks = set()
    for k in b["events"]:
        if k.startswith(LOAN_PREFIX):
            ks.add(k[:-5] if k.endswith(":elem") else k)
    for d in b.get("lt_diags", []):
        ks.add("diag:" + d)
    return ks


def blocker(b: dict) -> str:
    """The first stage a body fails, named by that stage's reason category."""
    if not b["lowered"]:
        return b["reason_cat"] or "?"
    if b.get("analyses") != "complete":
        return f"analysis: {b.get('analysis_gap')}"
    if b.get("storage") == "certified":
        return "(certified)"
    if b.get("storage") in ("conflict", "no_proof_required", "no_facts"):
        return f"storage: {b['storage']}"
    return f"storage: {b.get('storage_gap')}"


def body_rows(programs: list[dict]) -> list[dict]:
    rows = []
    for p in programs:
        if p["status"] != "ok":
            continue
        by_body = defaultdict(list)
        for d in p["diags"]:
            if d["family"] and d["family"] != "readonly_ref":
                by_body[(d["module"], d["body"])].append(d["family"])
        for b in p["bodies"]:
            b["corpus"] = p["corpus"]
            b["program"] = p["program"]
            b["lt_diags"] = by_body.get((b["module"], b["qualname"]), [])
            b["loan_kinds"] = sorted(loan_kinds(b))
            b.setdefault("lowered", b["status"] == "covered")
            b["certified"] = b.get("storage") == "certified"
            b.setdefault("conflict", False)
            b["blocker"] = blocker(b)
            rows.append(b)
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            cells = dict(r)
            cells["features"] = "|".join(r["features"])
            cells["analysis_conflicts"] = "|".join(r.get("analysis_conflicts") or [])
            cells["loan_kinds"] = "|".join(r["loan_kinds"])
            cells["check_events"] = "|".join(f"{k}={v}" for k, v in sorted(r["events"].items())
                                             if k.startswith("check:"))
            cells["lifetime_diags"] = "|".join(r["lt_diags"])
            w.writerow([cells.get(c, "") if cells.get(c) is not None else "" for c in CSV_COLUMNS])


def pct(a: float, b: float) -> str:
    return f"{100.0 * a / b:5.1f}%" if b else "   -  "


def four(rs: list[dict]) -> dict:
    return {"bodies": len(rs), "lowered": sum(r["lowered"] for r in rs),
            "complete": sum(r["lowered"] and r.get("analyses") == "complete" for r in rs),
            "conflict": sum(bool(r["conflict"]) for r in rs),
            "certified": sum(r["certified"] for r in rs),
            "no_proof": sum(r.get("storage") == "no_proof_required" for r in rs)}


FOUR_HEAD = f"{'bodies':>7} {'lowered':>15} {'complete':>15} {'conflict':>9} {'certified':>10} {'no-proof':>9}"


def four_line(c: dict) -> str:
    n = c["bodies"]
    return (f"{n:7} {c['lowered']:6} {pct(c['lowered'], n)} {c['complete']:6} {pct(c['complete'], n)} "
            f"{c['conflict']:9} {c['certified']:10} {c['no_proof']:9}")


def report(out_dir: Path, sample: dict | None, compare_dir: Path | None, cases_dir: Path | None) -> str:
    programs = load_programs(out_dir)
    rows = body_rows(programs)
    write_csv(rows, out_dir / "bodies.csv")
    bodies = [r for r in rows if r["status"] != "no_body"]
    lines: list[str] = []
    emit = lines.append
    summary: dict = {"programs": {}, "four_count": {}, "loan_active": {}, "by_position": {}}

    status = defaultdict(Counter)
    for p in programs:
        status[p["corpus"]][p["status"]] += 1
    summary["programs"] = {k: dict(v) for k, v in status.items()}
    emit("program status: " + "; ".join(f"{k}: {dict(v)}" for k, v in status.items()))
    unattr = Counter()
    for p in programs:
        unattr.update(p.get("unattributed_events", {}))
    if unattr:
        emit(f"unattributed sema events: {dict(unattr)}")
    for p in programs:
        if p["status"] == "exception":
            emit(f"  exception: {p['program']}: {p.get('error', '')[:150]}")

    emit("\n== 1. four-count (bodies with a body; no_body excluded) ==")
    emit("lowered = MIR lowering succeeded; complete = every --dump-mir analysis ran without MIRNotCovered;")
    emit("conflict = an analysis or the storage certificate found one; certified = certify_thir_storage")
    emit("CERTIFIED; no-proof = requires_proof False (nothing to certify, never counted as certified)")
    emit(f"{'corpus':18} {FOUR_HEAD}")
    for corpus in CORPORA:
        rs = [r for r in bodies if r["corpus"] == corpus]
        if not rs:
            continue
        c = four(rs)
        summary["four_count"][corpus] = c
        emit(f"{corpus:18} {four_line(c)}")
        ex = four([r for r in rs if r["position"] != "module_init"])
        summary["four_count"][corpus + "_excl_init"] = ex
        emit(f"{'  excl module init':18} {four_line(ex)}")
    if sample is not None and any(r["corpus"] == "tests" for r in bodies):
        groups = sample["groups"]
        tot = {k: 0.0 for k in ("n", "lowered", "complete", "certified")}
        for r in bodies:
            if r["corpus"] != "tests" or r["position"] == "module_init":
                continue
            g = groups.get(r["program"].split("/")[0])
            if not g:
                continue
            w = g[0] / g[1]
            tot["n"] += w
            tot["lowered"] += w * r["lowered"]
            tot["complete"] += w * (r["lowered"] and r.get("analyses") == "complete")
            tot["certified"] += w * r["certified"]
        emit(f"tests excl init, group-weighted to the full corpus: lowered {pct(tot['lowered'], tot['n'])}, "
             f"complete {pct(tot['complete'], tot['n'])}, certified {pct(tot['certified'], tot['n'])}")

    emit("\n-- storage verdicts over lowered bodies (all corpora) --")
    sv = Counter(r.get("storage") for r in bodies if r["lowered"])
    emit("   " + ", ".join(f"{k}={v}" for k, v in sv.most_common()))
    emit("-- analyses over lowered bodies (all corpora) --")
    av = Counter(r.get("analyses") for r in bodies if r["lowered"])
    emit("   " + ", ".join(f"{k}={v}" for k, v in av.most_common()))
    anyg = Counter(g for r in bodies if r["lowered"] for g in r.get("analysis_gaps") or [])
    if anyg:
        emit("   gap in (any, not only first): " + ", ".join(f"{k}={v}" for k, v in anyg.most_common()))
    ck = Counter(k for r in bodies for k in (r.get("analysis_conflicts") or []) + (r.get("storage_conflicts") or []))
    if ck:
        emit("   conflict kinds: " + ", ".join(f"{k}={v}" for k, v in ck.most_common()))

    emit("\n== 2. four-count by position (all corpora) ==")
    emit(f"{'position':18} {FOUR_HEAD}")
    positions = sorted({r["position"] for r in bodies})
    for pos in positions:
        c = four([r for r in bodies if r["position"] == pos])
        summary["by_position"][pos] = c
        emit(f"{pos:18} {four_line(c)}")
    emit("-- lowered by position and corpus --")
    for pos in positions:
        cells = []
        for corpus in CORPORA:
            c = four([r for r in bodies if r["corpus"] == corpus and r["position"] == pos])
            cells.append(f"{c['lowered']:5}/{c['bodies']:5} {pct(c['lowered'], c['bodies'])}")
        emit(f"{pos:18} " + " | ".join(cells) + "   (tests | examples | stdlib)")
    emit("-- body-content tags (tests+examples, excl module init): lowered --")
    ue = [r for r in bodies if r["corpus"] != "stdlib" and r["position"] != "module_init"]
    for tag in ("nested_def", "lambda", "comprehension", "try", "with", "match", "for", "while"):
        c = four([r for r in ue if tag in r["features"]])
        emit(f"  contains {tag:13} {c['lowered']:5}/{c['bodies']:5} {pct(c['lowered'], c['bodies'])}")
    c = four([r for r in ue if r.get("generic")])
    emit(f"  generic                {c['lowered']:5}/{c['bodies']:5} {pct(c['lowered'], c['bodies'])}")

    emit("\n== 3. loan-active bodies (a sema loan/lifetime STATE event or a lifetime diagnostic) ==")
    emit(f"{'':32} {FOUR_HEAD}")
    for label, sel in (("user (tests+examples)", lambda r: r["corpus"] != "stdlib"),
                       ("stdlib", lambda r: r["corpus"] == "stdlib")):
        rs = [r for r in bodies if sel(r)]
        if not rs:
            continue
        act = four([r for r in rs if r["loan_kinds"]])
        inact = four([r for r in rs if not r["loan_kinds"]])
        summary["loan_active"][label] = act
        summary["loan_active"][label + " inactive"] = inact
        emit(f"{label + ' active':32} {four_line(act)}")
        emit(f"{label + ' inactive':32} {four_line(inact)}")
    emit("-- by loan kind (all corpora) --")
    emit(f"{'':32} {FOUR_HEAD}")
    kinds = defaultdict(list)
    for r in bodies:
        for k in r["loan_kinds"]:
            kinds[k].append(r)
    for k, rs in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        emit(f"  {k:30} {four_line(four(rs))}")
    emit("-- check-only events (a check ran; all corpora): lowered --")
    checks = defaultdict(list)
    for r in bodies:
        for k in r["events"]:
            if k.startswith("check:"):
                checks[k].append(r)
    for k, rs in sorted(checks.items(), key=lambda kv: -len(kv[1])):
        c = four(rs)
        emit(f"  {k:40} {c['lowered']:5}/{c['bodies']:5} {pct(c['lowered'], c['bodies'])}")
    emit("-- loan-active by position (all corpora) --")
    for pos in positions:
        rs = [r for r in bodies if r["position"] == pos and r["loan_kinds"]]
        if rs:
            emit(f"  {pos:16} {four_line(four(rs))}")

    emit("\n== 4. first blockers (once per body) ==")
    nc = [r for r in bodies if not r["lowered"]]
    rc = Counter(r["reason_cat"] for r in nc)
    per = defaultdict(Counter)
    ex = {}
    for r in nc:
        per[r["reason_cat"]][r["corpus"]] += 1
        ex.setdefault(r["reason_cat"], f"{r['program']}::{r['qualname']}")
    emit(f"-- lowering ({len(nc)} bodies) --")
    emit(f"{'count':>6} {'tests':>6} {'ex':>4} {'lib':>5}  reason  -- example")
    for k, v in rc.most_common(25):
        emit(f"{v:6} {per[k]['tests']:6} {per[k]['examples']:4} {per[k]['stdlib']:5}  {k}  -- {ex[k]}")
    inc = [r for r in bodies if r["lowered"] and r.get("analyses") != "complete"]
    emit(f"-- analyses, over lowered bodies ({len(inc)} incomplete) --")
    for k, v in Counter(r.get("analysis_gap") for r in inc).most_common(15):
        emit(f"{v:6}  {k}")
    sg = [r for r in bodies if r["lowered"] and r.get("storage") in ("not_covered", "error", "no_facts")]
    emit(f"-- storage certificate, over lowered bodies ({len(sg)} not certified for a gap) --")
    for k, v in Counter(r.get("storage_gap") for r in sg).most_common(15):
        emit(f"{v:6}  {k}")
    emit("-- blocking TYPE named by a lowering blocker (all corpora) --")
    bt = Counter(r["blocker_type"] for r in nc if r.get("blocker_type"))
    emit("   " + ", ".join(f"{k}={v}" for k, v in bt.most_common(18)))

    emit("\n== 5. first blockers of loan-active bodies (unlock ranking) ==")
    la = [r for r in bodies if r["loan_kinds"] and not r["certified"]]
    lk = defaultdict(Counter)
    for r in la:
        for k in r["loan_kinds"]:
            lk[r["blocker"]][k] += 1
    for k, v in Counter(r["blocker"] for r in la).most_common(20):
        top = ", ".join(f"{a}={b}" for a, b in lk[k].most_common(3))
        emit(f"{v:6}  {k}   [{top}]")

    emit("\n== 6. programs with every non-init body lowered ==")
    for corpus in ("tests", "examples"):
        byprog = defaultdict(list)
        for r in bodies:
            if r["corpus"] == corpus and r["position"] != "module_init":
                byprog[r["program"]].append(r)
        if not byprog:
            continue
        full = sorted(p for p, rs in byprog.items() if all(r["lowered"] for r in rs))
        cert = sorted(p for p, rs in byprog.items() if all(r["certified"] for r in rs))
        ok = sum(1 for p in programs if p["corpus"] == corpus and p["status"] == "ok")
        emit(f"{corpus}: lowered {len(full)}/{ok}, certified {len(cert)}/{ok} compiled programs; e.g. {full[:8]}")

    emit("\n== 7. lifetime diagnostics in compiling programs (driver-attributed) ==")
    fam = defaultdict(Counter)
    for p in programs:
        if p["status"] != "ok" or p["corpus"] == "stdlib":
            continue
        bmap = {(b["module"], b["qualname"]): b for b in p["bodies"]}
        for d in p["diags"]:
            if not d["family"] or not d.get("user"):
                continue
            b = bmap.get((d["module"], d["body"]))
            key = (d["family"], d["level"])
            fam[key]["n"] += 1
            if b is None:
                fam[key]["unmapped"] += 1
            else:
                fam[key]["low" if b["lowered"] else "nl"] += 1
                fam[key]["pos:" + b["position"]] += 1
    for (f, lvl), c in sorted(fam.items(), key=lambda kv: -kv[1]["n"]):
        poss = ", ".join(f"{k[4:]}={v}" for k, v in c.most_common() if k.startswith("pos:"))
        emit(f"  {f:26} {lvl:7} n={c['n']:4} body lowered={c['low']:3} not={c['nl']:4} "
             f"unmapped={c['unmapped']:3}  [{poss}]")

    if cases_dir is not None:
        emit("\n== 8. lifetime diagnostics in expected/diag.txt (every case, ast-positioned) ==")
        tab = defaultdict(Counter)
        for d in diag_scan(cases_dir):
            key = (d["family"], d["level"], d["case_kind"])
            tab[key]["n"] += 1
            tab[key][d["position"] or "?"] += 1
            if d["inner"] and d["inner"] != "statement":
                tab[key]["in:" + d["inner"]] += 1
        for (f, lvl, ck2), c in sorted(tab.items(), key=lambda kv: (kv[0][2], -kv[1]["n"])):
            poss = ", ".join(f"{k}={v}" for k, v in c.most_common() if k != "n")
            emit(f"  {ck2:10} {f:26} {lvl:7} n={c['n']:4} [{poss}]")

    if compare_dir is not None:
        emit("")
        lines.extend(compare(rows, compare_dir))

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    text = "\n".join(lines) + "\n"
    (out_dir / "report.txt").write_text(text)
    return text


def compare(rows: list[dict], prev_dir: Path) -> list[str]:
    """Blocker transitions per body present in both runs.

    A previous run without the four-count columns (the prototype's CSV) is
    compared at the lowering stage only, so both sides use the same scale.
    """
    out = [f"== blocker transitions vs {prev_dir} =="]
    with (prev_dir / "bodies.csv").open(newline="") as fh:
        prev_rows = list(csv.DictReader(fh))
    full = bool(prev_rows) and "blocker" in prev_rows[0]

    def prev_blocker(r: dict) -> str:
        if full:
            return r["blocker"]
        return LOWERED if r["status"] == "covered" else (r["reason_cat"] or "?")

    def cur_blocker(r: dict) -> str:
        if full:
            return r["blocker"]
        return LOWERED if r["lowered"] else (r["reason_cat"] or "?")

    def key(r: dict) -> tuple:
        return (r["corpus"], r["program"], r["module"], r["qualname"])

    prev = {key(r): prev_blocker(r) for r in prev_rows if r["status"] != "no_body"}
    cur = {key(r): cur_blocker(r) for r in rows if r["status"] != "no_body"}
    both = prev.keys() & cur.keys()
    out.append(f"scale: {'full four-count blocker' if full else 'lowering stage only (previous run has no four-count)'}")
    out.append(f"bodies: previous {len(prev)}, current {len(cur)}, in both {len(both)}, "
               f"only previous {len(prev.keys() - cur.keys())}, only current {len(cur.keys() - prev.keys())}")
    for corpus in CORPORA:
        ks = [k for k in both if k[0] == corpus]
        if not ks:
            continue
        a = sum(prev[k] == LOWERED or prev[k] == "(certified)" for k in ks)
        b = sum(cur[k] == LOWERED or cur[k] == "(certified)" for k in ks)
        label = "certified" if full else "lowered"
        out.append(f"  {corpus:9} {label}: {a} -> {b} (of {len(ks)} common bodies)")
    moved = Counter((prev[k], cur[k]) for k in both if prev[k] != cur[k])
    out.append(f"{sum(moved.values())} bodies changed first blocker; top transitions:")
    for (a, b), n in moved.most_common(30):
        out.append(f"{n:6}  {a}  ->  {b}")
    only_prev = Counter(v for k, v in prev.items() if k not in cur)
    if only_prev:
        out.append("bodies gone since the previous run, by previous blocker: "
                   + ", ".join(f"{k}={v}" for k, v in only_prev.most_common(8)))
    return out


# --- expected/diag.txt scan -----------------------------------------------

_DIAG_LINE = re.compile(r"^(?P<file>[^:\s]+):(?P<line>\d+): (?P<level>error|warning): (?P<msg>.*)$")


def _ast_position(tree, line):
    """(top body position, innermost construct) of a source line."""
    chain = []

    def visit(node, parents):
        for child in ast.iter_child_nodes(node):
            lo = getattr(child, "lineno", None)
            hi = getattr(child, "end_lineno", None)
            if lo is not None and hi is not None and lo <= line <= hi:
                chain.append((child, parents))
                visit(child, parents + [child])
    visit(tree, [])
    funcs = [(n, p) for n, p in chain if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    inner = "statement"
    for n, _p in chain:
        if isinstance(n, ast.Lambda):
            inner = "lambda"
        elif isinstance(n, ast.GeneratorExp):
            inner = "genexpr"
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp)):
            inner = "comprehension"
    if not funcs:
        return ("class_body" if any(isinstance(n, ast.ClassDef) for n, _ in chain) else "module_init"), inner
    top, parents = funcs[0]
    in_class = any(isinstance(p, ast.ClassDef) for p in parents)
    decos = {getattr(d, "id", getattr(d, "attr", "")) for d in top.decorator_list}
    if isinstance(top, ast.AsyncFunctionDef):
        pos = "async"
    elif any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(top)):
        pos = "generator"
    elif not in_class:
        pos = "free_function"
    elif top.name == "__init__":
        pos = "constructor"
    elif "property" in decos or "setter" in decos:
        pos = "property"
    elif "staticmethod" in decos:
        pos = "staticmethod"
    elif "classmethod" in decos:
        pos = "classmethod"
    elif top.name.startswith("__") and top.name.endswith("__"):
        pos = "dunder_method"
    else:
        pos = "method"
    if len(funcs) > 1 and inner == "statement":
        inner = "nested_def"
    return pos, inner


def diag_scan(cases_dir: Path) -> list[dict]:
    out = []
    for diag in sorted(cases_dir.rglob("expected/diag.txt")):
        case = diag.parent.parent
        kind = ("error_case" if case.name.startswith("error_") else
                "panic_case" if case.name.startswith("panic_") else "compiling")
        trees: dict = {}
        for raw in diag.read_text(errors="replace").splitlines():
            m = _DIAG_LINE.match(raw)
            if not m:
                continue
            fam = family(m["msg"])
            if fam is None:
                continue
            f = case / "src" / m["file"]
            pos = inner = None
            if f.is_file() and f.suffix == ".py":
                if f not in trees:
                    try:
                        trees[f] = ast.parse(f.read_text())
                    except SyntaxError:
                        trees[f] = None
                if trees[f] is not None:
                    pos, inner = _ast_position(trees[f], int(m["line"]))
            out.append({"case": str(case.relative_to(cases_dir)), "case_kind": kind, "level": m["level"],
                        "family": fam, "position": pos, "inner": inner})
    return out
