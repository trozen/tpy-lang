"""Human-readable `--dump-thir` text format.

A debug rendering of the lowered THIR -- resolved types and facts visible at a
glance. Not consumed by codegen; purely for inspecting the sema/codegen seam.
"""

from __future__ import annotations

from typing import Iterable

from ..identity_map import IdentityMap
from ..typesys import TpyType, unwrap_readonly, unwrap_ref_type
from .frame import (
    LeafRole, THIRAsyncWithSetup, THIRBranch, THIRExceptRegion, THIRFall,
    THIRFinallyExit, THIRFinallyRegion,
    THIRFallOff, THIRForAdvance, THIRForIterSetup, THIRFrameRaise, THIRFrameReturn,
    THIRLoopSourceRegion, THIRMatchDispatch, THIRResumableFrame, THIRSuspend,
    THIRTryRegion, THIRWithEnter, THIRWithRegion, THIRYieldSuspension,
    stmt_slots, terminator_slots,
)
from .reject import is_bodyless_binding
from .validate import _iter_children
from .lower import iter_module_callables, iter_module_constructors
from .nodes import (
    Form, body_header,
    TruthinessMode,
    THIRArgTemp,
    THIRTempRead,
    BOUND_SOURCE_READ,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRCallableSignature,
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
    THIRFunctionIdentity,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRIf,
    THIRIdentity,
    THIRIfExpr,
    THIRIsNone,
    THIRIsinstance,
    THIRAnyIsinstance,
    THIRDynIsinstance,
    THIRDynIsinstanceMulti,
    THIRDynCast,
    THIREvalThen,
    THIRExprStmt,
    THIRContainerLiteral,
    THIRContinue,
    THIRDefaultConstruct,
    THIRCloseLocal,
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
    THIRNoneCheck,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRDynNarrowAlias,
    THIRAnyNarrowAlias,
    THIRNarrowedRead,
    THIROptionalPtrArg,
    THIRPrint,
    THIRPrintArg,
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
    THIRArrayComprehension,
    THIRComprehensionBlock,
    THIRCompInsert,
    THIRErrorReturnBind,
    THIRErrorReturnDiscard,
    THIRErrorReturnUnwrap,
    THIRForIterProto,
    THIRCoroHandleMove,
    THIRFrameSlotWrite,
    THIRFunction,
    THIRGenExpr,
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
    THIRResumableLeaves,
    THIRResumableReturn,
    THIRSetItem,
    THIRSliceAssign,
    THIRStmtSeq,
    THIRStrMembership,
    THIRTry,
    THIRTupleLiteral,
    THIRTupleMembership,
    THIRVarargPack,
)


# Node classes deliberately rendered as a bare `<Name>` placeholder rather
# than a real arm. Empty by design: an entry here is a hole in the dump, so
# it needs a stated reason, and `test_dump.py` fails an entry that HAS an arm
# (the list cannot silently outlive its exception).
_UNDUMPED: frozenset[type] = frozenset()


def _ty(t: TpyType) -> str:
    return getattr(t, "name", None) or str(t)


