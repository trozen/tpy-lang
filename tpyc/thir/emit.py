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
from typing import Callable, TextIO

from ..codegen_cpp.context import (
    INDENT, any_isinstance_check, cpp_bytes_literal_owned, cpp_bytes_literal_span,
    cpp_string_literal_expr, escape_cpp_char, escape_cpp_name,
    escape_cpp_string, expand_cpp_template, loop_var_binding,
    qualify_native_name,
)
from ..codegen_cpp.forms import LocalBinding, is_plain_nonvalue
from ..type_def_registry import (
    is_array, is_bytearray_type, is_bytes_type, is_bytes_view_type, is_dict,
    is_float32_type, is_list,
    is_set, is_str_type, is_string_type, view_to_owned_conv,
)
from ..typesys import (NoneType, OptionalType, TpyType, TupleType,
                       TypeParamRef, UnionType, VoidType, unwrap_qualifiers,
                       view_family_for_type)
from .nodes import (
    Form,
    PrintForm,
    PtrSlotKind,
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
    THIRWalrus,
    THIRComprehension,
    THIRGenExpr,
    THIRClassConstant,
    THIRCoerce,
    THIRConstructor,
    THIRContainerLiteral,
    THIRListRepeat,
    THIRContinue,
    THIRDelVar,
    THIRCtorCall,
    THIRConceptTest,
    THIREnumMember,
    THIREnumWrap,
    THIRErrorReturnBind,
    THIRErrorReturnDiscard,
    THIRErrorReturnUnwrap,
    THIRExpr,
    THIRExprStmt,
    THIRFieldAccess,
    THIRConsumingIter,
    THIRCopy,
    THIRForEach,
    THIRForIterProto,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRFunction,
    THIRIf,
    THIRIfExpr,
    THIRIsNone,
    THIRValueSelect,
    THIRMembership,
    THIRStrMembership,
    THIRTupleMembership,
    THIRIsinstance,
    THIRAnyIsinstance,
    THIRDynIsinstance,
    THIRDynIsinstanceMulti,
    THIRLiteral,
    THIRMatch,
    THIRMatchBinding,
    THIRLambda,
    THIRMethodCall,
    THIRModuleVar,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRAnyNarrowAlias,
    THIRNestedDef,
    THIRNarrowedRead,
    THIRFrameNestedDef,
    THIRNoOpStmt,
    THIRFoldedBlock,
    THIRMatchFoldBind,
    THIROptionalPtrArg,
    THIRParamCopy,
    THIRPrint,
    THIRPrintChain,
    THIRPrintArg,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRRaise,
    THIRResumableReturn,
    THIRReturn,
    THIRStmtSeq,
    THIRSetItem,
    THIRSelf,
    THIRSliceAssign,
    THIRInplaceContainerOp,
    THIRStmt,
    THIRFrameSlotWrite,
    THIRStrAppend,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRTry,
    THIRRecordCopy,
    THIRBorrowTupleLiteral,
    THIRTupleValueToBorrow,
    THIRTupleLiteral,
    THIRTupleUnpack,
    TupleSourceBind,
    THIRTruthy,
    THIROptViewArg,
    THIRUnaryNot,
    THIRUnaryArith,
    THIRUnionArgLift,
    THIRVarDecl,
    THIRVarargPack,
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

    def inline(self, out: TextIO, loc, indent: str) -> None:
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
        self._pending_named: list[tuple[str, str, 'str | None']] = []

    def create(self, cpp_type: str, init_expr: str, *,
               brace_init: bool = False) -> str:
        self._counter += 1
        name = f"__tmp_{self._counter}"
        self._pending.append((name, cpp_type, init_expr, brace_init))
        return name

    def declare_named(self, name: str, cpp_type: str, *,
                      init: 'str | None' = None) -> None:
        """Register a named pre-declaration (walrus target) -- rendered
        `type name[ = init];` ahead of the anonymous temps, like
        TempState's."""
        self._pending_named.append((name, cpp_type, init))

    def checkpoint(self) -> tuple[int, int]:
        """Snapshot the pending queues -- the cond-position seam
        (`has_*_since` / `flush_since` take this token), mirroring
        `TempState.checkpoint`."""
        return (len(self._pending), len(self._pending_named))

    def has_pending_since(self, checkpoint: tuple[int, ...]) -> bool:
        return len(self._pending) > checkpoint[0]

    def has_named_since(self, checkpoint: tuple[int, ...]) -> bool:
        return len(self._pending_named) > checkpoint[1]

    def flush_since(self, out: TextIO, checkpoint: tuple[int, int],
                    indent: str) -> None:
        """Emit (and remove) only the anonymous temps registered after
        `checkpoint` -- the restructured loop-head / nested-elif flush."""
        pending_n = checkpoint[0]
        for name, cpp_type, init_expr, brace_init in self._pending[pending_n:]:
            if brace_init:
                out.write(f"{indent}{cpp_type} {name}{{{init_expr}}};\n")
            else:
                out.write(f"{indent}{cpp_type} {name} = {init_expr};\n")
        del self._pending[pending_n:]

    def flush(self, out: TextIO, indent: str) -> None:
        for name, cpp_type, init in self._pending_named:
            if init is not None:
                out.write(f"{indent}{cpp_type} {name} = {init};\n")
            else:
                out.write(f"{indent}{cpp_type} {name};\n")
        self._pending_named.clear()
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

    def declare_named(self, name: str, cpp_type: str, *,
                      init: 'str | None' = None) -> None:
        self._ctx.temps.declare_named(name, cpp_type, init=init)

    def checkpoint(self) -> tuple[int, int]:
        return self._ctx.temps.checkpoint()

    def has_pending_since(self, checkpoint: tuple[int, ...]) -> bool:
        return self._ctx.temps.has_pending_since(checkpoint)

    def has_named_since(self, checkpoint: tuple[int, ...]) -> bool:
        return self._ctx.temps.has_named_since(checkpoint)

    def flush_since(self, out: TextIO, checkpoint: tuple[int, int],
                    indent: str) -> None:
        self._ctx.temps.flush_since(out, checkpoint, indent)

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
    # Mirrors FinallyContext.guard_name: the `bool __fin_ran_N` set right
    # before an exit-site copy so this frame's own catch skips its copy,
    # declared only where an exit site actually walked the frame -- tracked
    # by membership of the name in _EmitState.live_finally_guards.
    guard_name: 'str | None' = None


@dataclass
class _EmitState:
    """Per-function emit state. `iter_counter` reproduces `ctx.iter_counter`:
    in the eligible slice only range-`for` loops bump it, and it resets per
    function, so a counter seeded at 0 here and bumped once per loop (pre-order)
    matches the AST path's `__start_N`/`__stop_N` numbering exactly.

    `slot_counter` reproduces `ctx.slots` for F2d rebind-slot pointer-locals
    and reassigned borrow-tuple walruses: within the eligible slice only
    those bump it (the other `__slot_N` consumers -- unions, @dynamic -- are
    gated out), and it pre-increments per allocation just like
    `SlotState.next_slot`, so the `__slot_N` numbering matches the AST path.
    `rebind_slots` maps a rebind-slot local's name to its optional rebind
    slot N (allocated at the decl / first walrus, read at each reseat) --
    the analog of `ctx.rebind_slots`.

    `temps` is the `__tmp_N` sink THIRArgTemp renders through, flushed before
    the enclosing statement line (after its source comment, mirroring the AST's
    single flush point in `gen_stmt`). Unlike the counters above it is NOT
    per-function: the seam passes a CtxTempSink so the numbering stays
    module-cumulative across interleaved THIR/AST bodies."""
    comments: CommentSink
    temps: TempSink = field(default_factory=TempSink)
    # `with_counter` numbers `__ctx_N` (ctx attr `with_counter`); `try_counter`
    # numbers the throw tier's `__after_else_N` else labels, the return
    # tier's `__except_N`/`__after_try_N`/`__err_opt_N`, and the error_return
    # unwrap temps `__try_tmp_N`/`__er_N` (ctx attr `try_except_counter` --
    # one module-cumulative stream shared with the AST path).
    with_counter: ModuleCounter = field(default_factory=ModuleCounter)
    try_counter: ModuleCounter = field(default_factory=ModuleCounter)
    # Numbers the `__fin_ran_N` cleanup guards (ctx attr
    # `finally_guard_counter`); allocated per pushed finally frame, in the
    # AST's push order, so both paths land on the same names.
    finally_guard_counter: ModuleCounter = field(default_factory=ModuleCounter)
    return_cpp: 'str | None' = None
    # @error_return context, mirroring the AST ctx fields the error_return
    # renders read: `error_return_cpp` is the enclosing function's error type
    # (ctx.current_error_return; seeds bare-return `{}`, the void success
    # tail, and the propagate disposition); `try_except_label`/
    # `try_except_err_opt` are live while a return-tier try body emits (the
    # goto-except disposition + `as` capture); `in_except_tier` is the
    # enclosing handler's tier, read by the bare-raise re-raise arm.
    error_return_cpp: 'str | None' = None
    try_except_label: 'str | None' = None
    try_except_err_opt: 'str | None' = None
    in_except_tier: 'str | None' = None
    # Resumable-leaf shadow probe: the skeleton registers C++-local shadows of
    # frame fields (for-loop iter vars) in ctx.frame_field_shadows, and the
    # AST body-emit suppresses the `(*name)` peel for a shadowed name. The
    # leaf emitter wires this to the LIVE ctx set so a THIRName lowered with
    # deref=True (a frame-stored non-value local) renders bare exactly while
    # its shadow is in scope. None outside resumable leaves.
    frame_shadow_probe: 'Callable[[str], bool] | None' = None
    # Resumable MatchDispatch mode: the skeleton's arm emitter, keyed by a
    # case's body id -- called at each scalar-tier arm-body point instead
    # of emitting the (empty) lowered body. None for sync matches.
    match_arm_hook: 'Callable[[int, int], None] | None' = None
    # Resumable leaf-return mode: renders the FULL return scaffolding
    # (done-state, Poll wrap / StopIteration, finally-chain walk) for a
    # return nested in a leaf compound -- the skeleton's `_make_async_return`
    # / `_make_generator_resumable_return` bound to the live ctx, called
    # with (ast_stmt, indent_level). None outside resumable leaves.
    resumable_return_hook: 'Callable[[object, int], str] | None' = None
    iter_counter: int = 0
    slot_counter: int = 0
    unpack_counter: int = 0
    # Function-top hoist lines (content only, no indent/newline): a @dynamic
    # rebind slot's `std::optional<slot> __slot_N;` is allocated at the reassign
    # point but its DECL text precedes the whole body (the AST's
    # `pending_hoist_decls`). `emit_thir_body` drains this before the body.
    hoist_lines: list[str] = field(default_factory=list)
    # False in the generator LEAF emitters (Resumable/SimpleGen), which have no
    # drain point: a producer of `hoist_lines` asserts on it so a future
    # hoisting construct that slips past lowering's defer fails LOUD at the
    # produce site rather than emitting an undeclared `__slot_N`.
    hoist_drainable: bool = True
    rebind_slots: dict[str, int] = field(default_factory=dict)
    # Names whose rebind slot backs a ptr-variant UNION local: their rvalue
    # reseats spell `.emplace` + `to_ptr_variant(*slot)` via THIRPtrLocalRebind,
    # so a plain THIRAssign on them (a same-union name copy) must NOT take the
    # `&*(__slot_N = ...)` optional-slot reseat arm.
    union_slot_locals: set[str] = field(default_factory=set)
    # Names whose rebind slot backs a reassigned borrow-tuple WALRUS: their
    # later storage-alias reseats are plain assigns
    # (`t = tuple_to_pointer<..>(h.pair);`), never the optional-slot arm.
    btuple_slot_locals: set[str] = field(default_factory=set)
    # Enclosing `with` layers, innermost last -- return/break/continue walk it
    # to render the inline `__exit__` chain (the AST's `ctx.finally_stack`);
    # `loop_depth` mirrors `len(ctx.loop_else_labels)` (bumped around every
    # loop body) for the break/continue frame boundary. `return_cpp` is the
    # signature's return spelling (`ctx.current_return_cpp`), read only by the
    # finally-return temp.
    finally_frames: list[_FinallyFrame] = field(default_factory=list)
    # Live `bool __fin_ran_N` guard names -- the emit-side mirror of
    # CodeGenContext.live_finally_guards: a guard lands here when an exit
    # site emits its `= true`, and each frame's catch declares/tests the
    # guard only when its name is present (per-function; names are unique
    # via the module-cumulative finally_guard_counter).
    live_finally_guards: set[str] = field(default_factory=set)
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

    def inline(self, out: TextIO, loc, indent: str) -> None:
        # Leading `#`-comment trivia only (a skipped statement's comments;
        # the AST's gen_stmt emits these before the None-code suppression).
        self._ctx.emit_inline_comments(out, loc, indent)

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
        if isinstance(lit.result_type, NoneType):
            # A unit-typed None: the STORAGE form is the monostate VALUE
            # (`identity[None](None)`'s temp init); the VALUE form keeps the
            # target-less `nullptr` (a base-init arg).
            if lit.form is Form.STORAGE:
                return "std::monostate{}"
            return "nullptr"
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
    if e.template_override is not None:
        # The rebuilt fixed-int literal arm (gen_call_from_fi over the
        # target-typed operands): plain template expansion, no wrappers, no
        # parens, no divisor swap -- the AST's dedicated arm bypasses all of
        # those the same way.
        return expand_cpp_template(e.template_override, left, right)
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
    if e.op == "!=" and rb.method.name == "__eq__":
        # `!=` resolved via `__eq__` derives by negation -- `(!(...))`,
        # mirroring gen_binop's derived-negation wrap.
        return f"(!({result}))"
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
    # lowering, over the lowering-admitted args. @native_c PODs take the
    # aggregate `{args}` init.
    args = ", ".join(_emit_expr(a, state) for a in e.args)
    if e.brace_init:
        return f"{e.type_cpp}{{{args}}}"
    return f"{e.type_cpp}({args})"


def _emit_method_call(e: THIRMethodCall, state: _EmitState) -> str:
    # Mirrors gen_call_from_fi's three dispatch arms for a receiver call, in the
    # same order: cpp_template expansion, @native free-function symbol (receiver
    # prepended), plain member call. The member accessor is `->` only for a
    # user-record pointer-local receiver (`is_arrow`, the _gen_method_call
    # indirect-name arm); container receivers are pinned to bare names.
    recv = _emit_expr(e.receiver, state)
    if e.move_receiver:
        # Consuming method: the rvalue-qualified call moves the receiver
        # (_gen_method_call's is_consuming wrap; bare-name receivers only,
        # so no deref composes here).
        recv = f"std::move({recv})"
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
    if e.deref_chain:
        # User Deref-wrapper method call: N `.__deref__()` calls between the
        # bare receiver and the member call (`r.__deref__().sum()`); a
        # pointer-local receiver joins the first hop with `->`
        # (`g->__deref__().push_back(3)`).
        first = "->" if e.is_arrow else "."
        chain = (f"{first}__deref__()"
                 + ".__deref__()" * (e.deref_chain - 1))
        return f"{recv}{chain}.{e.method_cpp}({', '.join(args)})"
    mtargs = (f"<{', '.join(e.method_targs_cpp)}>"
              if e.method_targs_cpp else "")
    return (f"{recv}{'->' if e.is_arrow else '.'}"
            f"{e.method_cpp}{mtargs}({', '.join(args)})")


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
    if e.loop == "array_source":
        # The array_from_index SOURCE arm (_gen_array_comprehension's non-range
        # branch): a `({...})` prelude borrows the sized source once (lvalue
        # verdict) and the per-index lambda indexes it (`__obj_N[__i_N]`). The
        # loop-var binding is the shared non-const `loop_var_binding` (value
        # copy / `auto&&` borrow), matching the AST's value/non-value split.
        n = state.next_loop_index()
        obj = f"__obj_{n}"
        binding_kw = "auto&" if e.iterable_lvalue else "auto"
        buf = io.StringIO()
        buf.write("({\n")
        buf.write(f"{ind1}{binding_kw} {obj} = {_emit_expr(e.iterable, state)};\n")
        buf.write(f"{ind1}::tpy::array_from_index<{e.array_elem_cpp}, "
                  f"{e.array_size_cpp}>("
                  f"[&](std::size_t __i_{n}) -> {e.array_elem_cpp} {{\n")
        binding = loop_var_binding(
            e.elem_type, cpp_var, f"{obj}[__i_{n}]", False)
        buf.write(f"{ind2}{binding}\n")
        buf.write(f"{ind2}return {_emit_expr(e.element, state)};\n")
        buf.write(f"{ind1}}});\n")
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
        if e.value_moved:
            # Owned-move dict: the moved value leaves insert_or_assign's two args
            # unsequenced, so the key evaluates into a `__dk_N` local FIRST (a key
            # reading the moved-from loop var would otherwise be a use-after-move).
            # Render key then value (their own counters) before drawing __dk_N.
            key_s = _emit_expr(e.key, state)
            value_s = _emit_expr(e.value, state)
            dk = f"__dk_{state.next_unpack()}"
            insert = (f"{{ auto {dk} = {key_s}; __result.insert_or_assign("
                      f"std::move({dk}), {value_s}); }}")
        else:
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


def _emit_vararg_pack(e: 'THIRVarargPack', state: _EmitState) -> str:
    # Mirror _gen_vararg_pack: a sole `*expr` unpack forwards the container
    # directly (span source) or through a borrowed span, the empty pack takes
    # the nullary ctor, and the per-arg form hoists a std::array temp (element
    # temps first, then the array) exactly like the AST cascade.
    if e.star_source is not None:
        inner = _emit_expr(e.star_source, state)
        if e.span_fn is None:
            return f"::tpy::varargs<{e.elem_cpp}>({inner})"
        return f"::tpy::varargs<{e.elem_cpp}>(::tpy::{e.span_fn}({inner}))"
    if not e.args:
        return f"::tpy::varargs<{e.elem_cpp}>()"
    subs = []
    for i, a in enumerate(e.args):
        rendered = _emit_expr(a, state)
        if e.is_ref:
            if e.ref_lvalue[i]:
                subs.append(f"&{rendered}")
            else:
                tmp = state.temps.create(e.elem_cpp, rendered)
                subs.append(f"&{tmp}")
        else:
            subs.append(rendered)
    n = len(subs)
    init = ", ".join(subs)
    array_cpp = (f"std::array<{e.elem_cpp}*, {n}>" if e.is_ref
                 else f"std::array<{e.elem_cpp}, {n}>")
    temp = state.temps.create(array_cpp, init, brace_init=True)
    return f"::tpy::varargs<{e.elem_cpp}>({temp})"


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


def _emit_list_repeat(e: THIRListRepeat, state: _EmitState) -> str:
    """`[elems] * count` -- the _gen_list_repeat mirror. Elements render before
    the array counter draws / before the count (the AST computes `repeat_elems`
    first), so a counter drawn by an element keeps its AST position."""
    if is_array(unwrap_qualifiers(e.result_type)):
        # Aggregate build: evaluate the element(s) once, then array_from_index
        # copies each slot (from_range's array branch would default-construct N
        # spurious times). One `iter_counter` draw for `__rep_N`.
        rendered = [_emit_expr(x, state) for x in e.elements]
        n = state.next_loop_index()
        k = len(rendered)
        stmt_ind = INDENT * state.stmt_indent_level
        ind1 = stmt_ind + INDENT
        buf = io.StringIO()
        buf.write("({\n")
        if k == 1:
            buf.write(f"{ind1}{e.elem_cpp} __rep_{n} = {rendered[0]};\n")
            lam = f"[&](std::size_t) -> {e.elem_cpp} {{ return __rep_{n}; }}"
        else:
            buf.write(f"{ind1}std::array<{e.elem_cpp}, {k}> "
                      f"__rep_{n}{{{', '.join(rendered)}}};\n")
            lam = (f"[&](std::size_t __i_{n}) -> {e.elem_cpp} "
                   f"{{ return __rep_{n}[__i_{n} % {k}]; }}")
        buf.write(f"{ind1}::tpy::array_from_index<{e.elem_cpp}, "
                  f"{e.array_size_cpp}>({lam});\n")
        buf.write(f"{stmt_ind}}})")
        return buf.getvalue()
    elems = ", ".join(_emit_expr(x, state) for x in e.elements)
    count = _emit_expr(e.count, state)
    if e.count_bigint:
        # repeat_range's count is int32_t; a BigInt count checks-converts.
        count = f"{count}.to_fixed_check<int32_t>()"
    range_expr = f"::tpy::repeat_range<{e.elem_cpp}>({count}, {{{elems}}})"
    return f"::tpy::from_range<{e.result_cpp}>({range_expr})"


def _emit_field_access(e: THIRFieldAccess, state: _EmitState) -> str:
    if e.deref_check:
        # Unproven Optional member access: null-check the (already `T*`) receiver
        # before the `.` member read. Mirrors _gen_field_access's runtime-check path.
        return f"::tpy::deref_check({_emit_expr(e.receiver, state)}).{e.field_cpp}"
    if e.opt_deref_check:
        # Unproven access off a WHOLE value-repr Optional lvalue receiver
        # (`h.opt.x` -> `::tpy::deref_optional_check(h.opt).x`).
        return (f"::tpy::deref_optional_check({_emit_expr(e.receiver, state)})"
                f".{e.field_cpp}")
    if e.deref_chain:
        # User Deref-wrapper field access: N `__deref__()` calls between the
        # bare receiver and the field (`r.__deref__().x`); a pointer-local
        # receiver joins the first hop with `->` (`r->__deref__().x`).
        first = "->" if e.is_arrow else "."
        chain = (f"{first}__deref__()"
                 + ".__deref__()" * (e.deref_chain - 1))
        base = f"{_emit_expr(e.receiver, state)}{chain}.{e.field_cpp}"
        return f"(*{base})" if e.narrowed_deref else base
    base = f"{_emit_expr(e.receiver, state)}{'->' if e.is_arrow else '.'}{e.field_cpp}"
    # Sema-narrowed Optional field: the storage stays std::optional<T>, so the
    # value read unwraps unconditionally (gen_expr_deref's narrowed-field arm).
    return f"(*{base})" if e.narrowed_deref else base


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
    if e.record_getitem:
        # User-record operator[]: bare, no size_t cast (the operator takes the
        # user's declared key type -- mirrors _gen_subscript's fi fallback).
        return f"{recv}[{idx}]"
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
        if e.opt_deref_check:
            return f"::tpy::deref_optional_check({name})"
        if (e.deref and state.frame_shadow_probe is not None
                and state.frame_shadow_probe(e.name)):
            return name
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
    if isinstance(e, THIRWalrus):
        # The per-class walrus render (see the node doc); the first binding
        # registers its `type name[ = init];` pre-decl on the sink's named
        # row, which the enclosing statement / loop-head / lambda flush
        # places.
        if e.cpp_type is not None:
            state.temps.declare_named(e.cpp_name, e.cpp_type, init=e.init)
        v = _emit_expr(e.value, state)
        if e.slot_cpp is not None:
            # Reassigned borrow-tuple: the owning slot is allocated once per
            # target (sibling occurrences reuse it, the AST's rebind_slots
            # read) and declared on the named row next to the target.
            slot_n = state.rebind_slots.get(e.name)
            if slot_n is None:
                slot_n = state.next_slot()
                state.rebind_slots[e.name] = slot_n
                state.btuple_slot_locals.add(e.name)
                state.temps.declare_named(
                    f"__slot_{slot_n}", f"std::optional<{e.slot_cpp}>")
            v = (f"::tpy::tuple_to_pointer<{e.borrow_cpp}>"
                 f"(__slot_{slot_n}.emplace({v}))")
        if e.addr_of:
            v = f"&({v})"
        if e.tail == "deref":
            return f"({e.cpp_name} = {v}, *{e.cpp_name})"
        if e.tail == "name":
            return f"({e.cpp_name} = {v}, {e.cpp_name})"
        return f"({e.cpp_name} = {v})"
    if isinstance(e, THIRIsinstance):
        # Mirrors the AST isinstance arm over value/pointer variants: one
        # holds_alternative per check member, OR-joined and parenthesized for
        # the multi-member (tuple / inline-union) form.
        checks = [f"std::holds_alternative<{m}>({e.variant_cpp})"
                  for m in e.member_cpps]
        return checks[0] if len(checks) == 1 else "(" + " || ".join(checks) + ")"
    if isinstance(e, THIRDynIsinstance):
        # The C++17 if-init form: the whole `init; cond` sits inside the if's
        # own parens (mirrors _gen_if's `{init_clause}{cond}` composition).
        return f"{e.init_cpp}; ({e.ptr_local} != nullptr)"
    if isinstance(e, THIRDynIsinstanceMulti):
        # The tuple form's OR-chain; a single check (the root-class form)
        # renders bare (mirrors the AST isinstance arm's join rule).
        if len(e.checks_cpp) == 1:
            return e.checks_cpp[0]
        return "(" + " || ".join(e.checks_cpp) + ")"
    if isinstance(e, THIRAnyIsinstance):
        # The shared composition (any_isinstance_check) -- one spelling for
        # the AST isinstance arm's Any branch and this node.
        return any_isinstance_check(e.subject_cpp, e.member_cpps)
    if isinstance(e, THIRNarrowedRead):
        # A compound-condition read of the narrowed subject: the bare get, no
        # alias yet -- the ptr-variant deref parenthesizes for member access.
        get = f"std::get<{e.member_cpp}>({e.variant_cpp})"
        return f"(*{get})" if e.is_ptr_variant else get
    if isinstance(e, THIRFieldAccess):
        return _emit_field_access(e, state)
    if isinstance(e, THIRSubscript):
        sub = _emit_subscript(e, state)
        return (f"::tpy::deref_optional_check({sub})"
                if e.opt_deref_check else sub)
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
    if isinstance(e, THIRUnaryArith):
        # _gen_unaryop's resolved-dunder tail: expand the operator template
        # (`{self}` = operand) -- neg/pos/invert, checked or bare per the
        # method's own template.
        return expand_cpp_template(e.cpp_template, _emit_expr(e.operand, state))
    if isinstance(e, THIRMembership):
        # _gen_binop's resolved_contains arm: `(recv.contains(needle))`, the
        # negation wrapping the already-parenthesized find expr. A bytes
        # container's `__contains__` is a native FREE function, so it renders
        # `(::tpy::name(recv, needle))` instead.
        if e.ranges_contains:
            # `is_native_in` fallback: a bare `std::ranges::contains(recv,
            # needle)`, negation a `!` prefix (no outer parens).
            call = (f"std::ranges::contains({_emit_expr(e.receiver, state)}, "
                    f"{_emit_expr(e.needle, state)})")
            return f"!{call}" if e.negate else call
        if e.free_function:
            inner = (f"({qualify_native_name(e.method_cpp)}"
                     f"({_emit_expr(e.receiver, state)}, "
                     f"{_emit_expr(e.needle, state)}))")
        else:
            inner = (f"({_emit_expr(e.receiver, state)}.{e.method_cpp}"
                     f"({_emit_expr(e.needle, state)}))")
        return f"(!{inner})" if e.negate else inner
    if isinstance(e, THIRStrMembership):
        # _gen_binop's str `.find()` arm: `(s.find(needle) != npos)`, or
        # `== npos` for `not in`. A str-literal receiver wraps in string_view
        # (C string literals lack `.find`).
        recv = _emit_expr(e.receiver, state)
        if e.wrap_receiver_sv:
            recv = f"std::string_view({recv})"
        op = "==" if e.negate else "!="
        needle = _emit_expr(e.needle, state)
        return f"({recv}.find({needle}) {op} std::string::npos)"
    if isinstance(e, THIRTupleMembership):
        # _gen_binop's tuple-literal `in` arm: an OR-chain of `==` compares.
        left = _emit_expr(e.left, state)
        elems = [_emit_expr(el, state) for el in e.elements]
        if e.need_temp:
            joined = " || ".join(f"(__in_lhs == {el})" for el in elems)
            body = f"!({joined})" if e.negate else joined
            return f"({{ auto&& __in_lhs = {left}; {body}; }})"
        conditions = [f"({left} == {el})" for el in elems]
        joined = " || ".join(conditions) if conditions else "false"
        if e.negate:
            return f"(!({joined}))" if len(conditions) > 1 else f"(!{conditions[0]})"
        return f"({joined})"
    if isinstance(e, THIRValueSelect):
        # Value-position and/or (`_gen_logical_value`'s value slice): the
        # LHS renders (and hoists) FIRST so temp numbering matches the AST;
        # the RHS render sits inside the ternary branch (its EVALUATION is
        # lazy at runtime -- an RHS-nested temp would hoist above the
        # ternary exactly as on the AST path, but no admitted RHS shape
        # carries one).
        lhs_r = _emit_expr(e.lhs, state)
        if e.lhs_temp_cpp is not None:
            lhs_r = state.temps.create(e.lhs_temp_cpp, lhs_r)
        truthy = f"(!{lhs_r}.empty())" if e.truthy_nonempty else lhs_r
        rhs_r = _emit_expr(e.rhs, state)
        if e.rhs_sv:
            rhs_r = f"std::string_view({rhs_r})"
        lhs_b = f"{e.lhs_cast}({lhs_r})" if e.lhs_cast else lhs_r
        rhs_b = f"{e.rhs_cast}({rhs_r})" if e.rhs_cast else rhs_r
        if e.op == "||":
            return f"({truthy} ? {lhs_b} : {rhs_b})"
        return f"({truthy} ? {rhs_b} : {lhs_b})"
    if isinstance(e, THIRIsNone):
        inner = _emit_expr(e.operand, state)
        if e.any_typeid:
            # D15 typeid probe: the Any cell stores None as std::monostate.
            check = (f"({inner}.value.has_value() && "
                     f"{inner}.value.type() == typeid(std::monostate))")
            return f"(!{check})" if e.negate else check
        if e.value_repr:
            # `std::optional<T>` param: `is None` -> `(!p.has_value())`,
            # `is not None` -> `(p.has_value())` (_gen_binop's has_value arm).
            return f"({inner}.has_value())" if e.negate else f"(!{inner}.has_value())"
        if e.union_monostate:
            # Union binding: the monostate holds test (_gen_binop's union arm).
            check = f"std::holds_alternative<std::monostate>({inner})"
            return f"(!{check})" if e.negate else f"({check})"
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
    if isinstance(e, THIRVarargPack):
        return _emit_vararg_pack(e, state)
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
    if isinstance(e, THIRCopy):
        return f"{e.cpp_type}({_emit_expr(e.value, state)})"
    if isinstance(e, THIRConsumingIter):
        return (f"{qualify_native_name(e.native_name)}"
                f"(std::move({_emit_expr(e.value, state)}))")
    if isinstance(e, THIRMove):
        return f"std::move({_emit_expr(e.value, state)})"
    if isinstance(e, THIRLambda):
        params = ", ".join(e.params_cpp)
        body = _emit_expr(e.body, state)
        if e.ret_cpp is None:
            return f"{e.capture_cpp}({params}) {{ {body}; }}"
        return (f"{e.capture_cpp}({params}) -> {e.ret_cpp} "
                f"{{ return {body}; }}")
    if isinstance(e, THIRPrintChain):
        # The void-lambda body chain: the plain-print segments (default
        # sep/end literals), no sink/`;` -- the enclosing lambda adds those.
        return "std::cout << " + " << ".join(
            _print_parts(e.args, '" "', '"\\n"', state))
    if isinstance(e, THIROptionalPtrArg):
        if e.value is None:
            return "nullptr"
        inner = _emit_expr(e.value, state)
        if e.lift:
            return f"::tpy::optional_to_ptr({inner})"
        return f"&({inner})" if e.addr_of else inner
    if isinstance(e, THIRMethodCall):
        return _emit_method_call(e, state)
    if isinstance(e, THIRClassConstant):
        if e.recv_eval is not None:
            # Effectful / runtime-checked instance receiver: evaluate it,
            # discard, yield the static (`({ static_cast<void>(recv);
            # C::LIMIT; })` / the deref_check variant).
            recv = e.recv_wrap.format(_emit_expr(e.recv_eval, state))
            return f"({{ {recv}; {e.cpp}; }})"
        return e.cpp
    if isinstance(e, THIRConceptTest):
        _witness("if.constexpr_concept")
        return e.cpp
    if isinstance(e, (THIREnumMember, THIRModuleVar)):
        return e.cpp
    if isinstance(e, THIREnumWrap):
        if e.operand is None:
            return e.wrap  # plain-enum truthiness: literal `true`
        return e.wrap.format(_emit_expr(e.operand, state))
    if isinstance(e, THIRContainerLiteral):
        return _emit_container_literal(e, state)
    if isinstance(e, THIRListRepeat):
        return _emit_list_repeat(e, state)
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
    if isinstance(e, THIRBorrowTupleLiteral):
        # The borrow-slot render: the spelled slot type + per-element bare,
        # `&(...)` lift, or `{0}` wrap template (the generic to_val_or_ptr
        # elements). Single-element parenthesizes like the value arm.
        wraps = e.elem_wraps or (None,) * len(e.elements)
        elems = ", ".join(
            w.format(_emit_expr(x, state)) if w is not None
            else f"&({_emit_expr(x, state)})" if lift
            else _emit_expr(x, state)
            for x, lift, w in zip(e.elements, e.addr_of, wraps))
        if len(e.elements) == 1:
            return f"{e.spelled_cpp}({elems})"
        return f"{e.spelled_cpp}{{{elems}}}"
    if isinstance(e, THIRTupleValueToBorrow):
        elems = ", ".join(
            f"&({_emit_expr(x, state)})" if lift else _emit_expr(x, state)
            for x, lift in zip(e.elements, e.addr_of))
        src = (f"{e.src_cpp}({elems})" if len(e.elements) == 1
               else f"{e.src_cpp}{{{elems}}}")
        return f"::tpy::tuple_value_to_borrow<{e.dst_cpp}>({src})"
    if isinstance(e, THIRRecordCopy):
        # `copy(x)` of an F1 record: the explicit copy-ctor call `T(x)`
        # (the AST's `_gen_copy_expr` record arm).
        return f"{e.cpp_type}({_emit_expr(e.value, state)})"
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
    if isinstance(e, THIRErrorReturnUnwrap):
        # _maybe_error_return_unwrap: the call renders first, THEN the
        # counter draws (the AST wraps an already-rendered call), so nested
        # unwraps in arguments number lower than their host.
        call_cpp = _emit_expr(e.call, state)
        tmp = f"__er_{state.try_counter.next()}"
        check = _er_check_inline(tmp, state)
        if e.value_form:
            _witness("er.unwrap")
            return (f"({{ auto {tmp} = {call_cpp}; {check} "
                    f"::tpy::unwrap_ref_move(*{tmp}); }})")
        # Non-value result: return a pointer from the statement expression
        # (points at the original object via val_or_ref), deref outside for
        # an lvalue.
        _witness("er.unwrap_ptr")
        return (f"(*({{ auto {tmp} = {call_cpp}; {check} "
                f"&::tpy::unwrap_ref(*{tmp}); }}))")
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
    # _emit_branch_decls run before _gen_if (see _emit_try). A hoist_slots
    # entry allocates that name's rebind slot immediately before its predecl
    # line (the rvalue-reassigned arm's `std::optional<T> __slot_N;`).
    slot_types = dict(stmt.hoist_slots)
    for name, cpp_type in stmt.hoist_decls:
        if name in slot_types:
            slot = state.next_slot()
            state.rebind_slots[name] = slot
            out.write(f"{indent}std::optional<{slot_types[name]}> "
                      f"__slot_{slot};\n")
        out.write(f"{indent}{cpp_type} {name};\n")
    chain = [stmt]
    while (len(chain[-1].else_body) == 1
           and isinstance(chain[-1].else_body[0], THIRIf)
           and not chain[-1].else_is_nested
           and _is_elif(chain[-1], chain[-1].else_body[0])):
        chain.append(chain[-1].else_body[0])
    # An elif condition that registers temps abandons the flat `} else if`
    # chain: the temps have no legal spot between `}` and `else`, so the
    # remainder nests in an `} else {` block with the decls flushed inside
    # (_gen_if's probe-then-nest arm; the single render here reissues the
    # same `__tmp_N` names the AST's discard-and-regenerate produces).
    extra_closes: list[str] = []
    for i, node in enumerate(chain):
        # The AST's per-node keyword choice: a protocol-isinstance
        # condition compiles `if constexpr`.
        if_kw = "if constexpr" if node.is_constexpr else "if"
        if i == 0:
            cond = _emit_expr(node.condition, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{if_kw} ({cond}) {{\n")
        else:
            state.comments.elif_(out, node.loc, indent)
            cp = state.temps.checkpoint()
            cond = _emit_expr(node.condition, state)
            if (state.temps.has_pending_since(cp)
                    or state.temps.has_named_since(cp)):
                out.write(f"{indent}}} else {{\n")
                extra_closes.append(indent)
                indent_level += 1
                indent = INDENT * indent_level
                body_indent = INDENT * (indent_level + 1)
                state.temps.flush(out, indent)
                out.write(f"{indent}{if_kw} ({cond}) {{\n")
            else:
                out.write(f"{indent}}} else {if_kw} ({cond}) {{\n")
        _emit_stmts(out, node.then_body, indent_level + 1, state)
        state.comments.trailing(out, node.then_body, body_indent)
    last = chain[-1]
    if last.else_body:
        state.comments.else_(out, last.else_body, indent)
        out.write(f"{indent}}} else {{\n")
        _emit_stmts(out, last.else_body, indent_level + 1, state)
        state.comments.trailing(out, last.else_body, body_indent)
    out.write(f"{indent}}}\n")
    for ind in reversed(extra_closes):
        out.write(f"{ind}}}\n")


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
             state.loop_else_labels, dict(state.rebind_slots),
             set(state.union_slot_locals), state.error_return_cpp,
             state.try_except_label, state.try_except_err_opt,
             state.in_except_tier)
    state.finally_frames = []
    state.return_cpp = stmt.ret_cpp
    state.loop_depth = 0
    state.switch_depth = 0
    state.loop_break_labels = []
    state.loop_else_labels = []
    # A nested def is never @error_return (gated at lowering), so its body
    # must not inherit the enclosing function's error_return renders (a bare
    # `return` inside it is a plain `return;`, not `return {};`) nor a live
    # return-tier goto target.
    state.error_return_cpp = None
    state.try_except_label = None
    state.try_except_err_opt = None
    state.in_except_tier = None
    try:
        # No trailing-comment emission: _gen_nested_def raw-loops gen_stmt
        # with no emit_block_trailing_comments call, so a comment after the
        # lambda's last statement stays OUTSIDE the closing brace.
        _emit_stmts(out, stmt.body, indent_level + 1, state)
    finally:
        (state.finally_frames, state.return_cpp, state.loop_depth,
         state.switch_depth, state.loop_break_labels,
         state.loop_else_labels, state.rebind_slots,
         state.union_slot_locals, state.error_return_cpp,
         state.try_except_label, state.try_except_err_opt,
         state.in_except_tier) = saved
    out.write(f"{indent}}};\n")


def _emit_while(out: TextIO, stmt: THIRWhile, indent_level: int, state: _EmitState) -> None:
    # The `// while ...:` comment is emitted by the caller (_emit_stmts).
    indent = INDENT * indent_level
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    # Mirror _gen_while's restructured head: anonymous cond temps re-evaluate
    # per iteration, so they live in the loop head behind `while (true)` with
    # an inverted break -- a pre-loop flush would freeze a stale snapshot.
    # Lowering rejects the mixed walrus+temps shape, so a walrus pre-decl here
    # only ever rides the plain flush (before the loop, where it stays
    # visible after it).
    cond_checkpoint = state.temps.checkpoint()
    cond = _emit_expr(stmt.condition, state)
    if state.temps.has_pending_since(cond_checkpoint):
        # Sink-side guard for the lowering-side mixed reject: an in-head
        # temp next to a pre-loop-flushed walrus pre-decl would be the
        # stale-read hazard _cond_mixed_walrus_temps exists to exclude.
        assert not state.temps.has_named_since(cond_checkpoint), (
            "mixed walrus + temps while cond reached the restructured head")
        cond_temps = io.StringIO()
        state.temps.flush_since(cond_temps, cond_checkpoint, indent + INDENT)
        state.temps.flush(out, indent)
        out.write(f"{indent}while (true) {{\n")
        out.write(cond_temps.getvalue())
        out.write(f"{indent}{INDENT}if (!({cond})) break;\n")
    else:
        state.temps.flush(out, indent)
        out.write(f"{indent}while ({cond}) {{\n")
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
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    n = state.next_loop_index()
    cpp_elem = stmt.elem_type.to_cpp()
    var = escape_cpp_name(stmt.var)
    # A hoisted rebind runs the counter through a hidden `__range_N` and assigns
    # the user var inside the body (mirrors _gen_range_counter_loop).
    counter = f"__range_{n}" if stmt.hoist_loop_var else var
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
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"{counter} < {stop_cpp}; ++{counter}) {{\n")
    elif stmt.step_kind == "unit_neg":
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"{counter} > {stop_cpp}; --{counter}) {{\n")
    elif stmt.step_kind in ("literal_pos", "literal_neg"):
        step_cpp = _emit_expr(stmt.step, state)
        out.write(f"{indent}::tpy::range_check_overflow<{cpp_elem}>("
                  f"{start_cpp}, {stop_cpp}, {step_cpp});\n")
        cmp = "<" if stmt.step_kind == "literal_pos" else ">"
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"{counter} {cmp} {stop_cpp}; {counter} += {step_cpp}) {{\n")
    else:  # variable
        step_cpp = _emit_expr(stmt.step, state)
        out.write(f"{indent}{cpp_elem} __step_{n} = {step_cpp};\n")
        out.write(f"{indent}::tpy::range_check_step_nonzero(__step_{n});\n")
        out.write(f"{indent}::tpy::range_check_overflow<{cpp_elem}>("
                  f"{start_cpp}, {stop_cpp}, __step_{n});\n")
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"__step_{n} > 0 ? {counter} < {stop_cpp} : {counter} > {stop_cpp}; "
                  f"{counter} += __step_{n}) {{\n")
    if stmt.hoist_loop_var:
        out.write(f"{INDENT * (indent_level + 1)}{var} = {counter};\n")
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
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    n = state.next_loop_index()
    obj, beg, end = f"__obj_{n}", f"__beg_{n}", f"__end_{n}"
    binding_kw = "auto&" if stmt.iterable_lvalue else "auto"
    iterable_cpp = _emit_expr(stmt.iterable, state)
    if stmt.str_literal_iterable:
        iterable_cpp = f"std::string_view({iterable_cpp})"
    out.write(f"{indent}{binding_kw} {obj} = {iterable_cpp};\n")
    out.write(f"{indent}auto {beg} = {obj}.begin();\n")
    out.write(f"{indent}auto {end} = {obj}.end();\n")
    out.write(f"{indent}for (; {beg} != {end}; ++{beg}) {{\n")
    inner = INDENT * (indent_level + 1)
    binding = loop_var_binding(stmt.elem_type, escape_cpp_name(stmt.var),
                              f"*{beg}", stmt.const_loop_var,
                              hoisted=stmt.hoist_loop_var,
                              hoisted_tuple_lift_cpp=stmt.hoisted_tuple_lift_cpp)
    out.write(f"{inner}{binding}\n")
    state.loop_depth += 1
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.loop_depth -= 1
    state.comments.trailing(out, stmt.body, inner)
    out.write(f"{indent}}}\n")
    _pop_loop_frame(out, indent, state, saved_depth, stmt.orelse, indent_level)


