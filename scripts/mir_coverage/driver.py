"""Measure one program: compile in-process, lower every emitted body to MIR the
way `tpyc --dump-mir` does, and record the four-count verdict per body (plus
the exceptional-exit fact).

A job is `{corpus, program, src, opts, mode}`; `mode` is `user` (report the
program's own modules, workspace over them -- the `--dump-mir` semantics) or
`stdlib` (workspace over every compiled module, report the library ones).

Importing this module installs the sema hooks (`hooks.py`), so it must only be
imported by a measurement process, never by a test runner.
"""
import contextlib
import dataclasses
import io
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
# The compiler recurses per nesting level; big stdlib bodies exceed the default.
sys.setrecursionlimit(20000)

import hooks  # noqa: E402

hooks.install()

from families import family  # noqa: E402
import tpyc.mir.lower as _mir_lower  # noqa: E402
import tpyc.mir.coverage as _mir_cov  # noqa: E402
from tpyc.mir.coverage import scalar_param  # noqa: E402
from tpyc.codegen_cpp.context import CodeGenOptions  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.diagnostics import DiagnosticLevel, format_diagnostics  # noqa: E402
from tpyc.mir.collect import (  # noqa: E402
    MIRBodyVerdict, MIRRefusalKind, MIRStorageCheck, MIRStorageState, MIRVerdictStatus, enumerate_body_sources,
    verdict_of,
)
from tpyc.parse import nodes as pn  # noqa: E402

TPY_LIB = REPO / "lib" / "tpy"
CASES = REPO / "tests" / "cases"

# MIRNotCovered keeps only the node's class name; remember the node itself so
# the report can name the blocking TYPE ("unsupported return type" of what).
_LAST: dict = {"node": None}
_orig_require = _mir_lower._require
_orig_unsup_init = _mir_cov.MIRUnsupported.__init__


def _spy_require(node, condition, reason):
    if not condition:
        _LAST["node"] = node
    return _orig_require(node, condition, reason)


def _spy_unsup_init(self, node, reason):
    _LAST["node"] = node
    _orig_unsup_init(self, node, reason)


_mir_lower._require = _spy_require
_mir_cov.MIRUnsupported.__init__ = _spy_unsup_init


def blocker_type(reason: str) -> str | None:
    node = _LAST["node"]
    if node is None:
        return None
    try:
        if "return type" in reason:
            return str(node.return_type)
        if "parameter type" in reason:
            # The parameter lowering refused: no layout fact and no inert leaf
            # at its published passing.
            bad = [str(p.type) for p in node.params
                   if not scalar_param(p) and all(getattr(p, fact, None) is None for fact in (
                       "borrowed_record", "optional_layout", "union_layout", "tuple_layout", "native_container"))]
            return bad[0] if bad else None
        for attr in ("resolved_type", "result_type"):
            t = getattr(node, attr, None)
            if t is not None:
                return str(t)
    except Exception:  # noqa: BLE001
        return None
    return None


FEATURE_NODES = {
    "nested_def": (pn.TpyNestedDef,),
    "lambda": (pn.TpyLambda,),
    "comprehension": (pn.TpyListComprehension, pn.TpyDictComprehension, pn.TpySetComprehension),
    "try": (pn.TpyTry,),
    "with": (pn.TpyWith,),
    "match": (pn.TpyMatch,),
    "for": (pn.TpyForEach,),
    "while": (pn.TpyWhile,),
}


def _parse_node(x) -> bool:
    return dataclasses.is_dataclass(x) and not isinstance(x, type) and type(x).__module__.endswith("parse.nodes")