def _print_arg(a: THIRPrintArg) -> str:
    if a.star_elem is not None:
        return (f"*{_expr(a.expr)} [{a.print_form.name.lower()}: "
                f"{a.star_elem.print_form.name.lower()}]")
    return f"{_expr(a.expr)} [{a.print_form.name.lower()}]"


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
        slot = (f" [slot {e.reseat_slot}]" if e.reseat_slot is not None
                else "")
        value = _expr(e.value) if e.value is not None else "None"
        write = " [write]" if e.write is not None else ""
        return f"walrus({e.name}{decl}{slot}{write}, {value})"
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
    if isinstance(e, THIRIdentity):
        return (f"identity({_expr(e.left)}, {_expr(e.right)}"
                f"{', negate' if e.negate else ''})")
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
        # the ctx sink.
        mods = ((" move" if e.move else "") + (" addr" if e.addr_of else "")
                + (f" #{e.read_id}" if e.read_id is not None else ""))
        return f"%argtmp({e.cpp_type or 'auto'}{mods}){{{_expr(e.init)}}}"
    if isinstance(e, THIRTempRead):
        if e.read_id == BOUND_SOURCE_READ:
            return "%bound"
        return f"%argtmp#{e.read_id}"
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
        op = "->" if e.is_arrow else "."
        return f"{_expr(e.receiver)}{op}{e.field_cpp}{_declaring_owner(e)}{tag}"
    if isinstance(e, THIRSubscript):
        tag = "" if e.form is Form.VALUE else f" [{e.form.name.lower()}]"
        # A constant offset (every tuple `std::get<N>`, a literal container index)
        # renders bare (`t[0]`); a dynamic container index renders the expr (`c[i]`).
        idx = e.index.value if isinstance(e.index, THIRLiteral) else _expr(e.index)
        flags = " bounds_safe" if e.bounds_safe else ""
        if e.bound_getitem is not None and not e.element_access:
            b = e.bound_getitem
            name = b.cpp_template or b.native_function_name or b.method_cpp
            flags += f" bound({name})"
        return f"{_expr(e.receiver)}[{idx}]{tag}{flags}"
    if isinstance(e, THIRNoneCheck):
        u = e.use
        parts = [p for p in (u.attr, u.op, u.other) if p is not None]
        if u.other is not None and not u.none_left:
            parts.append("right")
        detail = f" {' '.join(parts)}" if parts else ""
        return f"none_check[{u.kind.value}{detail}]({_expr(e.value)})"
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
        return (f"isinstance({_expr(e.subject)}"
                f"{'.value' if e.wrapper else ''}, "
                f"[{', '.join(e.member_cpps)}])")
    if isinstance(e, THIRAnyIsinstance):
        return (f"any_isinstance({_expr(e.subject)}, "
                f"[{', '.join(e.member_cpps)}])")
    if isinstance(e, THIRDynCast):
        addr = "&" if e.address_of else ""
        return f"dyn_cast[{e.cpp_template}]({addr}{_expr(e.subject)})"
    if isinstance(e, THIRDynIsinstance):
        return (f"dyn_isinstance[{e.decl_cpp} {e.ptr_local}]"
                f"({_expr(e.cast)})")
    if isinstance(e, THIRDynIsinstanceMulti):
        return (f"dyn_isinstance_multi({_expr(e.subject)}, "
                f"[{', '.join(e.member_cpps)}])")
    if isinstance(e, THIRPrintChain):
        args = ", ".join(_print_arg(a) for a in e.args)
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
        if e.bound is not None:
            return (f"tuple[%bound = {_expr(e.bound)}]"
                    f"({_exprs(e.elements)})")
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
    if isinstance(e, THIRArrayComprehension):
        src = "" if e.iterable is None else f" over {_expr(e.iterable)}"
        head = ("" if e.head is None else " { " + "; ".join(
            line.strip() for line in _stmt_lines(e.head, 0)) + " }")
        return (f"array_comp[{e.arm}]({e.array_elem_cpp}, {e.array_size_cpp}, "
                f"var %{e.var}){src}{head} -> {_expr(e.element)}")
    if isinstance(e, THIRComprehensionBlock):
        # The body is ordinary statements; an expression dumps on one line,
        # so they join in braces.
        body = "; ".join(line.strip() for s in e.body
                         for line in _stmt_lines(s, 0))
        return f"comp[{e.kind}]({e.container_cpp}) {{ {body} }}"
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
        # The temps it binds, then the pairs over them.
        binds = [f"_cmp{i}{' [value]' if e.by_value and e.by_value[i] else ''}"
                 f" = {_expr(init)}" for i, init in enumerate(e.inits)
                 if init is not None]
        pairs = " && ".join(_expr(p) for p in e.pairs)
        return f"chained_compare({'; '.join(binds + [pairs])})"
    if isinstance(e, THIREvalThen):
        return f"eval_then({_expr(e.init)}; {_expr(e.value)})"
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
           else "method")
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


def _held(held) -> str:
    """The temps a statement holds, as a `held [...]; ` prefix."""
    if not held:
        return ""
    return f"held [{', '.join(_expr(t) for t in held)}]; "