def _emit_for_iter_proto(out: TextIO, stmt: THIRForIterProto,
                         indent_level: int, state: _EmitState) -> None:
    # Mirrors _gen_direct_next_loop_with_iter (the universal ::tpy::__iter__
    # default): the iterable renders BEFORE the brace scope opens (the AST
    # renders it in _gen_for_each_loop, so its arg temps flush inside the
    # scope at the AST's flush point), the source captures `auto&` (lvalue) /
    # owning `auto` (rvalue, brace-scoped so the temp dies at loop exit like
    # CPython's refcount drop), and the src/itr and __r indices are two
    # consecutive per-function loop-index draws.
    indent = INDENT * indent_level
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    it_cpp = _emit_expr(stmt.iterable, state)
    n = state.next_loop_index()
    src, itr = f"__src_{n}", f"__itr_{n}"
    outer = indent
    lvl = indent_level
    if not stmt.iterable_lvalue:
        out.write(f"{indent}{{\n")
        lvl += 1
        indent = INDENT * lvl
    state.temps.flush(out, indent)
    binding_kw = "auto&" if stmt.iterable_lvalue else "auto"
    out.write(f"{indent}{binding_kw} {src} = {it_cpp};\n")
    out.write(f"{indent}auto&& {itr} = ::tpy::__iter__({src});\n")
    r = f"__r_{state.next_loop_index()}"
    out.write(f"{indent}for (;;) {{\n")
    inner = INDENT * (lvl + 1)
    out.write(f"{inner}auto {r} = {itr}.__next__();\n")
    out.write(f"{inner}if (!{r}.has_value()) break;\n")
    binding = loop_var_binding(stmt.elem_type, escape_cpp_name(stmt.var),
                               f"::tpy::unwrap_ref(*{r})", stmt.const_loop_var)
    out.write(f"{inner}{binding}\n")
    state.loop_depth += 1
    # The body emits at the ORIGINAL level + 1 even inside the rvalue brace
    # scope: the AST's scope bump changes only _gen_direct_next_loop's local
    # `indent` string, never ctx.indent_level, which _gen_loop_body's
    # gen_stmt/trailing-comment walk draws from. The prelude/close lines
    # above follow the bumped string; the body follows the level.
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.loop_depth -= 1
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")
    if not stmt.iterable_lvalue:
        out.write(f"{outer}}}\n")
    _pop_loop_frame(out, outer, state, saved_depth, stmt.orelse, indent_level)


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
            # Set before the copy: a copy that raises must not be re-run by
            # its own frame's catch. Outer frames' guards stay false, so
            # their cleanup still runs -- Python's unwind semantics.
            if fr.guard_name is not None:
                state.live_finally_guards.add(fr.guard_name)
                out.write(f"{indent}{fr.guard_name} = true;\n")
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


