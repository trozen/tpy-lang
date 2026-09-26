"""Human-readable `--dump-thir` text format.

A debug rendering of the lowered THIR -- resolved types and facts visible at a
glance. Not consumed by codegen; purely for inspecting the sema/codegen seam.
"""

from __future__ import annotations

from typing import Iterable

from ..identity_map import IdentityMap
from ..typesys import TpyType
from .reject import is_bodyless_binding
from .lower import iter_module_callables, iter_module_constructors
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
    THIRWalrus,
    THIRValueSelect,
    THIRClassConstant,
    THIRConceptTest,
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
    THIRAnyIsinstance,
    THIRDynIsinstance,
    THIRDynIsinstanceMulti,
    THIRExprStmt,
    THIRContainerLiteral,
    THIRContinue,
    THIRDefaultConstruct,
    THIRDelVar,
    THIRDelItem,
    THIRFinallyDeferredReturn,
    THIRLiteral,
    THIRMatch,
    THIRMethodCall,
    THIRModuleVar,
    THIRConsumingIter,
    THIRCopy,
    THIRSlotEmplace,
    THIRDecayCopy,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRDynNarrowAlias,
    THIRAnyNarrowAlias,
    THIRNarrowedRead,
    THIROptionalPtrArg,
    THIRPrint,
    THIRPrintChain,
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
    THIROwnOptRebuild,
    THIRUnaryNot,
    THIRUnaryArith,
    THIRUnionArgLift,
    THIRVarDecl,
    THIRWhile,
    THIRWith,
    THIRBorrowTupleLiteral,
    THIRConstructor,
    THIRChainedCompareStmtExpr,
    THIRComprehension,
    THIRErrorReturnBind,
    THIRErrorReturnDiscard,
    THIRErrorReturnUnwrap,
    THIRForIterProto,
    THIRCoroHandleMove,
    THIRFrameSlotWrite,
    THIRFunction,
    THIRGenExpr,
    THIRInplaceContainerOp,
    THIRLambda,
    THIRListRepeat,
    THIRMembership,
    THIRNestedDef,
    THIRFoldedBlock,
    THIRFoldedIfChain,
    THIRMatchFoldBind,
    THIRFrameNestedDef,
    THIRImportInit,
    THIRNoOpStmt,
    THIROverloadDefault,
    THIRParamCopy,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRRaise,
    THIRRecordCopy,
    THIRResumableBody,
    THIRResumableReturn,
    THIRSetItem,
    THIRSliceAssign,
    THIRStmtSeq,
    THIRStrMembership,
    THIRTry,
    THIRTupleLiteral,
    THIRTupleMembership,
    THIRTupleValueToBorrow,
    THIRVarargPack,
)


# Node classes deliberately rendered as a bare `<Name>` placeholder rather
# than a real arm. Empty by design: an entry here is a hole in the dump, so
# it needs a stated reason, and `test_dump.py` fails an entry that HAS an arm
# (the list cannot silently outlive its exception).
_UNDUMPED: frozenset[type] = frozenset()


def _ty(t: TpyType) -> str:
    return getattr(t, "name", None) or str(t)


