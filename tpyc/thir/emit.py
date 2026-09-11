"""THIR -> C++ emission for function bodies.

`emit_thir_body` writes a function body's C++ from THIR alone -- no
SemanticAnalyzer, no CodeGenContext. It reuses the existing analyzer-free leaf
helpers (`escape_cpp_name`, `expand_cpp_template`, `TpyType.to_cpp`), and its
output is pinned by the committed `expected/` snapshots.

Source comments are rendered through a `CommentSink` supplied by the codegen
seam (the stateless `ctx` comment helpers); the dump/tests pass the no-op
default so emission stays decoupled from the analyzer.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Callable, TextIO

from ..codegen_cpp.context import (
    INDENT,
    any_isinstance_check, cpp_bytes_literal_owned, cpp_bytes_literal_span,
    cpp_string_literal_expr, escape_cpp_char, escape_cpp_name,
    escape_cpp_string, expand_cpp_template, loop_var_binding,
    qualify_native_name,
)
from ..codegen_cpp.forms import LocalBinding, is_plain_nonvalue
from ..type_def_registry import (
    is_array, is_big_int_type, is_bytearray_type, is_bytes_type,
    is_bytes_view_type, is_dict,
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
    THIRDelItem,
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
    THIRDefaultConstruct,
    THIRLiteral,
    THIRMatch,
    THIRMatchArmEntry,
    THIRMatchBinding,
    THIRLambda,
    THIRMethodCall,
    THIRModuleVar,
    THIRDecayCopy,
    THIRMove,
    THIRName,
    THIRDynNarrowAlias,
    THIRNarrowAlias,
    THIRAnyNarrowAlias,
    THIRNestedDef,
    THIRNarrowedRead,
    THIRFrameNestedDef,
    THIRImportInit,
    THIRNoOpStmt,
    THIRFoldedBlock,
    THIRFoldedIfChain,
    THIRMatchFoldBind,
    THIROptionalPtrArg,
    THIROverloadDefault,
    THIRParamCopy,
    THIRPrint,
    THIRPrintChain,
    THIRPrintArg,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRRaise,
    THIRResumableReturn,
    THIRReturn,
    THIRFinallyDeferredReturn,
    THIRStmtSeq,
    THIRSetItem,
    THIRSelf,
    THIRSliceAssign,
    THIRInplaceContainerOp,
    THIRStmt,
    THIRCoroHandleMove,
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
    THIROwnOptRebuild,
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
    """Renders the source comments that precede / surround statements,
    backed by a CodeGenContext's stateless comment helpers.

    Duck-typed on `ctx` so emit.py stays free of a CodeGenContext import.
    """

    def __init__(self, ctx):
        self._ctx = ctx

    def stmt(self, out: TextIO, loc, indent: str) -> None:
        self._ctx.emit_inline_comments(out, loc, indent)
        self._ctx.emit_source_comment(out, loc, indent)

    def inline(self, out: TextIO, loc, indent: str) -> None:
        # Leading `#`-comment trivia only: a skipped statement's comments
        # still emit even though its code does not.
        self._ctx.emit_inline_comments(out, loc, indent)

    def elif_(self, out: TextIO, loc, indent: str) -> None:
        # An elif condition gets only its source line: a flattened elif
        # emits no inline comments.
        self._ctx.emit_source_comment(out, loc, indent)

    def case_(self, out: TextIO, loc, indent: str) -> None:
        # A `match` arm gets only its source line -- the case loc's source
        # comment, never the leading `#`-comment trivia.
        self._ctx.emit_source_comment(out, loc, indent)

    def else_(self, out: TextIO, else_body, indent: str) -> None:
        self._ctx.emit_else_comment(out, else_body, indent)

    def trailing(self, out: TextIO, body, indent: str) -> None:
        self._ctx.emit_block_trailing_comments(out, body, indent)


class TempSink:
    """Allocates `__tmp_N` names for THIRArgTemp and renders the pending
    declarations at the statement flush point -- the emit-side seam of
    `TempState`, backed by a CodeGenContext's `temps` (duck-typed on `ctx`,
    keeping emit.py free of a CodeGenContext import). `create` delegates to
    `create_typed` -- the type is already rendered at lowering, so both
    TempState arms (`create`'s param-type render and `create_typed`'s
    explicit string) reduce to the same pending row -- and every draw comes
    from the live module-cumulative `__tmp_N` counter, so numbering runs
    continuously across the module's bodies."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def create(self, cpp_type: str, init_expr: str, *,
               brace_init: bool = False, movable: bool = False) -> str:
        return self._ctx.temps.create_typed(cpp_type, init_expr,
                                            brace_init=brace_init,
                                            movable=movable)

    def conditional_region(self):
        return self._ctx.temps.conditional_region()

    def declare_named(self, name: str, cpp_type: str, *,
                      init: 'str | None' = None) -> None:
        self._ctx.temps.declare_named(name, cpp_type, init=init)

    def declare_named_auto(self, prefix: str, cpp_type: str) -> str:
        return self._ctx.temps.declare_named_auto(prefix, cpp_type)

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
    (`__ctx_N`, `__after_else_N`, ...), backed by a named int attribute of
    the live CodeGenContext (duck-typed on `ctx` like TempSink). Unlike the
    per-function counters below, these streams are never reset (like
    `__tmp_N`), so the numbering runs continuously across the module's
    bodies."""

    def __init__(self, ctx, attr: str) -> None:
        self._ctx = ctx
        self._attr = attr

    def next(self) -> int:
        n = getattr(self._ctx, self._attr) + 1
        setattr(self._ctx, self._attr, n)
        return n


class IterCounter:
    """PRE-value draw counter for the `__tpy_ret_N` / `__tpy_retp_N` /
    `__after_else_N` iter stream (first id 0, unlike ModuleCounter's 1).
    The leaf seam passes a ctx-backed one so the skeleton's iter_counter
    draws and THIR draws share one stream."""

    def __init__(self) -> None:
        self._n = 0

    def draw(self) -> int:
        n = self._n
        self._n += 1
        return n


class CtxIterCounter(IterCounter):
    """IterCounter backed by the live CodeGenContext's `iter_counter` --
    the leaf seam's shared draw stream."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def draw(self) -> int:
        n = self._ctx.iter_counter
        self._ctx.iter_counter = n + 1
        return n


@dataclass
class _FinallyFrame:
    """One enclosing cleanup layer during body emission -- the emit-side
    `FinallyContext`. Two arms: a `with` layer renders the fixed
    `__ctx_N.__exit__(...)` call (`ctx_n`/`exc_null_arg`); a try/finally
    layer re-emits its lowered finally body (`stmts`) at every exit site,
    counters advancing per copy. `terminates` is the last-stmt raise/return
    fact -- a terminating frame stops the chain walk and the caller
    suppresses its trailing exit statement (with frames never terminate).
    `loop_depth` is the live loop-nesting count at push
    (`len(ctx.loop_else_labels)` -- every loop appends an entry, labeled or
    not), so break/continue walk
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
    reproduces the `__start_N`/`__stop_N` numbering exactly.

    `slot_counter` reproduces `ctx.slots` for F2d rebind-slot pointer-locals
    and reassigned borrow-tuple walruses: within the eligible slice only
    those bump it (the other `__slot_N` consumers -- unions, @dynamic -- are
    gated out), and it pre-increments per allocation just like
    `SlotState.next_slot`, which fixes the `__slot_N` numbering.
    `rebind_slots` maps a rebind-slot local's name to its optional rebind
    slot N (allocated at the decl / first walrus, read at each reseat) --
    the analog of `ctx.rebind_slots`.

    `temps` is the `__tmp_N` sink THIRArgTemp renders through, flushed before
    the enclosing statement line (after its source comment -- one flush point
    per statement). Unlike the counters above it is NOT per-function: it is
    ctx-backed, so the numbering stays module-cumulative across the module's
    bodies.

    `comments` is None only in the constant position (`constants.py`), which
    emits one EXPRESSION: every comment render hangs off a statement."""
    comments: 'CommentSink | None'
    temps: TempSink
    # `with_counter` numbers `__ctx_N` (ctx attr `with_counter`); `try_counter`
    # numbers the throw tier's `__after_else_N` else labels, the return
    # tier's `__except_N`/`__after_try_N`/`__err_opt_N`, and the error_return
    # unwrap temps `__try_tmp_N`/`__er_N` (ctx attr `try_except_counter` --
    # one module-cumulative stream).
    with_counter: ModuleCounter
    try_counter: ModuleCounter
    # Numbers the `__fin_ran_N` cleanup guards (ctx attr
    # `finally_guard_counter`); allocated per pushed finally frame, in push
    # order.
    finally_guard_counter: ModuleCounter
    return_cpp: 'str | None' = None
    # @error_return context, mirroring the ctx fields the error_return
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
    # frame fields (for-loop iter vars) in ctx.frame_field_shadows; a
    # shadowed name must not take the `(*name)` peel. The
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
    iter_counter: IterCounter = field(default_factory=IterCounter)
    slot_counter: int = 0
    # Slot spelling for the module-init walk: codegen's SlotState switches the
    # prefix to `__global_slot` and every slot decl gains `static` there
    # (`slots.reset(global_scope=True)`). Only the GLOBAL_RVALUE arm reads
    # these -- top-level lowering rejects every other slot-allocating shape.
    slot_prefix: str = "__slot"
    slot_static: str = ""
    unpack_counter: int = 0
    # Function-top hoist lines (content only, no indent/newline): a @dynamic
    # rebind slot's `std::optional<slot> __slot_N;` is allocated at the reassign
    # point but its DECL text precedes the whole body
    # (`ctx.pending_hoist_decls`). `emit_thir_body` drains this before the body.
    hoist_lines: list[str] = field(default_factory=list)
    # Rebind-slot hoist lines held back until a rebind consumes the slot --
    # mirrors `ctx.deferred_rebind_slot_decls`. The slot is reserved at the
    # declaration (a rebind must not emplace over an aliased init value), but a
    # name whose every assignment is a fresh declaration in its own scope has
    # no consumer, and emitting it there leaves a dead `std::optional<T>`.
    deferred_rebind_hoists: dict[int, str] = field(default_factory=dict)
    # False in the generator LEAF emitters (Resumable/SimpleGen), which have no
    # drain point: a producer of `hoist_lines` asserts on it so a future
    # hoisting construct that slips past lowering's defer fails LOUD at the
    # produce site rather than emitting an undeclared `__slot_N`.
    hoist_drainable: bool = True
    # The sgen leaf's drain: a callable routing a held-back rebind-slot decl
    # into the live ctx's nested hoist scope, which the skeleton's
    # _lambda_body_sink flushes at the lambda prologue (its own drain
    # point). Only the rebind-slot producer consults it; the other
    # hoist_lines producers keep the drainable assert.
    hoist_sink: 'Callable[[str], None] | None' = None
    rebind_slots: dict[str, int] = field(default_factory=dict)
    # Plain block slots allocated by a slotless local's first INLINE_RVALUE
    # reseat (function-top only). A SEPARATE registry from `rebind_slots`:
    # the THIRAssign rebind-slot special case keys on that dict, and a
    # slotless local's later field-lift / pointer-copy reseats are plain
    # assigns rendered without consulting the slot -- registering
    # here keeps them from being hijacked into `p = &*(__slot = ...)`.
    inline_rvalue_slots: dict[str, int] = field(default_factory=dict)
    # Names whose rebind slot backs a ptr-variant UNION local: their rvalue
    # reseats spell `.emplace` + `to_ptr_variant(*slot)` via THIRPtrLocalRebind,
    # so a plain THIRAssign on them (a same-union name copy) must NOT take the
    # `&*(__slot_N = ...)` optional-slot reseat arm.
    union_slot_locals: set[str] = field(default_factory=set)
    # Names whose `rebind_slots` entry numbers a MODULE-scope
    # `__global_slot_N` -- a slot the GLOBAL_* rebinds spell through
    # `slot_prefix` rather than as a local `__slot_N`, optional
    # (GLOBAL_HOIST_RVALUE) or not: a plain THIRAssign on such a name -- the
    # pointer-Optional global's pass-through / field-lift writes -- must NOT
    # take the `&*(__slot_N = ...)` reseat arm, which would both name an
    # undeclared local slot and deref a `T*`.
    global_slot_locals: set[str] = field(default_factory=set)
    # Names whose rebind slot backs a reassigned borrow-tuple WALRUS: their
    # later storage-alias reseats are plain assigns
    # (`t = tuple_to_pointer<..>(h.pair);`), never the optional-slot arm.
    btuple_slot_locals: set[str] = field(default_factory=set)
    # Enclosing `with` layers, innermost last -- return/break/continue walk it
    # to render the inline `__exit__` chain (the emit-side finally stack);
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
    # Leaf-mode bridge to the skeleton's finally stack: push mirrors a
    # THIR finally frame as a FinallyContext (so the resumable return
    # hook's chain walk inlines the finally with the SAME guard), pop
    # removes it. None in sync emission.
    ast_finally_push: 'Callable[..., object] | None' = None
    ast_finally_pop: 'Callable[[], None] | None' = None
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
    # the enclosing statement (the same role as ctx.indent_level).
    stmt_indent_level: int = 0

    def next_loop_index(self) -> int:
        return self.iter_counter.draw()

    def next_slot(self) -> int:
        self.slot_counter += 1  # pre-increment: first slot is __slot_1
        return self.slot_counter

    def global_slot(self) -> str:
        """The module-init slot spelling (`static __global_slot_N`). Every
        OTHER slot site spells a bare `__slot_N`, which at namespace scope
        would leave the global pointing at a dead frame once `__tpy_init`
        returns -- so those sites assert instead (see `assert_local_slot`).
        Lowering rejects such a body first; this is the independent backstop
        that turns a missed reject into a loud failure, not silent UB."""
        return f"{self.slot_prefix}_{self.next_slot()}"

    def assert_local_slot(self) -> None:
        assert self.slot_prefix == "__slot", (
            "a block-scoped __slot_N allocated at module scope: it needs "
            "`static __global_slot_N` lifetime (lowering should have rejected "
            "this body -- see _rejects_global_slot)")

    def next_unpack(self) -> int:
        # Reproduces ctx.unpack_counter: per-function, pre-incremented (first
        # is __tup_1); its other consumers are gate-rejected shapes.
        self.unpack_counter += 1
        return self.unpack_counter


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
        if lit.none_cpp is not None:
            return lit.none_cpp
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
        # repr() is the shortest round-tripping form and a valid C++
        # double literal; a Float32-typed
        # literal (retyped at lowering from its float_literal_to_float32
        # coerce) takes the `f` suffix.
        rendered = repr(v)
        f32 = is_float32_type(lit.result_type)
        if rendered in ("inf", "-inf", "nan"):
            # No C++ literal spells these, so they fold to the constexpr
            # numeric_limits form, keyed on the `repr()` token. The `-inf`
            # leg is unreachable from source (a negative float literal parses
            # as a unary minus over the positive one, which renders through
            # the operator).
            base = "float" if f32 else "double"
            lim = (f"std::numeric_limits<{base}>::quiet_NaN()"
                   if rendered == "nan"
                   else f"std::numeric_limits<{base}>::infinity()")
            return f"(-{lim})" if rendered == "-inf" else lim
        if f32:
            return rendered + "f"
        return rendered
    if isinstance(v, int):
        return lit.int_cpp if lit.int_cpp is not None else str(v)
    return str(v)


def _emit_chained_compare_stmtexpr(e: THIRChainedCompareStmtExpr,
                                   state: _EmitState) -> str:
    # Bind each non-simple operand to an `auto&& _cmpI` temp, then interleave
    # bindings with the left-folded `&&` chain so operands after a failed pair
    # never evaluate. Each pair renders as a bare op with per-side `{0}` casts
    # over the operand REPRs (temp name or inline render).
    n = len(e.ops)
    reprs: list[str] = []
    binds: list[str | None] = []
    for i in range(n + 1):
        # Operands 0 and 1 always run; every later one sits behind a passed
        # compare, so its deferred temps bank into a region and splice at
        # the operand.
        if i >= 2:
            with state.temps.conditional_region() as _region:
                code = _emit_expr(e.inits[i], state)
            prefix = _region.prefix
        else:
            code = _emit_expr(e.inits[i], state)
            prefix = ""
        if e.bound[i]:
            reprs.append(f"_cmp{i}")
            init = f"({prefix}{code})" if prefix else code
            binds.append(f"auto&& _cmp{i} = {init};")
        else:
            reprs.append(f"({prefix}{code})" if prefix else code)
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
    # Apply the operand wrappers, expand the operator's cpp_template, swap
    # the checked div/mod
    # helper when the divisor is proven non-zero, and paren-wrap the result.
    # Comparisons reuse this path (their dunder carries a `{self} OP {0}`
    # template), so the same code emits both arithmetic and comparison binops.
    left = _emit_expr(e.left, state)
    if e.resolved is None and e.op in ("&&", "||"):
        # The RHS runs only when the LHS does not short-circuit: its
        # deferred temps bank into a conditional region and splice ahead of
        # the operand -- the `({prefix}{right})` wrap.
        with state.temps.conditional_region() as _rhs_region:
            right = _emit_expr(e.right, state)
    else:
        _rhs_region = None
        right = _emit_expr(e.right, state)
    # Post-generation operand casts (int-enum underlying / mixed BigInt-float),
    # applied before the wrapper/template expansion.
    if e.left_cast is not None:
        left = e.left_cast.format(left)
    if e.right_cast is not None:
        right = e.right_cast.format(right)
    if _rhs_region is not None and _rhs_region.prefix:
        right = f"({_rhs_region.prefix}{right})"
    if e.template_override is not None:
        # The rebuilt fixed-int literal arm over the target-typed operands:
        # plain template expansion, no wrappers, no parens, no divisor swap.
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
        # the free function takes both operands as arguments. The gate
        # admits a template-less rb only in this shape.
        result = (f"{qualify_native_name(rb.method.native_name)}"
                  f"({wl}, {wr})")
    if e.divisor_non_zero:
        result = result.replace("div_check", "div_floor").replace("mod_check", "mod_floor")
    if e.op == "!=" and rb.method.name == "__eq__":
        # `!=` resolved via `__eq__` derives by negation -- `(!(...))`.
        return f"(!({result}))"
    return f"({result})" if e.paren_wrap else result


def _emit_call(e: THIRCall, state: _EmitState) -> str:
    if e.cpp_template is not None:
        # A scalar type-constructor call: expand the (sema-substituted,
        # positional-only) __init__ template over the args with no receiver.
        return expand_cpp_template(e.cpp_template, None,
                                   *[_emit_expr(a, state) for a in e.args])
    args = ", ".join(_emit_expr(a, state) for a in e.args)
    if e.callee_expr is not None:
        # A computed callable: the callee renders parenthesized ahead of the
        # arg list (`(make_adder(10))(5)`, `(::tpy::__getitem__(fns, 0))(100)`).
        return f"({_emit_expr(e.callee_expr, state)})({args})"
    if e.native_name is not None:
        # A @native free-function builtin (e.g. `len(c)` -> `::tpy::__len__(c)`):
        # dispatch on the resolved symbol.
        return f"{qualify_native_name(e.native_name)}({args})"
    # A generic TPy callee's explicit template-arg list (pre-rendered at
    # lowering): `callee<T1, T2>(args)` over the plain / imported spelling.
    targs = (f"<{', '.join(e.template_args_cpp)}>"
             if e.template_args_cpp else "")
    if e.callee_cpp is not None:
        # A cross-module callee: the pre-rendered absolute spelling
        # (from `imported_free_callee_cpp`).
        return f"{e.callee_cpp}{targs}({args})"
    return f"{escape_cpp_name(e.callee)}{targs}({args})"


def _emit_union_arg_lift(e: THIRUnionArgLift, state: _EmitState) -> str:
    # The temp-free pointer-variant arms: the monostate member for a None
    # literal, the address-of lift for a member-typed name (deref prepends the
    # pointer-local/receiver `(*...)`), and the const conversion for an
    # already-union name into a deep-const slot (const_wrap, whose spelling
    # lowering picked from the source's binding). variant_cpp was fixed at
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
    if e.const_wrap == "storage":
        return f"::tpy::to_const_ptr_variant({inner})"
    if e.const_wrap:
        return f"{inner}.as_const()"
    return f"{e.variant_cpp}{{&({inner})}}"


def _emit_ctor_call(e: THIRCtorCall, state: _EmitState) -> str:
    # The record-construction render: the RAW source name (same-module) or
    # the qualified `::ns::Name` spelling (imported record), decided at
    # lowering, over the lowering-admitted args. @native_c PODs take the
    # aggregate `{args}` init.
    args = ", ".join(_emit_expr(a, state) for a in e.args)
    if e.brace_init:
        return f"{e.type_cpp}{{{args}}}"
    return f"{e.type_cpp}({args})"


def _emit_method_call(e: THIRMethodCall, state: _EmitState) -> str:
    # Three dispatch arms for a receiver call, in order: cpp_template
    # expansion, @native free-function symbol (receiver prepended), plain
    # member call. The member accessor is `->` only for a user-record
    # pointer-local receiver (`is_arrow`); container receivers are pinned to
    # bare names.
    recv = _emit_expr(e.receiver, state)
    arrow = e.is_arrow
    if e.move_receiver:
        # Consuming method: the rvalue-qualified call moves the receiver.
        # A pointer-local receiver
        # moves its DEREF (`std::move(*w).take()` -- the arrow folds into
        # the deref, so the member access is `.`).
        if arrow:
            recv = f"std::move(*{recv})"
            arrow = False
        else:
            recv = f"std::move({recv})"
    args = [_emit_expr(a, state) for a in e.args]
    if e.cpp_template is not None:
        return expand_cpp_template(e.cpp_template, recv, *args)
    if e.native_function_name is not None:
        return f"{qualify_native_name(e.native_function_name)}({', '.join([recv, *args])})"
    mtargs = (f"<{', '.join(e.method_targs_cpp)}>"
              if e.method_targs_cpp else "")
    if e.deref_check:
        # Unproven pointer-repr Optional receiver: null-check the (already
        # `T*`) receiver before the `.` member call.
        return (f"::tpy::deref_check({recv}).{e.method_cpp}{mtargs}"
                f"({', '.join(args)})")
    if e.deref_chain:
        # User Deref-wrapper method call: N `.__deref__()` calls between the
        # bare receiver and the member call (`r.__deref__().sum()`); a
        # pointer-local receiver joins the first hop with `->`
        # (`g->__deref__().push_back(3)`).
        first = "->" if e.is_arrow else "."
        chain = (f"{first}__deref__()"
                 + ".__deref__()" * (e.deref_chain - 1))
        return f"{recv}{chain}.{e.method_cpp}({', '.join(args)})"
    if e.callable_value_unwrap:
        # Optional[Callable] field invoke: the `.value()` unwrap between
        # the member and the call.
        return f"{recv}.{e.method_cpp}.value()({', '.join(args)})"
    return (f"{recv}{'->' if arrow else '.'}"
            f"{e.method_cpp}{mtargs}({', '.join(args)})")


def _emit_comprehension(e: 'THIRComprehension', state: _EmitState) -> str:
    """The GCC stmt-expr comprehension render. Inner lines indent relative
    to the enclosing statement (`state.stmt_indent_level`); the
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
        # The array_from_index RANGE arm: sema proved literal bounds, so
        # start/step inline as index arithmetic inside the per-index lambda;
        # no `({` prelude, no reserve. Element temps flush into the lambda
        # before the `return` (per-iteration: `auto __tmp_N = i;` ahead of
        # `Box(std::move(__tmp_N))`).
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
        cp_el = state.temps.checkpoint()
        elem_s = _emit_expr(e.element, state)
        state.temps.flush_since(buf, cp_el, ind1)
        buf.write(f"{ind1}return {elem_s};\n")
        buf.write(f"{stmt_ind}}})")
        return buf.getvalue()
    if e.loop == "array_source":
        # The array_from_index SOURCE arm: a `({...})` prelude borrows the
        # sized source once (lvalue verdict) and the per-index lambda indexes
        # it (`__obj_N[__i_N]`). The loop-var binding is the shared non-const
        # `loop_var_binding` (value copy / `auto&&` borrow, split on whether
        # the element is a value type).
        n = state.next_loop_index()
        obj = f"__obj_{n}"
        binding_kw = "auto&" if e.iterable_lvalue else "auto"
        buf = io.StringIO()
        buf.write("({\n")
        buf.write(f"{ind1}{binding_kw} {obj} = {_emit_expr(e.iterable, state)};\n")
        buf.write(f"{ind1}::tpy::array_from_index<{e.array_elem_cpp}, "
                  f"{e.array_size_cpp}>("
                  f"[&](std::size_t __i_{n}) -> {e.array_elem_cpp} {{\n")
        if e.unpack_targets:
            # Unpack heads over the indexed element (`auto& __tup_N =
            # __obj_N[__i_N];` + per-var `std::get` decls -- the begin_end
            # arm's prologue at the indexed read, plain `auto&`).
            un = state.next_unpack()
            tmp = f"__tup_{un}"
            buf.write(f"{ind2}auto& {tmp} = {obj}[__i_{n}];\n")
            for i, name in enumerate(e.unpack_targets):
                if name is None:
                    continue
                buf.write(f"{ind2}{e.unpack_target_cpps[i]} "
                          f"{escape_cpp_name(name)} = std::get<{i}>({tmp});\n")
        else:
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
        cp_el = state.temps.checkpoint()
        insert = f"__result.insert({_emit_expr(e.element, state)})"
    else:
        cp_el = state.temps.checkpoint()
        insert = f"__result.push_back({_emit_expr(e.element, state)})"
    if e.conditions:
        # Condition temps land at loop-body indent BEFORE the `if` -- the
        # loop var they consume is only in scope here.
        # checkpoint/flush_since drains ONLY the
        # conditions' own temps: an outer pending decl (a walrus predecl
        # enqueued before this comp rendered) must stay for the statement
        # flush, not fall inside the loop. Element temps flush innermost
        # (right above the insert), after the filter passes.
        cp = state.temps.checkpoint()
        cond_str = " && ".join(_emit_expr(c, state) for c in e.conditions)
        state.temps.flush_since(buf, cp, ind2)
        buf.write(f"{ind2}if ({cond_str}) {{\n")
        if e.kind != "dict":
            state.temps.flush_since(buf, cp_el, ind3)
        buf.write(f"{ind3}{insert};\n")
        buf.write(f"{ind2}}}\n")
    else:
        # Element temps flush per-iteration at loop-body indent, right
        # above the insert (the degrade seam: a temp DEFERRED by an
        # enclosing conditional region relocates here as the eager
        # `std::optional<T> __tmp_N = init;` decl).
        if e.kind != "dict":
            state.temps.flush_since(buf, cp_el, ind2)
        buf.write(f"{ind2}{insert};\n")
    buf.write(f"{ind1}}}\n")
    buf.write(f"{ind1}std::move(__result);\n")
    buf.write(f"{stmt_ind}}})")
    return buf.getvalue()


def _emit_genexpr(e: 'THIRGenExpr', state: _EmitState) -> str:
    """The make_generator render of a generator expression: an inner mutable
    lambda binds each element and yields `optional<slot>`. An LVALUE source
    aliases through an outer `[caps]()` IIFE; a NON-LVALUE source moves into the
    lambda's init-captures under an `if (!__started)` seed. Indents relative to
    the enclosing statement (stmt_indent_level)."""
    stmt_ind = INDENT * state.stmt_indent_level
    ind1 = stmt_ind + INDENT

    def binding_lines(ind: str) -> str:
        # An unpack head draws its `__tup_N` off the shared per-function
        # counter at emit, like the comp unpack; the single-var shape keeps
        # the pre-rendered loop_var_binding line.
        if not e.unpack_targets:
            return f"{ind}{e.binding_cpp}\n"
        tmp = f"__tup_{state.next_unpack()}"
        ref = "const auto&" if e.const_loop_var else "auto&"
        out = f"{ind}{ref} {tmp} = *__beg++;\n"
        for i, name in enumerate(e.unpack_targets):
            if name is None:
                continue
            out += (f"{ind}{e.unpack_target_cpps[i]} "
                    f"{escape_cpp_name(name)} = std::get<{i}>({tmp});\n")
        return out

    def yield_lines(buf: io.StringIO, ind: str, ind_inner: str) -> None:
        # Cond/yield temps flush per-iteration inside the lambda, the yield
        # wrapped in the &&-joined filter when conditions exist.
        if e.conditions:
            cp = state.temps.checkpoint()
            cond_str = " && ".join(_emit_expr(c, state) for c in e.conditions)
            state.temps.flush_since(buf, cp, ind)
            buf.write(f"{ind}if ({cond_str}) {{\n")
            cp2 = state.temps.checkpoint()
            elem_s = _emit_expr(e.element, state)
            state.temps.flush_since(buf, cp2, ind_inner)
            buf.write(f"{ind_inner}return std::optional<{e.slot_cpp}>"
                      f"({elem_s});\n")
            buf.write(f"{ind}}}\n")
        else:
            cp = state.temps.checkpoint()
            elem_s = _emit_expr(e.element, state)
            state.temps.flush_since(buf, cp, ind)
            buf.write(f"{ind}return std::optional<{e.slot_cpp}>({elem_s});\n")

    if e.range_args:
        # The counter lambda: range bounds move into the init-captures, no
        # IIFE at any arity.
        ind2 = ind1 + INDENT
        ind3 = ind2 + INDENT
        cpp_iter = e.counter_cpp
        args = [_emit_expr(a, state) for a in e.range_args]
        if len(args) == 1:
            captures = (f"__i = {cpp_iter}(0), "
                        f"__stop = static_cast<{cpp_iter}>({args[0]})")
        elif len(args) == 2:
            captures = (f"__i = static_cast<{cpp_iter}>({args[0]}), "
                        f"__stop = static_cast<{cpp_iter}>({args[1]})")
        else:
            captures = (f"__i = static_cast<{cpp_iter}>({args[0]}), "
                        f"__stop = static_cast<{cpp_iter}>({args[1]}), "
                        f"__step = static_cast<{cpp_iter}>({args[2]})")
        buf = io.StringIO()
        buf.write(f"::tpy::make_generator<{e.slot_cpp}>(\n")
        buf.write(f"{ind1}[{e.inner_captures}{captures}]() mutable -> "
                  f"std::optional<{e.slot_cpp}> {{\n")
        if len(args) <= 2:
            buf.write(f"{ind2}while (__i < __stop) {{\n")
            buf.write(f"{ind3}{e.binding_cpp}\n")
        else:
            buf.write(f"{ind2}::tpy::range_check_step_nonzero(__step);\n")
            if e.range_overflow_check:
                buf.write(f"{ind2}::tpy::range_check_overflow<{cpp_iter}>"
                          f"(__i, __stop, __step);\n")
            buf.write(f"{ind2}while ((__step > 0) ? (__i < __stop) : "
                      f"(__i > __stop)) {{\n")
            buf.write(f"{ind3}{e.binding_cpp}\n")
            buf.write(f"{ind3}__i += __step;\n")
        yield_lines(buf, ind3, ind3 + INDENT)
        buf.write(f"{ind2}}}\n")
        buf.write(f"{ind2}return std::nullopt;\n")
        buf.write(f"{ind1}}}\n")
        buf.write(f"{stmt_ind})")
        return buf.getvalue()

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
        buf.write(binding_lines(ind3i))
        yield_lines(buf, ind3i, ind3i + INDENT)
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
    buf.write(binding_lines(ind3i))
    yield_lines(buf, ind3i, ind3i + INDENT)
    buf.write(f"{ind2i}}}\n")
    buf.write(f"{ind2i}return std::nullopt;\n")
    buf.write(f"{lambda_ind}}}\n")
    buf.write(f"{ind1});\n")
    buf.write(f"{stmt_ind}}}()")
    return buf.getvalue()


def _emit_vararg_pack(e: 'THIRVarargPack', state: _EmitState) -> str:
    # A sole `*expr` unpack forwards the container directly (span source) or
    # through a borrowed span, the empty pack takes the nullary ctor, and the
    # per-arg form hoists a std::array temp (element temps first, then the
    # array).
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
    # Dispatch on the resolved container family. list/Array brace-inits are
    # consumed
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
    # An empty list literal spells its type (a bare `{}` would be ambiguous
    # against a T* assignment) unless the lowering marked the position one
    # where the enclosing brace deduces it; an empty Array is gated out at
    # eligibility.
    if not e.elements and is_list(t):
        return "{}" if e.bare_empty else f"{t.to_cpp()}{{}}"
    elems = ", ".join(_emit_expr(x, state) for x in e.elements)
    if e.make_container:
        return f"::tpy::make_vector<{e.elem_cpp}>({elems})"
    literal = f"{{{elems}}}"
    # A std::array of a brace-initialised aggregate element (a nested list)
    # needs the extra std::array brace level so each element copy-list-inits
    # cleanly (only a demoted Array threads a container element target).
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
    """`[elems] * count`. Elements render before the array counter draws and
    before the count, so a counter drawn by an element numbers ahead of
    both."""
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
    if e.lazy:
        # Unmaterialized: the range object IS the value.
        return range_expr
    return f"::tpy::from_range<{e.result_cpp}>({range_expr})"


def _emit_field_access(e: THIRFieldAccess, state: _EmitState) -> str:
    if e.deref_check:
        # Unproven Optional member access: null-check the (already `T*`)
        # receiver before the `.` member read.
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
    # value read unwraps unconditionally.
    return f"(*{base})" if e.narrowed_deref else base


def _emit_subscript(e: THIRSubscript, state: _EmitState) -> str:
    recv = _emit_expr(e.receiver, state)
    if isinstance(unwrap_qualifiers(e.receiver.result_type), TupleType):
        # Tuple element read: the index is a normalized compile-time constant (a
        # THIRLiteral), so the C++ template argument is a bare non-negative
        # int (a value-scalar element, no lift).
        if not isinstance(e.index, THIRLiteral):
            raise THIRCodeGenError("tuple subscript index is not a THIRLiteral")
        get = f"std::get<{e.index.value}>({recv})"
        if e.elem_ref:
            # Generic val_or_ptr slot: read as a usable value/reference.
            get = f"::tpy::tuple_elem_ref({get})"
        return f"(*{get})" if e.deref else get
    # Container (list / dict) index/key lookup. A runtime-BigInt index arrives
    # pre-wrapped in its `.to_fixed_check<int32_t>()` THIRCoerce (lowering's
    # `_narrow_bigint_index`), so the emit stays index-type-neutral.
    idx = _emit_expr(e.index, state)
    if e.record_getitem:
        # User-record operator[]: bare, no size_t cast (the operator takes the
        # user's declared key type).
        return f"{recv}[{idx}]"
    if e.bounds_safe:
        # Index proven in [0, len): skip normalize_index. A literal index needs no
        # cast (a compile-time constant is -Wsign-conversion-exempt); a variable
        # index casts to size_t for the builtin operator[].
        if isinstance(e.index, THIRLiteral):
            return f"{recv}[{idx}]"
        return f"{recv}[static_cast<std::size_t>({idx})]"
    rt = unwrap_qualifiers(e.receiver.result_type)
    if is_bytes_type(rt) or is_bytes_view_type(rt) or is_bytearray_type(rt):
        # bytes' `__getitem__(Int32)` is a @native free-function dunder, not
        # the containers' checked `::tpy::__getitem__` template.
        # bytearray shares that dunder (its own natives are the WRITE side).
        return f"::tpy::bytes_getitem({recv}, {idx})"
    return f"::tpy::__getitem__({recv}, {idx})"


def _emit_fstring(e: THIRFString, state: _EmitState) -> str:
    # Assembled as a pure string function (the per-arg type dispatch is
    # already carried as wrap templates):
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
            # (never brace-escaped or C++-escaped), in both the source and
            # runtime-length views.
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
    # The slice arm: the resolved __getitem__ @cpp_template expanded over the
    # receiver and the slice argument -- a slice-typed variable index rendered
    # bare, or a BasicSlice/Slice initializer (stepped per the source syntax);
    # an absent bound renders std::nullopt.
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
    # Form lifts in both directions. optional_to_ptr's const overload is
    # auto-selected by the optional's own const-ness, so is_const here is
    # carried for other families, not the rendered helper. A pairing no arm
    # below covers is a THIRCodeGenError, not a silent passthrough.
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
        # non-owning borrow copies (`ptr_to_optional`, F2b/F2c). `move` is set
        # by lowering from the `movable_locals` + last-use facts.
        if isinstance(t, OptionalType):
            if t.uses_generic_param_trait():
                # A generic `T | None` storage slot: the source may be either
                # form (a `T*` read or an `opt_param_t<T>` slot), so the lift
                # is the runtime helper that absorbs both.
                helper = ("to_opt_storage_move" if e.move
                          else "to_opt_storage")
                return f"::tpy::{helper}<{t.to_cpp()}>({inner})"
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
        # owned constructor -- `std::string(x)` / `::tpy::Bytes(x)` -- the
        # view->owned construction being explicit, spelled through the shared
        # `view_to_owned_conv` helper. The materializing str-family coercions
        # (strview_to_str / str_to_string / strview_to_string) lower here too:
        # the cross-type respelling is family-internal, the emit identical --
        # `String` is the same owned std::string spelled as a distinct type.
        # An explicit `materialize` (set at lowering, where the view-vs-object
        # meaning is decided) overrides the family derivation -- bytearray's
        # view copy takes this branch only via the explicit True (see the
        # node docstring); an explicit False falls through to the object
        # move/copy arm below.
        if e.materialize is True or (e.materialize is None
                                     and (is_str_type(t) or is_string_type(t)
                                          or is_bytes_type(t))):
            return f"{view_to_owned_conv(t)}({inner})"
        # An open-`T` storage sink: the value arrives in the instantiation's
        # PARAMETER form, which for `str` / `bytes` is a view over the storage
        # form the sink spells, so the construction is explicit and keyed on
        # `T` alone. `param_to_return` is the return sink's sibling -- it must
        # not copy at a reference-typed instantiation, where `val_or_ref_t<T>`
        # is `T&`. An `Own[T]` param at its last use MOVES instead
        # (`own_param_t<T>` is already the storage form).
        if isinstance(t, TypeParamRef):
            if e.move:
                return f"std::move({inner})"
            helper = ("param_to_return" if e.generic_return
                      else "param_to_storage")
            return f"::tpy::{helper}<{t.to_cpp()}>({inner})"
        # A plain non-value record/container slot (a field write / MIL cell):
        # the storage sink consumes the source directly -- `std::move(v)` for
        # an owned source at its last use, the bare render (a copy) otherwise.
        # The record sibling of the TypeParamRef arm; both spell inline with
        # no runtime helper.
        if is_plain_nonvalue(t):
            return f"std::move({inner})" if e.move else inner
    raise THIRCodeGenError(
        f"unhandled THIRFormConvert: {type(t).__name__} {e.value.form}->{e.form}")


def _declare_rebind_slot(state: '_EmitState', name: str, slot: int,
                         slot_cpp: str) -> None:
    """Reserve `name`'s rebind slot, holding its hoist line back.

    The single registration point for a PRE-declared slot -- mirrors
    `ctx.declare_rebind_slot`. Emitting the line here instead would leave dead
    `std::optional<T>` locals behind.
    """
    state.rebind_slots[name] = slot
    state.deferred_rebind_hoists[slot] = f"std::optional<{slot_cpp}> __slot_{slot};"


def _use_rebind_slot(state: '_EmitState', name: str) -> int | None:
    """The rebind slot for `name`, emitting its held-back hoist line."""
    slot = state.rebind_slots.get(name)
    if slot is not None:
        line = state.deferred_rebind_hoists.pop(slot, None)
        if line is not None:
            if state.hoist_sink is not None:
                state.hoist_sink(line)
            else:
                assert state.hoist_drainable, (
                    "a deferred rebind-slot hoist reached a non-draining leaf "
                    "emitter")
                state.hoist_lines.append(line)
    return slot


def _emit_expr(e: THIRExpr, state: _EmitState) -> str:
    if isinstance(e, THIRName):
        # `deref`: a pointer-local read in a value position (a record call
        # arg) renders `(*p)`. A pre-spelled native/imported global (`cpp`)
        # renders verbatim -- `qualify_native_name` / `imported_variable_cpp`
        # output is already spelled, never escaped.
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
    if isinstance(e, THIRDefaultConstruct):
        return f"{e.cpp_type}{{}}"
    if isinstance(e, THIRStrLiteral):
        return cpp_string_literal_expr(e.value)
    if isinstance(e, THIRBytesLiteral):
        # The owned/span verdict was decided at lowering from the sink and
        # rides the form tag (see the node's doc); an empty span literal
        # spells the bare `std::span<const uint8_t>{}`.
        if e.form is Form.STORAGE:
            return cpp_bytes_literal_owned(e.value)
        if not e.value:
            return "std::span<const uint8_t>{}"
        return cpp_bytes_literal_span(e.value)
    if isinstance(e, THIRFString):
        return _emit_fstring(e, state)
    if isinstance(e, THIRCharLiteral):
        # A Char-targeted str literal (compare operand opposite a Char, a
        # Char-annotated decl init, a Char-slot call arg).
        return f"'{escape_cpp_char(e.value)}'"
    if isinstance(e, THIRWalrus):
        # The per-class walrus render (see the node doc); the first binding
        # registers its `type name[ = init];` pre-decl on the sink's named
        # row, which the enclosing statement / loop-head / lambda flush
        # places.
        if e.cpp_type is not None:
            state.temps.declare_named(e.cpp_name, e.cpp_type, init=e.init)
        v = _emit_expr(e.value, state)
        if e.emplace_cpp is not None:
            # frame_slot<T> write: a bare brace-init needs its type prefix to
            # bind to emplace's forwarding ref (`typed_brace_init`, shared
            # with the frame-slot statement write).
            if v.startswith("{"):
                v = f"{e.emplace_cpp}{v}"
            return f"{e.cpp_name}.emplace({v})"
        if e.slot_cpp is not None:
            # Reassigned borrow-tuple: the owning slot is allocated once per
            # target (sibling occurrences reuse it, keyed on `rebind_slots`)
            # and declared on the named row next to the target.
            slot_n = _use_rebind_slot(state, e.name)
            if slot_n is None:
                slot_n = (state.assert_local_slot() or state.next_slot())
                state.rebind_slots[e.name] = slot_n
                state.temps.declare_named(
                    f"__slot_{slot_n}", f"std::optional<{e.slot_cpp}>")
            # Both the fresh and the hoisted-slot (reuse) paths mark the
            # name so plain reseats stay off the ptr-Optional arm.
            state.btuple_slot_locals.add(e.name)
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
        # Over value/pointer variants: one
        # holds_alternative per check member, OR-joined and parenthesized for
        # the multi-member (tuple / inline-union) form.
        checks = [f"std::holds_alternative<{m}>({e.variant_cpp})"
                  for m in e.member_cpps]
        return checks[0] if len(checks) == 1 else "(" + " || ".join(checks) + ")"
    if isinstance(e, THIRDynIsinstance):
        # The C++17 if-init form: the whole `init; cond` sits inside the if's
        # own parens (the `{init_clause}{cond}` composition).
        return f"{e.init_cpp}; ({e.ptr_local} != nullptr)"
    if isinstance(e, THIRDynIsinstanceMulti):
        # The tuple form's OR-chain; a single check (the root-class form)
        # renders bare -- only a multi-member join takes the outer parens.
        if len(e.checks_cpp) == 1:
            return e.checks_cpp[0]
        return "(" + " || ".join(e.checks_cpp) + ")"
    if isinstance(e, THIRAnyIsinstance):
        # The shared composition (any_isinstance_check) -- one spelling for
        # every Any-typed isinstance check.
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
        # The `!` arm over a bool operand, whose truthiness render is the
        # plain value render. A pointer-repr Optional borrow name's truthiness
        # render is the bare `T*`, so the same wrap serves `not p` too.
        return f"(!({_emit_expr(e.operand, state)}))"
    if isinstance(e, THIRUnaryArith):
        # The resolved-dunder tail: expand the operator template
        # (`{self}` = operand) -- neg/pos/invert, checked or bare per the
        # method's own template.
        return expand_cpp_template(e.cpp_template, _emit_expr(e.operand, state))
    if isinstance(e, THIRMembership):
        # The resolved_contains arm: `(recv.contains(needle))`, the
        # negation wrapping the already-parenthesized find expr. A bytes
        # container's `__contains__` is a native FREE function, so it renders
        # `(::tpy::name(recv, needle))` instead.
        if e.ranges_contains:
            # `is_native_in` fallback: a bare `std::ranges::contains(recv,
            # needle)`, negation a `!` prefix (no outer parens).
            call = (f"std::ranges::contains({_emit_expr(e.receiver, state)}, "
                    f"{_emit_expr(e.needle, state)})")
            return f"!{call}" if e.negate else call
        if e.iter_loop:
            # The universal `__iter__`+`__next__` statement-expression loop
            # (no `__contains__`, not native-iterable); fixed temp names,
            # negation a `!` prefix.
            recv = _emit_expr(e.receiver, state)
            needle = _emit_expr(e.needle, state)
            loop = (f"({{ auto&& __itr = ::tpy::__iter__({recv}); "
                    f"bool __found = false; "
                    f"for (;;) {{ auto __r = __itr.__next__(); "
                    f"if (!__r.has_value()) break; "
                    f"if (::tpy::unwrap_ref(*__r) == {needle}) "
                    f"{{ __found = true; break; }} }} "
                    f"__found; }})")
            return f"!{loop}" if e.negate else loop
        if e.free_function:
            inner = (f"({qualify_native_name(e.method_cpp)}"
                     f"({_emit_expr(e.receiver, state)}, "
                     f"{_emit_expr(e.needle, state)}))")
        else:
            inner = (f"({_emit_expr(e.receiver, state)}.{e.method_cpp}"
                     f"({_emit_expr(e.needle, state)}))")
        return f"(!{inner})" if e.negate else inner
    if isinstance(e, THIRStrMembership):
        # The str `.find()` arm: `(s.find(needle) != npos)`, or
        # `== npos` for `not in`. A str-literal receiver wraps in string_view
        # (C string literals lack `.find`).
        recv = _emit_expr(e.receiver, state)
        if e.wrap_receiver_sv:
            recv = f"std::string_view({recv})"
        op = "==" if e.negate else "!="
        needle = _emit_expr(e.needle, state)
        return f"({recv}.find({needle}) {op} std::string::npos)"
    if isinstance(e, THIRTupleMembership):
        # The tuple-literal `in` arm: an OR-chain of `==` compares.
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
        # Value-position and/or: the LHS renders (and hoists) FIRST, so it
        # takes the lower temp numbers; the RHS render sits inside the ternary
        # branch (its EVALUATION is lazy at runtime -- an RHS-nested temp would
        # hoist above the ternary, but no admitted RHS shape carries one).
        lhs_r = _emit_expr(e.lhs, state)
        if e.lhs_temp_cpp is not None:
            lhs_r = state.temps.create(e.lhs_temp_cpp, lhs_r)
        if e.truthy_mode is TruthinessMode.RECORD_LEN:
            truthy = f"(::tpy::__len__({lhs_r}) != 0)"
        elif e.truthy_mode is TruthinessMode.RECORD_BOOL:
            truthy = f"::tpy::__bool__({lhs_r})"
        elif e.truthy_mode is TruthinessMode.NONEMPTY:
            truthy = f"(!{lhs_r}.empty())"
        elif e.truthy_mode is TruthinessMode.ALWAYS_TRUE:
            # The LHS is already evaluated (a bare name or the hoisted
            # temp), so the fold keeps the bare literal -- unlike
            # THIRTruthy's operand-effect wrap.
            truthy = "true"
        else:
            truthy = lhs_r
        # The RHS evaluates lazily inside its branch: deferred temps bank
        # into the region and splice ahead of the operand.
        with state.temps.conditional_region() as _rhs_region:
            rhs_r = _emit_expr(e.rhs, state)
        if e.rhs_sv:
            rhs_r = f"std::string_view({rhs_r})"
        if e.ptr_select_cpp is not None:
            # Rvalue non-value RHS: materialize lazily into the hoisted
            # optional slot; `*ptr` keeps the whole select an lvalue.
            slot = state.temps.declare_named_auto(
                "__logical_slot", f"std::optional<{e.ptr_select_cpp}>")
            lhs_p = f"&({lhs_r})"
            rhs_p = f"({_rhs_region.prefix}{slot}.emplace({rhs_r}), &*{slot})"
            if e.op == "||":
                return f"(*({truthy} ? {lhs_p} : {rhs_p}))"
            return f"(*({truthy} ? {rhs_p} : {lhs_p}))"
        lhs_b = f"{e.lhs_cast}({lhs_r})" if e.lhs_cast else lhs_r
        rhs_b = f"{e.rhs_cast}({rhs_r})" if e.rhs_cast else rhs_r
        if _rhs_region.prefix:
            rhs_b = f"({_rhs_region.prefix}{rhs_b})"
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
        if e.trait_repr:
            # A generic `T | None` slot: the form-neutral runtime reader.
            check = f"::tpy::opt_has_value({inner})"
            return f"({check})" if e.negate else f"(!{check})"
        if e.value_repr:
            # `std::optional<T>` param: `is None` -> `(!p.has_value())`,
            # `is not None` -> `(p.has_value())`.
            return f"({inner}.has_value())" if e.negate else f"(!{inner}.has_value())"
        if e.union_monostate:
            # Union binding: the monostate holds test.
            # A wrapper binding reads the variant through `.value`
            # (VariantAccess.variant_expr's wrapper indirection).
            if e.union_wrapper:
                inner = f"{inner}.value"
            check = f"std::holds_alternative<std::monostate>({inner})"
            return f"(!{check})" if e.negate else f"({check})"
        op = "!=" if e.negate else "=="
        return f"({inner} {op} nullptr)"
    if isinstance(e, THIRTruthy):
        if e.mode is TruthinessMode.ALWAYS_TRUE:
            return f"(static_cast<void>({_emit_expr(e.operand, state)}), true)"
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
        if e.mode is TruthinessMode.PTR_TRUTHY:
            return f"::tpy::ptr_truthy({inner})"
        raise THIRCodeGenError(f"unknown truthiness mode: {e.mode}")
    if isinstance(e, THIROptViewArg):
        # `_maybe_convert_opt_view_param`'s same-TPy-type ARG split: the
        # borrow-form `optional<view>` param -> the owned-storage
        # `optional<owned>` slot. The owned copy spelling keys on the view
        # FAMILY's owned type (`std::string` for str, incl. a `StrView` inner
        # whose family owned_type is still `str`), through
        # `view_to_owned_conv(family.owned_type)`.
        n = escape_cpp_name(e.name)
        fam = view_family_for_type(e.result_type.inner)
        conv = view_to_owned_conv(fam.owned_type)
        split = f"{n} ? std::make_optional({conv}(*{n})) : std::nullopt"
        return f"std::move({split})" if e.moved else split
    if isinstance(e, THIROwnOptRebuild):
        n = escape_cpp_name(e.name)
        return (f"{n} ? std::optional<{e.inner_cpp}>(std::move(*{n}))"
                f" : std::nullopt")
    if isinstance(e, THIRIfExpr):
        # Arm targets and the mixed-arm str wraps were decided at lowering.
        # Each arm evaluates only when chosen, so its deferred temps bank into
        # a per-arm region and splice ahead of the arm render (an empty prefix
        # concatenates as a no-op).
        cond_cpp = _emit_expr(e.cond, state)
        with state.temps.conditional_region() as _then_region:
            then_cpp = _emit_expr(e.then, state)
        with state.temps.conditional_region() as _else_region:
            else_cpp = _emit_expr(e.orelse, state)
        return (f"(({cond_cpp}) ? "
                f"({_then_region.prefix}{then_cpp}) : "
                f"({_else_region.prefix}{else_cpp}))")
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
        # here; args render left-to-right, so temps are created in argument
        # order. The pending decl flushes before the statement line.
        init_cpp = _emit_expr(e.init, state)
        cpp_type = e.cpp_type if e.cpp_type is not None else "auto"
        name = state.temps.create(cpp_type, init_cpp, brace_init=e.brace_init,
                                  movable=bool(e.movable))
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
    if isinstance(e, THIRDecayCopy):
        return f"auto({_emit_expr(e.value, state)})"
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
        # std::tuple constructors in C++23).
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
        vwraps = e.elem_wraps or (None,) * len(e.elements)
        elems = ", ".join(
            w.format(_emit_expr(x, state)) if w is not None
            else f"&({_emit_expr(x, state)})" if lift
            else _emit_expr(x, state)
            for x, lift, w in zip(e.elements, e.addr_of, vwraps))
        src = (f"{e.src_cpp}({elems})" if len(e.elements) == 1
               else f"{e.src_cpp}{{{elems}}}")
        return f"::tpy::tuple_value_to_borrow<{e.dst_cpp}>({src})"
    if isinstance(e, THIRRecordCopy):
        # `copy(x)` of a record: the explicit copy-ctor call `T(x)`.
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
        # The call renders first, THEN the counter draws, so nested unwraps
        # in arguments number lower than their host.
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
    """An `else_body` of a single THIRIf is a flattenable elif (vs a nested
    `else: if`) when their source columns match."""
    if outer.loc is None and inner.loc is None:
        return True
    if outer.loc is None or inner.loc is None:
        return False
    return inner.loc.column == outer.loc.column


def _emit_if(out: TextIO, stmt: THIRIf, indent_level: int, state: _EmitState) -> None:
    # The outer `// if ...:` comment is emitted by the caller (_emit_stmts).
    # Flatten the elif chain into `} else if (...)`.
    indent = INDENT * indent_level
    body_indent = INDENT * (indent_level + 1)
    # Hoisted predecls precede the whole chain (`_emit_branch_decls`). A
    # hoist_slots entry allocates that name's rebind slot immediately before
    # its predecl
    # line (the rvalue-reassigned arm's `std::optional<T> __slot_N;`).
    slot_types = dict(stmt.hoist_slots)
    for name, cpp_type in stmt.hoist_decls:
        if name in slot_types:
            slot = (state.assert_local_slot() or state.next_slot())
            _declare_rebind_slot(state, name, slot, slot_types[name])
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
    # -- the probe-then-nest arm, which renders the condition once so the
    # `__tmp_N` names it registers are the ones emitted.
    extra_closes: list[str] = []
    for i, node in enumerate(chain):
        # Per-node keyword choice: a protocol-isinstance condition compiles
        # `if constexpr`.
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
    # The loop-entry bracketing shared by every loop shape: the else label
    # drawn from iter_counter FIRST (before the loop draws its own index),
    # one empty loop-break slot per loop, and a zeroed switch depth (a switch
    # OUTSIDE the loop must not reroute a break INSIDE it). Returns the saved
    # depth for _pop_loop_frame.
    label = ""
    if has_else:
        label = f"__after_else_{state.iter_counter.draw()}"
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
    # `__loop_break_N:;` label. The else body emits AFTER the loop frames
    # pop, so a break inside it targets the enclosing loop.
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
    # A nested def emits as a lambda: header spelled at lowering (capture
    # list from sema's node facts, resolver param/return spellings), body one
    # level deeper. Name counters continue across the lambda
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
             state.in_except_tier, dict(state.inline_rvalue_slots))
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
    # A hoist line this body produces must be drained INSIDE the lambda: the
    # enclosing body's prologue is outside this capture list, so a declaration
    # written there is unreachable from the lambda (`nested_hoist_scope` +
    # buffered body). Every `hoist_lines` producer is covered, not just the
    # rebind-slot one lowering knows about.
    saved_hoists = state.hoist_lines
    state.hoist_lines = []
    # ... and must not leak through an active sgen hoist_sink either: a
    # rebind slot inside THIS lambda drains at THIS prologue, not the
    # enclosing generator lambda's.
    saved_sink = state.hoist_sink
    state.hoist_sink = None
    body_buf = io.StringIO()
    try:
        # No trailing-comment emission for a lambda body, so a comment after
        # its last statement stays OUTSIDE the closing brace.
        _emit_stmts(body_buf, stmt.body, indent_level + 1, state)
        if state.hoist_lines:
            _witness("stmt.nested_def_hoist")
        for content in state.hoist_lines:
            out.write(f"{INDENT * (indent_level + 1)}{content}\n")
    finally:
        state.hoist_lines = saved_hoists
        state.hoist_sink = saved_sink
        (state.finally_frames, state.return_cpp, state.loop_depth,
         state.switch_depth, state.loop_break_labels,
         state.loop_else_labels, state.rebind_slots,
         state.union_slot_locals, state.error_return_cpp,
         state.try_except_label, state.try_except_err_opt,
         state.in_except_tier, state.inline_rvalue_slots) = saved
    out.write(body_buf.getvalue())
    out.write(f"{indent}}};\n")


def _emit_while(out: TextIO, stmt: THIRWhile, indent_level: int, state: _EmitState) -> None:
    # The `// while ...:` comment is emitted by the caller (_emit_stmts).
    indent = INDENT * indent_level
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    # The restructured head: anonymous cond temps re-evaluate
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
    # Grab the loop index BEFORE the body so nested loops number after this
    # one. Non-literal bounds are
    # captured once into `__start_N`/`__stop_N` temps -- Python's range() reads
    # its args at call time, but the C++ condition re-reads each iteration.
    indent = INDENT * indent_level
    for name, cpp_type in stmt.hoist_decls:
        out.write(f"{indent}{cpp_type} {name};\n")
    saved_depth = _push_loop_frame(state, has_else=bool(stmt.orelse))
    n = state.next_loop_index()
    cpp_elem = stmt.elem_type.to_cpp()
    var = escape_cpp_name(stmt.var)
    # Keep target writes and post-loop values independent of induction.
    separate_target = stmt.hoist_loop_var or stmt.target_written
    counter = f"__range_{n}" if separate_target else var
    start_cpp = "0" if stmt.start is None else _emit_expr(stmt.start, state)
    stop_cpp = _emit_expr(stmt.stop, state)
    if stmt.start is not None and not stmt.start_is_literal:
        out.write(f"{indent}{cpp_elem} __start_{n} = {start_cpp};\n")
        start_cpp = f"__start_{n}"
    if not stmt.stop_is_literal:
        out.write(f"{indent}{cpp_elem} __stop_{n} = {stop_cpp};\n")
        stop_cpp = f"__stop_{n}"
    # The step arms. The unit steps are the plain ascending / descending
    # loop; the non-unit literal / variable steps add an upfront
    # range_check_overflow, which is fixed-int only -- a BigInt counter
    # cannot overflow, so its arms skip the check -- and, for a variable
    # step, a `__step_N` capture with a nonzero check and a ternary
    # direction condition.
    if stmt.step_kind == "plus_one":
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"{counter} < {stop_cpp}; ++{counter}) {{\n")
    elif stmt.step_kind == "unit_neg":
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"{counter} > {stop_cpp}; --{counter}) {{\n")
    elif stmt.step_kind in ("literal_pos", "literal_neg"):
        step_cpp = _emit_expr(stmt.step, state)
        if is_big_int_type(stmt.elem_type):
            # A BigInt counter's literal step captures into a `__step_N`
            # temp and skips the overflow check (fixed-int only).
            out.write(f"{indent}{cpp_elem} __step_{n} = {step_cpp};\n")
            step_cpp = f"__step_{n}"
        else:
            out.write(f"{indent}::tpy::range_check_overflow<{cpp_elem}>("
                      f"{start_cpp}, {stop_cpp}, {step_cpp});\n")
        cmp = "<" if stmt.step_kind == "literal_pos" else ">"
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"{counter} {cmp} {stop_cpp}; {counter} += {step_cpp}) {{\n")
    else:  # variable
        step_cpp = _emit_expr(stmt.step, state)
        out.write(f"{indent}{cpp_elem} __step_{n} = {step_cpp};\n")
        out.write(f"{indent}::tpy::range_check_step_nonzero(__step_{n});\n")
        if not is_big_int_type(stmt.elem_type):
            # An arbitrary-precision counter cannot overflow, so the check
            # is fixed-int only -- same split as the literal-step arms.
            out.write(f"{indent}::tpy::range_check_overflow<{cpp_elem}>("
                      f"{start_cpp}, {stop_cpp}, __step_{n});\n")
        out.write(f"{indent}for ({cpp_elem} {counter} = {start_cpp}; "
                  f"__step_{n} > 0 ? {counter} < {stop_cpp} : {counter} > {stop_cpp}; "
                  f"{counter} += __step_{n}) {{\n")
    if stmt.hoist_loop_var:
        out.write(f"{INDENT * (indent_level + 1)}{var} = {counter};\n")
    elif stmt.target_written:
        out.write(f"{INDENT * (indent_level + 1)}{cpp_elem} {var} = {counter};\n")
    state.loop_depth += 1
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.loop_depth -= 1
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")
    _pop_loop_frame(out, indent, state, saved_depth, stmt.orelse, indent_level)


def _emit_for_each(out: TextIO, stmt: THIRForEach, indent_level: int,
                   state: _EmitState) -> None:
    # The begin/end loop over an element off an lvalue name container: grab the
    # loop index before the body (nested loops number after this one), capture the
    # container -- `auto&` for an lvalue, owning `auto` for an rvalue (a
    # str-returning or Own-container-returning call: the temporary must outlive
    # the loop) -- then the loop-var binding via the
    # shared loop_var_binding (a scalar is a typed copy; a record is a borrow
    # alias -- auto&& / const auto&, so the const flag is threaded through,
    # not hardcoded).
    indent = INDENT * indent_level
    for name, cpp_type in stmt.hoist_decls:
        init = " = nullptr" if name in stmt.hoist_ptr_inits else ""
        out.write(f"{indent}{cpp_type} {name}{init};\n")
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
                              consuming=stmt.consuming,
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
    # The direct-`__next__` loop (the universal ::tpy::__iter__ default): the
    # iterable renders BEFORE the brace scope opens, so its arg temps flush
    # inside the scope, the source captures `auto&` (lvalue) /
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
    # scope: the scope bump changes only the local `indent` string, never the
    # statement indent LEVEL the body and its trailing comments follow. The
    # prelude/close lines above follow the bumped string.
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
    # body walks the OUTER frames only; the stack is restored on exit.
    # Returns True when a frame
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
    # return). The temp draws from the per-function iter_counter; the chain
    # buffers first so its own counter bumps land between the temp's
    # allocation and the decl's write. `value_cpp` is the already-rendered
    # (and temp-flushed) return value, None for a bare `return;` -- callers
    # render it first, so its own counter draws precede the temp's.
    _witness_chain("return", state, 0)
    if value_cpp is None:
        if _emit_finally_chain(out, indent, state):
            _witness("try.chain_terminated")
        else:
            out.write(f"{indent}return;\n")
        return
    tmp = f"__tpy_ret_{state.iter_counter.draw()}"
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


def _deferred_return_triple(stmt: THIRFinallyDeferredReturn,
                            state: _EmitState) -> 'tuple[str, str, str]':
    """(pointer name, capture RHS, materialize expression) for a
    finally-deferred return, shared by the sync statement emit and the
    resumable frame's leaf seam.

    The local's own render comes first and the pointer name second, so their
    counter draws stay in that order; both happen before the finally chain
    renders, which is where the chain's own draws belong."""
    assert stmt.capture is not None
    base = _emit_expr(stmt.capture, state)
    ptr = f"__tpy_retp_{state.iter_counter.draw()}"
    if stmt.optional_move:
        return ptr, base, f"::tpy::ptr_to_optional_move({ptr})"
    lvalue = f"(*{base})" if stmt.indirect else base
    return ptr, f"&({lvalue})", f"std::move(*{ptr})"


def _er_check_inline(tmp: str, state: _EmitState) -> str:
    # The one-line has_value check of the expression-level unwrap, in its
    # three dispositions. The propagate arm deliberately does NOT walk finally
    # frames -- the expression-level unwrap returns directly, unlike the
    # statement-level check below. Preserved, not endorsed.
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
    # The statement-block check line(s): the goto disposition (in a
    # return-tier try), the propagate disposition (in an @error_return body;
    # finally-aware -- active finally bodies run before the unexpected value
    # returns), or the top-level panic.
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
    # Only frames pushed inside the innermost active loop body run (the first
    # index whose loop_depth >= the live loop count -- the stack is monotone
    # non-decreasing in loop_depth). A terminating finally suppresses the tail
    # -- control already left through it. A break out of an else-loop jumps
    # its `__after_else_N` label, which also escapes any intervening match
    # switch, so it precedes the switch reroute. The break tail otherwise
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
            state.loop_break_labels[-1] = (
                f"__loop_break_{state.iter_counter.draw()}")
        _witness("match.loop_break_goto")
        out.write(f"{indent}goto {state.loop_break_labels[-1]};\n")
        return
    out.write(f"{indent}break;\n" if is_break else f"{indent}continue;\n")


def _emit_with(out: TextIO, stmt: THIRWith, indent_level: int,
               state: _EmitState) -> None:
    # See THIRWith for the shape: per-item header lines, then one try/catch
    # layer per manager, closed innermost-first so the innermost __exit__ runs
    # first. The header flush is a no-op in the slice -- temp-registering
    # manager expressions are gate-rejected. Hoisted predecls render first.
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
        if item.frame_ctx is not None:
            # Resumable leaf owned manager with a frame home: the target's
            # frame field aliases `__enter__()`'s result, so the manager
            # lives in the skeleton-declared `__with_ctx_<K>` field and
            # `__ctx_N` binds through it (the frame_ctx arm).
            out.write(f"{indent}__with_ctx_{item.frame_ctx}"
                      f".emplace({ctx_cpp});\n")
            out.write(f"{indent}auto& __ctx_{n} = "
                      f"(*__with_ctx_{item.frame_ctx});\n")
        elif item.manager_hoist_cpp is not None:
            # Kept owned manager (`_with_manager_needs_hoist`): the target's
            # slot aliases `__enter__()`'s result past the block, so the
            # manager lives in a function-scope optional and `__ctx_N` binds
            # through it. Slot allocated AFTER the manager expr renders, so
            # the expression's own counter draws come first. At module-init
            # scope the slot spells `static std::optional<T>
            # __global_slot_N;` (slot_static / slot_prefix), so no
            # assert_local_slot -- the hoist line carries the scope's own
            # lifetime, like the pointer-slot global arms.
            assert state.hoist_drainable, (
                "a with manager-hoist slot in a leaf emitter with no "
                "function-top drain (lowering should have rejected this body)")
            slot = f"{state.slot_prefix}_{state.next_slot()}"
            state.hoist_lines.append(
                f"{state.slot_static}std::optional<{item.manager_hoist_cpp}> "
                f"{slot};")
            out.write(f"{indent}{slot}.emplace({ctx_cpp});\n")
            out.write(f"{indent}auto& __ctx_{n} = (*{slot});\n")
        else:
            out.write(f"{indent}{ctx_bind} __ctx_{n} = {ctx_cpp};\n")
        # The as-target spells the RAW source name while later reads escape
        # it -- preserved, not fixed.
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
        elif item.target_arm is WithTargetArm.FRAME_SLOT:
            out.write(f"{indent}{item.target}"
                      f".emplace(__ctx_{n}.__enter__());\n")
        elif item.target_arm is WithTargetArm.FRAME_FIELD:
            out.write(f"{indent}{item.target} = __ctx_{n}.__enter__();\n")
        else:
            out.write(f"{indent}__ctx_{n}.__enter__();\n")
    # Per-layer terminates: the innermost layer carries body_terminates; once
    # an inner layer may suppress, every layer outside it can fall through.
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
        # Popped before the catch arms (the catches are fixed strings;
        # nothing walks the stack).
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
    # The unified try/finally shape: the finally frame sits on the
    # stack while the body emits; the catch-path and normal-path copies emit
    # with the frame popped, so nested exits redirect through OUTER frames
    # only. `stmt.body_terminates` is the terminates fact of whatever the
    # frame wraps (see THIRTry) and elides the normal-path copy. The body
    # emits into a buffer first: an exit site inside it decides whether the
    # frame's guard is needed, which has to be declared before the `try {`.
    inner = INDENT * inner_level
    ast_fctx = None
    if state.ast_finally_push is not None:
        # Leaf mode: mirror this frame onto the skeleton's finally stack so
        # the resumable return hook's chain walk inlines the finally with the
        # SAME guard -- that push allocates the guard from the shared counter
        # and we reuse its name.
        def _fin_writer(w, ind, _stmts=stmt.finally_body):
            _emit_stmts(w, _stmts, len(ind) // len(INDENT), state)
        ast_fctx = state.ast_finally_push(_fin_writer,
                                          stmt.finally_terminates)
        guard_name = ast_fctx.guard_name
    else:
        guard_name = f"__fin_ran_{state.finally_guard_counter.next()}"
    fr = _FinallyFrame(
        loop_depth=state.loop_depth,
        stmts=stmt.finally_body,
        terminates=stmt.finally_terminates,
        guard_name=guard_name)
    state.finally_frames.append(fr)
    body_buf = io.StringIO()
    try:
        emit_body(body_buf, inner_level + 1)
    finally:
        if ast_fctx is not None:
            state.ast_finally_pop()
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
    # The throw-tier try: the C++ try, one catch arm per handler (headers
    # pre-rendered at lowering; the catch parameter IS the as-binding), else
    # jumping past via the goto label drawn from the module-cumulative
    # try_except_counter sink. Handlers close with `}` and the next header
    # appends ` catch ... {` on the same line, the final `}` taking the
    # newline.
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
    # The return tier (see THIRTry): the counter draws first, the optional
    # `__err_opt_N` capture decl, then the goto-dispatch body -- wrapped in
    # the finally frame when a finally is present. The label is live only
    # while the TRY body emits (restored before the else body), while err_opt
    # stays set until the whole statement closes (the bare-raise re-raise in
    # the handler reads it).
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
    # Dispatch over the routed tiers (see THIRTry). The hoisted predecls
    # render first.
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
    # The scalar tiers (see THIRMatch): the hoisted predecls, the numbered
    # subject binding, then the tier body. ONE counter draw per match
    # (subject + inner names off a single bump; the inner name is an
    # Optional-tier concern); the guarded tiers' second draw is
    # gate-rejected.
    indent = INDENT * indent_level
    # A hoist_slots entry allocates that name's rebind slot immediately
    # before its predecl line (THIRIf's rvalue-reassigned arm).
    slot_types = dict(stmt.hoist_slots)
    for name, cpp_type in stmt.hoist_decls:
        if name in slot_types:
            slot = (state.assert_local_slot() or state.next_slot())
            _declare_rebind_slot(state, name, slot, slot_types[name])
        out.write(f"{indent}{cpp_type} {name};\n")
    state.match_counter += 1
    subject = f"__match_subject_{state.match_counter}"
    binding = "auto&" if stmt.subject_ref else "auto"
    subject_cpp = _emit_expr(stmt.subject, state)
    # Subject arg temps flush before the bind line; admitted subjects rarely
    # carry any, but a call-rooted rvalue subject can.
    state.temps.flush(out, indent)
    out.write(f"{indent}{binding} {subject} = {subject_cpp};\n")
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
                        subject: str, inner: str,
                        bases: 'dict[str, str] | None' = None) -> None:
    # The value-subject binding arms, mode folded at lowering (see
    # THIRMatchBinding); the arm block's first line, before the body. A
    # field capture composes the `.field` accessor onto the base spelling.
    # Nested rows: `base_name` swaps the base for a previously-bound name
    # (recorded in `bases`); mode 'field_alias' draws a
    # `__field_{parent}_{field}` temp, its name derived from the runtime base
    # spelling.
    if binding is None:
        return
    base = subject
    if binding.base_name is not None:
        assert bases is not None and binding.base_name in bases, \
            "match binding references an unbound base name"
        base = bases[binding.base_name]
    rhs = f"{binding.subject_prefix}{base}{binding.subject_suffix}"
    if binding.mode == "field_alias":
        parent = f"{base}{binding.alias_path}"
        parent_sfx = parent.replace(".", "_").replace("*", "").lstrip("_")
        temp = f"__field_{parent_sfx}_{binding.name}"
        out.write(f"{inner}auto& {temp} = {rhs};\n")
        if bases is not None:
            bases[binding.name] = temp
        return
    name = escape_cpp_name(binding.name)
    if bases is not None:
        bases[binding.name] = name
    if binding.mode == "assign":
        out.write(f"{inner}{name} = {rhs};\n")
    elif binding.mode == "assign_addr":
        out.write(f"{inner}{name} = &({rhs});\n")
    elif binding.mode == "assign_move":
        out.write(f"{inner}{name} = std::move({rhs});\n")
    elif binding.mode == "frame_emplace":
        # Resumable dispatch-hook capture into a frame_slot local: the
        # slot's emplace copy, a frame-resident bind.
        out.write(f"{inner}{name}.emplace({rhs});\n")
    elif binding.mode == "copy":
        out.write(f"{inner}auto {name} = {rhs};\n")
    else:
        out.write(f"{inner}auto& {name} = {rhs};\n")


def _emit_match_whole_bindings(out: TextIO, entry: THIRMatchArmEntry,
                               subject: str, inner: str,
                               case_var: 'str | None' = None) -> None:
    # The arm's whole-subject binding lines, in source order: the `as`
    # target last (`case x as y:` renders `x = ...;` then `y = ...;`). Every
    # tier binds through here, including the tiers that still reject the
    # two-binding arm, so admitting `case x as y:` on one of them cannot
    # silently drop the first name.
    #
    # `case_var` is the rhs a `from_case_var` binding reads -- the tier's
    # extracted alias (`__case_i`, the Optional deref). A tier with no such
    # alias passes none and every binding reads the subject.
    for b in (*entry.pre_bindings,
              *((entry.binding,) if entry.binding is not None else ())):
        rhs = (case_var if case_var is not None and b.from_case_var
               else subject)
        _emit_match_binding(out, b, rhs, inner)


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
        state.comments.case_(out, arm.entries[0].loc, indent)
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
            _emit_match_whole_bindings(out, entry, subject, inner)
            _emit_match_arm_body(out, entry, indent_level + 1, state)
        else:
            emitted: set[str] = set()
            for entry in arm.entries:
                for b in (*entry.pre_bindings,
                          *((entry.binding,) if entry.binding is not None
                            else ())):
                    if b.name in emitted:
                        continue
                    _emit_match_binding(out, b, subject, inner)
                    emitted.add(b.name)
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
    # The union switch tier: `switch (subject.index())`, arms in SOURCE
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
        state.comments.case_(out, entry.loc, indent)
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
        bases: dict[str, str] = {}
        for fb in entry.field_bindings:
            # Keyword captures always draw the alias, so the base is it.
            _emit_match_binding(out, fb, entry.case_alias, inner, bases)
        _emit_match_whole_bindings(out, entry, subject, inner,
                                   entry.case_alias or get)
        _emit_match_arm_body(out, entry, indent_level + 1, state)
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")
    state.switch_depth -= 1
    out.write(f"{indent}}}\n")


def _emit_match_guarded_union(out: TextIO, stmt: THIRMatch,
                              indent_level: int, state: _EmitState,
                              subject: str) -> None:
    # The guarded union tier: the end label draws the second per-function
    # counter bump BEFORE the switch;
    # per index group the case label, the once-per-block `__case_{idx}`
    # extraction (when any class entry drew it), then each entry -- its
    # source comment at INNER indent (unlike the unguarded tiers' case
    # indent), an extra `{ }` scope when the group has >1 entries (name
    # collisions between arms), the binding (capture/as -- vs the subject
    # for always-match entries, vs the alias for class entries), the guard
    # as `if (guard) { <body> goto end; }` one level deeper, or the
    # unguarded `<body> goto end;` inline; `break;` closes each block. The
    # trailing end label writes UNINDENTED, at column 0.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    inner2 = INDENT * (indent_level + 2)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    # A wrapper subject dispatches through its `.value` variant member
    # (both the switch head and the get positions), like the unguarded tier.
    variant = f"{subject}.value" if stmt.wrapper_value else subject
    out.write(f"{indent}switch ({variant}.index()) {{\n")
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
                          f"{deref}std::get<{arm.labels[0]}>({variant});\n")
        use_scope = len(arm.entries) > 1
        bind_indent = inner2 if use_scope else inner
        for entry in arm.entries:
            state.comments.case_(out, entry.loc, inner)
            if use_scope:
                out.write(f"{inner}{{\n")
            bases: dict[str, str] = {}
            if not entry.field_conds:
                # No field conditions: bindings precede the guard (it may
                # read them). With conditions they move INSIDE the if block
                # below.
                for fb in entry.field_bindings:
                    _emit_match_binding(out, fb, alias, bind_indent, bases)
                _emit_match_whole_bindings(out, entry, subject, bind_indent,
                                           alias)
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
                        _emit_match_binding(out, fb, alias, body_indent,
                                            bases)
                    _emit_match_whole_bindings(out, entry, subject,
                                               body_indent, alias)
                _emit_match_arm_body(out, entry, lvl, state)
                out.write(f"{INDENT * lvl}goto {end_label};\n")
                out.write(f"{bind_indent}}}\n")
            else:
                lvl = indent_level + (2 if use_scope else 1)
                _emit_match_arm_body(out, entry, lvl, state)
                out.write(f"{INDENT * lvl}goto {end_label};\n")
            if use_scope:
                out.write(f"{inner}}}\n")
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")
    state.switch_depth -= 1
    out.write(f"{indent}}}\n")
    out.write(f"{end_label}:;\n")