def walk_nodes(roots):
    """Every TpyStmt/TpyExpr reachable through dataclass fields (nested defs excluded)."""
    stack = list(roots)
    seen = set()
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        yield n
        if not dataclasses.is_dataclass(n):
            continue
        for f in dataclasses.fields(n):
            v = getattr(n, f.name, None)
            if isinstance(v, (pn.TpyStmt, pn.TpyExpr)):
                stack.append(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    if isinstance(x, (pn.TpyStmt, pn.TpyExpr)):
                        stack.append(x)
                    elif isinstance(x, (list, tuple)):
                        stack.extend(y for y in x if isinstance(y, (pn.TpyStmt, pn.TpyExpr)))
                    elif _parse_node(x):
                        stack.append(x)
            elif _parse_node(v) and not isinstance(v, pn.TpyFunction):
                stack.append(v)


def body_scan(stmts):
    """(feature tags, first line, last line, nested defs) of one body."""
    feats = set()
    lo = hi = None
    nested = []
    for n in walk_nodes(stmts):
        loc = getattr(n, "loc", None)
        if loc is not None and getattr(loc, "line", None):
            lo = loc.line if lo is None else min(lo, loc.line)
            hi = loc.line if hi is None else max(hi, loc.line)
        for k, types in FEATURE_NODES.items():
            if isinstance(n, types):
                feats.add(k)
        if isinstance(n, pn.TpyNestedDef):
            nested.append(n.func)
            for m in walk_nodes(n.func.body):
                loc = getattr(m, "loc", None)
                if loc is not None and getattr(loc, "line", None):
                    hi = max(hi or loc.line, loc.line)
    return feats, lo, hi, nested


def position_of(func, owner) -> str:
    if func.is_genexpr:
        return "genexpr_frame"
    if func.is_async:
        return "async"
    if func.is_generator:
        return "generator"
    if owner is None:
        return "free_function"
    if func.is_property_getter or func.is_property_setter:
        return "property"
    if func.is_staticmethod:
        return "staticmethod"
    if func.is_classmethod:
        return "classmethod"
    if func.name.startswith("__") and func.name.endswith("__"):
        return "dunder_method"
    return "method"


_QUOTED = re.compile(r"'[^']*'")
_NUM = re.compile(r"%?\b\d+\b")
_GENERIC_REASONS = ("unsupported expression", "unsupported statement", "unsupported record initializer",
                    "reference needs local name")


def normalize(reason: str) -> str:
    """Strip names and numbers so one blocker class is one category."""
    return _NUM.sub("N", _QUOTED.sub("'X'", reason))


def reason_category(raw: str, node_kind: str | None) -> str:
    r = raw
    if r.startswith("MIR not covered: "):
        r = r[len("MIR not covered: "):]
        if node_kind and r in _GENERIC_REASONS:
            return f"{r} [{node_kind}]"
        r = normalize(r)
        if node_kind and len(r) < 60:
            return f"{r} [{node_kind}]"
        return r[:160]
    if r.startswith("THIR rejected"):
        m = re.search(r"\(([^)]*)\)\s*$", r)
        return "THIR rejected" + (f" ({m.group(1).split(':')[0]})" if m else "")
    return r


def load_opts(case_dir: Path) -> dict:
    """The conftest's layered options.json merge (deeper wins; dsl_opts per key)."""
    layers = []
    cur = case_dir
    while True:
        layers.append(cur / "options.json")
        if cur == CASES or cur == cur.parent:
            break
        cur = cur.parent
    out: dict = {}
    for layer in reversed(layers):
        if layer.is_file():
            for k, v in json.loads(layer.read_text()).items():
                if k == "dsl_opts":
                    out.setdefault("dsl_opts", {}).update(v)
                else:
                    out[k] = v
    return out


def _frontend(opts: dict):
    spec = opts.get("plugin")
    if not spec:
        return None, []
    from tpyc.frontend_plugin import FrontendRegistry, load_plugin
    plugin = load_plugin(str((REPO / spec).resolve()), dict(opts.get("dsl_opts", {})))
    reg = FrontendRegistry()
    reg.register(plugin)
    return reg, [Path(p).resolve() for p in plugin.library_paths()]


# --- the four-count per lowered body ---------------------------------------

def storage_columns(check: MIRStorageCheck) -> dict:
    state = check.state.name.lower()
    if check.state is MIRStorageState.CONFLICT:
        return {"storage": state, "storage_gap": None, "storage_conflicts": list(check.conflicts)}
    return {"storage": state, "storage_gap": normalize(check.gap)[:140] if check.gap is not None else None}


def lowered_columns(verdict: MIRBodyVerdict) -> dict:
    """The analyses, exit fact and storage verdict of a lowered body as report columns."""
    analyses, storage = verdict.analyses, verdict.storage
    assert analyses is not None and storage is not None
    incomplete = verdict.status is MIRVerdictStatus.INCOMPLETE
    columns = {"analyses": "incomplete" if incomplete else "complete",
               # The verdict's reason is the first gap as "<analysis>: <reason>".
               "analysis_gap": normalize(verdict.reason)[:140] if incomplete else None,
               "analysis_gaps": [name for name, _r in analyses.gaps], "analysis_conflicts": list(analyses.conflicts),
               # Storage verdicts are normal-path evidence; the exit fact says which bodies they leave uncovered.
               "exceptional_exits": verdict.exceptional_exits}
    columns.update(storage_columns(storage))
    columns["conflict"] = bool(verdict.conflicts)
    return columns


# --- per-program job -------------------------------------------------------

def run_job(job: dict) -> dict:
    hooks.reset()
    src = Path(job["src"])
    opts = job.get("opts") or {}
    mode = job.get("mode", "user")
    t0 = time.monotonic()
    out = {"corpus": job["corpus"], "program": job["program"], "mode": mode, "bodies": [], "diags": []}
    old_cwd = os.getcwd()
    try:
        reg, extra = _frontend(opts)
        os.chdir(src.parent)
        compiler = Compiler(src, default_int=opts.get("default_int", "int32"),
                            lib_dirs=extra + [TPY_LIB], frontend_registry=reg)
        with contextlib.redirect_stdout(io.StringIO()):
            modules = compiler.compile()
        reported = format_diagnostics(compiler, modules)
        if any(d.level == DiagnosticLevel.ERROR for d, _ in reported):
            out["status"] = "compile_error"
            out["error"] = [line for d, line in reported if d.level == DiagnosticLevel.ERROR][:3]
            return out
        is_user = {m.name: compiler.is_user_module(m) for m in modules}
        if mode == "user":
            targets = [m for m in modules if is_user[m.name]]
        else:
            targets = [m for m in modules if m.ast is not None and m.analyzer is not None]
        collected = []
        with contextlib.redirect_stdout(io.StringIO()):
            for m in targets:
                try:
                    ctx = compiler.collect_thir(m, CodeGenOptions(), tolerate_reject=True)
                except Exception as e:  # noqa: BLE001
                    out.setdefault("collect_errors", []).append(f"{m.name}: {type(e).__name__}: {str(e)[:200]}")
                    continue
                collected.append((m, ctx))
        body_ranges: dict[str, list] = {}
        report_user = mode == "user"
        with compiler.mir_analysis(collected) as mir:
            for m, ctx in collected:
                if is_user[m.name] == report_user:
                    out["bodies"].extend(body_records(m, ctx, mir.definitions, mir.workspace,
                                                      compiler.thir_reject_by_node, is_user[m.name],
                                                      body_ranges))
        _attribute_diags(out, modules, is_user, body_ranges)
        out["unattributed_events"] = dict(hooks.UNATTRIBUTED)
        out["status"] = "ok"
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, KeyboardInterrupt):
            raise
        # Sema still raises some user errors instead of reporting them.
        out["status"] = "compile_error" if type(e).__name__ == "SemanticError" else "exception"
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        if out["status"] == "exception":
            out["tb"] = traceback.format_exc()[-1500:]
    finally:
        os.chdir(old_cwd)
        out["t"] = round(time.monotonic() - t0, 2)
    return out


