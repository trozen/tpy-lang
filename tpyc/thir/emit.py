"""THIR -> C++ emission for the value-scalar slice.

`emit_thir_body` writes a function body's C++ from THIR alone -- no
SemanticAnalyzer, no CodeGenContext. It reuses the existing analyzer-free leaf
helpers (`escape_cpp_name`, `expand_cpp_template`, `TpyType.to_cpp`) so its
output matches the AST-driven path byte-for-byte. As the slice grows this is
where the THIR codegen backend accretes.

Source comments are rendered through a `CommentSink` supplied by the codegen
seam (the stateless `ctx` comment helpers); the dump/tests pass the no-op
default so emission stays decoupled from the analyzer.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TextIO

from ..codegen_cpp.context import (
    INDENT, cpp_bytes_literal_owned, cpp_bytes_literal_span,
    cpp_string_literal_expr, escape_cpp_char, escape_cpp_name,
    escape_cpp_string, expand_cpp_template, loop_var_binding,
    qualify_native_name,
)
from ..codegen_cpp.forms import LocalBinding, is_plain_nonvalue
from ..type_def_registry import (
    is_array, is_bytes_type, is_bytes_view_type, is_dict,
    is_float32_type, is_list,
    is_set, is_str_type, is_string_type, view_to_owned_conv,
)
from ..typesys import (OptionalType, TpyType, TupleType, TypeParamRef,
                       UnionType, unwrap_qualifiers, view_family_for_type)
from .nodes import (
    Form,
    PrintForm,
    TruthinessMode,
    THIRArgTemp,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRChainedCompareStmtExpr,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRComprehension,
    THIRGenExpr,
    THIRClassConstant,
    THIRCoerce,
    THIRConstructor,
    THIRContainerLiteral,
    THIRContinue,
    THIRDelVar,
    THIRCtorCall,
    THIREnumMember,
    THIREnumWrap,
    THIRExpr,
    THIRExprStmt,
    THIRFieldAccess,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRFunction,
    THIRIf,
    THIRIfExpr,
    THIRIsNone,
    THIRMembership,
    THIRIsinstance,
    THIRLiteral,
    THIRMatch,
    THIRMatchBinding,
    THIRMethodCall,
    THIRModuleVar,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRNestedDef,
    THIRNarrowedRead,
    THIRNoOpStmt,
    THIROptionalPtrArg,
    THIRParamCopy,
    THIRPrint,
    THIRPrintArg,
    THIRRaise,
    THIRReturn,
    THIRSetItem,
    THIRSelf,
    THIRStmt,
    THIRFrameSlotWrite,
    THIRStrAppend,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRTry,
    THIRTupleLiteral,
    THIRTupleUnpack,
    THIRTruthy,
    THIROptViewArg,
    THIRUnaryNot,
    THIRUnionArgLift,
    THIRVarDecl,
    THIRWhile,
    THIRWith,
    WithTargetArm,
)
from .faces import witness as _witness


class THIRCodeGenError(Exception):
    """A THIR node the slice's emitter does not handle reached emission.

    Lowering should make this unreachable; it firing means lowering and the
    emitter disagree on the supported set.
    """


class CommentSink:
    """Renders the source comments the AST path emits before/around statements.

    The codegen seam supplies a subclass backed by the stateless `ctx` comment
    helpers; the no-op default keeps the dump/test paths analyzer-free.
    """

    def stmt(self, out: TextIO, loc, indent: str) -> None:
        ...

    def elif_(self, out: TextIO, loc, indent: str) -> None:
        ...

    def else_(self, out: TextIO, else_body, indent: str) -> None:
        ...

    def trailing(self, out: TextIO, body, indent: str) -> None:
        ...


_NO_COMMENTS = CommentSink()


class TempSink:
    """Allocates `__tmp_N` names for THIRArgTemp and renders the pending
    declarations at the statement flush point -- the emit-side seam of the
    AST path's `TempState`. This default implementation is the standalone /
    unit-test sink: a fresh module-local counter starting at `__tmp_1`, with
    `TempState._render`'s exact decl spelling. The codegen seam supplies
    `CtxTempSink` instead, backed by the module-cumulative `ctx.temps`
    counter shared with AST-emitted bodies (interleaved THIR/AST numbering
    must stay continuous)."""

    def __init__(self) -> None:
        self._counter = 0
        self._pending: list[tuple[str, str, str, bool]] = []

    def create(self, cpp_type: str, init_expr: str, *,
               brace_init: bool = False) -> str:
        self._counter += 1
        name = f"__tmp_{self._counter}"
        self._pending.append((name, cpp_type, init_expr, brace_init))
        return name

    def flush(self, out: TextIO, indent: str) -> None:
        for name, cpp_type, init_expr, brace_init in self._pending:
            if brace_init:
                out.write(f"{indent}{cpp_type} {name}{{{init_expr}}};\n")
            else:
                out.write(f"{indent}{cpp_type} {name} = {init_expr};\n")
        self._pending.clear()


class CtxTempSink(TempSink):
    """TempSink backed by a CodeGenContext's `TempState` (duck-typed on `ctx`
    like CtxCommentSink, keeping emit.py free of a CodeGenContext import).
    `create` delegates to `create_typed` -- the type is already rendered at
    lowering, so both AST arms (`create`'s param-type render and
    `create_typed`'s explicit string) reduce to the same pending row -- and
    both draw from the live module-cumulative `__tmp_N` counter, so a THIR
    body's temps keep every later AST body's numbering unshifted."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def create(self, cpp_type: str, init_expr: str, *,
               brace_init: bool = False) -> str:
        return self._ctx.temps.create_typed(cpp_type, init_expr,
                                            brace_init=brace_init)

    def flush(self, out: TextIO, indent: str) -> None:
        self._ctx.temps.flush(out, indent)


class ModuleCounter:
    """Module-cumulative int sink for a hidden-name numbering stream
    (`__ctx_N`, `__after_else_N`, ...). Unlike the per-function counters
    below, these streams are never reset (like `__tmp_N`) and are shared
    with AST-emitted bodies -- the codegen seam passes `CtxCounter` so
    interleaved THIR/AST numbering stays continuous. This default is the
    standalone / unit-test sink (first id is 1, a fresh module's
    numbering)."""

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n


class CtxCounter(ModuleCounter):
    """ModuleCounter backed by a named int attribute of the live
    CodeGenContext (duck-typed on `ctx` like CtxTempSink, keeping emit.py
    free of a CodeGenContext import)."""

    def __init__(self, ctx, attr: str) -> None:
        self._ctx = ctx
        self._attr = attr

    def next(self) -> int:
        n = getattr(self._ctx, self._attr) + 1
        setattr(self._ctx, self._attr, n)
        return n


@dataclass
class _FinallyFrame:
    """One enclosing cleanup layer during body emission -- the emit-side
    `FinallyContext`. Two arms: a `with` layer renders the fixed
    `__ctx_N.__exit__(...)` call (`ctx_n`/`exc_null_arg`); a try/finally
    layer re-emits its lowered finally body (`stmts`) at every exit site,
    counters advancing per copy like the AST's repeated `gen_stmt` runs.
    `terminates` is the AST's last-stmt raise/return fact -- a terminating
    frame stops the chain walk and the caller suppresses its trailing exit
    statement (with frames never terminate). `loop_depth` is the live
    loop-nesting count at push (`len(ctx.loop_else_labels)` in the AST --
    every loop appends an entry, labeled or not), so break/continue walk
    only frames pushed inside the innermost loop body."""
    loop_depth: int
    ctx_n: int | None = None
    exc_null_arg: str = "{}"
    stmts: 'tuple[THIRStmt, ...] | None' = None
    terminates: bool = False


@dataclass
class _EmitState:
    """Per-function emit state. `iter_counter` reproduces `ctx.iter_counter`:
    in the eligible slice only range-`for` loops bump it, and it resets per
    function, so a counter seeded at 0 here and bumped once per loop (pre-order)
    matches the AST path's `__start_N`/`__stop_N` numbering exactly.

    `slot_counter` reproduces `ctx.slots` for F2d rebind-slot pointer-locals:
    within the eligible slice only a REBIND_SLOT decl bumps it (every other
    `__slot_N` consumer -- unions, tuples, @dynamic, walrus -- is gated out), and
    it pre-increments per allocation just like `SlotState.next_slot`, so the
    `__slot_N` numbering matches the AST path. `rebind_slots` maps a rebind-slot
    local's name to its optional rebind slot N (allocated at the decl, read at
    each reseat) -- the analog of `ctx.rebind_slots`.

    `temps` is the `__tmp_N` sink THIRArgTemp renders through, flushed before
    the enclosing statement line (after its source comment, mirroring the AST's
    single flush point in `gen_stmt`). Unlike the counters above it is NOT
    per-function: the seam passes a CtxTempSink so the numbering stays
    module-cumulative across interleaved THIR/AST bodies."""
    comments: CommentSink
    temps: TempSink = field(default_factory=TempSink)
    # `with_counter` numbers `__ctx_N` (ctx attr `with_counter`); `try_counter`
    # numbers `__after_else_N` throw-tier else labels (ctx attr
    # `try_except_counter` -- its other consumers, `__try_tmp_N`/`__er_N`
    # error_return unwraps and the return tier's `__except_N`, are all
    # gate-rejected, so within a routed body only the else label draws).
    with_counter: ModuleCounter = field(default_factory=ModuleCounter)
    try_counter: ModuleCounter = field(default_factory=ModuleCounter)
    return_cpp: 'str | None' = None
    iter_counter: int = 0
    slot_counter: int = 0
    unpack_counter: int = 0
    rebind_slots: dict[str, int] = field(default_factory=dict)
    # Enclosing `with` layers, innermost last -- return/break/continue walk it
    # to render the inline `__exit__` chain (the AST's `ctx.finally_stack`);
    # `loop_depth` mirrors `len(ctx.loop_else_labels)` (bumped around every
    # loop body) for the break/continue frame boundary. `return_cpp` is the
    # signature's return spelling (`ctx.current_return_cpp`), read only by the
    # finally-return temp.
    finally_frames: list[_FinallyFrame] = field(default_factory=list)
    loop_depth: int = 0
    # Per-function match-switch state, mirroring reset_scope's fields:
    # `match_counter` numbers `__match_subject_N` (one bump per match, the
    # iter_counter precedent -- NOT a module-cumulative sink); `switch_depth`
    # mirrors `ctx.match_switch_depth` (bumped around every emitted switch,
    # zeroed around loop bodies); `loop_break_labels` mirrors
    # `ctx.loop_break_labels` -- one ""-slot per enclosing loop, lazily
    # filled with `__loop_break_{iter_counter}` by a break that must escape
    # an intervening switch, the label emitted after the loop's close brace.
    # `loop_else_labels` mirrors `ctx.loop_else_labels` -- one slot per
    # enclosing loop, `__after_else_{iter_counter}` when the loop has an
    # else clause (allocated at loop entry, BEFORE the loop draws its own
    # index) else "": a break targets the innermost slot's label so the
    # else block is skipped.
    match_counter: int = 0
    switch_depth: int = 0
    loop_break_labels: list[str] = field(default_factory=list)
    loop_else_labels: list[str] = field(default_factory=list)
    # The current statement's indent level, stamped by _emit_stmt before its
    # arms render expressions: the comprehension stmt-expr is the one
    # multi-line EXPRESSION render, and its inner lines indent relative to
    # the enclosing statement (the AST reads ctx.indent_level the same way).
    stmt_indent_level: int = 0

    def next_loop_index(self) -> int:
        n = self.iter_counter
        self.iter_counter += 1
        return n

    def next_slot(self) -> int:
        self.slot_counter += 1  # pre-increment: first slot is __slot_1
        return self.slot_counter

    def next_unpack(self) -> int:
        # Reproduces ctx.unpack_counter: per-function, pre-incremented (first
        # is __tup_1); its other consumers are gate-rejected shapes.
        self.unpack_counter += 1
        return self.unpack_counter


class CtxCommentSink(CommentSink):
    """CommentSink backed by a CodeGenContext's stateless comment helpers.

    Duck-typed on `ctx` so emit.py stays free of a CodeGenContext import.
    """

    def __init__(self, ctx):
        self._ctx = ctx

    def stmt(self, out: TextIO, loc, indent: str) -> None:
        self._ctx.emit_inline_comments(out, loc, indent)
        self._ctx.emit_source_comment(out, loc, indent)

    def elif_(self, out: TextIO, loc, indent: str) -> None:
        # An elif condition gets only its source line (the AST path emits no
        # inline comments for a flattened elif).
        self._ctx.emit_source_comment(out, loc, indent)

    def else_(self, out: TextIO, else_body, indent: str) -> None:
        self._ctx.emit_else_comment(out, else_body, indent)

    def trailing(self, out: TextIO, body, indent: str) -> None:
        self._ctx.emit_block_trailing_comments(out, body, indent)


# --- expressions ---


def _emit_literal(lit: THIRLiteral) -> str:
    v = lit.value
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        # Positional: a None into a value-union slot (decl init / reassign /
        # return) is the monostate member; into a storage-form Optional slot
        # (field write / storage-Optional return) `std::nullopt`; a borrow/
        # value-form None (pointer-repr slot) `nullptr`. Set by lowering.
        if isinstance(lit.result_type, UnionType):
            return "std::monostate{}"
        return "std::nullopt" if lit.form is Form.STORAGE else "nullptr"
    if isinstance(v, float):
        # Matches the gen_expr float-literal arm: repr() is the shortest
        # round-tripping form and a valid C++ double literal; a Float32-typed
        # literal (retyped at lowering from its float_literal_to_float32
        # coerce) takes the `f` suffix. inf/nan never reach here -- the
        # Lowering admits finite literals only.
        rendered = repr(v)
        if is_float32_type(lit.result_type):
            return rendered + "f"
        return rendered
    if isinstance(v, int):
        return lit.int_cpp if lit.int_cpp is not None else str(v)
    return str(v)


def _emit_chained_compare_stmtexpr(e: THIRChainedCompareStmtExpr,
                                   state: _EmitState) -> str:
    # Mirrors _gen_chained_compare_lambda: bind each non-simple operand to an
    # `auto&& _cmpI` temp, then interleave bindings with the left-folded `&&`
    # chain so operands after a failed pair never evaluate. Each pair renders
    # `_gen_comparison_pair` (bare op + per-side `{0}` casts) over the operand
    # REPRs (temp name or inline render).
    n = len(e.ops)
    reprs: list[str] = []
    binds: list[str | None] = []
    for i in range(n + 1):
        code = _emit_expr(e.inits[i], state)
        if e.bound[i]:
            reprs.append(f"_cmp{i}")
            binds.append(f"auto&& _cmp{i} = {code};")
        else:
            reprs.append(code)
            binds.append(None)

    def render_pair(i: int, left: str, right: str) -> str:
        if e.left_casts[i] is not None:
            left = e.left_casts[i].format(left)
        if e.right_casts[i] is not None:
            right = e.right_casts[i].format(right)
        return f"({left} {e.ops[i]} {right})"

    inner = render_pair(n - 1, reprs[n - 1], reprs[n])
    for i in range(n - 2, 0, -1):
        inner = ("({ " + binds[i + 1] + " "
                 + render_pair(i, reprs[i], reprs[i + 1])
                 + " && " + inner + "; })")
    outer_parts = [b for b in binds[:2] if b]
    outer_parts.append(render_pair(0, reprs[0], reprs[1]) + " && " + inner + ";")
    return "({ " + " ".join(outer_parts) + " })"