def _stmt_lines(stmt: THIRStmt, depth: int) -> list[str]:
    # A predeclaration is part of where a name lives, so the dump shows it at
    # the statement that owns it.
    pad = "  " * depth
    lines = [f"{pad}hoist %{h.name}: {h.cpp_type}"
             for h in getattr(stmt, "hoist_decls", ())]
    lines += [f"{pad}slot %{s.name}: std::optional<{s.cpp}>"
              for s in stmt.anchored_slots]
    return lines + _stmt_body_lines(stmt, depth)


def _stmt_body_lines(stmt: THIRStmt, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(stmt, THIRVarDecl):
        init = _expr(stmt.init) if stmt.init is not None else "<uninit>"
        return [f"{pad}%{stmt.name}: {_ty(stmt.resolved_type)} = {init}"]
    if isinstance(stmt, THIRAssign):
        # target is a THIRName (`%x`) or, for F2b, a THIRFieldAccess (`recv.field`).
        return [f"{pad}{_held(stmt.held)}{_expr(stmt.target)} = {_expr(stmt.value)}"]
    if isinstance(stmt, THIRStrAppend):
        return [f"{pad}%{stmt.target} += {_expr(stmt.value)}"]
    if isinstance(stmt, THIRNarrowAlias):
        deref = "*" if stmt.is_ptr_variant else ""
        cst = "const " if stmt.const_ref else ""
        return [f"{pad}%{stmt.alias} = {cst}&{deref}get<{stmt.member_cpp}>"
                f"(%{stmt.variant_cpp})"]
    if isinstance(stmt, THIRDynNarrowAlias):
        cst = "const " if stmt.is_const else ""
        return [f"{pad}%{stmt.alias} = {cst}&*{_expr(stmt.cast)}"]
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
        presize = "" if stmt.presize is None else f" [presize {stmt.presize}]"
        lines = [f"{pad}for %{stmt.var} in range({start}, {_expr(stmt.stop)}{step}){presize}:"]
        for s in stmt.body:
            lines.extend(_stmt_lines(s, depth + 1))
        _extend_orelse(lines, stmt.orelse, depth)
        return lines
    if isinstance(stmt, THIRForEach):
        # `[<binding>]` is the loop variable record's binding kind, which
        # spells its C++ binding line (`const auto&`, `auto&&`, a copy, ...).
        # `[rvalue]` marks an owning `auto __obj_N` capture (vs the `auto&` alias).
        const = _loop_binding_tag(stmt.binding)
        rval = "" if stmt.iterable_lvalue else " [rvalue]"
        # `[frame_src=N]`: the resumable frame owns the source in that field.
        fsrc = ("" if stmt.frame_src_field is None
                else f" [frame_src={stmt.frame_src_field.name}]")
        presize = "" if stmt.presize is None else f" [presize {stmt.presize}]"
        lines = [f"{pad}for %{stmt.var}{const}{rval}{fsrc}{presize} "
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
    if isinstance(stmt, THIRTupleUnpack) and stmt.per_target:
        tgts = ", ".join(n if n is not None else "_" for n in stmt.targets)
        lines = [f"{pad}unpack {tgts}"]
        if stmt.holder is not None:
            ref = "&&" if stmt.holder_ref else ""
            lines.append(f"{pad}  hold{ref} %{stmt.holder_read} = "
                         f"{_expr(stmt.holder)}")
        for write in stmt.writes:
            lines.extend(_stmt_lines(write, depth + 1))
        return lines
    if isinstance(stmt, THIRTupleUnpack):
        tgts = ", ".join(n if n is not None else "_" for n in stmt.targets)
        if stmt.tuple_target is not None:
            tgts = ", ".join(
                f"{stmt.tuple_target.name}[{t.index}]:{t.bind.name.lower()}"
                + (f":{t.storage.value}" if t.storage is not None else "")
                for t in stmt.elem_targets)
        return [f"{pad}{tgts} = {_expr(stmt.source_expr)}"]
    if isinstance(stmt, THIRBreak):
        return [f"{pad}break"]
    if isinstance(stmt, THIRContinue):
        return [f"{pad}continue"]
    if isinstance(stmt, THIRDelVar):
        sinks = ", ".join(f"{'*' if deref else ''}{name}"
                          for name, deref in stmt.sinks)
        return [f"{pad}del [{sinks}]"]
    if isinstance(stmt, THIRCloseLocal):
        return [f"{pad}close [{', '.join(stmt.names)}]"]
    if isinstance(stmt, THIRDelItem):
        return [f"{pad}del [{', '.join(_expr(c) for c in stmt.calls)}]"]
    if isinstance(stmt, THIRPrint):
        args = ", ".join(_print_arg(a) for a in stmt.args)
        return [f"{pad}print({args})"]
    if isinstance(stmt, THIRExprStmt):
        return [f"{pad}{_held(stmt.held)}{_expr(stmt.expr)}"]
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
        if stmt.raise_expr is None and stmt.cpp_type is None:
            return [f"{pad}raise [bare]"]
        obj = (_expr(stmt.raise_expr) if stmt.raise_expr is not None
               else f"{stmt.cpp_type}{{}}")
        via = " [virtual]" if stmt.via_virtual else ""
        tier = " [return_tier]" if stmt.return_tier else ""
        return [f"{pad}raise {obj}{via}{tier}"]
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
        return [f"{pad}{_held(stmt.held)}{_expr(stmt.target)} = {_expr(stmt.value)}"]
    if isinstance(stmt, THIRSliceAssign):
        lo = "" if stmt.lower is None else _expr(stmt.lower)
        hi = "" if stmt.upper is None else _expr(stmt.upper)
        return [f"{pad}{_expr(stmt.receiver)}[{lo}:{hi}] = "
                f"{_expr(stmt.value)} [{stmt.native_name}]"]
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
        const = _loop_binding_tag(stmt.binding)
        fsrc = ("" if stmt.frame_src_field is None
                else f" [frame_src={stmt.frame_src_field.name}]")
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
    if isinstance(stmt, THIRCompInsert):
        if stmt.kind == "dict":
            first = " [key_first]" if stmt.key_first else ""
            return [f"{pad}comp_insert{first} {_expr(stmt.key)}: "
                    f"{_expr(stmt.value)}"]
        return [f"{pad}comp_insert {_expr(stmt.element)}"]
    if type(stmt) in _UNDUMPED:
        return [f"{pad}<{type(stmt).__name__}>"]
    raise AssertionError(
        f"--dump-thir has no arm for {type(stmt).__name__}; add one in "
        f"dump.py (or list it in _UNDUMPED with a reason)")


def _loop_binding_tag(rec) -> str:
    kind = rec.loop_binding if rec is not None else None
    return f" [{kind.name.lower()}]" if kind is not None else ""


def _extend_orelse(lines: list[str], orelse, depth: int) -> None:
    if not orelse:
        return
    lines.append("  " * depth + "else:")
    for s in orelse:
        lines.extend(_stmt_lines(s, depth + 1))


def _signature_facts(signature: THIRCallableSignature) -> str:
    passings = ", ".join(
        f"{_ty(t)}: {'?' if signature.passings is None else signature.passings[i].value}"
        for i, t in enumerate(signature.param_types))
    result = ("?" if signature.return_representation is None
              else signature.return_representation.value)
    follows = ", follows receiver" if signature.result_follows_receiver else ""
    storage = ", storage reference" if signature.storage_reference_result else ""
    return f"({passings}) -> {result}{follows}{storage}"


def _callee_name(identity: THIRFunctionIdentity) -> str:
    """A user callee's module-qualified name; a method's names its owning
    record (`__main__.Counter.bump`), a property accessor its role too
    (`__main__.Counter.count.fget`)."""
    if identity.owner is not None:
        accessor = "" if identity.accessor is None else f".{identity.accessor}"
        return f"{identity.owner}.{identity.name}{accessor}"
    return f"{identity.module}.{identity.name}"


def _callee_facts(e: THIRCall | THIRMethodCall) -> str | None:
    """The callee fact a call carries: a user callee's identity, or a free
    stub's declared contract (`none` when it declares nothing), each with
    how its parameters pass and what its result is. A native method stub
    is not listed."""
    if e.resolved_callee is not None:
        return f"callee {_callee_name(e.resolved_callee.identity)}{_signature_facts(e.resolved_callee.signature)}"
    if isinstance(e, THIRCall) and e.stub_callee is not None:
        stub = e.stub_callee
        contract = "none" if stub.contract is None else stub.contract.value
        return f"stub {stub.identity.qualified_name}{_signature_facts(stub.signature)}, {contract}"
    return None


def _walk(node: object) -> Iterable[object]:
    yield node
    for child in _iter_children(node):
        yield from _walk(child)


def _callee_lines(fn: 'THIRFunction') -> list[str]:
    """The callee facts of a body, listed after it rather than inline: the
    statement renders stay the arguments' lowering, which the committed sweep
    tables fingerprint."""
    lines = []
    if fn.signature is not None:
        identity = fn.resolved_callee.identity if fn.resolved_callee is not None else None
        owner = (None if identity is None else identity.owner
                 if identity.accessor is None or identity.owner is None else _callee_name(identity))
        lines.append(f"  signature{'' if owner is None else ' ' + owner}{_signature_facts(fn.signature)}")
    facts = [((node.callee or "<computed>") if isinstance(node, THIRCall) else node.method_cpp, fact)
             for stmt in fn.body for node in _walk(stmt)
             if isinstance(node, (THIRCall, THIRMethodCall)) and (fact := _callee_facts(node)) is not None]
    if facts:
        lines.append("  callees:")
        lines.extend(f"    [{i}] {name}: {fact}" for i, (name, fact) in enumerate(facts))
    return lines


def _function_lines(fn: 'THIRFunction') -> list[str]:
    params = ", ".join(f"{p.name}: {_ty(p.type)}" for p in fn.params)
    lines = [f"fn {fn.name}({params}) -> {_ty(fn.return_type)}:"]
    for s in fn.body:
        lines.extend(_stmt_lines(s, 1))
    return lines


def _leaf_lines(leaves: 'THIRResumableLeaves | None', slot,
                depth: int) -> list[str]:
    """What THIR lowered at one frame slot, rendered by the slot's role."""
    pad = "  " * depth
    role = slot.role
    if leaves is None or slot not in leaves.leaves:
        return [f"{pad}{role.value}: <no leaf>"]
    leaf = leaves.leaves[slot]
    if role is LeafRole.AWAIT_ARGS:
        return [f"{pad}await_args: ({_exprs(leaf)})"]
    if role is LeafRole.LOOP_STEP:
        return [f"{pad}loop_step: lent"]
    if role is LeafRole.NESTED_DEF:
        lines = [f"{pad}nested_def {slot.source.func.name}:"]
        for st in leaf:
            lines.extend(_stmt_lines(st, depth + 1))
        return lines
    if isinstance(leaf, THIRStmt):
        return _stmt_lines(leaf, depth)
    flags = "".join(f" [{tag}]" for tag, table in (
        ("pointer", leaves.pointer_sources),
        ("unwrapped", leaves.unwrapped_sources),
        ("owned-erased", leaves.owned_erased_operands)) if slot in table)
    return [f"{pad}{role.value}: {_expr(leaf)}{flags}"]


def _resume_extraction_line(var: str, alias: str, sub) -> str:
    """One narrowing subject's resume-case re-extraction facts."""
    facts = [f for f, on in (
        ("receiver", sub.receiver), ("cast-const", sub.cast_const),
        ("indirect", sub.indirect), ("slot", sub.slot_backed),
        ("param-const", sub.param_const), ("ptr-variant", sub.ptr_variant),
        ("ptr-const", sub.ptr_const)) if on]
    decl = _ty(sub.declared) if sub.declared is not None else "?"
    storage = "" if sub.storage == var else f" via {sub.storage}"
    tail = (" " + " ".join(facts)) if facts else ""
    inner = ("" if sub.source_inner is None
             else f" source-inner={_ty(sub.source_inner)}")
    return (f"  resume extract {var} -> {alias}: {decl}{storage}{tail}"
            f" cast-arg={sub.cast_arg}{inner}")


def _region_label(r: object) -> str:
    """One region of a block's stack, by operation."""
    name = type(r).__name__.removeprefix("THIR").removesuffix("Region").lower()
    if isinstance(r, THIRTryRegion):
        if r.finally_helper_name is not None:
            name = f"{name}({r.finally_helper_name})"
        if r.finally_entry_bb is not None:
            name += f" finally b{r.finally_entry_bb}"
        return name
    if isinstance(r, THIRFinallyRegion) and r.finally_exit_bb is not None:
        return f"{name} exit b{r.finally_exit_bb}"
    if isinstance(r, THIRExceptRegion) and r.parent_finally_entry_bb is not None:
        return f"{name} finally b{r.parent_finally_entry_bb}"
    if isinstance(r, THIRWithRegion):
        return f"{name}(ctx {r.ctx_n}) post b{r.post_with_bb}"
    if isinstance(r, THIRLoopSourceRegion):
        return f"{name}(uid {r.uid})"
    return name


def _terminator_label(t: object) -> str:
    """A block's terminator, by operation, with the blocks it reaches."""
    if isinstance(t, THIRFall):
        return f"fall b{t.next_bb}"
    if isinstance(t, THIRBranch):
        return f"branch b{t.then_bb} b{t.else_bb}"
    if isinstance(t, THIRSuspend):
        kind = "yield" if isinstance(t.payload, THIRYieldSuspension) else (
            f"await {t.payload.mode.name.lower()} {t.payload.kind.name.lower()}")
        return f"suspend#{t.suspension_index} {kind} -> b{t.resume_bb}"
    if isinstance(t, THIRForAdvance):
        return f"for_advance uid {t.uid} b{t.has_value_bb} b{t.exhausted_bb}"
    if isinstance(t, THIRMatchDispatch):
        arms = " ".join(f"b{b}" for b in t.arm_bbs)
        join = "" if t.join_bb is None else f" join b{t.join_bb}"
        return f"match_dispatch {arms}{join}"
    if isinstance(t, THIRFrameReturn):
        return "return"
    if isinstance(t, THIRFrameRaise):
        return "raise"
    if isinstance(t, THIRFallOff):
        return "fall off"
    return "(open: no edge reaches it)"


def _block_stmt_label(s: object) -> str:
    """A block statement: a pseudo-statement by operation, a source leaf by
    its statement kind (its lowering follows it)."""
    if isinstance(s, THIRForIterSetup):
        return f"for_iter_setup uid {s.uid}"
    if isinstance(s, THIRWithEnter):
        return f"with_enter ctx {s.ctx_n}"
    if isinstance(s, THIRAsyncWithSetup):
        return f"async_with_setup ctx {s.ctx_n}"
    if isinstance(s, THIRFinallyExit):
        return f"finally_exit {s.captured_exc_field}"
    return f"leaf {type(s.source).__name__.removeprefix('Tpy')}"


def _frame_lines(frame: 'THIRResumableFrame',
                 leaves: 'THIRResumableLeaves | None' = None) -> list[str]:
    """A published frame with its leaves: its captures, locals and
    placement, then its coarse CFG in block order -- each block's region
    stack, statements and terminator, each followed by what THIR lowered at
    its slots -- the finally helpers and nested defs, and the frames it
    embeds by value."""
    header = body_header(leaves if leaves is not None else frame)
    lines = [f"frame {header.name} [{frame.shape.value}]:"]
    if header.signature is not None:
        lines.append(f"  signature{_signature_facts(header.signature)}")
    if header.receiver is not None:
        const = " const" if header.receiver.readonly else ""
        lines.append(f"  receiver {_ty(header.receiver.type)}{const}")
    for p, capture in zip(frame.params, frame.captures):
        const = " const" if p.name in frame.const_params else ""
        lines.append(f"  param {p.name}: {_ty(p.type)} "
                     f"capture={capture.name}{const}")
    for loc in frame.locals:
        pl = loc.placement
        extra = "".join((" const" if pl.const else "",
                         f" payload={pl.payload}" if pl.payload else "",
                         " one-shot" if loc.one_shot else ""))
        ty = _ty(loc.type) if loc.type is not None else "?"
        lines.append(f"  local {loc.name}: {ty} placement={pl.kind.name}{extra}")
    if leaves is not None:
        for var in sorted(leaves.resume_extractions):
            lines.append(_resume_extraction_line(
                var, leaves.narrow_aliases.get(var, "?"),
                leaves.resume_extractions[var]))
    entries = set(frame.case_entries)
    for bid in sorted(frame.blocks):
        block = frame.blocks[bid]
        flags = [f for f, on in (("entry", bid == frame.entry_block),
                                 ("case", bid in entries)) if on]
        head = f"  b{bid}" + (f" [{', '.join(flags)}]" if flags else "")
        if block.region_stack:
            head += " in " + " > ".join(_region_label(r) for r in block.region_stack)
        lines.append(head + ":")
        for s in block.stmts:
            lines.append(f"    {_block_stmt_label(s)}")
            for slot, _role in stmt_slots(s):
                lines.extend(_leaf_lines(leaves, slot, 3))
        lines.append(f"    -> {_terminator_label(block.terminator)}")
        for slot, _role in terminator_slots(block.terminator):
            lines.extend(_leaf_lines(leaves, slot, 3))
    for region, by_handler in frame.handler_entries.items():
        for handler, bid in by_handler.items():
            caught = handler.exception_type or "*"
            lines.append(f"  handler {caught} of {_region_label(region)}"
                         f" enters b{bid}")
    for name, helper in frame.finally_helpers:
        lines.append(f"  finally helper {name}:")
        for slot in helper:
            lines.extend(_leaf_lines(leaves, slot, 2))
    for slot in frame.nested_defs:
        lines.extend(_leaf_lines(leaves, slot, 1))
    for h in (*frame.for_fields, *frame.with_fields, *frame.ptr_slot_fields,
              *frame.try_finally_fields):
        loop_line = (h.loop.stmt.loc.line
                     if h.loop is not None and h.loop.stmt.loc is not None
                     else "?")
        owner_facts = ""
        if h.loop is not None:
            strategy = (h.loop.info.strategy if h.loop.info is not None
                        else "whole")
            bound = ",".join(sorted(h.loop.bound_names)) or "-"
            owner_facts = (f" of loop@{loop_line} {strategy}"
                           f" alias={h.loop.alias or '-'} binds={bound}")
        extra = "".join((" borrowed" if h.borrowed else "",
                         f" for {h.local}" if h.local is not None else "",
                         owner_facts,
                         " drops" if h.drop_runs_user_code else ""))
        lines.append(f"  holder {h.role.value} {h.name}: {h.cpp_type}{extra}")
    for name, owner in frame.embedding_edges:
        lines.append(f"  embeds {owner + '.' if owner else ''}{name}")
    return lines


def _declaring_owner(e: THIRFieldAccess) -> str:
    """` <Owner::name>` when the field's identity is declared by a record
    other than the receiver's type (an inherited member)."""
    identity = e.field_identity
    if identity is None or identity.owner == unwrap_readonly(unwrap_ref_type(e.receiver.result_type)):
        return ""
    return f" <{identity.owner.name}::{identity.name}>"


def _constructor_lines(name: str, ctor: 'THIRConstructor') -> list[str]:
    lines = [f"ctor {name}:"]
    for base in ctor.base_inits:
        identity = "" if base.base is None else f" <{base.base.name}>"
        lines.append(f"  base {base.base_cpp}({_exprs(base.args)}){identity}")
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
            lines.extend(_callee_lines(top))
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
            lines.extend(_callee_lines(ctx.thir_functions[func]))
        elif stub_fns and all(fn is not None for fn in stub_fns):
            for fn in stub_fns:
                lines.extend(_function_lines(fn))
                lines.extend(_callee_lines(fn))
                lines.append("")
            lines.pop()
        elif ctx.thir_resumables.get(func) is not None:
            leaves = ctx.thir_resumables[func]
            lines.extend(_frame_lines(leaves.frame, leaves))
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