def _emit_poly_whole_binding(out: TextIO, entry: THIRMatchArmEntry,
                             subject: str, at: str) -> None:
    # The whole-subject capture/`as` bindings: vs the `__case_i` alias for a
    # class arm (`from_case_var`), vs the subject otherwise.
    _emit_match_whole_bindings(out, entry, subject, at, entry.case_alias)


def _emit_match_poly_if_elif(out: TextIO, stmt: THIRMatch,
                             indent_level: int, state: _EmitState,
                             subject: str) -> None:
    # The polymorphic chain: per class arm the C++17 if-init cast
    # (poly_cast composed around the subject), the `__case_i` ref line,
    # field bindings + the `as` binding against the alias, the body one
    # level in; or-arms the ||-joined null tests; the always-match arm the
    # chain's `{` / `} else {`. One trailing `}` closes the chain (also for
    # a non-exhaustive chain with no else arm).
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.case_(out, entry.loc, indent)
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
    # The guarded polymorphic chain: the end label draws the second counter
    # bump; class arms are standalone
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
        state.comments.case_(out, entry.loc, indent)
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
    # The optimized-Optional tier over the pointer-repr subject slice (see
    # THIRMatch.none_entry): the None arm's comment at the OUTER indent, then
    # `if (subj == nullptr) { <none body> } else {` (or the bare
    # `if (subj != nullptr) {` when no None arm exists), the
    # `__match_inner_N` deref alias, and the single always-match inner block
    # -- `_emit_optional_inner_record`'s no-field `{` ... `}` (comment at the
    # else level, binding vs the alias, body two levels in). The inner name
    # must snapshot the subject's counter draw BEFORE the None body emits: a
    # nested match in there bumps the counter, and each match's names are
    # saved and restored around it. No switch, so no switch_depth
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
        _emit_match_arm_body(out, stmt.none_entry, indent_level + 1, state)
        out.write(f"{indent}}} else {{\n")
    else:
        out.write(f"{indent}if ({has_value_cond}) {{\n")
    out.write(f"{inner}auto& {inner_name} = (*{subject});\n")
    if stmt.inner_strategy is None:
        entry = stmt.arms[0].entries[0]
        state.comments.case_(out, entry.loc, inner)
        out.write(f"{inner}{{\n")
        _emit_match_whole_bindings(out, entry, inner_name, inner2)
        _emit_stmts(out, entry.body, indent_level + 2, state)
        out.write(f"{inner}}}\n")
    elif stmt.inner_strategy == "if_elif":
        _emit_match_if_elif(out, stmt, indent_level + 1, state, inner_name,
                            paren_or=False)
    elif stmt.inner_strategy == "if_elif_record":
        # The inner record shape is the record tier's unguarded chain over
        # the deref alias, one level in (guarded/true-alt shapes are
        # gate-rejected), so the record chain emitter is reused verbatim.
        _emit_match_if_elif_record(out, stmt, indent_level + 1, state,
                                   inner_name)
    else:  # switch_enum / switch_primitive over the inner alias
        _emit_match_switch(out, stmt, indent_level + 1, state, inner_name)
    out.write(f"{indent}}}\n")