def _emit_binop(e: THIRBinOp, state: _EmitState) -> str:
    # Mirrors ExpressionGenerator._gen_binop_from_result: apply the operand
    # wrappers, expand the operator's cpp_template, swap the checked div/mod
    # helper when the divisor is proven non-zero, and paren-wrap the result.
    # Comparisons reuse this path (their dunder carries a `{self} OP {0}`
    # template), so the same code emits both arithmetic and comparison binops.
    left, right = _emit_expr(e.left, state), _emit_expr(e.right, state)
    # Post-generation operand casts (int-enum underlying / mixed BigInt-float),
    # applied before the wrapper/template expansion like the AST's.
    if e.left_cast is not None:
        left = e.left_cast.format(left)
    if e.right_cast is not None:
        right = e.right_cast.format(right)
    rb = e.resolved
    if rb is None:
        # Derived comparison (`<= > >= !=`) or logical `&&`/`||` (incl. the
        # chained-compare pair fold): bare C++ operator, no template.
        return f"({left} {e.op} {right})" if e.paren_wrap else f"{left} {e.op} {right}"
    wl = rb.left_wrapper.replace("{self}", left).replace("{expr}", left)
    wr = rb.right_wrapper.replace("{self}", right).replace("{expr}", right)
    if rb.is_reverse:
        wl, wr = wr, wl
    if rb.method.cpp_template:
        result = expand_cpp_template(rb.method.cpp_template, wl, wr)
    else:
        # A @native free-function dunder (bytes `==` -> `::tpy::bytes_eq`):
        # gen_call_from_fi's native arm with the receiver prepended. The gate
        # admits a template-less rb only in this shape.
        result = (f"{qualify_native_name(rb.method.native_name)}"
                  f"({wl}, {wr})")
    if e.divisor_non_zero:
        result = result.replace("div_check", "div_floor").replace("mod_check", "mod_floor")
    return f"({result})" if e.paren_wrap else result


def _emit_call(e: THIRCall, state: _EmitState) -> str:
    if e.cpp_template is not None:
        # A scalar type-constructor call: expand the (sema-substituted,
        # positional-only) __init__ template over the args with no receiver --
        # gen_call_from_fi's cpp_template arm for a free call.
        return expand_cpp_template(e.cpp_template, None,
                                   *[_emit_expr(a, state) for a in e.args])
    args = ", ".join(_emit_expr(a, state) for a in e.args)
    if e.native_name is not None:
        # A @native free-function builtin (e.g. `len(c)` -> `::tpy::__len__(c)`):
        # dispatch on the resolved symbol, mirroring gen_call_from_fi's native arm.
        return f"{qualify_native_name(e.native_name)}({args})"
    # A generic TPy callee's explicit template-arg list (pre-rendered at
    # lowering): `callee<T1, T2>(args)` over the plain / imported spelling.
    targs = (f"<{', '.join(e.template_args_cpp)}>"
             if e.template_args_cpp else "")
    if e.callee_cpp is not None:
        # A cross-module callee: the pre-rendered absolute spelling
        # (imported_free_callee_cpp, shared with the AST emit).
        return f"{e.callee_cpp}{targs}({args})"
    return f"{escape_cpp_name(e.callee)}{targs}({args})"


def _emit_union_arg_lift(e: THIRUnionArgLift, state: _EmitState) -> str:
    # Mirrors _gen_union_arg's temp-free pointer-variant arms: the monostate
    # member for a None literal, the address-of lift for a member-typed name
    # (deref prepends the pointer-local/receiver `(*...)`, gen_expr_deref's
    # indirect render), and the mutable->const conversion for an already-union
    # name into a deep-const slot (const_wrap). variant_cpp was fixed at
    # lowering (const-pointee spelling for a deep-const slot).
    if e.value is None:
        return f"{e.variant_cpp}{{std::monostate{{}}}}"
    inner = _emit_expr(e.value, state)
    if e.temp_cpp is not None:
        # The rvalue branch: hoist the member-typed ctor into a named temp at
        # the statement flush and lift its address -- `pv{&__tmp_N}`, no
        # parens (TempState's temp-arm spelling, unlike the name lift below).
        name = state.temps.create(e.temp_cpp, inner)
        return f"{e.variant_cpp}{{&{name}}}"
    if e.deref:
        inner = f"(*{inner})"
    if e.const_wrap:
        return f"::tpy::ptr_variant_to_const<{e.variant_cpp}>({inner})"
    return f"{e.variant_cpp}{{&({inner})}}"


def _emit_ctor_call(e: THIRCtorCall, state: _EmitState) -> str:
    # _gen_call's record-branch tail: the RAW source name (same-module) or
    # the qualified `::ns::Name` spelling (imported record), decided at
    # lowering, over the lowering-admitted args.
    return f"{e.type_cpp}({', '.join(_emit_expr(a, state) for a in e.args)})"


def _emit_method_call(e: THIRMethodCall, state: _EmitState) -> str:
    # Mirrors gen_call_from_fi's three dispatch arms for a receiver call, in the
    # same order: cpp_template expansion, @native free-function symbol (receiver
    # prepended), plain member call. The member accessor is `->` only for a
    # user-record pointer-local receiver (`is_arrow`, the _gen_method_call
    # indirect-name arm); container receivers are pinned to bare names.
    recv = _emit_expr(e.receiver, state)
    args = [_emit_expr(a, state) for a in e.args]
    if e.cpp_template is not None:
        return expand_cpp_template(e.cpp_template, recv, *args)
    if e.native_function_name is not None:
        return f"{qualify_native_name(e.native_function_name)}({', '.join([recv, *args])})"
    if e.deref_check:
        # Unproven pointer-repr Optional receiver: null-check the (already
        # `T*`) receiver before the `.` member call -- _gen_method_call's
        # runtime-check arm (type args are gate-excluded, so no {method_targs}).
        return f"::tpy::deref_check({recv}).{e.method_cpp}({', '.join(args)})"
    return f"{recv}{'->' if e.is_arrow else '.'}{e.method_cpp}({', '.join(args)})"


def _emit_comprehension(e: 'THIRComprehension', state: _EmitState) -> str:
    """The GCC stmt-expr comprehension render -- `_gen_comprehension_iife`'s
    mirror for the C1+C2 slice. Inner lines indent relative to the enclosing
    statement (`state.stmt_indent_level`, the AST's `ctx.indent_level`); the
    first line is bare `({` (it renders inline after `= `). NB the range arm
    draws one loop index PER non-literal bound (the comprehension emitter's
    scheme -- unlike the statement range-for's single draw), start before
    stop in source order."""
    stmt_ind = INDENT * state.stmt_indent_level
    ind1 = stmt_ind + INDENT
    ind2 = ind1 + INDENT
    ind3 = ind2 + INDENT
    cpp_var = escape_cpp_name(e.var)
    if e.loop == "array_range":
        # The array_from_index RANGE arm (_gen_array_comprehension): sema
        # proved literal bounds, so start/step inline as index arithmetic
        # inside the per-index lambda; no `({` prelude, no reserve, and the
        # element renders directly after the binding (this arm admits no
        # temp-producing elements).
        n = state.next_loop_index()
        idx = f"{e.counter_cpp}(__i_{n})"
        if e.range_start is None:
            var_init = idx
        elif e.range_step is None:
            var_init = f"{_emit_expr(e.range_start, state)} + {idx}"
        else:
            var_init = (f"{_emit_expr(e.range_start, state)} + {idx} * "
                        f"({_emit_expr(e.range_step, state)})")
        buf = io.StringIO()
        buf.write(f"::tpy::array_from_index<{e.array_elem_cpp}, "
                  f"{e.array_size_cpp}>("
                  f"[&](std::size_t __i_{n}) -> {e.array_elem_cpp} {{\n")
        buf.write(f"{ind1}{e.counter_cpp} {cpp_var} = {var_init};\n")
        buf.write(f"{ind1}return {_emit_expr(e.element, state)};\n")
        buf.write(f"{stmt_ind}}})")
        return buf.getvalue()
    skip_reserve = e.kind != "list"
    buf = io.StringIO()
    buf.write("({\n")
    buf.write(f"{ind1}{e.container_cpp} __result;\n")
    if e.loop == "range":
        cpp_elem = e.counter_cpp
        if e.range_start is not None:
            start_cpp = _emit_expr(e.range_start, state)
            if e.range_start_literal:
                start_var = start_cpp
            else:
                n = state.next_loop_index()
                start_var = f"__start_{n}"
                buf.write(f"{ind1}const {cpp_elem} {start_var} = {start_cpp};\n")
        stop_cpp = _emit_expr(e.range_stop, state)
        if e.range_stop_literal:
            stop_var = stop_cpp
        else:
            n = state.next_loop_index()
            stop_var = f"__stop_{n}"
            buf.write(f"{ind1}const {cpp_elem} {stop_var} = {stop_cpp};\n")
        if e.range_start is None:
            if not skip_reserve:
                if e.counter_bigint:
                    buf.write(f"{ind1}{{ size_t __sz; if ({stop_var}"
                              f".to_size_checked(__sz)) __result.reserve(__sz); }}\n")
                else:
                    buf.write(f"{ind1}if ({stop_var} > 0) __result.reserve("
                              f"static_cast<size_t>({stop_var}));\n")
            buf.write(f"{ind1}for ({cpp_elem} {cpp_var} = 0; "
                      f"{cpp_var} < {stop_var}; ++{cpp_var}) {{\n")
        else:
            if not skip_reserve:
                if e.counter_bigint:
                    buf.write(f"{ind1}if ({stop_var} > {start_var}) {{ size_t __sz; "
                              f"if (({stop_var} - {start_var}).to_size_checked(__sz)) "
                              f"__result.reserve(__sz); }}\n")
                else:
                    buf.write(f"{ind1}if ({stop_var} > {start_var}) __result.reserve("
                              f"static_cast<size_t>({stop_var} - {start_var}));\n")
            buf.write(f"{ind1}for ({cpp_elem} {cpp_var} = {start_var}; "
                      f"{cpp_var} < {stop_var}; ++{cpp_var}) {{\n")
    else:
        n = state.next_loop_index()
        obj, beg, end = f"__obj_{n}", f"__beg_{n}", f"__end_{n}"
        binding_kw = "auto&" if e.iterable_lvalue else "auto"
        buf.write(f"{ind1}{binding_kw} {obj} = {_emit_expr(e.iterable, state)};\n")
        if not skip_reserve and e.sized_reserve:
            buf.write(f"{ind1}__result.reserve(static_cast<std::size_t>"
                      f"({obj}.size()));\n")
        buf.write(f"{ind1}auto {beg} = {obj}.begin();\n")
        buf.write(f"{ind1}auto {end} = {obj}.end();\n")
        buf.write(f"{ind1}for (; {beg} != {end}; ++{beg}) {{\n")
        if e.unpack_targets:
            un = state.next_unpack()
            tmp = f"__tup_{un}"
            ref = "const auto&" if e.const_loop_var else "auto&"
            buf.write(f"{ind2}{ref} {tmp} = *{beg};\n")
            for i, name in enumerate(e.unpack_targets):
                if name is None:
                    continue
                buf.write(f"{ind2}{e.unpack_target_cpps[i]} "
                          f"{escape_cpp_name(name)} = std::get<{i}>({tmp});\n")
        else:
            binding = loop_var_binding(e.elem_type, cpp_var, f"*{beg}",
                                       e.const_loop_var)
            buf.write(f"{ind2}{binding}\n")
    if e.kind == "dict":
        insert = (f"__result.insert_or_assign({_emit_expr(e.key, state)}, "
                  f"{_emit_expr(e.value, state)})")
    elif e.kind == "set":
        insert = f"__result.insert({_emit_expr(e.element, state)})"
    else:
        insert = f"__result.push_back({_emit_expr(e.element, state)})"
    if e.conditions:
        cond_str = " && ".join(_emit_expr(c, state) for c in e.conditions)
        buf.write(f"{ind2}if ({cond_str}) {{\n")
        buf.write(f"{ind3}{insert};\n")
        buf.write(f"{ind2}}}\n")
    else:
        buf.write(f"{ind2}{insert};\n")
    buf.write(f"{ind1}}}\n")
    buf.write(f"{ind1}std::move(__result);\n")
    buf.write(f"{stmt_ind}}})")
    return buf.getvalue()


def _emit_genexpr(e: 'THIRGenExpr', state: _EmitState) -> str:
    """The make_generator render of _gen_generator_expression: an inner mutable
    lambda binds each element and yields `optional<slot>`. An LVALUE source
    aliases through an outer `[caps]()` IIFE; a NON-LVALUE source moves into the
    lambda's init-captures under an `if (!__started)` seed. Indents relative to
    the enclosing statement (stmt_indent_level)."""
    stmt_ind = INDENT * state.stmt_indent_level
    ind1 = stmt_ind + INDENT
    elem = _emit_expr(e.element, state)
    if e.moved_source:
        lambda_ind = ind1
        ind2i = lambda_ind + INDENT
        ind3i = ind2i + INDENT
        elems = ", ".join(_emit_expr(el, state) for el in e.iterable_elements)
        buf = io.StringIO()
        buf.write(f"::tpy::make_generator<{e.slot_cpp}>(\n")
        buf.write(f"{lambda_ind}[{e.inner_captures}__src = {e.cpp_iterable}"
                  f"({{{elems}}}), __started = false, "
                  f"__beg = {e.cpp_iterable}::iterator(), "
                  f"__end = {e.cpp_iterable}::iterator()]() mutable -> "
                  f"std::optional<{e.slot_cpp}> {{\n")
        buf.write(f"{ind2i}if (!__started) {{ __beg = __src.begin(); "
                  f"__end = __src.end(); __started = true; }}\n")
        buf.write(f"{ind2i}while (__beg != __end) {{\n")
        buf.write(f"{ind3i}{e.binding_cpp}\n")
        buf.write(f"{ind3i}return std::optional<{e.slot_cpp}>({elem});\n")
        buf.write(f"{ind2i}}}\n")
        buf.write(f"{ind2i}return std::nullopt;\n")
        buf.write(f"{lambda_ind}}}\n")
        buf.write(f"{stmt_ind})")
        return buf.getvalue()
    lambda_ind = ind1 + INDENT
    ind2i = lambda_ind + INDENT
    ind3i = ind2i + INDENT
    iife = e.iife_captures.removesuffix(", ")
    src = _emit_expr(e.iterable, state)
    buf = io.StringIO()
    buf.write(f"[{iife}]() {{\n")
    buf.write(f"{ind1}auto& __src = {src};\n")
    buf.write(f"{ind1}return ::tpy::make_generator<{e.slot_cpp}>(\n")
    buf.write(f"{lambda_ind}[{e.inner_captures}__beg = __src.begin(), "
              f"__end = __src.end()]() mutable -> std::optional<{e.slot_cpp}> {{\n")
    buf.write(f"{ind2i}while (__beg != __end) {{\n")
    buf.write(f"{ind3i}{e.binding_cpp}\n")
    buf.write(f"{ind3i}return std::optional<{e.slot_cpp}>({elem});\n")
    buf.write(f"{ind2i}}}\n")
    buf.write(f"{ind2i}return std::nullopt;\n")
    buf.write(f"{lambda_ind}}}\n")
    buf.write(f"{ind1});\n")
    buf.write(f"{stmt_ind}}}()")
    return buf.getvalue()


