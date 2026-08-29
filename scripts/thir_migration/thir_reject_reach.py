"""Reach census over the `raise ThirUnsupported` population: which sites a
program we actually compile can reach, and what happens when it does.

WHY this is not the fallback tally. Today a reject is a routing decision, so
the instruments that exist count BODIES (`thir_stdlib_fallback.py`) or the tag
a body was blocked by (`thir_stdlib_sites.py`). After the cutover there is no
AST body emitter to degrade into, so a reject that reaches the per-body
boundary stops being a routing decision and becomes a hard compile error on
whatever user program hit it. The population that has to be audited before
that flip is therefore the RAISE SITES, and they split three ways:

  caught     the raise is control flow INSIDE lowering -- a sibling arm is
             tried, or the reason is recomposed onto an enclosing landmark
             and re-raised. Harmless at cutover in itself.
  fallback   the raise reached a body boundary and the body fell back. At
             cutover this is a hard error on that source.
  unreached  no compile in the swept populations ever constructed it. Not
             proven dead -- proven UNMEASURED, which is the point.

The key counted is SITES: one `file:line` per `ThirUnsupported(...)`
construction, deduped across every occurrence. It is NOT the reject-tag key
and NOT the body key -- a single site composes many tags (`stmt_reject_reason`
prefixes the host statement's shape), and one body's fallback names exactly
one site. Never compare a figure here against a fallback-body count.

METHOD. Two spies, both keyed off facts that hold statically in this tree:

  * `ThirUnsupported.__init__` records the constructing frame (its caller),
    which is the raise site.
  * `note()` decides the outcome. Every `except ThirUnsupported` handler in
    `tpyc/thir/` either re-raises (internal control flow) or calls
    `note(ex.reason)` / `_reject(ex.reason)` and returns None (a body
    boundary) -- checked by parsing the handlers here, so a new handler that
    breaks the dichotomy shows up as a boundary-line set that no longer
    matches. A `note()` running with a ThirUnsupported in flight AND standing
    on one of those handler lines means that exception was ABSORBED; every
    other exception constructed during the same lowering attempt was caught.

Attempts are bracketed by `begin_attempt` / `fold_attempt` / `commit_attempt`,
so the outcome is read per (site, ATTEMPT) rather than per site: the same site
can be caught in one body and fatal in another, and both are reported.

BLIND SPOTS, all of them load-bearing:

 1. `resumable.py` / `simple_gen.py` reject through `_reject()`, which notes a
    reason and returns None without ever constructing a ThirUnsupported. Those
    are counted, but in their own key (`note_only`) -- they are NOT part of
    the raise-site population and must never be added to it.
 2. `error_*` cases never reach codegen (the harness returns after sema
    errors), so no site reachable only from a program that fails compilation
    can be observed. Anything gated behind a diagnostic lands in `unreached`
    wrongly.
 3. The corpus sweep routes USER modules only (the shipped scoping gate); the
    stdlib sweep lifts it. Neither population is user code in the wild, so
    `unreached` is an upper bound on genuinely dead sites, never a proof.
 4. Sites reached outside any attempt window (signature/type work) are
    reported separately: their post-cutover behaviour depends on a caller
    this instrument does not model.
 5. Corpus and stdlib can only reach a raise site by FAILING to route, and
    the per-case ratchet forbids exactly that -- so the healthier the
    migration gets, the blinder these two populations become. The `units`
    population exists to counter that: only a boundary pin reaches a site on
    purpose. Run `--population all` or the census reads far emptier than the
    tree deserves.

Usage (from the repo root):
    uv run python scripts/thir_migration/thir_reject_reach.py \
        [--population corpus|stdlib|units|both|all] [--jobs N] [--limit N] \
        [--out reach.json] [--report reach.json]
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(os.environ.get("TPY_REPO") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(REPO))

_SWEEP = REPO / "scripts" / "thir_migration" / "thir_stdlib_fallback.py"
_spec = importlib.util.spec_from_file_location("tpy_thir_stdlib_fallback", _SWEEP)
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

from tpyc.thir import fallback as FB  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp.context import CodeGenOptions  # noqa: E402

THIR_DIR = REPO / "tpyc" / "thir"
CASES_DIR = REPO / "tests" / "cases"
DEFAULT_OUT = REPO / "__tpyc__" / "thir_reject_reach.json"
# Mirrors tests/conftest.py TEST_CODEGEN_OPTIONS: the reject an option-gated
# arm takes depends on these, so a census emitted under CLI defaults would be
# measuring a configuration the corpus never runs.
CORPUS_OPTS = CodeGenOptions(emit_source_comments=True,
                             comment_line_numbers=False, thir_codegen=True)


def _rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return path


def _thir_sources() -> 'list[Path]':
    return [p for p in sorted(THIR_DIR.rglob("*.py"))
            if "__pycache__" not in p.parts and not p.name.startswith("test_")]


def static_sites() -> 'dict[str, dict]':
    """Every `ThirUnsupported(...)` construction in non-test `tpyc/thir/`.

    AST-counted, not grepped: a construction spanning several lines is one
    site, and the line recorded is the one the interpreter reports for the
    call, so static and dynamic keys are directly comparable."""
    out: dict[str, dict] = {}
    for path in _thir_sources():
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text())
        sites: list[tuple[str, ast.Call]] = []
        # `_lower_stmt_dispatch` and `_lower_expr_impl` are ~7k and ~5k lines
        # and hold 40% of the population between them, so the enclosing def is
        # not a grouping. The nearest preceding witness / detail tag names the
        # ARM instead, which is the unit anyone would actually work.
        markers: dict[str, list[tuple[int, str]]] = defaultdict(list)
        depths: dict[int, int] = {}
        for func, depth, node in _walk_calls(tree):
            f = node.func
            if not isinstance(f, ast.Name):
                continue
            if f.id == "ThirUnsupported":
                sites.append((func, node))
                depths[id(node)] = depth
            elif (f.id in ("note_detail", "_witness") and node.args
                  and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)):
                markers[func].append((node.lineno, node.args[0].value))
        for func, node in sites:
            arm = func
            for lineno, text in sorted(markers[func]):
                if lineno > node.lineno:
                    break
                arm = text
            out[f"{rel}:{node.lineno}"] = {
                "file": rel, "func": func, "arm": arm,
                "depth": depths[id(node)],
                # The reason EXPRESSION, not a formatted tag: it is the only
                # static signal for how wide a site's guard is (a bare
                # `stmt.<kind>` dispatch tail catches a whole AST node kind;
                # a composed one sits behind a shape probe).
                "reason_src": (ast.unparse(node.args[0])[:100]
                               if node.args else ""),
            }
    return out


# Branch/loop/handler nodes: how many of them a raise sits under, inside its
# own def, is the cheapest static proxy for how NARROW its guard is. A depth-0
# or depth-1 raise is a dispatch tail that a whole construct kind falls into; a
# deep one sits behind a stack of shape probes. Coarse on purpose -- it ranks
# groups, it does not decide anything.
_GUARDS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
           ast.Try, ast.ExceptHandler, ast.match_case)


def _walk_calls(tree: ast.AST):
    """Yield (enclosing def name, guard depth, Call node) for every call."""
    stack: list[tuple[str, int, ast.AST]] = [("<module>", 0, tree)]
    while stack:
        name, depth, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            is_def = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            child_name = child.name if is_def else name
            child_depth = (0 if is_def
                           else depth + (1 if isinstance(child, _GUARDS)
                                         else 0))
            if isinstance(child, ast.Call):
                yield name, depth, child
            stack.append((child_name, child_depth, child))


def boundary_lines() -> 'set[tuple[str, int]]':
    """Lines that ABSORB a ThirUnsupported into a body-level reject.

    A `note(...)` / `_reject(...)` call lexically inside an
    `except ThirUnsupported` handler. Derived rather than listed so a new
    handler is classified by what it does, not by a table someone forgot."""
    out: set[tuple[str, int]] = set()
    for path in _thir_sources():
        rel = str(path.relative_to(REPO))
        for h in ast.walk(ast.parse(path.read_text())):
            if not isinstance(h, ast.ExceptHandler):
                continue
            names = ([h.type] if isinstance(h.type, ast.Name)
                     else list(getattr(h.type, "elts", [])))
            if not any(isinstance(n, ast.Name) and n.id == "ThirUnsupported"
                       for n in names):
                continue
            absorbs = reraises = False
            for stmt in h.body:
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Raise):
                        reraises = True
                    elif (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id in ("note", "_reject")):
                        out.add((rel, node.lineno))
                        absorbs = True
            if not absorbs and not reraises:
                # The whole outcome split rests on the handler dichotomy
                # (absorb into a body reject, or re-raise as control flow). A
                # handler that does neither swallows the reject silently and
                # would be scored as "caught" on a body that in fact degraded.
                print(f"tpy| WARNING: {rel}:{h.lineno} catches ThirUnsupported "
                      f"without noting or re-raising -- outcomes for that "
                      f"path are unclassified", file=sys.stderr)
    return out


class Census:
    """Per-site outcome tallies plus the samples that make them readable."""

    def __init__(self) -> None:
        # site -> outcome -> count, over (site, ATTEMPT) pairs.
        self.site: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        # Reject reasons and unit labels observed for a fallback-causing site.
        self.samples: dict[str, dict[str, list]] = defaultdict(
            lambda: {"reason": [], "unit": []})
        self.note_only: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        self.note_samples: dict[str, dict[str, list]] = defaultdict(
            lambda: {"reason": [], "unit": []})
        self.counters: dict[str, int] = defaultdict(int)
        self.attempts: dict[str, int] = defaultdict(int)

    def add_sample(self, slot: dict, reason: str, unit: str) -> None:
        for key, val in (("reason", reason), ("unit", unit)):
            if val and val not in slot[key] and len(slot[key]) < 4:
                slot[key].append(val)

    def to_json(self) -> dict:
        return {
            "site": {k: dict(v) for k, v in self.site.items()},
            "samples": dict(self.samples),
            "note_only": {k: dict(v) for k, v in self.note_only.items()},
            "note_samples": dict(self.note_samples),
            "counters": dict(self.counters),
            "attempts": dict(self.attempts),
        }

    def merge(self, other: dict) -> None:
        for key in ("site", "note_only"):
            for site, outcomes in other[key].items():
                slot = getattr(self, key)[site]
                for outcome, n in outcomes.items():
                    slot[outcome] += n
        for key in ("samples", "note_samples"):
            for site, s in other[key].items():
                slot = getattr(self, key)[site]
                for reason in s["reason"]:
                    self.add_sample(slot, reason, "")
                for unit in s["unit"]:
                    self.add_sample(slot, "", unit)
        for k, n in other["counters"].items():
            self.counters[k] += n
        for k, n in other["attempts"].items():
            self.attempts[k] += n


class _Attempt:
    __slots__ = ("raises", "notes", "absorbed")

    def __init__(self) -> None:
        self.raises: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.absorbed: list[tuple[str, tuple[str, ...]]] = []


_census = Census()
_boundary: set[tuple[str, int]] = set()
_attempt: '_Attempt | None' = None
_label = "?"


def set_label(label: str) -> None:
    global _label
    _label = label


def _construct_site() -> str:
    f = sys._getframe(2)  # 0 = here, 1 = the __init__ spy, 2 = the raise
    return f"{_rel(f.f_code.co_filename)}:{f.f_lineno}"


def _chain(exc: BaseException) -> 'tuple[str, ...]':
    """Origin sites of the ThirUnsupported chain a recomposed reject carries.

    `raise ThirUnsupported(f"...{ex.reason}") from None` keeps the inner
    exception in `__context__`, so the site that DECIDED the reject is
    recoverable even though the site that reached the boundary is the
    outermost composer."""
    out: list[str] = []
    cur = exc.__context__
    while isinstance(cur, FB.ThirUnsupported) and len(out) < 8:
        out.append(getattr(cur, "_reach_site", "?"))
        cur = cur.__context__
    return tuple(out)


def _close(routed: bool) -> None:
    global _attempt
    at = _attempt
    _attempt = None
    if at is None:
        return
    _census.attempts["routed" if routed else "fallback"] += 1
    fatal: set[str] = set()
    if not routed:
        if at.absorbed:
            fatal = {site for site, _c in at.absorbed}
        else:
            _census.counters["fallback_without_absorbed_raise"] += 1
    elif at.absorbed:
        # A nested entry rejected but the enclosing body still routed: the
        # raise did not cost this body, so it is not fallback-causing here.
        _census.counters["absorbed_in_routed_attempt"] += 1
    compiler = FB.get_current_compiler()
    reason = getattr(compiler, "_thir_reject_reason", None) or ""
    for site in set(at.raises):
        if site in fatal:
            _census.site[site]["fallback"] += 1
            _census.add_sample(_census.samples[site], reason, _label)
        else:
            _census.site[site]["caught"] += 1
    if not routed:
        for _site, chain in at.absorbed:
            for inner in chain:
                _census.site[inner]["chained"] += 1
                _census.add_sample(_census.samples[inner], reason, _label)
    if not routed and not at.absorbed:
        # The frame-gate class: a reject decided by `note()` with no
        # exception anywhere. Attributed to the note whose reason WON,
        # since `note` is set-if-empty and earlier probe notes may sit in
        # the same attempt.
        for site, note_reason in at.notes:
            if note_reason == reason:
                _census.note_only[site]["fallback"] += 1
                _census.add_sample(_census.note_samples[site], reason, _label)
                break
        else:
            _census.counters["fallback_note_unmatched"] += 1


_installed = False


def install_spies() -> None:
    """Patch the constructor and `note`, in fallback.py AND in the four
    modules that imported `note` by name (a module-level rebind would miss
    them and silently report every reject as unreached)."""
    global _boundary, _installed
    if _installed:
        return
    _installed = True
    _boundary = boundary_lines()
    orig_init = FB.ThirUnsupported.__init__
    orig_note = FB.note
    orig_begin = FB.begin_attempt
    orig_fold = FB.fold_attempt
    orig_commit = FB.commit_attempt

    def spy_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        site = _construct_site()
        self._reach_site = site
        if _attempt is not None:
            _attempt.raises.append(site)
        else:
            _census.site[site]["outside_attempt"] += 1

    def spy_note(reason: str) -> bool:
        exc = sys.exc_info()[1]
        if isinstance(exc, FB.ThirUnsupported):
            f1 = sys._getframe(1)
            frames = [f1]
            if f1.f_code.co_name == "_reject" and f1.f_back is not None:
                frames.append(f1.f_back)
            for f in frames:
                if (_rel(f.f_code.co_filename), f.f_lineno) in _boundary:
                    if _attempt is not None:
                        _attempt.absorbed.append(
                            (getattr(exc, "_reach_site", "?"), _chain(exc)))
                    else:
                        _census.counters["absorbed_outside_attempt"] += 1
                    return orig_note(reason)
            _census.counters["note_under_unrelated_exception"] += 1
        if _attempt is not None:
            f1 = sys._getframe(1)
            if f1.f_code.co_name == "_reject" and f1.f_back is not None:
                f1 = f1.f_back
            _attempt.notes.append(
                (f"{_rel(f1.f_code.co_filename)}:{f1.f_lineno}", reason))
        return orig_note(reason)

    def spy_begin() -> None:
        global _attempt
        if _attempt is not None:
            # An attempt that neither folded nor committed escaped through a
            # crash; counting it as either would launder a hard failure.
            _census.counters["attempt_escaped"] += 1
            _attempt = None
        orig_begin()
        _attempt = _Attempt()

    def spy_fold(component: str, node: object = None) -> None:
        orig_fold(component, node)
        _close(routed=False)

    def spy_commit() -> None:
        orig_commit()
        _close(routed=True)

    FB.ThirUnsupported.__init__ = spy_init
    FB.note = spy_note
    FB.begin_attempt = spy_begin
    FB.fold_attempt = spy_fold
    FB.commit_attempt = spy_commit
    import tpyc.thir.constants
    import tpyc.thir.lower.functions
    import tpyc.thir.lower.resumable
    import tpyc.thir.lower.simple_gen
    for mod in (tpyc.thir.constants, tpyc.thir.lower.functions,
                tpyc.thir.lower.resumable, tpyc.thir.lower.simple_gen):
        if getattr(mod, "note", None) is orig_note:
            mod.note = spy_note


def _case_default_int(case_dir: Path) -> 'str | None':
    """Layered options.json walk, mirroring the conftest -- but reporting a
    plugin case as unrunnable rather than compiling it with the wrong
    frontend."""
    default_int = "Int32"
    parts: list[Path] = []
    cur = case_dir
    while cur != CASES_DIR and CASES_DIR in cur.parents:
        parts.append(cur)
        cur = cur.parent
    for d in reversed(parts):
        f = d / "options.json"
        if not f.exists():
            continue
        raw = json.loads(f.read_text())
        if raw.get("plugin"):
            return None
        default_int = raw.get("default_int", default_int)
    return default_int


def run_case(main_py: Path) -> str:
    """Compile one corpus case with THIR on, discarding the C++.

    Returns a one-word outcome so the sweep can report what it could not
    measure instead of quietly measuring less."""
    case_dir = main_py.parent.parent
    default_int = _case_default_int(case_dir)
    if default_int is None:
        return "skipped_plugin"
    compiler = Compiler(main_py, default_int=default_int,
                        lib_dirs=[sweep.LIB])
    modules = compiler.compile()
    for mod in modules:
        for d in mod.analyzer.diagnostics:
            if d.level.name == "ERROR":
                return "sema_error"
    for mod in modules:
        # The shipped scoping gate stays ON here: `_make_codegen` turns THIR
        # off for non-user modules, so the corpus measures what the corpus
        # actually routes today.
        compiler.collect_thir(mod, CORPUS_OPTS)
    return "ok"


def run_units(path: str) -> str:
    """One THIR unit-test FILE, as part of the third population.

    It is the only population that reaches a raise site ON PURPOSE: the
    migration's "three units" rule asks every new arm for a boundary pin, and
    a boundary pin is a program built to be rejected. Corpus and stdlib can
    only reach a site by FAILING to route, which the per-case ratchet forbids
    -- so without this population the census reports the pinned sites as
    unreached.

    Per FILE, and `-n0` inside: an xdist worker is a fresh interpreter with no
    spy in it and would silently measure nothing, so the parallelism has to
    come from this script's own pool instead."""
    import pytest
    rc = pytest.main(["-q", "-n0", "-p", "no:cacheprovider", path])
    return f"units_rc:{int(rc)}"


