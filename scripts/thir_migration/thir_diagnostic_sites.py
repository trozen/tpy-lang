"""Inventory of the `raise CodeGenError` population under `tpyc/codegen_cpp/`:
which user-facing diagnostics the cutover takes with it.

WHY this is not the `error_*` case ratchet. The harness ratchet classifies a
diagnostic by walking the traceback of a raise some committed case actually
provoked, so its population is "diagnostics the corpus reaches" -- strictly
smaller than the set that exists, and blind by construction to a diagnostic no
case reaches. This counts raise SITES instead. It is the population-complete
twin: it goes to zero when the four body-emitter modules are deleted, and that,
not the case count, is the definition of done.

CLASSIFICATION IS BY CALLER, NEVER BY FILE. Several diagnostics sit in modules
the cutover keeps but run only under ones it deletes (the ctor member-init
extraction, the simple-generator while condition, the rebind-slot flush), so
the file a raise sits in says nothing about its fate. Each raise's enclosing
function is placed in a call graph over the whole of `tpyc/` and read two ways:

  BODY      no path from a live entry point reaches it without passing through
            code the cutover deletes. The diagnostic dies with the emitters --
            unless it is re-homed, its source becomes an internal crash.
  SKELETON  reached without ever entering the deleted set. Outlives the
            cutover untouched; calling it a blocker manufactures work.
  BOTH      reached both ways. The AST caller dies and the other survives,
            which is exactly what a re-homed diagnostic looks like.

Three things make a function part of the dying layer. The obvious one is living
in a deleted module. The second is being an AST arm: `test_cutover_gate.py`
freezes each skeleton entry into the emitters with a disposition, and a
raise-hosting function whose every such entry is AST_ARM only reaches that
raise when a body did not route. The third is a guard in the CALLER -- the ctor
tail returns early on a lowered constructor, so its extraction helpers never
run for a routed body. No static analysis sees that, so it is read from the
recorded body-diagnostic list the harness ratchet already keys on. All three
are read from their existing homes rather than restated here.

The reported figure is WITNESSED crossed with BODY and deliberate: a deliberate
BODY diagnostic that no committed `diag.txt` contains is one nothing in the
tree can currently see fire.

The deliberate/internal split is a message-SHAPE judgement (an `internal:`
prefix, a `{type(n).__name__}` node dump, an `Expected ...` shape assertion),
not a verdict on reachability. Both buckets are reported; neither is dropped.

KNOWN LIMITS:
 1. The call graph resolves callees by NAME, disambiguated by import bindings
    and by the attribute a collaborator is reached through. A name defined in
    several modules with no such hint over-approximates the callers, which
    biases toward SKELETON -- so BODY is a lower bound, never an upper one.
 2. Calls made through a variable, a callback table or getattr are invisible.
    Functions whose name is taken as a VALUE anywhere in surviving code, and
    dunder methods, are therefore treated as live entry points.
 3. A witness is textual: the message's longest literal run appearing in some
    committed `diag.txt`. A message built entirely from interpolations has no
    static text and is reported as unknown rather than as unwitnessed.
 4. The internal markers are a deliberately narrow list, so a defensive message
    phrased outside them reads as deliberate. That is the safe direction: an
    internal one listed as a risk costs a read, whereas the reverse drops a
    real diagnostic out of the count silently.

Usage (from the repo root):
    uv run python scripts/thir_migration/thir_diagnostic_sites.py \
        [--out sites.json] [--report sites.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(os.environ.get("TPY_REPO") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(REPO))

from tpyc.codegen_cpp import test_cutover_gate as GATE  # noqa: E402

TPYC_DIR = REPO / "tpyc"
CODEGEN_DIR = TPYC_DIR / "codegen_cpp"
CONFTEST = REPO / "tests" / "conftest.py"
DIAG_DIRS = (REPO / "tests" / "cases", REPO / "tests" / "interop")
DEFAULT_OUT = REPO / "__tpyc__" / "thir_diagnostic_sites.json"
RECORDED_BODY_FUNCS = "BODY_DIAGNOSTIC_FUNCTIONS"

BODY = "BODY"
SKELETON = "SKELETON"
BOTH = "BOTH"
UNREACHED = "UNREACHED"

# The four modules the cutover deletes, as tree-relative paths.
DELETED_FILES = frozenset(
    str((CODEGEN_DIR / f"{m}.py").relative_to(REPO))
    for m in GATE.BODY_EMITTER_MODULES)

# A message wearing one of these is a defensive assertion about codegen's own
# invariants, not a sentence written for a user.
_INTERNAL_OPENERS = ("Unsupported", "Unhandled", "Expected ", "unknown ",
                     "Unknown ")
_INTERNAL_PHRASES = ("internal", "has no analyzed type", "has no resolved",
                     "no resolved type", "reached codegen without",
                     "reached the ")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def _sources() -> list[Path]:
    """Production `tpyc/` modules. Tests are excluded: a call from one proves
    the function is testable, not that the shipped compiler reaches it."""
    return [p for p in sorted(TPYC_DIR.rglob("*.py"))
            if "__pycache__" not in p.parts and not p.name.startswith("test_")]


def _owners(tree: ast.AST) -> dict[int, str]:
    """line -> OUTERMOST enclosing def name.

    Outermost rather than innermost on purpose: a closure's calls run when its
    enclosing function runs, so attributing them to the enclosing def is what
    keeps reachability honest for a nested def nothing calls by name."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owner.setdefault(ln, node.name)
    return owner