def _emit_container_literal(e: THIRContainerLiteral, state: _EmitState) -> str:
    # Dispatch on the resolved container family, mirroring _gen_array_literal /
    # _gen_dict_literal / _gen_set_literal. list/Array brace-inits are consumed
    # by the spelled decl type; dict/set spell their runtime container
    # constructor; `make_container` picks the reserve+emplace helpers (const
    # std::initializer_list elements would copy a std::move / cannot hold a
    # non-copyable element).
    t = unwrap_qualifiers(e.result_type)
    if is_dict(t):
        k_cpp = t.type_args[0].to_cpp()
        v_cpp = t.type_args[1].to_cpp()
        if not e.elements:
            return f"::tpy::ordered_map<{k_cpp}, {v_cpp}>()"
        pairs = [(_emit_expr(k, state), _emit_expr(v, state))
                 for k, v in zip(e.elements, e.values)]
        if e.make_container:
            flat = ", ".join(f"{k}, {v}" for k, v in pairs)
            return f"::tpy::make_ordered_map<{k_cpp}, {v_cpp}>({flat})"
        braces = ", ".join(f"{{{k}, {v}}}" for k, v in pairs)
        return f"::tpy::ordered_map<{k_cpp}, {v_cpp}>({{{braces}}})"
    if is_set(t):
        cpp_elem = t.type_args[0].to_cpp()
        if not e.elements:
            return f"::tpy::ordered_set<{cpp_elem}>()"
        elems = ", ".join(_emit_expr(x, state) for x in e.elements)
        if e.make_container:
            return f"::tpy::make_ordered_set<{cpp_elem}>({elems})"
        return f"::tpy::ordered_set<{cpp_elem}>({{{elems}}})"
    # An empty list literal spells its type (the T*-assignment-ambiguity guard in
    # _gen_array_literal); an empty Array is gated out at eligibility.
    if not e.elements and is_list(t):
        return f"{t.to_cpp()}{{}}"
    elems = ", ".join(_emit_expr(x, state) for x in e.elements)
    if e.make_container:
        return f"::tpy::make_vector<{e.elem_cpp}>({elems})"
    literal = f"{{{elems}}}"
    # A std::array of a brace-initialised aggregate element (a nested list)
    # needs the extra std::array brace level so each element copy-list-inits
    # cleanly (mirrors _gen_array_literal's elem_target check; only a demoted
    # Array threads a container element target).
    if is_array(t):
        args = getattr(t, "type_args", None)
        et = args[0] if args else None
        if isinstance(et, TpyType) and (is_list(et) or is_array(et)):
            literal = f"{{{literal}}}"
    if e.typed_brace_cpp is not None and literal.startswith("{"):
        # typed_brace_init: self-describe a bare brace-init so it can bind to
        # a template parameter (dict-comp insert_or_assign value).
        return f"{e.typed_brace_cpp}{literal}"
    return literal


def _emit_field_access(e: THIRFieldAccess, state: _EmitState) -> str:
    if e.deref_check:
        # Unproven Optional member access: null-check the (already `T*`) receiver
        # before the `.` member read. Mirrors _gen_field_access's runtime-check path.
        return f"::tpy::deref_check({_emit_expr(e.receiver, state)}).{e.field_cpp}"
    return f"{_emit_expr(e.receiver, state)}{'->' if e.is_arrow else '.'}{e.field_cpp}"


def _emit_subscript(e: THIRSubscript, state: _EmitState) -> str:
    recv = _emit_expr(e.receiver, state)
    if isinstance(unwrap_qualifiers(e.receiver.result_type), TupleType):
        # Tuple element read: the index is a normalized compile-time constant (a
        # THIRLiteral), so the C++ template argument is a bare non-negative int.
        # Mirrors _gen_subscript's tuple branch (value-scalar element, no lift).
        if not isinstance(e.index, THIRLiteral):
            raise THIRCodeGenError("tuple subscript index is not a THIRLiteral")
        return f"std::get<{e.index.value}>({recv})"
    # Container (list / dict) index/key lookup, mirroring _gen_subscript's
    # container branch. A runtime-BigInt index arrives pre-wrapped in its
    # `.to_fixed_check<int32_t>()` THIRCoerce (lowering's _narrow_bigint_index
    # mirrors gen_index_expr), so the emit stays index-type-neutral.
    idx = _emit_expr(e.index, state)
    if e.bounds_safe:
        # Index proven in [0, len): skip normalize_index. A literal index needs no
        # cast (a compile-time constant is -Wsign-conversion-exempt); a variable
        # index casts to size_t for the builtin operator[]. Mirrors _gen_subscript.
        if isinstance(e.index, THIRLiteral):
            return f"{recv}[{idx}]"
        return f"{recv}[static_cast<std::size_t>({idx})]"
    rt = unwrap_qualifiers(e.receiver.result_type)
    if is_bytes_type(rt) or is_bytes_view_type(rt):
        # bytes' `__getitem__(Int32)` is a @native free-function dunder, not
        # the containers' checked `::tpy::__getitem__` template -- mirrors
        # _gen_subscript's fi dispatch (get_type_method_fi -> the native arm).
        return f"::tpy::bytes_getitem({recv}, {idx})"
    return f"::tpy::__getitem__({recv}, {idx})"


def _emit_fstring(e: THIRFString, state: _EmitState) -> str:
    # Mirrors ExpressionGenerator._gen_fstring's assembly as a pure string
    # function (the per-arg type dispatch is already carried as wrap templates):
    # a pure-literal f-string renders as a std::string of the joined segments;
    # an interpolated one as std::format over the brace-escaped format string.
    # A literal segment embedding a NUL byte takes the explicit-length arms --
    # the const char* std::string ctor / std::format's consteval string_view
    # ctor would truncate via strlen.
    fmt_parts: list[str] = []
    raw_parts: list[str] = []  # without brace-escaping, for the pure-literal path
    raw_value = ""  # original (unescaped) literal content, for NUL detection
    decoded_fmt_parts: list[str] = []  # runtime view of the fmt string (NUL length)
    args: list[str] = []
    all_literal = True
    for part in e.parts:
        if isinstance(part, str):
            escaped = escape_cpp_string(part)
            raw_parts.append(escaped)
            raw_value += part
            fmt_parts.append(escaped.replace("{", "{{").replace("}", "}}"))
            decoded_fmt_parts.append(part.replace("{", "{{").replace("}", "}}"))
        else:
            all_literal = False
            # A constant format spec splices into the placeholder verbatim
            # (the AST arm's raw concatenation -- never brace-escaped or
            # C++-escaped), in both the source and runtime-length views.
            placeholder = ("{}" if part.format_spec is None
                           else "{:" + part.format_spec + "}")
            fmt_parts.append(placeholder)
            decoded_fmt_parts.append(placeholder)
            inner = _emit_expr(part.expr, state)
            args.append(inner if part.wrap is None
                        else expand_cpp_template(part.wrap, None, inner))
    if all_literal:
        joined = "".join(raw_parts)
        if "\x00" in raw_value:
            nbytes = len(raw_value.encode("utf-8"))
            return f'std::string("{joined}", {nbytes})'
        return f'std::string("{joined}")'
    fmt_str = "".join(fmt_parts)
    args_str = ", ".join(args)
    if "\x00" in raw_value:
        nbytes = len("".join(decoded_fmt_parts).encode("utf-8"))
        return (f'std::vformat(std::string_view{{"{fmt_str}", {nbytes}}}, '
                f'std::make_format_args({args_str}))')
    return f'std::format("{fmt_str}", {args_str})'


def _emit_str_slice(e: THIRStrSlice, state: _EmitState) -> str:
    # Mirrors _gen_subscript's slice arm: the resolved __getitem__ @cpp_template
    # expanded over the receiver and the slice argument -- a slice-typed
    # variable index rendered bare, or a BasicSlice/Slice initializer
    # (_gen_slice_object, stepped per the source syntax); an absent bound
    # renders std::nullopt (_gen_optional_slice_bound).
    if e.index is not None:
        return expand_cpp_template(e.cpp_template, _emit_expr(e.receiver, state),
                                   _emit_expr(e.index, state))
    lo = _emit_expr(e.lower, state) if e.lower is not None else "std::nullopt"
    hi = _emit_expr(e.upper, state) if e.upper is not None else "std::nullopt"
    if e.stepped:
        step = _emit_expr(e.step, state) if e.step is not None else "std::nullopt"
        slice_arg = f"::tpy::Slice{{{lo}, {hi}, {step}}}"
    else:
        slice_arg = f"::tpy::BasicSlice{{{lo}, {hi}}}"
    return expand_cpp_template(e.cpp_template, _emit_expr(e.receiver, state), slice_arg)


def _emit_form_convert(e: THIRFormConvert, state: _EmitState) -> str:
    # storage->borrow lifts. optional_to_ptr's const overload is auto-selected by
    # the optional's own const-ness, so is_const here is carried for MIR / other
    # families, not the rendered helper. The borrow->storage direction (F2b) and
    # the union / tuple families arrive in later rungs.
    inner = _emit_expr(e.value, state)
    t = unwrap_qualifiers(e.result_type)
    if e.form is Form.BORROW:
        # F1 Optional[ref] read: `std::optional<T>` lvalue -> `T*`.
        if isinstance(t, OptionalType):
            return f"::tpy::optional_to_ptr({inner})"
        # F4 U2: a storage `std::variant<A, B>` lvalue (a union field) lifts to
        # the pointer variant; the const helper aliases const pointees (a
        # readonly receiver). Mirrors context.convert's union BORROW arm.
        if isinstance(t, UnionType):
            helper = "to_const_ptr_variant" if e.is_const else "to_ptr_variant"
            return f"::tpy::{helper}({inner})"
        # F2a plain non-value lvalue -> reseatable `T*` pointer-local: address-of.
        if is_plain_nonvalue(t):
            return f"&({inner})"
        # F3 storage tuple -> borrow tuple: the runtime helper absorbs the
        # per-element pointer/optional mask from the spelled destination, so the
        # only thing to render is that destination (the borrow form, const when
        # the source is const). Mirrors context.convert's tuple BORROW arm.
        if isinstance(t, TupleType):
            borrow_cpp = t.to_cpp_return_const() if e.is_const else t.to_cpp_return()
            return f"::tpy::tuple_to_pointer<{borrow_cpp}>({inner})"
    elif e.form is Form.STORAGE:
        # borrow `T*` -> storage `std::optional<T>` (write/return direction). An
        # owned source at last use moves (`ptr_to_optional_move`, F2e); a
        # non-owning borrow copies (`ptr_to_optional`, F2b/F2c). `move` is set by
        # lowering from the same `movable_locals` + last-use facts the AST reads.
        if isinstance(t, OptionalType):
            helper = "ptr_to_optional_move" if e.move else "ptr_to_optional"
            return f"::tpy::{helper}({inner})"
        # F4 U2: a borrow pointer-variant into a storage `std::variant<A, B>`
        # slot (a union field write) copies the active member out. Mirrors
        # context.convert's union STORAGE arm.
        if isinstance(t, UnionType):
            return f"::tpy::to_value_variant<{t.to_cpp()}>({inner})"
        # F3 borrow tuple -> storage tuple: the helper absorbs the per-element
        # pointer->optional/value mask from the spelled storage destination. An
        # owned source at last use moves; a borrow copies. Mirrors context.convert's
        # tuple STORAGE arm.
        if isinstance(t, TupleType):
            helper = "tuple_to_storage_move" if e.move else "tuple_to_storage"
            return f"::tpy::{helper}<{t.to_cpp()}>({inner})"
        # S1/S6 str+bytes slices: a view-form source (string_view / span) into
        # an owned storage sink (decl init / return) copies via the family's
        # owned constructor -- `std::string(x)` / `::tpy::bytes_copy(x)` -- the
        # view->owned construction being explicit. Mirrors the AST's
        # `_view_source_to_owned` chokepoint spelling via the shared
        # `view_to_owned_conv` helper. The materializing str-family coercions
        # (strview_to_str / str_to_string / strview_to_string) lower here too:
        # the cross-type respelling is family-internal, the emit identical --
        # `String` is the same owned std::string spelled as a distinct type.
        if is_str_type(t) or is_string_type(t) or is_bytes_type(t):
            return f"{view_to_owned_conv(t)}({inner})"
        # F5: a generic record's `T` field write. The C++ template's
        # `param_val_or_ref_t<T>` / `own_param_t<T>` resolve the copy/move target
        # per instantiation, so the source-level assign is a plain `field = v` (a
        # bare copy) or `field = std::move(v)` (an Own param at last use) -- the
        # same `e.move` decision the sibling arms make, no runtime helper.
        if isinstance(t, TypeParamRef):
            return f"std::move({inner})" if e.move else inner
        # A plain non-value record/container slot (a field write / MIL cell):
        # the storage sink consumes the source directly -- `std::move(v)` for
        # an owned source at its last use, the bare render (a copy) otherwise.
        # The record sibling of the TypeParamRef arm; the AST spells both
        # inline with no runtime helper.
        if is_plain_nonvalue(t):
            return f"std::move({inner})" if e.move else inner
    raise THIRCodeGenError(
        f"unhandled THIRFormConvert: {type(t).__name__} {e.value.form}->{e.form}")