def _opt_chain_cond(opt_conds, subject: str) -> str:
    # The Optional condition join: per group the (prefix, suffix) pieces
    # composed around the subject and &&-joined; groups ||-joined, or-pattern
    # alternatives parenthesized (the bare null alternative is not).
    parts = []
    for paren, pieces in opt_conds:
        rendered = " && ".join(f"{pre}{subject}{suf}" for pre, suf in pieces)
        parts.append(f"({rendered})" if paren else rendered)
    return " || ".join(parts)


def _emit_match_opt_arm_bindings(out: TextIO, entry: THIRMatchArmEntry,
                                 subject: str, inner: str) -> None:
    # The Optional arm bindings: class-arm field captures
    # against the `(*subj)` deref, then the whole-subject capture/`as`
    # binding -- the deref for a value-side binding (from_case_var), the
    # full Optional for sema's binds_full_optional.
    deref = f"(*{subject})"
    for fb in entry.field_bindings:
        _emit_match_binding(out, fb, deref, inner)
    _emit_match_whole_bindings(out, entry, subject, inner, deref)


def _emit_match_if_elif_optional(out: TextIO, stmt: THIRMatch,
                                 indent_level: int, state: _EmitState,
                                 subject: str) -> None:
    # The Optional chain, unguarded, arms in source order:
    # per arm the comment, `if (cond) {` / `} else if (cond) {` off the
    # pre-rendered opt_conds (the always-match arm is `{` / `} else {`),
    # the bindings, the body one level in; one closing brace ends the chain.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.case_(out, entry.loc, indent)
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
    # The guarded arm tail: inside an opened arm block (bindings already
    # emitted), the guard as `if (guard) { <body> goto end; }` two levels
    # in, or the unguarded body + goto one level; closes the block.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    if entry.guard is not None:
        out.write(f"{inner}if ({_emit_expr(entry.guard, state)}) {{\n")
        _emit_match_arm_body(out, entry, indent_level + 2, state)
        out.write(f"{INDENT * (indent_level + 2)}goto {end_label};\n")
        out.write(f"{inner}}}\n")
    else:
        _emit_match_arm_body(out, entry, indent_level + 1, state)
        out.write(f"{inner}goto {end_label};\n")
    out.write(f"{indent}}}\n")


