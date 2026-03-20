"""Pre-scan utilities for TurboPython function bodies.

Pure AST walking -- no type registry or analysis context needed.
Run once in sema; results consumed by both sema and codegen.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dc_fields

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign,
    TpyIf, TpyWhile, TpyForEach, TpyName, TpySubscript,
    TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
    TpyCoerce, TpyFieldAccess, TpyIfExpr, TpyNamedExpr,
    TpyExprStmt, TpyReturn, TpyAssert,
    TpyFStringValue, TpyComprehensionGenerator,
    TpyDictLiteral, TpySetLiteral, TpyTupleLiteral, TpyFString,
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
    # Reassigned vars become T* pointers, not T& refs -- remove from alias map
    for name in result.reassigned:
        result.alias_sources.pop(name, None)
    return result


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
    if isinstance(stmt, TpyIf):
        _scan_walrus_in_expr(stmt.condition, declared, result)
    elif isinstance(stmt, TpyWhile):
        _scan_walrus_in_expr(stmt.condition, declared, result)
    elif isinstance(stmt, TpyExprStmt):
        _scan_walrus_in_expr(stmt.expr, declared, result)
    elif isinstance(stmt, TpyVarDecl):
        _scan_walrus_in_expr(stmt.init, declared, result)
    elif isinstance(stmt, TpyAssign):
        _scan_walrus_in_expr(stmt.value, declared, result)
    elif isinstance(stmt, TpyReturn):
        _scan_walrus_in_expr(stmt.value, declared, result)
    elif isinstance(stmt, TpyAssert):
        _scan_walrus_in_expr(stmt.condition, declared, result)
        _scan_walrus_in_expr(stmt.message, declared, result)
    elif isinstance(stmt, TpyAugAssign):
        _scan_walrus_in_expr(stmt.value, declared, result)


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
        if isinstance(stmt, TpyIf):
            _scan_stmts(stmt.then_body, declared, result)
            _scan_stmts(stmt.else_body, declared, result)
        elif isinstance(stmt, TpyForEach):
            declared.add(stmt.var)
            _scan_stmts(stmt.body, declared, result)
            _scan_stmts(stmt.orelse, declared, result)
        elif isinstance(stmt, TpyWhile):
            _scan_stmts(stmt.body, declared, result)
            _scan_stmts(stmt.orelse, declared, result)