def _emit_expr(e: THIRExpr, state: _EmitState) -> str:
    if isinstance(e, THIRName):
        # `deref`: an F2 pointer-local read in a value position (a record call
        # arg) -- gen_expr_deref's `(*p)` indirect render. A pre-spelled
        # native/imported global (`cpp`) renders verbatim -- the AST emits
        # qualify_native_name / imported_variable_cpp output unescaped.
        name = e.cpp if e.cpp is not None else escape_cpp_name(e.name)
        return f"(*{name})" if e.deref else name
    if isinstance(e, THIRSelf):
        return f"(*{e.cpp})" if e.deref else e.cpp
    if isinstance(e, THIRLiteral):
        return _emit_literal(e)
    if isinstance(e, THIRStrLiteral):
        return cpp_string_literal_expr(e.value)
    if isinstance(e, THIRBytesLiteral):
        # The owned/span verdict was decided at lowering from the sink and
        # rides the form tag (see the node's doc); the empty-literal arms
        # mirror gen_expr's TpyBytesLiteral branch and gen_call_arg's
        # static-span pin.
        if e.form is Form.STORAGE:
            return cpp_bytes_literal_owned(e.value)
        if not e.value:
            return "std::span<const uint8_t>{}"
        return cpp_bytes_literal_span(e.value)
    if isinstance(e, THIRFString):
        return _emit_fstring(e, state)
    if isinstance(e, THIRCharLiteral):
        # A Char-targeted str literal (compare operand opposite a Char, a
        # Char-annotated decl init, a Char-slot call arg) -- mirrors
        # gen_expr's char-literal branch.
        return f"'{escape_cpp_char(e.value)}'"
    if isinstance(e, THIRIsinstance):
        # Mirrors the AST isinstance arm over value/pointer variants: one
        # holds_alternative per check member, OR-joined and parenthesized for
        # the multi-member (tuple / inline-union) form.
        checks = [f"std::holds_alternative<{m}>({e.variant_cpp})"
                  for m in e.member_cpps]
        return checks[0] if len(checks) == 1 else "(" + " || ".join(checks) + ")"
    if isinstance(e, THIRNarrowedRead):
        # A compound-condition read of the narrowed subject: the bare get, no
        # alias yet -- the ptr-variant deref parenthesizes for member access.
        get = f"std::get<{e.member_cpp}>({e.variant_cpp})"
        return f"(*{get})" if e.is_ptr_variant else get
    if isinstance(e, THIRFieldAccess):
        return _emit_field_access(e, state)
    if isinstance(e, THIRSubscript):
        return _emit_subscript(e, state)
    if isinstance(e, THIRStrSlice):
        return _emit_str_slice(e, state)
    if isinstance(e, THIRFormConvert):
        return _emit_form_convert(e, state)
    if isinstance(e, THIRBinOp):
        return _emit_binop(e, state)
    if isinstance(e, THIRChainedCompareStmtExpr):
        return _emit_chained_compare_stmtexpr(e, state)
    if isinstance(e, THIRUnaryNot):
        # Mirrors _gen_unaryop's `!` arm over a bool operand, whose truthiness
        # render is the plain value render. A pointer-repr Optional borrow
        # name's truthiness render is the bare `T*` (gen_truthy_expr), so the
        # same wrap serves `not p` too.
        return f"(!({_emit_expr(e.operand, state)}))"
    if isinstance(e, THIRMembership):
        # _gen_binop's resolved_contains arm: `(recv.contains(needle))`, the
        # negation wrapping the already-parenthesized find expr.
        inner = (f"({_emit_expr(e.receiver, state)}.{e.method_cpp}"
                 f"({_emit_expr(e.needle, state)}))")
        return f"(!{inner})" if e.negate else inner
    if isinstance(e, THIRIsNone):
        inner = _emit_expr(e.operand, state)
        if e.value_repr:
            # `std::optional<T>` param: `is None` -> `(!p.has_value())`,
            # `is not None` -> `(p.has_value())` (_gen_binop's has_value arm).
            return f"({inner}.has_value())" if e.negate else f"(!{inner}.has_value())"
        op = "!=" if e.negate else "=="
        return f"({inner} {op} nullptr)"
    if isinstance(e, THIRTruthy):
        if e.mode is TruthinessMode.ALWAYS_TRUE:
            return "true"
        assert e.operand is not None
        inner = _emit_expr(e.operand, state)
        if e.deref:
            inner = f"(*{inner})"
        if e.mode is TruthinessMode.NONEMPTY:
            return f"(!{inner}.empty())"
        if e.mode is TruthinessMode.IS_TRUTHY:
            return f"::tpy::is_truthy({inner})"
        if e.mode is TruthinessMode.TO_BOOL:
            return f"::tpy::to_bool({inner})"
        if e.mode is TruthinessMode.RECORD_BOOL:
            return f"::tpy::__bool__({inner})"
        if e.mode is TruthinessMode.RECORD_LEN:
            return f"(::tpy::__len__({inner}) != 0)"
        raise THIRCodeGenError(f"unknown truthiness mode: {e.mode}")
    if isinstance(e, THIROptViewArg):
        # `_maybe_convert_opt_view_param`'s same-TPy-type ARG split: the
        # borrow-form `optional<view>` param -> the owned-storage
        # `optional<owned>` slot. The owned copy spelling keys on the view
        # FAMILY's owned type (`std::string` for str, incl. a `StrView` inner
        # whose family owned_type is still `str`), matching the AST's
        # `view_to_owned_conv(family.owned_type)`.
        n = escape_cpp_name(e.name)
        fam = view_family_for_type(e.result_type.inner)
        conv = view_to_owned_conv(fam.owned_type)
        return f"{n} ? std::make_optional({conv}(*{n})) : std::nullopt"
    if isinstance(e, THIRIfExpr):
        # _gen_if_expr's render; arm targets and the mixed-arm str wraps were
        # decided at lowering, so the emit is pure spelling.
        return (f"(({_emit_expr(e.cond, state)}) ? "
                f"({_emit_expr(e.then, state)}) : "
                f"({_emit_expr(e.orelse, state)}))")
    if isinstance(e, THIRCall):
        return _emit_call(e, state)
    if isinstance(e, THIRUnionArgLift):
        return _emit_union_arg_lift(e, state)
    if isinstance(e, THIRCtorCall):
        return _emit_ctor_call(e, state)
    if isinstance(e, THIRArgTemp):
        # Register the hoisted decl with the sink and read the real __tmp_N
        # here; args render left-to-right, so creation order matches the AST's
        # per-arg cascade. The pending decl flushes before the statement line.
        init_cpp = _emit_expr(e.init, state)
        cpp_type = e.cpp_type if e.cpp_type is not None else "auto"
        name = state.temps.create(cpp_type, init_cpp, brace_init=e.brace_init)
        if e.move:
            return f"std::move({name})"
        return f"&({name})" if e.addr_of else name
    if isinstance(e, THIRMove):
        return f"std::move({_emit_expr(e.value, state)})"
    if isinstance(e, THIROptionalPtrArg):
        if e.value is None:
            return "nullptr"
        inner = _emit_expr(e.value, state)
        if e.lift:
            return f"::tpy::optional_to_ptr({inner})"
        return f"&({inner})" if e.addr_of else inner
    if isinstance(e, THIRMethodCall):
        return _emit_method_call(e, state)
    if isinstance(e, (THIREnumMember, THIRClassConstant, THIRModuleVar)):
        return e.cpp
    if isinstance(e, THIREnumWrap):
        if e.operand is None:
            return e.wrap  # plain-enum truthiness: literal `true`
        return e.wrap.format(_emit_expr(e.operand, state))
    if isinstance(e, THIRContainerLiteral):
        return _emit_container_literal(e, state)
    if isinstance(e, THIRTupleLiteral):
        # The spelled value-tuple render (`std::tuple<...>{e1, e2}`);
        # result_type is the slot TupleType, whose scalar/owned-str elements
        # spell identically via to_cpp and the resolver. A single-element
        # tuple parenthesizes instead (GCC brace-init ambiguity with
        # std::tuple constructors in C++23 -- _gen_tuple_literal's tail).
        elems = ", ".join(_emit_expr(x, state) for x in e.elements)
        cpp_type = unwrap_qualifiers(e.result_type).to_cpp()
        if len(e.elements) == 1:
            return f"{cpp_type}({elems})"
        return f"{cpp_type}{{{elems}}}"
    if isinstance(e, THIRComprehension):
        return _emit_comprehension(e, state)
    if isinstance(e, THIRGenExpr):
        return _emit_genexpr(e, state)
    if isinstance(e, THIRCoerce):
        # Passthrough coercions render the inner expression in the target
        # type's context (int/float literal coercions, the identity str-family
        # positions); the scalar-cast family formats the inner render through
        # the `{0}` wrap computed at lowering (`static_cast<float>(x)` etc.).
        inner = _emit_expr(e.expr, state)
        if e.wrap is not None:
            return e.wrap.format(inner)
        return inner
    raise THIRCodeGenError(f"unhandled THIR expr: {type(e).__name__}")


# --- statements ---


def _is_elif(outer: THIRIf, inner: THIRIf) -> bool:
    """Mirror StatementGenerator._is_elif: an `else_body` of a single THIRIf is
    a flattenable elif (vs a nested `else: if`) when their source columns match."""
    if outer.loc is None and inner.loc is None:
        return True
    if outer.loc is None or inner.loc is None:
        return False
    return inner.loc.column == outer.loc.column


def _emit_if(out: TextIO, stmt: THIRIf, indent_level: int, state: _EmitState) -> None:
    # The outer `// if ...:` comment is emitted by the caller (_emit_stmts).
    # Flatten the elif chain into `} else if (...)`, matching the AST path.
    indent = INDENT * indent_level
    body_indent = INDENT * (indent_level + 1)
    # Hoisted predecls precede the whole chain, like the AST's
    # _emit_branch_decls run before _gen_if (see _emit_try).
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
    chain = [stmt]
    while (len(chain[-1].else_body) == 1
           and isinstance(chain[-1].else_body[0], THIRIf)
           and not chain[-1].else_is_nested
           and _is_elif(chain[-1], chain[-1].else_body[0])):
        chain.append(chain[-1].else_body[0])
    for i, node in enumerate(chain):
        if i == 0:
            out.write(f"{indent}if ({_emit_expr(node.condition, state)}) {{\n")
        else:
            state.comments.elif_(out, node.loc, indent)
            out.write(f"{indent}}} else if ({_emit_expr(node.condition, state)}) {{\n")
        _emit_stmts(out, node.then_body, indent_level + 1, state)
        state.comments.trailing(out, node.then_body, body_indent)
    last = chain[-1]
    if last.else_body:
        state.comments.else_(out, last.else_body, indent)
        out.write(f"{indent}}} else {{\n")
        _emit_stmts(out, last.else_body, indent_level + 1, state)
        state.comments.trailing(out, last.else_body, body_indent)
    out.write(f"{indent}}}\n")


def _push_loop_frame(state: _EmitState, has_else: bool = False) -> int:
    # Mirrors the loop-entry bracketing shared by _gen_while/_gen_for_each:
    # the else label drawn from iter_counter FIRST (before the loop draws its
    # own index -- the AST allocates it at the top of _gen_while/_gen_for_each),
    # one empty loop-break slot per loop, and a zeroed switch depth (a switch
    # OUTSIDE the loop must not reroute a break INSIDE it). Returns the saved
    # depth for _pop_loop_frame.
    label = ""
    if has_else:
        label = f"__after_else_{state.iter_counter}"
        state.iter_counter += 1
    state.loop_else_labels.append(label)
    state.loop_break_labels.append("")
    saved = state.switch_depth
    state.switch_depth = 0
    return saved


def _pop_loop_frame(out: TextIO, indent: str, state: _EmitState,
                    saved_depth: int, orelse: 'tuple[THIRStmt, ...]' = (),
                    indent_level: int = 0) -> None:
    # The loop-exit half: restore the switch depth, emit the else block (a
    # bare `{...}` + its `__after_else_N:;` label -- run on normal completion,
    # jumped past by a break), then place the lazily allocated
    # `__loop_break_N:;` label (the AST's `if break_label:` tail). The else
    # body emits AFTER the loop frames pop, so a break inside it targets the
    # enclosing loop, exactly like the AST's pop-then-emit order.
    state.switch_depth = saved_depth
    else_label = state.loop_else_labels.pop()
    break_label = state.loop_break_labels.pop()
    if else_label:
        state.comments.else_(out, orelse, indent)
        out.write(f"{indent}{{\n")
        _emit_stmts(out, orelse, indent_level + 1, state)
        state.comments.trailing(out, orelse, INDENT * (indent_level + 1))
        out.write(f"{indent}}}\n")
        out.write(f"{indent}{else_label}:;\n")
    if break_label:
        out.write(f"{indent}{break_label}:;\n")


def _emit_nested_def(out: TextIO, stmt: THIRNestedDef, indent_level: int,
                     state: _EmitState) -> None:
    # _gen_nested_def's lambda: header spelled at lowering (capture list from
    # sema's node facts, resolver param/return spellings), body one level
    # deeper. Name counters continue across the lambda, exactly like the AST
    # (nested_def_emission_scope leaves them alone) -- but the PER-FUNCTION
    # emission state must not leak in: the lambda is its own function, so a
    # return inside it must not walk the enclosing finally chain, its
    # finally-return temps spell ITS return type, and loop/switch frames
    # reset (the emit-state half of nested_def_emission_scope).
    indent = INDENT * indent_level
    ret = f" -> {stmt.ret_cpp}" if stmt.ret_cpp is not None else ""
    out.write(f"{indent}auto {stmt.name} = {stmt.capture_cpp}"
              f"({', '.join(stmt.params_cpp)}){ret} {{\n")
    saved = (state.finally_frames, state.return_cpp, state.loop_depth,
             state.switch_depth, state.loop_break_labels,
             state.loop_else_labels, dict(state.rebind_slots))
    state.finally_frames = []
    state.return_cpp = stmt.ret_cpp
    state.loop_depth = 0
    state.switch_depth = 0
    state.loop_break_labels = []
    state.loop_else_labels = []
    try:
        # No trailing-comment emission: _gen_nested_def raw-loops gen_stmt
        # with no emit_block_trailing_comments call, so a comment after the
        # lambda's last statement stays OUTSIDE the closing brace.
        _emit_stmts(out, stmt.body, indent_level + 1, state)
    finally:
        (state.finally_frames, state.return_cpp, state.loop_depth,
         state.switch_depth, state.loop_break_labels,
         state.loop_else_labels, state.rebind_slots) = saved
    out.write(f"{indent}}};\n")


def _emit_while(out: TextIO, stmt: THIRWhile, indent_level: int, state: _EmitState) -> None:
    # The `// while ...:` comment is emitted by the caller (_emit_stmts).
    indent = INDENT * indent_level
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    out.write(f"{indent}while ({_emit_expr(stmt.condition, state)}) {{\n")
    state.loop_depth += 1
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.loop_depth -= 1
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")
    _pop_loop_frame(out, indent, state, saved_depth, stmt.orelse, indent_level)


def _emit_for_range(out: TextIO, stmt: THIRForRange, indent_level: int,
                    state: _EmitState) -> None:
    # Mirrors _gen_range_counter_loop (plus_one / non-hoisted branch): grab the
    # loop index BEFORE the body so nested loops number after this one (the AST
    # grabs `n` at the top of _gen_range_counter_loop). Non-literal bounds are
    # captured once into `__start_N`/`__stop_N` temps -- Python's range() reads
    # its args at call time, but the C++ condition re-reads each iteration.
    indent = INDENT * indent_level
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    n = state.next_loop_index()
    cpp_elem = stmt.elem_type.to_cpp()
    var = escape_cpp_name(stmt.var)
    start_cpp = "0" if stmt.start is None else _emit_expr(stmt.start, state)
    stop_cpp = _emit_expr(stmt.stop, state)
    if stmt.start is not None and not stmt.start_is_literal:
        out.write(f"{indent}{cpp_elem} __start_{n} = {start_cpp};\n")
        start_cpp = f"__start_{n}"
    if not stmt.stop_is_literal:
        out.write(f"{indent}{cpp_elem} __stop_{n} = {stop_cpp};\n")
        stop_cpp = f"__stop_{n}"
    # Mirror _gen_range_counter_loop's step arms. The unit steps are the plain
    # ascending / descending loop; the non-unit literal / variable steps add the
    # AST's upfront range_check_overflow (fixed-int only -- the gate admits no
    # other counter here) and, for a variable step, a `__step_N` capture with a
    # nonzero check and a ternary direction condition.
    if stmt.step_kind == "plus_one":
        out.write(f"{indent}for ({cpp_elem} {var} = {start_cpp}; "
                  f"{var} < {stop_cpp}; ++{var}) {{\n")
    elif stmt.step_kind == "unit_neg":
        out.write(f"{indent}for ({cpp_elem} {var} = {start_cpp}; "
                  f"{var} > {stop_cpp}; --{var}) {{\n")
    elif stmt.step_kind in ("literal_pos", "literal_neg"):
        step_cpp = _emit_expr(stmt.step, state)
        out.write(f"{indent}::tpy::range_check_overflow<{cpp_elem}>("
                  f"{start_cpp}, {stop_cpp}, {step_cpp});\n")
        cmp = "<" if stmt.step_kind == "literal_pos" else ">"
        out.write(f"{indent}for ({cpp_elem} {var} = {start_cpp}; "
                  f"{var} {cmp} {stop_cpp}; {var} += {step_cpp}) {{\n")
    else:  # variable
        step_cpp = _emit_expr(stmt.step, state)
        out.write(f"{indent}{cpp_elem} __step_{n} = {step_cpp};\n")
        out.write(f"{indent}::tpy::range_check_step_nonzero(__step_{n});\n")
        out.write(f"{indent}::tpy::range_check_overflow<{cpp_elem}>("
                  f"{start_cpp}, {stop_cpp}, __step_{n});\n")
        out.write(f"{indent}for ({cpp_elem} {var} = {start_cpp}; "
                  f"__step_{n} > 0 ? {var} < {stop_cpp} : {var} > {stop_cpp}; "
                  f"{var} += __step_{n}) {{\n")
    state.loop_depth += 1
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.loop_depth -= 1
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")
    _pop_loop_frame(out, indent, state, saved_depth, stmt.orelse, indent_level)