def _emit_match_if_elif_optional_guarded(out: TextIO, stmt: THIRMatch,
                                         indent_level: int,
                                         state: _EmitState,
                                         subject: str) -> None:
    # The guarded Optional chain's standalone-if + goto shape (the
    # end label draws the second per-function counter bump): each arm opens
    # its own `if (cond) {` (bare `{` for an always-match arm), binds, then
    # the goto tail. The label line closes the match at the arm indent.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.case_(out, entry.loc, indent)
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
    # The str switch tier: the end label draws the second per-function
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
        state.comments.case_(out, entry.loc, indent)
        cond = _opt_chain_cond(entry.opt_conds, subject)
        out.write(f"{indent}if ({cond}) {{\n")
        _emit_match_whole_bindings(out, entry, subject, inner)
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
            state.comments.case_(out, entry.loc, sw_inner)
            cond = _opt_chain_cond(entry.opt_conds, subject)
            out.write(f"{sw_inner}if ({cond}) {{\n")
            _emit_match_whole_bindings(out, entry, subject, sw_deep)
            _emit_match_arm_body(out, entry, sw_level + 2, state)
            out.write(f"{sw_deep}goto {end_label};\n")
            out.write(f"{sw_inner}}}\n")
        out.write(f"{sw_inner}break;\n")
        out.write(f"{sw_indent}}}\n")
    state.switch_depth -= 1
    out.write(f"{sw_indent}}}\n")
    if stmt.str_disc_kind == "char_at":
        out.write(f"{indent}}}\n")
    for entry in stmt.str_trailing:
        state.comments.case_(out, entry.loc, indent)
        out.write(f"{indent}{{\n")
        _emit_match_whole_bindings(out, entry, subject, inner)
        _emit_match_goto_tail(out, entry, indent_level, state, end_label)
    out.write(f"{indent}{end_label}:;\n")