def _attribute_diags(out: dict, modules, is_user: dict, body_ranges: dict) -> None:
    path_to_mod = {str(m.path.resolve()): m.name for m in modules if m.path is not None}
    for m in modules:
        if m.analyzer is None:
            continue
        for d in m.analyzer.diagnostics:
            fam = family(d.message)
            if fam is None:
                continue
            line = d.loc.line if d.loc is not None else None
            mod_name = m.name
            if d.loc is not None and getattr(d.loc, "file", None):
                mod_name = path_to_mod.get(str(Path(d.loc.file).resolve()), mod_name)
            out["diags"].append({"module": mod_name, "line": line, "level": d.level.value, "family": fam,
                                 "msg": d.message[:200], "body": _locate(body_ranges.get(mod_name, []), line),
                                 "user": is_user.get(mod_name, False)})


def _locate(ranges, line):
    if line is None:
        return None
    best = None
    for key, lo, hi in ranges:
        if lo is not None and hi is not None and lo <= line <= hi:
            if best is None or (hi - lo) < (best[2] - best[1]):
                best = (key, lo, hi)
    return best[0] if best else "__tpy_init"


def _events_for(func, nested) -> dict:
    c: Counter = Counter()
    for f in [func, *nested]:
        e = hooks.EVENTS.get(id(f))
        if e is not None and e[0] is f:
            c.update(e[1])
    return dict(c)