def _emit_for_each(out: TextIO, stmt: THIRForEach, indent_level: int,
                   state: _EmitState) -> None:
    # Mirrors _gen_begin_end_loop for an element off an lvalue name container: grab the
    # loop index before the body (nested loops number after this one), capture the
    # container -- `auto&` for an lvalue, owning `auto` for an rvalue (a
    # str-returning or Own-container-returning call: the temporary must outlive
    # the loop; mirrors _gen_begin_end_loop's obj_binding) -- then the loop-var binding via the
    # shared loop_var_binding (a scalar is a typed copy; a record is a borrow
    # alias -- auto&& / const auto&, so the const flag is threaded through,
    # not hardcoded).
    indent = INDENT * indent_level
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    n = state.next_loop_index()
    obj, beg, end = f"__obj_{n}", f"__beg_{n}", f"__end_{n}"
    binding_kw = "auto&" if stmt.iterable_lvalue else "auto"
    out.write(f"{indent}{binding_kw} {obj} = {_emit_expr(stmt.iterable, state)};\n")
    out.write(f"{indent}auto {beg} = {obj}.begin();\n")
    out.write(f"{indent}auto {end} = {obj}.end();\n")
    out.write(f"{indent}for (; {beg} != {end}; ++{beg}) {{\n")
    inner = INDENT * (indent_level + 1)
    binding = loop_var_binding(stmt.elem_type, escape_cpp_name(stmt.var),
                              f"*{beg}", stmt.const_loop_var)
    out.write(f"{inner}{binding}\n")
    state.loop_depth += 1
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.loop_depth -= 1
    state.comments.trailing(out, stmt.body, inner)
    out.write(f"{indent}}}\n")
    _pop_loop_frame(out, indent, state, saved_depth, stmt.orelse, indent_level)