def _worker(chunk: 'list[str]') -> dict:
    install_spies()
    outcomes: dict[str, int] = defaultdict(int)
    for item in chunk:
        kind, name = item.split("|", 1)
        set_label(f"{kind}:{name}")
        try:
            if kind == "units":
                outcomes[run_units(name)] += 1
            elif kind == "case":
                outcomes[run_case(Path(name))] += 1
            else:
                sweep.run_entry(f"import {name}\n", _tmpdir(),
                                name.replace(".", "_"))
                outcomes["ok"] += 1
        except Exception as exc:  # noqa: BLE001 -- a crash IS the datum
            outcomes[f"crash:{type(exc).__name__}"] += 1
    payload = _census.to_json()
    payload["outcomes"] = dict(outcomes)
    return payload


def _tmpdir() -> Path:
    d = REPO / "__tpyc__" / "_reach_tmp" / str(os.getpid())
    d.mkdir(parents=True, exist_ok=True)
    return d


def unit_items(limit: 'int | None') -> 'list[str]':
    files = sorted(THIR_DIR.rglob("test_*.py"))
    items = [f"units|{p}" for p in files]
    return items[:limit] if limit else items


def corpus_items(limit: 'int | None') -> 'list[str]':
    mains = sorted(CASES_DIR.glob("*/*/src/main.py"))
    items = [f"case|{p}" for p in mains]
    return items[:limit] if limit else items