def _expr(e: THIRExpr) -> str:
    if isinstance(e, THIRName):
        # A pointer-local read in a value position renders `(*name)`.
        return f"*%{e.name}" if e.deref else f"%{e.name}"
    if isinstance(e, THIRSelf):
        # A plain-method receiver read is `(*this)` in a value position;
        # the dump has to show the two apart, like the name arm above.
        return "*%self" if e.deref else "%self"
    if isinstance(e, THIRConceptTest):
        return f"concept({e.cpp})"
    if isinstance(e, THIRLiteral):
        # The form is load-bearing for a None literal (STORAGE -> std::nullopt
        # vs VALUE/BORROW -> nullptr), so surface it like the other form tags.
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return f"lit({e.value!r}){tag}"
    if isinstance(e, THIRDefaultConstruct):
        return f"default({e.cpp_type})"
    if isinstance(e, THIRStrLiteral):
        return f"str({e.value!r})" + ("" if e.form is Form.VALUE else " [view]")
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
    if isinstance(e, THIRWalrus):
        decl = f" [decl {e.cpp_type}]" if e.cpp_type is not None else ""
        return f"walrus({e.name}{decl}, {_expr(e.value)})"
    if isinstance(e, THIRBinOp):
        return f"binop({_expr(e.left)}, {e.op}, {_expr(e.right)})"
    if isinstance(e, THIRValueSelect):
        temp = (f" [lhs_temp {e.lhs_temp_cpp}"
                f"{', moved' if e.lhs_move else ''}]"
                if e.lhs_temp_cpp is not None else "")
        return (f"value_select({_expr(e.lhs)}, {e.op}, {_expr(e.rhs)}"
                f"{temp})")
    if isinstance(e, THIRUnaryNot):
        return f"not({_expr(e.operand)})"
    if isinstance(e, THIRUnaryArith):
        return f"unary_arith({e.cpp_template!r}, {_expr(e.operand)})"
    if isinstance(e, THIRIsNone):
        return (f"is_none({_expr(e.operand)}{', negate' if e.negate else ''}"
                f"{', value_repr' if e.value_repr else ''})")
    if isinstance(e, THIRTruthy):
        deref = "*" if e.deref else ""
        return f"truthy[{e.mode.name.lower()}]({deref}{_expr(e.operand)})"
    if isinstance(e, THIROptViewArg):
        return f"opt_view_arg({e.name})"
    if isinstance(e, THIROwnOptRebuild):
        return f"own_opt_rebuild({e.name})"
    if isinstance(e, THIRIfExpr):
        # The form tag is emit-relevant for a str-family result (the
        # owned-sink copy fires on BORROW), so surface it.
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        return (f"ifexpr({_expr(e.cond)} ? {_expr(e.then)}"
                f" : {_expr(e.orelse)}){tag}")
    if isinstance(e, THIRCall):
        # Surface the emit arm: a scalar-ctor cpp_template, a @native
        # free-function symbol, or the bare callee name.
        if e.callee_expr is not None:
            name = f"({_expr(e.callee_expr)})"
        elif e.cpp_template is not None:
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
            # NB pre-fold display: a move_receiver+is_arrow call PRINTS
            # "->" here but EMITS `std::move(*recv).m()` (dot).
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
            inner = f"to_const:{e.const_wrap}({_expr(e.value)})"
        else:
            inner = f"&({'*' if e.deref else ''}{_expr(e.value)})"
        return f"union_lift[{e.variant_cpp}]{{{inner}}}"
    if isinstance(e, THIRArgTemp):
        # Number-free by design: the real __tmp_N is drawn at emission from
        # the module-cumulative sink.
        mods = (" move" if e.move else "") + (" addr" if e.addr_of else "")
        return f"%argtmp({e.cpp_type or 'auto'}{mods}){{{_expr(e.init)}}}"
    if isinstance(e, THIRSlotEmplace):
        return f"slot_emplace[{e.cpp_type}]({_expr(e.value)})"
    if isinstance(e, THIRCopy):
        return f"copy[{e.cpp_type}]({_expr(e.value)})"
    if isinstance(e, THIRConsumingIter):
        return f"consuming_iter[{e.native_name}]({_expr(e.value)})"
    if isinstance(e, THIRMove):
        return f"move({_expr(e.value)})"
    if isinstance(e, THIRDecayCopy):
        return f"decay_copy({_expr(e.value)})"
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
    if isinstance(e, THIRAnyIsinstance):
        return (f"any_isinstance(%{e.subject_cpp}, "
                f"[{', '.join(e.member_cpps)}])")
    if isinstance(e, THIRDynIsinstance):
        return f"dyn_isinstance[{e.ptr_local}]({e.init_cpp})"
    if isinstance(e, THIRDynIsinstanceMulti):
        return f"dyn_isinstance_multi([{', '.join(e.checks_cpp)}])"
    if isinstance(e, THIRPrintChain):
        args = ", ".join(f"{_expr(a.expr)} [{a.print_form.name.lower()}]"
                         for a in e.args)
        return f"print_chain({args})"
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
        # The wrap template is the emit; the operand fills its `{0}` slot.
        return f"enum_wrap({e.wrap!r}, {_expr(e.operand)})"
    if isinstance(e, THIRTupleLiteral):
        return f"tuple({_exprs(e.elements)})"
    if isinstance(e, THIRBorrowTupleLiteral):
        # Per-element address-of / wrap is the borrow-form decision, so it
        # shows per element rather than as one flag.
        wraps = e.elem_wraps or (None,) * len(e.elements)
        elems = ", ".join(
            w.format(_expr(x)) if w is not None
            else ("&" if a else "") + _expr(x)
            for x, a, w in zip(e.elements, e.addr_of, wraps))
        return f"borrow_tuple<{e.spelled_cpp}>({elems})"
    if isinstance(e, THIRTupleValueToBorrow):
        elems = ", ".join(("&" if a else "") + _expr(x)
                          for x, a in zip(e.elements, e.addr_of))
        return f"tuple_value_to_borrow<{e.dst_cpp}>({e.src_cpp}{{{elems}}})"
    if isinstance(e, THIRComprehension):
        # The loop strategy and result container are the emit-shaping facts.
        proto = " [iter_protocol]" if e.iter_protocol else ""
        return (f"comp[{e.kind}/{e.loop}]({e.container_cpp}, "
                f"var %{e.var}{' const' if e.const_loop_var else ''}){proto}")
    if isinstance(e, THIRGenExpr):
        src = (f"range({_exprs(e.range_args)})" if e.range_args
               else "" if e.iterable is None else _expr(e.iterable))
        owned = (" [owned_source, pinned]" if e.pinned_source
                 else " [owned_source]" if e.owned_source else "")
        caps = "".join(f", {_expr(c)}" for c in e.frame_captures)
        return f"genexpr[{e.frame_factory_cpp}]({src}{caps}){owned}"
    if isinstance(e, THIRLambda):
        params = ", ".join(e.params_cpp)
        ret = f" -> {e.ret_cpp}" if e.ret_cpp is not None else ""
        return f"lambda[{e.capture_cpp}]({params}){ret}: {_expr(e.body)}"
    if isinstance(e, THIRListRepeat):
        count = "" if e.count is None else _expr(e.count)
        big = " [bigint]" if e.count_bigint else ""
        return f"list_repeat([{_exprs(e.elements)}] * {count}){big}"
    if isinstance(e, (THIRMembership, THIRStrMembership, THIRTupleMembership)):
        return _membership(e)
    if isinstance(e, THIRChainedCompareStmtExpr):
        # Rendered as the comparison chain it collapses to; the bound flags
        # mark which operands were hoisted to temps.
        parts = []
        for i, op in enumerate(e.ops):
            lhs = _expr(e.inits[i])
            if i < len(e.bound) and e.bound[i]:
                lhs += " [temp]"
            parts.append(f"{lhs} {op}")
        tail = _expr(e.inits[-1]) if e.inits else ""
        return f"chained_compare({' '.join(parts)} {tail})"
    if isinstance(e, THIRErrorReturnUnwrap):
        form = "value" if e.value_form else "ptr"
        return f"er_unwrap[{form}]({_expr(e.call)})"
    if isinstance(e, THIRRecordCopy):
        return f"record_copy<{e.cpp_type}>({_expr(e.value)})"
    if isinstance(e, THIRVarargPack):
        star = "" if e.star_source is None else f", *{_expr(e.star_source)}"
        ref = " [ref]" if e.is_ref else ""
        return f"vararg_pack<{e.elem_cpp}>({_exprs(e.args)}{star}){ref}"
    if type(e) in _UNDUMPED:
        return f"<{type(e).__name__}>"
    raise AssertionError(
        f"--dump-thir has no arm for {type(e).__name__}; add one in dump.py "
        f"(or list it in _UNDUMPED with a reason)")


