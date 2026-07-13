"""Human-readable `--dump-thir` text format.

A debug rendering of the lowered THIR -- resolved types and facts visible at a
glance. Not consumed by codegen; purely for inspecting the sema/codegen seam.
"""

from __future__ import annotations

from ..typesys import TpyType
from .nodes import (
    Form,
    TruthinessMode,
    THIRArgTemp,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRClassConstant,
    THIRCoerce,
    THIRCtorCall,
    THIREnumMember,
    THIREnumWrap,
    THIRExpr,
    THIRFieldAccess,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRIf,
    THIRIfExpr,
    THIRIsNone,
    THIRIsinstance,
    THIRExprStmt,
    THIRContainerLiteral,
    THIRContinue,
    THIRDelVar,
    THIRLiteral,
    THIRMatch,
    THIRMethodCall,
    THIRModule,
    THIRModuleVar,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRNarrowedRead,
    THIROptionalPtrArg,
    THIRPrint,
    THIRReturn,
    THIRSelf,
    THIRStmt,
    THIRStrAppend,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRTupleUnpack,
    THIRTruthy,
    THIROptViewArg,
    THIRUnaryNot,
    THIRUnionArgLift,
    THIRVarDecl,
    THIRWhile,
    THIRWith,
)


def _ty(t: TpyType) -> str:
    return getattr(t, "name", None) or str(t)