def _emit_finally_chain(out: TextIO, indent: str, state: _EmitState,
                        stop_at: int = 0) -> bool:
    # Mirrors _emit_finally_chain: render each frame's cleanup innermost-first
    # down to stop_at (exclusive). A stmt frame emits with itself (and
    # everything above) popped, so a return/break/continue inside the finally
    # body walks the OUTER frames only; the stack is restored on exit (the
    # AST snapshots and restores around the walk). Returns True when a frame
    # terminates (its finally body ends in raise/return) -- the caller must
    # suppress its own trailing exit statement, control already left.
    snapshot = list(state.finally_frames)
    terminated = False
    try:
        while len(state.finally_frames) > stop_at:
            fr = state.finally_frames.pop()
            if fr.stmts is not None:
                _emit_stmts(out, fr.stmts, len(indent) // len(INDENT), state)
            else:
                out.write(f"{indent}__ctx_{fr.ctx_n}.__exit__({{}}, "
                          f"{fr.exc_null_arg}, {{}});\n")
            if fr.terminates:
                terminated = True
                break
    finally:
        state.finally_frames[:] = snapshot
    return terminated


def _witness_chain(kind: str, state: _EmitState, stop_at: int) -> None:
    # Face the walked segment by frame arm, so with/try zero-witness
    # reporting stays honest when both kinds of frame are live.
    seg = state.finally_frames[stop_at:]
    if any(fr.stmts is None for fr in seg):
        _witness(f"with.finally_{kind}")
    if any(fr.stmts is not None for fr in seg):
        _witness(f"try.finally_{kind}")


def _emit_finally_return(out: TextIO, stmt: THIRReturn, indent: str,
                         state: _EmitState) -> None:
    # Mirrors _make_return's finally-chain arm: the value lands in a
    # signature-typed temp BEFORE the chain runs (Python evaluates the return
    # expression first -- and still evaluates it when a terminating finally
    # overrides the return: the [[maybe_unused]] decl + suppressed trailing
    # return). The temp draws from the same per-function iter_counter the AST
    # uses; the chain buffers first like the AST so its own counter bumps land
    # between the temp's allocation and the decl's write.
    _witness_chain("return", state, 0)
    if stmt.value is None:
        if _emit_finally_chain(out, indent, state):
            _witness("try.chain_terminated")
        else:
            out.write(f"{indent}return;\n")
        return
    value_cpp = _emit_expr(stmt.value, state)
    state.temps.flush(out, indent)
    tmp = f"__tpy_ret_{state.iter_counter}"
    state.iter_counter += 1
    ret_cpp = state.return_cpp or "auto"
    chain = io.StringIO()
    terminated = _emit_finally_chain(chain, indent, state)
    maybe_unused = "[[maybe_unused]] " if terminated else ""
    out.write(f"{indent}{maybe_unused}{ret_cpp} {tmp} = {value_cpp};\n")
    out.write(chain.getvalue())
    if terminated:
        _witness("try.chain_terminated")
    else:
        out.write(f"{indent}return {tmp};\n")


def _emit_loop_exit(out: TextIO, indent: str, state: _EmitState,
                    *, is_break: bool) -> None:
    # Mirrors _make_break_continue: only frames pushed inside the innermost
    # active loop body run (the first index whose loop_depth >= the live loop
    # count -- the stack is monotone non-decreasing in loop_depth). A
    # terminating finally suppresses the tail -- control already left through
    # it. A break out of an else-loop jumps its `__after_else_N` label (this
    # also escapes any intervening match switch, so it precedes the switch
    # reroute exactly like the AST's arm order). The break tail otherwise
    # routes around an intervening match switch via the loop's
    # lazily-allocated `__loop_break_N` label (a bare `break;` would exit the
    # switch). C++ `continue` passes through a switch to the enclosing loop,
    # so the continue tail never reroutes.
    boundary = len(state.finally_frames)
    for i, fr in enumerate(state.finally_frames):
        if fr.loop_depth >= state.loop_depth:
            boundary = i
            break
    if boundary < len(state.finally_frames):
        _witness_chain("loop_exit", state, boundary)
    if _emit_finally_chain(out, indent, state, stop_at=boundary):
        _witness("try.chain_terminated")
        return
    if is_break and state.loop_else_labels and state.loop_else_labels[-1]:
        _witness("loop.break_else_goto")
        out.write(f"{indent}goto {state.loop_else_labels[-1]};\n")
        return
    if is_break and state.switch_depth > 0 and state.loop_break_labels:
        if not state.loop_break_labels[-1]:
            state.loop_break_labels[-1] = f"__loop_break_{state.iter_counter}"
            state.iter_counter += 1
        _witness("match.loop_break_goto")
        out.write(f"{indent}goto {state.loop_break_labels[-1]};\n")
        return
    out.write(f"{indent}break;\n" if is_break else f"{indent}continue;\n")


def _emit_with(out: TextIO, stmt: THIRWith, indent_level: int,
               state: _EmitState) -> None:
    # Mirrors _gen_with + _emit_with_try_catch (see THIRWith for the shape):
    # per-item header lines, then one try/catch layer per manager, closed
    # innermost-first so the innermost __exit__ runs first. The header flush
    # mirrors _gen_with's `ctx.temps.flush` (a no-op in the slice --
    # temp-registering manager expressions are gate-rejected).
    indent = INDENT * indent_level
    state.temps.flush(out, indent)
    ctx_ids: list[int] = []
    for item in stmt.items:
        n = state.with_counter.next()
        ctx_ids.append(n)
        ctx_cpp = _emit_expr(item.ctx_expr, state)
        if item.deref_manager:
            ctx_cpp = f"*({ctx_cpp})"
        ctx_bind = "auto&" if item.manager_borrowed else "auto"
        out.write(f"{indent}{ctx_bind} __ctx_{n} = {ctx_cpp};\n")
        # The as-target spells the RAW source name (the AST arm does not
        # escape it), while later reads escape -- mirrored, not fixed.
        if item.target_arm is WithTargetArm.VALUE:
            out.write(f"{indent}auto {item.target} = __ctx_{n}.__enter__();\n")
        elif item.target_arm is WithTargetArm.REF:
            out.write(f"{indent}auto& {item.target} = __ctx_{n}.__enter__();\n")
        elif item.target_arm is WithTargetArm.PTR_DECL:
            out.write(f"{indent}{item.target_cpp}* {item.target} = "
                      f"&(__ctx_{n}.__enter__());\n")
        elif item.target_arm is WithTargetArm.ASSIGN_PTR:
            out.write(f"{indent}{item.target} = &(__ctx_{n}.__enter__());\n")
        else:
            out.write(f"{indent}__ctx_{n}.__enter__();\n")
    # Per-layer terminates: the innermost layer carries body_terminates; once
    # an inner layer may suppress, every layer outside it can fall through --
    # the AST's layer_terminates propagation, folded here from node facts.
    layer_term = [False] * len(stmt.items)
    t = stmt.body_terminates
    for k in range(len(stmt.items) - 1, -1, -1):
        layer_term[k] = t
        if stmt.items[k].can_suppress:
            t = False
    for k, (n, item) in enumerate(zip(ctx_ids, stmt.items)):
        out.write(f"{INDENT * (indent_level + k)}try {{\n")
        state.finally_frames.append(_FinallyFrame(
            ctx_n=n,
            exc_null_arg="nullptr" if item.takes_exc_val else "{}",
            loop_depth=state.loop_depth))
    _emit_stmts(out, stmt.body, indent_level + len(stmt.items), state)
    for k in range(len(stmt.items) - 1, -1, -1):
        n, item = ctx_ids[k], stmt.items[k]
        ind = INDENT * (indent_level + k)
        body_ind = INDENT * (indent_level + k + 1)
        exc_null = "nullptr" if item.takes_exc_val else "{}"
        if not layer_term[k]:
            out.write(f"{body_ind}__ctx_{n}.__exit__({{}}, {exc_null}, {{}});\n")
        # Popped before the catch arms, mirroring _emit_with_try_catch's pop
        # discipline (the catches are fixed strings; nothing walks the stack).
        state.finally_frames.pop()
        if item.can_suppress or item.takes_exc_val:
            exc_obj = f"&__exc_{n}" if item.takes_exc_val else "{}"
            out.write(f"{ind}}} catch (::tpy::BaseException& __exc_{n}) {{\n")
            if item.can_suppress:
                out.write(f"{body_ind}if (!__ctx_{n}.__exit__({{}}, "
                          f"{exc_obj}, {{}})) throw;\n")
            else:
                out.write(f"{body_ind}__ctx_{n}.__exit__({{}}, "
                          f"{exc_obj}, {{}});\n")
                out.write(f"{body_ind}throw;\n")
        out.write(f"{ind}}} catch (...) {{\n")
        out.write(f"{body_ind}__ctx_{n}.__exit__({{}}, {exc_null}, {{}});\n")
        out.write(f"{body_ind}throw;\n")
        out.write(f"{ind}}}\n")


def _emit_frame_wrapped(out: TextIO, inner_level: int, state: _EmitState,
                        stmt: THIRTry, emit_body) -> None:
    # _emit_try_with_finally's unified shape: the finally frame sits on the
    # stack while the body emits; the catch-path and normal-path copies emit
    # with the frame popped, so nested exits redirect through OUTER frames
    # only. `stmt.body_terminates` is the terminates fact of whatever the
    # frame wraps (see THIRTry) and elides the normal-path copy.
    inner = INDENT * inner_level
    state.finally_frames.append(_FinallyFrame(
        loop_depth=state.loop_depth,
        stmts=stmt.finally_body,
        terminates=stmt.finally_terminates))
    out.write(f"{inner}try {{\n")
    emit_body(inner_level + 1)
    out.write(f"{inner}}} catch (...) {{\n")
    state.finally_frames.pop()
    _emit_stmts(out, stmt.finally_body, inner_level + 1, state)
    if not stmt.finally_terminates:
        out.write(f"{INDENT * (inner_level + 1)}throw;\n")
    out.write(f"{inner}}}\n")
    if not stmt.body_terminates:
        _emit_stmts(out, stmt.finally_body, inner_level, state)


def _emit_try_except(out: TextIO, stmt: THIRTry, level: int,
                     state: _EmitState) -> None:
    # Mirrors _gen_try_throw's emit_try_except: the C++ try, one catch arm
    # per handler (headers pre-rendered at lowering; the catch parameter IS
    # the as-binding), else jumping past via the goto label drawn from the
    # module-cumulative try_except_counter sink. Handlers close with `}` and
    # the next header appends ` catch ... {` on the same line, the final `}`
    # taking the newline -- the AST's exact write sequence.
    ind = INDENT * level
    label = ""
    if stmt.else_body:
        label = f"__after_else_{state.try_counter.next()}"
    out.write(f"{ind}try {{\n")
    _emit_stmts(out, stmt.try_body, level + 1, state)
    out.write(f"{ind}}}")
    for h in stmt.handlers:
        if h.cpp_type is None:
            out.write(" catch (...) {\n")
        elif h.binding:
            out.write(f" catch (const {h.cpp_type}& "
                      f"{escape_cpp_name(h.binding)}) {{\n")
        else:
            out.write(f" catch (const {h.cpp_type}&) {{\n")
        _emit_stmts(out, h.body, level + 1, state)
        if stmt.else_body:
            out.write(f"{INDENT * (level + 1)}goto {label};\n")
        out.write(f"{ind}}}")
    out.write("\n")
    if stmt.else_body:
        out.write(f"{ind}// else:\n")
        _emit_stmts(out, stmt.else_body, level, state)
        out.write(f"{ind}{label}:;\n")


def _emit_try(out: TextIO, stmt: THIRTry, indent_level: int,
              state: _EmitState) -> None:
    # Mirrors _gen_try over the two routed tiers (see THIRTry). The hoisted
    # predecls render first, like the AST's gen_stmt dispatch
    # (_emit_branch_decls before _gen_try).
    indent = INDENT * indent_level
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
    out.write(f"{indent}{{\n")
    inner_level = indent_level + 1
    if stmt.tier == "finally_only":
        _emit_frame_wrapped(
            out, inner_level, state, stmt,
            lambda lvl: _emit_stmts(out, stmt.try_body, lvl, state))
    elif stmt.finally_body:
        _emit_frame_wrapped(
            out, inner_level, state, stmt,
            lambda lvl: _emit_try_except(out, stmt, lvl, state))
    else:
        _emit_try_except(out, stmt, inner_level, state)
    out.write(f"{indent}}}\n")


def _emit_match(out: TextIO, stmt: THIRMatch, indent_level: int,
                state: _EmitState) -> None:
    # Mirrors _gen_match_dispatch's scalar tiers (see THIRMatch): the hoisted
    # predecls, the numbered subject binding, then the tier body. `gen_match`
    # draws ONE counter per match (subject + inner names off a single bump;
    # the inner name is an Optional-tier concern); the guarded tiers' second
    # draw is gate-rejected.
    indent = INDENT * indent_level
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
    state.match_counter += 1
    subject = f"__match_subject_{state.match_counter}"
    binding = "auto&" if stmt.subject_ref else "auto"
    out.write(f"{indent}{binding} {subject} = "
              f"{_emit_expr(stmt.subject, state)};\n")
    if stmt.strategy == "if_elif":
        _emit_match_if_elif(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "if_elif_guarded":
        _emit_match_if_elif_guarded(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "switch_union":
        _emit_match_switch_union(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "guarded_union":
        _emit_match_guarded_union(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "if_elif_record":
        _emit_match_if_elif_record(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "guarded_record":
        _emit_match_guarded_record(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "optional_partition":
        _emit_match_optional(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "if_elif_optional":
        _emit_match_if_elif_optional(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "if_elif_optional_guarded":
        _emit_match_if_elif_optional_guarded(out, stmt, indent_level, state,
                                             subject)
    elif stmt.strategy == "switch_str":
        _emit_match_switch_str(out, stmt, indent_level, state, subject)
    else:
        _emit_match_switch(out, stmt, indent_level, state, subject)
    if stmt.emit_unreachable:
        out.write(f"{indent}::std::unreachable();\n")


def _emit_match_binding(out: TextIO, binding: 'THIRMatchBinding | None',
                        subject: str, inner: str) -> None:
    # _emit_binding's value-subject arms, mode folded at lowering (see
    # THIRMatchBinding); the arm block's first line, before the body. A
    # field capture composes the `.field` accessor onto the base spelling
    # (_gen_match_field_bindings' `{case_var}.{field}` RHS).
    if binding is None:
        return
    name = escape_cpp_name(binding.name)
    rhs = f"{subject}{binding.subject_suffix}"
    if binding.mode == "assign":
        out.write(f"{inner}{name} = {rhs};\n")
    elif binding.mode == "copy":
        out.write(f"{inner}auto {name} = {rhs};\n")
    else:
        out.write(f"{inner}auto& {name} = {rhs};\n")


def _emit_match_switch(out: TextIO, stmt: THIRMatch, indent_level: int,
                       state: _EmitState, subject: str) -> None:
    # _emit_switch_groups: the default-goto label draws its counter bump
    # before the switch head; per group the arm comment (the FIRST entry's
    # loc), the case label(s), then either the single-unguarded short path
    # (binding, body one level in) or the guard chain (every entry's
    # binding first, deduped by name, then `if (g) { ... } else if ... }
    # else { ... }` with bodies two levels in; an all-guarded labeled group
    # falls back via `goto __match_default_N;`). Every group closes with
    # the unconditional `break;` (dead after a goto/continue but always
    # written). The always-match group was placed last at lowering.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    default_label = None
    if stmt.default_goto:
        state.match_counter += 1
        default_label = f"__match_default_{state.match_counter}"
    out.write(f"{indent}switch ({subject}) {{\n")
    state.switch_depth += 1
    for arm in stmt.arms:
        state.comments.stmt(out, arm.entries[0].loc, indent)
        if not arm.labels:
            if default_label is not None:
                out.write(f"{indent}default: {default_label}: {{\n")
            else:
                out.write(f"{indent}default: {{\n")
        elif len(arm.labels) == 1:
            out.write(f"{indent}case {arm.labels[0]}: {{\n")
        else:
            for label in arm.labels:
                out.write(f"{indent}case {label}:\n")
            out.write(f"{indent}{{\n")
        if len(arm.entries) == 1 and arm.entries[0].guard is None:
            entry = arm.entries[0]
            _emit_match_binding(out, entry.binding, subject, inner)
            _emit_stmts(out, entry.body, indent_level + 1, state)
        else:
            emitted: set[str] = set()
            for entry in arm.entries:
                if entry.binding is not None and entry.binding.name not in emitted:
                    _emit_match_binding(out, entry.binding, subject, inner)
                    emitted.add(entry.binding.name)
            has_unguarded = any(e.guard is None for e in arm.entries)
            if_opened = False
            for entry in arm.entries:
                if entry.guard is not None:
                    keyword = "if" if not if_opened else "} else if"
                    if_opened = True
                    out.write(f"{inner}{keyword} "
                              f"({_emit_expr(entry.guard, state)}) {{\n")
                else:
                    out.write(f"{inner}}} else {{\n")
                _emit_stmts(out, entry.body, indent_level + 2, state)
            out.write(f"{inner}}}\n")
            if (not has_unguarded and default_label is not None
                    and arm.labels):
                out.write(f"{inner}goto {default_label};\n")
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")
    if stmt.synthetic_default:
        out.write(f"{indent}default: break;\n")
    state.switch_depth -= 1
    out.write(f"{indent}}}\n")


def _emit_match_switch_union(out: TextIO, stmt: THIRMatch, indent_level: int,
                             state: _EmitState, subject: str) -> None:
    # _gen_match_switch_union: `switch (subject.index())`, arms in SOURCE
    # order (`default:` emits in place -- no regrouping), numeric variant-
    # index case labels, the `__case_{i}` extraction alias when sema
    # narrowing drew one (`auto& __case_i = [*]std::get<idx>(subject);`),
    # the `as` binding against the alias (or the composed get), stacked
    # index labels for binding-free or-patterns, and the unconditional
    # `break;` per arm. Wrapper subjects (`.value` indirection) are
    # gate-rejected, so the variant expression is the bare subject.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    variant = subject
    out.write(f"{indent}switch ({variant}.index()) {{\n")
    state.switch_depth += 1
    deref = "*" if stmt.is_ptr_variant else ""
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if not arm.labels:
            out.write(f"{indent}default: {{\n")
        elif len(arm.labels) == 1:
            out.write(f"{indent}case {arm.labels[0]}: {{\n")
        else:
            for label in arm.labels:
                out.write(f"{indent}case {label}:\n")
            out.write(f"{indent}{{\n")
        get = None
        if entry.variant_index is not None:
            get = f"{deref}std::get<{entry.variant_index}>({variant})"
        if entry.case_alias is not None:
            out.write(f"{inner}auto& {entry.case_alias} = {get};\n")
        for fb in entry.field_bindings:
            # Keyword captures always draw the alias, so the base is it.
            _emit_match_binding(out, fb, entry.case_alias, inner)
        if entry.binding is not None:
            rhs = ((entry.case_alias or get)
                   if entry.binding.from_case_var else subject)
            _emit_match_binding(out, entry.binding, rhs, inner)
        _emit_stmts(out, entry.body, indent_level + 1, state)
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")
    state.switch_depth -= 1
    out.write(f"{indent}}}\n")


def _emit_match_guarded_union(out: TextIO, stmt: THIRMatch,
                              indent_level: int, state: _EmitState,
                              subject: str) -> None:
    # _gen_match_guarded_union + _gen_guarded_switch_arm_action: the end
    # label draws the second per-function counter bump BEFORE the switch;
    # per index group the case label, the once-per-block `__case_{idx}`
    # extraction (when any class entry drew it), then each entry -- its
    # source comment at INNER indent (unlike the unguarded tiers' case
    # indent), an extra `{ }` scope when the group has >1 entries (name
    # collisions between arms), the binding (capture/as -- vs the subject
    # for always-match entries, vs the alias for class entries), the guard
    # as `if (guard) { <body> goto end; }` one level deeper, or the
    # unguarded `<body> goto end;` inline; `break;` closes each block. The
    # trailing end label mirrors the AST's UNINDENTED write.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    inner2 = INDENT * (indent_level + 2)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    out.write(f"{indent}switch ({subject}.index()) {{\n")
    state.switch_depth += 1
    deref = "*" if stmt.is_ptr_variant else ""
    for arm in stmt.arms:
        alias = next((e.case_alias for e in arm.entries
                      if e.case_alias is not None), None)
        if not arm.labels:
            out.write(f"{indent}default: {{\n")
        else:
            out.write(f"{indent}case {arm.labels[0]}: {{\n")
            if alias is not None:
                out.write(f"{inner}auto& {alias} = "
                          f"{deref}std::get<{arm.labels[0]}>({subject});\n")
        use_scope = len(arm.entries) > 1
        bind_indent = inner2 if use_scope else inner
        for entry in arm.entries:
            state.comments.stmt(out, entry.loc, inner)
            if use_scope:
                out.write(f"{inner}{{\n")
            if not entry.field_conds:
                # No field conditions: bindings precede the guard (it may
                # read them). With conditions they move INSIDE the if block
                # below (_gen_guarded_switch_arm_action's split).
                for fb in entry.field_bindings:
                    _emit_match_binding(out, fb, alias, bind_indent)
                if entry.binding is not None:
                    rhs = alias if entry.binding.from_case_var else subject
                    _emit_match_binding(out, entry.binding, rhs, bind_indent)
            if entry.field_conds or entry.guard is not None:
                cond_parts = [f"{pre}{alias}{suf}"
                              for pre, suf in entry.field_conds]
                if entry.guard is not None:
                    cond_parts.append(_emit_expr(entry.guard, state))
                out.write(f"{bind_indent}if "
                          f"({' && '.join(cond_parts)}) {{\n")
                lvl = indent_level + (3 if use_scope else 2)
                if entry.field_conds:
                    body_indent = INDENT * lvl
                    for fb in entry.field_bindings:
                        _emit_match_binding(out, fb, alias, body_indent)
                    if entry.binding is not None:
                        rhs = (alias if entry.binding.from_case_var
                               else subject)
                        _emit_match_binding(out, entry.binding, rhs,
                                            body_indent)
                _emit_stmts(out, entry.body, lvl, state)
                out.write(f"{INDENT * lvl}goto {end_label};\n")
                out.write(f"{bind_indent}}}\n")
            else:
                lvl = indent_level + (2 if use_scope else 1)
                _emit_stmts(out, entry.body, lvl, state)
                out.write(f"{INDENT * lvl}goto {end_label};\n")
            if use_scope:
                out.write(f"{inner}}}\n")
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")
    state.switch_depth -= 1
    out.write(f"{indent}}}\n")
    out.write(f"{end_label}:;\n")


def _emit_match_optional(out: TextIO, stmt: THIRMatch, indent_level: int,
                         state: _EmitState, subject: str) -> None:
    # _gen_match_optimized_optional over the pointer-repr subject slice (see
    # THIRMatch.none_entry): the None arm's comment at the OUTER indent, then
    # `if (subj == nullptr) { <none body> } else {` (or the bare
    # `if (subj != nullptr) {` when no None arm exists), the
    # `__match_inner_N` deref alias, and the single always-match inner block
    # -- `_emit_optional_inner_record`'s no-field `{` ... `}` (comment at the
    # else level, binding vs the alias, body two levels in). The inner name
    # must snapshot the subject's counter draw BEFORE the None body emits: a
    # nested match in there bumps the counter (the AST saves/restores its
    # names per gen_match the same way). No switch, so no switch_depth
    # bracket -- a `break` in an arm body exits the loop directly.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    inner2 = INDENT * (indent_level + 2)
    inner_name = f"__match_inner_{state.match_counter}"
    null_cond = (f"!{subject}.has_value()" if stmt.optional_value_repr
                 else f"{subject} == nullptr")
    has_value_cond = (f"{subject}.has_value()" if stmt.optional_value_repr
                      else f"{subject} != nullptr")
    if stmt.none_entry is not None:
        state.comments.stmt(out, stmt.none_entry.loc, indent)
        out.write(f"{indent}if ({null_cond}) {{\n")
        _emit_stmts(out, stmt.none_entry.body, indent_level + 1, state)
        out.write(f"{indent}}} else {{\n")
    else:
        out.write(f"{indent}if ({has_value_cond}) {{\n")
    out.write(f"{inner}auto& {inner_name} = (*{subject});\n")
    if stmt.inner_strategy is None:
        entry = stmt.arms[0].entries[0]
        state.comments.stmt(out, entry.loc, inner)
        out.write(f"{inner}{{\n")
        _emit_match_binding(out, entry.binding, inner_name, inner2)
        _emit_stmts(out, entry.body, indent_level + 2, state)
        out.write(f"{inner}}}\n")
    elif stmt.inner_strategy == "if_elif":
        _emit_match_if_elif(out, stmt, indent_level + 1, state, inner_name,
                            paren_or=False)
    elif stmt.inner_strategy == "if_elif_record":
        # _emit_optional_inner_record is the record tier's unguarded chain
        # over the deref alias, one level in (guarded/true-alt shapes are
        # gate-rejected), so the record chain emitter is reused verbatim.
        _emit_match_if_elif_record(out, stmt, indent_level + 1, state,
                                   inner_name)
    else:  # switch_enum / switch_primitive over the inner alias
        _emit_match_switch(out, stmt, indent_level + 1, state, inner_name)
    out.write(f"{indent}}}\n")


def _opt_chain_cond(opt_conds, subject: str) -> str:
    # _gen_match_optional_cond's join: per group the (prefix, suffix) pieces
    # composed around the subject and &&-joined; groups ||-joined, or-pattern
    # alternatives parenthesized (the bare null alternative is not).
    parts = []
    for paren, pieces in opt_conds:
        rendered = " && ".join(f"{pre}{subject}{suf}" for pre, suf in pieces)
        parts.append(f"({rendered})" if paren else rendered)
    return " || ".join(parts)


def _emit_match_opt_arm_bindings(out: TextIO, entry, subject: str,
                                 inner: str) -> None:
    # _emit_optional_arm_bindings' routed slice: class-arm field captures
    # against the `(*subj)` deref, then the whole-subject capture/`as`
    # binding -- the deref for a value-side binding (from_case_var), the
    # full Optional for sema's binds_full_optional.
    deref = f"(*{subject})"
    for fb in entry.field_bindings:
        _emit_match_binding(out, fb, deref, inner)
    if entry.binding is not None:
        rhs = deref if entry.binding.from_case_var else subject
        _emit_match_binding(out, entry.binding, rhs, inner)


def _emit_match_if_elif_optional(out: TextIO, stmt: THIRMatch,
                                 indent_level: int, state: _EmitState,
                                 subject: str) -> None:
    # _gen_match_if_elif_optional's unguarded chain, arms in source order:
    # per arm the comment, `if (cond) {` / `} else if (cond) {` off the
    # pre-rendered opt_conds (the always-match arm is `{` / `} else {`),
    # the bindings, the body one level in; one closing brace ends the chain.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if entry.opt_conds is None:
            out.write(f"{indent}{{\n" if i == 0 else f"{indent}}} else {{\n")
        else:
            cond = _opt_chain_cond(entry.opt_conds, subject)
            keyword = "if" if i == 0 else "} else if"
            out.write(f"{indent}{keyword} ({cond}) {{\n")
        _emit_match_opt_arm_bindings(out, entry, subject, inner)
        _emit_stmts(out, entry.body, indent_level + 1, state)
    out.write(f"{indent}}}\n")


def _emit_match_goto_tail(out: TextIO, entry, indent_level: int,
                          state: _EmitState, end_label: str) -> None:
    # _emit_guarded_arm_tail: inside an opened arm block (bindings already
    # emitted), the guard as `if (guard) { <body> goto end; }` two levels
    # in, or the unguarded body + goto one level; closes the block.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    if entry.guard is not None:
        out.write(f"{inner}if ({_emit_expr(entry.guard, state)}) {{\n")
        _emit_stmts(out, entry.body, indent_level + 2, state)
        out.write(f"{INDENT * (indent_level + 2)}goto {end_label};\n")
        out.write(f"{inner}}}\n")
    else:
        _emit_stmts(out, entry.body, indent_level + 1, state)
        out.write(f"{inner}goto {end_label};\n")
    out.write(f"{indent}}}\n")


def _emit_match_if_elif_optional_guarded(out: TextIO, stmt: THIRMatch,
                                         indent_level: int,
                                         state: _EmitState,
                                         subject: str) -> None:
    # _gen_match_if_elif_optional_guarded's standalone-if + goto shape (the
    # end label draws the second per-function counter bump): each arm opens
    # its own `if (cond) {` (bare `{` for an always-match arm), binds, then
    # the goto tail. The label line closes the match at the arm indent.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if entry.opt_conds is None:
            out.write(f"{indent}{{\n")
        else:
            cond = _opt_chain_cond(entry.opt_conds, subject)
            out.write(f"{indent}if ({cond}) {{\n")
        _emit_match_opt_arm_bindings(out, entry, subject, inner)
        _emit_match_goto_tail(out, entry, indent_level, state, end_label)
    out.write(f"{indent}{end_label}:;\n")


def _emit_match_switch_str(out: TextIO, stmt: THIRMatch, indent_level: int,
                           state: _EmitState, subject: str) -> None:
    # _gen_match_switch_str: the end label draws the second per-function
    # counter bump; the guarded-literal prefix arms (standalone `if (cond)
    # {` + binding + goto tail, source order); the discriminator switch --
    # `switch (subj.size())` for 'length', or `switch (static_cast<unsigned
    # char>(subj[i]))` inside a `size() >= i+1` guard for 'char_at' (one
    # extra indent level); per bucket the pre-rendered case label, per
    # entry the comment + `if (subj == "lit") {` + binding + body + goto +
    # close, then the unconditional `break;`; no `default:`. The trailing
    # wildcard/capture arms are bare blocks with the goto tail. The
    # switch_depth bracket wraps only the switch, so a loop `break` in a
    # bucket body renders the goto escape while prefix/trailing bodies
    # break the loop directly.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for entry in stmt.str_guarded:
        state.comments.stmt(out, entry.loc, indent)
        cond = _opt_chain_cond(entry.opt_conds, subject)
        out.write(f"{indent}if ({cond}) {{\n")
        _emit_match_binding(out, entry.binding, subject, inner)
        _emit_match_goto_tail(out, entry, indent_level, state, end_label)
    if stmt.str_disc_kind == "char_at":
        out.write(f"{indent}if ({subject}.size() >= "
                  f"{stmt.str_disc_param + 1}) {{\n")
        sw_level = indent_level + 1
        out.write(f"{inner}switch (static_cast<unsigned char>"
                  f"({subject}[{stmt.str_disc_param}])) {{\n")
    else:
        sw_level = indent_level
        out.write(f"{indent}switch ({subject}.size()) {{\n")
    sw_indent = INDENT * sw_level
    sw_inner = INDENT * (sw_level + 1)
    sw_deep = INDENT * (sw_level + 2)
    state.switch_depth += 1
    for arm in stmt.arms:
        out.write(f"{sw_indent}case {arm.labels[0]}: {{\n")
        for entry in arm.entries:
            state.comments.stmt(out, entry.loc, sw_inner)
            cond = _opt_chain_cond(entry.opt_conds, subject)
            out.write(f"{sw_inner}if ({cond}) {{\n")
            _emit_match_binding(out, entry.binding, subject, sw_deep)
            _emit_stmts(out, entry.body, sw_level + 2, state)
            out.write(f"{sw_deep}goto {end_label};\n")
            out.write(f"{sw_inner}}}\n")
        out.write(f"{sw_inner}break;\n")
        out.write(f"{sw_indent}}}\n")
    state.switch_depth -= 1
    out.write(f"{sw_indent}}}\n")
    if stmt.str_disc_kind == "char_at":
        out.write(f"{indent}}}\n")
    for entry in stmt.str_trailing:
        state.comments.stmt(out, entry.loc, indent)
        out.write(f"{indent}{{\n")
        _emit_match_binding(out, entry.binding, subject, inner)
        _emit_match_goto_tail(out, entry, indent_level, state, end_label)
    out.write(f"{indent}{end_label}:;\n")


def _emit_match_if_elif(out: TextIO, stmt: THIRMatch, indent_level: int,
                        state: _EmitState, subject: str,
                        paren_or: bool = True) -> None:
    # _gen_match_if_elif's unguarded chain, arms in source order: per arm the
    # comment, then `if (cond) {` / `} else if (cond) {` (the wildcard arm is
    # `{` / `} else {`), the body one level in; one closing brace ends the
    # chain. Conditions compose `{subject} == {rhs}` per pre-rendered
    # alternative, or-patterns ||-joined in parens (a single alternative
    # stays bare -- _gen_match_if_elif_cond's join). No break, no default,
    # no switch_depth: a chain is not a switch, so `break` inside an arm
    # exits the loop directly like any if body.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if not arm.labels:
            out.write(f"{indent}{{\n" if i == 0 else f"{indent}}} else {{\n")
        else:
            conds = [f"{subject} == {rhs}" for rhs in arm.labels]
            # The optional inner chain joins or-alternatives bare
            # (_emit_optional_inner_if_elif); the top-level chain wraps
            # (_gen_match_if_elif_cond's join).
            joined = " || ".join(conds)
            cond = (conds[0] if len(conds) == 1
                    else joined if not paren_or else f"({joined})")
            keyword = "if" if i == 0 else "} else if"
            out.write(f"{indent}{keyword} ({cond}) {{\n")
        _emit_match_binding(out, entry.binding, subject, inner)
        _emit_stmts(out, entry.body, indent_level + 1, state)
    out.write(f"{indent}}}\n")


def _emit_match_if_elif_guarded(out: TextIO, stmt: THIRMatch,
                                indent_level: int, state: _EmitState,
                                subject: str) -> None:
    # _gen_match_if_elif_guarded's standalone-if + goto shape: the end label
    # draws the SECOND per-function counter bump (the subject took the
    # first); each arm is its own `if (cond) {` (bare `{` for an
    # always-match arm) so a failed guard falls out of the block to the
    # next arm; _emit_guarded_arm_tail places the guarded body + goto two
    # levels in (inside the guard if), the unguarded one level. The label
    # line closes the match.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if not arm.labels:
            out.write(f"{indent}{{\n")
        else:
            conds = [f"{subject} == {rhs}" for rhs in arm.labels]
            cond = conds[0] if len(conds) == 1 else "(" + " || ".join(conds) + ")"
            out.write(f"{indent}if ({cond}) {{\n")
        _emit_match_binding(out, entry.binding, subject, inner)
        if entry.guard is not None:
            out.write(f"{inner}if ({_emit_expr(entry.guard, state)}) {{\n")
            _emit_stmts(out, entry.body, indent_level + 2, state)
            out.write(f"{INDENT * (indent_level + 2)}goto {end_label};\n")
            out.write(f"{inner}}}\n")
        else:
            _emit_stmts(out, entry.body, indent_level + 1, state)
            out.write(f"{inner}goto {end_label};\n")
        out.write(f"{indent}}}\n")
    out.write(f"{indent}{end_label}:;\n")


def _record_or_cond(or_conds, subject: str) -> str:
    # The or-branches' or_parts join: each alternative's conds &&-joined in
    # parens (empty alternatives were skipped at lowering), ||-joined.
    parts = ["(" + " && ".join(f"{pre}{subject}{suf}" for pre, suf in grp)
             + ")" for grp in or_conds]
    return " || ".join(parts)


def _emit_match_if_elif_record(out: TextIO, stmt: THIRMatch,
                               indent_level: int, state: _EmitState,
                               subject: str) -> None:
    # _gen_match_if_elif_record's unguarded chain, arms in source order: a
    # class arm's field conditions &&-join into `if (...)` / `} else if
    # (...)` (condition-free class arms and wildcards open bare `{` / `}
    # else {`), then the field capture bindings, the whole-subject
    # capture/`as` binding, and the body one level in; an or-pattern arm
    # renders its ||-joined alternative groups and carries NO bindings.
    # One closing brace ends the chain (the gate keeps the always-match
    # arm last, so no `} else { ... } else if` can arise).
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        keyword = "if" if i == 0 else "} else if"
        if entry.or_conds is not None:
            if entry.or_conds:
                cond = _record_or_cond(entry.or_conds, subject)
                out.write(f"{indent}{keyword} ({cond}) {{\n")
            else:
                out.write(f"{indent}{{\n" if i == 0
                          else f"{indent}}} else {{\n")
            _emit_stmts(out, entry.body, indent_level + 1, state)
            continue
        conds = [f"{pre}{subject}{suf}" for pre, suf in entry.field_conds]
        if conds:
            out.write(f"{indent}{keyword} ({' && '.join(conds)}) {{\n")
        else:
            out.write(f"{indent}{{\n" if i == 0
                      else f"{indent}}} else {{\n")
        for fb in entry.field_bindings:
            _emit_match_binding(out, fb, subject, inner)
        _emit_match_binding(out, entry.binding, subject, inner)
        _emit_stmts(out, entry.body, indent_level + 1, state)
    out.write(f"{indent}}}\n")


def _emit_match_guarded_record(out: TextIO, stmt: THIRMatch,
                               indent_level: int, state: _EmitState,
                               subject: str) -> None:
    # _gen_match_guarded_record's standalone-if + goto shape (the end label
    # draws the second per-function counter bump): a class/wildcard arm
    # opens `if (conds) {` (bare `{` without conditions), binds its fields
    # and whole-subject name, then nests the guard as `if (guard) { <body>
    # goto end; }` two levels in (unguarded: body + goto one level). An
    # or-pattern arm composes the guard INTO the block condition
    # (`(conds) && guard`, or the bare guard when its groups collapsed
    # empty) with the body + goto one level in regardless.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if entry.or_conds is not None:
            if entry.or_conds:
                cond = _record_or_cond(entry.or_conds, subject)
                if entry.guard is not None:
                    cond = f"({cond}) && {_emit_expr(entry.guard, state)}"
                out.write(f"{indent}if ({cond}) {{\n")
            elif entry.guard is not None:
                out.write(f"{indent}if "
                          f"({_emit_expr(entry.guard, state)}) {{\n")
            else:
                out.write(f"{indent}{{\n")
            _emit_stmts(out, entry.body, indent_level + 1, state)
            out.write(f"{inner}goto {end_label};\n")
            out.write(f"{indent}}}\n")
            continue
        conds = [f"{pre}{subject}{suf}" for pre, suf in entry.field_conds]
        if conds:
            out.write(f"{indent}if ({' && '.join(conds)}) {{\n")
        else:
            out.write(f"{indent}{{\n")
        for fb in entry.field_bindings:
            _emit_match_binding(out, fb, subject, inner)
        _emit_match_binding(out, entry.binding, subject, inner)
        if entry.guard is not None:
            out.write(f"{inner}if ({_emit_expr(entry.guard, state)}) {{\n")
            _emit_stmts(out, entry.body, indent_level + 2, state)
            out.write(f"{INDENT * (indent_level + 2)}goto {end_label};\n")
            out.write(f"{inner}}}\n")
        else:
            _emit_stmts(out, entry.body, indent_level + 1, state)
            out.write(f"{inner}goto {end_label};\n")
        out.write(f"{indent}}}\n")
    out.write(f"{indent}{end_label}:;\n")


def _emit_stmt(out: TextIO, stmt: THIRStmt, indent_level: int, state: _EmitState) -> None:
    indent = INDENT * indent_level
    state.stmt_indent_level = indent_level
    if isinstance(stmt, THIRVarDecl):
        name = escape_cpp_name(stmt.name)
        if stmt.cpp_local_representation is LocalBinding.REBIND_SLOT:
            # F2d two-slot rvalue pointer-local: a direct init slot holding the
            # value (so an alias taken before a reseat survives) + an empty
            # `std::optional<T>` rebind slot reused on each reseat. Mirrors
            # _gen_pointer_local_init's rvalue branch: the init slot is allocated
            # before the rebind slot.
            init_slot = state.next_slot()
            rebind_slot = state.next_slot()
            state.rebind_slots[stmt.name] = rebind_slot
            cpp = stmt.cpp_type
            const_pfx = "const " if stmt.is_const else ""
            out.write(f"{indent}{cpp} __slot_{init_slot} = {_emit_expr(stmt.init, state)};\n")
            out.write(f"{indent}std::optional<{cpp}> __slot_{rebind_slot};\n")
            out.write(f"{indent}{const_pfx}{cpp}* {name} = &__slot_{init_slot};\n")
        elif stmt.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS:
            # F3 storage-tuple alias: `auto&& name = <lvalue storage tuple>` binds a
            # forwarding reference to the source's storage (no spelled type). Reads
            # off it lift via tuple_to_pointer at borrow boundaries.
            out.write(f"{indent}auto&& {name} = {_emit_expr(stmt.init, state)};\n")
        elif stmt.cpp_local_representation is LocalBinding.PTR_VARIANT:
            # F4 U2 pointer-variant local: cpp_type carries the full (possibly
            # const-pointee) variant spelling -- no sigil, no const prefix.
            out.write(f"{indent}{stmt.cpp_type} {name} = {_emit_expr(stmt.init, state)};\n")
        elif stmt.cpp_local_representation is not None:
            # Non-value borrow local. cpp_type is already the pointee record (the
            # optional's inner for OPTIONAL_TO_PTR, not the optional itself), so
            # the sigil alone distinguishes the `T&` alias from the `T*`.
            const_pfx = "const " if stmt.is_const else ""
            sigil = ("&" if stmt.cpp_local_representation is LocalBinding.REF_ALIAS
                     else "*")
            out.write(f"{indent}{const_pfx}{stmt.cpp_type}{sigil} {name} = "
                      f"{_emit_expr(stmt.init, state)};\n")
        elif stmt.init is None:
            cpp = stmt.cpp_type if stmt.cpp_type is not None \
                else stmt.resolved_type.to_cpp()
            out.write(f"{indent}{cpp} {name};\n")
        else:
            # Render before flushing: the init may register arg temps, whose
            # decls the AST flushes between the source comment and the
            # statement line (gen_stmt's single flush point).
            # cpp_type (when set at lowering -- enum decls) overrides the
            # bare to_cpp() spelling.
            cpp_type = stmt.cpp_type if stmt.cpp_type is not None \
                else stmt.resolved_type.to_cpp()
            init_cpp = _emit_expr(stmt.init, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{cpp_type} {name} = {init_cpp};\n")
    elif isinstance(stmt, THIRAssign):
        # target is a THIRName (`x = ...`) or, for F2b, a THIRFieldAccess
        # (`recv.field = ...` / `recv->field = ...`); _emit_expr renders both. An
        # F2d rebind-slot pointer-local reseat reuses its optional slot:
        # `p = &*(__slot_N = <rvalue>);`.
        if isinstance(stmt.target, THIRName) and stmt.target.name in state.rebind_slots:
            slot = state.rebind_slots[stmt.target.name]
            out.write(f"{indent}{escape_cpp_name(stmt.target.name)} = "
                      f"&*(__slot_{slot} = {_emit_expr(stmt.value, state)});\n")
        else:
            # Receiver eval (class-constant writes) renders first, then the
            # value (its arg temps flush before the line); targets are
            # names/field lvalues that never register temps.
            recv_cpp = (_emit_expr(stmt.recv_eval, state)
                        if stmt.recv_eval is not None else None)
            target_cpp = _emit_expr(stmt.target, state)
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            if recv_cpp is not None:
                out.write(f"{indent}{stmt.recv_wrap.format(recv_cpp)};\n")
            out.write(f"{indent}{target_cpp} = {value_cpp};\n")
    elif isinstance(stmt, THIRSetItem):
        # Mirrors _gen_assign_code's subscript arm (and the aug-assign
        # subscript arm, whose synthetic binop value arrives pre-built):
        # bounds-safe writes share _emit_subscript's operator[] render;
        # checked writes call the free-function dunder.
        value_cpp = _emit_expr(stmt.value, state)
        if stmt.target.bounds_safe:
            target_cpp = _emit_subscript(stmt.target, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{target_cpp} = {value_cpp};\n")
        else:
            recv_cpp = _emit_expr(stmt.target.receiver, state)
            idx_cpp = _emit_expr(stmt.target.index, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}::tpy::__setitem__({recv_cpp}, {idx_cpp}, "
                      f"{value_cpp});\n")
    elif isinstance(stmt, THIRStrAppend):
        # `t += v;` -- the str in-place append (the `+=` statement and the
        # `x = x + y` peephole share the emit).
        out.write(f"{indent}{escape_cpp_name(stmt.target)} += "
                  f"{_emit_expr(stmt.value, state)};\n")
    elif isinstance(stmt, THIRFrameSlotWrite):
        # `name.emplace(value);` -- a resumable frame_slot local write (R1c).
        # Render the value first so its arg temps flush before the line
        # (mirroring the AST frame_slot write's single flush point). A
        # brace-init value takes the typed_brace_init type prefix so it binds
        # to emplace's forwarding ref (a record-ctor value is self-describing).
        value_cpp = _emit_expr(stmt.value, state)
        if value_cpp.startswith("{") and stmt.cpp_type is not None:
            value_cpp = f"{stmt.cpp_type}{value_cpp}"
        state.temps.flush(out, indent)
        out.write(f"{indent}{escape_cpp_name(stmt.name)}.emplace({value_cpp});\n")
    elif isinstance(stmt, THIRNarrowAlias):
        # The isinstance-narrowing extraction (F4 U3) -- mirrors
        # _emit_isinstance_extractions' variant arm (VariantAccess.get_by_type
        # with lvalue=True: the ptr-variant deref carries no outer parens).
        qualifier = "const auto&" if stmt.const_ref else "auto&"
        deref = "*" if stmt.is_ptr_variant else ""
        out.write(f"{indent}{qualifier} {stmt.alias} = {deref}"
                  f"std::get<{stmt.member_cpp}>({stmt.variant_cpp});\n")
    elif isinstance(stmt, THIRAssert):
        # The narrowing alias (if any) follows as its own THIRNarrowAlias.
        if stmt.message is None:
            throw = "::tpy::raise_assertion_error()"
        elif isinstance(stmt.message, str):
            msg = stmt.message.replace("\\", "\\\\").replace('"', '\\"')
            throw = f'::tpy::raise_assertion_error("{msg}")'
        else:
            throw = None
        if (stmt.fold_constant and isinstance(stmt.condition, THIRLiteral)
                and stmt.condition.value in (True, False, None)):
            if stmt.condition.value is True:
                return
            if throw is None:
                throw = ("::tpy::raise_assertion_error("
                         f"{_emit_expr(stmt.message, state)})")
                state.temps.flush(out, indent)
            out.write(f"{indent}{throw};\n")
        elif throw is not None:
            out.write(
                f"{indent}if (!({_emit_expr(stmt.condition, state)})) {throw};\n")
        else:
            out.write(f"{indent}if (!({_emit_expr(stmt.condition, state)})) {{\n")
            inner = indent + INDENT
            message = _emit_expr(stmt.message, state)
            state.temps.flush(out, inner)
            out.write(f"{inner}::tpy::raise_assertion_error({message});\n")
            out.write(f"{indent}}}\n")
    elif isinstance(stmt, THIRReturn):
        if state.finally_frames:
            _emit_finally_return(out, stmt, indent, state)
        elif stmt.value is None:
            out.write(f"{indent}return;\n")
        else:
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}return {value_cpp};\n")
    elif isinstance(stmt, THIRIf):
        _emit_if(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRWhile):
        _emit_while(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRNestedDef):
        _emit_nested_def(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRForRange):
        _emit_for_range(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRForEach):
        _emit_for_each(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRWith):
        _emit_with(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRTry):
        _emit_try(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRMatch):
        _emit_match(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRPrint):
        _emit_print(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRExprStmt):
        expr_cpp = _emit_expr(stmt.expr, state)
        state.temps.flush(out, indent)
        out.write(f"{indent}{expr_cpp};\n")
    elif isinstance(stmt, THIRTupleUnpack):
        # Mirrors _gen_tuple_unpack's slice arm: each non-discard target
        # declares a fresh value-scalar local. The source bind splits on shape
        # -- a bare-name / loop-shadow source is ref-bound (`const auto&`, no
        # owned/ref elements), a call / field rvalue is materialized by value
        # (`auto`, the AST's non-name `else` arm; its arg temps flush before the
        # bind line, exactly like a bare expr statement).
        tmp = f"__tup_{state.next_unpack()}"
        if stmt.source_expr is not None:
            src_cpp = _emit_expr(stmt.source_expr, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}auto {tmp} = {src_cpp};\n")
        else:
            out.write(f"{indent}const auto& {tmp} = "
                      f"{escape_cpp_name(stmt.source)};\n")
        for i, (name, cpp) in enumerate(zip(stmt.targets, stmt.target_cpps)):
            if name is None:
                continue
            out.write(f"{indent}{cpp} {escape_cpp_name(name)} = "
                      f"std::get<{i}>({tmp});\n")
    elif isinstance(stmt, THIRBreak):
        _emit_loop_exit(out, indent, state, is_break=True)
    elif isinstance(stmt, THIRContinue):
        _emit_loop_exit(out, indent, state, is_break=False)
    elif isinstance(stmt, THIRRaise):
        # Mirrors _gen_raise's throw-tier arms: a raise never walks the
        # finally-frame stack -- the throw propagates through the emitted
        # catch(...) arms, which run the finally bodies.
        if stmt.cpp_type is None:
            out.write(f"{indent}throw;\n")
        elif stmt.args:
            args = ", ".join(_emit_expr(a, state) for a in stmt.args)
            if stmt.via_virtual:
                out.write(f"{indent}{stmt.cpp_type}({args}).__raise__();\n")
            else:
                out.write(f"{indent}throw {stmt.cpp_type}({args});\n")
        elif stmt.via_virtual:
            out.write(f"{indent}{stmt.cpp_type}{{}}.__raise__();\n")
        else:
            out.write(f"{indent}throw {stmt.cpp_type}{{}};\n")
    elif isinstance(stmt, THIRParamCopy):
        # Mutable owned copy of a reassigned const-ref param; the signature
        # (AST-emitted) renamed the param to `__param_{name}`.
        out.write(f"{indent}{stmt.cpp_type} {stmt.name} = "
                  f"__param_{stmt.name};\n")
    elif isinstance(stmt, THIRDelVar):
        # `{ auto __del_sink = std::move(name); }` per sunk name -- the value
        # moves into a block-scoped temp destroyed immediately (early release).
        for name, deref in stmt.sinks:
            sigil = "*" if deref else ""
            out.write(f"{indent}{{ auto __del_sink = "
                      f"std::move({sigil}{name}); }}\n")
    elif isinstance(stmt, THIRNoOpStmt):
        # No code -- the `// pass` source comment (if any) is emitted by the
        # caller (_emit_stmts) from the node's loc.
        pass
    else:
        raise THIRCodeGenError(f"unhandled THIR stmt: {type(stmt).__name__}")


def _emit_print_arg(a: THIRPrintArg, state: _EmitState) -> str:
    inner = _emit_expr(a.expr, state)
    if a.print_form is PrintForm.BOOL:
        return f"::tpy::print_bool({inner})"
    if a.print_form is PrintForm.FLOAT:
        return f"::tpy::print_float({inner})"
    if a.print_form is PrintForm.FLOAT32:
        return f"::tpy::print_float(static_cast<double>({inner}))"
    if a.print_form is PrintForm.INT8:
        return f"static_cast<int>({inner})"
    if a.print_form is PrintForm.BYTES:
        return f"::tpy::BytesPrinter({inner})"
    if a.print_form is PrintForm.REPR:
        return f"::tpy::__repr__({inner})"
    if a.print_form is PrintForm.LIST:
        return f"::tpy::ListPrinter({inner})"
    if a.print_form is PrintForm.SET:
        return f"::tpy::SetPrinter({inner})"
    if a.print_form is PrintForm.DICT:
        return f"::tpy::DictPrinter({inner})"
    if a.print_form is PrintForm.TUPLE:
        return f"::tpy::TuplePrinter({inner})"
    if a.print_form is PrintForm.OPT_VAL:
        return f"::tpy::print_optional_val({inner})"
    if a.print_form is PrintForm.OPT_VAL_BOOL:
        return f"::tpy::print_optional_val<::tpy::print_bool, {a.opt_inner_cpp}>({inner})"
    if a.print_form is PrintForm.OPT_VAL_FLOAT:
        return f"::tpy::print_optional_val<::tpy::print_float, {a.opt_inner_cpp}>({inner})"
    return inner


def _emit_print(out: TextIO, stmt: THIRPrint, indent_level: int,
                state: _EmitState) -> None:
    # Mirrors gen_print's no-kwargs common-arg path: `std::cout << a0 << " " << a1
    # << ... << "\n";`. Default sep=" " between args, end="\n"; empty print() is
    # just the newline.
    indent = INDENT * indent_level
    parts = []
    for i, a in enumerate(stmt.args):
        if i > 0:
            parts.append('" "')
        parts.append(_emit_print_arg(a, state))
    parts.append('"\\n"')
    # Args render first: their hoisted temps flush before the cout line
    # (the AST's pre-statement `ctx.temps.flush`).
    state.temps.flush(out, indent)
    out.write(f"{indent}std::cout << " + " << ".join(parts) + ";\n")


def _emit_stmts(out: TextIO, stmts, indent_level: int, state: _EmitState) -> None:
    indent = INDENT * indent_level
    for stmt in stmts:
        # A desugar-expanded statement (no_source_comment) shares the first
        # statement's source comment -- skip the repeat, mirroring the AST path.
        if not stmt.no_source_comment:
            state.comments.stmt(out, stmt.loc, indent)
        _emit_stmt(out, stmt, indent_level, state)


def emit_thir_body(out: TextIO, fn: THIRFunction, indent_level: int = 1,
                   *, comments: CommentSink | None = None,
                   temps: TempSink | None = None,
                   with_counter: ModuleCounter | None = None,
                   try_counter: ModuleCounter | None = None,
                   return_cpp: 'str | None' = None) -> None:
    """Emit `fn`'s body statements (no signature, no braces) at `indent_level`.

    `temps` is the `__tmp_N` sink, `with_counter` the `__ctx_N` sink, and
    `try_counter` the `__after_else_N` else-label sink -- all
    module-cumulative, so the codegen seam passes the ctx-backed
    implementations (CtxTempSink / CtxCounter); the defaults are fresh local
    sinks (standalone/unit callers). `return_cpp` is the signature's return
    spelling (`ctx.current_return_cpp` at the seam), read only by the
    finally-chain return temp decl."""
    _emit_stmts(out, fn.body, indent_level,
                _EmitState(comments or _NO_COMMENTS, temps=temps or TempSink(),
                           with_counter=with_counter or ModuleCounter(),
                           try_counter=try_counter or ModuleCounter(),
                           return_cpp=return_cpp))


def emit_thir_constructor_tail(out: TextIO, ctor: THIRConstructor,
                               *, comments: CommentSink | None = None,
                               temps: TempSink | None = None,
                               with_counter: ModuleCounter | None = None,
                               try_counter: ModuleCounter | None = None) -> None:
    """Emit a constructor's member-init-list + body tail (the ` : f(v)... {}` that
    follows the signature). The THIR counterpart of gen_record_decl's AST MIL+body
    emit: the signature is written by the AST path before this is called (the M1
    precedent -- signatures stay on the AST path). Byte-identical to that path's
    tail. M3a is pure-MIL, so `body` is empty and this emits ` {}` (or
    ` : inits {}`). MIL / base-init cells have no flush point, so arg temps
    never lower there (gate + validator enforced); the body shares the
    statement machinery and its sink."""
    state = _EmitState(comments or _NO_COMMENTS, temps=temps or TempSink(),
                       with_counter=with_counter or ModuleCounter(),
                       try_counter=try_counter or ModuleCounter())
    inits = [f"{bi.base_cpp}({', '.join(_emit_expr(a, state) for a in bi.args)})"
             for bi in ctor.base_inits]
    inits.extend(
        f"{mi.field_cpp}(std::move({_emit_expr(mi.value, state)}))" if mi.move
        else f"{mi.field_cpp}({_emit_expr(mi.value, state)})"
        for mi in ctor.mil_inits)
    if inits:
        out.write(" : ")
        out.write(", ".join(inits))
    if ctor.body:
        out.write(" {\n")
        _emit_stmts(out, ctor.body, 2, state)
        out.write(f"{INDENT}}}\n")
    else:
        out.write(" {}\n")


class ResumableLeafEmitter:
    """Per-routed-body leaf renderer driven by the shared resumable-frame
    skeleton (`gen_async`). One instance per routed body holds one
    `_EmitState`, so per-function streams (rebind slots, `__tup_N`) behave
    as one body across leaf calls while the module-cumulative streams come
    from the ctx-backed sinks -- the same contract as `emit_thir_body`.

    The skeleton looks up exactly the leaves lowering stored, keyed by the
    id() of the AST node it holds; a missing key means the gate and the
    seam disagree on the routed body's shape -- a hard error, never a
    silent per-leaf fallback (per-body routing is all-or-nothing)."""

    def __init__(self, body, *, comments: 'CommentSink | None' = None,
                 temps: 'TempSink | None' = None,
                 with_counter: 'ModuleCounter | None' = None,
                 try_counter: 'ModuleCounter | None' = None,
                 return_cpp: 'str | None' = None) -> None:
        self._body = body
        self._state = _EmitState(comments or _NO_COMMENTS,
                                 temps=temps or TempSink(),
                                 with_counter=with_counter or ModuleCounter(),
                                 try_counter=try_counter or ModuleCounter(),
                                 return_cpp=return_cpp)

    def _lookup(self, table, node, what: str):
        if id(node) not in table:
            raise THIRCodeGenError(
                f"resumable seam: routed body has no lowered {what} for "
                f"{type(node).__name__} (lowering/seam disagreement)")
        return table[id(node)]

    def emit_leaf_stmt(self, out: TextIO, stmt, indent_level: int) -> None:
        """Emit one BB leaf statement (or a RaiseT terminator's statement),
        source comment included -- the seam replacement for the skeleton's
        `statements.gen_stmt(out, stmt)` calls."""
        node = self._lookup(self._body.leaves, stmt, "leaf statement")
        _emit_stmts(out, (node,), indent_level, self._state)

    def render_cond(self, cond) -> str:
        """Render a Branch terminator's condition; arg temps queue on the
        shared ctx sink and flush at the skeleton's existing flush point."""
        return _emit_expr(self._lookup(self._body.conds, cond, "condition"),
                          self._state)

    def render_await_args(self, call) -> 'list[str]':
        """Render a suspension's sub-coro emplace arguments (same flush
        contract as `render_cond`)."""
        args = self._lookup(self._body.await_args, call, "await args")
        return [_emit_expr(a, self._state) for a in args]

    def render_return_value(self, ret) -> str:
        """Render a `return v`'s value for `_make_async_return`'s
        scaffolding (the ret-tmp init / pending-slot store)."""
        return _emit_expr(self._lookup(self._body.return_values, ret,
                                       "return value"), self._state)

    def render_yield_value(self, ys) -> str:
        """Render a generator `yield v`'s value -- the seam replacement for
        the skeleton's `statements.gen_yield_value(ys)`."""
        return _emit_expr(self._lookup(self._body.yield_values, ys,
                                       "yield value"), self._state)

    def render_suspend_expr(self, expr) -> str:
        """Render an ERASED/BORROWED await operand or a bound-method await
        receiver -- the seam replacement for the skeleton's
        `gen_expr(operand)` / `gen_expr(call.obj)` at the suspend site (the
        skeleton keeps its move / & / .get() / __self-prepend wrap)."""
        return _emit_expr(self._lookup(self._body.suspend_exprs, expr,
                                       "suspend expr"), self._state)
