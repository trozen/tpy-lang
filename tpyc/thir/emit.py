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
    is_big_int_type, is_bytes_type, is_bytes_view_type, is_dict,
    is_float32_type, is_list,
    is_set, is_str_type, is_string_type, view_to_owned_conv,
)
from ..typesys import OptionalType, TupleType, UnionType, unwrap_qualifiers
from .nodes import (
    Form,
    PrintForm,
    THIRArgTemp,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRCoerce,
    THIRConstructor,
    THIRContainerLiteral,
    THIRContinue,
    THIRCtorCall,
    THIREnumMember,
    THIRExpr,
    THIRExprStmt,
    THIRFieldAccess,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRFunction,
    THIRIf,
    THIRIsNone,
    THIRIsinstance,
    THIRLiteral,
    THIRMethodCall,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRNarrowedRead,
    THIRNoOpStmt,
    THIROptionalPtrArg,
    THIRPrint,
    THIRPrintArg,
    THIRReturn,
    THIRSelf,
    THIRStmt,
    THIRStrAppend,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRTupleUnpack,
    THIRUnaryNot,
    THIRUnionArgLift,
    THIRVarDecl,
    THIRWhile,
)


class THIRCodeGenError(Exception):
    """A THIR node the slice's emitter does not handle reached emission.

    Lowering's eligibility gate should make this unreachable; it firing means
    the gate and the emitter disagree on the supported set.
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
    iter_counter: int = 0
    slot_counter: int = 0
    unpack_counter: int = 0
    rebind_slots: dict[str, int] = field(default_factory=dict)

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
        # eligibility gate admits finite literals only.
        rendered = repr(v)
        if is_float32_type(lit.result_type):
            return rendered + "f"
        return rendered
    if is_big_int_type(lit.result_type):
        # _gen_int_literal_value's BigInt arms. Arm 1 deliberately excludes
        # INT32_MIN (the AST avoids a `long` vs `int64_t` overload ambiguity
        # on macOS arm64); the gate's +-2^31 literal range keeps the
        # from_str arm unreachable, kept for the exact-mirror discipline.
        if -(2**31 - 1) <= v <= 2**31 - 1:
            return f"::tpy::BigInt({v})"
        if -2**63 <= v <= 2**63 - 1:
            return f"::tpy::BigInt(static_cast<int64_t>({v}LL))"
        return f'::tpy::BigInt::from_str("{v}")'
    return str(v)


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
    return f"{escape_cpp_name(e.callee)}({args})"


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
    if e.deref:
        inner = f"(*{inner})"
    if e.const_wrap:
        return f"::tpy::ptr_variant_to_const<{e.variant_cpp}>({inner})"
    return f"{e.variant_cpp}{{&({inner})}}"


def _emit_ctor_call(e: THIRCtorCall, state: _EmitState) -> str:
    # _gen_call's record-branch tail for a same-module plain record: the RAW
    # source name over bare scalar args.
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


def _emit_container_literal(e: THIRContainerLiteral, state: _EmitState) -> str:
    # Dispatch on the resolved container family, mirroring the scalar branches of
    # _gen_array_literal / _gen_dict_literal / _gen_set_literal. list/Array
    # brace-inits are consumed by the spelled decl type; dict/set spell their
    # runtime container constructor.
    t = unwrap_qualifiers(e.result_type)
    if is_dict(t):
        k_cpp = t.type_args[0].to_cpp()
        v_cpp = t.type_args[1].to_cpp()
        if not e.elements:
            return f"::tpy::ordered_map<{k_cpp}, {v_cpp}>()"
        braces = ", ".join(f"{{{_emit_expr(k, state)}, {_emit_expr(v, state)}}}"
                           for k, v in zip(e.elements, e.values))
        return f"::tpy::ordered_map<{k_cpp}, {v_cpp}>({{{braces}}})"
    if is_set(t):
        cpp_elem = t.type_args[0].to_cpp()
        if not e.elements:
            return f"::tpy::ordered_set<{cpp_elem}>()"
        elems = ", ".join(_emit_expr(x, state) for x in e.elements)
        return f"::tpy::ordered_set<{cpp_elem}>({{{elems}}})"
    # An empty list literal spells its type (the T*-assignment-ambiguity guard in
    # _gen_array_literal); an empty Array is gated out at eligibility.
    if not e.elements and is_list(t):
        return f"{t.to_cpp()}{{}}"
    return f"{{{', '.join(_emit_expr(x, state) for x in e.elements)}}}"


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
    # container branch. The index is a fixed-int value scalar (a runtime-BigInt
    # index is out of the scalar slice), so no `.to_fixed_check` narrow arises.
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
            fmt_parts.append("{}")  # format specs are gate-excluded
            decoded_fmt_parts.append("{}")
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
    raise THIRCodeGenError(
        f"unhandled THIRFormConvert: {type(t).__name__} {e.value.form}->{e.form}")


def _emit_expr(e: THIRExpr, state: _EmitState) -> str:
    if isinstance(e, THIRName):
        # `deref`: an F2 pointer-local read in a value position (a record call
        # arg) -- gen_expr_deref's `(*p)` indirect render.
        name = escape_cpp_name(e.name)
        return f"(*{name})" if e.deref else name
    if isinstance(e, THIRSelf):
        return "this"
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
    if isinstance(e, THIRUnaryNot):
        # Mirrors _gen_unaryop's `!` arm over a bool operand, whose truthiness
        # render is the plain value render. A pointer-repr Optional borrow
        # name's truthiness render is the bare `T*` (gen_truthy_expr), so the
        # same wrap serves `not p` too.
        return f"(!({_emit_expr(e.operand, state)}))"
    if isinstance(e, THIRIsNone):
        op = "!=" if e.negate else "=="
        return f"({_emit_expr(e.operand, state)} {op} nullptr)"
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
    if isinstance(e, THIREnumMember):
        return e.cpp
    if isinstance(e, THIRContainerLiteral):
        return _emit_container_literal(e, state)
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


def _emit_while(out: TextIO, stmt: THIRWhile, indent_level: int, state: _EmitState) -> None:
    # The `// while ...:` comment is emitted by the caller (_emit_stmts).
    indent = INDENT * indent_level
    out.write(f"{indent}while ({_emit_expr(stmt.condition, state)}) {{\n")
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")


def _emit_for_range(out: TextIO, stmt: THIRForRange, indent_level: int,
                    state: _EmitState) -> None:
    # Mirrors _gen_range_counter_loop (plus_one / non-hoisted branch): grab the
    # loop index BEFORE the body so nested loops number after this one (the AST
    # grabs `n` at the top of _gen_range_counter_loop). Non-literal bounds are
    # captured once into `__start_N`/`__stop_N` temps -- Python's range() reads
    # its args at call time, but the C++ condition re-reads each iteration.
    indent = INDENT * indent_level
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
    out.write(f"{indent}for ({cpp_elem} {var} = {start_cpp}; "
              f"{var} < {stop_cpp}; ++{var}) {{\n")
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")


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
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.comments.trailing(out, stmt.body, inner)
    out.write(f"{indent}}}\n")


def _emit_stmt(out: TextIO, stmt: THIRStmt, indent_level: int, state: _EmitState) -> None:
    indent = INDENT * indent_level
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
            out.write(f"{indent}{stmt.resolved_type.to_cpp()} {name};\n")
        else:
            # Render before flushing: the init may register arg temps, whose
            # decls the AST flushes between the source comment and the
            # statement line (gen_stmt's single flush point).
            cpp_type = stmt.resolved_type.to_cpp()
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
            # Value renders first (its arg temps flush before the line);
            # targets are names/field lvalues that never register temps.
            target_cpp = _emit_expr(stmt.target, state)
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}{target_cpp} = {value_cpp};\n")
    elif isinstance(stmt, THIRStrAppend):
        # `t += v;` -- the str in-place append (the `+=` statement and the
        # `x = x + y` peephole share the emit).
        out.write(f"{indent}{escape_cpp_name(stmt.target)} += "
                  f"{_emit_expr(stmt.value, state)};\n")
    elif isinstance(stmt, THIRNarrowAlias):
        # The isinstance-narrowing extraction (F4 U3) -- mirrors
        # _emit_isinstance_extractions' variant arm (VariantAccess.get_by_type
        # with lvalue=True: the ptr-variant deref carries no outer parens).
        qualifier = "const auto&" if stmt.const_ref else "auto&"
        deref = "*" if stmt.is_ptr_variant else ""
        out.write(f"{indent}{qualifier} {stmt.alias} = {deref}"
                  f"std::get<{stmt.member_cpp}>({stmt.variant_cpp});\n")
    elif isinstance(stmt, THIRAssert):
        # Mirrors _gen_assert's non-constant, non-lazy-message arm; the
        # narrowing alias (if any) follows as its own THIRNarrowAlias
        # statement. The message escape mirrors _gen_assert_throw.
        if stmt.message is None:
            throw = "::tpy::raise_assertion_error()"
        else:
            msg = stmt.message.replace("\\", "\\\\").replace('"', '\\"')
            throw = f'::tpy::raise_assertion_error("{msg}")'
        out.write(f"{indent}if (!({_emit_expr(stmt.condition, state)})) {throw};\n")
    elif isinstance(stmt, THIRReturn):
        if stmt.value is None:
            out.write(f"{indent}return;\n")
        else:
            value_cpp = _emit_expr(stmt.value, state)
            state.temps.flush(out, indent)
            out.write(f"{indent}return {value_cpp};\n")
    elif isinstance(stmt, THIRIf):
        _emit_if(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRWhile):
        _emit_while(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRForRange):
        _emit_for_range(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRForEach):
        _emit_for_each(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRPrint):
        _emit_print(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRExprStmt):
        expr_cpp = _emit_expr(stmt.expr, state)
        state.temps.flush(out, indent)
        out.write(f"{indent}{expr_cpp};\n")
    elif isinstance(stmt, THIRTupleUnpack):
        # Mirrors _gen_tuple_unpack's slice arm: a bare-name loop-shadow
        # source binds `const auto&` (no owned/ref elements), each non-discard
        # target declares a fresh value-scalar local.
        tmp = f"__tup_{state.next_unpack()}"
        out.write(f"{indent}const auto& {tmp} = "
                  f"{escape_cpp_name(stmt.source)};\n")
        for i, (name, cpp) in enumerate(zip(stmt.targets, stmt.target_cpps)):
            if name is None:
                continue
            out.write(f"{indent}{cpp} {escape_cpp_name(name)} = "
                      f"std::get<{i}>({tmp});\n")
    elif isinstance(stmt, THIRBreak):
        out.write(f"{indent}break;\n")
    elif isinstance(stmt, THIRContinue):
        out.write(f"{indent}continue;\n")
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
                   temps: TempSink | None = None) -> None:
    """Emit `fn`'s body statements (no signature, no braces) at `indent_level`.

    `temps` is the `__tmp_N` sink -- the codegen seam passes a CtxTempSink so
    THIR bodies draw from the module-cumulative `ctx.temps` counter; the
    default is a fresh local sink (standalone/unit callers)."""
    _emit_stmts(out, fn.body, indent_level,
                _EmitState(comments or _NO_COMMENTS, temps=temps or TempSink()))


def emit_thir_constructor_tail(out: TextIO, ctor: THIRConstructor,
                               *, comments: CommentSink | None = None,
                               temps: TempSink | None = None) -> None:
    """Emit a constructor's member-init-list + body tail (the ` : f(v)... {}` that
    follows the signature). The THIR counterpart of gen_record_decl's AST MIL+body
    emit: the signature is written by the AST path before this is called (the M1
    precedent -- signatures stay on the AST path). Byte-identical to that path's
    tail. M3a is pure-MIL, so `body` is empty and this emits ` {}` (or
    ` : inits {}`). MIL / base-init cells have no flush point, so arg temps
    never lower there (gate + validator enforced); the body shares the
    statement machinery and its sink."""
    state = _EmitState(comments or _NO_COMMENTS, temps=temps or TempSink())
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