def _exprs(items: 'Iterable[THIRExpr]') -> str:
    return ", ".join(_expr(x) for x in items)


def _membership(e: THIRExpr) -> str:
    """`in` / `not in` across the three membership nodes -- they differ only
    in how the containment is spelled, which is the fact worth showing."""
    neg = "not_in" if e.negate else "in"
    if isinstance(e, THIRStrMembership):
        sv = " [sv]" if e.wrap_receiver_sv else ""
        return f"str_{neg}({_expr(e.needle)}, {_expr(e.receiver)}){sv}"
    if isinstance(e, THIRTupleMembership):
        tmp = " [temp]" if e.need_temp else ""
        return f"tuple_{neg}({_expr(e.left)}, [{_exprs(e.elements)}]){tmp}"
    how = ("free" if e.free_function
           else "ranges" if e.ranges_contains else "method")
    return (f"{neg}[{how}]({_expr(e.needle)}, {_expr(e.receiver)}"
            f", {e.method_cpp!r})")


def _deferred_parts(parts) -> str:
    out = []
    for p in parts:
        if p.kind.name == "TUPLE":
            out.append(_deferred_parts(p.parts))
        else:
            out.append(f"{p.kind.name.lower()}:{_expr(p.expr)}")
    return f"({', '.join(out)})"


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
    if isinstance(stmt, THIRDynNarrowAlias):
        cst = "const " if stmt.is_const else ""
        return [f"{pad}%{stmt.alias} = {cst}&*{stmt.cast_rhs_cpp}"]
    if isinstance(stmt, THIRAnyNarrowAlias):
        return [f"{pad}%{stmt.alias} = const &any_cast<{stmt.member_cpp}>"
                f"(%{stmt.subject_cpp})"]
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
    if isinstance(stmt, THIRFinallyDeferredReturn):
        if stmt.tuple_cpp is not None:
            return [f"{pad}return [finally-deferred tuple] "
                    f"{_deferred_parts(stmt.tuple_parts)}"]
        kind = "opt-move" if stmt.optional_move else "move"
        cap = _expr(stmt.capture)
        return [f"{pad}return [finally-deferred {kind}] "
                f"{'*' if stmt.indirect else ''}{cap}"]
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
        # `[frame_src=N]`: the resumable frame owns the source in that field.
        fsrc = ("" if stmt.frame_src_field is None
                else f" [frame_src={stmt.frame_src_field}]")
        lines = [f"{pad}for %{stmt.var}{const}{rval}{fsrc} "
                 f"in {_expr(stmt.iterable)}:"]
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
                if entry.poly_cast is not None:
                    head = f"cast -> {entry.case_alias}"
                elif entry.poly_or_conds is not None:
                    head = f"cast-or[{len(entry.poly_or_conds)}]"
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
    if isinstance(stmt, THIRDelItem):
        return [f"{pad}del [{', '.join(_expr(c) for c in stmt.calls)}]"]
    if isinstance(stmt, THIRPrint):
        args = ", ".join(f"{_expr(a.expr)} [{a.print_form.name.lower()}]" for a in stmt.args)
        return [f"{pad}print({args})"]
    if isinstance(stmt, THIRExprStmt):
        return [f"{pad}{_expr(stmt.expr)}"]
    if isinstance(stmt, THIRNoOpStmt):
        return [f"{pad}noop"]
    if isinstance(stmt, THIRImportInit):
        return [f"{pad}import-init [{', '.join(stmt.calls)}]"]
    if isinstance(stmt, THIRFrameNestedDef):
        return [f"{pad}frame-nested-def {stmt.name}"]
    if isinstance(stmt, THIRFoldedBlock):
        burn = " [burns_match_counter]" if stmt.burns_match_counter else ""
        lines = [f"{pad}overload-fold{burn}:"]
        for s in stmt.stmts:
            lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRFoldedIfChain):
        lines = [f"{pad}overload-live-chain:"]
        for cond, body in stmt.branches:
            lines.append(f"{pad}  branch {_expr(cond)}:")
            for s in body:
                lines.extend(_stmt_lines(s, depth + 2))
        if stmt.else_body:
            lines.append(f"{pad}  else:")
            for s in stmt.else_body:
                lines.extend(_stmt_lines(s, depth + 2))
        return lines
    if isinstance(stmt, THIRMatchFoldBind):
        binder = "auto" if stmt.by_value else "auto&"
        return [f"{pad}{binder} %{stmt.name_cpp} = {stmt.source_cpp}"]
    if isinstance(stmt, THIRStmtSeq):
        # A transparent carrier: emit its members at the same depth so the
        # dump mirrors the emitted statement sequence.
        lines: list[str] = []
        for s in stmt.stmts:
            lines.extend(_stmt_lines(s, depth))
        return lines
    if isinstance(stmt, THIRRaise):
        if stmt.cpp_type is None:
            return [f"{pad}raise [bare]"]
        via = " [virtual]" if stmt.via_virtual else ""
        tier = " [return_tier]" if stmt.return_tier else ""
        return [f"{pad}raise {stmt.cpp_type}({_exprs(stmt.args)}){via}{tier}"]
    if isinstance(stmt, THIRTry):
        lines = [f"{pad}try [{stmt.tier.value}]:"]
        for s in stmt.try_body:
            lines.extend(_stmt_lines(s, depth + 1))
        for h in stmt.handlers:
            bind = f" as %{h.binding}" if h.binding else ""
            lines.append(f"{pad}except {h.cpp_type or ''}{bind}:")
            for s in h.body:
                lines.extend(_stmt_lines(s, depth + 1))
        if stmt.else_body:
            lines.append(f"{pad}else:")
            for s in stmt.else_body:
                lines.extend(_stmt_lines(s, depth + 1))
        if stmt.finally_body:
            lines.append(f"{pad}finally:")
            for s in stmt.finally_body:
                lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRSetItem):
        return [f"{pad}{_expr(stmt.target)} = {_expr(stmt.value)}"]
    if isinstance(stmt, THIRSliceAssign):
        lo = "" if stmt.lower is None else _expr(stmt.lower)
        hi = "" if stmt.upper is None else _expr(stmt.upper)
        return [f"{pad}{_expr(stmt.receiver)}[{lo}:{hi}] = "
                f"{_expr(stmt.value)} [{stmt.native_name}]"]
    if isinstance(stmt, THIRInplaceContainerOp):
        # The native symbol is a call, not an operator -- `x tpy::list_extend= y`
        # read as though it were one.
        return [f"{pad}inplace[{stmt.native_name}]({_expr(stmt.receiver)}, "
                f"{_expr(stmt.value)})"]
    if isinstance(stmt, THIRParamCopy):
        return [f"{pad}param_copy %{stmt.name}: {stmt.cpp_type}"]
    if isinstance(stmt, THIROverloadDefault):
        return [f"{pad}overload_default %{stmt.name}: {stmt.cpp_type} = "
                f"{stmt.cpp_default}"]
    if isinstance(stmt, THIRPtrLocalDecl):
        init = "" if stmt.init is None else f" = {_expr(stmt.init)}"
        return [f"{pad}ptr_decl[{stmt.kind.name.lower()}] %{stmt.name}: "
                f"{_ty(stmt.resolved_type)}{init}"]
    if isinstance(stmt, THIRPtrLocalRebind):
        val = "" if stmt.value is None else f" = {_expr(stmt.value)}"
        return [f"{pad}ptr_rebind[{stmt.kind.name.lower()}] %{stmt.name}{val}"]
    if isinstance(stmt, THIRFrameSlotWrite):
        return [f"{pad}frame_slot %{stmt.name} <- {_expr(stmt.value)}"]
    if isinstance(stmt, THIRCoroHandleMove):
        return [f"{pad}coro_handle_move %{stmt.target} <- %{stmt.source}"]
    if isinstance(stmt, THIRResumableReturn):
        val = "" if stmt.value is None else f" {_expr(stmt.value)}"
        return [f"{pad}resumable_return{val}"]
    if isinstance(stmt, THIRNestedDef):
        params = ", ".join(stmt.params_cpp)
        ret = f" -> {stmt.ret_cpp}" if stmt.ret_cpp is not None else ""
        lines = [f"{pad}nested_def[{stmt.capture_cpp}] "
                 f"{stmt.name}({params}){ret}:"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        return lines
    if isinstance(stmt, THIRForIterProto):
        const = " const" if stmt.const_loop_var else ""
        fsrc = ("" if stmt.frame_src_field is None
                else f" [frame_src={stmt.frame_src_field}]")
        lines = [f"{pad}for %{stmt.var}{const}{fsrc}: {_ty(stmt.elem_type)} "
                 f"in iter_proto({_expr(stmt.iterable)}):"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        _extend_orelse(lines, stmt.orelse, depth)
        return lines
    if isinstance(stmt, THIRErrorReturnBind):
        decl = f": {stmt.decl_cpp}" if stmt.decl_cpp is not None else ""
        return [f"{pad}er_bind %{stmt.name}{decl} = {_expr(stmt.call)}"]
    if isinstance(stmt, THIRErrorReturnDiscard):
        return [f"{pad}er_discard({_expr(stmt.call)})"]
    if type(stmt) in _UNDUMPED:
        return [f"{pad}<{type(stmt).__name__}>"]
    raise AssertionError(
        f"--dump-thir has no arm for {type(stmt).__name__}; add one in "
        f"dump.py (or list it in _UNDUMPED with a reason)")


def _extend_orelse(lines: list[str], orelse, depth: int) -> None:
    if not orelse:
        return
    lines.append("  " * depth + "else:")
    for s in orelse:
        lines.extend(_stmt_lines(s, depth + 1))


def _function_lines(fn: 'THIRFunction') -> list[str]:
    params = ", ".join(f"{p.name}: {_ty(p.type)}" for p in fn.params)
    lines = [f"fn {fn.name}({params}) -> {_ty(fn.return_type)}:"]
    for s in fn.body:
        lines.extend(_stmt_lines(s, 1))
    return lines


def _resumable_lines(name: str, body: 'THIRResumableBody') -> list[str]:
    """A resumable body holds LEAVES keyed by the skeleton's node ids, not a
    statement list -- the skeleton emits the state machine around them. Render
    each keyed group so what THIR contributed is visible per seam."""
    lines = [f"resumable {name}:"]
    groups = (
        ("leaves", body.leaves, _stmt_lines),
        ("match_dispatches", body.match_dispatches, _stmt_lines),
        ("conds", body.conds, None),
        ("return_values", body.return_values, None),
        ("yield_values", body.yield_values, None),
        ("suspend_exprs", body.suspend_exprs, None),
        ("region_exprs", body.region_exprs, None),
    )
    for label, mapping, stmt_render in groups:
        if not mapping:
            continue
        lines.append(f"  {label}:")
        # Keyed by skeleton node id -- renumbered sequentially in lowering
        # order so the dump is stable across runs (an address is not).
        for i, key in enumerate(mapping):
            if stmt_render is not None:
                rendered = stmt_render(mapping[key], 0)
                lines.append(f"    [{i}] {rendered[0].lstrip()}"
                             if rendered else f"    [{i}]")
                lines.extend(f"    {ln}" for ln in rendered[1:])
            else:
                lines.append(f"    [{i}] {_expr(mapping[key])}")
    if body.await_args:
        lines.append("  await_args:")
        for i, key in enumerate(body.await_args):
            lines.append(f"    [{i}] ({_exprs(body.await_args[key])})")
    return lines


def _constructor_lines(name: str, ctor: 'THIRConstructor') -> list[str]:
    lines = [f"ctor {name}:"]
    for base in ctor.base_inits:
        lines.append(f"  base {base.base_cpp}({_exprs(base.args)})")
    for init in ctor.mil_inits:
        move = " [move]" if init.move else ""
        lines.append(f"  mil {init.field_cpp} = {_expr(init.value)}{move}")
    for s in ctor.body:
        lines.extend(_stmt_lines(s, 1))
    return lines


def dump_codegen_thir(module_ast, analyzer, ctx,
                      reasons: 'IdentityMap | None' = None) -> str:
    """Dump the bodies CODEGEN lowered, read off its per-module THIR caches.

    Every body kind is shown -- sync, resumable, constructor -- and the
    ones with no THIR are named, since "what did NOT
    lower" is usually the question being asked. `reasons` (the compiler's
    per-body first-reject map) names WHY a body rejected.

    Three ways a body can have no THIR, and the dump must not conflate them:
    it has no body to lower at all, lowering was attempted and rejected, or a
    reject earlier in the module ended emission before this body's turn.
    """
    reasons = reasons if reasons is not None else IdentityMap()

    def _not_routed(kind: str, name: str, fn) -> str:
        if is_bodyless_binding(fn) or getattr(fn, "is_overload_stub", False):
            return f"{kind} {name}: <no body to lower>"
        why = reasons.get(fn)
        if why is not None:
            return f"{kind} {name}: <rejected: {why}>"
        return f"{kind} {name}: <not attempted: an earlier reject ended emission>"

    lines: list[str] = []
    seen_any = False
    # The module-init body first, in source order: it runs before every
    # callable and is a lowering unit like them, so omitting it would read as
    # "top-level does not lower" rather than "top-level is not shown".
    if module_ast.top_level_stmts:
        top = getattr(ctx, "thir_top_level", None)
        if top is not None:
            lines.extend(_function_lines(top))
        else:
            why = reasons.get(module_ast)
            lines.append(
                f"top-level __tpy_init: <rejected: {why}>" if why is not None
                else "top-level __tpy_init: "
                     "<not attempted: an earlier reject ended emission>")
        lines.append("")
        seen_any = True
    for func, _self_type in iter_module_callables(module_ast, analyzer):
        stubs = analyzer.overload_groups.get(func)
        per_stub = ctx.thir_overload_functions.get(func)
        stub_fns = ([None if per_stub is None else per_stub.get(s) for s in stubs]
                    if stubs else [])
        if func in ctx.thir_functions:
            lines.extend(_function_lines(ctx.thir_functions[func]))
        elif stub_fns and all(fn is not None for fn in stub_fns):
            for fn in stub_fns:
                lines.extend(_function_lines(fn))
                lines.append("")
            lines.pop()
        elif ctx.thir_resumables.get(func) is not None:
            lines.extend(_resumable_lines(func.name, ctx.thir_resumables[func]))
        else:
            lines.append(_not_routed("fn", func.name, func))
        lines.append("")
        seen_any = True
    for record, init, _self in iter_module_constructors(
            module_ast, analyzer):
        name = f"{record.name}.__init__"
        if init in ctx.thir_constructors:
            lines.extend(_constructor_lines(name, ctx.thir_constructors[init]))
        else:
            lines.append(_not_routed("ctor", name, init))
        lines.append("")
        seen_any = True
    if not seen_any:
        lines.append("(no callables in this module)")
    return "\n".join(lines).rstrip("\n") + "\n"
