"""Measure one program: compile in-process, lower every emitted body to MIR the
way `tpyc --dump-mir` does, and record the four-count verdict per body.

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
from tpyc.mir.call_effects import analyze_call_effects  # noqa: E402
from tpyc.mir.collect import _declaration_name  # noqa: E402
from tpyc.mir.dependencies import analyze_dependencies  # noqa: E402
from tpyc.mir.liveness import analyze_liveness  # noqa: E402
from tpyc.mir.lower import lower_constructor, lower_function  # noqa: E402
from tpyc.mir.nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered  # noqa: E402
from tpyc.mir.payload_lifetime import inspect_payload_lifetimes  # noqa: E402
from tpyc.mir.retention import analyze_retention  # noqa: E402
from tpyc.mir.scope_lifetime import inspect_scope_lifetimes  # noqa: E402
from tpyc.mir.storage import analyze_storage  # noqa: E402
from tpyc.mir.storage_adapter import MIRStorageRequest, certify_thir_storage  # noqa: E402
from tpyc.mir.storage_evidence import MIRStorageVerdict  # noqa: E402
from tpyc.parse import nodes as pn  # noqa: E402
from tpyc.thir.lower import iter_module_callables, iter_module_constructors  # noqa: E402
from tpyc.thir.reject import is_bodyless_binding  # noqa: E402

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

def run_analyses(fn: MIRFunction) -> dict:
    """The analyses `--dump-mir` runs over a lowered body; first gap + conflicts."""
    try:
        liveness = analyze_liveness(fn)
        deps = analyze_dependencies(fn, liveness)
        scope = inspect_scope_lifetimes(fn)
        payload = inspect_payload_lifetimes(fn)
        events = analyze_storage(fn)
        effects = analyze_call_effects(fn, deps)
        retention = analyze_retention(fn, liveness, deps, events)
    except Exception as e:  # noqa: BLE001
        return {"analyses": "error", "analysis_gap": f"error: {type(e).__name__}: {normalize(str(e))[:120]}",
                "analysis_gaps": ["error"], "analysis_conflicts": []}
    staged = (("dependencies", deps), ("scope ends", scope.ends), ("scope conflicts", scope.conflicts),
              ("payload ends", payload.ends), ("payload conflicts", payload.conflicts),
              ("storage", events), ("call effects", effects), ("retention", retention))
    gaps = [(name, r) for name, r in staged if isinstance(r, MIRNotCovered)]
    conflicts = []
    if not isinstance(scope.conflicts, MIRNotCovered) and scope.conflicts:
        conflicts.append("scope_end")
    if not isinstance(payload.conflicts, MIRNotCovered) and payload.conflicts:
        conflicts.append("payload_end")
    if not isinstance(retention, MIRNotCovered) and retention.conflicts:
        conflicts.append("replacement")
    if scope.freshness:
        conflicts.append("stale_alias")
    first = gaps[0] if gaps else None
    return {"analyses": "incomplete" if gaps else "complete",
            "analysis_gap": f"{first[0]}: {normalize(first[1].reason)[:140]}" if first else None,
            "analysis_gaps": [name for name, _r in gaps], "analysis_conflicts": conflicts}


def certify(source, body: MIRBodyId, kind: MIRBodyKind, definitions, summaries) -> dict:
    """`certify_thir_storage` verdict; `requires_proof=False` is never CERTIFIED."""
    try:
        request = MIRStorageRequest(source, body, kind, definitions, summaries)
        bound = certify_thir_storage(request)
        requires = bound.requires_proof
        if requires is None:
            return {"storage": "no_facts", "storage_gap": "storage facts have not been published"}
        if not requires:
            return {"storage": "no_proof_required", "storage_gap": None}
        verdict = bound.verdict
        if verdict is MIRStorageVerdict.CERTIFIED:
            if bound.function is not None and bound.certifies(request, source, bound.function):
                return {"storage": "certified", "storage_gap": None}
            return {"storage": "not_covered", "storage_gap": "certificate does not bind the request"}
        if verdict is MIRStorageVerdict.CONFLICT:
            kinds = sorted({c.kind.name.lower() for c in bound.evidence.conflicts})
            return {"storage": "conflict", "storage_gap": None, "storage_conflicts": kinds}
        gaps = list(bound.gaps) + (list(bound.evidence.gaps) if bound.evidence is not None else [])
        gap = (f"{gaps[0].node_kind}: {normalize(gaps[0].reason)[:140]}" if gaps
               else "no evidence")
        return {"storage": "not_covered", "storage_gap": gap}
    except Exception as e:  # noqa: BLE001
        return {"storage": "error", "storage_gap": f"error: {type(e).__name__}: {normalize(str(e))[:120]}"}


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
                    out["bodies"].extend(enumerate_bodies(m, ctx, mir.definitions, mir.workspace,
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


def enumerate_bodies(module, ctx, definitions, workspace, reasons, user, body_ranges) -> list[dict]:
    """One record per body, in the order and with the identities of `dump_codegen_mir`."""
    tpy = module.ast
    analyzer = module.analyzer
    recs: list[dict] = []
    identities: dict[str, int] = {}
    ranges = body_ranges.setdefault(module.name, [])

    def identity(name, func=None):
        name = _declaration_name(name, func)
        count = identities.get(name, 0) + 1
        identities[name] = count
        return MIRBodyId(module.name, name if count == 1 else f"{name}#{count}")

    def rec(body, position, status, raw, *, node_kind=None, extra=None, feats=(), line=None, events=None,
            btype=None, measured=None):
        r = {"module": module.name, "qualname": body.declaration, "user": user, "position": position,
             "status": status, "reason_raw": raw, "reason_cat": reason_category(raw, node_kind) if raw else None,
             "node_kind": node_kind, "features": sorted(feats), "blocker_line": line, "events": events or {},
             "blocker_type": btype, "lowered": status == "covered"}
        r.update(extra or {})
        r.update(measured or {})
        recs.append(r)

    def missing(func):
        if is_bodyless_binding(func) or func.is_overload_stub:
            return "no_body", "no body to lower"
        why = reasons.get(func)
        if why is not None:
            return "not_covered", f"THIR rejected: {why}"
        return "not_covered", "THIR not attempted: an earlier reject ended emission"

    def measure(source, body, kind, lower):
        """Lower (or take the workspace's result), then run the four-count stages."""
        cached = workspace.bodies.get(body) if kind is not MIRBodyKind.CONSTRUCTOR else None
        # The workspace lowers every callable it schedules as a free function;
        # certification must re-lower the body the same way it was lowered.
        used_kind = MIRBodyKind.FREE_FUNCTION if cached is not None else kind
        try:
            _LAST["node"] = None
            result = cached if cached is not None else lower(used_kind)
            if isinstance(result, MIRNotCovered) and cached is not None:
                _LAST["node"] = None
                again = lower(used_kind)
                if not (isinstance(again, MIRNotCovered) and again.reason == result.reason):
                    _LAST["node"] = None
        except Exception as e:  # noqa: BLE001
            return "not_covered", f"MIR crash: {type(e).__name__}: {str(e)[:120]}", None, None, None, {}
        if isinstance(result, MIRNotCovered):
            return ("not_covered", f"MIR not covered: {result.reason}", result.node_kind,
                    result.loc.line if result.loc is not None else None, blocker_type(result.reason), {})
        measured = run_analyses(result)
        measured.update(certify(source, body, used_kind, definitions, workspace.summaries))
        measured["conflict"] = bool(measured["analysis_conflicts"]) or measured["storage"] == "conflict"
        return "covered", None, None, None, None, measured

    if tpy.top_level_stmts:
        body = identity("__tpy_init")
        feats, _lo, _hi, _nested = body_scan(tpy.top_level_stmts)
        ev = dict(hooks.INIT_EVENTS.get(module.name, {}))
        if ctx.thir_top_level is not None:
            raw = "MIR not covered: module initialization"
        elif reasons.get(tpy) is not None:
            raw = f"THIR rejected: {reasons.get(tpy)}"
        else:
            raw = "THIR not attempted: an earlier reject ended emission"
        rec(body, "module_init", "not_covered", raw, feats=feats, events=ev)

    for func, owner in iter_module_callables(tpy, analyzer):
        name = f"{owner.name}.{func.name}" if owner is not None else func.name
        body = identity(name, func)
        feats, lo, hi, nested = body_scan(func.body)
        if func.loc is not None:
            lo = func.loc.line if lo is None else min(lo, func.loc.line)
        ranges.append((body.declaration, lo, hi))
        pos = position_of(func, owner)
        generic = bool(func.type_params or (owner is not None and getattr(owner, "type_args", None)))
        extra = {"generic": generic, "overloaded": bool(analyzer.overload_groups.get(func))}
        ev = _events_for(func, nested)
        common = {"extra": extra, "feats": feats, "events": ev}
        if is_bodyless_binding(func) or func.is_overload_stub:
            st, raw = missing(func)
            rec(body, pos, st, raw, **common)
        elif ctx.thir_resumables.get(func) is not None:
            rec(body, pos, "not_covered", "MIR not covered: resumable body", **common)
        elif func in ctx.thir_functions or func in ctx.thir_overload_functions:
            fn = ctx.thir_functions.get(func)
            if generic:
                rec(body, pos, "not_covered", "MIR not covered: generic body", **common)
            elif analyzer.overload_groups.get(func):
                rec(body, pos, "not_covered", "MIR not covered: overloaded callable", **common)
            elif fn is None:
                st, raw = missing(func)
                rec(body, pos, st, raw, **common)
            else:
                kind = MIRBodyKind.METHOD if owner is not None else MIRBodyKind.FREE_FUNCTION
                st, raw, nk, line, bt, measured = measure(
                    fn, body, kind, lambda k, fn=fn, body=body: lower_function(
                        fn, body, kind=k, definitions=definitions, summaries=workspace.summaries))
                rec(body, pos, st, raw, node_kind=nk, line=line, btype=bt, measured=measured, **common)
        else:
            st, raw = missing(func)
            rec(body, pos, st, raw, **common)

    for record, ctor, _owner in iter_module_constructors(tpy, analyzer):
        body = identity(f"{record.name}.__init__", ctor)
        feats, lo, hi, nested = body_scan(ctor.body)
        if ctor.loc is not None:
            lo = ctor.loc.line if lo is None else min(lo, ctor.loc.line)
        ranges.append((body.declaration, lo, hi))
        common = {"extra": {"generic": bool(record.type_params), "overloaded": False}, "feats": feats,
                  "events": _events_for(ctor, nested)}
        if ctor not in ctx.thir_constructors:
            st, raw = missing(ctor)
            rec(body, "constructor", st, raw, **common)
        elif record.type_params:
            rec(body, "constructor", "not_covered", "MIR not covered: generic constructor", **common)
        else:
            thir_ctor = ctx.thir_constructors[ctor]
            st, raw, nk, line, bt, measured = measure(
                thir_ctor, body, MIRBodyKind.CONSTRUCTOR, lambda _k, c=thir_ctor, body=body: lower_constructor(
                    c, body, definitions=definitions, summaries=workspace.summaries))
            rec(body, "constructor", st, raw, node_kind=nk, line=line, btype=bt, measured=measured, **common)
    return recs