def body_records(module, ctx, definitions, workspace, reasons, user, body_ranges) -> list[dict]:
    """One record per body of `collect.enumerate_body_sources`, the walk and
    identities `dump_codegen_mir` uses, measured by `collect.verdict_of`; the
    survey columns are this tool's own."""
    tpy = module.ast
    analyzer = module.analyzer
    recs: list[dict] = []
    ranges = body_ranges.setdefault(module.name, [])

    def rec(body, position, status, raw, *, node_kind=None, extra=None, feats=(), line=None, events=None,
            btype=None, measured=None):
        r = {"module": module.name, "qualname": body.declaration, "user": user, "position": position,
             "status": status, "reason_raw": raw, "reason_cat": reason_category(raw, node_kind) if raw else None,
             "node_kind": node_kind, "features": sorted(feats), "blocker_line": line, "events": events or {},
             "blocker_type": btype, "lowered": status == "covered"}
        r.update(extra or {})
        r.update(measured or {})
        recs.append(r)

    def measure(source) -> dict:
        """The body's verdict as `rec` arguments. A MIR exception is recorded
        rather than raised, so one body cannot end the program's measurement."""
        _LAST["node"] = None
        try:
            verdict = verdict_of(source, definitions, workspace, fresh=True)
        except Exception as e:  # noqa: BLE001
            return {"status": "not_covered", "raw": f"MIR crash: {type(e).__name__}: {str(e)[:120]}"}
        refusal = verdict.refusal
        if refusal is None:
            return {"status": "covered", "raw": None, "measured": lowered_columns(verdict)}
        return {"status": "no_body" if refusal.kind is MIRRefusalKind.NO_BODY else "not_covered",
                "raw": refusal.display, "node_kind": refusal.node_kind,
                "line": refusal.loc.line if refusal.loc is not None else None,
                "btype": blocker_type(refusal.reason)}

    for source in enumerate_body_sources(tpy, analyzer, ctx, module.name, reasons):
        body, func = source.body, source.func
        if func is None:
            feats, _lo, _hi, _nested = body_scan(tpy.top_level_stmts)
            rec(body, "module_init", feats=feats, events=dict(hooks.INIT_EVENTS.get(module.name, {})),
                **measure(source))
            continue
        feats, lo, hi, nested = body_scan(func.body)
        if func.loc is not None:
            lo = func.loc.line if lo is None else min(lo, func.loc.line)
        ranges.append((body.declaration, lo, hi))
        if source.record is not None:
            pos = "constructor"
            extra = {"generic": bool(source.record.type_params), "overloaded": False}
        else:
            pos = position_of(func, source.owner)
            extra = {"generic": bool(func.type_params or (source.owner is not None
                                                           and getattr(source.owner, "type_args", None))),
                     "overloaded": bool(analyzer.overload_groups.get(func))}
        rec(body, pos, extra=extra, feats=feats, events=_events_for(func, nested), **measure(source))
    return recs