def _emit_match_if_elif(out: TextIO, stmt: THIRMatch, indent_level: int,
                        state: _EmitState, subject: str,
                        paren_or: bool = True) -> None:
    # The scalar chain, unguarded, arms in source order: per arm the
    # comment, then `if (cond) {` / `} else if (cond) {` (the wildcard arm is
    # `{` / `} else {`), the body one level in; one closing brace ends the
    # chain. Conditions compose `{subject} == {rhs}` per pre-rendered
    # alternative, or-patterns ||-joined in parens (a single alternative
    # stays bare). No break, no default,
    # no switch_depth: a chain is not a switch, so `break` inside an arm
    # exits the loop directly like any if body.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    for i, arm in enumerate(stmt.arms):
        entry = arm.entries[0]
        state.comments.case_(out, entry.loc, indent)
        if not arm.labels:
            out.write(f"{indent}{{\n" if i == 0 else f"{indent}}} else {{\n")
        else:
            conds = [f"{subject} == {rhs}" for rhs in arm.labels]
            # The Optional inner chain joins or-alternatives bare; the
            # top-level chain wraps them in parens.
            joined = " || ".join(conds)
            cond = (conds[0] if len(conds) == 1
                    else joined if not paren_or else f"({joined})")
            if entry.guard is not None:
                # A guard on a chain arm folds into its condition: there is no
                # per-arm block to fall out of, so the guard has to gate the
                # arm's own test. Lowering admits this only for a labelled,
                # binding-free arm. An or-group must be parenthesized here
                # even where the bare join is the tier's spelling -- `&&`
                # binds tighter than `||`.
                gated = f"({joined})" if len(conds) > 1 else cond
                cond = f"{gated} && {_emit_expr(entry.guard, state)}"
            keyword = "if" if i == 0 else "} else if"
            out.write(f"{indent}{keyword} ({cond}) {{\n")
        _emit_match_whole_bindings(out, entry, subject, inner)
        _emit_match_arm_body(out, entry, indent_level + 1, state)
    out.write(f"{indent}}}\n")