def _emit_finally_return(out: TextIO, value_cpp: 'str | None', indent: str,
                         state: _EmitState) -> None:
    # Mirrors _make_return's finally-chain arm: the value lands in a
    # signature-typed temp BEFORE the chain runs (Python evaluates the return
    # expression first -- and still evaluates it when a terminating finally
    # overrides the return: the [[maybe_unused]] decl + suppressed trailing
    # return). The temp draws from the same per-function iter_counter the AST
    # uses; the chain buffers first like the AST so its own counter bumps land
    # between the temp's allocation and the decl's write. `value_cpp` is the
    # already-rendered (and temp-flushed) return value, None for a bare
    # `return;` -- callers render it first so the counter draws stay in the
    # AST's order.
    _witness_chain("return", state, 0)
    if value_cpp is None:
        if _emit_finally_chain(out, indent, state):
            _witness("try.chain_terminated")
        else:
            out.write(f"{indent}return;\n")
        return
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


def _er_check_inline(tmp: str, state: _EmitState) -> str:
    # The one-line has_value check of the expression-level unwrap
    # (_maybe_error_return_unwrap's three dispositions). The propagate arm
    # deliberately does NOT walk finally frames -- the AST expression unwrap
    # returns directly (unlike the statement-level _gen_propagate_check);
    # mirrored, not endorsed.
    if state.try_except_label:
        if state.try_except_err_opt:
            return (f"if (!{tmp}.has_value()) {{ "
                    f"{state.try_except_err_opt} = std::move({tmp}.error()); "
                    f"goto {state.try_except_label}; }}")
        return f"if (!{tmp}.has_value()) goto {state.try_except_label};"
    if state.error_return_cpp:
        return (f"if (!{tmp}.has_value()) "
                f"return ::tpy::make_unexpected({tmp}.error());")
    return (f"if (!{tmp}.has_value()) "
            f'::tpy::tpy_panic("unhandled error return");')


