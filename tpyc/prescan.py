"""Pre-scan utilities for TurboPython function bodies.

Pure AST walking -- no type registry or analysis context needed.
Run once in sema; results consumed by both sema and codegen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyAssign, TpyAugAssign,
    TpyIf, TpyWhile, TpyForEach, TpyName, TpySubscript,
    TpyCall, TpyBinOp, TpyUnaryOp, TpyMethodCall,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyArrayLiteral, TpyListRepeat,
    TpyCoerce, TpyFieldAccess,
)


@dataclass
class ScanResult:
    """Result of pre-scanning a function body for reassigned variables."""
    reassigned: set[str] = field(default_factory=set)
    rvalue_reassigned: set[str] = field(default_factory=set)
    lvalue_reassigned: set[str] = field(default_factory=set)
    aug_assigned: set[str] = field(default_factory=set)


def scan_reassigned_vars(stmts: list[TpyStmt]) -> ScanResult:
    """Pre-scan a function body to find variables that are reassigned after first declaration.

    Returns a ScanResult with:
    - reassigned: variables with a second TpyVarDecl or TpyAssign after first declaration
    - rvalue_reassigned: subset of reassigned with at least one rvalue reassignment
    - lvalue_reassigned: subset of reassigned with at least one lvalue reassignment
    - aug_assigned: variables targeted by augmented assignment (+=, -=, etc.)
    """
    declared: set[str] = set()
    result = ScanResult()
    _scan_stmts(stmts, declared, result)
    return result


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
    return isinstance(expr, (TpyCall, TpyBinOp, TpyUnaryOp, TpyMethodCall,
                             TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyArrayLiteral, TpyListRepeat))


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
        elif isinstance(stmt, TpyAssign):
            if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                result.reassigned.add(stmt.target.name)
                if is_scan_rvalue(stmt.value):
                    result.rvalue_reassigned.add(stmt.target.name)
                else:
                    result.lvalue_reassigned.add(stmt.target.name)
        elif isinstance(stmt, TpyAugAssign):
            if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                result.aug_assigned.add(stmt.target.name)
        if isinstance(stmt, TpyIf):
            _scan_stmts(stmt.then_body, declared, result)
            _scan_stmts(stmt.else_body, declared, result)
        elif isinstance(stmt, (TpyWhile, TpyForEach)):
            _scan_stmts(stmt.body, declared, result)
