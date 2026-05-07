"""Compute the set of external symbols a module's generated code references.

Per-module result keyed in `ModuleInfo.reached`. Used by codegen to drive
header inclusion via a transitive closure: when a consumer module reaches
module M, all M's reached external types must also be visible (so e.g.
native `# tpy: include(...)` directives propagate through field chains).

Walks all type-bearing positions in the module's resolved AST and extracts
cross-module `NominalType._module_qname` references. The walk is conservative
on the import-statement axis: a module's reach contains every symbol in
its `user_module_imports`, so this layer never strips imports the user
explicitly wrote. The new precision -- and the bug fix this lands -- is in
the transitive type-graph reach added on top.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..parse.nodes import (
    TpyFunction,
    TpyModule,
    TpyProtocol,
    TpyRecord,
    TpyStmt,
    TpyVarDecl,
)
from ..typesys import (
    NominalType,
    TpyType,
)

if TYPE_CHECKING:
    from .analyzer import SemanticAnalyzer


def compute_reached_symbols(
    module: TpyModule,
    analyzer: "SemanticAnalyzer",
    self_module: str,
) -> set[str]:
    """Return defining modules this module's generated code references.

    Covers only non-builtin user modules other than `self_module`.
    `user_module_imports` are seeded unconditionally so existing import-driven
    inclusion semantics carry over; type-graph reaches add on top of that.
    """
    registry = analyzer.registry
    reached: set[str] = set()

    def add_module(mod: str) -> None:
        if mod == self_module or not mod:
            return
        info = registry.get_module(mod)
        if info is not None and info.is_builtin:
            return
        reached.add(mod)

    def visit_type(t: object) -> None:
        if not isinstance(t, TpyType):
            return
        if isinstance(t, NominalType):
            qname = t._module_qname
            if qname:
                add_module(_module_from_qname(qname, registry))
            if t.type_args:
                for arg in t.type_args:
                    visit_type(arg)
            return
        for inner in t.inner_types():
            visit_type(inner)

    def visit_func_signature(f: TpyFunction) -> None:
        for _, ptype in f.params:
            visit_type(ptype)
        visit_type(f.return_type)
        if f.vararg_type is not None:
            visit_type(f.vararg_type)
        if f.kwarg_type is not None:
            visit_type(f.kwarg_type)
        for bound in f.type_param_bounds.values():
            visit_type(bound)
        if f.self_annotation is not None:
            visit_type(f.self_annotation)

    def visit_record(r: TpyRecord) -> None:
        for fld in r.fields:
            visit_type(fld.type)
        for base in r.bases:
            visit_type(base)
        for bound in r.type_param_bounds.values():
            visit_type(bound)
        for m in r.methods:
            visit_func_signature(m)
            visit_body(m.body)
        for nested in r.nested_records:
            visit_record(nested)

    def visit_protocol(p: TpyProtocol) -> None:
        for sig in p.methods:
            for _, ptype in sig.params:
                visit_type(ptype)
            visit_type(sig.return_type)
        for _, ftype in p.fields:
            visit_type(ftype)
        for parent in p.parent_protocols:
            visit_type(parent)

    def visit_stmt(s: TpyStmt) -> None:
        if isinstance(s, TpyVarDecl):
            visit_type(s.type)
        for child in _iter_typed_children(s):
            visit_type(child)

    def visit_body(stmts: list[TpyStmt] | None) -> None:
        if not stmts:
            return
        for s in stmts:
            visit_stmt(s)

    # Seed: every module the import statements bring in counts as reached,
    # whether or not the type walk also lands on it. Preserves the
    # backward-compatible "import => include" behavior; the type walk below
    # only adds more, never subtracts.
    for mod_name in module.user_module_imports:
        add_module(mod_name)

    # Function re-export reach: when this module imports a function from
    # an intermediate that itself re-exports it from another module,
    # consumer codegen qualifies to the ultimate defining module
    # (`func_info.originating_module`) -- so that module must be in the
    # include set too. Critical for cycle re-exports where the
    # intermediate's `<peer>.hpp` suppresses the `using` line that
    # would otherwise route the call back through it.
    for src_mod, names in (module.imports or {}).items():
        if not isinstance(names, set):
            continue
        src_info = registry.get_module(src_mod)
        if src_info is None:
            continue
        for original_name, _local in names:
            finfos = src_info.functions.get(original_name)
            if not finfos:
                continue
            ult = finfos[0].originating_module
            if ult and ult != src_mod:
                add_module(ult)

    for r in module.all_records():
        visit_record(r)
    for p in module.protocols:
        visit_protocol(p)
    for _name, (typ, _loc) in module.type_aliases.items():
        visit_type(typ)
    for f in module.functions:
        visit_func_signature(f)
        visit_body(f.body)
    visit_body(module.top_level_stmts)

    return reached


def _module_from_qname(qname: str, registry) -> str | None:
    """Extract the defining module from a qualified name. Walks back through
    dots until a registered module is found, so nested names like
    `pkg.sub.Outer.Inner` resolve to `pkg.sub` (the longest matching prefix)."""
    parts = qname.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i])
        if registry.get_module(candidate) is not None:
            return candidate
    # Fallback for types whose enclosing module isn't (yet) in the registry --
    # treat the first dotted component as the module. Builtin modules are
    # filtered downstream by the is_builtin check in add_module.
    if len(parts) >= 2:
        return parts[0]
    return None


def _iter_typed_children(node) -> list[TpyType]:
    """Best-effort walk of TpyType-bearing fields on a statement or expression
    node. Reads dataclass fields whose values are TpyType instances or sequences
    of them; ignores everything else. Recurses into nested TpyStmt/TpyExpr
    children.

    Reach analysis only needs the types it can find; a missed body type is
    safe (it still falls under the function signature or record field path,
    or it lives in a builtin/local-only type).
    """
    out: list[TpyType] = []
    seen: set[int] = set()
    stack: list[object] = [node]
    while stack:
        cur = stack.pop()
        cid = id(cur)
        if cid in seen:
            continue
        seen.add(cid)
        if isinstance(cur, TpyType):
            out.append(cur)
            continue
        d = getattr(cur, "__dict__", None)
        if not d:
            continue
        for v in d.values():
            if v is None or isinstance(v, (str, int, float, bool, bytes)):
                continue
            if isinstance(v, TpyType):
                out.append(v)
            elif isinstance(v, (list, tuple, set, frozenset)):
                for item in v:
                    if isinstance(item, TpyType):
                        out.append(item)
                    elif hasattr(item, "__dict__"):
                        stack.append(item)
            elif hasattr(v, "__dict__"):
                stack.append(v)
    return out
