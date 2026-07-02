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
    THIRCharLiteral,
    THIRCoerce,
    THIRExpr,
    THIRFieldAccess,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRIf,
    THIRExprStmt,
    THIRContainerLiteral,
    THIRLiteral,
    THIRMethodCall,
    THIRModule,
    THIRName,
    THIRPrint,
    THIRReturn,
    THIRSelf,
    THIRStmt,
    THIRStrAppend,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRUnaryNot,
    THIRVarDecl,
    THIRWhile,
)


def _ty(t: TpyType) -> str:
    return getattr(t, "name", None) or str(t)


def _expr(e: THIRExpr) -> str:
    if isinstance(e, THIRName):
        return f"%{e.name}"
    if isinstance(e, THIRSelf):
        return "%self"
    if isinstance(e, THIRLiteral):
        # The form is load-bearing for a None literal (STORAGE -> std::nullopt
        # vs VALUE/BORROW -> nullptr), so surface it like the other form tags.
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return f"lit({e.value!r}){tag}"
    if isinstance(e, THIRStrLiteral):
        return f"str({e.value!r})"
    if isinstance(e, THIRFString):
        # Literal segments render repr'd; interpolated args in braces, with the
        # carried wrap template when one applies (it is emit-relevant).
        parts = ", ".join(
            repr(p) if isinstance(p, str)
            else f"{{{_expr(p.expr)}}}"
            + (f" [wrap {p.wrap!r}]" if p.wrap is not None else "")
            for p in e.parts)
        return f"fstring({parts})"
    if isinstance(e, THIRCharLiteral):
        return f"char({e.value!r})"
    if isinstance(e, THIRBinOp):
        return f"binop({_expr(e.left)}, {e.op}, {_expr(e.right)})"
    if isinstance(e, THIRUnaryNot):
        return f"not({_expr(e.operand)})"
    if isinstance(e, THIRCall):
        # Surface the emit arm: a scalar-ctor cpp_template, a @native
        # free-function symbol, or the bare callee name.
        if e.cpp_template is not None:
            name = f"{e.callee} [template {e.cpp_template!r}]"
        elif e.native_name:
            name = f"{e.callee} [{e.native_name}]"
        else:
            name = e.callee
        return f"call({name}, [{', '.join(_expr(a) for a in e.args)}])"
    if isinstance(e, THIRContainerLiteral):
        if e.values:  # dict: elements are keys
            pairs = ", ".join(f"{_expr(k)}: {_expr(v)}"
                              for k, v in zip(e.elements, e.values))
            return f"container_literal[{_ty(e.result_type)}]({{{pairs}}})"
        elems = ", ".join(_expr(x) for x in e.elements)
        return f"container_literal[{_ty(e.result_type)}]([{elems}])"
    if isinstance(e, THIRMethodCall):
        # Surface the emit arm: the cpp_template body, the @native free-function
        # symbol, or the plain member name.
        if e.cpp_template is not None:
            how = f"template {e.cpp_template!r}"
        elif e.native_function_name is not None:
            how = f"native {e.native_function_name}"
        else:
            how = e.method_cpp
        return (f"method_call({_expr(e.receiver)}, {how}, "
                f"[{', '.join(_expr(a) for a in e.args)}])")
    if isinstance(e, THIRCoerce):
        return f"coerce({_expr(e.expr)} -> {_ty(e.result_type)})"
    if isinstance(e, THIRFieldAccess):
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        if e.deref_check:
            return f"deref_check({_expr(e.receiver)}).{e.field_cpp}{tag}"
        op = "->" if e.is_arrow else "."
        return f"{_expr(e.receiver)}{op}{e.field_cpp}{tag}"
    if isinstance(e, THIRSubscript):
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        # A constant offset (every tuple `std::get<N>`, a literal container index)
        # renders bare (`t[0]`); a dynamic container index renders the expr (`c[i]`).
        idx = e.index.value if isinstance(e.index, THIRLiteral) else _expr(e.index)
        flags = " bounds_safe" if e.bounds_safe else ""
        return f"{_expr(e.receiver)}[{idx}]{tag}{flags}"
    if isinstance(e, THIRStrSlice):
        lo = _expr(e.lower) if e.lower is not None else ""
        hi = _expr(e.upper) if e.upper is not None else ""
        return f"{_expr(e.receiver)}[{lo}:{hi}]"
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
    if isinstance(stmt, THIRStrAppend):
        return [f"{pad}%{stmt.target} += {_expr(stmt.value)}"]
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
    if isinstance(stmt, THIRForEach):
        # `[const]` marks a `const auto&` record loop var (vs `auto&&`); load-bearing
        # for record elements, so surface it like the other emit-relevant tags.
        const = " [const]" if stmt.const_loop_var else ""
        lines = [f"{pad}for %{stmt.var}{const} in {_expr(stmt.iterable)}:"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRPrint):
        args = ", ".join(f"{_expr(a.expr)} [{a.print_form.name.lower()}]" for a in stmt.args)
        return [f"{pad}print({args})"]
    if isinstance(stmt, THIRExprStmt):
        return [f"{pad}{_expr(stmt.expr)}"]
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