def _er_check_stmt(tmp: str, indent: str, state: _EmitState) -> str:
    # The statement-block check line(s): _gen_error_goto (in a return-tier
    # try), _gen_propagate_check (in an @error_return body; finally-aware --
    # active finally bodies run before the unexpected value returns), or the
    # top-level panic.
    if state.try_except_label:
        if state.try_except_err_opt:
            return (f"{indent}if (!{tmp}.has_value()) "
                    f"{{ {state.try_except_err_opt} = "
                    f"std::move({tmp}.error()); "
                    f"goto {state.try_except_label}; }}\n")
        return (f"{indent}if (!{tmp}.has_value()) "
                f"goto {state.try_except_label};\n")
    if state.error_return_cpp:
        if not state.finally_frames:
            return (f"{indent}if (!{tmp}.has_value()) "
                    f"return ::tpy::make_unexpected({tmp}.error());\n")
        body = io.StringIO()
        _emit_finally_return(body, f"::tpy::make_unexpected({tmp}.error())",
                             indent + INDENT, state)
        return (f"{indent}if (!{tmp}.has_value()) {{\n"
                f"{body.getvalue()}{indent}}}\n")
    return (f"{indent}if (!{tmp}.has_value()) "
            f'::tpy::tpy_panic("unhandled error return");\n')


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
    # temp-registering manager expressions are gate-rejected). Hoisted
    # predecls render first, like the AST's gen_stmt dispatch
    # (_emit_branch_decls before _gen_with).
    indent = INDENT * indent_level
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
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
        elif item.target_arm is WithTargetArm.ASSIGN_OPT:
            out.write(f"{indent}{item.target} = __ctx_{n}.__enter__();\n")
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
    frames: list[_FinallyFrame] = []
    for k, (n, item) in enumerate(zip(ctx_ids, stmt.items)):
        fr = _FinallyFrame(
            ctx_n=n,
            exc_null_arg="nullptr" if item.takes_exc_val else "{}",
            loop_depth=state.loop_depth,
            guard_name=f"__fin_ran_{state.finally_guard_counter.next()}")
        frames.append(fr)
        state.finally_frames.append(fr)
    # Buffered so the exit sites inside the body settle each layer's guard
    # liveness before that layer's `try {` (and its decl) is written.
    body_buf = io.StringIO()
    _emit_stmts(body_buf, stmt.body, indent_level + len(stmt.items), state)
    for k in range(len(stmt.items)):
        g = (frames[k].guard_name
             if frames[k].guard_name in state.live_finally_guards else None)
        if g is not None:
            out.write(f"{INDENT * (indent_level + k)}bool {g} = false;\n")
        out.write(f"{INDENT * (indent_level + k)}try {{\n")
    out.write(body_buf.getvalue())
    for k in range(len(stmt.items) - 1, -1, -1):
        n, item = ctx_ids[k], stmt.items[k]
        ind = INDENT * (indent_level + k)
        body_ind = INDENT * (indent_level + k + 1)
        exc_null = "nullptr" if item.takes_exc_val else "{}"
        # The fall-through __exit__ copy sits after the catches, so a
        # throwing __exit__ isn't re-run by this layer's own catch-all.
        needs_after_label = item.can_suppress and not layer_term[k]
        guard = (frames[k].guard_name
                 if frames[k].guard_name in state.live_finally_guards else None)
        if not layer_term[k]:
            out.write(f"{body_ind}goto __with_exit_{n};\n")
        # Popped before the catch arms, mirroring _emit_with_try_catch's pop
        # discipline (the catches are fixed strings; nothing walks the stack).
        state.finally_frames.pop()
        if item.can_suppress or item.takes_exc_val:
            exc_obj = f"&__exc_{n}" if item.takes_exc_val else "{}"
            out.write(f"{ind}}} catch (::tpy::BaseException& __exc_{n}) {{\n")
            # An exit-site __exit__ that raised IS the cleanup's own
            # exception: never re-call __exit__ with it, and never offer it
            # to __exit__ for suppression.
            if guard is not None:
                out.write(f"{body_ind}if ({guard}) throw;\n")
            if item.can_suppress:
                out.write(f"{body_ind}if (!__ctx_{n}.__exit__({{}}, "
                          f"{exc_obj}, {{}})) throw;\n")
                if needs_after_label:
                    out.write(f"{body_ind}goto __with_after_{n};\n")
            else:
                out.write(f"{body_ind}__ctx_{n}.__exit__({{}}, "
                          f"{exc_obj}, {{}});\n")
                out.write(f"{body_ind}throw;\n")
        out.write(f"{ind}}} catch (...) {{\n")
        if guard is not None:
            out.write(f"{body_ind}if ({guard}) throw;\n")
        out.write(f"{body_ind}__ctx_{n}.__exit__({{}}, {exc_null}, {{}});\n")
        out.write(f"{body_ind}throw;\n")
        out.write(f"{ind}}}\n")
        if not layer_term[k]:
            out.write(f"{ind}__with_exit_{n}:\n")
            out.write(f"{ind}__ctx_{n}.__exit__({{}}, {exc_null}, {{}});\n")
        if needs_after_label:
            out.write(f"{ind}__with_after_{n}:;\n")


