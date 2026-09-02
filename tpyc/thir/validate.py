"""Structural form validator: a second gate beside the whole-corpus byte-diff.

The byte-parity gate is corpus-observational -- a node carrying a wrong `form`
tag survives it unless some corpus case happens to observe the difference
(proven by the F6 review round's THIRCoerce finding: a passthrough defaulting
`form=VALUE` over a STORAGE source stayed byte-identical). As routing grows
into the F3+/F4 rungs, where form drives real conversions, this lowering-time
walk makes a form lie fail loudly at the function that carries it instead of
waiting for a corpus witness.

Node-local checks (F4 U1):
  * `THIRFormConvert` must convert -- change the form or the (family-internal)
    result type. A no-op convert is a lie: emit would render a conversion
    helper around an already-converted value.
  * `THIRCoerce` is an emit passthrough -- it must carry its inner form,
    EXCEPT the view-target disposition (str_to_strview / string_to_strview):
    the result is a view into the source's buffer whatever the source's form,
    so lowering sets BORROW itself.

Sink-position checks (F4 U2 -- where borrow/storage conversions become
load-bearing across union slots):
  * A field write into a POINTER-LIFTED storage slot (pointer-repr Optional /
    pointer-variant union / pointer-repr tuple) never takes a BORROW value --
    the borrow->storage converts (`ptr_to_optional` / `to_value_variant` /
    `tuple_to_storage`) must have wrapped it. A plain record borrow (`T&`)
    copy-constructs implicitly and form alone cannot tell `T&` from `T*`, so
    plain-record sinks are left unchecked.
  * A MIL cell's value gets the value-side version of the same test (the ctor
    node carries no field types; an unconverted Optional borrow's
    `result_type` is the pointee record, a known blind spot).
  * A BORROW return value requires a borrow-legal return type: a non-value
    type (pointer/pointer-variant), a pointer-repr tuple, or a str/bytes VIEW
    (a `std::string_view` / span return is a legitimate borrow of a value
    type) -- or else a borrow whose OWN type is a value record, whose `T&`
    render copy-constructs into the by-value `T` slot.
`THIRBytesLiteral`'s render verdict rides the `form` tag (the bespoke `owned`
flag was folded in with U2's opening), so bytes literals sit on the validated
axis like every other expression.

Every lowered body is validated, whatever its shape: ordinary functions and
constructors through `validate_function` / `validate_constructor`, and the
resumable-frame and simple-generator bodies -- whose leaves the skeleton
holds apart in seam tables rather than one linear body -- through
`validate_resumable_body` / `validate_simple_gen_body`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from ..codegen_cpp.forms import is_plain_nonvalue, is_ptr_variant_union
from ..type_def_registry import (
    is_basic_slice_type, is_bytearray_type, is_bytes_type, is_bytes_view_type,
    is_slice_type, is_span, is_str_type, is_str_view_type, is_string_type,
)
from ..typesys import (
    AnyType, NominalType, OptionalType, OwnType, PtrType, TupleType,
    UnionType,
    TypeParamRef,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .nodes import (
    Form, THIRArgTemp, THIRAssign, THIRCall, THIRChainedCompareStmtExpr,
    THIRCoerce, THIRConstructor,
    THIRCtorCall, THIRErrorReturnBind, THIRErrorReturnDiscard,
    THIRErrorReturnUnwrap, THIRExprStmt, THIRFieldAccess, THIRFormConvert,
    THIRBinOp, THIRExpr, THIRForIterProto, THIRFunction, THIRIf,
    THIRIfExpr, THIRMethodCall,
    THIRNode, THIRInplaceContainerOp, THIRWhile,
    THIRPrint, THIRRaise, THIRReturn, THIRSetItem, THIRSliceAssign,
    THIRSubscript,
    THIRFrameSlotWrite,
    THIRPtrLocalDecl, THIRResumableBody, THIRSelf, THIRSimpleGenBody,
    THIRUnionArgLift, THIRVarDecl,
)


class THIRValidationError(Exception):
    """A lowered node violates a THIR structural invariant -- a lowering bug,
    never an unsupported shape (those must raise during lowering, not produce
    inconsistent THIR)."""


def _iter_children(node: THIRNode):
    for f in dataclasses.fields(node):
        v = getattr(node, f.name)
        if isinstance(v, THIRNode):
            yield v
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, THIRNode):
                    yield item
                elif dataclasses.is_dataclass(item) and not isinstance(item, type):
                    # MIL/base-init cells (THIRMilInit / THIRBaseInit) are
                    # plain dataclasses holding THIR exprs.
                    yield from _iter_children(item)


def _fail(owner: str, node: THIRNode, why: str) -> None:
    loc = getattr(node, "loc", None)
    where = f" at {loc}" if loc is not None else ""
    raise THIRValidationError(
        f"{owner}: {type(node).__name__}{where}: {why}")


def _check_node(owner: str, node: THIRNode) -> None:
    if isinstance(node, (THIRFieldAccess, THIRMethodCall)):
        # A plain method's receiver read carries its own value-position
        # deref (`(*this)`), so the member reached THROUGH the pointer must
        # take the raw receiver -- `(*this)->x` is not valid C++. Keeping
        # this a structural rule is what stops the deref from drifting back
        # into a fact each consumer re-applies by hand.
        recv = node.receiver
        if (isinstance(recv, THIRSelf) and recv.deref
                and node.receiver_through_pointer):
            _fail(owner, node,
                  "dereferenced receiver behind an arrow member access")
    if isinstance(node, THIRCall):
        # Explicit template args ride only the plain / imported spellings --
        # the AST's native and cpp_template arms never emit them.
        if node.template_args_cpp and (node.native_name is not None
                                       or node.cpp_template is not None):
            _fail(owner, node,
                  "template_args_cpp combined with a native/template callee")
    if isinstance(node, THIRFormConvert):
        # `move` is part of the node's identity (its helper is a pure function of
        # family / form / is_const / move), so a same-form same-type convert that
        # carries a move is NOT a no-op -- it materializes `std::move(x)` (an
        # Own[T] param written into a `T` field is already STORAGE form, so the
        # move is the whole operation).
        cv = node.result_type
        cv = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(cv)))
              if cv is not None else None)
        # A plain-non-value BORROW convert is the `T&` -> reseatable `T*`
        # address-of lift, and BORROW spells BOTH of those for such a type,
        # so form + type cannot tell the two apart -- the same blind spot
        # the pointer-lifted field sink rule names. Its same-form same-type
        # shape is the lift doing its job, not a dead node.
        ref_to_ptr_lift = (node.form is Form.BORROW and cv is not None
                           and is_plain_nonvalue(cv))
        if (node.form is node.value.form
                and node.result_type == node.value.result_type
                and not node.move and not ref_to_ptr_lift):
            _fail(owner, node,
                  f"no-op form convert (form={node.form.name}, "
                  f"type={node.result_type})")
        if node.materialize:
            # The view->owned copy: only the view families own the render,
            # and a fresh buffer never moves.
            if not (cv is not None
                    and (is_str_type(cv) or is_string_type(cv)
                         or is_bytes_type(cv) or is_bytearray_type(cv))):
                _fail(owner, node,
                      f"materialize convert with non-view-family result "
                      f"{node.result_type}")
            if node.move:
                _fail(owner, node, "materialize convert carrying a move "
                                   "(a fresh buffer never moves)")
        if (cv is not None and is_bytearray_type(cv)
                and node.form is Form.STORAGE and node.materialize is None):
            # bytearray is the one view-family member whose (family, form)
            # pair does NOT determine the render (see the node docstring):
            # the meaning must be decided at lowering.
            _fail(owner, node,
                  "bytearray STORAGE convert without an explicit "
                  "materialize decision")
    elif isinstance(node, THIRCoerce):
        rt = node.result_type
        rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
              if rt is not None else None)
        view_target = rt is not None and is_str_view_type(rt)
        # The ptr/span/slice coercion families produce VALUE results (`T*`,
        # std::span, Slice) whatever the inner's form; lowering tags VALUE.
        # An `into_any` coerce materializes a fresh `tpy::Any` VALUE cell via
        # make_any whatever the wrapped source's form (a STORAGE `std::monostate`
        # None, a VALUE literal), so it is form-producing like the ptr/span
        # families, not a form passthrough.
        value_target = rt is not None and (
            isinstance(rt, (PtrType, AnyType)) or is_span(rt)
            or is_slice_type(rt) or is_basic_slice_type(rt)
            # An `Optional[Span[...]]` slot coerce produces the same VALUE
            # result (`std::optional<span>` absorbs the as_span rvalue).
            or (isinstance(rt, OptionalType)
                and is_span(unwrap_readonly(rt.inner))))
        # The async return slot's borrow/trait lifts (`&(x)`,
        # `to_val_or_ptr<val_or_ptr_t<T>>(x)`) materialize a pointer or
        # trait-selected prvalue out of any source form, so they are
        # form-producing like the ptr/span families. Keyed by NAME, not by
        # result type: both deliberately keep the SOURCE's type spelling
        # (the pointer/trait spelling lives in the wrap), so the ptr row
        # above structurally cannot see them.
        value_wrap_target = node.coercion_name in (
            "async_ret_addr_of", "async_ret_val_or_ptr")
        # The optional-borrow-tuple wrap (`std::optional<B>{<borrow rhs>}`)
        # materializes a STORAGE optional out of the borrow tuple -- form-
        # producing like the ptr/span families.
        opt_btuple_target = node.coercion_name == "opt_btuple_wrap"
        if node.form is not node.expr.form and not (
                (view_target and node.form is Form.BORROW)
                or ((value_target or value_wrap_target)
                    and node.form is Form.VALUE)
                or (opt_btuple_target and node.form is Form.STORAGE)):
            _fail(owner, node,
                  f"coerce form {node.form.name} != inner "
                  f"{node.expr.form.name} (non-view-target passthrough)")


def _borrow_legal_return(rt) -> bool:
    if rt is None:
        return True
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    # `return name;` into a by-value RECORD or CONTAINER return (`Own[Box]` ->
    # `Box`, `Own[list[T]]` -> `std::vector<T>`; containers are plain
    # NominalTypes): C++ materializes the storage from the borrow source
    # (NRVO / implicit move / copy-construct) with no spelled convert -- the
    # borrow value is legal.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), NominalType)
            and not t.wrapped.is_value_type()):
        return True
    # `Own[T]` with T an open type param: the C++ argument above is the same
    # at every instantiation (`val_or_cref_t<T>` -> `T` is the same NRVO /
    # implicit-move materialization), but a TypeParamRef is not a
    # NominalType, so the row above misses it and the walk FAILS instead of
    # falling back.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), TypeParamRef)):
        return True
    # `return name;` / a slice rvalue into a by-value `Own[A | B]` variant
    # return (`std::variant<...>`): the variant's converting ctor
    # materializes from the borrow source (implicit move for a returned
    # local under P1825), no spelled convert -- the return arm gates the
    # admitted source shapes.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), UnionType)):
        return True
    if not t.is_value_type():
        return True
    if isinstance(t, TupleType) and t.has_pointer_repr_element():
        return True
    # A REFERENCE-element tuple return (`std::tuple<Tree&, int32_t>` -- a
    # wrapper element with no Own marker): the slot itself is the borrow
    # form, so a BORROW value is exactly right (ret.wrapper_ref_tuple).
    if isinstance(t, TupleType) and t.has_ref_elements():
        return True
    if isinstance(t, OptionalType) and not t.uses_pointer_repr():
        # A VIEW-inner value optional (`std::optional<std::string_view>`):
        # the view converts into the optional implicitly, exactly as it does
        # into the bare view slot below -- no spelled convert to miss.
        inner = unwrap_readonly(t.inner)
        if is_str_view_type(inner) or is_bytes_view_type(inner):
            return True
    return is_str_view_type(t) or is_bytes_view_type(t)


def _pointer_lifted_storage(t) -> bool:
    """A storage slot whose borrow form is a POINTER shape with no implicit
    C++ conversion back: `optional<T>` (vs `T*`), value-variant (vs pointer
    variant), storage tuple (vs pointer tuple). A BORROW value at such a slot
    is a form lie -- the convert must have wrapped it."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OptionalType) and t.uses_pointer_repr():
        return True
    if is_ptr_variant_union(t):
        return True
    return isinstance(t, TupleType) and t.has_pointer_repr_element()