def _emit_match_if_elif_guarded(out: TextIO, stmt: THIRMatch,
                                indent_level: int, state: _EmitState,
                                subject: str) -> None:
    # The guarded scalar chain's standalone-if + goto shape: the end label
    # draws the SECOND per-function counter bump (the subject took the
    # first); each arm is its own `if (cond) {` (bare `{` for an
    # always-match arm) so a failed guard falls out of the block to the
    # next arm; the guarded body + goto sit two levels in (inside the guard
    # if), the unguarded one level. The label line closes the match.
    indent = INDENT * indent_level
    inner = INDENT * (indent_level + 1)
    state.match_counter += 1
    end_label = f"__match_end_{state.match_counter}"
    for arm in stmt.arms:
        entry = arm.entries[0]
        state.comments.case_(out, entry.loc, indent)
        if not arm.labels:
            out.write(f"{indent}{{\n")
        else:
            conds = [f"{subject} == {rhs}" for rhs in arm.labels]
            cond = conds[0] if len(conds) == 1 else "(" + " || ".join(conds) + ")"
            out.write(f"{indent}if ({cond}) {{\n")
        _emit_match_whole_bindings(out, entry, subject, inner)
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
    # The record chain, unguarded, arms in source order: a
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
        state.comments.case_(out, entry.loc, indent)
        keyword = "if" if i == 0 else "} else if"
        if entry.or_conds is not None:
            if entry.or_conds:
                cond = _record_or_cond(entry.or_conds, subject)
                out.write(f"{indent}{keyword} ({cond}) {{\n")
            else:
                out.write(f"{indent}{{\n" if i == 0
                          else f"{indent}}} else {{\n")
            _emit_match_arm_body(out, entry, indent_level + 1, state)
            continue
        conds = [f"{pre}{subject}{suf}" for pre, suf in entry.field_conds]
        if conds:
            out.write(f"{indent}{keyword} ({' && '.join(conds)}) {{\n")
        else:
            out.write(f"{indent}{{\n" if i == 0
                      else f"{indent}}} else {{\n")
        bases: dict[str, str] = {}
        for fb in entry.field_bindings:
            _emit_match_binding(out, fb, subject, inner, bases)
        _emit_match_whole_bindings(out, entry, subject, inner)
        _emit_match_arm_body(out, entry, indent_level + 1, state)
    out.write(f"{indent}}}\n")