def _chain(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    if parts:
        return list(reversed(parts))
    return None


def _module_path(dotted: str, level: int, origin: Path,
                 known: set[str]) -> str | None:
    if level:
        base = origin.parent
        for _ in range(level - 1):
            base = base.parent
        parts = dotted.split(".") if dotted else []
        cand = base.joinpath(*parts)
    else:
        if not dotted.startswith("tpyc"):
            return None
        cand = REPO.joinpath(*dotted.split("."))
    for candidate in (cand.with_suffix(".py"), cand / "__init__.py"):
        try:
            rel = _rel(candidate)
        except ValueError:
            continue
        if rel in known:
            return rel
    return None


def _import_bindings(path: Path, tree: ast.AST,
                     known: set[str]) -> dict[str, str]:
    """local name -> module relpath, for modules this file imports.

    This is what keeps `statements`, `match` and `functions` -- each of which
    names a module in BOTH `codegen_cpp` and `thir/lower` -- from collapsing
    into one node."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                rel = _module_path(alias.name, 0, path, known)
                if rel:
                    out[alias.asname or alias.name.split(".")[0]] = rel
        elif isinstance(node, ast.ImportFrom):
            pkg = node.module or ""
            for alias in node.names:
                dotted = f"{pkg}.{alias.name}" if pkg else alias.name
                rel = _module_path(dotted, node.level, path, known)
                if rel:
                    out[alias.asname or alias.name] = rel
    return out


def _collaborator_files(known: set[str]) -> dict[str, str]:
    """attribute name -> module relpath, for the generators the codegen
    objects hand each other (`self.expressions`, `self.statements.match`)."""
    out: dict[str, str] = {}
    for path in sorted(CODEGEN_DIR.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        rel = _rel(path)
        if rel in known:
            out[path.stem] = rel
    return out


class Graph:
    def __init__(self) -> None:
        self.defs: dict[str, set[str]] = defaultdict(set)   # name -> files
        self.callers: dict[tuple[str, str], set[tuple[str, str]]] = \
            defaultdict(set)
        self.callees: dict[tuple[str, str], set[tuple[str, str]]] = \
            defaultdict(set)
        self.nodes: set[tuple[str, str]] = set()
        self.value_refs: set[tuple[str, str]] = set()
        self.frozen_arm_only: set[tuple[str, str]] = set()
        self.recorded_body: set[tuple[str, str]] = set()
        self.raise_hosts: set[tuple[str, str]] = set()


def _ast_arm_only_functions() -> set[tuple[str, str]]:
    """Skeleton functions whose every frozen entry into a deleted module is an
    AST arm -- they reach the emitter only when a body did not route."""
    per_func: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (mod, func, _call), (count, disp) in GATE.FROZEN_SITES.items():
        rel = _rel(CODEGEN_DIR / mod)
        per_func[(rel, func)].update(GATE._dispositions(count, disp))
    return {key for key, disps in per_func.items() if disps == {GATE.AST_ARM}}


def _recorded_body_functions() -> set[tuple[str, str]]:
    """The harness ratchet's list of surviving-module functions whose every
    caller dies, read by parsing rather than importing: this stays a static
    analysis with no pytest or compiler on the import path."""
    tree = ast.parse(CONFTEST.read_text())
    for node in ast.walk(tree):
        target = (node.target if isinstance(node, ast.AnnAssign)
                  else node.targets[0] if isinstance(node, ast.Assign)
                  and len(node.targets) == 1 else None)
        if not (isinstance(target, ast.Name)
                and target.id == RECORDED_BODY_FUNCS and node.value):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        return {(mod.replace(".", "/") + ".py", func)
                for mod, func in ast.literal_eval(value)}
    raise SystemExit(f"{RECORDED_BODY_FUNCS} not found in {_rel(CONFTEST)}")


def build_graph() -> Graph:
    files = _sources()
    known = {_rel(p) for p in files}
    collaborators = _collaborator_files(known)
    graph = Graph()
    trees: dict[str, tuple[Path, ast.AST, dict[int, str]]] = {}

    for path in files:
        rel = _rel(path)
        tree = ast.parse(path.read_text())
        owner = _owners(tree)
        trees[rel] = (path, tree, owner)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                graph.defs[node.name].add(rel)
                graph.nodes.add((rel, node.name))
        graph.nodes.add((rel, "<module>"))

    for rel, (path, tree, owner) in trees.items():
        binds = _import_bindings(path, tree, known)
        call_funcs = {id(n.func) for n in ast.walk(tree)
                      if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                chain = _chain(node.func)
                if chain is None:
                    continue
                src = (rel, owner.get(node.lineno, "<module>"))
                for dst in _resolve(chain, rel, binds, collaborators, graph):
                    if dst != src:
                        graph.callers[dst].add(src)
                        graph.callees[src].add(dst)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                if id(node) in call_funcs or rel in DELETED_FILES:
                    continue
                name = node.id if isinstance(node, ast.Name) else node.attr
                for target in graph.defs.get(name, ()):
                    graph.value_refs.add((target, name))

    graph.frozen_arm_only = _ast_arm_only_functions()
    graph.recorded_body = _recorded_body_functions()
    graph.raise_hosts = {(rel, func) for rel, func, _line in _raise_sites()}
    return graph


def _raise_sites() -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for path in sorted(CODEGEN_DIR.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        rel = _rel(path)
        tree = ast.parse(path.read_text())
        owner = _owners(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Raise)
                    and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id == "CodeGenError"):
                out.append((rel, owner.get(node.lineno, "<module>"),
                            node.lineno))
    return out


def _resolve(chain: list[str], rel: str, binds: dict[str, str],
             collaborators: dict[str, str],
             graph: Graph) -> set[tuple[str, str]]:
    name = chain[-1]
    files = graph.defs.get(name)
    if not files:
        return set()
    if len(chain) >= 2:
        qual = chain[-2]
        hinted = binds.get(qual) or collaborators.get(qual)
        if hinted and hinted in files:
            return {(hinted, name)}
    if rel in files:
        return {(rel, name)}
    return {(f, name) for f in files}


def _closure(seeds: set[tuple[str, str]], graph: Graph,
             blocked: set[tuple[str, str]]) -> set[tuple[str, str]]:
    seen = {s for s in seeds if s not in blocked}
    stack = list(seen)
    while stack:
        cur = stack.pop()
        for nxt in graph.callees.get(cur, ()):
            if nxt in blocked or nxt in seen:
                continue
            seen.add(nxt)
            stack.append(nxt)
    return seen


def dead_functions(graph: Graph) -> set[tuple[str, str]]:
    dead = {n for n in graph.nodes if n[0] in DELETED_FILES}
    dead |= {n for n in graph.recorded_body if n in graph.nodes}
    # An arm-only entry says the CALL dies, not the function -- a frame emitter
    # can hold a leaf-guarded arm and still run for every routed body. It is
    # the whole answer only where the raise itself sits in such a function.
    dead |= {n for n in graph.frozen_arm_only
             if n in graph.raise_hosts and n in graph.nodes}
    return dead


def reachability(graph: Graph) -> tuple[set, set, set]:
    dead = dead_functions(graph)

    roots = {n for n in graph.nodes
             if n not in dead and (
                 n[1] == "<module>"
                 or not graph.callers.get(n)
                 or n in graph.value_refs
                 or (n[1].startswith("__") and n[1].endswith("__")))}
    live = _closure(roots, graph, dead)
    reached_from_dead = _closure(dead, graph, set()) - dead
    return dead, live, reached_from_dead


def _message(call: ast.Call) -> tuple[list[str], list[str], str]:
    """(literal runs, interpolated sources, rendered source)."""
    if not call.args:
        return [], [], ""
    arg = call.args[0]
    src = ast.unparse(arg)
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value], [], src
    if isinstance(arg, ast.JoinedStr):
        lits = [v.value for v in arg.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        interps = [ast.unparse(v.value) for v in arg.values
                   if isinstance(v, ast.FormattedValue)]
        return lits, interps, src
    return [], [], src


def _is_internal(literals: list[str], interps: list[str]) -> bool:
    text = "".join(literals)
    if any(".__name__" in i for i in interps):
        return True
    lowered = text.lower()
    if any(p in lowered for p in _INTERNAL_PHRASES):
        return True
    return text.lstrip().startswith(_INTERNAL_OPENERS)


def _static_text(literals: list[str]) -> str | None:
    """The longest literal run, which is what a `diag.txt` search can key on.
    Short runs are punctuation between interpolations and match everything."""
    best = max((lit.strip() for lit in literals), key=len, default="")
    return best if len(best) >= 12 else None


def _diag_index() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in DIAG_DIRS:
        for path in sorted(root.rglob("expected/diag.txt")):
            case = path.parent.parent
            out[str(case.relative_to(root.parent))] = path.read_text()
    return out


def collect_sites(graph: Graph) -> list[dict]:
    dead, live, from_dead = reachability(graph)
    diags = _diag_index()
    sites: list[dict] = []
    for path in sorted(CODEGEN_DIR.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        rel = _rel(path)
        tree = ast.parse(path.read_text())
        owner = _owners(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Raise)
                    and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id == "CodeGenError"):
                continue
            func = owner.get(node.lineno, "<module>")
            key = (rel, func)
            if key in dead:
                klass = BODY
            else:
                in_live, in_dead = key in live, key in from_dead
                klass = (BOTH if in_live and in_dead
                         else SKELETON if in_live
                         else BODY if in_dead else UNREACHED)
            literals, interps, src = _message(node.exc)
            text = _static_text(literals)
            witnesses = sorted(c for c, body in diags.items()
                               if text and text in body)
            sites.append({
                "site": f"{rel}:{node.lineno}",
                "func": func,
                "class": klass,
                "kind": "internal" if _is_internal(literals, interps)
                        else "deliberate",
                "message": src[:160],
                "witnessed": bool(witnesses) if text else None,
                "witnesses": witnesses,
                "callers": sorted(f"{f}::{n}" for f, n in
                                  graph.callers.get(key, ())),
            })
    return sites


def record_drift(graph: Graph) -> dict[str, list[str]]:
    """Where the recorded body-diagnostic list and the call graph disagree.

    A THIR caller is the drift that matters: it means the diagnostic has been
    re-homed and the entry now claims a fate its function no longer has."""
    missing = [f"{f}::{n}" for f, n in sorted(graph.recorded_body)
               if (f, n) not in graph.nodes]
    thir_callers = sorted(
        f"{f}::{n} <- " + ", ".join(
            f"{cf}::{cn}" for cf, cn in sorted(graph.callers[(f, n)])
            if cf.startswith("tpyc/thir/"))
        for f, n in sorted(graph.recorded_body)
        if any(cf.startswith("tpyc/thir/")
               for cf, _cn in graph.callers.get((f, n), ())))
    unrecorded = [f"{f}::{n}" for f, n in sorted(graph.frozen_arm_only)
                  if n in {h[1] for h in graph.raise_hosts if h[0] == f}
                  and (f, n) not in graph.recorded_body]
    return {"recorded_but_gone": missing,
            "recorded_with_thir_caller": thir_callers,
            "ast_arm_raise_host_not_recorded": unrecorded}


def report(payload: dict) -> None:
    sites = payload["sites"]
    by_class: dict[str, int] = defaultdict(int)
    by_file: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in sites:
        by_class[s["class"]] += 1
        by_file[s["site"].split(":")[0]][s["class"]] += 1
    print(f"raise CodeGenError sites under tpyc/codegen_cpp: {len(sites)}")
    for klass in (BODY, SKELETON, BOTH, UNREACHED):
        if by_class[klass]:
            print(f"  {klass:9s}: {by_class[klass]:3d}")
    dies = by_class[BODY]
    print(f"  -> dies at cutover: {dies}   survives: {len(sites) - dies}")

    print("\nper file (class -> count):")
    for f in sorted(by_file):
        counts = ", ".join(f"{k} {n}" for k, n in sorted(by_file[f].items()))
        print(f"  {Path(f).name:20s} {counts}")

    def bucket(klass: str, kind: str) -> list[dict]:
        return [s for s in sites if s["class"] == klass and s["kind"] == kind]

    for klass in (BODY, BOTH, SKELETON):
        d, i = len(bucket(klass, "deliberate")), len(bucket(klass, "internal"))
        print(f"\n{klass}: {d} deliberate / {i} internal")

    risk = [s for s in bucket(BODY, "deliberate") if not s["witnessed"]]
    print(f"\nBODY + deliberate, NO witnessing case ({len(risk)}) -- each is "
          "a diagnostic nothing in the tree can see fire:")
    for s in risk:
        print(f"  {s['site']:34s} {s['func']}")
        print(f"      {s['message'][:120]}")

    ok = [s for s in bucket(BODY, "deliberate") if s["witnessed"]]
    print(f"\nBODY + deliberate, witnessed ({len(ok)}):")
    for s in ok:
        print(f"  {s['site']:34s} {s['func']}  <- {', '.join(s['witnesses'])}")

    rehomed = bucket(BOTH, "deliberate")
    print(f"\nBOTH + deliberate ({len(rehomed)}) -- the AST caller dies, the "
          "other survives:")
    for s in rehomed:
        mark = "witnessed" if s["witnessed"] else "unwitnessed"
        print(f"  {s['site']:34s} {s['func']}  [{mark}]")

    unknown = [s for s in sites if s["witnessed"] is None]
    if unknown:
        print(f"\nno static text to search for ({len(unknown)}):")
        for s in unknown:
            print(f"  {s['site']:34s} {s['func']}  {s['message'][:60]}")

    drift = payload["record_drift"]
    print("\nrecorded body-diagnostic list vs the call graph:")
    for key, rows in sorted(drift.items()):
        print(f"  {key}: {len(rows)}")
        for row in rows:
            print(f"      {row}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--report", default=None,
                    help="re-print the report from an existing json and exit")
    args = ap.parse_args()

    if args.report:
        report(json.loads(Path(args.report).read_text()))
        return 0

    graph = build_graph()
    payload = {
        "deleted_modules": sorted(DELETED_FILES),
        "ast_arm_only_functions": sorted(f"{f}::{n}" for f, n
                                         in graph.frozen_arm_only),
        "dead_functions": len(dead_functions(graph)),
        "record_drift": record_drift(graph),
        "sites": collect_sites(graph),
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True))
    report(payload)
    print(f"\njson: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