def _emit_frame_wrapped(out: TextIO, inner_level: int, state: _EmitState,
                        stmt: THIRTry, emit_body) -> None:
    # _emit_try_with_finally's unified shape: the finally frame sits on the
    # stack while the body emits; the catch-path and normal-path copies emit
    # with the frame popped, so nested exits redirect through OUTER frames
    # only. `stmt.body_terminates` is the terminates fact of whatever the
    # frame wraps (see THIRTry) and elides the normal-path copy. The body
    # emits into a buffer first: an exit site inside it decides whether the
    # frame's guard is needed, which has to be declared before the `try {`.
    inner = INDENT * inner_level
    fr = _FinallyFrame(
        loop_depth=state.loop_depth,
        stmts=stmt.finally_body,
        terminates=stmt.finally_terminates,
        guard_name=f"__fin_ran_{state.finally_guard_counter.next()}")
    state.finally_frames.append(fr)
    body_buf = io.StringIO()
    emit_body(body_buf, inner_level + 1)
    guard = (fr.guard_name
             if fr.guard_name in state.live_finally_guards else None)
    if guard is not None:
        out.write(f"{inner}bool {guard} = false;\n")
    out.write(f"{inner}try {{\n")
    out.write(body_buf.getvalue())
    out.write(f"{inner}}} catch (...) {{\n")
    state.finally_frames.pop()
    if guard is not None:
        out.write(f"{INDENT * (inner_level + 1)}if (!{guard}) {{\n")
        _emit_stmts(out, stmt.finally_body, inner_level + 2, state)
        out.write(f"{INDENT * (inner_level + 1)}}}\n")
        # Always rethrow behind a guard: the guarded-true path carries the
        # exit-site copy's own exception, which must propagate even when the
        # finally body itself terminates.
        out.write(f"{INDENT * (inner_level + 1)}throw;\n")
    else:
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
        # A bare `raise` in a throw-tier handler re-throws even when the
        # handler sits inside a return-tier handler body (ctx.in_except_tier
        # is reassigned per handler emit).
        prev_tier = state.in_except_tier
        state.in_except_tier = "throw"
        _emit_stmts(out, h.body, level + 1, state)
        state.in_except_tier = prev_tier
        if stmt.else_body:
            out.write(f"{INDENT * (level + 1)}goto {label};\n")
        out.write(f"{ind}}}")
    out.write("\n")
    if stmt.else_body:
        out.write(f"{ind}// else:\n")
        _emit_stmts(out, stmt.else_body, level, state)
        out.write(f"{ind}{label}:;\n")


def _emit_try_return(out: TextIO, stmt: THIRTry, inner_level: int,
                     state: _EmitState) -> None:
    # Mirrors _gen_try_return (see THIRTry): the counter draws first, the
    # optional `__err_opt_N` capture decl, then the goto-dispatch body --
    # wrapped in the finally frame when a finally is present. The emit
    # state's label/err_opt are live only while the TRY body emits (the AST
    # restores the label before the else body), while err_opt stays set
    # until the whole statement closes (the bare-raise re-raise in the
    # handler reads it).
    inner = INDENT * inner_level
    h = stmt.handlers[0]
    n = state.try_counter.next()
    except_label = f"__except_{n}"
    after_label = f"__after_try_{n}"
    err_opt_var: 'str | None' = None
    prev_err_opt = state.try_except_err_opt
    if h.binding:
        err_opt_var = f"__err_opt_{n}"
        out.write(f"{inner}std::optional<{stmt.err_opt_cpp}> "
                  f"{err_opt_var};\n")
        state.try_except_err_opt = err_opt_var
        _witness("er.try_binding")

    def emit_try_except(o: TextIO, level: int) -> None:
        body_indent = INDENT * level
        prev_label = state.try_except_label
        state.try_except_label = except_label
        _emit_stmts(o, stmt.try_body, level, state)
        state.try_except_label = prev_label
        if stmt.else_body:
            o.write(f"{body_indent}// else:\n")
            _emit_stmts(o, stmt.else_body, level, state)
        o.write(f"{body_indent}goto {after_label};\n")
        exc_display = h.source_display or "..."
        o.write(f"{body_indent}// except {exc_display}:\n")
        o.write(f"{body_indent}{except_label}:;\n")
        prev_tier = state.in_except_tier
        state.in_except_tier = "return"
        if h.binding and err_opt_var:
            binding = escape_cpp_name(h.binding)
            o.write(f"{body_indent}{{\n")
            o.write(f"{INDENT * (level + 1)}auto& {binding} = "
                    f"*{err_opt_var};\n")
            _emit_stmts(o, h.body, level + 1, state)
            o.write(f"{body_indent}}}\n")
        else:
            _emit_stmts(o, h.body, level, state)
        state.in_except_tier = prev_tier
        o.write(f"{body_indent}{after_label}:;\n")

    _witness("er.try_return")
    if stmt.finally_body:
        _emit_frame_wrapped(out, inner_level, state, stmt, emit_try_except)
    else:
        emit_try_except(out, inner_level)
    state.try_except_err_opt = prev_err_opt


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
            lambda o, lvl: _emit_stmts(o, stmt.try_body, lvl, state))
    elif stmt.tier == "return":
        _emit_try_return(out, stmt, inner_level, state)
    elif stmt.finally_body:
        _emit_frame_wrapped(
            out, inner_level, state, stmt,
            lambda o, lvl: _emit_try_except(o, stmt, lvl, state))
    else:
        _emit_try_except(out, stmt, inner_level, state)
    out.write(f"{indent}}}\n")



def _emit_match_arm_body(out: 'TextIO', entry, lvl: int,
                         state: '_EmitState') -> None:
    """One scalar-tier arm-body point. In resumable dispatch-hook mode the
    body is a BB chain the skeleton walks -- call its arm hook with the
    case's body key; sync matches emit the lowered body."""
    if state.match_arm_hook is not None and entry.body_key is not None:
        state.match_arm_hook(entry.body_key, lvl)
        return
    _emit_stmts(out, entry.body, lvl, state)


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
    elif stmt.strategy == "poly_if_elif":
        _emit_match_poly_if_elif(out, stmt, indent_level, state, subject)
    elif stmt.strategy == "poly_guarded":
        _emit_match_poly_guarded(out, stmt, indent_level, state, subject)
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
    elif binding.mode == "assign_addr":
        out.write(f"{inner}{name} = &({rhs});\n")
    elif binding.mode == "assign_move":
        out.write(f"{inner}{name} = std::move({rhs});\n")
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
            _emit_match_arm_body(out, entry, indent_level + 1, state)
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
                _emit_match_arm_body(out, entry, indent_level + 2, state)
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
    # `break;` per arm. A wrapper subject dispatches through its `.value`
    # variant member (both the switch head and the get positions).
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    variant = f"{subject}.value" if stmt.wrapper_value else subject
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
        _emit_match_arm_body(out, entry, indent_level + 1, state)
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


def _emit_poly_whole_binding(out: TextIO, entry, subject: str,
                             at: str) -> None:
    # The whole-subject capture/`as` binding: vs the `__case_i` alias for a
    # class arm (`from_case_var`), vs the subject otherwise.
    if entry.binding is not None:
        rhs = entry.case_alias if entry.binding.from_case_var else subject
        _emit_match_binding(out, entry.binding, rhs, at)


def _emit_match_poly_if_elif(out: TextIO, stmt: THIRMatch,
                             indent_level: int, state: _EmitState,
                             subject: str) -> None:
    # _gen_match_polymorphic_if_elif: per class arm the C++17 if-init cast
    # (poly_cast composed around the subject), the `__case_i` ref line,
    # field bindings + the `as` binding against the alias, the body one
    # level in; or-arms the ||-joined null tests; the always-match arm the
    # chain's `{` / `} else {`. One trailing `}` closes the chain (also for
    # a non-exhaustive chain with no else arm).
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if entry.poly_cast is not None:
            keyword = "if" if i == 0 else "} else if"
            pre, suf = entry.poly_cast
            out.write(f"{indent}{keyword} ({pre}{subject}{suf}) {{\n")
            out.write(f"{inner}{entry.poly_ref_decl}\n")
            for fb in entry.field_bindings:
                _emit_match_binding(out, fb, entry.case_alias, inner)
            _emit_poly_whole_binding(out, entry, subject, inner)
        elif entry.poly_or_conds is not None:
            keyword = "if" if i == 0 else "} else if"
            cond = " || ".join(f"{p}{subject}{s}"
                               for p, s in entry.poly_or_conds)
            out.write(f"{indent}{keyword} ({cond}) {{\n")
            _emit_poly_whole_binding(out, entry, subject, inner)
        else:
            out.write(f"{indent}{{\n" if i == 0 else f"{indent}}} else {{\n")
            _emit_poly_whole_binding(out, entry, subject, inner)
        _emit_stmts(out, entry.body, indent_level + 1, state)
    out.write(f"{indent}}}\n")