def stdlib_items(limit: 'int | None') -> 'list[str]':
    names = [n for n in sweep.stdlib_module_names()
             if not sweep.is_macro_module(n)]
    items = [f"stdlib|{n}" for n in names]
    return items[:limit] if limit else items


def sweep_items(items: 'list[str]', jobs: int) -> tuple[Census, dict]:
    census = Census()
    outcomes: dict[str, int] = defaultdict(int)
    if jobs <= 1:
        payload = _worker(items)
        census.merge(payload)
        for k, n in payload["outcomes"].items():
            outcomes[k] += n
        return census, dict(outcomes)
    # Round-robin, not contiguous slices: neighbouring cases in one directory
    # share a shape, so a contiguous chunk would leave one worker holding all
    # of an expensive family.
    chunks = [items[i::jobs] for i in range(jobs)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for payload in pool.map(_worker, chunks):
            census.merge(payload)
            for k, n in payload["outcomes"].items():
                outcomes[k] += n
    return census, dict(outcomes)


def report(payload: dict) -> None:
    static = payload["static_sites"]
    site = payload["census"]["site"]
    reached = {s for s, o in site.items()
               if any(o.get(k) for k in ("caught", "fallback", "chained",
                                         "outside_attempt"))}
    fallback = {s for s, o in site.items() if o.get("fallback")}
    chained = {s for s, o in site.items() if o.get("chained")} - fallback
    caught = reached - fallback - chained
    unreached = set(static) - reached
    print(f"static raise sites (AST-counted, non-test tpyc/thir): {len(static)}")
    print(f"  reached at least once            : {len(reached)}")
    print(f"  (b) fallback-causing             : {len(fallback)}")
    print(f"  (b') reached only as a recomposed inner reject: {len(chained)}")
    print(f"  (a) caught internally only       : {len(caught)}")
    print(f"  (c) never reached by this sweep  : {len(unreached)}")
    both = {s for s in fallback if site[s].get("caught")}
    print(f"  in BOTH (a) and (b) across attempts: {len(both)}")
    stray = reached - set(static)
    if stray:
        print(f"  !! {len(stray)} dynamic sites absent from the static set "
              f"(line-attribution drift): {sorted(stray)[:5]}")
    print("\ncounters:", json.dumps(payload["census"]["counters"], sort_keys=True))
    print("attempts:", json.dumps(payload["census"]["attempts"], sort_keys=True))
    print("sweep outcomes:", json.dumps(payload["outcomes"], sort_keys=True))
    print("\n(c) unreached, by reason NAMESPACE (the wide-guard tags first):")
    by_ns: dict[str, int] = defaultdict(int)
    for s in unreached:
        src = static[s]["reason_src"].strip("'\"f")
        by_ns[src.split(".")[0].split(":")[0][:24] or "<computed>"] += 1
    for ns, n in sorted(by_ns.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {ns}")
    print("\n(c) unreached, grouped by ARM (nearest witness/detail tag):")
    by_arm: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in unreached:
        info = static[s]
        by_arm[(info["file"], info.get("arm", info["func"]))].append(s)
    for (f, arm), sites in sorted(by_arm.items(), key=lambda kv: -len(kv[1])):
        depth = min((static[s].get("depth", -1) for s in sites), default=-1)
        print(f"  {len(sites):4d}  guard>={depth}  {Path(f).name}::{arm}")
    print("\n(c) unreached, by GUARD DEPTH (0-1 = a whole construct kind "
          "falls in; deep = a shape probe):")
    by_depth: dict[int, int] = defaultdict(int)
    for s in unreached:
        by_depth[static[s].get("depth", -1)] += 1
    for d, n in sorted(by_depth.items()):
        print(f"  depth {d:2d}: {n:4d}")
    print("\n(c) unreached at guard depth <= 1 -- the widest untested "
          "rejects:")
    for s in sorted(unreached, key=lambda x: (static[x].get("depth", 0), x)):
        info = static[s]
        if info.get("depth", 99) > 1:
            continue
        print(f"  d{info['depth']}  {s}  {info['func']}  "
              f"{info['reason_src']}")
    print("\n(b) fallback-causing sites, by attempts blocked:")
    for s in sorted(fallback, key=lambda x: -site[x]["fallback"]):
        smp = payload["census"]["samples"].get(s, {})
        info = static.get(s, {})
        print(f"  {site[s]['fallback']:6d}  {s}  {info.get('func', '?')}  "
              f"{info.get('reason_src', '')}")
        for r in smp.get("reason", [])[:2]:
            print(f"            reason: {r}")
        for u in smp.get("unit", [])[:2]:
            print(f"            unit  : {u}")
    print("\n(b') inner deciders of a recomposed fatal reject "
          "(fatal too, via their composer):")
    for s in sorted(chained, key=lambda x: -site[x]["chained"]):
        info = static.get(s, {})
        print(f"  {site[s]['chained']:6d}  {s}  {info.get('func', '?')}  "
              f"{info.get('reason_src', '')}")
    print("\n(a) caught-internally-only sites:")
    for s in sorted(caught, key=lambda x: -site[x].get("caught", 0)):
        info = static.get(s, {})
        print(f"  {site[s].get('caught', 0):6d}  {s}  {info.get('func', '?')}  "
              f"{info.get('reason_src', '')}")
    print("\nnote-only rejects (frame gates -- NOT part of the raise-site key):")
    no = payload["census"]["note_only"]
    for s in sorted(no, key=lambda x: -no[x].get("fallback", 0)):
        print(f"  {no[s].get('fallback', 0):6d}  {s}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--population", default="both",
                    choices=["corpus", "stdlib", "units", "both", "all"])
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None,
                    help="first N items of each population (a SAMPLE: the "
                         "unreached bucket is then an upper bound twice over)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--report", default=None,
                    help="re-print the report from an existing json and exit")
    args = ap.parse_args()

    if args.report:
        report(json.loads(Path(args.report).read_text()))
        return 0

    items: list[str] = []
    if args.population in ("corpus", "both", "all"):
        items += corpus_items(args.limit)
    if args.population in ("stdlib", "both", "all"):
        items += stdlib_items(args.limit)
    if args.population in ("units", "all"):
        items += unit_items(args.limit)

    t0 = time.monotonic()
    census, outcomes = sweep_items(items, args.jobs)
    payload = {
        "elapsed_s": round(time.monotonic() - t0, 1),
        "population": args.population,
        "items_attempted": len(items),
        "sampled": args.limit is not None,
        "static_sites": static_sites(),
        "census": census.to_json(),
        "outcomes": outcomes,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True))
    report(payload)
    print(f"\njson: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
