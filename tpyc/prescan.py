"""Pre-scan utilities for TurboPython function bodies.

Pure AST walking -- no type registry or analysis context needed.
Run once in sema; results consumed by both sema and codegen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign,
    TpyIf, TpyWhile, TpyForEach, TpyName, TpySubscript,
    TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
    TpyCoerce, TpyFieldAccess, TpyIfExpr,
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

    Returns a simple name for TpyName, or a dotted path for single-level
    TpyFieldAccess (e.g. "obj.field"). Returns None for unsupported expressions.
    """
    if isinstance(expr, TpyName):
        return expr.name
    if isinstance(expr, TpyFieldAccess) and isinstance(expr.obj, TpyName):
        return f"{expr.obj.name}.{expr.field}"
    return None


def match_is_none(expr: TpyExpr) -> tuple[str, bool] | None:
    """Match `v is None`, `v is not None`, `None is v`, `None is not v`.

    Also matches single-level field access: `obj.field is None` etc.
    Returns (key, is_not_none) or None if the pattern doesn't match.
    The key is a simple name or a dotted path ("obj.field").
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
    return isinstance(expr, (TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
                             TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat,
                             TpyIfExpr))


def _scan_stmts(stmts: list[TpyStmt], declared: set[str],
                result: ScanResult) -> None:
    for stmt in stmts:
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