def _value_record_borrow(v: THIRExpr) -> bool:
    """A BORROW whose own type is a VALUE record, so its render is a `T&` /
    `const T&` lvalue (`ps[i]`, an lvalue ternary, a `T&`-returning call).

    Such a value materializes into any slot that accepts a `T` -- the return
    object copy-constructs from the reference with no spelled convert, exactly
    as the non-value `Own[record]` row of `_borrow_legal_return` argues. That
    row keys on the return TYPE alone, which cannot see this: a value record
    returned by value is spelled `-> T`, so every borrow source at it reads as
    a form lie. The pointer-lifted shapes a BORROW genuinely could not
    initialize from are other types (Optional / variant / tuple), never a
    plain record."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(v.result_type)))
    return (isinstance(t, NominalType) and t.is_value_type()
            and t.is_user_record)


def _check_stmt(owner: str, stmt: THIRNode, return_type) -> None:
    if isinstance(stmt, THIRAssign):
        if (isinstance(stmt.target, THIRFieldAccess)
                and stmt.value.form is Form.BORROW
                and _pointer_lifted_storage(stmt.target.result_type)):
            _fail(owner, stmt,
                  "BORROW value at a pointer-lifted field-write sink "
                  "(missing a borrow->storage convert)")
    elif isinstance(stmt, THIRReturn):
        if (stmt.value is not None and stmt.value.form is Form.BORROW
                and not _borrow_legal_return(return_type)
                and not _value_record_borrow(stmt.value)):
            _fail(owner, stmt,
                  f"BORROW return value for value-typed return {return_type}")


def _walk_arg_list(owner: str, args: 'Sequence[THIRExpr]',
                   return_type=None, *,
                   argtemp_ok: bool = False,
                   eager_only: bool = False) -> None:
    """Walk one call-shaped argument list -- the call/ctor/method args, a
    raise's ctor args, an await emplace's args.

    All three are the same position: a temp there flushes at the enclosing
    statement iff that statement is a flush point, and a temp's own SOURCE
    ctor flushes its nested temps at the SAME point (`describe(Canvas(
    Circle(5)))` -- __tmp_1 innermost-first, then __tmp_2), so the right
    propagates through call-arg nesting."""
    for a in args:
        if isinstance(a, THIRArgTemp):
            if not argtemp_ok:
                _fail(owner, a, "THIRArgTemp under a non-flushable "
                                "statement position")
            if eager_only and a.movable is None and a.would_defer():
                _fail(owner, a, "unaudited deferring THIRArgTemp under "
                                "a conditional operand")
            _walk(owner, a.init, return_type, argtemp_ok=argtemp_ok,
                  eager_only=eager_only)
        elif isinstance(a, THIRUnionArgLift) and a.temp_cpp is not None:
            # The temp-bearing lift hoists a decl like THIRArgTemp does, so
            # it needs the same flush right.
            if not argtemp_ok:
                _fail(owner, a, "temp-bearing THIRUnionArgLift under a "
                                "non-flushable statement position")
            if a.value is not None:
                _walk(owner, a.value, return_type, argtemp_ok=argtemp_ok,
                      eager_only=eager_only)
        else:
            _walk(owner, a, return_type, argtemp_ok=argtemp_ok,
                  eager_only=eager_only)


def _walk(owner: str, node: THIRNode, return_type=None, *,
          argtemp_ok: bool = False, eager_only: bool = False) -> None:
    """`argtemp_ok` marks the value expression of a flushable statement
    (expr stmt / var-decl init / assign value / return value / print arg)
    -- the only
    region where a THIRArgTemp may appear, and there only under call-arg
    nesting (the flush right rides through call-shaped args / receivers /
    operands). If and while CONDITIONS are also flushable: the emit places
    their temps (pre-`if` flush, the nested-elif block, the restructured
    `while (true)` loop head -- never a pre-loop stale snapshot). Anywhere
    else (a loop-header iterable, a MIL cell) a temp has no flush point on
    the AST path, so reaching one is a lowering bug.

    `eager_only` marks a CONDITIONAL operand position (a ternary arm, a
    logical RHS): an AUDITED temp (movable fact mirrored off the AST
    creator) is legal there -- the emit's conditional region defers or
    keeps it eager exactly as the AST decides -- while an UNAUDITED temp
    that might defer (`THIRArgTemp.would_defer`'s conservative guess) is a
    lowering bug: its eager/deferred placement could diverge."""
    _check_node(owner, node)
    _check_stmt(owner, node, return_type)
    if isinstance(node, THIRArgTemp):
        _fail(owner, node, "THIRArgTemp outside a call arg position")
    if isinstance(node, THIRUnionArgLift) and node.temp_cpp is not None:
        # The temp-bearing lift hoists a decl like THIRArgTemp does, so it
        # is legal only where a temp has a flush point (checked in the
        # call-arm loop below, which returns before re-reaching this node).
        _fail(owner, node, "temp-bearing THIRUnionArgLift outside a call "
                           "arg position")
    if isinstance(node, (THIRCall, THIRMethodCall, THIRCtorCall)):
        # A ctor call carries a temp only for its mutated-ref-slot record
        # rvalue (the ctor_mutated arm); const-slot rvalues inline temp-free.
        if isinstance(node, THIRMethodCall):
            # A call-shaped receiver's arg temps flush at the same statement
            # (allow_temps rides into receivers at lowering). The receiver
            # itself may BE a temp: the generator-factory ctor-rvalue lift
            # (`Counter __tmp_N = Counter(..);` + `__tmp_N.each()`), flushed
            # at the consuming for-head / iterator-object decl.
            if isinstance(node.receiver, THIRArgTemp):
                if not argtemp_ok:
                    _fail(owner, node.receiver,
                          "receiver THIRArgTemp under a non-flushable "
                          "statement position")
                if (eager_only and node.receiver.movable is None
                        and node.receiver.would_defer()):
                    _fail(owner, node.receiver,
                          "unaudited deferring THIRArgTemp receiver under "
                          "a conditional operand")
                _walk(owner, node.receiver.init, return_type,
                      argtemp_ok=argtemp_ok, eager_only=eager_only)
            else:
                _walk(owner, node.receiver, return_type,
                      argtemp_ok=argtemp_ok, eager_only=eager_only)
        _walk_arg_list(owner, node.args, return_type, argtemp_ok=argtemp_ok,
                       eager_only=eager_only)
        return
    if isinstance(node, THIRErrorReturnUnwrap):
        # The expression unwrap is TRANSPARENT for flushability: its call's
        # arg temps flush at the enclosing statement exactly as they would
        # unwrapped (the wrapper only composes the `({ ... })` render).
        _walk(owner, node.call, return_type, argtemp_ok=argtemp_ok)
        return
    if isinstance(node, (THIRErrorReturnBind, THIRErrorReturnDiscard)):
        # Statement-level unwrap blocks: the call renders and its temps
        # flush before the block line (gen_stmt's single flush point), so
        # the call is a flushable value position.
        _walk(owner, node.call, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRExprStmt):
        _walk(owner, node.expr, return_type, argtemp_ok=True)
        return
    if isinstance(node, (THIRIf, THIRWhile)):
        # Conditions are flushable: _emit_if flushes before the `if (` /
        # inside the nested-elif block, _emit_while restructures the loop
        # head (`while (true) { <temps> if (!cond) break;`).
        _walk(owner, node.condition, return_type, argtemp_ok=True)
        for child in _iter_children(node):
            if child is not node.condition:
                _walk(owner, child, return_type)
        return
    if isinstance(node, THIRForIterProto):
        # The iterable renders as its own `__src` bind with a temps flush
        # right before it (inside the rvalue brace scope -- the AST's
        # for-each flush point), so it is a flushable value position. The
        # begin/end for-each route stays temp-free: its iterable renders
        # into the loop header, which has no flush point.
        _walk(owner, node.iterable, return_type, argtemp_ok=True)
        for child in _iter_children(node):
            if child is not node.iterable:
                _walk(owner, child, return_type)
        return
    if isinstance(node, THIRPrint):
        # A print statement is a flush position on the AST path (arg temps
        # hoist before the `std::cout` chain).
        for a in node.args:
            _walk(owner, a.expr, return_type, argtemp_ok=True)
        return
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)):
        # Both decl flavors are flush positions: the AST hoists a slot
        # init's arg temps BEFORE the decl/`__slot_N` line.
        if node.init is not None:
            _walk(owner, node.init, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRAssign):
        # The receiver eval is lowered with the default (temp-free) use, so
        # no argtemp exemption -- a temp reaching it is a lowering bug.
        if node.recv_eval is not None:
            _walk(owner, node.recv_eval, return_type)
        _walk(owner, node.target, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRSetItem):
        # The value AND the target's INDEX are flushable positions: the
        # write sits at a statement, so an index-call's arg temps hoist
        # before the setitem line exactly like the value's (the AST's
        # statement flush; threaded via the setitem arm's allow_temps
        # target use). The RECEIVER stays temp-free -- the lowering never
        # forwards flushability there, so a temp reaching it is a
        # lowering bug (the THIRAssign discipline).
        if isinstance(node.target, THIRSubscript):
            _walk(owner, node.target.receiver, return_type)
            _walk(owner, node.target.index, return_type, argtemp_ok=True)
        else:
            _walk(owner, node.target, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRSliceAssign):
        # Same flush semantics as a subscript write: the RHS is a flushable
        # position; the receiver and slice bounds never carry temps.
        _walk(owner, node.receiver, return_type)
        for b in (node.lower, node.upper, node.step):
            if b is not None:
                _walk(owner, b, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRFrameSlotWrite):
        # `name.emplace(value);` -- a statement, so the value's arg temps
        # hoist before the emplace line exactly like a THIRAssign value.
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRInplaceContainerOp):
        _walk(owner, node.receiver, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRReturn):
        if node.value is not None:
            _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRRaise):
        # `raise X(args)` flushes its ctor arg temps before the throw line, so
        # the args are a flush position exactly like a call's. (They are the
        # ctor's directly -- no intermediate THIRCtorCall node carries them.)
        _walk_arg_list(owner, node.args, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRIfExpr):
        # Ternary ARMS evaluate lazily: only a NON-DEFERRING temp may hoist
        # there (the AST's non-movable arm hoists it eagerly at the
        # statement; a deferring temp would need the conditional-region
        # render THIR does not carry). The CONDITION evaluates exactly once
        # unconditionally, so it inherits the enclosing flush right
        # (`Gate __tmp_1 = Gate(true);` before
        # `((check(__tmp_1)) ? (1) : (0))` -- the AST hoist).
        for child in _iter_children(node):
            if child is node.cond:
                _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                      eager_only=eager_only)
            else:
                _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                      eager_only=True)
        return
    if isinstance(node, THIRChainedCompareStmtExpr):
        # Operands 0 and 1 always evaluate; every later one sits behind a
        # passed compare, so inits[2:] are conditional-operand positions --
        # the ternary-arm rule, mirrored (the lowering-time cond_eager check
        # is the primary gate; this keeps the structural net symmetric).
        for idx, init in enumerate(node.inits):
            _walk(owner, init, return_type, argtemp_ok=argtemp_ok,
                  eager_only=(True if idx >= 2 else eager_only))
        return
    if (isinstance(node, THIRBinOp) and node.resolved is None
            and node.op in ("&&", "||")):
        # Short-circuit RHS: the conditional-operand rule (audited temps
        # defer through the emit region, unaudited would-defer ones are a
        # lowering bug). The LHS always evaluates: it keeps the plain
        # inherited right (its temp hoists at the statement or banks into
        # an enclosing region).
        for child in _iter_children(node):
            _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                  eager_only=(True if child is node.right else eager_only))
        return
    # The flush right propagates through EXPRESSION nesting (binop operands,
    # coerce wraps, ...): every sub-position of a flushable value expression
    # flushes at the same statement on the AST path. Statement nodes reset it
    # -- each statement handler above grants the right per position.
    for child in _iter_children(node):
        _walk(owner, child, return_type,
              argtemp_ok=argtemp_ok and isinstance(node, THIRExpr),
              eager_only=eager_only and isinstance(node, THIRExpr))


def validate_function(fn: THIRFunction) -> None:
    for stmt in fn.body:
        _walk(fn.name, stmt, fn.return_type)


def validate_constructor(ctor: THIRConstructor) -> None:
    owner = f"{ctor.record_name}.__init__"
    for mil in ctor.mil_inits:
        _walk(owner, mil.value)
        if (mil.value.form is Form.BORROW
                and _pointer_lifted_storage(mil.value.result_type)):
            _fail(owner, mil.value,
                  "BORROW value at a MIL cell (missing a borrow->storage "
                  "convert)")
    for base in ctor.base_inits:
        for arg in base.args:
            _walk(owner, arg)
    for stmt in ctor.body:
        _walk(owner, stmt)


def validate_stmts(owner: str, stmts, return_type=None) -> None:
    """Validate a statement block that is not a whole function body -- a
    frame nested def's member body, a seam's leaf block."""
    for stmt in stmts:
        _walk(owner, stmt, return_type)


def validate_resumable_body(owner: str, body: THIRResumableBody) -> None:
    """Same structural gate the ordinary bodies get, applied to a resumable
    frame's leaf tables.

    The frame skeleton holds the statements/expressions apart in id()-keyed
    maps instead of one linear body, so each seam is walked at the flush
    right its lowering grants. `return_type` stays out: a `return` in a
    resumable is a CFG terminator whose value renders through
    `return_values`, so no THIRReturn statement reaches a leaf, and the
    frame's declared return type is not the sink type of that value render
    (the scaffolding binds it to its own `__tpy_async_ret` slot).
    `nested_def_bodies` is validated where it is lowered, under the member's
    own return type."""
    for stmt in body.leaves.values():
        _walk(owner, stmt)
    for stmt in body.match_dispatches.values():
        _walk(owner, stmt)
    # Temp-free seams: their lowering never grants a flush right, so an
    # arg temp reaching one is a lowering bug.
    for expr in body.conds.values():
        _walk(owner, expr)
    for expr in body.return_values.values():
        _walk(owner, expr)
    for expr in body.yield_values.values():
        _walk(owner, expr)
    # The deferred-return recipe is consulted by the return scaffolding, which
    # renders the capture into an `auto* p = ...;` line with no flush point.
    for stmt in body.deferred_returns.values():
        _walk(owner, stmt)
    # Flushable seams: the sub-coro emplace, the await operand and the sync
    # for-head source are statement positions where the skeleton flushes
    # temps ahead of the line. Both maps below pool entries from several
    # populate sites of which exactly ONE grants temps -- `suspend_exprs`
    # holds the await operand (flushable) plus the bound-method receiver;
    # `region_exprs` the sync for-head iterable (flushable) plus the range
    # bounds, the with-manager and the async-for iterable. Pooling by
    # expression id() leaves no way to tell them apart here, so both are
    # walked at the looser right: a temp reaching one of the four temp-free
    # seams, where the skeleton has no flush point, is NOT caught.
    for args in body.await_args.values():
        # The tuple IS the emplace's arg list, so it is walked the way the
        # call-node arm walks a call's args.
        _walk_arg_list(owner, args, argtemp_ok=True)
    for expr in body.suspend_exprs.values():
        _walk(owner, expr, argtemp_ok=True)
    for expr in body.region_exprs.values():
        _walk(owner, expr, argtemp_ok=True)


def validate_simple_gen_body(owner: str, body: THIRSimpleGenBody) -> None:
    """The structural gate for a simple-generator peephole's leaf blocks.

    No `return_type`: the peephole shape has no return statement (the
    per-pull optional return is skeleton). The while condition is a flush
    position (the peephole restructures its loop head like the sync while);
    the for-head iterable and range bounds are not -- the skeleton renders
    them into the lambda's capture/header, which has no flush point."""
    validate_stmts(owner, body.init)
    validate_stmts(owner, body.pre_yield)
    validate_stmts(owner, body.post_yield)
    _walk(owner, body.yield_value)
    if body.cond is not None:
        _walk(owner, body.cond, argtemp_ok=True)
    if body.iterable is not None:
        _walk(owner, body.iterable)
    for a in body.range_args:
        _walk(owner, a)
