"""Pre-scan utilities for TurboPython function bodies.

Pure AST walking -- no type registry or analysis context needed.
Run once in sema; results consumed by both sema and codegen.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dc_fields

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign,
    TpyForEach, TpyWith, TpyName, TpySubscript,
    TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
    TpyCoerce, TpyFieldAccess, TpyIfExpr, TpyNamedExpr,
    TpyNestedDef,
    TpyFStringValue, TpyComprehensionGenerator,
    TpyDictLiteral, TpySetLiteral, TpyTupleLiteral, TpyFString,
    TpyDelVar, TpyDelAttr, TpyDelItem, TpyNonlocal, TpyGlobal,
)


@dataclass
class ScanResult:
    """Result of pre-scanning a function body for reassigned variables."""
    reassigned: set[str] = field(default_factory=set)
    rvalue_reassigned: set[str] = field(default_factory=set)
    lvalue_reassigned: set[str] = field(default_factory=set)
    aug_assigned: set[str] = field(default_factory=set)
    # alias_name -> source_name for lvalue-initialized, non-reassigned variables
    # with simple TpyName init (T& reference candidates).
    alias_sources: dict[str, str] = field(default_factory=dict)
    # alias_name -> root_name for field/subscript-chain inits (a = o.inner,
    # n = xs[0]): the binding references INTO the root's storage, so moving
    # the root while the alias is live dangles it. Kept separate from
    # alias_sources because that map also feeds del codegen and flow-fact
    # kill groups, which expect whole-object name-to-name aliases only;
    # this map is merged in solely for last-use liveness suppression.
    chain_alias_sources: dict[str, str] = field(default_factory=dict)
    # Variables initially aliased from another name (before reassignment cleanup).
    # Used by del codegen to avoid destroying through a pointer that may
    # still point at the source variable's storage.
    initial_alias_names: set[str] = field(default_factory=set)


def scan_reassigned_vars(stmts: list[TpyStmt],
                         pre_declared: set[str] | None = None) -> ScanResult:
    """Pre-scan a function body to find variables that are reassigned after first declaration.

    Args:
        stmts: The statements to scan.
        pre_declared: Names already in scope (e.g. function parameters).
            Assignment to these names counts as reassignment.

    Returns a ScanResult with:
    - reassigned: variables with a second TpyVarDecl or TpyAssign after first declaration
    - rvalue_reassigned: subset of reassigned with at least one rvalue reassignment
    - lvalue_reassigned: subset of reassigned with at least one lvalue reassignment
    - aug_assigned: variables targeted by augmented assignment (+=, -=, etc.)
    """
    declared: set[str] = set(pre_declared) if pre_declared else set()
    result = ScanResult()
    _scan_stmts(stmts, declared, result)
    result.initial_alias_names = set(result.alias_sources.keys())
    # Reassigned vars become T* pointers, not T& refs -- remove from alias map
    for name in result.reassigned:
        result.alias_sources.pop(name, None)
        result.chain_alias_sources.pop(name, None)
    return result


def liveness_alias_sources(result: ScanResult) -> dict[str, str]:
    """Alias map for last-use liveness: name-to-name aliases plus
    field/subscript-chain root aliases (both reference the source's storage,
    so both must suppress auto-move of the source while live).
    """
    if not result.chain_alias_sources:
        return result.alias_sources
    return {**result.chain_alias_sources, **result.alias_sources}


def _expr_to_narrowing_key(expr: TpyExpr) -> str | None:
    """Convert an expression to a narrowing key string.

    Returns a simple name for TpyName, or a dotted path for multi-level
    TpyFieldAccess (e.g. "obj.field", "obj.inner.field").
    Returns None for unsupported expressions.
    """
    if isinstance(expr, TpyName):
        return expr.name
    if isinstance(expr, TpyNamedExpr):
        return expr.target
    if isinstance(expr, TpyFieldAccess):
        obj_key = _expr_to_narrowing_key(expr.obj)
        if obj_key is not None:
            return f"{obj_key}.{expr.field}"
    return None


# A deref-view narrowing rides `narrowed_types` under a distinct key: it
# narrows the polymorphic payload reached through an owning wrapper's
# reference-returning __deref__ (`if isinstance(rc, Dog): rc.bark()` resolves
# `bark` against Dog through the deref, while `rc.clone()` stays an Rc method).
# Keying it apart from the wrapper variable's own narrowing key leaves the
# wrapper type untouched and lets the existing save/restore/merge-at-joins
# machinery for narrowed_types apply unchanged -- exactly like field-path
# narrowing keys. The NUL suffix cannot collide with any source-level dotted
# path produced by _expr_to_narrowing_key.
#
# INVARIANT: a deref-view key is NOT a `name.`-dotted field path, so the
# field-path prefix sweeps (NarrowingTracker._invalidate_field_facts and
# friends) do NOT cover it. Any code that invalidates narrowed_types on a
# write/mutation of `name` must also drop deref_view_key(name) explicitly (see
# update_after_write); a prefix-scan alone silently leaves the deref-view fact
# stale -- the bug class that null-derefs a reassigned wrapper.
_DEREF_VIEW_SUFFIX = "\x00deref"


def deref_view_key(narrowing_key: str) -> str:
    """Map a receiver narrowing key to its deref-view narrowing key."""
    return narrowing_key + _DEREF_VIEW_SUFFIX


def parse_deref_view_key(key: str) -> 'str | None':
    """If `key` is a deref-view narrowing key, return the receiver key it wraps;
    otherwise None."""
    if key.endswith(_DEREF_VIEW_SUFFIX):
        return key[:-len(_DEREF_VIEW_SUFFIX)]
    return None


def match_is_none(expr: TpyExpr) -> tuple[str, bool] | None:
    """Match `v is None`, `v is not None`, `None is v`, `None is not v`.

    Also matches dotted field access at any depth: `obj.field is None`,
    `obj.a.b is None`, etc.
    Returns (key, is_not_none) or None if the pattern doesn't match.
    The key is a simple name or a dotted path ("obj.field", "obj.a.b").
    """
    if not isinstance(expr, TpyBinOp) or expr.op not in ("is", "is not"):
        return None
    key: str | None = None
    if isinstance(expr.right, TpyNoneLiteral):
        key = _expr_to_narrowing_key(expr.left)
    elif isinstance(expr.left, TpyNoneLiteral):
        key = _expr_to_narrowing_key(expr.right)
    if key is None:
        return None
    return key, expr.op == "is not"


def is_scan_rvalue(expr: TpyExpr | None) -> bool:
    """Conservative rvalue check for pre-scan (no type registry needed)."""
    if expr is None:
        return False
    if isinstance(expr, TpyCoerce):
        return is_scan_rvalue(expr.expr)
    if isinstance(expr, (TpyName, TpySubscript)):
        return False
    if isinstance(expr, TpyFieldAccess):
        return is_scan_rvalue(expr.obj)
    if isinstance(expr, TpyNamedExpr):
        return is_scan_rvalue(expr.value)
    return isinstance(expr, (TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
                             TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
                             TpyDictLiteral, TpySetLiteral, TpyTupleLiteral, TpyFString,
                             TpyIfExpr))


def _scan_walrus_in_expr(expr: TpyExpr | None, declared: set[str],
                         result: ScanResult) -> None:
    """Walk an expression tree to find walrus operator bindings.

    Uses generic dataclass field introspection so new expression types
    are handled automatically without manual enumeration.
    """
    if expr is None:
        return
    if isinstance(expr, TpyNamedExpr):
        if expr.target in declared:
            result.reassigned.add(expr.target)
            result.rvalue_reassigned.add(expr.target)
        else:
            declared.add(expr.target)
    for f in dc_fields(expr):
        val = getattr(expr, f.name)
        if isinstance(val, TpyExpr):
            _scan_walrus_in_expr(val, declared, result)
        elif isinstance(val, TpyComprehensionGenerator):
            _scan_walrus_in_expr(val.iterable, declared, result)
            for cond in val.conditions:
                _scan_walrus_in_expr(cond, declared, result)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, TpyExpr):
                    _scan_walrus_in_expr(item, declared, result)
                elif isinstance(item, TpyFStringValue):
                    _scan_walrus_in_expr(item.expr, declared, result)
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, TpyExpr):
                    _scan_walrus_in_expr(v, declared, result)


def _scan_walrus_in_stmt(stmt: TpyStmt, declared: set[str],
                          result: ScanResult) -> None:
    """Scan expressions within a statement for walrus bindings."""
    for expr in stmt.exprs():
        _scan_walrus_in_expr(expr, declared, result)


def _scan_stmts(stmts: list[TpyStmt], declared: set[str],
                result: ScanResult) -> None:
    for stmt in stmts:
        # Scan for walrus bindings inside expressions before normal stmt scanning
        _scan_walrus_in_stmt(stmt, declared, result)
        if isinstance(stmt, TpyVarDecl):
            if stmt.name in declared:
                result.reassigned.add(stmt.name)
                if is_scan_rvalue(stmt.init):
                    result.rvalue_reassigned.add(stmt.name)
                else:
                    result.lvalue_reassigned.add(stmt.name)
            else:
                declared.add(stmt.name)
                if (stmt.init is not None
                        and isinstance(stmt.init, TpyName)
                        and stmt.init.name != stmt.name):
                    result.alias_sources[stmt.name] = stmt.init.name
                elif stmt.init is not None:
                    root = chain_root_name(stmt.init)
                    if root is not None and root != stmt.name:
                        result.chain_alias_sources[stmt.name] = root
        elif isinstance(stmt, TpyAssign):
            if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                result.reassigned.add(stmt.target.name)
                if is_scan_rvalue(stmt.value):
                    result.rvalue_reassigned.add(stmt.target.name)
                else:
                    result.lvalue_reassigned.add(stmt.target.name)
        elif isinstance(stmt, TpyTupleUnpack):
            for name in stmt.targets:
                if name is None:
                    continue
                if name in declared:
                    result.reassigned.add(name)
                    # std::get<i>(tmp) is always an rvalue
                    result.rvalue_reassigned.add(name)
                else:
                    declared.add(name)
        elif isinstance(stmt, TpyAugAssign):
            if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                result.aug_assigned.add(stmt.target.name)
        elif isinstance(stmt, TpyNestedDef):
            name = stmt.func.name
            if name in declared:
                result.reassigned.add(name)
                result.rvalue_reassigned.add(name)
            else:
                declared.add(name)
            # Do NOT recurse into nested body (separate scope)
        # Recurse into sub-bodies (if/while/for/with)
        if isinstance(stmt, TpyForEach):
            declared.add(stmt.var)
        if isinstance(stmt, TpyWith):
            for item in stmt.items:
                if item.target is not None:
                    if item.target in declared:
                        result.reassigned.add(item.target)
                        result.rvalue_reassigned.add(item.target)
                    else:
                        declared.add(item.target)
        for body in stmt.sub_bodies():
            _scan_stmts(body, declared, result)


@dataclass
class FactKills:
    """Conservative syntactic over-approximation of the flow facts a body
    may invalidate (narrowing, ptr non-null, value ranges).

    Applied at control-flow meets where single-pass analysis would otherwise
    resurrect facts the body kills: loop body entry (back-edge), except
    handler entry (mid-try exception), finally entry (any-path exception).
    """
    # Names that may be rebound: kills the name's own facts, its deref-view
    # fact, all field facts rooted at it, and its value range.
    names: set[str] = field(default_factory=set)
    # Dotted field paths written directly (obj.field = ...): kills the path's
    # facts and all deeper paths, but not the root name's own facts.
    paths: set[str] = field(default_factory=set)
    # Names/paths whose *contents* may be mutated through a call (method
    # receivers, reference-type call args): kills field facts under the key
    # and len-derived ranges, but not the key's own narrowing.
    receivers: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.names or self.paths or self.receivers)

    def update(self, other: 'FactKills') -> None:
        self.names |= other.names
        self.paths |= other.paths
        self.receivers |= other.receivers


def _kills_in_expr(expr: TpyExpr | None, kills: FactKills) -> None:
    if expr is None:
        return
    if isinstance(expr, TpyNamedExpr):
        kills.names.add(expr.target)
    elif isinstance(expr, TpyMethodCall):
        key = _expr_to_narrowing_key(expr.obj)
        if key is not None:
            kills.receivers.add(key)
    if isinstance(expr, (TpyCall, TpyMethodCall)):
        for arg in expr.args:
            if isinstance(arg, TpyName):
                kills.receivers.add(arg.name)
        for kw_val in getattr(expr, "kwargs", {}).values():
            if isinstance(kw_val, TpyName):
                kills.receivers.add(kw_val.name)
    for f in dc_fields(expr):
        val = getattr(expr, f.name)
        if isinstance(val, TpyExpr):
            _kills_in_expr(val, kills)
        elif isinstance(val, TpyComprehensionGenerator):
            _kills_in_expr(val.iterable, kills)
            for cond in val.conditions:
                _kills_in_expr(cond, kills)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, TpyExpr):
                    _kills_in_expr(item, kills)
                elif isinstance(item, TpyFStringValue):
                    _kills_in_expr(item.expr, kills)
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, TpyExpr):
                    _kills_in_expr(v, kills)


def _kills_assign_target(target: TpyExpr, kills: FactKills) -> None:
    if isinstance(target, TpyName):
        kills.names.add(target.name)
    elif isinstance(target, TpyFieldAccess):
        key = _expr_to_narrowing_key(target)
        if key is not None:
            kills.paths.add(key)
        else:
            root = _expr_root_name(target)
            if root is not None:
                kills.receivers.add(root)
    elif isinstance(target, TpySubscript):
        root = _expr_root_name(target.obj)
        if root is not None:
            kills.receivers.add(root)


def _expr_root_name(expr: TpyExpr) -> str | None:
    while isinstance(expr, TpyFieldAccess):
        expr = expr.obj
    if isinstance(expr, TpyName):
        return expr.name
    return None


def chain_root_name(expr: TpyExpr) -> str | None:
    """Root TpyName of a pure field/subscript chain (o.inner, xs[0],
    o.items[0].inner -> the root name), or None when the chain bottoms out
    in anything else (call results are handled by the borrow tracker, not
    the prescan alias map).
    """
    while isinstance(expr, (TpyFieldAccess, TpySubscript)):
        expr = expr.obj
    return expr.name if isinstance(expr, TpyName) else None


def alias_group(aliases: dict[str, str], name: str) -> set[str]:
    """All names statically known to alias ``name`` (from the prescan
    alias map, alias -> source), including ``name`` itself.

    A mutation through any member reaches every member -- they are the
    same object -- so fact invalidation must cover the whole group.
    Rebinding a member does NOT affect the others (use only for
    mutation-driven kills, never for rebinds).
    """
    group = {name}
    cur = name
    while cur in aliases and aliases[cur] not in group:
        cur = aliases[cur]
        group.add(cur)
    changed = True
    while changed:
        changed = False
        for a, s in aliases.items():
            if s in group and a not in group:
                group.add(a)
                changed = True
    return group


def _collect_nested_def_writes(stmts: list[TpyStmt], kills: FactKills) -> None:
    """Names a nested def may rebind in the enclosing scope (nonlocal/global)."""
    for stmt in stmts:
        if isinstance(stmt, (TpyNonlocal, TpyGlobal)):
            kills.names.update(stmt.names)
        if isinstance(stmt, TpyNestedDef):
            _collect_nested_def_writes(stmt.func.body, kills)
        for body in stmt.sub_bodies():
            _collect_nested_def_writes(body, kills)


def collect_fact_kills(stmts: list[TpyStmt],
                       extra_exprs: 'tuple[TpyExpr, ...] | list[TpyExpr]' = ()
                       ) -> FactKills:
    """Collect the fact kill-set of a statement body (recursive).

    ``extra_exprs`` covers re-evaluated expressions that are part of the
    same control-flow cycle but not of the body (a while-loop condition).
    """
    kills = FactKills()
    _collect_fact_kills(stmts, kills)
    for e in extra_exprs:
        _kills_in_expr(e, kills)
    return kills


def _collect_fact_kills(stmts: list[TpyStmt], kills: FactKills) -> None:
    for stmt in stmts:
        if isinstance(stmt, TpyVarDecl):
            kills.names.add(stmt.name)
        elif isinstance(stmt, (TpyAssign, TpyAugAssign)):
            _kills_assign_target(stmt.target, kills)
        elif isinstance(stmt, TpyTupleUnpack):
            for name in stmt.targets:
                if name is not None:
                    kills.names.add(name)
        elif isinstance(stmt, TpyForEach):
            kills.names.add(stmt.var)
        elif isinstance(stmt, TpyWith):
            for item in stmt.items:
                if item.target is not None:
                    kills.names.add(item.target)
        elif isinstance(stmt, TpyDelVar):
            kills.names.update(stmt.names)
        elif isinstance(stmt, TpyDelAttr):
            for t in stmt.targets:
                key = _expr_to_narrowing_key(t)
                if key is not None:
                    kills.paths.add(key)
        elif isinstance(stmt, TpyDelItem):
            for t in stmt.targets:
                root = _expr_root_name(t.obj)
                if root is not None:
                    kills.receivers.add(root)
        elif isinstance(stmt, TpyGlobal):
            kills.names.update(stmt.names)
        elif isinstance(stmt, TpyNestedDef):
            # Calls anywhere in the cycle may invoke the closure; treat its
            # nonlocal/global targets as killable. Body otherwise not scanned
            # (separate scope).
            _collect_nested_def_writes(stmt.func.body, kills)
            continue
        for expr in stmt.exprs():
            _kills_in_expr(expr, kills)
        for body in stmt.sub_bodies():
            _collect_fact_kills(body, kills)
