"""Human-readable `--dump-thir` text format.

A debug rendering of the lowered THIR -- resolved types and facts visible at a
glance. Not consumed by codegen; purely for inspecting the sema/codegen seam.
"""

from __future__ import annotations

from ..typesys import TpyType
from .nodes import (
    Form,
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRExpr,
    THIRFieldAccess,
    THIRForRange,
    THIRFormConvert,
    THIRIf,
    THIRLiteral,
    THIRModule,
    THIRName,
    THIRReturn,
    THIRStmt,
    THIRVarDecl,
    THIRWhile,
)


def _ty(t: TpyType) -> str:
    return getattr(t, "name", None) or str(t)


def _expr(e: THIRExpr) -> str:
    if isinstance(e, THIRName):
        return f"%{e.name}"
    if isinstance(e, THIRLiteral):
        # The form is load-bearing for a None literal (STORAGE -> std::nullopt
        # vs VALUE/BORROW -> nullptr), so surface it like the other form tags.
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return f"lit({e.value!r}){tag}"
    if isinstance(e, THIRBinOp):
        return f"binop({_expr(e.left)}, {e.op}, {_expr(e.right)})"
    if isinstance(e, THIRCall):
        return f"call({e.callee}, [{', '.join(_expr(a) for a in e.args)}])"
    if isinstance(e, THIRCoerce):
        return f"coerce({_expr(e.expr)} -> {_ty(e.result_type)})"
    if isinstance(e, THIRFieldAccess):
        op = "->" if e.is_arrow else "."
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return f"{_expr(e.receiver)}{op}{e.field_cpp}{tag}"
    if isinstance(e, THIRFormConvert):
        cst = "const " if e.is_const else ""
        return f"form_convert[{cst}{e.form.name.lower()}]({_expr(e.value)})"
    return f"<{type(e).__name__}>"


def _stmt_lines(stmt: THIRStmt, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(stmt, THIRVarDecl):
        init = _expr(stmt.init) if stmt.init is not None else "<uninit>"
        return [f"{pad}%{stmt.name}: {_ty(stmt.resolved_type)} = {init}"]
    if isinstance(stmt, THIRAssign):
        # target is a THIRName (`%x`) or, for F2b, a THIRFieldAccess (`recv.field`).
        return [f"{pad}{_expr(stmt.target)} = {_expr(stmt.value)}"]
    if isinstance(stmt, THIRReturn):
        return [f"{pad}return {_expr(stmt.value)}" if stmt.value is not None
                else f"{pad}return"]
    if isinstance(stmt, THIRIf):
        lines = [f"{pad}if {_expr(stmt.condition)}:"]
        for s in stmt.then_body:
            lines.extend(_stmt_lines(s, depth + 1))
        if stmt.else_body:
            lines.append(f"{pad}else:")
            for s in stmt.else_body:
                lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRWhile):
        lines = [f"{pad}while {_expr(stmt.condition)}:"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRForRange):
        start = "0" if stmt.start is None else _expr(stmt.start)
        lines = [f"{pad}for %{stmt.var} in range({start}, {_expr(stmt.stop)}):"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        return lines
    return [f"{pad}<{type(stmt).__name__}>"]


def dump_thir(module: THIRModule) -> str:
    lines: list[str] = []
    for fn in module.functions:
        params = ", ".join(f"{p.name}: {_ty(p.type)}" for p in fn.params)
        lines.append(f"fn {fn.name}({params}) -> {_ty(fn.return_type)}:")
        for s in fn.body:
            lines.extend(_stmt_lines(s, 1))
        lines.append("")
    if not module.functions:
        lines.append("(no THIR-eligible functions)")
    return "\n".join(lines).rstrip("\n") + "\n"