def _expr(e: THIRExpr) -> str:
    if isinstance(e, THIRName):
        # A pointer-local read in a value position renders `(*name)`.
        return f"*%{e.name}" if e.deref else f"%{e.name}"
    if isinstance(e, THIRSelf):
        return "%self"
    if isinstance(e, THIRLiteral):
        # The form is load-bearing for a None literal (STORAGE -> std::nullopt
        # vs VALUE/BORROW -> nullptr), so surface it like the other form tags.
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return f"lit({e.value!r}){tag}"
    if isinstance(e, THIRStrLiteral):
        return f"str({e.value!r})"
    if isinstance(e, THIRBytesLiteral):
        # The owned/span render verdict (the form tag) is emit-relevant.
        return f"bytes({e.value!r})" + ("" if e.form is Form.STORAGE else " [span]")
    if isinstance(e, THIRFString):
        # Literal segments render repr'd; interpolated args in braces, with
        # the carried wrap template and format spec when they apply (both are
        # emit-relevant).
        parts = ", ".join(
            repr(p) if isinstance(p, str)
            else f"{{{_expr(p.expr)}}}"
            + (f" [wrap {p.wrap!r}]" if p.wrap is not None else "")
            + (f" [spec {p.format_spec!r}]" if p.format_spec is not None else "")
            for p in e.parts)
        return f"fstring({parts})"
    if isinstance(e, THIRCharLiteral):
        return f"char({e.value!r})"
    if isinstance(e, THIRBinOp):
        return f"binop({_expr(e.left)}, {e.op}, {_expr(e.right)})"
    if isinstance(e, THIRUnaryNot):
        return f"not({_expr(e.operand)})"
    if isinstance(e, THIRIsNone):
        return (f"is_none({_expr(e.operand)}{', negate' if e.negate else ''}"
                f"{', value_repr' if e.value_repr else ''})")
    if isinstance(e, THIRTruthy):
        if e.mode is TruthinessMode.ALWAYS_TRUE:
            return "truthy[always_true]()"
        assert e.operand is not None
        deref = "*" if e.deref else ""
        return f"truthy[{e.mode.name.lower()}]({deref}{_expr(e.operand)})"
    if isinstance(e, THIROptViewArg):
        return f"opt_view_arg({e.name})"
    if isinstance(e, THIRIfExpr):
        # The form tag is emit-relevant for a str-family result (the
        # owned-sink copy fires on BORROW), so surface it.
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return (f"ifexpr({_expr(e.cond)} ? {_expr(e.then)}"
                f" : {_expr(e.orelse)}){tag}")
    if isinstance(e, THIRCall):
        # Surface the emit arm: a scalar-ctor cpp_template, a @native
        # free-function symbol, or the bare callee name.
        if e.cpp_template is not None:
            name = f"{e.callee} [template {e.cpp_template!r}]"
        elif e.native_name:
            name = f"{e.callee} [{e.native_name}]"
        else:
            name = e.callee
        if e.template_args_cpp:
            name += f"<{', '.join(e.template_args_cpp)}>"
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
        elif e.deref_check:
            # An unproven Optional-ptr receiver's runtime-checked call.
            how = f"deref_check.{e.method_cpp}"
        else:
            # `->member` surfaces a pointer-local receiver's arrow access.
            how = f"->{e.method_cpp}" if e.is_arrow else e.method_cpp
        return (f"method_call({_expr(e.receiver)}, {how}, "
                f"[{', '.join(_expr(a) for a in e.args)}])")
    if isinstance(e, THIRCtorCall):
        return f"ctor({e.type_cpp}, [{', '.join(_expr(a) for a in e.args)}])"
    if isinstance(e, THIRUnionArgLift):
        # The three inline arms: monostate (None), the const conversion of an
        # already-union name, and the address-of member lift (deref for a
        # pointer-local / self receiver).
        if e.value is None:
            inner = "monostate"
        elif e.const_wrap:
            inner = f"to_const({_expr(e.value)})"
        else:
            inner = f"&({'*' if e.deref else ''}{_expr(e.value)})"
        return f"union_lift[{e.variant_cpp}]{{{inner}}}"
    if isinstance(e, THIRArgTemp):
        # Number-free by design: the real __tmp_N is drawn at emission from
        # the module-cumulative sink.
        mods = (" move" if e.move else "") + (" addr" if e.addr_of else "")
        return f"%argtmp({e.cpp_type or 'auto'}{mods}){{{_expr(e.init)}}}"
    if isinstance(e, THIRMove):
        return f"move({_expr(e.value)})"
    if isinstance(e, THIROptionalPtrArg):
        if e.value is None:
            inner = "nullptr"
        elif e.lift:
            inner = f"optional_to_ptr({_expr(e.value)})"
        else:
            inner = f"&({_expr(e.value)})"
        return f"optptr{{{inner}}}"
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
        if e.index is not None:
            return f"{_expr(e.receiver)}[{_expr(e.index)}]"
        lo = _expr(e.lower) if e.lower is not None else ""
        hi = _expr(e.upper) if e.upper is not None else ""
        if e.stepped:
            step = _expr(e.step) if e.step is not None else ""
            return f"{_expr(e.receiver)}[{lo}:{hi}:{step}]"
        return f"{_expr(e.receiver)}[{lo}:{hi}]"
    if isinstance(e, THIRFormConvert):
        cst = "const " if e.is_const else ""
        return f"form_convert[{cst}{e.form.name.lower()}]({_expr(e.value)})"
    if isinstance(e, THIRIsinstance):
        return f"isinstance(%{e.variant_cpp}, [{', '.join(e.member_cpps)}])"
    if isinstance(e, THIRNarrowedRead):
        deref = "*" if e.is_ptr_variant else ""
        return f"({deref}get<{e.member_cpp}>(%{e.variant_cpp}))"
    if isinstance(e, THIREnumMember):
        return f"enum_member({e.cpp})"
    if isinstance(e, THIRClassConstant):
        return f"class_const({e.cpp})"
    if isinstance(e, THIRModuleVar):
        return f"module_var({e.cpp})"
    if isinstance(e, THIREnumWrap):
        # The wrap template is the emit; a dropped operand (plain-enum
        # truthiness) surfaces as an empty operand slot.
        inner = "" if e.operand is None else f", {_expr(e.operand)}"
        return f"enum_wrap({e.wrap!r}{inner})"
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
    if isinstance(stmt, THIRNarrowAlias):
        deref = "*" if stmt.is_ptr_variant else ""
        cst = "const " if stmt.const_ref else ""
        return [f"{pad}%{stmt.alias} = {cst}&{deref}get<{stmt.member_cpp}>"
                f"(%{stmt.variant_cpp})"]
    if isinstance(stmt, THIRAssert):
        msg = ""
        if isinstance(stmt.message, str):
            msg = f", {stmt.message!r}"
        elif stmt.message is not None:
            msg = f", {_expr(stmt.message)}"
        return [f"{pad}assert {_expr(stmt.condition)}{msg}"]
    if isinstance(stmt, THIRReturn):
        return [f"{pad}return {_expr(stmt.value)}" if stmt.value is not None
                else f"{pad}return"]
    if isinstance(stmt, THIRIf):
        lines = [f"{pad}if {_expr(stmt.condition)}:"]
        for s in stmt.then_body:
            lines.extend(_stmt_lines(s, depth + 1))
        if stmt.else_body:
            # `[nested]` marks the broken elif chain (a concrete-extraction
            # else emits `} else {` + a nested if, not a flat `else if`).
            lines.append(f"{pad}else:" + (" [nested]" if stmt.else_is_nested else ""))
            for s in stmt.else_body:
                lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRWhile):
        lines = [f"{pad}while {_expr(stmt.condition)}:"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        _extend_orelse(lines, stmt.orelse, depth)
        return lines
    if isinstance(stmt, THIRForRange):
        start = "0" if stmt.start is None else _expr(stmt.start)
        step = f", {_expr(stmt.step)}" if stmt.step is not None else ""
        lines = [f"{pad}for %{stmt.var} in range({start}, {_expr(stmt.stop)}{step}):"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        _extend_orelse(lines, stmt.orelse, depth)
        return lines
    if isinstance(stmt, THIRForEach):
        # `[const]` marks a `const auto&` record loop var (vs `auto&&`); load-bearing
        # for record elements, so surface it like the other emit-relevant tags.
        # `[rvalue]` marks an owning `auto __obj_N` capture (vs the `auto&` alias).
        const = " [const]" if stmt.const_loop_var else ""
        rval = "" if stmt.iterable_lvalue else " [rvalue]"
        lines = [f"{pad}for %{stmt.var}{const}{rval} in {_expr(stmt.iterable)}:"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        _extend_orelse(lines, stmt.orelse, depth)
        return lines
    if isinstance(stmt, THIRWith):
        # Emit-relevant item facts surface as tags: the manager binding
        # (borrowed/owned, deref), the target arm, and the __exit__ shape
        # (suppress / exc_val pick the catch arms).
        its = []
        for it in stmt.items:
            mgr = f"{'*' if it.deref_manager else ''}{_expr(it.ctx_expr)}"
            mgr += " [borrowed]" if it.manager_borrowed else " [owned]"
            if it.target is not None:
                mgr += f" as %{it.target} [{it.target_arm.name.lower()}]"
            if it.can_suppress:
                mgr += " [suppress]"
            if it.takes_exc_val:
                mgr += " [exc_val]"
            its.append(mgr)
        term = " [terminates]" if stmt.body_terminates else ""
        lines = [f"{pad}with {', '.join(its)}:{term}"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRMatch):
        tags = f" [{stmt.strategy}]"
        if stmt.emit_unreachable:
            tags += " [unreachable]"
        if stmt.synthetic_default:
            tags += " [synthetic_default]"
        if stmt.default_goto:
            tags += " [default_goto]"
        lines = [f"{pad}match {_expr(stmt.subject)}:{tags}"]
        arm_pad = "  " * (depth + 1)
        if stmt.none_entry is not None:
            lines.append(f"{arm_pad}case None:")
            for s in stmt.none_entry.body:
                lines.extend(_stmt_lines(s, depth + 2))
        for arm in stmt.arms:
            head = ", ".join(arm.labels) if arm.labels else "default"
            for entry in arm.entries:
                extra = ""
                if entry.binding is not None:
                    extra += f" as %{entry.binding.name} [{entry.binding.mode}]"
                if entry.guard is not None:
                    extra += f" if {_expr(entry.guard)}"
                lines.append(f"{arm_pad}case {head}:{extra}")
                for s in entry.body:
                    lines.extend(_stmt_lines(s, depth + 2))
        return lines
    if isinstance(stmt, THIRTupleUnpack):
        tgts = ", ".join(n if n is not None else "_" for n in stmt.targets)
        src = _expr(stmt.source_expr) if stmt.source_expr is not None \
            else f"%{stmt.source}"
        return [f"{pad}{tgts} = {src}"]
    if isinstance(stmt, THIRBreak):
        return [f"{pad}break"]
    if isinstance(stmt, THIRContinue):
        return [f"{pad}continue"]
    if isinstance(stmt, THIRDelVar):
        sinks = ", ".join(f"{'*' if deref else ''}{name}"
                          for name, deref in stmt.sinks)
        return [f"{pad}del [{sinks}]"]
    if isinstance(stmt, THIRPrint):
        args = ", ".join(f"{_expr(a.expr)} [{a.print_form.name.lower()}]" for a in stmt.args)
        return [f"{pad}print({args})"]
    if isinstance(stmt, THIRExprStmt):
        return [f"{pad}{_expr(stmt.expr)}"]
    return [f"{pad}<{type(stmt).__name__}>"]


def _extend_orelse(lines: list[str], orelse, depth: int) -> None:
    if not orelse:
        return
    lines.append("  " * depth + "else:")
    for s in orelse:
        lines.extend(_stmt_lines(s, depth + 1))


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
