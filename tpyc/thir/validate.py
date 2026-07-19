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
    type).
`THIRBytesLiteral`'s render verdict rides the `form` tag (the bespoke `owned`
flag was folded in with U2's opening), so bytes literals sit on the validated
axis like every other expression.
"""

from __future__ import annotations

import dataclasses

from ..codegen_cpp.forms import is_ptr_variant_union
from ..type_def_registry import (
    is_basic_slice_type, is_bytes_view_type, is_slice_type, is_span,
    is_str_view_type,
)
from ..typesys import (
    AnyType, NominalType, OptionalType, OwnType, PtrType, TupleType,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .nodes import (
    Form, THIRArgTemp, THIRAssign, THIRCall, THIRCoerce, THIRConstructor,
    THIRCtorCall, THIRErrorReturnBind, THIRErrorReturnDiscard,
    THIRErrorReturnUnwrap, THIRExprStmt, THIRFieldAccess, THIRFormConvert,
    THIRForIterProto, THIRFunction, THIRMethodCall, THIRNode,
    THIRInplaceContainerOp,
    THIRPrint, THIRRaise, THIRReturn, THIRSetItem, THIRSliceAssign,
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
        # move is the whole operation). Only a move-free same-form convert is dead.
        if (node.form is node.value.form
                and node.result_type == node.value.result_type
                and not node.move):
            _fail(owner, node,
                  f"no-op form convert (form={node.form.name}, "
                  f"type={node.result_type})")
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
            or is_slice_type(rt) or is_basic_slice_type(rt))
        if node.form is not node.expr.form and not (
                (view_target and node.form is Form.BORROW)
                or (value_target and node.form is Form.VALUE)):
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
    # borrow value is legal. An Own[union] return (a non-Nominal wrapped
    # type) would need its own conversion arm, and this check must keep
    # catching a missing one.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), NominalType)
            and not t.wrapped.is_value_type()):
        return True
    if not t.is_value_type():
        return True
    if isinstance(t, TupleType) and t.has_pointer_repr_element():
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
                and not _borrow_legal_return(return_type)):
            _fail(owner, stmt,
                  f"BORROW return value for value-typed return {return_type}")


def _walk(owner: str, node: THIRNode, return_type=None, *,
          argtemp_ok: bool = False) -> None:
    """`argtemp_ok` marks the value expression of a flushable statement
    (expr stmt / var-decl init / assign value / return value / print arg)
    -- the only
    region where a THIRArgTemp may appear, and there only as a direct
    free-call, method-call, or ctor-call arg (the ctor face only for a
    mutated ref slot). Anywhere else (a condition, an iterable, a MIL cell,
    a non-call operand) a temp has no flush point on the AST path -- a
    while-condition hoist is the stale-snapshot miscompile -- so reaching
    one is a lowering bug."""
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
            _walk(owner, node.receiver, return_type)
        for a in node.args:
            if isinstance(a, THIRArgTemp):
                if not argtemp_ok:
                    _fail(owner, a, "THIRArgTemp under a non-flushable "
                                    "statement position")
                # A temp's SOURCE ctor flushes its own arg temps at the SAME
                # statement point (`describe(Canvas(Circle(5)))` -- __tmp_1
                # innermost-first, then __tmp_2), so nested temps under the
                # init are legal when the outer temp is flushable.
                _walk(owner, a.init, return_type, argtemp_ok=argtemp_ok)
            elif (isinstance(a, THIRUnionArgLift)
                    and a.temp_cpp is not None):
                if not argtemp_ok:
                    _fail(owner, a, "temp-bearing THIRUnionArgLift under a "
                                    "non-flushable statement position")
                if a.value is not None:
                    _walk(owner, a.value, return_type)
            else:
                _walk(owner, a, return_type)  # temps never nest deeper
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
    if isinstance(node, THIRVarDecl):
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
        # The value is a flushable position (like an assign value); the
        # target subscript's receiver/index never carry temps.
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
    if isinstance(node, THIRInplaceContainerOp):
        _walk(owner, node.receiver, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRReturn):
        if node.value is not None:
            _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRRaise):
        # `raise X(args)` flushes its ctor arg temps before the throw line
        # (the mutated-ref-slot record rvalue), so its args are a flush
        # position -- mirror the call-node arg loop (the args are the ctor's
        # directly, with no intermediate THIRCtorCall node to carry them).
        for a in node.args:
            if isinstance(a, THIRArgTemp):
                _walk(owner, a.init, return_type, argtemp_ok=True)
            elif (isinstance(a, THIRUnionArgLift) and a.temp_cpp is not None):
                if a.value is not None:
                    _walk(owner, a.value, return_type)
            else:
                _walk(owner, a, return_type)
        return
    for child in _iter_children(node):
        _walk(owner, child, return_type)


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