def _emit_match_poly_guarded(out: TextIO, stmt: THIRMatch,
                             indent_level: int, state: _EmitState,
                             subject: str) -> None:
    # _gen_match_polymorphic_guarded + _emit_poly_guarded_action: the end
    # label draws the second counter bump; class arms are standalone
    # `if (cast) {` blocks -- alias + bindings first, then the field-cond /
    # guard `if` gating body + `goto end` (or the inline body + goto when
    # unconditional); or-arms AND the guard into the block condition;
    # always-match arms bind at the OUTER indent, a guarded one gates the
    # body, an unguarded one is a bare block with NO goto (it falls through
    # to the label). The end label writes INDENTED (unlike the
    # guarded-union tier's column-0 write).
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.stmt(out, entry.loc, indent)
        if entry.poly_cast is not None:
            pre, suf = entry.poly_cast
            out.write(f"{indent}if ({pre}{subject}{suf}) {{\n")
            out.write(f"{inner}{entry.poly_ref_decl}\n")
            for fb in entry.field_bindings:
                _emit_match_binding(out, fb, entry.case_alias, inner)
            _emit_poly_whole_binding(out, entry, subject, inner)
            cond_parts = [f"{p}{entry.case_alias}{s}"
                          for p, s in entry.field_conds]
            if entry.guard is not None:
                cond_parts.append(_emit_expr(entry.guard, state))
            if cond_parts:
                out.write(f"{inner}if ({' && '.join(cond_parts)}) {{\n")
                _emit_stmts(out, entry.body, indent_level + 2, state)
                out.write(f"{inner}    goto {end_label};\n")
                out.write(f"{inner}}}\n")
            else:
                _emit_stmts(out, entry.body, indent_level + 1, state)
                out.write(f"{inner}goto {end_label};\n")
            out.write(f"{indent}}}\n")
        elif entry.poly_or_conds is not None:
            cond = " || ".join(f"{p}{subject}{s}"
                               for p, s in entry.poly_or_conds)
            if entry.guard is not None:
                cond = f"({cond}) && {_emit_expr(entry.guard, state)}"
            out.write(f"{indent}if ({cond}) {{\n")
            _emit_poly_whole_binding(out, entry, subject, inner)
            _emit_stmts(out, entry.body, indent_level + 1, state)
            out.write(f"{inner}goto {end_label};\n")
            out.write(f"{indent}}}\n")
        else:
            _emit_poly_whole_binding(out, entry, subject, indent)
            if entry.guard is not None:
                out.write(f"{indent}if ({_emit_expr(entry.guard, state)}) "
                          f"{{\n")
                _emit_stmts(out, entry.body, indent_level + 1, state)
                out.write(f"{inner}goto {end_label};\n")
                out.write(f"{indent}}}\n")
            else:
                out.write(f"{indent}{{\n")
                _emit_stmts(out, entry.body, indent_level + 1, state)
                out.write(f"{indent}}}\n")
    out.write(f"{indent}{end_label}:;\n")


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
        _emit_match_arm_body(out, entry, indent_level + 1, state)
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
            _emit_match_arm_body(out, entry, indent_level + 2, state)
            out.write(f"{INDENT * (indent_level + 2)}goto {end_label};\n")
            out.write(f"{inner}}}\n")
        else:
            _emit_match_arm_body(out, entry, indent_level + 1, state)
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
    elif isinstance(stmt, THIRPtrLocalDecl):
        # Slot-hoist pointer-repr locals. Slot NUMBERING mirrors the AST's
        # allocation order exactly (SlotState.next_slot call sites in
        # _gen_pointer_local_init / _gen_ptr_variant_local_init): the OPT
        # kinds allocate the init slot before the rebind slot; the UNION
        # rvalue kind allocates the value slot before the rebind slot but
        # EMITS the rebind pre-decl line first.
        name = escape_cpp_name(stmt.name)
        # `const T*` only on the pointer line (mirrors the AST `const_pfx`); the
        # rebind `std::optional<T>` slot backing a reseat stays non-const.
        cpfx = "const " if stmt.is_const else ""
        if stmt.kind is PtrSlotKind.OPT_NONE:
            if stmt.needs_rebind_slot:
                slot = state.next_slot()
                state.rebind_slots[stmt.name] = slot
                out.write(f"{indent}std::optional<{stmt.cpp_type}> "
                          f"__slot_{slot};\n")
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = nullptr;\n")
        elif stmt.kind in (PtrSlotKind.OPT_RVALUE, PtrSlotKind.RECORD_RVALUE):
            # RECORD_RVALUE (the escape-hoist plain-record flavor) shares the
            # render exactly: `T __slot_N = init;` + `T* name = &__slot_N;`
            # (its needs_rebind_slot is always False -- an rvalue-reassigned
            # record is the REBIND_SLOT binding, not this kind).
            init_cpp = _emit_expr(stmt.init, state)
            init_slot = state.next_slot()
            out.write(f"{indent}{stmt.cpp_type} __slot_{init_slot} = "
                      f"{init_cpp};\n")
            if stmt.needs_rebind_slot:
                rebind = state.next_slot()
                state.rebind_slots[stmt.name] = rebind
                out.write(f"{indent}std::optional<{stmt.cpp_type}> "
                          f"__slot_{rebind};\n")
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = "
                      f"&__slot_{init_slot};\n")
        elif stmt.kind is PtrSlotKind.RECORD_HOISTED:
            # Hoisted record pointer-local: the `std::optional<T>` slot
            # pre-decl rides the function-top hoist lines; the decl statement
            # re-emplaces per execution and re-points the alias
            # (`T* x = &*(__slot_N = init);` -- _gen_pointer_local_init's
            # hoisted rvalue branch via _ptr_from_rvalue_slot).
            init_cpp = _emit_expr(stmt.init, state)
            init_slot = state.next_slot()
            assert state.hoist_drainable, (
                "RECORD_HOISTED decl hoist reached a non-draining leaf emitter")
            state.hoist_lines.append(
                f"std::optional<{stmt.cpp_type}> __slot_{init_slot};")
            if stmt.needs_rebind_slot:
                rebind = state.next_slot()
                state.rebind_slots[stmt.name] = rebind
                state.hoist_lines.append(
                    f"std::optional<{stmt.cpp_type}> __slot_{rebind};")
            # Deliberately NO rebind_slots registration without a rebind
            # slot: the THIRAssign rebind-slot emit special-case is keyed on
            # membership alone, so registering the init slot would hijack a
            # later field / pointer-copy reseat into `&*(__slot = <T*>)` --
            # uncompilable. An rvalue reseat implies rvalue_reassigned,
            # which implies needs_rebind_slot -- no valid consumer exists.
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = "
                      f"&*(__slot_{init_slot} = {init_cpp});\n")
        elif stmt.kind is PtrSlotKind.UNION_RVALUE:
            init_cpp = _emit_expr(stmt.init, state)
            slot = state.next_slot()
            if stmt.needs_rebind_slot:
                rebind = state.next_slot()
                state.rebind_slots[stmt.name] = rebind
                state.union_slot_locals.add(stmt.name)
                out.write(f"{indent}std::optional<{stmt.val_cpp}> "
                          f"__slot_{rebind};\n")
            out.write(f"{indent}{stmt.val_cpp} __slot_{slot} = {init_cpp};\n")
            out.write(f"{indent}{stmt.cpp_type} {name} = "
                      f"::tpy::to_ptr_variant(__slot_{slot});\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL:
            # @dynamic protocol local: a concrete/adapter slot brace-inited from
            # the init, aliased by a protocol Base* pointer (the AST's
            # _gen_dynamic_protocol_init non-erased arm). Draw the slot BEFORE
            # emitting the init, matching the oracle's `next_slot()`-then-
            # `gen_expr` order, so the numbering stays aligned even if an init
            # ever consumes a slot of its own.
            init_slot = state.next_slot()
            init_cpp = _emit_expr(stmt.init, state)
            out.write(f"{indent}{stmt.cpp_type} __slot_{init_slot}"
                      f"{{{init_cpp}}};\n")
            out.write(f"{indent}{stmt.base_cpp}* {name} = "
                      f"&__slot_{init_slot};\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL_ERASED:
            # `p2: P = p1` -- alias the same erased object (no slot). The deref'd
            # source already renders its own `(*p1)` parens (AST: `&{expr}`).
            out.write(f"{indent}{stmt.base_cpp}* {name} = "
                      f"&{_emit_expr(stmt.init, state)};\n")
        else:  # PtrSlotKind.UNION_ADDR
            init_cpp = _emit_expr(stmt.init, state)
            if stmt.needs_rebind_slot:
                rebind = state.next_slot()
                state.rebind_slots[stmt.name] = rebind
                state.union_slot_locals.add(stmt.name)
                out.write(f"{indent}std::optional<{stmt.val_cpp}> "
                          f"__slot_{rebind};\n")
            out.write(f"{indent}{stmt.cpp_type} {name}{{&({init_cpp})}};\n")
    elif isinstance(stmt, THIRPtrLocalRebind):
        name = escape_cpp_name(stmt.name)
        if stmt.kind is PtrSlotKind.OPT_NONE:
            out.write(f"{indent}{name} = nullptr;\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL:
            # @dynamic rebind: a FRESH hoisted optional slot per reseat (the
            # AST's _gen_dynamic_protocol_rebind -- a distinct concrete/adapter
            # type per target). Slot drawn before the value (oracle order); its
            # decl hoists to the function top, the emplace + `p = &*slot` reseat
            # stay inline. `val_cpp` carries the slot (concrete/adapter) spelling.
            slot = state.next_slot()
            val_cpp = _emit_expr(stmt.value, state)
            # Backstop: this hoist has no drain point in a leaf emitter; lowering
            # defers generator/async bodies, so reaching here undrainable is a bug.
            assert state.hoist_drainable, (
                "DYN_PROTOCOL rebind hoist reached a non-draining leaf emitter")
            state.hoist_lines.append(
                f"std::optional<{stmt.val_cpp}> __slot_{slot};")
            out.write(f"{indent}__slot_{slot}.emplace({val_cpp});\n")
            out.write(f"{indent}{name} = &*__slot_{slot};\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL_ERASED:
            # `p2 = p1` where p1 is already erased: re-alias, no slot (the deref'd
            # source renders its own parens).
            out.write(f"{indent}{name} = &{_emit_expr(stmt.value, state)};\n")
        elif stmt.kind is PtrSlotKind.PTR_ADDR:
            # Lvalue-name reseat: address-of the bare storage read.
            out.write(f"{indent}{name} = "
                      f"&({_emit_expr(stmt.value, state)});\n")
        elif stmt.kind is PtrSlotKind.INLINE_RVALUE:
            # Slotless local's rvalue reseat: the first allocates the plain
            # block slot in place (value renders before the slot draw,
            # matching _gen_pointer_local_rebind's order); later rvalue
            # reseats reuse it.
            val_cpp = _emit_expr(stmt.value, state)
            slot = state.rebind_slots.get(stmt.name)
            if slot is None:
                slot = state.next_slot()
                state.rebind_slots[stmt.name] = slot
                out.write(f"{indent}{stmt.val_cpp} __slot_{slot} = "
                          f"{val_cpp};\n")
                out.write(f"{indent}{name} = &__slot_{slot};\n")
            else:
                out.write(f"{indent}{name} = &(__slot_{slot} = {val_cpp});\n")
        elif stmt.kind is PtrSlotKind.BRANCH_RVALUE:
            # Branch-hoisted rvalue reseat without an if-head slot: the first
            # reseat allocates the function-top `std::optional<T>` lazily
            # (the AST's pending_hoist_decls append) and registers it; later
            # rvalue reseats reuse it. Value renders before the allocation,
            # matching _gen_pointer_local_rebind's gen_expr-then-next_slot
            # order.
            val_cpp = _emit_expr(stmt.value, state)
            slot = state.rebind_slots.get(stmt.name)
            if slot is None:
                slot = state.next_slot()
                state.rebind_slots[stmt.name] = slot
                assert state.hoist_drainable, (
                    "BRANCH_RVALUE rebind hoist reached a non-draining "
                    "leaf emitter")
                state.hoist_lines.append(
                    f"std::optional<{stmt.val_cpp}> __slot_{slot};")
            out.write(f"{indent}{name} = &*(__slot_{slot} = {val_cpp});\n")
        else:  # PtrSlotKind.UNION_RVALUE -- emplace + re-lift the rebind slot
            slot = state.rebind_slots[stmt.name]
            out.write(f"{indent}__slot_{slot}.emplace("
                      f"{_emit_expr(stmt.value, state)});\n")
            out.write(f"{indent}{name} = "
                      f"::tpy::to_ptr_variant(*__slot_{slot});\n")
    elif isinstance(stmt, THIRAssign):
        # target is a THIRName (`x = ...`) or, for F2b, a THIRFieldAccess
        # (`recv.field = ...` / `recv->field = ...`); _emit_expr renders both. An
        # F2d rebind-slot pointer-local reseat reuses its optional slot:
        # `p = &*(__slot_N = <rvalue>);`.
        if (isinstance(stmt.target, THIRName)
                and stmt.target.name in state.rebind_slots
                and stmt.target.name not in state.union_slot_locals
                and stmt.target.name not in state.btuple_slot_locals):
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
            # bytearray's `__setitem__` is its own @native free-function
            # dunder (range-checked value), not the containers' checked
            # template -- mirrors _gen_assign_code's fi dispatch
            # (get_type_method_fi -> gen_call_from_fi), like the
            # bytes_getitem read arm.
            rt = unwrap_qualifiers(stmt.target.receiver.result_type)
            sym = ("::tpy::bytearray_setitem" if is_bytearray_type(rt)
                   else "::tpy::__setitem__")
            out.write(f"{indent}{sym}({recv_cpp}, {idx_cpp}, "
                      f"{value_cpp});\n")
    elif isinstance(stmt, THIRSliceAssign):
        # Mirrors _gen_slice_assign: the resolved slice __setitem__ @native
        # free-function (list_set_slice / list_set_stepped_slice) over the
        # receiver, the slice initializer (like _emit_str_slice's bound arm),
        # and the RHS. A non-empty array-literal RHS wears the std::vector<E>{...}
        # type prefix the checked helper needs to deduce its Range.
        recv_cpp = _emit_expr(stmt.receiver, state)
        lo = _emit_expr(stmt.lower, state) if stmt.lower is not None else "std::nullopt"
        hi = _emit_expr(stmt.upper, state) if stmt.upper is not None else "std::nullopt"
        if stmt.stepped:
            step = (_emit_expr(stmt.step, state)
                    if stmt.step is not None else "std::nullopt")
            slice_arg = f"::tpy::Slice{{{lo}, {hi}, {step}}}"
        else:
            slice_arg = f"::tpy::BasicSlice{{{lo}, {hi}}}"
        value_cpp = _emit_expr(stmt.value, state)
        if stmt.value_vector_cpp is not None:
            value_cpp = f"std::vector<{stmt.value_vector_cpp}>{value_cpp}"
        state.temps.flush(out, indent)
        out.write(f"{indent}{qualify_native_name(stmt.native_name)}"
                  f"({recv_cpp}, {slice_arg}, {value_cpp});\n")
    elif isinstance(stmt, THIRInplaceContainerOp):
        # Mirrors _gen_aug_assign_code's resolved_inplace arm: the mutating
        # dunder's @native free-function (list_extend, ...) over the receiver
        # and the RHS. A non-empty array-literal RHS wears the std::vector<E>{...}
        # type prefix the two-parameter template needs to deduce its Range.
        recv_cpp = _emit_expr(stmt.receiver, state)
        value_cpp = _emit_expr(stmt.value, state)
        if stmt.value_vector_cpp is not None:
            value_cpp = f"{stmt.value_vector_cpp}{value_cpp}"
        state.temps.flush(out, indent)
        out.write(f"{indent}{qualify_native_name(stmt.native_name)}"
                  f"({recv_cpp}, {value_cpp});\n")
    elif isinstance(stmt, THIRStrAppend):
        # `t += v;` -- the str in-place append (the `+=` statement and the
        # `x = x + y` peephole share the emit). A str-FIELD append renders its
        # lowered lvalue (`recv.field += v;`).
        tgt = (_emit_expr(stmt.target_expr, state)
               if stmt.target_expr is not None
               else escape_cpp_name(stmt.target))
        out.write(f"{indent}{tgt} += {_emit_expr(stmt.value, state)};\n")
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
    elif isinstance(stmt, THIRAnyNarrowAlias):
        # The Any-narrowing extraction (D15) -- mirrors
        # _emit_isinstance_extractions' Any arm (explicit type, not auto&).
        out.write(f"{indent}const {stmt.member_cpp}& {stmt.alias} = "
                  f"std::any_cast<const {stmt.member_cpp}&>"
                  f"({stmt.subject_cpp}.value);\n")
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
        # A bare `return` in an @error_return body constructs the success
        # value: `return {};` (_gen_simple_stmt's current_error_return arm).
        if stmt.value is None:
            value_cpp = "{}" if state.error_return_cpp else None
            if value_cpp is not None:
                _witness("er.bare_return")
        else:
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
        if state.finally_frames:
            _emit_finally_return(out, value_cpp, indent, state)
        elif value_cpp is None:
            out.write(f"{indent}return;\n")
        else:
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
    elif isinstance(stmt, THIRForIterProto):
        _emit_for_iter_proto(out, stmt, indent_level, state)
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
        # One holder line per TupleSourceBind form (renders documented on
        # the enum); the name forms share the source_cpp spelling override.
        sb = stmt.source_bind
        src = (stmt.source_cpp if stmt.source_cpp is not None
               else escape_cpp_name(stmt.source))
        if sb is TupleSourceBind.STORAGE_WRAP:
            out.write(f"{indent}auto {tmp} = ::tpy::tuple_to_pointer<"
                      f"{stmt.source_wrap_cpp}>({src});\n")
        elif sb is TupleSourceBind.RVALUE:
            src_cpp = _emit_expr(stmt.source_expr, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}auto {tmp} = {src_cpp};\n")
        elif sb is TupleSourceBind.ONESHOT_DEREF:
            out.write(f"{indent}auto&& {tmp} = "
                      f"(*{escape_cpp_name(stmt.source)});\n")
        elif sb is TupleSourceBind.NAME_REF:
            out.write(f"{indent}auto& {tmp} = {src};\n")
        else:
            out.write(f"{indent}const auto& {tmp} = {src};\n")
        for i, (name, cpp) in enumerate(zip(stmt.targets, stmt.target_cpps)):
            if name is None:
                continue
            get = f"std::get<{i}>({tmp})"
            bind = stmt.binds[i] if stmt.binds else "value"
            if bind == "frame_ptr_addr":
                # Loop-head alias target: the element is a live value
                # lvalue inside the container's storage tuple.
                out.write(f"{indent}{escape_cpp_name(name)} = "
                          f"&({get});\n")
            elif bind == "frame_ptr_elem":
                # Pointer-alias target: alias the LIVE element --
                # tuple_elem_ref normalizes a `T*` borrow element (deref)
                # or a value element (forward) to the live `T&`, and the
                # address-of re-points the `T*` alias field.
                out.write(f"{indent}{escape_cpp_name(name)} = "
                          f"&(::tpy::unwrap_ref(::tpy::tuple_elem_ref"
                          f"({get})));\n")
            elif bind in ("frame_assign", "frame_emplace"):
                # Resumable frame targets: assigned, never re-declared. The
                # wrap mirrors the AST's per-element is_owned move (ref
                # elements reject at lowering -- re-add an unwrap_ref wrap
                # when the name-source ladder cell makes them reachable);
                # an emplace's typed_brace_init is identity for a get-expr.
                wrap = stmt.wraps[i] if stmt.wraps else ""
                if wrap == "move":
                    get = f"std::move({get})"
                if bind == "frame_emplace":
                    out.write(f"{indent}{escape_cpp_name(name)}"
                              f".emplace({get});\n")
                else:
                    out.write(f"{indent}{escape_cpp_name(name)} = {get};\n")
            elif bind == "assign":
                # Reused target: the AST's declared-name tail (no decl).
                out.write(f"{indent}{escape_cpp_name(name)} = {get};\n")
            elif bind == "move":
                out.write(f"{indent}{cpp} {escape_cpp_name(name)} = "
                          f"std::move({get});\n")
            elif bind == "cref":
                out.write(f"{indent}const {cpp}& {escape_cpp_name(name)} = "
                          f"{get};\n")
            elif bind == "ref":
                # Borrow-tuple element: `std::get<i>(__tup)` is a `T*` (or
                # val_or_ref); one `auto&&` reference aliases the live element
                # in every form -- tuple_elem_ref derefs the pointer, unwrap_ref
                # the val_or_ref -- so `.` access is plain, non-nullable.
                out.write(f"{indent}auto&& {escape_cpp_name(name)} = "
                          f"::tpy::unwrap_ref(::tpy::tuple_elem_ref({get}));\n")
            else:
                out.write(f"{indent}{cpp} {escape_cpp_name(name)} = "
                          f"{get};\n")
    elif isinstance(stmt, THIRBreak):
        _emit_loop_exit(out, indent, state, is_break=True)
    elif isinstance(stmt, THIRContinue):
        _emit_loop_exit(out, indent, state, is_break=False)
    elif isinstance(stmt, THIRRaise):
        # Mirrors _gen_raise. Throw-tier arms never walk the finally-frame
        # stack -- the throw propagates through the emitted catch(...) arms,
        # which run the finally bodies. The return-tier arms are RETURNS
        # (make_unexpected), so they take the finally-aware _make_return
        # shape like THIRReturn.
        if stmt.raise_expr is not None:
            # `raise <expr>` -> `<expr>{.__deref__()*N}.__raise__();` -- render
            # the source first (a call-result may register arg temps), flush the
            # `__tmp_N` decls ahead of the line, then the virtual hop.
            expr = _emit_expr(stmt.raise_expr, state)
            chain = ".__deref__()" * stmt.deref_depth
            state.temps.flush(out, indent)
            out.write(f"{indent}{expr}{chain}.__raise__();\n")
        elif stmt.return_tier:
            if stmt.args:
                args = ", ".join(_emit_expr(a, state) for a in stmt.args)
                value_cpp = f"::tpy::make_unexpected({stmt.cpp_type}({args}))"
                _witness("er.raise_args")
            else:
                value_cpp = f"::tpy::make_unexpected({stmt.cpp_type}{{}})"
                _witness("er.raise")
            state.temps.flush(out, indent)
            if state.finally_frames:
                _emit_finally_return(out, value_cpp, indent, state)
            else:
                out.write(f"{indent}return {value_cpp};\n")
        elif stmt.cpp_type is None and state.in_except_tier == "return":
            # Bare re-raise inside a return-tier handler: re-return the
            # captured error (_gen_raise's bare return-tier arm).
            assert state.try_except_err_opt is not None, \
                "return-tier re-raise without a live error capture"
            _witness("er.reraise")
            value_cpp = ("::tpy::make_unexpected("
                         f"std::move(*{state.try_except_err_opt}))")
            if state.finally_frames:
                _emit_finally_return(out, value_cpp, indent, state)
            else:
                out.write(f"{indent}return {value_cpp};\n")
        elif stmt.cpp_type is None:
            out.write(f"{indent}throw;\n")
        elif stmt.args:
            # Render args first so a mutated-ref-slot / Own-copy / union-ctor
            # arg temp registers, then flush the `__tmp_N` decls ahead of the
            # throw line -- the AST's per-statement temp flush.
            args = ", ".join(_emit_expr(a, state) for a in stmt.args)
            state.temps.flush(out, indent)
            if stmt.via_virtual:
                out.write(f"{indent}{stmt.cpp_type}({args}).__raise__();\n")
            else:
                out.write(f"{indent}throw {stmt.cpp_type}({args});\n")
        elif stmt.via_virtual:
            out.write(f"{indent}{stmt.cpp_type}{{}}.__raise__();\n")
        else:
            out.write(f"{indent}throw {stmt.cpp_type}{{}};\n")
    elif isinstance(stmt, THIRErrorReturnBind):
        # _gen_error_return_[propagate_/unwrap_]var_decl / _assign: the
        # counter draws BEFORE the call renders (the AST bumps first, so a
        # nested unwrap in an argument gets the higher number).
        n = state.try_counter.next()
        tmp = f"__try_tmp_{n}"
        call_cpp = _emit_expr(stmt.call, state)
        state.temps.flush(out, indent)
        name = escape_cpp_name(stmt.name)
        if stmt.decl_cpp is not None:
            out.write(f"{indent}{stmt.decl_cpp} {name};\n")
        inner = indent + INDENT
        out.write(f"{indent}{{\n")
        out.write(f"{inner}auto {tmp} = {call_cpp};\n")
        out.write(_er_check_stmt(tmp, inner, state))
        out.write(f"{inner}{name} = ::tpy::unwrap_ref_move(*{tmp});\n")
        out.write(f"{indent}}}\n")
        _witness("er.bind")
    elif isinstance(stmt, THIRErrorReturnDiscard):
        # _gen_error_return_stmt_block via the expr-stmt handler: there the
        # call renders BEFORE the counter draws (the block helper takes the
        # rendered call and bumps inside).
        call_cpp = _emit_expr(stmt.call, state)
        state.temps.flush(out, indent)
        n = state.try_counter.next()
        tmp = f"__try_tmp_{n}"
        inner = indent + INDENT
        out.write(f"{indent}{{\n")
        out.write(f"{inner}auto {tmp} = {call_cpp};\n")
        out.write(_er_check_stmt(tmp, inner, state))
        out.write(f"{indent}}}\n")
        _witness("er.discard")
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
    elif isinstance(stmt, THIRStmtSeq):
        _emit_stmts(out, stmt.stmts, indent_level, state)
    elif isinstance(stmt, THIRResumableReturn):
        # Scaffolding is skeleton in every position: the hook re-enters
        # `_make_async_return` / `_make_generator_resumable_return`, whose
        # value render loops back through `render_return_value` (the
        # id(ast)-keyed table this node's value was registered into).
        # No temps.flush here: `_lower_resumable_return_value` lowers with
        # the default `_ExprUse()` (allow_temps=False), so the value can
        # never carry a THIRArgTemp -- widening that seam to temp-bearing
        # values must add the flush.
        if state.resumable_return_hook is None:
            raise THIRCodeGenError(
                "THIRResumableReturn outside resumable leaf emission")
        out.write(state.resumable_return_hook(stmt.ast_stmt, indent_level))
    elif isinstance(stmt, THIRFrameNestedDef):
        # The member itself is scaffolding-emitted; the statement position
        # keeps only the marker line under its source comment.
        out.write(f"{indent}// def {stmt.name_cpp}: frame member\n")
    elif isinstance(stmt, THIRNoOpStmt):
        # No code -- the `// pass` source comment (if any) is emitted by the
        # caller (_emit_stmts) from the node's loc. A skipped statement's
        # leading trivia (trivia_loc) emits inline comments only.
        if stmt.trivia_loc is not None:
            state.comments.inline(out, stmt.trivia_loc, INDENT * indent_level)
    elif isinstance(stmt, THIRFoldedBlock):
        # Per-@overload-stub fold splice: the surviving statements emit flat
        # at the enclosing indent (the AST's direct gen_stmt calls).
        _witness("fold.overload_block")
        if stmt.burns_match_counter:
            state.match_counter += 1
        _emit_stmts(out, stmt.stmts, indent_level, state)
    elif isinstance(stmt, THIRMatchFoldBind):
        _witness("fold.overload_bind")
        binder = "auto" if stmt.by_value else "auto&"
        out.write(f"{INDENT * indent_level}{binder} {stmt.name_cpp}"
                  f" = {stmt.source_cpp};\n")
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
    if a.print_form is PrintForm.STR:
        return f"::tpy::__str__({inner})"
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
    if a.print_form is PrintForm.BYTEARRAY:
        return f"::tpy::ByteArrayPrinter({inner})"
    if a.print_form is PrintForm.OPT_VAL:
        return f"::tpy::print_optional_val({inner})"
    if a.print_form is PrintForm.OPT_VAL_BOOL:
        return f"::tpy::print_optional_val<::tpy::print_bool, {a.opt_inner_cpp}>({inner})"
    if a.print_form is PrintForm.OPT_VAL_FLOAT:
        return f"::tpy::print_optional_val<::tpy::print_float, {a.opt_inner_cpp}>({inner})"
    return inner


def _print_chain_token(expr, value, state: _EmitState) -> 'str | None':
    """gen_print's chain_token: a runtime kwarg renders as its expression, a
    literal via cpp_string_literal_expr, an empty/suppressed one skips (None).
    The " "/"\\n" defaults ride the value slot too -- cpp_string_literal_expr
    spells them byte-identically to gen_print's f'"{default}"' tokens."""
    if expr is not None:
        return _emit_expr(expr, state)
    if value is None:
        return None
    return cpp_string_literal_expr(value)


def _print_parts(args, sep_token: 'str | None', end_token: 'str | None',
                 state: _EmitState) -> list[str]:
    """The `<<` chain segments shared by the print statement and the
    void-lambda THIRPrintChain body, so the two renders cannot drift."""
    parts: list[str] = []
    for i, a in enumerate(args):
        if i > 0 and sep_token is not None:
            parts.append(sep_token)
        parts.append(_emit_print_arg(a, state))
    if end_token is not None:
        parts.append(end_token)
    return parts


def _emit_print(out: TextIO, stmt: THIRPrint, indent_level: int,
                state: _EmitState) -> None:
    # Mirrors gen_print's cout-sink path: `std::cout << a0 << SEP << a1
    # << ... << END;`. Default sep=" " between args, end="\n"; empty print()
    # is just the newline.
    indent = INDENT * indent_level
    # The AST path renders end before sep. Order is unobservable while the
    # kwarg gate admits only literal/plain-name sources (no hoisted temps);
    # match the AST order before widening that gate.
    sep_token = _print_chain_token(stmt.sep_expr, stmt.sep_value, state)
    end_token = _print_chain_token(stmt.end_expr, stmt.end_value, state)
    parts = _print_parts(stmt.args, sep_token, end_token, state)
    # Args render first: their hoisted temps flush before the cout line
    # (the AST's pre-statement `ctx.temps.flush`).
    state.temps.flush(out, indent)
    if not parts:
        # gen_print returns "" for a fully-suppressed chain; lowering rejects
        # the kwargs-on-empty-print shape, so this is a defensive no-op.
        return
    sink = ("std::cout" if stmt.sink_expr is None
            else f"::tpy::as_ostream({_emit_expr(stmt.sink_expr, state)})")
    out.write(f"{indent}{sink} << " + " << ".join(parts) + ";\n")


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
                   finally_guard_counter: ModuleCounter | None = None,
                   return_cpp: 'str | None' = None) -> None:
    """Emit `fn`'s body statements (no signature, no braces) at `indent_level`.

    `temps` is the `__tmp_N` sink, `with_counter` the `__ctx_N` sink,
    `try_counter` the try/error_return label+temp sink, and
    `finally_guard_counter` the `__fin_ran_N` cleanup-guard sink -- all
    module-cumulative, so the codegen seam passes the ctx-backed
    implementations (CtxTempSink / CtxCounter); the defaults are fresh local
    sinks (standalone/unit callers). `return_cpp` is the signature's return
    spelling (`ctx.current_return_cpp` at the seam), read only by the
    finally-chain return temp decl."""
    state = _EmitState(comments or _NO_COMMENTS, temps=temps or TempSink(),
                       with_counter=with_counter or ModuleCounter(),
                       try_counter=try_counter or ModuleCounter(),
                       finally_guard_counter=(finally_guard_counter
                                              or ModuleCounter()),
                       return_cpp=return_cpp,
                       error_return_cpp=fn.error_return_cpp)
    # Buffer the body so function-top hoists (@dynamic rebind slots, allocated
    # mid-body) can be prepended in the AST's `pending_hoist_decls` position.
    body_buf = io.StringIO()
    _emit_stmts(body_buf, fn.body, indent_level, state)
    for content in state.hoist_lines:
        out.write(f"{INDENT * indent_level}{content}\n")
    out.write(body_buf.getvalue())
    # Void @error_return functions return `{}` at the end -- the implicit
    # success value (gen_body's current_error_return tail; unconditional,
    # like the AST's).
    if fn.error_return_cpp and isinstance(fn.return_type, VoidType):
        out.write(f"{INDENT * indent_level}return {{}};\n")
        _witness("er.void_tail")


def emit_thir_constructor_tail(out: TextIO, ctor: THIRConstructor,
                               *, comments: CommentSink | None = None,
                               temps: TempSink | None = None,
                               with_counter: ModuleCounter | None = None,
                               try_counter: ModuleCounter | None = None,
                               finally_guard_counter: ModuleCounter | None = None,
                               body_indent_level: int = 2) -> None:
    """Emit a constructor's member-init-list + body tail (the ` : f(v)... {}` that
    follows the signature). The THIR counterpart of gen_record_decl's AST MIL+body
    emit: the signature is written by the AST path before this is called (the M1
    precedent -- signatures stay on the AST path). Byte-identical to that path's
    tail. M3a is pure-MIL, so `body` is empty and this emits ` {}` (or
    ` : inits {}`). MIL / base-init cells have no flush point, so arg temps
    never lower there (gate + validator enforced); the body shares the
    statement machinery and its sink. ``body_indent_level`` is 2 for an
    in-struct definition, 1 for an out-of-line one at namespace scope."""
    state = _EmitState(comments or _NO_COMMENTS, temps=temps or TempSink(),
                       with_counter=with_counter or ModuleCounter(),
                       try_counter=try_counter or ModuleCounter(),
                       finally_guard_counter=(finally_guard_counter
                                              or ModuleCounter()))
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
        # Buffer + prepend function-top hoists (a @dynamic rebind slot), same as
        # emit_thir_body -- the ctor body shares the DYN_PROTOCOL rebind arm, so
        # its hoisted `std::optional<slot>` must land at the body top too (else
        # the `__slot_N.emplace` references an undeclared slot).
        body_buf = io.StringIO()
        _emit_stmts(body_buf, ctor.body, body_indent_level, state)
        for content in state.hoist_lines:
            out.write(f"{INDENT * body_indent_level}{content}\n")
        out.write(body_buf.getvalue())
        out.write(f"{INDENT * (body_indent_level - 1)}}}\n")
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
                 finally_guard_counter: 'ModuleCounter | None' = None,
                 return_cpp: 'str | None' = None,
                 frame_shadow_probe: 'Callable[[str], bool] | None' = None,
                 resumable_return_hook: 'Callable[[object, int], str] | None'
                 = None,
                 ) -> None:
        self._body = body
        self._state = _EmitState(comments or _NO_COMMENTS,
                                 temps=temps or TempSink(),
                                 with_counter=with_counter or ModuleCounter(),
                                 try_counter=try_counter or ModuleCounter(),
                                 finally_guard_counter=(finally_guard_counter
                                                        or ModuleCounter()),
                                 return_cpp=return_cpp,
                                 frame_shadow_probe=frame_shadow_probe,
                                 resumable_return_hook=resumable_return_hook,
                                 hoist_drainable=False)

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

    def render_return_value(self, ret, *, allow_move: bool = False) -> str:
        """Render a `return v`'s value for `_make_async_return`'s
        scaffolding (the ret-tmp init / pending-slot store). The lowering
        bakes the last-use move (THIRMove) position-blind; the scaffolding
        knows the site, so a pre-finally store unwraps it -- an alias bound
        before the try can still read the local from the finally body.
        Defaults to no-move (the AST flag's fail-safe polarity): a future
        call site that forgets the kwarg gets the copy, never the move."""
        node = self._lookup(self._body.return_values, ret, "return value")
        if not allow_move and isinstance(node, THIRMove):
            node = node.value
        return _emit_expr(node, self._state)

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

    def render_region_expr(self, expr) -> str:
        """Render a region/loop pseudo-statement's user expression (with
        manager, for-loop iterable, range bound) -- the seam replacement for
        the skeleton's `gen_expr(...)` inside its emplace / &(..) /
        static_cast scaffolding (same flush contract as `render_cond`)."""
        return _emit_expr(self._lookup(self._body.region_exprs, expr,
                                       "region expr"), self._state)

    def emit_match_dispatch(self, out: TextIO, match_stmt,
                            indent_level: int,
                            arm_hook: 'Callable[[int, int], None]') -> None:
        """Emit a MatchDispatch's whole type-aware dispatch (subject +
        labels + guards) through THIR's match tiers -- the seam replacement
        for the skeleton's `gen_match` call. `arm_hook` is the skeleton's
        arm emitter keyed by id(case.body); it fires at each arm-body point
        (the same contract gen_match honors via resumable_arm_emitter), so
        arm bodies stay BB chains in the state machine."""
        node = self._lookup(self._body.match_dispatches, match_stmt,
                            "match dispatch")
        prev = self._state.match_arm_hook
        self._state.match_arm_hook = arm_hook
        try:
            # Direct tier emit: the skeleton calls gen_match without a
            # gen_stmt wrapper, so no leading statement comment here either.
            _emit_match(out, node, indent_level, self._state)
        finally:
            self._state.match_arm_hook = prev


class SimpleGenLeafEmitter:
    """Per-routed-body leaf renderer driven by the simple-generator lambda
    peephole skeleton (`gen_generators.gen_simple_generator_inline`). One
    instance per routed body holds one `_EmitState` -- the same contract as
    `ResumableLeafEmitter`, but the seam sites are static (one loop, one
    yield), so the body's blocks and expressions are direct fields, not
    id()-keyed tables."""

    def __init__(self, body, *, comments: 'CommentSink | None' = None,
                 temps: 'TempSink | None' = None,
                 with_counter: 'ModuleCounter | None' = None,
                 try_counter: 'ModuleCounter | None' = None,
                 finally_guard_counter: 'ModuleCounter | None' = None) -> None:
        self._body = body
        self._state = _EmitState(comments or _NO_COMMENTS,
                                 temps=temps or TempSink(),
                                 with_counter=with_counter or ModuleCounter(),
                                 try_counter=try_counter or ModuleCounter(),
                                 finally_guard_counter=(finally_guard_counter
                                                        or ModuleCounter()),
                                 hoist_drainable=False)

    def emit_init(self, out: TextIO, indent_level: int) -> None:
        """Emit the pre-loop init block -- the seam replacement for the
        skeleton's `gen_body(init_stmts, ...)` call."""
        _emit_stmts(out, self._body.init, indent_level, self._state)

    def emit_pre_yield(self, out: TextIO, indent_level: int) -> None:
        _emit_stmts(out, self._body.pre_yield, indent_level, self._state)

    def emit_post_yield(self, out: TextIO, indent_level: int) -> None:
        _emit_stmts(out, self._body.post_yield, indent_level, self._state)

    def render_cond(self) -> str:
        """Render the while-branch condition."""
        return _emit_expr(self._body.cond, self._state)

    def render_yield_value(self) -> str:
        """Render the yield value -- the seam replacement for the skeleton's
        `statements.gen_yield_value(ys)`."""
        return _emit_expr(self._body.yield_value, self._state)

    def render_iterable(self) -> str:
        """Render the for-branch source expression (the skeleton reuses the
        returned string across its capture / decltype / emplace scaffolding,
        exactly like the AST's single `gen_expr(iterable)` render)."""
        return _emit_expr(self._body.iterable, self._state)

    def render_range_arg(self, i: int) -> str:
        """Render the i-th for-range bound (the skeleton wraps it in its
        `static_cast` scaffolding)."""
        return _emit_expr(self._body.range_args[i], self._state)