def _emit_match_guarded_record(out: TextIO, stmt: THIRMatch,
                               indent_level: int, state: _EmitState,
                               subject: str) -> None:
    # The guarded record chain's standalone-if + goto shape (the end label
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
        state.comments.case_(out, entry.loc, indent)
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
        bases: dict[str, str] = {}
        for fb in entry.field_bindings:
            _emit_match_binding(out, fb, subject, inner, bases)
        _emit_match_whole_bindings(out, entry, subject, inner)
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
            # `std::optional<T>` rebind slot reused on each reseat. The init
            # slot is allocated before the rebind slot.
            init_slot = (state.assert_local_slot() or state.next_slot())
            rebind_slot = (state.assert_local_slot() or state.next_slot())
            cpp = stmt.cpp_type
            _declare_rebind_slot(state, stmt.name, rebind_slot, cpp)
            const_pfx = "const " if stmt.is_const else ""
            out.write(f"{indent}{cpp} __slot_{init_slot} = {_emit_expr(stmt.init, state)};\n")
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
            # Render before flushing: a container-select init hoists its
            # non-name LHS as an `auto&& __tmp_N` line that must precede
            # the alias decl (the statement's temp flush point).
            init_cpp = _emit_expr(stmt.init, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{const_pfx}{stmt.cpp_type}{sigil} {name} = "
                      f"{init_cpp};\n")
        elif stmt.init is None:
            cpp = stmt.cpp_type if stmt.cpp_type is not None \
                else stmt.resolved_type.to_cpp()
            out.write(f"{indent}{cpp} {name};\n")
        elif stmt.btuple_slot_cpp is not None:
            # Reassigned borrow-tuple decl off an owning call: the rvalue
            # emplaces into a per-target `std::optional<...>` slot the local
            # aliases. Init renders before the slot draws its number (the
            # init's own counter draws come first); the
            # slot registers for sibling reuse, and the btuple_slot_locals
            # membership keeps THIRAssign's rebind-slot reseat off these
            # names (their reseats are plain tuple_to_pointer assigns).
            init_cpp = _emit_expr(stmt.init, state)
            state.temps.flush(out, indent)
            slot = (state.assert_local_slot() or state.next_slot())
            state.rebind_slots[stmt.name] = slot
            state.btuple_slot_locals.add(stmt.name)
            out.write(f"{indent}std::optional<{stmt.btuple_slot_cpp}> "
                      f"__slot_{slot};\n")
            if stmt.btuple_opt_borrow_cpp is not None:
                # The OPTIONAL-borrow-tuple decl: the alias wraps through
                # the optional spelling (cpp_type IS that spelling).
                out.write(
                    f"{indent}{stmt.cpp_type} {name} = {stmt.cpp_type}"
                    f"{{::tpy::tuple_to_pointer<"
                    f"{stmt.btuple_opt_borrow_cpp}>"
                    f"(__slot_{slot}.emplace({init_cpp}))}};\n")
            else:
                out.write(f"{indent}{stmt.cpp_type} {name} = "
                          f"::tpy::tuple_to_pointer<{stmt.cpp_type}>"
                          f"(__slot_{slot}.emplace({init_cpp}));\n")
        else:
            # Render before flushing: the init may register arg temps, whose
            # decls flush between the source comment and the statement line.
            # cpp_type (when set at lowering -- enum decls) overrides the
            # bare to_cpp() spelling.
            cpp_type = stmt.cpp_type if stmt.cpp_type is not None \
                else stmt.resolved_type.to_cpp()
            init_cpp = _emit_expr(stmt.init, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{cpp_type} {name} = {init_cpp};\n")
    elif isinstance(stmt, THIRPtrLocalDecl):
        # Slot-hoist pointer-repr locals. Slot NUMBERING follows a fixed
        # allocation order: the OPT kinds allocate the init slot before the
        # rebind slot; the UNION rvalue kind allocates the value slot before
        # the rebind slot but EMITS the rebind pre-decl line first.
        name = escape_cpp_name(stmt.name)
        # `const T*` only on the pointer line; the rebind `std::optional<T>`
        # slot backing a reseat stays non-const.
        cpfx = "const " if stmt.is_const else ""
        if stmt.kind is PtrSlotKind.OPT_NONE:
            if stmt.needs_rebind_slot:
                slot = (state.assert_local_slot() or state.next_slot())
                _declare_rebind_slot(state, stmt.name, slot, stmt.cpp_type)
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = nullptr;\n")
        elif stmt.kind in (PtrSlotKind.OPT_RVALUE, PtrSlotKind.RECORD_RVALUE):
            # RECORD_RVALUE (the escape-hoist plain-record flavor) shares the
            # render exactly: `T __slot_N = init;` + `T* name = &__slot_N;`
            # (its needs_rebind_slot is always False -- an rvalue-reassigned
            # record is the REBIND_SLOT binding, not this kind).
            init_cpp = _emit_expr(stmt.init, state)
            # The decl is a flush position: an init's arg temps print before
            # the slot line (the statement-level drain).
            state.temps.flush(out, indent)
            init_slot = (state.assert_local_slot() or state.next_slot())
            out.write(f"{indent}{stmt.cpp_type} __slot_{init_slot} = "
                      f"{init_cpp};\n")
            if stmt.needs_rebind_slot:
                rebind = (state.assert_local_slot() or state.next_slot())
                _declare_rebind_slot(state, stmt.name, rebind, stmt.cpp_type)
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = "
                      f"&__slot_{init_slot};\n")
        elif stmt.kind is PtrSlotKind.OPT_PROTO_RVALUE:
            # Optional[@dynamic P] local from a conformer rvalue: the slot
            # spells the rvalue's class (val_cpp; `auto` for a structural
            # conformer) and the pointer line deduces.
            init_cpp = _emit_expr(stmt.init, state)
            state.temps.flush(out, indent)
            init_slot = (state.assert_local_slot() or state.next_slot())
            out.write(f"{indent}{stmt.val_cpp} __slot_{init_slot} = "
                      f"{init_cpp};\n")
            out.write(f"{indent}{cpfx}auto* {name} = &__slot_{init_slot};\n")
        elif stmt.kind is PtrSlotKind.GLOBAL_RVALUE:
            # Module-init global init: the name is pre-declared at namespace
            # scope, so only the `static` slot carries a type.
            init_cpp = _emit_expr(stmt.init, state)
            state.temps.flush(out, indent)
            slot = state.global_slot()
            _gs_static = "" if stmt.branch_scope else state.slot_static
            out.write(f"{indent}{_gs_static}{stmt.cpp_type} {slot} = "
                      f"{init_cpp};\n")
            out.write(f"{indent}{name} = &{slot};\n")
            # A later rvalue write reuses this slot, so it registers in
            # `rebind_slots` here; the slot is plain, not an
            # optional, so the reseat takes `&(slot = ...)`.
            state.rebind_slots[stmt.name] = state.slot_counter
            state.global_slot_locals.add(stmt.name)
            _witness("top_level.global_slot")
        elif stmt.kind is PtrSlotKind.RECORD_HOISTED:
            # Hoisted record pointer-local: the `std::optional<T>` slot
            # pre-decl rides the function-top hoist lines; the decl statement
            # re-emplaces per execution and re-points the alias
            # (`T* x = &*(__slot_N = init);`).
            init_cpp = _emit_expr(stmt.init, state)
            # No assert_local_slot: the hoist line spells the scope's own
            # prefix + static, so the module-scope flavor is lifetime-safe.
            init_slot = state.next_slot()
            assert state.hoist_drainable, (
                "RECORD_HOISTED decl hoist reached a non-draining leaf emitter")
            state.hoist_lines.append(
                f"{state.slot_static}std::optional<{stmt.cpp_type}> "
                f"{state.slot_prefix}_{init_slot};")
            if stmt.needs_rebind_slot:
                rebind = (state.assert_local_slot() or state.next_slot())
                _declare_rebind_slot(state, stmt.name, rebind, stmt.cpp_type)
            # Deliberately NO rebind_slots registration without a rebind
            # slot: the THIRAssign rebind-slot emit special-case is keyed on
            # membership alone, so registering the init slot would hijack a
            # later field / pointer-copy reseat into `&*(__slot = <T*>)` --
            # uncompilable. An rvalue reseat implies rvalue_reassigned,
            # which implies needs_rebind_slot -- no valid consumer exists.
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = "
                      f"&*({state.slot_prefix}_{init_slot} = {init_cpp});\n")
        elif stmt.kind is PtrSlotKind.UNION_RVALUE:
            init_cpp = _emit_expr(stmt.init, state)
            # Flush position, like the plain-decl arms: a member-ctor init's
            # value-union arg temps print before the `__slot_N` line
            # (`std::variant<...> __tmp_1 = "world";` then the slot).
            state.temps.flush(out, indent)
            slot = (state.assert_local_slot() or state.next_slot())
            if stmt.needs_rebind_slot:
                rebind = (state.assert_local_slot() or state.next_slot())
                _declare_rebind_slot(state, stmt.name, rebind, stmt.val_cpp)
                state.union_slot_locals.add(stmt.name)
            out.write(f"{indent}{stmt.val_cpp} __slot_{slot} = {init_cpp};\n")
            out.write(f"{indent}{stmt.cpp_type} {name} = "
                      f"::tpy::to_ptr_variant(__slot_{slot});\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL:
            # @dynamic protocol local: a concrete/adapter slot brace-inited from
            # the init, aliased by a protocol Base* pointer (the non-erased
            # arm). Draw the slot BEFORE emitting the init, so the numbering
            # stays aligned even if an init ever consumes a slot of its own.
            init_slot = (state.assert_local_slot() or state.next_slot())
            init_cpp = _emit_expr(stmt.init, state)
            out.write(f"{indent}{stmt.cpp_type} __slot_{init_slot}"
                      f"{{{init_cpp}}};\n")
            out.write(f"{indent}{stmt.base_cpp}* {name} = "
                      f"&__slot_{init_slot};\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL_ERASED:
            # `p2: P = p1` -- alias the same erased object (no slot). The deref'd
            # source already renders its own `(*p1)` parens, so this is a
            # bare `&{expr}`.
            out.write(f"{indent}{stmt.base_cpp}* {name} = "
                      f"&{_emit_expr(stmt.init, state)};\n")
        elif stmt.kind is PtrSlotKind.OPT_STORAGE_CALL:
            # Own-declared optional-returning call init: the storage
            # optional materializes in a slot, the binding lifts the
            # pointer (`std::optional<T> __slot_N = make_some();`
            # `T* s = ::tpy::optional_to_ptr(__slot_N);`). The slot is
            # registered in `rebind_slots` here at the decl site for reseat
            # reuse -- the OPT_STORAGE_CALL rebind arm
            # is its only consumer (lowering rejects other reseat shapes
            # for such names, so the THIRAssign special-case cannot see
            # them).
            init_slot = (state.assert_local_slot() or state.next_slot())
            init_cpp = _emit_expr(stmt.init, state)
            out.write(f"{indent}std::optional<{stmt.cpp_type}> "
                      f"__slot_{init_slot} = {init_cpp};\n")
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = "
                      f"::tpy::optional_to_ptr(__slot_{init_slot});\n")
            state.rebind_slots[stmt.name] = init_slot
        elif stmt.kind is PtrSlotKind.PTR_ADDR:
            # Address-of an existing lvalue -- the decl itself takes no slot
            # (the decl twin of the PTR_ADDR reseat). A rebind slot is drawn
            # ahead of the pointer line, as an lvalue init's slot
            # pre-declaration always is.
            init_cpp = _emit_expr(stmt.init, state)
            if stmt.needs_rebind_slot:
                rebind = (state.assert_local_slot() or state.next_slot())
                _declare_rebind_slot(state, stmt.name, rebind, stmt.cpp_type)
            out.write(f"{indent}{cpfx}{stmt.cpp_type}* {name} = "
                      f"&({init_cpp});\n")
        else:  # PtrSlotKind.UNION_ADDR
            init_cpp = _emit_expr(stmt.init, state)
            if stmt.needs_rebind_slot:
                rebind = (state.assert_local_slot() or state.next_slot())
                _declare_rebind_slot(state, stmt.name, rebind, stmt.val_cpp)
                state.union_slot_locals.add(stmt.name)
            out.write(f"{indent}{stmt.cpp_type} {name}{{&({init_cpp})}};\n")
    elif isinstance(stmt, THIRPtrLocalRebind):
        name = escape_cpp_name(stmt.name)
        if stmt.kind is PtrSlotKind.OPT_NONE:
            out.write(f"{indent}{name} = nullptr;\n")
        elif stmt.kind is PtrSlotKind.FRAME_STORAGE_CALL:
            # The Own-declared optional call's frame-field fill + re-lift
            # (`__ptr_slot_fN = make_opt(3);` then
            # `got = ::tpy::optional_to_ptr(__ptr_slot_fN);`).
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{stmt.val_cpp} = {value_cpp};\n")
            out.write(f"{indent}{name} = "
                      f"::tpy::optional_to_ptr({stmt.val_cpp});\n")
        elif stmt.kind is PtrSlotKind.FRAME_RVALUE:
            # Resumable frame-field slot reseat: emplace-assign the field
            # and re-point the pointer in one expression
            # (`saved = &*(__ptr_slot_fN = Point(9));`, over the prescanned
            # frame field).
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{name} = "
                      f"&*({stmt.val_cpp} = {value_cpp});\n")
        elif stmt.kind is PtrSlotKind.OPT_FIELD_RVALUE:
            # Storage-form Optional FIELD off an rvalue receiver: write the
            # decl-site rebind slot INLINE and lift the pointer off the
            # assignment result, so the whole optional outlives the
            # receiver temporary.
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            slot = _use_rebind_slot(state, stmt.name)
            assert slot is not None, (
                "OPT_FIELD_RVALUE reseat without its decl-site rebind slot")
            out.write(f"{indent}{name} = ::tpy::optional_to_ptr("
                      f"{state.slot_prefix}_{slot} = {value_cpp});\n")
        elif stmt.kind is PtrSlotKind.OPT_STORAGE_CALL:
            # Reseat of an OPT_STORAGE_CALL-declared name: re-fill the slot
            # registered at the decl, re-lift the pointer (`__slot_1 =
            # make(43);` `z = ::tpy::optional_to_ptr(__slot_1);`).
            slot = state.rebind_slots.get(stmt.name)
            assert slot is not None, (
                "OPT_STORAGE_CALL reseat without its decl-registered slot")
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}__slot_{slot} = {value_cpp};\n")
            out.write(f"{indent}{name} = "
                      f"::tpy::optional_to_ptr(__slot_{slot});\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL:
            # @dynamic rebind: a FRESH hoisted optional slot per reseat (a
            # distinct concrete/adapter type per target). Slot drawn before
            # the value; its
            # decl hoists to the function top, the emplace + `p = &*slot` reseat
            # stay inline. `val_cpp` carries the slot (concrete/adapter)
            # spelling. At module-init scope the slot spells
            # `static std::optional<T> __global_slot_N;` (slot_static /
            # slot_prefix), so protocol GLOBALS share this arm -- the one
            # rebind slot draw that is scope-safe by construction (no
            # assert_local_slot).
            slot = state.next_slot()
            val_cpp = _emit_expr(stmt.value, state)
            # Backstop: this hoist has no drain point in a leaf emitter; lowering
            # defers generator/async bodies, so reaching here undrainable is a bug.
            assert state.hoist_drainable, (
                "DYN_PROTOCOL rebind hoist reached a non-draining leaf emitter")
            state.hoist_lines.append(
                f"{state.slot_static}std::optional<{stmt.val_cpp}> "
                f"{state.slot_prefix}_{slot};")
            out.write(f"{indent}{state.slot_prefix}_{slot}"
                      f".emplace({val_cpp});\n")
            out.write(f"{indent}{name} = &*{state.slot_prefix}_{slot};\n")
        elif stmt.kind is PtrSlotKind.DYN_PROTOCOL_ERASED:
            # `p2 = p1` where p1 is already erased: re-alias, no slot (the deref'd
            # source renders its own parens).
            out.write(f"{indent}{name} = &{_emit_expr(stmt.value, state)};\n")
        elif stmt.kind is PtrSlotKind.PTR_ADDR:
            # Lvalue-name reseat: address-of the bare storage read.
            out.write(f"{indent}{name} = "
                      f"&({_emit_expr(stmt.value, state)});\n")
        elif stmt.kind is PtrSlotKind.GLOBAL_HOIST_RVALUE:
            # Write of a HOISTED pointer-slot global: the slot is
            # re-assignable, so it rides the hoist lines as a
            # `static std::optional<T> __global_slot_N;` and the write lifts
            # through it. No assert_local_slot -- the hoist line spells the
            # scope's own prefix + static, like RECORD_HOISTED. The FIRST
            # write allocates the slot and registers it by name; every later
            # write to the same global lifts through that same slot.
            val_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            slot = _use_rebind_slot(state, stmt.name)
            if slot is None:
                slot = state.next_slot()
                state.rebind_slots[stmt.name] = slot
                state.global_slot_locals.add(stmt.name)
                assert state.hoist_drainable, (
                    "GLOBAL_HOIST_RVALUE hoist reached a non-draining leaf "
                    "emitter")
                state.hoist_lines.append(
                    f"{state.slot_static}std::optional<{stmt.val_cpp}> "
                    f"{state.slot_prefix}_{slot};")
            out.write(f"{indent}{name} = "
                      f"&*({state.slot_prefix}_{slot} = {val_cpp});\n")
            _witness("top_level.global_hoist_slot")
        elif stmt.kind is PtrSlotKind.GLOBAL_NULL:
            out.write(f"{indent}{name} = nullptr;\n")
            _witness("top_level.global_null")
        elif stmt.kind is PtrSlotKind.GLOBAL_PTR_COPY:
            # Source is another pointer-slot global: already a `T*`, so the
            # write copies the raw pointer (NOT the value-position deref).
            out.write(f"{indent}{name} = "
                      f"{escape_cpp_name(stmt.value.name)};\n")
            _witness("top_level.global_ptr_copy")
        elif stmt.kind is PtrSlotKind.GLOBAL_REBIND:
            # Later rvalue write: reuse the `static __global_slot_N` the
            # first write allocated (a PLAIN slot, hence `&(slot = ..)`).
            val_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            slot = _use_rebind_slot(state, stmt.name)
            out.write(f"{indent}{name} = "
                      f"&({state.slot_prefix}_{slot} = {val_cpp});\n")
            _witness("top_level.global_slot_reuse")
        elif stmt.kind is PtrSlotKind.INLINE_RVALUE:
            # Slotless local's rvalue reseat: the first allocates the plain
            # block slot in place (value renders before the slot draw);
            # later rvalue reseats reuse it.
            val_cpp = _emit_expr(stmt.value, state)
            slot = state.inline_rvalue_slots.get(stmt.name)
            if slot is None:
                slot = (state.assert_local_slot() or state.next_slot())
                state.inline_rvalue_slots[stmt.name] = slot
                out.write(f"{indent}{stmt.val_cpp} __slot_{slot} = "
                          f"{val_cpp};\n")
                out.write(f"{indent}{name} = &__slot_{slot};\n")
            else:
                out.write(f"{indent}{name} = &(__slot_{slot} = {val_cpp});\n")
        elif stmt.kind is PtrSlotKind.BRANCH_RVALUE:
            # Branch-hoisted rvalue reseat without an if-head slot: the first
            # reseat allocates the function-top `std::optional<T>` lazily
            # (appended to the function-top hoist lines) and registers it;
            # later rvalue reseats reuse it. Value renders before the
            # allocation, so its own counter draws come first.
            val_cpp = _emit_expr(stmt.value, state)
            slot = _use_rebind_slot(state, stmt.name)
            if slot is None:
                slot = (state.assert_local_slot() or state.next_slot())
                state.rebind_slots[stmt.name] = slot
                assert state.hoist_drainable, (
                    "BRANCH_RVALUE rebind hoist reached a non-draining "
                    "leaf emitter")
                state.hoist_lines.append(
                    f"std::optional<{stmt.val_cpp}> __slot_{slot};")
            out.write(f"{indent}{name} = &*(__slot_{slot} = {val_cpp});\n")
        elif stmt.kind is PtrSlotKind.UNION_INLINE_SLOT:
            # The slotless reseat: a FRESH value-variant slot declared at
            # the reseat line + the lift, for a decl that pre-declared no
            # rebind slot.
            val_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            slot = (state.assert_local_slot() or state.next_slot())
            out.write(f"{indent}{stmt.val_cpp} __slot_{slot} = "
                      f"{val_cpp};\n")
            out.write(f"{indent}{name} = "
                      f"::tpy::to_ptr_variant(__slot_{slot});\n")
        else:  # PtrSlotKind.UNION_RVALUE -- emplace + re-lift the rebind slot
            slot = _use_rebind_slot(state, stmt.name)
            out.write(f"{indent}__slot_{slot}.emplace("
                      f"{_emit_expr(stmt.value, state)});\n")
            out.write(f"{indent}{name} = "
                      f"::tpy::to_ptr_variant(*__slot_{slot});\n")
    elif isinstance(stmt, THIRAssign):
        # target is a THIRName (`x = ...`) or, for F2b, a THIRFieldAccess
        # (`recv.field = ...` / `recv->field = ...`); _emit_expr renders both. An
        # F2d rebind-slot pointer-local reseat reuses its optional slot:
        # `p = &*(__slot_N = <rvalue>);`.
        if (stmt.btuple_borrow_cpp is not None
                and isinstance(stmt.target, THIRName)):
            # Owning-call reseat of a hoisted borrow-tuple local: emplace
            # into the pre-declared slot, alias via tuple_to_pointer. The
            # name joins btuple_slot_locals so later plain reseats stay
            # off the `&*(__slot = ...)` ptr-Optional arm.
            v = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            slot = _use_rebind_slot(state, stmt.target.name)
            assert slot is not None, "btuple emplace reseat without a slot"
            state.btuple_slot_locals.add(stmt.target.name)
            if stmt.btuple_opt_cpp is not None:
                out.write(
                    f"{indent}{escape_cpp_name(stmt.target.name)} = "
                    f"{stmt.btuple_opt_cpp}{{::tpy::tuple_to_pointer<"
                    f"{stmt.btuple_borrow_cpp}>"
                    f"(__slot_{slot}.emplace({v}))}};\n")
            else:
                out.write(f"{indent}{escape_cpp_name(stmt.target.name)} = "
                          f"::tpy::tuple_to_pointer<{stmt.btuple_borrow_cpp}>"
                          f"(__slot_{slot}.emplace({v}));\n")
            _witness("btuple.reseat_emplace_emit")
        elif (isinstance(stmt.target, THIRName)
                and stmt.target.name not in state.union_slot_locals
                and stmt.target.name not in state.btuple_slot_locals
                and stmt.target.name not in state.global_slot_locals
                # A borrow-tuple target never takes the ptr-Optional
                # `&*(__slot = ...)` reseat, whatever the branch order put
                # in btuple_slot_locals so far -- its plain reseats are
                # bare tuple_to_pointer assigns over the SAME hoisted slot.
                and not (isinstance(stmt.target.result_type, TupleType)
                         and stmt.target.result_type
                         .has_pointer_repr_element())
                and _use_rebind_slot(state, stmt.target.name) is not None):
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
        # The subscript write (and the aug-assign subscript form, whose
        # synthetic binop value arrives pre-built): bounds-safe writes share
        # _emit_subscript's operator[] render; checked writes call the
        # free-function dunder.
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
            # template -- the write-side twin of the bytes_getitem read
            # arm.
            rt = unwrap_qualifiers(stmt.target.receiver.result_type)
            sym = ("::tpy::bytearray_setitem" if is_bytearray_type(rt)
                   else "::tpy::__setitem__")
            out.write(f"{indent}{sym}({recv_cpp}, {idx_cpp}, "
                      f"{value_cpp});\n")
    elif isinstance(stmt, THIRSliceAssign):
        # The resolved slice __setitem__ @native free-function
        # (list_set_slice / list_set_stepped_slice) over the
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
        # The resolved_inplace arm: the mutating dunder's @native
        # free-function (list_extend, ...) over the receiver
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
        # (one flush point per write). A
        # brace-init value takes the typed_brace_init type prefix so it binds
        # to emplace's forwarding ref (a record-ctor value is self-describing).
        value_cpp = _emit_expr(stmt.value, state)
        if value_cpp.startswith("{") and stmt.cpp_type is not None:
            value_cpp = f"{stmt.cpp_type}{value_cpp}"
        state.temps.flush(out, indent)
        out.write(f"{indent}{escape_cpp_name(stmt.name)}.emplace({value_cpp});\n")
    elif isinstance(stmt, THIRCoroHandleMove):
        # The NAME-source coro-handle write's two-line pair.
        tgt = escape_cpp_name(stmt.target)
        src = escape_cpp_name(stmt.source)
        out.write(f"{indent}{tgt}.emplace(std::move(*{src}));\n")
        out.write(f"{indent}{src}.reset();\n")
    elif isinstance(stmt, THIRNarrowAlias):
        # The isinstance-narrowing extraction's variant arm
        # (VariantAccess.get_by_type with lvalue=True: the ptr-variant deref
        # carries no outer parens).
        qualifier = "const auto&" if stmt.const_ref else "auto&"
        deref = "*" if stmt.is_ptr_variant else ""
        out.write(f"{indent}{qualifier} {stmt.alias} = {deref}"
                  f"std::get<{stmt.member_cpp}>({stmt.variant_cpp});\n")
    elif isinstance(stmt, THIRDynNarrowAlias):
        # The polymorphic cast-and-cache extraction: explicit type + `*`
        # deref of the pre-composed cast RHS.
        const_pfx = "const " if stmt.is_const else ""
        out.write(f"{indent}{const_pfx}{stmt.member_cpp}& {stmt.alias} = "
                  f"*{stmt.cast_rhs_cpp};\n")
    elif isinstance(stmt, THIRAnyNarrowAlias):
        # The Any-narrowing extraction: explicit type, not auto&.
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
        # value: `return {};`.
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
    elif isinstance(stmt, THIRFinallyDeferredReturn):
        # The pointer capture binds BEFORE the chain, the materialize move
        # runs after it, and a
        # terminating finally keeps the [[maybe_unused]] capture -- Python
        # still evaluates the return expression it then overrides.
        _witness_chain("return", state, 0)
        ptr, capture_rhs, materialize = _deferred_return_triple(stmt, state)
        chain = io.StringIO()
        terminated = _emit_finally_chain(chain, indent, state)
        maybe_unused = "[[maybe_unused]] " if terminated else ""
        out.write(f"{indent}{maybe_unused}auto* {ptr} = {capture_rhs};\n")
        out.write(chain.getvalue())
        if terminated:
            _witness("try.chain_terminated")
        else:
            out.write(f"{indent}return {materialize};\n")
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
        if stmt.void_cast:
            out.write(f"{indent}(void)({expr_cpp});\n")
        else:
            out.write(f"{indent}{expr_cpp};\n")
    elif isinstance(stmt, THIRTupleUnpack):
        # Each non-discard target declares a fresh value-scalar local. The
        # source bind splits on shape -- a bare-name / loop-shadow source is
        # ref-bound (`const auto&`, no owned/ref elements), a call / field
        # rvalue is materialized by value (`auto`; its arg temps flush before
        # the bind line, exactly like a bare expr statement).
        tmp = f"__tup_{state.next_unpack()}"
        # One holder line per TupleSourceBind form (renders documented on
        # the enum); the name forms share the source_cpp spelling override.
        sb = stmt.source_bind
        src = (stmt.source_cpp if stmt.source_cpp is not None
               else escape_cpp_name(stmt.source))
        if sb is TupleSourceBind.STORAGE_WRAP:
            if stmt.source_expr is not None:
                # An expression source (`pairs[0]`): render + flush its
                # temps like the RVALUE arm, then lift the whole element.
                src = _emit_expr(stmt.source_expr, state)
                state.temps.flush(out, indent)
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
        elif sb is TupleSourceBind.NAME_MOVE:
            out.write(f"{indent}auto&& {tmp} = std::move({src});\n")
        elif sb is TupleSourceBind.NAME_COPY:
            out.write(f"{indent}auto {tmp} = {src};\n")
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
            elif bind == "frame_opt_ptr":
                # Ptr-repr Optional frame target off a STORAGE optional
                # element: nullptr doubles as None.
                out.write(f"{indent}{escape_cpp_name(name)} = "
                          f"::tpy::optional_to_ptr({get});\n")
            elif bind in ("frame_assign", "frame_emplace"):
                # Resumable frame targets: assigned, never re-declared. The
                # wrap is the per-element is_owned move (ref elements
                # reject at lowering -- they would need an unwrap_ref wrap);
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
                # Reused target: an already-declared name, so no decl.
                out.write(f"{indent}{escape_cpp_name(name)} = {get};\n")
            elif bind == "global_slot":
                # Pointer-slot global at module init: the moved-out element
                # needs storage that outlives `__tpy_init`, so it lands in a
                # `static` slot the pre-declared global points at. Lowering
                # admits owned elements only, hence the unconditional move.
                slot = state.global_slot()
                out.write(f"{indent}{state.slot_static}{cpp} {slot} = "
                          f"std::move({get});\n")
                out.write(f"{indent}{escape_cpp_name(name)} = &{slot};\n")
                # A later rvalue write reuses this slot, so it registers in
                # `rebind_slots` here.
                state.rebind_slots[name] = state.slot_counter
                state.global_slot_locals.add(name)
            elif bind == "unwrap_ref":
                # Wrapper-reference element: the capture's slot is a live
                # `X&`; unwrap_ref hands back that reference to alias.
                out.write(f"{indent}{cpp}& {escape_cpp_name(name)} = "
                          f"::tpy::unwrap_ref({get});\n")
            elif bind == "ptr_variant":
                # Own[A | B] element: the capture holds the VALUE variant,
                # so the target lifts per element into the pointer variant.
                out.write(f"{indent}{cpp} {escape_cpp_name(name)} = "
                          f"::tpy::to_ptr_variant({get});\n")
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
        # Throw-tier arms never walk the finally-frame stack -- the throw
        # propagates through the emitted catch(...) arms, which run the
        # finally bodies. The return-tier arms are RETURNS (make_unexpected),
        # so they take the finally-aware return shape like THIRReturn.
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
            # captured error.
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
            # throw line -- the per-statement temp flush.
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
        # The bind form: the counter draws BEFORE the call renders, so a
        # nested unwrap in an argument gets the higher number.
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
        if stmt.target is not None:
            tgt_cpp = _emit_expr(stmt.target, state)
            out.write(f"{inner}{tgt_cpp} = "
                      f"::tpy::unwrap_ref_move(*{tmp});\n")
            _witness("er.bind_field_target")
        elif stmt.ptr_rebind:
            slot = _use_rebind_slot(state, stmt.name)
            out.write(f"{inner}{name} = &*({state.slot_prefix}_{slot} = "
                      f"::tpy::unwrap_ref_move(*{tmp}));\n")
            _witness("er.bind_ptr_rebind")
        elif stmt.alias_bind:
            out.write(f"{inner}{name} = &(::tpy::unwrap_ref(*{tmp}));\n")
            _witness("er.bind_alias")
        else:
            out.write(f"{inner}{name} = ::tpy::unwrap_ref_move(*{tmp});\n")
        out.write(f"{indent}}}\n")
        _witness("er.bind")
    elif isinstance(stmt, THIRErrorReturnDiscard):
        # The discard form, reached through the expr-stmt position: the call
        # renders BEFORE the counter draws, the reverse of the bind form.
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
        # renamed the param to `__param_{name}`.
        init = stmt.init_cpp or f"__param_{stmt.name}"
        out.write(f"{indent}{stmt.cpp_type} {stmt.name} = {init};\n")
    elif isinstance(stmt, THIROverloadDefault):
        # Short-stub omitted impl param as a default-initialized local.
        out.write(f"{indent}{stmt.cpp_type} {stmt.name} = "
                  f"{stmt.cpp_default};\n")
    elif isinstance(stmt, THIRDelVar):
        # `{ auto __del_sink = std::move(name); }` per sunk name -- the value
        # moves into a block-scoped temp destroyed immediately (early release).
        for name, deref in stmt.sinks:
            sigil = "*" if deref else ""
            out.write(f"{indent}{{ auto __del_sink = "
                      f"std::move({sigil}{name}); }}\n")
    elif isinstance(stmt, THIRDelItem):
        # One `::tpy::__delitem__(recv, key);` line per target, in source
        # order.
        for call in stmt.calls:
            call_cpp = _emit_expr(call, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{call_cpp};\n")
    elif isinstance(stmt, THIRStmtSeq):
        _emit_stmts(out, stmt.stmts, indent_level, state)
    elif isinstance(stmt, THIRResumableReturn):
        # Scaffolding is skeleton in every position: the hook re-enters
        # `_make_async_return` / `_make_generator_resumable_return`, whose
        # value render loops back through `render_return_value` (the
        # identity-keyed table this node's value was registered into).
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
        out.write(f"{indent}// def {stmt.name}: frame member\n")
    elif isinstance(stmt, THIRImportInit):
        _witness("top_level.import_init")
        for call in stmt.calls:
            out.write(f"{indent}{call}();\n")
    elif isinstance(stmt, THIRNoOpStmt):
        # No code -- the `// pass` source comment (if any) is emitted by the
        # caller (_emit_stmts) from the node's loc. A skipped statement's
        # leading trivia (trivia_loc) emits inline comments only.
        if stmt.trivia_loc is not None:
            state.comments.inline(out, stmt.trivia_loc, INDENT * indent_level)
    elif isinstance(stmt, THIRFoldedBlock):
        # Per-@overload-stub fold splice: the surviving statements emit flat
        # at the enclosing indent. The chain head's preceding `#` comments
        # emit even for an all-dead chain.
        _witness("fold.overload_block")
        if stmt.trivia_loc is not None:
            state.comments.inline(out, stmt.trivia_loc, INDENT * indent_level)
        if stmt.burns_match_counter:
            state.match_counter += 1
        _emit_stmts(out, stmt.stmts, indent_level, state)
    elif isinstance(stmt, THIRFoldedIfChain):
        # The partially-folded live chain: clean `if / else if` over the
        # surviving branches, NO condition source comments, else from the
        # last original node. Temps flush before each branch line; lowering
        # admits them only on the first branch.
        _witness("fold.overload_live_chain")
        if stmt.trivia_loc is not None:
            state.comments.inline(out, stmt.trivia_loc, indent)
        for i, (cond_node, body) in enumerate(stmt.branches):
            cond = _emit_expr(cond_node, state)
            state.temps.flush(out, indent)
            if i == 0:
                out.write(f"{indent}if ({cond}) {{\n")
            else:
                out.write(f"{indent}}} else if ({cond}) {{\n")
            _emit_stmts(out, body, indent_level + 1, state)
        if stmt.else_body:
            out.write(f"{indent}}} else {{\n")
            _emit_stmts(out, stmt.else_body, indent_level + 1, state)
        out.write(f"{indent}}}\n")
    elif isinstance(stmt, THIRMatchFoldBind):
        _witness("fold.overload_bind")
        binder = "auto" if stmt.by_value else "auto&"
        out.write(f"{INDENT * indent_level}{binder} {stmt.name_cpp}"
                  f" = {stmt.source_cpp};\n")
    else:
        raise THIRCodeGenError(f"unhandled THIR stmt: {type(stmt).__name__}")


def _emit_print_arg(a: THIRPrintArg, state: _EmitState) -> str:
    inner = _emit_expr(a.expr, state)
    if a.deref:
        inner = f"(*{inner})"
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
    if a.print_form is PrintForm.VARARGS:
        return f"::tpy::VarargsPrinter({inner})"
    if a.print_form is PrintForm.VALUE_GENERIC:
        return f"::tpy::ValuePrinter({inner})"
    if a.print_form is PrintForm.BYTEARRAY:
        return f"::tpy::ByteArrayPrinter({inner})"
    if a.print_form is PrintForm.OPT_VAL:
        return f"::tpy::print_optional_val({inner})"
    if a.print_form is PrintForm.OPT_VAL_BOOL:
        return f"::tpy::print_optional_val<::tpy::print_bool, {a.opt_inner_cpp}>({inner})"
    if a.print_form is PrintForm.OPT_VAL_FLOAT:
        return f"::tpy::print_optional_val<::tpy::print_float, {a.opt_inner_cpp}>({inner})"
    if a.print_form is PrintForm.OPT_VAL_FMT:
        return (f"::tpy::print_optional_val<{a.opt_fmt_cpp}, "
                f"{a.opt_inner_cpp}>({inner})")
    if a.print_form is PrintForm.OPT_PTR:
        return f"::tpy::print_optional({inner})"
    if a.print_form is PrintForm.OPT_PTR_FMT:
        return (f"::tpy::print_optional<{a.opt_fmt_cpp}, "
                f"{a.opt_inner_cpp}>({inner})")
    return inner


def _print_chain_token(expr, value, state: _EmitState) -> 'str | None':
    """One print chain token: a runtime kwarg renders as its expression, a
    literal via cpp_string_literal_expr, an empty/suppressed one skips (None).
    The " "/"\\n" defaults ride the value slot too, spelled by the same
    helper."""
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
    # The cout-sink path: `std::cout << a0 << SEP << a1 << ... << END;`.
    # Default sep=" " between args, end="\n"; empty print() is just the
    # newline.
    indent = INDENT * indent_level
    # end renders before sep. The order is unobservable while the kwarg gate
    # admits only literal/plain-name sources (no hoisted temps); revisit it
    # before widening that gate.
    sep_token = _print_chain_token(stmt.sep_expr, stmt.sep_value, state)
    end_token = _print_chain_token(stmt.end_expr, stmt.end_value, state)
    parts = _print_parts(stmt.args, sep_token, end_token, state)
    if stmt.flush:
        parts.append("std::flush")
    # Args render first: their hoisted temps flush before the cout line.
    state.temps.flush(out, indent)
    if not parts:
        # A fully-suppressed chain emits nothing; lowering rejects the
        # kwargs-on-empty-print shape, so this is a defensive no-op.
        return
    sink = ("std::cout" if stmt.sink_expr is None
            else f"::tpy::as_ostream({_emit_expr(stmt.sink_expr, state)})")
    out.write(f"{indent}{sink} << " + " << ".join(parts) + ";\n")


def _emit_stmts(out: TextIO, stmts, indent_level: int, state: _EmitState) -> None:
    indent = INDENT * indent_level
    for stmt in stmts:
        # A desugar-expanded statement (no_source_comment) shares the first
        # statement's source comment -- skip the repeat.
        if not stmt.no_source_comment:
            state.comments.stmt(out, stmt.loc, indent)
        _emit_stmt(out, stmt, indent_level, state)


def emit_thir_body(out: TextIO, fn: THIRFunction, indent_level: int = 1,
                   *, comments: CommentSink,
                   temps: TempSink,
                   with_counter: ModuleCounter,
                   try_counter: ModuleCounter,
                   finally_guard_counter: ModuleCounter,
                   return_cpp: 'str | None' = None,
                   global_scope: bool = False) -> None:
    """Emit `fn`'s body statements (no signature, no braces) at `indent_level`.

    `temps` is the `__tmp_N` sink, `with_counter` the `__ctx_N` sink,
    `try_counter` the try/error_return label+temp sink, and
    `finally_guard_counter` the `__fin_ran_N` cleanup-guard sink -- all
    module-cumulative and ctx-backed, so the numbering runs continuously
    across the module's bodies. `return_cpp` is the signature's return
    spelling (`ctx.current_return_cpp` at the seam), read only by the
    finally-chain return temp decl. `global_scope` emits the module-init body
    (`__tpy_init`), whose slots spell `static __global_slot_N`."""
    state = _EmitState(comments, temps=temps,
                       with_counter=with_counter,
                       try_counter=try_counter,
                       finally_guard_counter=finally_guard_counter,
                       return_cpp=return_cpp,
                       error_return_cpp=fn.error_return_cpp,
                       slot_prefix=("__global_slot" if global_scope
                                    else "__slot"),
                       slot_static=("static " if global_scope else ""))
    # Buffer the body so function-top hoists (@dynamic rebind slots,
    # allocated mid-body) can be prepended ahead of it.
    body_buf = io.StringIO()
    _emit_stmts(body_buf, fn.body, indent_level, state)
    for content in state.hoist_lines:
        out.write(f"{INDENT * indent_level}{content}\n")
    out.write(body_buf.getvalue())
    # Void @error_return functions return `{}` at the end -- the implicit
    # success value, emitted unconditionally.
    if fn.error_return_cpp and isinstance(fn.return_type, VoidType):
        out.write(f"{INDENT * indent_level}return {{}};\n")
        _witness("er.void_tail")


def emit_thir_constructor_tail(out: TextIO, ctor: THIRConstructor,
                               *, comments: CommentSink,
                               temps: TempSink,
                               with_counter: ModuleCounter,
                               try_counter: ModuleCounter,
                               finally_guard_counter: ModuleCounter,
                               body_indent_level: int = 2) -> None:
    """Emit a constructor's member-init-list + body tail (the ` : f(v)... {}` that
    follows the signature). The signature itself is written by the record
    skeleton before this is called. A pure-MIL constructor has an empty
    `body`, so this emits ` {}` (or
    ` : inits {}`). MIL / base-init cells have no flush point, so arg temps
    never lower there (gate + validator enforced); the body shares the
    statement machinery and its sink. ``body_indent_level`` is 2 for an
    in-struct definition, 1 for an out-of-line one at namespace scope."""
    state = _EmitState(comments, temps=temps,
                       with_counter=with_counter,
                       try_counter=try_counter,
                       finally_guard_counter=finally_guard_counter)
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
    parse-tree node it holds; a missing key means the gate and
    the seam disagree on the routed body's shape -- a hard error, never
    silently skipped."""

    def __init__(self, body, *, comments: 'CommentSink',
                 temps: 'TempSink',
                 with_counter: 'ModuleCounter',
                 try_counter: 'ModuleCounter',
                 finally_guard_counter: 'ModuleCounter',
                 return_cpp: 'str | None' = None,
                 frame_shadow_probe: 'Callable[[str], bool] | None' = None,
                 resumable_return_hook: 'Callable[[object, int], str] | None'
                 = None,
                 live_finally_guards: 'set[str] | None' = None,
                 ast_finally_push=None, ast_finally_pop=None,
                 iter_counter: 'IterCounter | None' = None,
                 ) -> None:
        self._body = body
        self._state = _EmitState(comments,
                                 temps=temps,
                                 with_counter=with_counter,
                                 try_counter=try_counter,
                                 finally_guard_counter=finally_guard_counter,
                                 return_cpp=return_cpp,
                                 frame_shadow_probe=frame_shadow_probe,
                                 resumable_return_hook=resumable_return_hook,
                                 hoist_drainable=False)
        if live_finally_guards is not None:
            # Share the CTX's live-guard set: hook-side exit sites (the
            # skeleton's _emit_finally_chain) and THIR's guard decls must
            # see one liveness truth (names are unique via the shared
            # counter).
            self._state.live_finally_guards = live_finally_guards
        self._state.ast_finally_push = ast_finally_push
        self._state.ast_finally_pop = ast_finally_pop
        if iter_counter is not None:
            self._state.iter_counter = iter_counter

    def _lookup(self, table, node, what: str):
        if node not in table:
            raise THIRCodeGenError(
                f"resumable seam: routed body has no lowered {what} for "
                f"{type(node).__name__} (lowering/seam disagreement)")
        return table[node]

    def emit_leaf_stmt(self, out: TextIO, stmt, indent_level: int) -> None:
        """Emit one BB leaf statement (or a RaiseT terminator's statement),
        source comment included -- the seam the skeleton calls at each
        leaf-statement position."""
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
        Defaults to no-move, the fail-safe polarity: a call site that
        forgets the kwarg gets the copy, never the move."""
        node = self._lookup(self._body.return_values, ret, "return value")
        if not allow_move and isinstance(node, THIRMove):
            node = node.value
        return _emit_expr(node, self._state)

    def render_deferred_return(self, ret) -> 'tuple[str, str, str]':
        """The (pointer name, capture RHS, materialize expression) triple the
        frame's return scaffolding binds around its inline finally chain, for
        a sema-stamped finally-deferred return.

        Lowering rejects a body whose stamped return it cannot build a recipe
        for, so a routed body always has one: a missing entry is a
        lowering/seam disagreement and `_lookup`'s raise is the only correct
        answer. Falling through to the eager arms instead would move storage
        the finally chain still reads."""
        node = self._lookup(self._body.deferred_returns, ret,
                            "deferred return recipe")
        return _deferred_return_triple(node, self._state)

    def render_yield_value(self, ys) -> str:
        """Render a generator `yield v`'s value -- the seam the skeleton
        calls at the yield site."""
        return _emit_expr(self._lookup(self._body.yield_values, ys,
                                       "yield value"), self._state)

    def emit_nested_def_body(self, out: TextIO, func,
                             indent_level: int) -> None:
        """Emit a frame nested def's MEMBER body -- the seam
        `gen_coro_finally_top_def` calls for it. The
        signature/struct-decl lines stay skeleton; the body statements were
        lowered under the member scope at frame lowering."""
        body = self._lookup(self._body.nested_def_bodies, func,
                            "nested def body")
        _emit_stmts(out, body, indent_level, self._state)

    def render_suspend_expr(self, expr) -> str:
        """Render an ERASED/BORROWED await operand or a bound-method await
        receiver -- the seam the skeleton calls at the suspend site (it keeps
        its own move / & / .get() / __self-prepend wrap)."""
        return _emit_expr(self._lookup(self._body.suspend_exprs, expr,
                                       "suspend expr"), self._state)

    def render_region_expr(self, expr) -> str:
        """Render a region/loop pseudo-statement's user expression (with
        manager, for-loop iterable, range bound) -- the seam the skeleton
        calls inside its emplace / &(..) / static_cast scaffolding (same
        flush contract as `render_cond`)."""
        return _emit_expr(self._lookup(self._body.region_exprs, expr,
                                       "region expr"), self._state)

    def emit_match_dispatch(self, out: TextIO, match_stmt,
                            indent_level: int,
                            arm_hook: 'Callable[[int, int], None]') -> None:
        """Emit a MatchDispatch's whole type-aware dispatch (subject +
        labels + guards) through THIR's match tiers -- the seam the skeleton
        calls for a match inside a resumable frame. `arm_hook` is the
        skeleton's arm emitter keyed by the case body; it fires at each
        arm-body point, so arm bodies stay BB chains in the state
        machine."""
        node = self._lookup(self._body.match_dispatches, match_stmt,
                            "match dispatch")
        prev = self._state.match_arm_hook
        self._state.match_arm_hook = arm_hook
        try:
            # Direct tier emit: the skeleton calls this without a statement
            # wrapper, so no leading statement comment here either.
            _emit_match(out, node, indent_level, self._state)
        finally:
            self._state.match_arm_hook = prev


class SimpleGenLeafEmitter:
    """Per-routed-body leaf renderer driven by the simple-generator lambda
    peephole skeleton (`gen_generators.gen_simple_generator_inline`). One
    instance per routed body holds one `_EmitState` -- the same contract as
    `ResumableLeafEmitter`, but the seam sites are static (one loop, one
    yield), so the body's blocks and expressions are direct fields, not
    identity-keyed tables."""

    def __init__(self, body, *, comments: 'CommentSink',
                 temps: 'TempSink',
                 with_counter: 'ModuleCounter',
                 try_counter: 'ModuleCounter',
                 finally_guard_counter: 'ModuleCounter',
                 hoist_sink: 'Callable[[str], None] | None' = None) -> None:
        self._body = body
        self._state = _EmitState(comments,
                                 temps=temps,
                                 with_counter=with_counter,
                                 try_counter=try_counter,
                                 finally_guard_counter=finally_guard_counter,
                                 hoist_drainable=False,
                                 hoist_sink=hoist_sink)

    def emit_init(self, out: TextIO, indent_level: int) -> None:
        """Emit the pre-loop init block -- the seam the skeleton calls for
        it."""
        _emit_stmts(out, self._body.init, indent_level, self._state)

    def emit_pre_yield(self, out: TextIO, indent_level: int) -> None:
        _emit_stmts(out, self._body.pre_yield, indent_level, self._state)

    def emit_post_yield(self, out: TextIO, indent_level: int) -> None:
        _emit_stmts(out, self._body.post_yield, indent_level, self._state)

    def render_cond(self) -> str:
        """Render the while-branch condition."""
        return _emit_expr(self._body.cond, self._state)

    def render_yield_value(self) -> str:
        """Render the yield value -- the seam the skeleton calls at the
        yield site."""
        return _emit_expr(self._body.yield_value, self._state)

    def render_iterable(self) -> str:
        """Render the for-branch source expression (the skeleton reuses the
        returned string across its capture / decltype / emplace scaffolding,
        so the iterable renders exactly once)."""
        return _emit_expr(self._body.iterable, self._state)

    def render_range_arg(self, i: int) -> str:
        """Render the i-th for-range bound (the skeleton wraps it in its
        `static_cast` scaffolding)."""
        return _emit_expr(self._body.range_args[i], self._state)
