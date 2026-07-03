"""AST + sema -> THIR lowering.

`lower_function` converts one analyzed `TpyFunction` to a `THIRFunction`,
returning None when the function falls outside the supported slice (the
eligibility gate). Lowering reads the analyzer here so codegen never has to;
every fact codegen consumes is materialized onto the returned THIR nodes.

Eligible slice: non-generic, non-generator/async, plain-linkage free functions
-- and methods of same-module non-generic records: instance methods and
property getters/setters (`self` as an F1-record `this` receiver; dunders are
plain instance methods here) plus static methods (receiver-less, lowered like
free functions) -- whose params/locals/return are fixed-width-int
scalars, `Char`, or str/bytes-slice values (owned `str` / `StrView`, owned
`bytes` / `BytesView`; the view/owned
duality is sema-resolved pre-lowering, and the owned-sink copy is an explicit
THIRFormConvert; S4 adds str subscript/slice/iteration -- Char reads,
`::tpy::str_slice` views, Char loop vars; S5 adds owned-str container
elements/keys/values -- dict[str] reads, str-element literals with the
per-slot owned copy, str loop vars; S6 adds bytes values -- target-typed
literal renders, `BytesPrinter` print args, `::tpy::bytes_copy` owned sinks --
and the bytes tail: `::tpy::bytes_getitem` subscripts, `::tpy::bytes_slice`
views, UInt8 loop vars, `::tpy::bytes_concat` concat/aug-assign),
plus the F1/F2 non-value record
forms for locals/returns, with
straight-line bodies (var-decl / assign / return) over names and literals.
Anything else -> None (stays on the AST codegen path). The gate is the safety
boundary: it must reject every construct the emitter cannot reproduce byte-
for-byte.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..parse.nodes import (
    FSTRING_CONV_NONE,
    FunctionLinkage,
    TpyArrayLiteral,
    TpyAssign,
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyChainedCompare,
    TpyCoerce,
    TpyDictLiteral,
    TpyExpr,
    TpyExprStmt,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyForEach,
    TpyFString,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyMethodCall,
    TpyModule,
    TpyName,
    TpyNoneLiteral,
    TpyPassStmt,
    TpyReturn,
    TpySetLiteral,
    TpySlice,
    TpyStmt,
    TpyStrLiteral,
    TpySubscript,
    TpyUnaryOp,
    TpyVarDecl,
    TpyWhile,
    VarLinkage,
    expr_reads_self_field,
    is_base_init_call,
    is_docstring,
)
from ..typesys import (
    BYTES_FAMILY, CHAR, CONST_PARAMS_METHODS, FloatLiteralType, IntLiteralType,
    LiteralType,
    NominalType,
    OptionalType, OwnType, PendingViewType, ReadonlyType, STR_FAMILY, TpyType,
    TupleType,
    TypeParamRef, UnionType, ValueForm, VoidType, is_float_type,
    is_void_like_type,
    resolve_int_literals,
    unwrap_optional_own, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import (
    int_traits_of, is_array, is_basic_slice_type, is_big_int_type,
    is_bool_type, is_bytes_type, is_bytes_view_type, is_char_type,
    is_dict, is_fixed_int_type, is_float32_type, is_list, is_set,
    is_slice_type, is_str_type,
    is_str_view_type, is_string_type,
)
from ..coercions import CoercionContext
from ..codegen_cpp.type_resolution import resolve_stmt_binding_type
from ..codegen_cpp.types import resolve_pending_container
from ..modules.type_resolution import is_native_iterable
from ..codegen_cpp.forms import (
    LocalBinding, classify_local_binding, is_ptr_variant_union,
    is_storage_tuple_alias_decl,
    reads_storage_form_optional,
)
from ..value_category import is_rvalue_source
from ..codegen_cpp.context import escape_cpp_name
from .validate import validate_constructor, validate_function
# The chained-compare inline-vs-statement-expr trigger, imported (not mirrored)
# so the eligibility gate can never drift from the emit decision.
from ..codegen_cpp.expressions import ExpressionGenerator
from .nodes import (
    Form,
    PrintForm,
    THIRAssign,
    THIRBaseInit,
    THIRBinOp,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRCoerce,
    THIRConstructor,
    THIRContainerLiteral,
    THIRExpr,
    THIRExprStmt,
    THIRFieldAccess,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFString,
    THIRFStringArg,
    THIRFunction,
    THIRFunctionLayout,
    THIRIf,
    THIRLiteral,
    THIRMethodCall,
    THIRMilInit,
    THIRModule,
    THIRName,
    THIRNoOpStmt,
    THIRParam,
    THIRPrint,
    THIRPrintArg,
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

# Arithmetic operators whose dunders carry a `@cpp_template` (`add_check`, ...).
# NB the parser emits true-division as op `div`, not `/`, so the `/` token here
# is inert -- truediv stays on the AST path (see TODO: decide enable-or-drop).
# `in`/`is`/bitwise take other emit paths, out of the slice.
_ARITH_OPS = frozenset({"+", "-", "*", "/", "//", "%"})
# Comparison operators -- `<`/`==` dunders carry a `{self} OP {0}` template (the
# derived ones emit as a bare C++ operator); the result is bool. Admitted both
# as `if`/`while` conditions and as values (`x = a < b`).
_COMPARE_OPS = frozenset({"<", "<=", ">", ">=", "==", "!="})
# Logical and/or (the parser folds `a and b` to TpyBinOp("&&")). Admitted only
# with a bool result over bool operands, where the AST emits the bare C++
# operator (`(l && r)`); the non-bool Python value semantics (`x or default` ->
# temp + ternary via _gen_logical_value) stay on the AST path.
_LOGICAL_OPS = frozenset({"&&", "||"})
# Literal-into-typed-slot coercions the slice reproduces, both pass-throughs on
# the C++ side (the inner literal renders directly in the slot's type): a literal
# into a fixed-int slot, and a float literal into a double `float` slot.
_INT_LIT_COERCION = "int_literal_to_fixed_int"
_FLOAT_LIT_COERCION = "float_literal_to_float"
# `String` (a concat result) into a `str` slot: identity in EVERY position (the
# Coercion carries no codegen lambda -- both sides spell std::string, and a str
# ARG slot's std::string_view converts implicitly), so it lowers as a
# THIRCoerce passthrough. The other str-family coercions are position-dependent
# (materializing at some sinks) -- see _coerce_disposition.
_STRING_TO_STR_COERCION = "string_to_str"
# Str-family coercions that are identity in EVERY position (no codegen lambda):
# both sides of string_to_str spell std::string; the two *_to_strview arms feed
# a std::string_view slot every source converts into implicitly.
_IDENTITY_STR_COERCIONS = frozenset(
    {_STRING_TO_STR_COERCION, "str_to_strview", "string_to_strview"})


def _coerce_disposition(e: TpyCoerce) -> 'str | None':
    """'identity' (emit passthrough), 'materialize' (`std::string(x)`, lowered
    to the S1 view->owned THIRFormConvert), or None (outside the slice).

    Mirrors the tpyc/coercions.py codegen lambdas exactly, reading the same
    facts off the node: `strview_to_str` is identity at a plain ARG slot (a
    `str` param spells std::string_view) and materializes at INIT/ASSIGN/
    RETURN; an `Own[...]` ARG slot is rejected -- the surrounding gen_call_arg
    auto-move cascade is its own deferred frontier. `str_to_string`
    materializes only at ARG for a non-literal source; a NUL-free literal is
    const char[N], binding const std::string& directly (the lambda's
    startswith('"') token check made structural: cpp_string_literal_expr emits
    the bare-quote form exactly when the value is NUL-free).
    `strview_to_string` (const std::string& slot / owned String target)
    materializes in every position. The Optional and Char arms have their own
    renders -> AST path."""
    name = e.coercion.name
    if name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION):
        return "identity"
    if name in _IDENTITY_STR_COERCIONS:
        return "identity"
    if isinstance(e.expected_type, OwnType):
        return None
    if name == "strview_to_str":
        return ("identity" if e.context_kind == CoercionContext.ARG
                else "materialize")
    if name == "str_to_string":
        if e.context_kind != CoercionContext.ARG:
            return "identity"
        if isinstance(e.expr, TpyStrLiteral) and "\x00" not in e.expr.value:
            return "identity"
        return "materialize"
    if name == "strview_to_string":
        return "materialize"
    return None


def _eligible_scalar(t: TpyType | None) -> bool:
    """A type the emitter can render and reason about without form facts.

    Fixed-width ints, `bool`, and double `float` (-> `double`): borrow/storage
    form never arises and the C++ spelling comes straight from `TpyType.to_cpp()`.
    Float32 is excluded -- its literals need a `f` suffix the slice does not emit.
    """
    return t is not None and (is_fixed_int_type(t) or is_bool_type(t)
                              or (is_float_type(t) and not is_float32_type(t)))


def _eligible_value_union(t: TpyType | None) -> 'UnionType | None':
    """The F4 U1 slice: a value-form union of eligible scalar members
    (`Int32 | Float64 [| None]`) -- `std::variant<...>` with no borrow/storage
    duality, so reads/writes/returns/same-type args render bare (the variant
    converting ctor does the work) and a `None` source renders
    `std::monostate{}`. Unions with str/view members (form-relevant per slot),
    Char members (target-typed literal renders), records (pointer-variant,
    U2), or a recursive-alias wrapper ride later F4 cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return None
    if not all(_eligible_scalar(m) or is_void_like_type(m) for m in t.members):
        return None
    return t


def _eligible_ptr_union(t: TpyType | None, analyzer) -> 'UnionType | None':
    """The F4 U2 slice: a pointer-repr union of F1-record members (`A | B` ->
    borrow `std::variant<A*, B*>` / storage `std::variant<A, B>`). Members must
    be F1-renderable (same-module plain records) so both C++ spellings stay off
    cross-module/native/generic recursion. A None member (the monostate write
    arms), recursive-alias wrappers, and protocol unions ride later cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, UnionType) and is_ptr_variant_union(t)):
        return None
    if not all(_f1_record(m, analyzer) for m in t.members):
        return None
    return t


def _ptr_union_source_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer,
                         u: 'UnionType', *, allow_field: bool) -> bool:
    """A source expression for a pointer-variant local decl/reseat: a bare name
    whose binding is the SAME union (a borrow-form copy, rendered bare), or --
    when `allow_field` -- a value-variant field lvalue off an F1-record
    receiver (lifts via `to_[const_]ptr_variant`). Field sources are gated to
    single-assignment locals: a reseat's const verdict comes from its own
    receiver, a mixed-const reseat chain the slice does not reproduce."""
    if isinstance(e, TpyName):
        bt = declared.get(e.name)
        if bt is None:
            return False
        bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
        return bt == u and _expr_eligible(e, declared, analyzer)
    if allow_field and isinstance(e, TpyFieldAccess):
        if not _field_receiver_ok(e, declared, analyzer):
            return False
        ft = analyzer.get_expr_type(e)
        ft = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
              if ft is not None else None)
        return ft == u
    return False


def _union_binding_divergent(e: 'TpyName', locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A union-declared name whose read type is NOT that union: sema's
    assignment narrowing retyped the read to a member (`x: I | F = k; v = x`
    reads `x` as Int32), but the AST renders the bare variant name into the
    member-typed sink -- invalid C++ without a `std::get` (a pre-existing AST
    miscompile, see BUGS.md). No green corpus case can exercise it, so any
    narrowing-divergent union read stays on the AST path."""
    bt = locals_.get(e.name)
    if bt is None:
        return False
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    if not isinstance(bt, UnionType):
        return False
    rt = analyzer.get_expr_type(e)
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    return rt != bt


def _folded_neg_int_literal(e: TpyExpr, analyzer) -> int | None:
    """The negated value of a unary-minus int literal (`-3`), or None.

    Mirrors `_gen_unaryop`'s literal-negation fold exactly (op `-`, a
    `TpyIntLiteral` operand of `IntLiteralType`): the AST renders
    `_gen_int_literal_value(-v, target)`, which is the bare `-v` token for
    every value in the +-int32 literal range the slice admits (targets are
    fixed-int slots the literal provably fits, or target-less positions) --
    the same `str(v)` a `THIRLiteral` emits. Values whose negation falls
    outside the range return None (the wide-literal suffix/cast renders)."""
    if not (isinstance(e, TpyUnaryOp) and e.op == "-"
            and isinstance(e.operand, TpyIntLiteral)
            and isinstance(analyzer.get_expr_type(e.operand), IntLiteralType)):
        return None
    v = -e.operand.value
    return v if -2**31 <= v <= 2**31 - 1 else None


def _resolved_scalar(t: TpyType | None, analyzer) -> bool:
    """`_eligible_scalar` over a type that may still be an IntLiteralType: a
    literal-seeded container leaves IntLiteral element types on its use sites
    (the sema-resolved method fi's slots, a `pop`/subscript result, print args of
    its loop var) -- the AST path resolves these through TypeResolver/default-int
    at emit; the emitted value is the same bare literal either way. Readonly/Ref
    wrappers are peeled first: a scalar coerced into a `readonly[K]` slot carries
    the wrapper on its expr type, and a readonly scalar is representationally
    the same C++ value.

    Companion convention: a RECEIVER gate reads the declared/`locals_` BINDING
    type, never `get_expr_type` on the name -- a literal-seeded local's use sites
    carry the pre-resolution pending container type (see `_is_len_call`,
    `_method_call_eligible`, `_container_subscript_value_read`,
    `_for_each_container_eligible`)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return _eligible_scalar(
        resolve_int_literals(t, analyzer.ctx.default_int_for_literal))


def _resolve_pending_view(t: TpyType | None, analyzer) -> TpyType | None:
    """A `PendingStrType`/`PendingBytesType` resolved to its concrete view/owned
    type through the family's ViewVarInfo (sema's usage resolution is FINAL
    pre-lowering), else None. Mirrors codegen's `_resolve_view_storage`: no
    registry entry (or an unresolved one) falls back to the owned type."""
    if not isinstance(t, PendingViewType):
        return None
    info = analyzer.ctx.view_vars(t.family).get(t.var_id)
    return (info.resolved_type if info is not None and info.resolved_type
            else t.family.owned_type)


def _resolved_str_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The sema-RESOLVED str-slice type -- owned `str` (`std::string` storage /
    `std::string_view` param) or `StrView` (`std::string_view`) -- or None
    outside the slice. A str local's binding type stays `PendingStrType` on the
    AST/sema side; resolve it like `_resolve_pending_view` does. `String`,
    `Char`, `Literal[str]`-annotated bindings, and the bytes family stay on the
    AST path (later cells)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return _resolve_pending_view(t, analyzer) if t.family is STR_FAMILY else None
    if isinstance(t, NominalType) and (is_str_type(t) or is_str_view_type(t)):
        return t
    return None


def _resolved_bytes_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The bytes twin of `_resolved_str_value`: the sema-RESOLVED bytes-slice
    type -- owned `bytes` (`std::vector<uint8_t>` storage / `std::span<const
    uint8_t>` param) or `BytesView` (span) -- or None outside the slice. A
    bytes local's binding stays `PendingBytesType`; resolve it through the
    family's ViewVarInfo. `bytearray` (a reference type, different axis) and
    `Literal[bytes]` bindings stay on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return (_resolve_pending_view(t, analyzer)
                if t.family is BYTES_FAMILY else None)
    if isinstance(t, NominalType) and (is_bytes_type(t) or is_bytes_view_type(t)):
        return t
    return None


def _resolved_viewfam_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The resolved str- OR bytes-family slice value -- the two view families
    share the slice/iteration receiver shapes and emit machinery -- or None."""
    st = _resolved_str_value(t, analyzer)
    return st if st is not None else _resolved_bytes_value(t, analyzer)


def _bytes_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-slice comparison operand: a bytes literal (rendered OWNED --
    `_comparison_targets` threads no target for bytes, so the AST's
    `gen_expr(lit, None)` takes the owned arm) or a bytes/BytesView value.
    Guards the compare arm's operand pin -- see `_binop_eligible`."""
    if isinstance(e, TpyBytesLiteral):
        return True
    return _resolved_bytes_value(t, analyzer) is not None


def _str_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A str-slice comparison operand: a str literal (its expr type is
    `LiteralType[str]`, but the const char[N] emit is position-independent),
    a str/StrView value, or a `String` value (a concat result -- std::string
    takes the same compare templates / bare operators, rendered bare). Guards
    the compare arm's operand pin -- see `_binop_eligible`."""
    if isinstance(e, TpyStrLiteral):
        return True
    return (_resolved_str_value(t, analyzer) is not None
            or _is_string_owned(t))


def _is_string_owned(t: TpyType | None) -> bool:
    """A `tpy.String` value -- the owned std::string type a str-family concat
    produces (and the resulting type of a local bound to one). Kept separate
    from `_resolved_str_value` deliberately: String PARAMS spell
    `const std::string&` in the signature, a shape the S1 param emit does not
    reproduce, so the param/return/call gates must keep rejecting String while
    the concat slice admits it for operands, locals, len and print args."""
    if t is None:
        return False
    return is_string_type(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))


def _str_concat_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A str-concat operand the slice renders bare into the resolved dunder's
    template: a str literal (const char[N]), a str/StrView value (param, local,
    or owned-str call result), or a `String` value (a concat-result local /
    nested concat). A `Char` operand's overload wraps it in `char_to_str` with
    its own operand render -- out of the slice (S4 introduces Char values)."""
    if isinstance(e, TpyStrLiteral):
        return True
    return (_resolved_str_value(t, analyzer) is not None
            or _is_string_owned(t))


def _bytes_concat_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-concat operand rendered bare into `::tpy::bytes_concat(l, r)`:
    a bytes literal (rendered OWNED -- the resolved `__add__` overload's
    receiver/param target is owned `bytes`, never `BytesView`, so the AST's
    target-threaded render takes the owned arm) or a bytes/BytesView value
    (param, local, nested concat, or owned-bytes call result -- names and
    spans render bare; the vector->span conversion at the span params is
    implicit). A `bytearray` operand also resolves to the native
    `bytes_concat` dunder but is not a bytes-slice value -> AST path."""
    if isinstance(e, TpyBytesLiteral):
        return True
    return _resolved_bytes_value(t, analyzer) is not None


def _peel_coerce(e: TpyExpr) -> TpyExpr:
    """The expression under any stack of TpyCoerce wrappers."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e


def _str_self_append_rhs(target_name: str, value: TpyExpr) -> 'TpyExpr | None':
    """The `x = x + y` self-append trigger, mirroring the AST's
    `_try_str_inplace_append` shape test exactly: peel any TpyCoerce wrappers,
    then match `TpyBinOp("+", TpyName(target), rhs)`. Returns the rhs (`y`) the
    peephole appends, or None when the shape does not match (the assignment
    then lowers as a plain reassign). The TARGET-type half of the AST condition
    is checked by the caller via `_owned_str_append_target`."""
    inner = _peel_coerce(value)
    if (isinstance(inner, TpyBinOp) and inner.op == "+"
            and isinstance(inner.left, TpyName)
            and inner.left.name == target_name):
        return inner.right
    return None


def _owned_str_append_target(t: TpyType | None, analyzer) -> bool:
    """The target-type half of the AST's in-place-append conditions (the str
    `+=` branch and `_try_str_inplace_append`): the binding is owned-str-family
    -- `str`, `String`, or a `PendingStrType`. The AST admits ANY pending
    binding; here the pending must RESOLVE owned, which is equivalent for every
    shape that reaches lowering (the very reassign/aug-assign being checked
    forces the owned resolution) and keeps the emitted `t += v;` honest."""
    st = _resolved_str_value(t, analyzer)
    if st is not None:
        return is_str_type(st)
    return _is_string_owned(t)


def _str_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The C++ shape of a str-slice NAME read -- mirrors the AST's
    `_is_str_view_source`: a `StrView`-resolved binding and a `str`-typed param
    (the signature spells `std::string_view`) are view/BORROW; an owned local is
    `std::string` (STORAGE). The owned-sink copy (`std::string(x)` at a decl
    init / return) fires only on a BORROW source; a str literal is const
    char[N] (implicitly convertible both ways) and stays VALUE, never wrapped."""
    if is_str_view_type(resolved) or name in param_names:
        return Form.BORROW
    return Form.STORAGE


def _bytes_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The bytes twin of `_str_name_form`, mirroring the AST's
    `_is_bytes_view_source`: a `BytesView`-resolved binding and a `bytes`-typed
    param (the signature spells `std::span<const uint8_t>`) are view/BORROW --
    they drive the owned-sink `::tpy::bytes_copy(x)` -- while an owned local is
    `std::vector<uint8_t>` (STORAGE)."""
    if is_bytes_view_type(resolved) or name in param_names:
        return Form.BORROW
    return Form.STORAGE


def _eligible_char(t: TpyType | None) -> bool:
    """A `Char` value (C++ `char`): value-scalar-like for the shapes this slice
    routes -- str subscript results, str-iteration loop vars, params/returns,
    compare operands, print args (streamed raw; Char has no int_traits, so no
    int8 cast arises). Kept out of `_eligible_scalar` deliberately: a str
    literal in a Char-typed slot renders as a target-typed C++ char literal
    (`'x'`), which the scalar positions do not thread -- each Char position
    gates its literal shape explicitly (single-char literals route at compare
    operands, annotated decl inits, and Char-slot call args; the reassign /
    return guards stay as defensive rejects -- sema type-errors those)."""
    if t is None:
        return False
    return is_char_type(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))


def _slice_object_type(t: TpyType | None) -> bool:
    """A slice-object value (`basic_slice` -> `::tpy::BasicSlice`, `slice` ->
    `::tpy::Slice`): a by-value C++ type whose only admitted use is as a str
    subscript index (`s[sl]`, rendered bare into the resolved slice
    `__getitem__` template). Admitted as a param and as a ctor-initialized
    local (`sl = basic_slice(1, 3)`, the `_slice_ctor_call_eligible` shape)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return is_basic_slice_type(t) or is_slice_type(t)


def _char_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A Char comparison operand: a Char-typed value, or a single-char str
    literal (the AST threads target=CHAR into its render -> `'x'`,
    `_comparison_targets`' char arm). A multi-char literal never renders as a
    char literal -> AST path. The str-pair arm is checked first, so a
    literal-vs-literal compare stays a plain string compare (no char target
    arises without a Char-typed operand)."""
    if isinstance(e, TpyStrLiteral):
        return len(e.value) == 1
    return _eligible_char(t)


def _eligible_return(t: TpyType | None, analyzer) -> bool:
    return (t is None or isinstance(t, VoidType) or _eligible_scalar(t)
            or _eligible_char(t)
            or _resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None
            or _storage_optional_return_type(t, analyzer) is not None
            or _borrow_tuple_return_type(t, analyzer) is not None
            or _eligible_value_union(t) is not None
            or _eligible_ptr_union(t, analyzer) is not None)


# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "hoisted", "move_through",
                 "ret_storage_opt", "ret_borrow_tuple", "ret_str", "ret_bytes",
                 "ret_char", "ret_union", "ret_ptr_union", "param_names")

    def __init__(self, func: TpyFunction, analyzer) -> None:
        # Param names, for gates that must tell a param from a local (a str
        # param's aug-assign would need the owned-copy prologue -- see
        # _str_aug_append_ok).
        self.param_names = {n for n, _t in func.params}
        scan = analyzer.function_scan_results.get(id(func))
        global_decls = analyzer.function_global_decls.get(id(func), set())
        self.reassigned = (scan.reassigned - global_decls) if scan else set()
        # F2d: the subset reassigned with an rvalue source (the rebind-slot
        # trigger -- mirrors codegen's `ctx.rvalue_reassigned_vars` seeding).
        self.rvalue_reassigned = (
            (scan.rvalue_reassigned - global_decls) if scan else set())
        self.hoisted = analyzer.function_hoisted_vars.get(id(func), set())
        self.move_through = analyzer.function_move_through_vars.get(id(func), set())
        # F2c: the function's storage-form Optional[F1-record] return slot, if any
        # (`Own[T] | None` -> `std::optional<T>`), so a `return None` /
        # `return <borrow T*>` lowers to `std::nullopt` / `ptr_to_optional`. None
        # for every other return type (the value-scalar/pointer-repr paths).
        rt = func.return_type if isinstance(func.return_type, TpyType) else None
        self.ret_storage_opt = _storage_optional_return_type(rt, analyzer)
        # F3: the function's borrow-form pointer-repr tuple return slot, if any
        # (`tuple[..., Ref]` -> `std::tuple<..., T*>`), so a `return <storage tuple
        # lvalue>` lifts via `tuple_to_pointer`. None for every other return type.
        self.ret_borrow_tuple = _borrow_tuple_return_type(rt, analyzer)
        # S1 str slice: the resolved str-family return type (owned `str` or
        # `StrView`), so a `return <view-form source>` into an owned `std::string`
        # return copies via the view->owned THIRFormConvert. None otherwise.
        self.ret_str = _resolved_str_value(rt, analyzer)
        # S6: the resolved bytes-family return type -- an owned `bytes` return
        # copies a view-form source via `::tpy::bytes_copy`; a `BytesView`
        # return renders a literal in its span form.
        self.ret_bytes = _resolved_bytes_value(rt, analyzer)
        # S4: a Char return slot -- `return "x"` renders a target-typed char
        # literal (`'x'`) on the AST path, a shape the return arm rejects.
        self.ret_char = _eligible_char(rt)
        # F4 U1: a value-union return slot -- `return None` renders
        # `std::monostate{}` (target-typed); other sources return bare.
        self.ret_union = _eligible_value_union(rt)
        # F4 U2: a pointer-variant return slot -- only same-union borrow
        # names return bare; a MEMBER record name takes the AST's `&(...)`
        # address-of lift, which the slice does not reproduce.
        self.ret_ptr_union = _eligible_ptr_union(rt, analyzer)


def _f1_record(t: TpyType | None, analyzer) -> bool:
    """A same-module, non-native, non-generic concrete user record -- the F1
    record slice where `TpyType.to_cpp()` == `TypeResolver.type_to_cpp()` (no
    cross-module qualification, no native name/field rename, no generic-arg
    recursion). Other records stay on the AST path."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = t.wrapped
    if not (isinstance(t, NominalType) and t.is_user_record):
        return False
    if t.type_args:
        return False
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return False
    return analyzer.registry.imported_record_qualification_for_type(
        t, analyzer.ctx.module_name) is None


def _unwrap_own(t: TpyType) -> TpyType:
    """The payload of an `Own[T]` wrapper, else `t` unchanged -- the recurring unwrap
    the `Optional`-inner helpers apply before an `_f1_record` check."""
    return t.wrapped if isinstance(t, OwnType) else t


def _storage_optional_return_type(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The storage-form `Optional[F1-record]` return slot (F2c): `Own[T] | None`,
    which lowers to a `std::optional<T>` returned by value. `Inner | None` is
    pointer-repr (the function returns a borrow `Inner*`, a different direction)
    and is excluded -- it stays on the AST path. The caller passes None for a
    non-`TpyType` (unresolved) return annotation."""
    if not isinstance(t, OptionalType) or t.uses_pointer_repr():
        return None
    return t if _f1_record(_unwrap_own(t.inner), analyzer) else None


def _f1_tuple_element_ok(e: TpyType, analyzer) -> bool:
    """A tuple element that renders byte-identically off the F1 slice: an eligible
    value scalar (`T`, same in both forms), an F1-record (BORROW_REF: `T*` borrow /
    `T` storage), or a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL: `T*` borrow
    / `std::optional<T>` storage). Each keeps `to_cpp_return()` / `to_cpp()`
    recursion off cross-module / native / generic / pending types, where bare
    `to_cpp()` would mis-spell. Union / container / generic elements ride later
    rungs."""
    if _eligible_scalar(e) or _f1_record(e, analyzer):
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    if isinstance(inner, OptionalType) and inner.uses_pointer_repr():
        return _f1_record(_unwrap_own(inner.inner), analyzer)
    return False


def _f1_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A pointer-repr tuple whose every element is F1-renderable -- the F3 tuple:
    borrow form `std::tuple<..., T*>` differs from storage form
    `std::tuple<..., std::optional<T>>` / `std::tuple<..., T>`, so a storage source
    lifts via `tuple_to_pointer` and a borrow source stores via `tuple_to_storage`.
    `has_pointer_repr_element` ensures the two forms genuinely differ (an all-value
    tuple needs no conversion). Other tuples stay on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or not t.has_pointer_repr_element():
        return None
    if not all(_f1_tuple_element_ok(e, analyzer) for e in t.element_types):
        return None
    return t


def _borrow_tuple_return_type(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The function's borrow-form pointer-repr tuple return slot (F3): a
    `tuple[..., Ref]` returned as `std::tuple<..., T*>`, into which a `return
    <storage tuple lvalue>` lifts via `tuple_to_pointer`."""
    return _f1_tuple(t, analyzer)


def _is_borrow_form_name(t: TpyType | None) -> bool:
    """Whether a bare name read renders in borrow form: a non-value type (record /
    Optional / etc. -- a pointer / reference) or a pointer-repr tuple (`std::tuple<
    ..., T*>`, value-typed yet with distinct borrow and storage forms). Used to keep
    a THIRName's form tag honest so a convert source is never mislabeled VALUE.

    Precondition: callers must first exclude a STORAGE-form pointer-repr tuple (an F3
    `auto&&` alias local), which has the same type but reads as STORAGE -- this query
    keys on the type alone and would mistag it BORROW. The name-read call site checks
    `storage_tuple_locals` before falling through here. The other call site -- the
    `TpySubscript` branch tagging a subscript *result* -- is safe without that check
    because an admitted element is only ever a value scalar or a plain record, never
    itself a pointer-repr tuple (a nested-tuple element is not in the admitted set), so
    the storage-alias ambiguity cannot arise there."""
    if t is None:
        return False
    if not t.is_value_type():
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(inner, TupleType) and inner.has_pointer_repr_element()


def _value_scalar_tuple(t: TpyType | None) -> bool:
    """A pure value-scalar tuple (`tuple[int, bool, ...]`): a value type rendered
    `std::tuple<...>` where borrow and storage forms coincide, so a subscript read
    of any element needs no lift. Every element is an eligible value scalar -- a
    non-value element makes it pointer-repr (the `_f1_tuple` family), and a
    str/view/nested-tuple element rides a later cell. Admitting it as a param (whose
    signature stays on the AST path) routes functions that read it by subscript."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return (isinstance(t, TupleType)
            and all(_eligible_scalar(e) for e in t.element_types))


def _const_index(index: TpyExpr) -> 'int | None':
    """The compile-time integer index of a tuple subscript, mirroring the AST's
    `_extract_compile_time_index`: a bare int literal or a negated int literal. A
    non-constant tuple index never reaches lowering (sema rejects it); the
    eligibility gate uses this to confirm the literal form regardless."""
    if isinstance(index, TpyIntLiteral):
        return index.value
    if (isinstance(index, TpyUnaryOp) and index.op == "-"
            and isinstance(index.operand, TpyIntLiteral)):
        return -index.operand.value
    return None


def _subscript_index_and_tuple(sub: TpySubscript,
                               analyzer) -> 'tuple[TupleType, int] | None':
    """`(tuple_type, normalized_idx)` for a tuple subscript with a compile-time-const,
    in-bounds index (a negative literal folded by the tuple arity), or None if the
    receiver is not a tuple or the index is not such a constant. The receiver-type
    resolution + index fold written once, shared by the eligibility gate, the arrow
    decision, and lowering so the three can never drift."""
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(sub.obj))))
    if not isinstance(recv_t, TupleType):
        return None
    idx = _const_index(sub.index)
    if idx is None:
        return None
    n = len(recv_t.element_types)
    if idx < 0:
        idx += n
    if not (0 <= idx < n):
        return None
    return recv_t, idx


def _subscript_recv_tuple(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> 'tuple[TupleType, int] | None':
    """`(tuple_type, normalized_idx)` for a subscript `t[N]` off an in-scope
    eligible-tuple name (a value-scalar tuple or an already-routed pointer-repr
    `_f1_tuple`); else None. Shared by the value-element and record-element read gates
    -- non-name receivers, ineligible tuples, and non-const indices stay on the AST
    path."""
    if not isinstance(e, TpySubscript):
        return None
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return None
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return None
    recv_t, _idx = res
    if not (_value_scalar_tuple(recv_t) or _f1_tuple(recv_t, analyzer) is not None):
        return None
    return res


def _tuple_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                analyzer) -> 'int | None':
    """A value-result tuple subscript read `t[N]` -> `std::get<N>(t)` (value form, no
    lift): element N is a value scalar. Returns the normalized index, or None -- record
    / `Optional` (borrow) elements ride the field-receiver path (`t[N].field`)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    return idx if _eligible_scalar(recv_t.element_types[idx]) else None


def _subscript_record_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> 'int | None':
    """`t[N]` whose element is a plain F1-record (a `BORROW_REF` pointer-repr slot) --
    a borrow result usable as a scalar-field-read receiver (`t[N].field`). Returns the
    normalized index, or None. `Optional[record]` elements take the null-check member
    path (`_subscript_optional_field_recv`) and are excluded here (`_f1_record` rejects
    them)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    return idx if _f1_record(recv_t.element_types[idx], analyzer) else None


def _subscript_optional_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                   analyzer) -> 'int | None':
    """`t[N]` whose element is a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL) -- a
    nullable borrow usable as a runtime-null-checked field receiver (`t[N].field` ->
    `deref_check(...)`). Returns the normalized index, or None. Mirrors the Optional arm
    of `_f1_tuple_element_ok`."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t.element_types[idx])))
    if not (isinstance(inner, OptionalType) and inner.uses_pointer_repr()):
        return None
    return idx if _f1_record(_unwrap_own(inner.inner), analyzer) else None


def _field_over_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A scalar field access off a record-element tuple subscript (`t[N].field`): the
    receiver `t[N]` is a plain-record borrow, the field a value scalar (checked by the
    caller). Position-neutral -- valid as a read (RHS) or a scalar-field write target
    (LHS), since both render `std::get<N>(t)->field` / `.field` off the same receiver.
    The borrow-local-binding source path keeps its own name-receiver gate, so `b = t[N]`
    stays on the AST path. Markers-clean excludes the Optional null-check / property /
    setattr shapes, so an Optional-element write and a property-setter write stay on the
    AST path."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _subscript_record_field_recv(e.obj, locals_, analyzer) is not None)


def _optional_field_over_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                      analyzer) -> bool:
    """A scalar field read off an `Optional[record]`-element tuple subscript with an
    unproven None (`t[N].field` -> `deref_check(...).field`, the
    `needs_optional_runtime_check` path). The receiver `t[N]` is a nullable borrow, the
    field a value scalar (checked by the caller). Read only -- writes / binds keep the
    name-receiver gate."""
    return (isinstance(e, TpyFieldAccess) and e.needs_optional_runtime_check
            and _field_markers_clean(e, allow_optional_check=True)
            and _subscript_optional_field_recv(e.obj, locals_, analyzer) is not None)


def _owned_str_slot(t: TpyType | None, analyzer) -> bool:
    """An owned `str` container element/key/value slot (S5). Only the owned
    nominal is admitted: a `StrView`/`BytesView` slot makes the container hold
    views, whose literal keys/elements the AST pins to static storage
    (`view_key_target` threads the key type into the literal render) -- a shape
    this slice does not reproduce; the bytes family rides S6."""
    st = _resolved_str_value(t, analyzer)
    return st is not None and is_str_type(st)


def _container_scalar_read(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value read renders as a bare value via the
    container subscript emit: `list[scalar|str]`, `Array[scalar|str, N]` (sema's
    read-only list-literal demotion -- subscript/len/iteration emit identically
    to list), or `dict[fixed-int|str key, scalar|str value]`. `set` has no
    `__getitem__`. Owned-`str` keys read identically to a fixed-int index
    (`::tpy::__getitem__(c, k)` -- the static-storage literal pin fires only for
    VIEW-typed keys, which `_owned_str_slot` excludes); a BigInt key rides the
    same cell as BigInt indices (both need the `.to_fixed_check<int32_t>()`
    narrow, out of the fixed-int scalar slice). An `Own[container]` (move-in
    `T&&` param) is excluded explicitly -- its ABI differs from the borrow shape
    this slice's emit assumes, and it rides a later cell (mirrors the Own unwrap
    in `_f1_record`, which admits Own where this deliberately does not)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if is_list(t) or is_array(t):
        return bool(args) and (_eligible_scalar(args[0])
                               or _owned_str_slot(args[0], analyzer))
    if is_dict(t):
        if not args or len(args) < 2:
            return False
        key, val = args[0], args[1]
        return ((is_fixed_int_type(key) or _owned_str_slot(key, analyzer))
                and (_eligible_scalar(val) or _owned_str_slot(val, analyzer)))
    return False


def _container_literal_decl_ok(stmt: TpyVarDecl, declared: dict[str, TpyType],
                               prescan: '_Prescan', analyzer) -> bool:
    """First decl of a container-literal local: `xs = [1, 2]` / `xs: list[T] = []`
    / `d = {k: v}` / `s = {a, b}`. The decl's binding type is sema's RESOLVED
    container (a list literal's vector-vs-array decision -- the PendingListType
    resolution -- is final before lowering), so the emit is a pure function of
    that type + the elements. Admitted families mirror the receiver slice:
    `list[scalar|str]` / `Array[scalar|str, N]` / `dict[fixed-int|str,
    scalar|str]`, plus `set[scalar|str]` (decl/len/iteration only -- set has no
    `__getitem__`). Every element/key/value is an eligible scalar or str-slice
    expr; a view-form str source into an owned `std::string` slot copies via the
    per-element `THIRFormConvert` wrap (`std::string(x)`, S5 -- the
    `_wrap_for_owned_slot` mirror), everything else lands bare in the AST's
    brace-init pass-through. The `make_vector`/`make_ordered_*` move arm cannot
    fire: scalars and the str family are value types, never in
    `movable_locals` (sema's ever-owned tracking guards on non-value), so
    `_maybe_move` is inert for every admitted element. A `[0] * n` repeat
    (TpyListRepeat) and a
    nested container element stay on the AST path. The empty-literal-to-Array
    reject is defensive-only: sema errors on both routes to that shape (a bare
    `[]` is un-inferable; an `Array[T, 0]` annotation mismatches the literal),
    so only the empty LIST form (the spelled `std::vector<T>{}` emit) is
    reachable."""
    # A reassigned container local is a POINTER-LOCAL on the AST path (`a = b`
    # rebinds the alias -- `std::vector<T>* a = &__slot_N; ... a = &(b);` -- so a
    # later `a.append` mutates the aliased list, Python's rebinding semantics).
    # The plain value decl this cell emits would silently copy instead; reject
    # (hoisted / move-through conservatively ride along).
    if (stmt.name in prescan.reassigned or stmt.name in prescan.hoisted
            or stmt.name in prescan.move_through):
        return False
    init = stmt.init
    t = _var_decl_type(stmt, analyzer)
    if t is None:
        return False
    if isinstance(init, TpyDictLiteral):
        if not (is_dict(t) and _container_scalar_read(t, analyzer)):
            return False
        elems = list(init.keys) + list(init.values)
    elif isinstance(init, TpySetLiteral):
        args = getattr(t, "type_args", None)
        if not (is_set(t) and bool(args)
                and (_eligible_scalar(args[0])
                     or _owned_str_slot(args[0], analyzer))):
            return False
        elems = list(init.elements)
    elif isinstance(init, TpyArrayLiteral):
        if is_dict(t) or not _container_scalar_read(t, analyzer):
            return False
        if not init.elements and not is_list(t):
            return False
        elems = list(init.elements)
    else:
        return False
    return all(_expr_eligible(e, declared, analyzer) for e in elems)


def _container_record_iter(t: TpyType | None, analyzer) -> bool:
    """A `list[F1-record]` container -- iterated (`for x in c`) with a record loop var
    (`auto&&` / `const auto&`, a borrow alias). The iteration counterpart to
    `_container_scalar_read` (scalar-element containers read by subscript). A dict's
    record VALUES need `for k, v in d.items()` (tuple-unpack, a later cell); `for k in d`
    yields keys, which is the scalar path. `set` / `Span` / `Array` record params ride a
    later cell (their params aren't admitted). `Own[list]` is excluded (mirrors
    `_container_scalar_read`)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if is_list(t):
        return bool(args) and _f1_record(args[0], analyzer)
    return False


def _container_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                    analyzer) -> bool:
    """A container subscript read `c[i]` off an in-scope container name whose
    element/value is a value scalar or a str-slice value
    (`::tpy::__getitem__(c, i)`, or the bounds-safe
    `c[static_cast<std::size_t>(i)]`). The receiver is a plain name (a non-name or
    narrowed-Optional receiver rides a later cell); the index is any eligible
    value-scalar expr, or -- for an owned-str-keyed dict -- any eligible
    str-slice expr (a literal / name / concat renders bare in the key slot; the
    static-storage pin fires only for view-typed keys, which the receiver gate
    excludes). A `readonly[container]` receiver routes too (byte-identical) --
    sema readonly-wraps only non-value elements, so a scalar element read is never
    `readonly[scalar]`; the result check is a defensive guard confirming the read
    yields a value scalar / str value (redundant with the element check today,
    robust if the container predicate later widens)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return False
    ret = analyzer.get_expr_type(e)
    # locals_ (the declared binding type) rather than get_expr_type: a
    # container-literal local's use sites carry the pre-resolution
    # PendingListType (see _method_call_eligible).
    return (_container_scalar_read(locals_[recv.name], analyzer)
            and (_resolved_scalar(ret, analyzer)
                 or _resolved_str_value(ret, analyzer) is not None)
            and _expr_eligible(e.index, locals_, analyzer))


def _str_subscript_char_read(e: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A str subscript read `s[i]` -> Char off a str-family receiver:
    `::tpy::__getitem__(s, i)` (str's `__getitem__` @cpp_template spells the
    same checked dunder as the container arm), or the bounds-safe
    `s[static_cast<std::size_t>(i)]` / literal `s[0]`. The receiver shapes are
    the shared slice/iteration set (`_str_slice_receiver_ok`: an in-scope name,
    a str-family field off an F1-record receiver, an eligible str-returning
    call -- the receiver renders bare into the dunder / operator[] either way,
    and `bounds_safe` is a carried node fact); the index is any eligible
    value-scalar expr (a BigInt index cannot arise -- no BigInt binding is
    admitted -- so no `.to_fixed_check` narrow). The slice form (`s[a:b]`,
    slice_function_info) has its own gate; bytes has its
    `::tpy::bytes_getitem` twin (`_bytes_subscript_read`, name receivers
    only)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None:
        return False
    if not _str_slice_receiver_ok(e.obj, locals_, analyzer):
        return False
    return (_eligible_char(analyzer.get_expr_type(e))
            and _expr_eligible(e.index, locals_, analyzer))


def _bytes_subscript_read(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> bool:
    """A bytes subscript read `b[i]` -> UInt8 off an in-scope bytes-family
    name: `::tpy::bytes_getitem(b, i)` -- bytes' `__getitem__(Int32)` is a
    @native free-function dunder, NOT the containers' `::tpy::__getitem__`
    checked template, so the emit dispatches on the bytes receiver -- or the
    bounds-safe `b[static_cast<std::size_t>(i)]` / literal `b[0]` shared with
    the container arm. Receiver and index constraints mirror the str twin
    (`_str_subscript_char_read`): a plain name receiver, an eligible
    value-scalar index (no BigInt binding is admitted, so no
    `.to_fixed_check` narrow)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None:
        return False
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return False
    if _resolved_bytes_value(locals_[recv.name], analyzer) is None:
        return False
    return (_eligible_scalar(analyzer.get_expr_type(e))
            and _expr_eligible(e.index, locals_, analyzer))


def _slice_bound_ok(b: 'TpyExpr | None', locals_: dict[str, TpyType],
                    analyzer) -> bool:
    """A str-slice bound: absent (-> `std::nullopt`), or an eligible fixed-int
    value expr rendered bare into the BasicSlice initializer (the AST's
    `_gen_slice_bound` is a target-less gen_expr_deref, so the expression
    render is position-neutral). A BigInt-resolving bound appends
    `.to_fixed_check<int32_t>()` -> AST path; int literals resolve through the
    module default int so a BigInt default rejects rather than mis-admits."""
    if b is None:
        return True
    if not _expr_eligible(b, locals_, analyzer):
        return False
    bt = analyzer.get_expr_type(b)
    if bt is None:
        return False
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    return is_fixed_int_type(
        resolve_int_literals(bt, analyzer.ctx.default_int_for_literal))


def _str_slice_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                           analyzer) -> bool:
    """A str/bytes-family slice/iteration receiver rendered bare into the
    resolved template's `{self}` slot: an in-scope str/bytes name, a
    str/bytes-family field off an F1-record receiver (`h.name` / `p->name` --
    an lvalue, like a name), or an eligible owned/view-returning call
    (`full(s)[0:2]` -- the temporary lives to the end of the full expression
    on both paths, and sema resolves any BINDING of the resulting view owned,
    so the view->owned copy (`std::string(...)` / `::tpy::bytes_copy(...)`)
    materializes it before the temporary dies -- no view outlives it)."""
    if isinstance(recv, TpyName):
        return (recv.name in locals_
                and _resolved_viewfam_value(locals_[recv.name],
                                            analyzer) is not None)
    if isinstance(recv, TpyFieldAccess):
        return (_field_receiver_ok(recv, locals_, analyzer)
                and _resolved_viewfam_value(analyzer.get_expr_type(recv),
                                            analyzer) is not None)
    if isinstance(recv, TpyCall):
        return (_call_eligible(recv, locals_, analyzer)
                and _resolved_viewfam_value(analyzer.get_expr_type(recv),
                                            analyzer) is not None)
    return False


def _str_slice_read(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """A str/bytes slice off a str/bytes-family receiver -> the resolved slice
    `__getitem__`'s @cpp_template over the slice argument (see `THIRStrSlice`
    for the three index shapes; bytes carries `::tpy::bytes_slice` /
    `::tpy::bytes_stepped_slice` on the same node). Non-stepped `s[a:b]`
    yields a `std::string_view` / `std::span<const uint8_t>` VIEW result
    (BORROW form), consumed at view sinks (view local, view reassign,
    print/compare/len-free positions) or materialized at an owned sink: a str
    owned sink arrives as a sema `strview_to_str` TpyCoerce, lowered via
    `_coerce_disposition` to the view->owned THIRFormConvert
    (`std::string(...)` around the slice); a bytes owned DECL INIT carries no
    coerce (the pending local's owned resolution) and takes the S6 decl-init
    BORROW wrap (`::tpy::bytes_copy(...)`) directly, while a bytes owned
    RETURN arrives as the gate-rejected `bytesview_to_bytes` coerce (the
    deferred cross-type bytes-coercion cell) -> AST. Stepped `s[a:b:c]` and
    a `slice`-typed
    variable index yield the family's OWNED type (STORAGE, bare at every
    sink); a `basic_slice`-typed variable index yields the view. A `{cpp}`
    placeholder would need return-type substitution the emit lacks."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    fi = e.slice_function_info
    if fi is None:
        return False
    if not fi.cpp_template or "{cpp}" in fi.cpp_template:
        return False
    if not _str_slice_receiver_ok(e.obj, locals_, analyzer):
        return False
    rt = _resolved_viewfam_value(analyzer.get_expr_type(e), analyzer)
    if rt is None:
        return False
    if isinstance(e.index, TpySlice):
        sl = e.index
        if e.is_stepped_slice:
            # `::tpy::Slice{lo, hi, step}` -> the owned std::string /
            # std::vector<uint8_t> result.
            if not (is_str_type(rt) or is_bytes_type(rt)):
                return False
            if not _slice_bound_ok(sl.step, locals_, analyzer):
                return False
        else:
            # Defensive: sema sets is_stepped_slice iff the syntax has a step.
            if sl.step is not None or not (is_str_view_type(rt)
                                           or is_bytes_view_type(rt)):
                return False
        return (_slice_bound_ok(sl.lower, locals_, analyzer)
                and _slice_bound_ok(sl.upper, locals_, analyzer))
    # Slice-typed VARIABLE index (`s[sl]`): a bare in-scope slice-object name,
    # rendered bare into the template's `{0}` slot. The view/owned result
    # follows the sema-resolved overload; either way the emit is the bare
    # template expansion, so only the name shape is pinned.
    return (isinstance(e.index, TpyName) and e.index.name in locals_
            and _slice_object_type(locals_[e.index.name]))


def _field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a plain field access `recv.field` off an F1-record receiver (a record
    param, REF_ALIAS local, or F2 plain `T*` pointer-local in `declared`) with no
    special-emit marker -- read or write position. The receiver's pointer-vs-
    reference shape (`->` vs `.`) is decided at lowering from the pointer-local set;
    eligibility only needs the receiver to be an F1-record. Pointer-local Optional
    reads would need narrowing (the marker guards below reject those), so only plain
    non-null receivers pass. The property-setter / `__setattr__` markers guard the
    write position (a property/setattr field assign takes a method-call emit path)."""
    if not isinstance(e, TpyFieldAccess) or not _field_markers_clean(e):
        return False
    recv = e.obj
    return (isinstance(recv, TpyName)
            and _f1_record(declared.get(recv.name), analyzer))


def _field_markers_clean(e: TpyFieldAccess, *,
                         allow_optional_check: bool = False) -> bool:
    """The field access carries no special-emit marker (module var / class constant /
    property / dyn attr / unbound-self / deref chain / Optional null-check) -- a plain
    `.field` read or write. Each marker takes its own AST emit path, out of the slice.
    `allow_optional_check` keeps `needs_optional_runtime_check` admissible for the
    Optional-element member path (which reproduces that runtime check)."""
    return not (e.module_var_access is not None or e.class_constant_owner is not None
                or e.property_getter_call is not None or e.dyn_getattr_call is not None
                or e.property_setter_call is not None or e.dyn_setattr_call is not None
                or e.unbound_self_parent_type is not None or e.deref_depth
                or e.deref_narrowed_to is not None
                or (e.needs_optional_runtime_check and not allow_optional_check))


def _borrow_local_binding(stmt: TpyVarDecl, target_type: TpyType | None,
                          declared: dict[str, TpyType], prescan: _Prescan,
                          analyzer) -> 'LocalBinding | None':
    """The binding for a non-value local var-decl's *first* declaration, or None
    if it is outside the emit slice. The form decision comes from the shared
    classifier; the slice additionally requires a field-access source off an
    F1-record receiver and an F1-record local (REF_ALIAS / POINTER) / inner
    (OPTIONAL_TO_PTR) type. REF_ALIAS and POINTER are the single-assignment and
    reassigned shapes of the same plain-record lvalue lift; POINTER's reseats are
    gated separately in `_stmt_eligible`."""
    binding = classify_local_binding(
        target_type, stmt.init, analyzer, name=stmt.name,
        reassigned=prescan.reassigned, rvalue_reassigned=prescan.rvalue_reassigned,
        hoisted=prescan.hoisted, move_through=prescan.move_through)
    if binding is LocalBinding.OTHER:
        return None
    if binding is LocalBinding.REBIND_SLOT:
        # F2d: the source is an rvalue F1-record ctor / by-value call (not a field
        # read), so it bypasses the field-receiver check the lvalue bindings need.
        return binding if (_f1_record(target_type, analyzer)
                           and _is_record_rvalue_source(stmt.init, declared, analyzer)) else None
    if not _field_receiver_ok(stmt.init, declared, analyzer):
        return None
    if binding is LocalBinding.REF_ALIAS or binding is LocalBinding.POINTER:
        return binding if _f1_record(target_type, analyzer) else None
    # OPTIONAL_TO_PTR: the borrow `T*` points at the optional's inner record.
    inner = target_type.inner if isinstance(target_type, OptionalType) else None
    return binding if _f1_record(inner, analyzer) else None


def _is_record_rvalue_source(init: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """An F2d rebind-slot source: an rvalue call producing an F1-record (a ctor
    `Inner(...)` or a by-value record-returning call) with eligible scalar args.
    It emits as the bare `Name(args)` the two-slot init / reseat wraps. kwargs /
    star-unpack args take other emit paths and stay on the AST path."""
    if not isinstance(init, TpyCall):
        return False
    if init.kwargs or init.double_star_unpack is not None:
        return False
    if not (_f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    # Exact positional arity (mirror _call_eligible): an omitted default is
    # synthesized by the AST arg emit, which the bare THIRCall does not do.
    fi = init.resolved_function_info
    if fi is None or len(init.args) != len(fi.params):
        return False
    # Args must be eligible SCALARS or slot-resolved bare float literals
    # (mirror _call_eligible): a non-scalar arg (a record pointer-local /
    # Own[T]) needs the AST's `(*q)` deref or auto-move `std::move(q)`,
    # neither of which the bare THIRCall arg emit reproduces.
    return all((_eligible_scalar(analyzer.get_expr_type(a))
                and _expr_eligible(a, declared, analyzer))
               or _float_literal_pass_through_arg(a, p.type, declared, analyzer)
               for a, p in zip(init.args, fi.params))


def _f2_reseat_ok(init: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """A pointer-local reseat value: an lvalue field read off an F1-record receiver
    whose field is itself an F1-record (the new pointee), so it reseats as
    `p = &(recv.field);`. rvalue / `None` / name-alias reseats need the rebind-slot
    (`__slot_N`) machinery and stay on the AST path."""
    return (_field_receiver_ok(init, declared, analyzer)
            and _f1_record(analyzer.get_expr_type(init), analyzer))


def _is_borrow_ptr_local(e: TpyExpr, declared: dict[str, TpyType],
                         pointers: set[str]) -> bool:
    """`e` is a bare borrow `T*` local that lifts to a storage `optional<T>` at a
    write/return: an F2a POINTER / F2d REBIND_SLOT (plain-record, in `pointers`) or
    an F1 OPTIONAL_TO_PTR (a pointer-repr `Optional` local, known by its declared
    type). All render `T*`; the copy-vs-move choice (`ptr_to_optional` vs
    `ptr_to_optional_move`) is decided at lowering from movability + last-use, not
    here (a non-owning borrow is never movable, so it always copies). A `T&`
    REF_ALIAS is excluded -- not a pointer, so the AST path emits it differently."""
    if not isinstance(e, TpyName):
        return False
    if e.name in pointers:
        return True
    t = declared.get(e.name)
    return isinstance(t, OptionalType) and t.uses_pointer_repr()


def _f2b_optional_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """An optional-field write `recv.field = <value>`: the target is a pointer-repr
    `Optional[record]` field off an F1-record receiver and the value is either a
    bare borrow `T*` local (`recv.field = ::tpy::ptr_to_optional[_move](p)`, copy
    or move per last-use) or a `None` literal (`recv.field = std::nullopt`). The
    `copy()`-acknowledged and call/rvalue value sources stay on the AST path."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()):
        return False
    if not _f1_record(ftype.inner, analyzer):
        return False
    return (isinstance(stmt.value, TpyNoneLiteral)
            or _is_borrow_ptr_local(stmt.value, declared, pointers))


def _scalar_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                           analyzer) -> bool:
    """A scalar-field write `recv.field = <scalar>`: a value-scalar field off an
    F1-record receiver (`_field_receiver_ok` also rejects the property-setter /
    __setattr__ write target), written with an eligible scalar expression. The
    scalar sibling of `_f2b_optional_field_write_ok` -- it emits as the AST's
    default field-assign path (`recv.field = <value>;`, no borrow<->storage lift). The
    target is a plain field off an F1-record receiver, or a record-element tuple
    subscript (`t[N].field = <scalar>` -> `std::get<N>(t)->field = ...`, the write analog
    of the record-element read); an Optional-element target stays on the AST path (its
    markers reject it). A Char field writes identically (`recv.c = z`); its
    str-literal value guard is defensive -- sema type-errors a literal into a
    Char field, but the target-typed `'x'` render would otherwise diverge."""
    target = stmt.target
    if not (_field_receiver_ok(target, declared, analyzer)
            or _field_over_subscript_ok(target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(target)
    if _eligible_char(ftype):
        if isinstance(stmt.value, TpyStrLiteral):
            return False
    elif not _eligible_scalar(ftype):
        return False
    return _expr_eligible(stmt.value, declared, analyzer)


def _ptr_union_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                              analyzer) -> bool:
    """A union-field write `recv.field = <borrow union name>` (F4 U2): a
    value-variant field off an F1-record receiver written from a borrow-form
    pointer-variant name of the same union -- lowers to the borrow->storage
    `THIRFormConvert` (`::tpy::to_value_variant<...>`), the union sibling of
    the F2b Optional write. None sources (a monostate write), member-valued
    sources (the ctor-arg cascade), and field-to-field copies ride later
    cells; the last two are also pre-existing AST gaps at other positions."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    u = _eligible_ptr_union(analyzer.get_expr_type(target), analyzer)
    if u is None:
        return False
    return _ptr_union_source_ok(stmt.value, declared, analyzer, u,
                                allow_field=False)


def _is_borrow_tuple_source(e: TpyExpr, declared: dict[str, TpyType],
                            storage_tuple_locals: set[str], analyzer) -> bool:
    """A borrow-form tuple name (`std::tuple<..., T*>`) that lifts to storage form
    at a field write via `tuple_to_storage`: a borrow tuple PARAM. A storage-tuple
    alias local (`auto&&`, in `storage_tuple_locals`) is STORAGE form -- a direct
    copy, no wrap -- and is excluded; a storage-form field/subscript/global source
    is likewise a direct copy and is not this borrow source."""
    return (isinstance(e, TpyName)
            and e.name not in storage_tuple_locals
            and _f1_tuple(declared.get(e.name), analyzer) is not None)


def _f1_tuple_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                             storage_tuple_locals: set[str], analyzer) -> bool:
    """A tuple-field write `recv.field = <borrow tuple>`: an F3 tuple field off an
    F1-record receiver, written from a borrow tuple source -> the field-write lifts
    borrow->storage via `tuple_to_storage` (copy; the `Own[tuple]` move arm and the
    storage-source direct-copy ride later cells)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    if _f1_tuple(analyzer.get_expr_type(target), analyzer) is None:
        return False
    return _is_borrow_tuple_source(stmt.value, declared, storage_tuple_locals, analyzer)


def _scalar_aug_assign_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """A scalar augmented assignment `x += y` / `recv.field += y` that is
    byte-identical to `target = (target OP value)` -- the plain
    `_gen_binop_from_result` branch of `_gen_aug_assign_code`, with every
    preprocessing branch of the AST path gated out:

      - an in-place dunder (`resolved_inplace`) mutates the target via a method
        call, not the binop substitution;
      - a missing/non-template `resolved_binop` emits a bare C++ `op=` fallback;
      - `str +=` takes the in-place-append optimization (excluded for free: a
        str target is not an eligible scalar);
      - `FixedInt += BigInt` wraps the value in `.to_fixed_check<T>()` the
        synthetic binop cannot reproduce;
      - a class-constant / narrowed-optional target is not a plain eligible-scalar
        lvalue (a record-element tuple-subscript target IS admitted, via
        `_field_over_subscript_ok` -- the target renders identically on both sides
        of the synthetic `target = (target OP value)`).

    The target is a declared scalar local, an F1-record scalar field, or a
    record-element tuple subscript (`t[N].field`); the value
    is an eligible scalar expression. Lowering synthesizes the binop with
    `divisor_non_zero=False` -- the AST aug-assign path never swaps
    `div_check`->`div_floor` (no `TpyBinOp` node carries the flag)."""
    if stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    target = stmt.target
    if isinstance(target, TpyName):
        if target.name not in declared:
            return False
    elif not (_field_receiver_ok(target, declared, analyzer)
              or _field_over_subscript_ok(target, declared, analyzer)):
        return False
    # A narrowed-Optional or non-scalar target is rejected here (the AST unwraps
    # the former and never reaches the binop branch for the latter).
    target_type = analyzer.get_expr_type(target)
    if not _eligible_scalar(target_type):
        return False
    # FixedInt += BigInt: the AST converts the value via `.to_fixed_check<T>()`
    # before the binop -- the synthetic THIRBinOp would emit a bare `t + b`.
    if is_fixed_int_type(target_type) and is_big_int_type(analyzer.get_expr_type(stmt.value)):
        return False
    return _expr_eligible(stmt.value, declared, analyzer)


def _str_aug_append_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                       prescan: _Prescan, analyzer) -> bool:
    """A str in-place append `t += v` -> `t += v;` -- the string branch of
    `_gen_aug_assign_code`'s resolved-binop arm, mirrored condition for
    condition: no in-place dunder (checked first there), a resolved binop, op
    `+`, and an owned-str-family target. The value renders bare via
    `gen_expr(value, target_type)` for every admitted shape (str literal /
    str-family name / owned-str call / nested concat), so it is pinned to the
    concat-operand slice.

    The target must be a declared LOCAL: a str param's aug-assign would need
    the AST's owned-copy prologue -- which `_function_eligible`'s reassigned-
    param reject does NOT cover, because the prescan tracks aug-assign targets
    in `aug_assigned`, not `reassigned`. (The AST path itself emits `a += v` on
    the untouched `std::string_view` param there -- an invalid-C++ miscompile,
    tracked in BUGS.md -- so the reject also avoids reproducing it.)"""
    if stmt.op != "+" or stmt.resolved_inplace is not None:
        return False
    if stmt.resolved_binop is None:
        return False
    target = stmt.target
    if not (isinstance(target, TpyName) and target.name in declared
            and target.name not in prescan.param_names):
        return False
    if not _owned_str_append_target(analyzer.get_expr_type(target), analyzer):
        return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_str_concat_operand(stmt.value, vt, analyzer)
            and _expr_eligible(stmt.value, declared, analyzer))


def _bytes_aug_concat_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                         prescan: _Prescan, analyzer) -> bool:
    """A bytes `t += v` -> the concat-and-assign
    `t = ::tpy::bytes_concat(t, v);` -- there is NO in-place append for bytes
    (`_gen_aug_assign_code`'s string branch is str-family-pinned), so the AST
    takes the resolved-binop arm and the lowering's generic
    `target = (target OP value)` desugar reproduces it (the native
    `bytes_concat` emit, unwrapped -- `paren_wrap=False`). Mirrored condition
    for condition with the str twin (`_str_aug_append_ok`): no in-place
    dunder, a resolved binop (here the template-less native dunder), op `+`,
    and an owned-bytes target.

    The target must be a declared LOCAL: an aug-assigned bytes PARAM skips the
    AST's owned-copy prologue (the prescan tracks aug-assign targets in
    `aug_assigned`, not `reassigned`) and emits
    `a = ::tpy::bytes_concat(a, b);` on the untouched span param -- the span
    silently rebinds to the concat's dying temporary vector (a dangling-view
    miscompile, the bytes face of the str aug-assign-param bug in BUGS.md) --
    so the reject avoids reproducing it."""
    if stmt.op != "+" or stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or getattr(rb.method, "cpp_template", None):
        return False
    if not (rb.method.native_function and rb.method.native_name):
        return False
    target = stmt.target
    if not (isinstance(target, TpyName) and target.name in declared
            and target.name not in prescan.param_names):
        return False
    bt = _resolved_bytes_value(analyzer.get_expr_type(target), analyzer)
    if bt is None or not is_bytes_type(bt):
        return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_bytes_concat_operand(stmt.value, vt, analyzer)
            and _expr_eligible(stmt.value, declared, analyzer))


def _param_is_const(name: str, func: TpyFunction, analyzer,
                    record_name: str | None = None) -> bool:
    """Whether param `name` is emitted `const` -- read from the sema fact
    `FunctionInfo.const_borrow_params` (param indices), which equals codegen's
    `const_ref_params` for a function's plain F1-record (ref) params. This holds
    for a readonly callable (method or free function) too: a `@readonly` callable's
    non-value param is stored as `Ref(ReadonlyType(T))`, so `decide_param_const`
    takes the `ReadonlyType` early-exit -- the forced-const (codegen body) and
    inferred (`const_borrow_params`) verdicts traverse the same branch, making the
    inferred set exact. `cbp` is None when Phase-2 has not run -- unreachable for an
    admitted function (Phase-1 always sets `mutated_params`), so the resulting
    not-const is a safe default, not a divergence. A method's FunctionInfo lives on
    the owning record (`record_name`) -- the same lookup codegen's
    `_get_method_mutated_params` uses; a free function (record_name None) reads the
    function registry."""
    if record_name is not None:
        ri = analyzer.registry.get_record(record_name)
        overloads = ri.get_method_overloads(func.name) if ri is not None else None
    else:
        overloads = analyzer.registry.get_function(func.name)
    # A property pair shares one overload list (getter + setter); [-1] is safe
    # only because a getter has no non-self params (this lookup is never
    # consulted for it) and the setter's non-value param is forced Own[...]
    # (routing around const entirely). A widening that loosens either
    # invariant must match by is_property_getter/setter instead.
    fi = overloads[-1] if overloads else None
    cbp = fi.const_borrow_params if fi is not None else None
    if not cbp:
        return False
    idx = next((i for i, (n, _) in enumerate(func.params) if n == name), None)
    return idx is not None and idx in cbp


def _f1_is_const(binding: 'LocalBinding', target_type: TpyType | None,
                 stmt: TpyVarDecl, func: TpyFunction, analyzer,
                 const_locals: set[str], record_name: str | None = None) -> bool:
    """The const-ness of an F1 borrow local's decl (`const T&` / `const T*`).

    Mirrors `_is_const_indirect` for the field source (ReadonlyType reads on the
    optional inner / the init's raw sema type / the var_types entry) plus, for
    OPTIONAL_TO_PTR, the storage-optional const bump (`_is_const_union_source`:
    the receiver in const_ref_params (param) or const_indirect_locals (a const F1
    local, tracked in `const_locals`)). The name/method-call const branches of
    `_is_const_indirect` do not apply to a field source."""
    if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
        return True
    if isinstance(analyzer.get_expr_type(stmt.init), ReadonlyType):  # raw sema type
        return True
    svt = analyzer.var_types.get(id(stmt))
    if isinstance(svt, OptionalType) and isinstance(svt.inner, ReadonlyType):
        return True
    if binding is LocalBinding.OPTIONAL_TO_PTR:
        recv = stmt.init.obj  # TpyName (validated by _field_receiver_ok)
        if (recv.name in const_locals
                or _param_is_const(recv.name, func, analyzer, record_name)):
            return True
    return False


def _operand_type(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> TpyType | None:
    # The operand's resolved type for the mixed-sign comparison gate. For a
    # local/param name use the tracked resolved type -- codegen's get_resolved_type
    # reads ctx.var_types, which holds e.g. a retro-widened literal-seeded local's
    # final type (UInt64), whereas analyzer.get_expr_type returns the pre-widen
    # seed (Int32). Using the seed would over-exclude same-sign-after-widen loops.
    if isinstance(e, TpyName):
        t = locals_.get(e.name)
        if t is not None:
            return t
    return analyzer.get_expr_type(e)


def _mixed_sign_compare(left: TpyType | None, right: TpyType | None) -> bool:
    # Mirror of codegen's _mixed_sign_fixed_int (expressions.py): a signed-vs-
    # unsigned fixed-int comparison emits std::cmp_* (and a mixed-sign one with a
    # coercion target emits a cast), never the bare `(l op r)` the slice emits --
    # so exclude it. Built on the same int_traits_of primitive; the byte-identical
    # net gates any drift from the codegen predicate.
    if not (is_fixed_int_type(left) and is_fixed_int_type(right)):
        return False
    lt, rt = int_traits_of(left), int_traits_of(right)
    return lt is not None and rt is not None and lt.signed != rt.signed


def _union_compare_pair(lt: TpyType | None, rt: TpyType | None) -> bool:
    """Two SAME-TYPE value-union compare operands: `std::variant`'s own
    comparison operators, the rb=None bare-operator arm -- `(a == b)` on both
    paths. A union-vs-member compare renders the bare mixed pair on the AST
    path (invalid C++, the union-operand BUGS.md class) -> AST path; the
    equal-union requirement rejects it."""
    u = _eligible_value_union(lt)
    return u is not None and u == _eligible_value_union(rt)


def _binop_eligible(e: TpyBinOp, locals_: dict[str, TpyType], analyzer) -> bool:
    rb = e.resolved_binop
    rt = analyzer.get_expr_type(e)
    if e.op in _ARITH_OPS:
        # Same-width arithmetic: a templated dunder, scalar result. Excludes any
        # mixed/widening result the slice can't render without a coercion node.
        # _resolved_scalar: two literal-seeded-container element reads (e.g.
        # `ys[0] + ys[2]`) produce an IntLiteral result type; the emit reads only
        # the resolved dunder's template, so the resolved default int is the fact
        # that matters.
        if rb is None or not getattr(rb.method, "cpp_template", None):
            # Bytes concat `a + b`: the resolved `__add__` is a template-less
            # @native free-function dunder (`::tpy::bytes_concat`, an owned
            # `bytes` result) -- the emit's native binop arm, the shape the
            # compare gate already admits for `bytes_eq`. Any other
            # template-less rb takes an unmirrored emit path -> AST. The
            # `bytearray` overload also resolves to `bytes_concat`, but its
            # operand is not a bytes-slice value and rejects below.
            if rb is None or e.op != "+":
                return False
            if not (rb.method.native_function and rb.method.native_name):
                return False
            bt = _resolved_bytes_value(rt, analyzer)
            if bt is None or not is_bytes_type(bt):
                return False
            lt = _operand_type(e.left, locals_, analyzer)
            rt_op = _operand_type(e.right, locals_, analyzer)
            if not (_bytes_concat_operand(e.left, lt, analyzer)
                    and _bytes_concat_operand(e.right, rt_op, analyzer)):
                return False
        elif e.op == "+" and _is_string_owned(rt):
            # str-family concat -> an owned `String` result
            # (`::tpy::str_concat({self}, {0})` via the resolved __add__).
            # Operands are pinned to the bare-rendering str slice (literal /
            # str / StrView / String); a Char operand's overload wraps it in
            # `char_to_str` with the Char value slice's render -> AST path.
            lt = _operand_type(e.left, locals_, analyzer)
            rt_op = _operand_type(e.right, locals_, analyzer)
            if not (_str_concat_operand(e.left, lt, analyzer)
                    and _str_concat_operand(e.right, rt_op, analyzer)):
                return False
        elif not _resolved_scalar(rt, analyzer):
            return False
        # Both operands IntLiteral-typed NON-NAMES (two literal-seeded-container
        # element reads, `ys[0] + ys[2]`): in a fixed-int target context the AST
        # short-circuits to gen_call_from_fi WITHOUT the paren wrap
        # (_gen_binop's literal-operand branch), and the target is
        # position-dependent -- keep the shape on the AST path. A name operand
        # (incl. an IntLiteral-typed loop var) takes the resolved-binop branch
        # THIR mirrors.
        elif (isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
                and not isinstance(e.left, TpyName)
                and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
                and not isinstance(e.right, TpyName)):
            return False
    elif e.op in _COMPARE_OPS:
        # A scalar comparison -> bool, usable as a value (`x = a < b`) or an
        # `if`/`while` condition. `<`/`==` carry a `{self} OP {0}` template; the
        # derived comparisons (`<= > >= !=`) have rb=None and emit as a bare C++
        # operator. A rb *with* a non-template would emit some other way -> reject.
        if rt is None or not is_bool_type(rt):
            return False
        if rb is not None and not getattr(rb.method, "cpp_template", None):
            # A @native free-function dunder (bytes `==`/`!=` ->
            # `::tpy::bytes_eq`) emits via gen_call_from_fi's native arm,
            # mirrored by the emitter's native binop arm; any other
            # template-less rb takes an unmirrored emit path -> AST.
            if not (rb.method.native_function and rb.method.native_name):
                return False
        lt = _operand_type(e.left, locals_, analyzer)
        rt_op = _operand_type(e.right, locals_, analyzer)
        # Both operands must be value scalars (or both str-slice values, or a
        # Char pair): a record compare also reaches the rb=None bare-operator
        # arm (a user dunder carries no template), but its operands take
        # gen_expr_deref's indirection handling -- `self` renders `(*this)`, a
        # pointer-local `(*p)` -- which the scalar emit does not reproduce.
        # Str/Char names are value types (never pointer-locals), so those pairs
        # are safe; str `<`/`==` templates, the Char rb=None bare `==`/`!=`,
        # and the derived bare operators emit identically on both paths. The
        # str arm is checked before the char arm so a literal-vs-literal
        # compare stays a plain string compare; in the char arm the single-char
        # literal renders as a char literal (`'x'`, lowered via
        # _lower_char_targeted). The name-eligibility check below is no
        # guard here (any in-scope name passes it, whatever its type).
        if not ((_resolved_scalar(lt, analyzer)
                 and _resolved_scalar(rt_op, analyzer))
                or (_str_compare_operand(e.left, lt, analyzer)
                    and _str_compare_operand(e.right, rt_op, analyzer))
                or (_bytes_compare_operand(e.left, lt, analyzer)
                    and _bytes_compare_operand(e.right, rt_op, analyzer))
                or (_char_compare_operand(e.left, lt, analyzer)
                    and _char_compare_operand(e.right, rt_op, analyzer))
                or _union_compare_pair(lt, rt_op)):
            return False
        if _mixed_sign_compare(lt, rt_op):
            return False
    elif e.op in _LOGICAL_OPS:
        # Bool-result and/or over bool operands emits the bare C++ operator
        # (`(l && r)`, rb is None), identical in value and condition position
        # (gen_truthy_expr reduces to the value render for every admitted bool
        # shape). A non-bool result takes _gen_logical_value's temp+ternary; a
        # non-bool operand under a bool result would need per-operand truthiness
        # reasoning -- both stay on the AST path. The literal_facts chain fold
        # (_try_fold_literal_chain) cannot fire in an eligible function: it needs
        # a LiteralType-typed var, which the param/local gates reject. The
        # isinstance-narrowing propagation to the RHS is inert too (isinstance
        # calls are gated out of the operand set).
        if rt is None or not is_bool_type(rt):
            return False
        lt = _operand_type(e.left, locals_, analyzer)
        rt_op = _operand_type(e.right, locals_, analyzer)
        if not (lt is not None and is_bool_type(lt)
                and rt_op is not None and is_bool_type(rt_op)):
            return False
    else:
        # is/in/bitwise take other emit paths, out of the slice -> AST path.
        return False
    return (_expr_eligible(e.left, locals_, analyzer)
            and _expr_eligible(e.right, locals_, analyzer))


def _chained_compare_eligible(e: TpyChainedCompare, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """The inline arm of `_gen_chained_compare`: every INTERMEDIATE operand is
    side-effect-free (`_is_simple_expr` -- imported, so the trigger cannot drift),
    letting the chain desugar to a left-folded `&&` over the sema-synthesized
    pairs (`((a < b) && (b < c))`); endpoints may be complex (evaluated once).
    A non-simple intermediate takes the GCC statement-expression arm
    (`({ auto&& _cmp1 = ...; ... && ...; })`) -> AST path. Each pair is gated
    exactly like a single comparison (`_binop_eligible`: bool result, template
    dunder or bare operator, mixed-sign exclusion, operand eligibility)."""
    if e.pairs is None:
        return False
    if not all(ExpressionGenerator._is_simple_expr(c) for c in e.comparators[:-1]):
        return False
    return all(_binop_eligible(p, locals_, analyzer) for p in e.pairs)


def _unary_not_eligible(e: TpyUnaryOp, locals_: dict[str, TpyType],
                        analyzer) -> bool:
    """Logical `not` over a bool operand -> `(!(operand))`. A bool operand's
    truthiness render (gen_truthy_expr) is its plain value render, so the emit
    is position-independent. Non-bool operands (int / Optional / __bool__
    truthiness wraps) and the arithmetic unaries (`- + ~`) stay on the AST path."""
    if e.op != "!":
        return False
    ot = analyzer.get_expr_type(e.operand)
    return (ot is not None and is_bool_type(ot)
            and _expr_eligible(e.operand, locals_, analyzer))


def _is_len_native(e: TpyExpr) -> bool:
    """Whether `e` is the builtin `len(...)` call -- it resolves to the `tpy::__len__`
    @native free function. A user function named `len` has a different (or no)
    native_name and is excluded, so the emit dispatch keys on the symbol, not the name."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "len"):
        return False
    fi = e.resolved_function_info
    return fi is not None and fi.native_name == "tpy::__len__"


def _is_len_call(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """The eligible `len(name)` form: the builtin len over a single in-scope name of a
    builtin container type or a str-slice value (`::tpy::__len__(name)`, Int32 -- the
    runtime overloads cover std::string and std::string_view). The container/str
    restriction is load-bearing, not cosmetic: a container/str is a by-ref/by-value
    param that emits as the bare name, but a record (or `Optional`) with `__len__`
    bound to a pointer-local would need `(*p)` (the AST's is_indirect_name deref) that
    the bare emit misses -- so only list/dict/set/Array/str (never pointer-locals) are
    admitted. A non-name arg (literal, subscript, call) rides a later cell."""
    if not _is_len_native(e):
        return False
    if e.kwargs or e.double_star_unpack is not None or len(e.args) != 1:
        return False
    arg = e.args[0]
    if not (isinstance(arg, TpyName) and arg.name in locals_):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[arg.name])))
    return (is_list(t) or is_dict(t) or is_set(t) or is_array(t)
            or _resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None  # span/vector overloads
            or is_string_type(t))  # a String local: same std::string overload


def _call_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer,
                   *, stmt_position: bool = False) -> bool:
    if _is_len_call(e, locals_, analyzer):
        return True
    # Only a bare-name call to a same-module plain user free function emits as
    # `name(args)`. Every special form (constructor, generic, cast, isinstance,
    # macro, **kwargs, expression callee) or imported/builtin callee takes a
    # different emit path the slice does not reproduce.
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.call_type is not None or e.type_args or e.inferred_type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        return False
    if e.func_name in analyzer.imported_names:  # cross-module/builtin -> qualified
        return False
    fi = e.resolved_function_info
    if fi is None or fi.cpp_template or fi.native_function or fi.native_name:
        return False
    # Only a DEFAULT-linkage function emits as a bare `name(args)`. @native /
    # @native_c / @export(binding="C") get special name qualification the bare
    # THIRCall emit doesn't reproduce (is_native_import renders `::name`; the
    # extern-C forms use the raw symbol). Gate on the enum so a future linkage is
    # rejected by default rather than silently mis-emitted.
    if fi.linkage != FunctionLinkage.DEFAULT:
        return False
    # A literal-specialized overload emits a mangled name (`f__lit_N`) the
    # bare-name call does not reproduce. The error_return guard is defense in
    # depth: sema already forces an @error_return call into a try/except or a
    # propagating (@error_return) caller, both of which are ineligible anyway.
    if fi.error_return_type is not None:
        return False
    if any(isinstance(p.type, LiteralType) for p in fi.params):
        return False
    if (fi.type_params or fi.is_method or fi.is_staticmethod or fi.is_async
            or fi.is_generator or fi.is_property_getter or fi.is_property_setter):
        return False
    # Exact positional arity -- no omitted defaults, no varargs (the AST would
    # synthesize the missing/packed args, which the slice does not).
    if len(e.args) != len(fi.params):
        return False
    # In value position the result must be an eligible scalar, Char, or a
    # str-slice value (owned str returns by value, StrView by view -- both emit
    # the bare call); as a bare statement the result is discarded, so a `void`
    # (None) return is admitted too. The emit (`callee(args);`) is identical
    # either way.
    ret = analyzer.get_expr_type(e)
    if not (_eligible_scalar(ret) or _eligible_char(ret)
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or (stmt_position and is_void_like_type(ret))):
        return False
    # A str-LITERAL arg to a multi-overload callee is pinned to its param's view
    # form (`std::string_view("...")`, _wants_str_literal_pin) -- the bare-literal
    # emit does not reproduce that, so the shape stays on the AST path. The AST
    # pin peels TpyCoerce wrappers, so a coerced literal must be caught too.
    # (A generic callee never pins, but fi.type_params is already rejected above.)
    if any(isinstance(_peel_coerce(a), TpyStrLiteral) for a in e.args):
        fis = analyzer.registry.get_function(e.func_name)
        if fis is not None and len(fis) > 1:
            return False
    # Every argument is an eligible SCALAR (a value type: copied, never moved,
    # so the bare call is byte-identical), a bare numeric literal resolved
    # against its param slot (a float literal into a double slot renders
    # repr(v) bare; an int literal arrives coerce-wrapped and rides the
    # passthrough -- a BigInt slot's `::tpy::BigInt(v)` wrap stays AST), a
    # str-slice value into a str-family
    # param (both spell the borrow `std::string_view` at the boundary, so the
    # bare emit is byte-identical), or a bare-name CONTAINER into a non-Own
    # concrete container param (the other pass-through slot shape --
    # `use_list(xs)` emits the bare name on both paths). Any other non-value
    # arg (record / Own / Span / protocol slot) crosses an ownership or
    # conversion boundary -- an Own param at its last use auto-moves
    # (`f(std::move(p))`), a Span slot converts -- which the bare-name THIRCall
    # emit does not reproduce.
    return all((_eligible_scalar(analyzer.get_expr_type(a))
                and _expr_eligible(a, locals_, analyzer))
               or _float_literal_pass_through_arg(a, p.type, locals_, analyzer)
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _bytes_pass_through_arg(a, p.type, locals_, analyzer)
               or _char_pass_through_arg(a, p.type, locals_, analyzer)
               or _container_pass_through_arg(a, p.type, locals_, analyzer)
               or _union_pass_through_arg(a, p.type, locals_, analyzer)
               for a, p in zip(e.args, fi.params))


def _union_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare-name union arg into a non-Own slot of the SAME union type (F4).
    An already-union source skips `_gen_union_arg`'s member-lift arms and
    falls to the default bare-name render (a value union's `const
    std::variant<...>&` binds directly; a pointer variant copies by value). A
    member-valued arg (a scalar name, a float literal, a None, a record name)
    hoists a temp / brace-lifts on the AST path -- the gen_call_arg cascade
    frontier -> AST. `Own[union]` slots auto-move -> AST. A READONLY
    pointer-variant slot takes `_gen_union_arg`'s `ptr_variant_to_const` wrap
    (or skips it for narrowed args) -- an emit the slice does not reproduce ->
    AST. NB a const-lifted pointer-variant LOCAL into a mutable slot renders
    the bare name on BOTH paths (a pre-existing AST miscompile, BUGS.md) --
    byte-identical, so the shape is not carved out here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_send_sync(pt)
    is_readonly_slot = isinstance(pt, ReadonlyType)
    pt = unwrap_readonly(pt)
    if isinstance(pt, OwnType):
        return False
    ut = _eligible_value_union(pt)
    if ut is None:
        ut = _eligible_ptr_union(pt, analyzer)
        if ut is None or is_readonly_slot:
            return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return at == ut and _expr_eligible(a, locals_, analyzer)


def _str_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """A str-slice arg into a non-Own `str`/`StrView`/`String` param slot. A
    `str`/`StrView` param renders `std::string_view`, and every slice source
    lands in it bare: a param/view local IS a string_view, an owned local
    converts implicitly, a literal is const char[N]. A `String` slot takes
    String values bare and coerced str/StrView sources through the coerce
    arm. An `Own[...]` slot materializes an owned copy the bare emit does not
    reproduce (the gen_call_arg auto-move cascade) -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, NominalType) and is_string_type(pt):
        # A `String` slot (`const std::string&`): an owned String value binds
        # bare; a str/StrView source arrives as a str_to_string /
        # strview_to_string coerce (identity for a NUL-free literal,
        # `std::string(x)` otherwise) -- rendered by the coerce arm itself.
        at = analyzer.get_expr_type(a)
        return ((_resolved_str_value(at, analyzer) is not None
                 or _is_string_owned(at))
                and _expr_eligible(a, locals_, analyzer))
    if not (isinstance(pt, NominalType) and (is_str_type(pt) or is_str_view_type(pt))):
        return False
    if isinstance(a, TpyStrLiteral):
        return True
    return (_resolved_str_value(analyzer.get_expr_type(a), analyzer) is not None
            and _expr_eligible(a, locals_, analyzer))


def _bytes_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bytes-slice arg into a non-Own `bytes`/`BytesView` param slot. The
    param renders `std::span<const uint8_t>`; a param/view local IS a span, an
    owned local (vector) converts implicitly, and a literal takes gen_call_arg's
    static-span pin (`::tpy::bytes_literal(...)`, lowered BORROW). The
    pin keys on the RAW ptype (`is_bytes_type(ptype) or is_bytes_view_type(
    ptype)`), so a wrapped slot (readonly/Own) rejects the literal -- the AST
    renders it owned there, a shape this arm does not thread. An `Own[bytes]`
    slot materializes an owned copy for value args too -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    # gen_call_arg peels coerce wrappers before its literal span pin (a literal
    # into a BytesView slot arrives wrapped in the view coercion); mirror it.
    lit = _peel_coerce(a)
    if isinstance(lit, TpyBytesLiteral):
        return is_bytes_type(pt) or is_bytes_view_type(pt)
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not (isinstance(pt, NominalType) and (is_bytes_type(pt) or is_bytes_view_type(pt))):
        return False
    return (_resolved_bytes_value(analyzer.get_expr_type(a), analyzer) is not None
            and _expr_eligible(a, locals_, analyzer))


def _char_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A Char value into a Char param slot -- both spell `char`, passed bare
    (a value scalar in all but name) -- or a single-char str literal into one
    (the target-typed `'x'` render, gen_expr's char-literal arm; lowered
    param-aware via `_lower_char_targeted`). A multi-char literal never
    renders as a char literal -> AST path (sema rejects it anyway). An
    `Own[Char]` slot is rejected conservatively (the unwrap chain does not
    peel Own)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if not _eligible_char(pt):
        return False
    if isinstance(a, TpyStrLiteral):
        return len(a.value) == 1
    return (_eligible_char(analyzer.get_expr_type(a))
            and _expr_eligible(a, locals_, analyzer))


def _float_literal_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                    locals_: dict[str, TpyType],
                                    analyzer) -> bool:
    """A bare float literal (FloatLiteralType -- sema leaves it unwrapped in a
    matching float slot) into a double param slot: both paths render repr(v)
    bare (gen_expr's TpyFloatLiteral double branch == _emit_literal's float
    arm). A Float32 slot cannot reach here -- sema wraps the literal in the
    `float_literal_to_float32` coercion (the `f`-suffix render), which the
    coerce gate rejects -- but pin the slot to double anyway so the pairing
    is explicit. inf/nan literals (`1e400`) are rejected by _expr_eligible's
    isfinite check."""
    if not isinstance(a, TpyFloatLiteral):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    return (is_float_type(pt) and not is_float32_type(pt)
            and _expr_eligible(a, locals_, analyzer))


def _scalar_pass_through_slot(ptype: TpyType | None, analyzer) -> bool:
    """A method param slot the inline-template arg path passes a scalar into
    bare: a value scalar or `Own[scalar]`. Scalars are value types -- copied,
    never moved -- and `gen_call_arg`'s Own handling skips the copy+move temp for
    a template/native callee (`inline_template=True`), so the arg emits as the
    bare expression. Every other slot shape (record / Optional / union / tuple /
    Ptr / str / protocol / unsubstituted type param) takes a lift, move, or
    conversion the slice does not reproduce. A literal-seeded container's fi
    carries `Own[IntLiteral]` slots (resolved like every scalar-typed check)."""
    if ptype is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(ptype))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return _resolved_scalar(t, analyzer)


def _container_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                locals_: dict[str, TpyType], analyzer) -> bool:
    """A container arg the AST passes as the bare name: a bare in-scope name
    with a builtin-container binding, into a NON-Own concrete builtin-container
    param (`std::vector<T>&` / `const ordered_map<K, V>&` / ...).
    `gen_call_arg`'s ownership cascade never fires for that slot shape (`own is
    None`), so the emit is the bare name on both paths. An `Own[container]`
    slot auto-moves at last use (`f(std::move(xs))`), a `Span` / protocol
    (`Iterable`) slot converts (`::tpy::as_mut_span(xs)` / adapter wrap), and an
    `Optional[container]` slot lifts (`&(xs)`), so those stay on the AST path.
    The binding type is read from `locals_`, per the receiver-gate convention on
    `_resolved_scalar`. NB unlike the sibling `_scalar_pass_through_slot` (slot
    check only; the arg shape is checked separately at its call sites), this
    predicate owns BOTH sides -- the container arg shape is inseparable from the
    slot shape it pairs with."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if not (is_list(at) or is_dict(at) or is_set(at) or is_array(at)):
        return False
    if ptype is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    # Explicit Own/Optional rejects (the move / address-of lift slots); the
    # concrete-container check below would also exclude them, but the invariant
    # should be self-evident, mirroring gen_call_arg's own Own detection.
    if isinstance(pt, (OwnType, OptionalType)):
        return False
    return is_list(pt) or is_dict(pt) or is_set(pt) or is_array(pt)


def _positional_only_template(tmpl: str, n_args: int) -> bool:
    """Whether a `@cpp_template` body contains only in-range positional
    placeholders (`{0}`, `{1}`, ...) and `{{`/`}}` literal-brace escapes --
    the subset `expand_cpp_template` can render with no receiver and no
    substitution context. A surviving named field (`{cpp}`, `{self}`, a type
    param) means sema's substitution did not fully resolve the template, so
    the call must stay on the AST path (which has the substitution machinery)."""
    i, n = 0, len(tmpl)
    while i < n:
        c = tmpl[i]
        if c == "{":
            if i + 1 < n and tmpl[i + 1] == "{":
                i += 2
                continue
            close = tmpl.find("}", i + 1)
            field = tmpl[i + 1:close] if close != -1 else ""
            if not field.isdigit() or int(field) >= n_args:
                return False
            i = close + 1
        elif c == "}":
            # Only the `}}` escape is admitted; a lone `}` (which expand would
            # pass through literally) never occurs in a real template -- reject
            # conservatively.
            if not (i + 1 < n and tmpl[i + 1] == "}"):
                return False
            i += 2
        else:
            i += 1
    return True


def _ctor_arg_slot_ok(ptype: TpyType | None, analyzer) -> bool:
    """A scalar-ctor param slot the arg passes bare: a value scalar /
    `Own[scalar]` (`_scalar_pass_through_slot`), or the generic conversion
    overload's unsubstituted method type param (`__init__[T: AnyFixedInt](self,
    x: T)`, the `int_cast_check` arm). `gen_call_arg`'s target hint is inert for
    a TypeParamRef slot -- not Own, not fixed-int, no borrow/storage lift -- so
    an eligible-scalar arg emits bare exactly as into a concrete scalar slot.
    The Ref/readonly peel mirrors gen_call_arg's own `ptype_inner` unwrap (the
    stub stores the generic param as `Ref(TypeParamRef)`)."""
    if ptype is not None and isinstance(
            unwrap_readonly(unwrap_ref_type(ptype)), TypeParamRef):
        return True
    return _scalar_pass_through_slot(ptype, analyzer)


def _scalar_ctor_call_eligible(e: TpyCall, locals_: dict[str, TpyType],
                               analyzer) -> bool:
    """A builtin scalar type-constructor call -- `Int32(0)` / `UInt32(x)` /
    `Int64(a + b)` / `Float64(1.5)` / `bool(n)` -- resolved by sema to a
    `@cpp_template` `__init__` overload and emitted via `_gen_call`'s
    `fi.cpp_template and not call_type` branch (-> `gen_template_or_native_call`
    -> `gen_call_from_fi`'s template arm). Sema already substituted `{cpp}` /
    class type params into the stored template (`_resolve_cpp_template_type_
    params`), and `_check_cast_safe`'s `static_cast` rewrite lands on the same
    node fact, so the emit is a pure `expand_cpp_template(template, None,
    *args)` -- the gate requires the template be positional-only to keep any
    still-unsubstituted shape on the AST path.

    Admitted: an eligible-scalar result (fixed-int / bool / double) and
    eligible-scalar args in scalar / `Own[scalar]` / method-type-param slots
    (the bare `gen_call_arg` pass-through). str / bytes / BigInt / Float32 /
    Char conversions fail the scalar checks (a `@native(function=True)` ctor
    like Char has no cpp_template at all); `int(...)` produces BigInt; enum
    ctors carry `enum_from_value`; borrowing-view ctors (StrView/Span) set
    `call_type` -- all stay on the AST path."""
    fi = _template_init_call_fi(e)
    if fi is None:
        return False
    if not _eligible_scalar(analyzer.get_expr_type(e)):
        return False
    return all(_ctor_arg_slot_ok(p.type, analyzer)
               and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
               and _expr_eligible(a, locals_, analyzer)
               for a, p in zip(e.args, fi.params))


def _template_init_call_fi(e: TpyCall) -> 'FunctionInfo | None':
    """The resolved `__init__` FunctionInfo of a bare-name `@cpp_template`
    type-constructor call in the pure-template-expansion shape (the `_gen_call`
    `fi.cpp_template and not call_type` branch), or None. Shared by the scalar
    and slice-object ctor gates; each adds its own result/arg checks."""
    if not isinstance(e.func, TpyName):
        return None
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Markers that take earlier / different _gen_call branches. Explicit
    # type_args are rejected; INFERRED type args (the generic int_cast_check
    # overload) are fine -- the positional-only template makes gen_call_from_fi's
    # substitution loop a no-op.
    if (e.call_type is not None or e.type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        return None
    fi = e.resolved_function_info
    if fi is None or not (fi.is_method and fi.name == "__init__"):
        return None
    # Post-call wrappers / special member forms the bare template emit does not
    # reproduce (mirrors _method_call_eligible's fi rejects).
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or fi.is_async or fi.is_generator
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if not fi.cpp_template or not _positional_only_template(fi.cpp_template,
                                                            len(e.args)):
        return None
    if len(e.args) != len(fi.params):
        return None
    return fi


def _slice_ctor_call_eligible(e: TpyCall, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """A slice-object constructor call -- `basic_slice(1, 3)` / `slice(a, b, c)`
    -- the same pure-template-expansion shape as the scalar ctors
    (`::tpy::BasicSlice{{{0}, {1}}}` / `::tpy::Slice{{{0}, {1}, {2}}}`). The
    stub params are `Int32 | None` (value-repr Optional) slots, into which
    gen_call_arg passes every admitted arg bare: an in-range int literal / a
    fixed-int name renders itself, a `None` literal renders `std::nullopt` (the
    value-repr Optional render, THIRLiteral's STORAGE-form None). Coerced
    (widening / BigInt) bound sources fail `_expr_eligible`'s coerce gate and
    stay on the AST path."""
    fi = _template_init_call_fi(e)
    if fi is None:
        return False
    if not _slice_object_type(analyzer.get_expr_type(e)):
        return False
    return all(isinstance(a, TpyNoneLiteral)
               or (_resolved_scalar(analyzer.get_expr_type(a), analyzer)
                   and _expr_eligible(a, locals_, analyzer))
               for a in e.args)


def _method_call_eligible(e: TpyMethodCall, locals_: dict[str, TpyType], analyzer,
                          *, stmt_position: bool = False) -> bool:
    """A method call on a bare-name builtin-container receiver whose emit is the
    pass-through subset of `_gen_method_call`: `xs.append(v)` -> `xs.push_back(v)`
    (@native member), `xs.pop()` -> `::tpy::pop_back(xs)` (@native free function),
    `xs.sort()` -> `std::stable_sort(...)` (@cpp_template). The receiver is an
    in-scope container name (the admitted list/dict param family -- never a
    pointer-local, so no deref/arrow/narrowing arises); args are value scalars
    into scalar / `Own[scalar]` slots, str-slice values into non-Own str-family
    slots (`d.pop(k)` -- a dict's `readonly[K]` key slot renders the arg bare),
    or pass-through container names into
    non-Own container slots (`d.update(e)` -- see `_container_pass_through_arg`);
    the result is a value scalar or a str-slice value (or void, discarded, in
    statement position). Every special-emit marker (static / super /
    module-qualified / typed-dict / nested-ctor / callable-field / macro / fstr /
    deref chain / Optional runtime check) takes a different `_gen_method_call`
    path and is rejected."""
    if not isinstance(e.obj, TpyName) or e.obj.name not in locals_:
        return False
    # The declared binding type, not get_expr_type: a container-literal local's
    # use sites carry the pre-resolution PendingListType (the AST path unwraps it
    # in TypeResolver.get_resolved_type); the binding type is post-resolution.
    # Mirrors _is_len_call's locals_ lookup.
    if not _container_scalar_read(locals_[e.obj.name], analyzer):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.is_static_call or e.super_parent_type is not None
            or e.unbound_self_parent_type is not None
            or e.user_module_call is not None
            or e.builtin_module_call is not None
            or e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None or e.type_args
            or e.inferred_type_args or e.deref_depth
            or e.deref_narrowed_to is not None
            or e.needs_optional_runtime_check):
        return False
    fi = e.resolved_function_info
    if fi is None:
        return False
    # A consuming method moves the receiver (`std::move(xs)`); a `{cpp}` template
    # placeholder substitutes the return type; `cpp_return_type` wraps the call in
    # a static_cast; @error_return unwraps via a statement expression; a
    # LiteralType param mangles the member name. None are reproduced.
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or (fi.cpp_template is not None and "{cpp}" in fi.cpp_template)
            or any(isinstance(p.type, LiteralType) for p in fi.params)
            or fi.is_async or fi.is_generator
            or fi.is_property_getter or fi.is_property_setter):
        return False
    # Exact positional arity -- no omitted defaults, no varargs.
    if len(e.args) != len(fi.params):
        return False
    # A void method's call carries no resolved expr type (None), unlike a void
    # free-function call (NoneType); both are discard-only, statement position.
    # An owned-str result (`xs.pop()` on list[str] -> `::tpy::pop_back(xs)`)
    # emits the same bare call and lands in the S1 str sinks (S5).
    ret = analyzer.get_expr_type(e)
    if not (_resolved_scalar(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return False
    # A str arg into a non-Own str-family slot passes bare, like a free-call arg
    # (`d.pop(k)` -> `::tpy::dict_pop(d, k)`; builtin-container methods never
    # take the `_wants_str_literal_pin` path -- that pin is the free-call /
    # user-record-method arg paths only). An `Own[str]` slot (`xs.append(s)`)
    # materializes an owned copy / a `std::move(__tmp_N)` temp the bare emit
    # does not reproduce -- `_str_pass_through_arg` rejects Own, so it stays AST.
    return all((_scalar_pass_through_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _container_pass_through_arg(a, p.type, locals_, analyzer)
               for a, p in zip(e.args, fi.params))


def _is_builtin_print(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a call to the builtin `print` (not a user/local shadow): the builtin
    is in `imported_names` and `print` is not redefined as a same-module function /
    record or bound as a local. A shadowed `print` conservatively stays on the AST
    path (never a divergence). This is stricter than the AST print path, which
    intercepts `print(...)` unconditionally regardless of a user shadow."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "print"):
        return False
    reg = analyzer.registry
    return ("print" in analyzer.imported_names
            and reg.get_function("print") is None
            and reg.get_record("print") is None
            and "print" not in declared)


def _print_arg_form(t: TpyType) -> PrintForm:
    """The `std::cout <<` wrapper for a print arg's resolved type -- mirrors the
    gen_print per-type dispatch for the eligible subset. bool is checked before
    the 8-bit-int case (a `bool` has an 8-bit int trait but must format as
    `True`/`False`, not `static_cast<int>`)."""
    if is_bool_type(t):
        return PrintForm.BOOL
    if is_float_type(t):  # float64 -- float32 is excluded by arg eligibility
        return PrintForm.FLOAT
    # A bytes-slice value (incl. a still-pending bytes local binding -- the
    # view/owned resolution doesn't change the printer) wraps in BytesPrinter
    # (gen_print's is_any_bytes_type arm; bytearray is gated out of the args).
    if _is_bytes_family(t):
        return PrintForm.BYTES
    tr = int_traits_of(t)
    if tr is not None and tr.bits == 8:
        return PrintForm.INT8
    return PrintForm.RAW


def _is_bytes_family(t: TpyType | None) -> bool:
    """A bytes/BytesView value or a pending bytes binding -- an analyzer-free
    family check for positions where the view/owned resolution is irrelevant
    (the print wrapper)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return t.family is BYTES_FAMILY
    return is_bytes_type(t) or is_bytes_view_type(t)


def _print_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer) -> bool:
    """A `print(<args>)` in the no-kwargs common-arg subset: every arg is a str
    literal, an eligible scalar (fixed-int / bool / double), a Char (streamed
    raw -- gen_print's direct-output arm; Char has no int_traits, so no int8
    cast), or a str-slice value (a str/StrView name or str-returning call --
    string and string_view stream raw, like the AST's is_any_str_type arm).
    Any `sep=`/`end=`/`file=`/`flush=` kwarg, `**`-unpack, f-string, or other
    non-scalar arg falls back to the AST path (gen_print's richer cases)."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    for a in e.args:
        if isinstance(a, (TpyStrLiteral, TpyBytesLiteral)):
            # A bytes literal prints owned (gen_print threads no target).
            continue
        at = analyzer.get_expr_type(a)
        if ((_resolved_scalar(at, analyzer) or _eligible_char(at)
             or _resolved_str_value(at, analyzer) is not None
             or _resolved_bytes_value(at, analyzer) is not None  # BytesPrinter
             or _is_string_owned(at))  # a concat result / String local: raw <<
                and _expr_eligible(a, locals_, analyzer)):
            continue
        return False
    return True


# Sentinel for an f-string arg type outside the mirrored wrapper rows.
_FSTRING_INELIGIBLE = object()


def _fstring_arg_wrap(a: TpyExpr, analyzer) -> 'str | None | object':
    """The Python-compatible formatting wrapper for one interpolated f-string
    arg, as a positional `{0}` template (None = pass through bare) -- the
    mirrored subset of `_gen_fstring`'s per-arg table -- or `_FSTRING_INELIGIBLE`
    for any row the slice does not reproduce (BigInt `.to_string()`, enum,
    user/union `__str__`, containers, float32, Char). bool is checked before
    the 8-bit-int row, mirroring the AST order (bool carries 8-bit int traits
    but must format as True/False). An IntLiteral-typed arg (`f"{5}"`) resolves
    through the module default int -- fixed widths format bare like the AST's
    fall-through; a BigInt default rejects (the runtime-bigint row)."""
    if isinstance(a, TpyStrLiteral):
        return None  # const char[N] formats directly
    t = analyzer.get_expr_type(a)
    if t is None:
        return _FSTRING_INELIGIBLE
    if _resolved_str_value(t, analyzer) is not None:
        return None  # string/string_view format directly
    if _is_string_owned(t):
        return None  # a concat-result std::string formats directly too
    if is_bool_type(t):
        return "::tpy::bool_to_str({0})"
    # A bare float literal (FloatLiteralType) resolves to float64 in an
    # f-string slot -- there is no Float32-typed context inside one -- so it
    # takes the same row as a concrete double. A concrete Float32 arg takes a
    # static_cast row the slice does not mirror (rejected below).
    if isinstance(t, FloatLiteralType) or (is_float_type(t)
                                           and not is_float32_type(t)):
        return "::tpy::float_to_str({0})"
    rt = resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
    if is_fixed_int_type(rt):
        tr = int_traits_of(rt)
        if tr is not None and tr.bits == 8:
            return "static_cast<int>({0})"
        return None
    return _FSTRING_INELIGIBLE


def _fstring_eligible(e: TpyFString, locals_: dict[str, TpyType],
                      analyzer) -> bool:
    """An f-string in the mirrored slice: literal segments plus interpolated
    args that are themselves eligible exprs with a mirrored wrapper row.
    Conversions (`!r`/`!s`) and format specs (they change the placeholder and
    the bool row) stay on the AST path. FStr-macro f-strings never reach this
    gate: they only arise as args to `FStr`-typed params, which the call-arg
    slot pins reject."""
    for part in e.parts:
        if isinstance(part, str):
            continue
        if part.conversion != FSTRING_CONV_NONE or part.format_spec is not None:
            return False
        if not _expr_eligible(part.expr, locals_, analyzer):
            return False
        if _fstring_arg_wrap(part.expr, analyzer) is _FSTRING_INELIGIBLE:
            return False
    return True


def _expr_eligible(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    if isinstance(e, TpyName):
        # A name outside the local/param set is a module/native/cross-module
        # global: the AST path resolves it to a qualified C++ symbol, which the
        # slice does not yet materialize. Reject -> stays on the AST path.
        # A narrowing-divergent union read (declared union, member-typed read)
        # is a pre-existing AST miscompile -> AST path (see the helper).
        return (e.name in locals_
                and not _union_binding_divergent(e, locals_, analyzer))
    if isinstance(e, TpyIntLiteral):
        # Only literals that emit as a bare value in any fixed-int slot. Wider
        # values need a `ull` suffix / `static_cast` that the slice's emitter
        # does not reproduce (see ExpressionGenerator._gen_int_literal_value).
        return -2**31 <= e.value <= 2**31 - 1
    if isinstance(e, TpyFloatLiteral):
        # A finite float literal renders as repr(value) in a double slot, byte
        # for byte (the AST path's _gen_float_literal_value double branch). inf/
        # nan only arise from float(...) calls, never a bare literal, but guard
        # anyway -- repr(inf)/repr(nan) are not valid C++.
        return math.isfinite(e.value)
    if isinstance(e, TpyBoolLiteral):
        return True  # True/False -> true/false; no target-type dependence
    if isinstance(e, TpyStrLiteral):
        # const char[N] via cpp_string_literal_expr -- implicitly convertible to
        # every str-slice slot (string_view AND string), so never wrapped. The
        # positions that reach here are gated by their slot checks (a str-slice
        # decl init / compare operand / call arg / print arg / return value).
        return True
    if isinstance(e, TpyBytesLiteral):
        # Unlike a str literal, the render is TARGET-dependent (owned vector vs
        # static-storage span), so every admitting position threads the owned
        # flag at lowering: decl inits/reassigns and returns key on the resolved
        # binding/return type, call args on the param slot (the gen_call_arg
        # span pin), and the target-less positions (print args, compare
        # operands) render owned -- gen_expr's default arm. Positions outside
        # that set reject the literal on their own type checks (a bytes value
        # is not a scalar / str / container).
        return True
    if isinstance(e, TpyFieldAccess):
        # A scalar or Char field read off an F1-record receiver (`recv.field`,
        # value form -- the access render is type-independent, and a Char value
        # lands only in positions whose own gates admit it). The non-value field
        # source for a borrow-local binding is handled in the var-decl branch,
        # not here -- a non-value field read is not a value expression.
        ft = analyzer.get_expr_type(e)
        return ((_eligible_scalar(ft) or _eligible_char(ft))
                and (_field_receiver_ok(e, locals_, analyzer)
                     or _field_over_subscript_ok(e, locals_, analyzer)
                     or _optional_field_over_subscript_ok(e, locals_, analyzer)))
    if isinstance(e, TpySubscript):
        # A value-result tuple subscript read `t[N]` (`std::get<N>(t)`) off an
        # eligible tuple receiver; a container subscript read `c[i]`
        # (`::tpy::__getitem__(c, i)` / bounds-safe operator[]) off a
        # list[scalar] / dict[int, scalar] receiver; a str subscript `s[i]`
        # (-> Char, same emit shapes); a bytes subscript `b[i]` (-> UInt8,
        # `::tpy::bytes_getitem`); or a str/bytes slice (`::tpy::str_slice` /
        # `::tpy::bytes_slice` views, the stepped owned variants).
        return (_tuple_subscript_value_read(e, locals_, analyzer) is not None
                or _container_subscript_value_read(e, locals_, analyzer)
                or _str_subscript_char_read(e, locals_, analyzer)
                or _bytes_subscript_read(e, locals_, analyzer)
                or _str_slice_read(e, locals_, analyzer))
    if isinstance(e, TpyFString):
        # An owned-str-producing `std::format(...)` / `std::string("...")`
        # expression (STORAGE form) -- composes into the S1 owned-str sinks
        # (decl init, return, print/call arg, compare operand) bare.
        return _fstring_eligible(e, locals_, analyzer)
    if isinstance(e, TpyBinOp):
        return _binop_eligible(e, locals_, analyzer)
    if isinstance(e, TpyUnaryOp):
        # A negated int literal (`-3`) folds to a plain literal on both paths
        # (the AST's _gen_unaryop literal-negation branch); a negated FLOAT
        # literal takes the resolved __neg__ template (`-(1.5)`), a render the
        # slice does not reproduce -> AST path.
        if _folded_neg_int_literal(e, analyzer) is not None:
            return True
        return _unary_not_eligible(e, locals_, analyzer)
    if isinstance(e, TpyChainedCompare):
        return _chained_compare_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCall):
        # A same-module free-function call, or a builtin scalar / slice-object
        # type-constructor call (`Int32(x)` / `basic_slice(1, 3)`, emitted via
        # its resolved __init__ @cpp_template).
        return (_call_eligible(e, locals_, analyzer)
                or _scalar_ctor_call_eligible(e, locals_, analyzer)
                or _slice_ctor_call_eligible(e, locals_, analyzer))
    if isinstance(e, TpyMethodCall):
        # A value-scalar-returning container method call (`x = xs.pop()`).
        return _method_call_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCoerce):
        # The literal-into-typed-slot pair and the str-family cross-type
        # coercions, position-disposed (identity passthrough vs the
        # `std::string(x)` materialization -- see _coerce_disposition). Other
        # coercions (widening, bigint, int<->float, float32, optional-wrap,
        # Char) take their own emit paths.
        return (_coerce_disposition(e) is not None
                and _expr_eligible(e.expr, locals_, analyzer))
    return False


def _f1_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """An F1-eligible param: a value scalar, an F1-record passed by reference
    (`T&` / `const T&`, accessed `.`), an F3 borrow-form pointer-repr tuple
    (`std::tuple<..., T*>`, a borrow source for a `tuple_to_storage` field write), a
    pure value-scalar tuple (`const std::tuple<...>&`, read by subscript), a
    by-value slice object (`basic_slice` / `slice`, a str subscript index), a
    value-element container (`list[scalar|str]` / `Array[scalar|str, N]` /
    `dict[fixed-int|str, scalar|str]`, read by subscript),
    or a record-element list (`list[record]`, iterated by `for x in c` -- the signature
    stays on the AST path per M1). Optional/view-keyed-container/cross-module/native
    record params stay on the AST path."""
    return (_eligible_scalar(ptype) or _eligible_char(ptype)
            or _f1_record(ptype, analyzer)
            or _resolved_str_value(ptype, analyzer) is not None
            or _resolved_bytes_value(ptype, analyzer) is not None
            or _slice_object_type(ptype)
            or _f1_tuple(ptype, analyzer) is not None
            or _value_scalar_tuple(ptype)
            or _eligible_value_union(ptype) is not None
            or _eligible_ptr_union(ptype, analyzer) is not None
            or _container_scalar_read(ptype, analyzer)
            or _container_record_iter(ptype, analyzer))


def _function_eligible(func: TpyFunction, analyzer,
                       self_type: 'TpyType | None' = None) -> bool:
    # A record-owned callable is admitted when its owning record is an
    # F1-record (`self_type` passed by the caller). All method kinds funnel
    # their bodies through gen_body, so only the receiver model differs:
    # instance methods
    # (M1/M2, dunders included -- the C++ operator wrappers delegating to them
    # are structural emission, not body emission) and property getters/setters
    # lower with a `self` (`this`) receiver; static methods lower like free
    # functions (no receiver -- the `static` prefix, the setter's `set_` rename
    # and the getter's ref-return arm are all signature-only; the getter's
    # body-side return arm needs a pointer-repr Optional/union return, which
    # _eligible_return rejects). A record param's const verdict comes from the
    # method's FunctionInfo on the owning record -- see `_param_is_const`.
    if func.is_method:
        if self_type is None or not _f1_record(self_type, analyzer):
            return False
        # Inplace dunders (__iadd__ ...): the AST forces const params on them
        # (CONST_PARAMS_METHODS), a verdict `_param_is_const` does not mirror;
        # their mandatory `return self` (`return *this;`) is outside the slice
        # anyway.
        if func.name in CONST_PARAMS_METHODS:
            return False
        # @readonly on a @staticmethod is not sema-rejected but emits with the
        # readonly verdicts dropped (no const overload, no forced-const
        # params) -- an asymmetry the mirror does not reproduce.
        if func.is_staticmethod and func.is_readonly:
            return False
    elif func.is_staticmethod:
        # Defensive: the parser sets is_method=True on staticmethods, so a free
        # function should never carry the flag.
        return False
    if func.is_overload_stub or func.native_function or func.is_consuming:
        return False
    # An overload IMPL body is emitted once per stub with per-stub dead-branch
    # facts (literal_overload_facts / overload_param_types), but gen_body's THIR
    # interception keys on id(func) -- routing the shared impl would hijack
    # every specialization with the unspecialized body. Reject any callable in
    # a multi-entry overload set (functions and methods alike). Sole carve-out:
    # a property getter+setter pair shares one method name in the registry but
    # each has its own body (no shared-impl hijack possible).
    if func.is_method:
        ri = analyzer.registry.get_record_for_type(self_type)
        overloads = ri.get_method_overloads(func.name) if ri is not None else []
        if len(overloads) > 1:
            is_property_pair = (
                len(overloads) == 2
                and any(fi.is_property_getter for fi in overloads)
                and any(fi.is_property_setter for fi in overloads))
            if not is_property_pair:
                return False
    else:
        fis = analyzer.registry.get_function(func.name)
        if fis is not None and len(fis) > 1:
            return False
    if func.builtin_decorator_key is not None:
        return False
    if func.is_async or func.is_generator:
        return False
    if func.error_return is not None or func.type_params:
        return False
    if func.linkage != FunctionLinkage.DEFAULT:
        return False
    for _name, ptype in func.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        # Free functions and instance methods both take F1-record params; the const
        # verdict comes from the function's own const_borrow_params (a method's read
        # via the owning record at lowering). This holds for readonly callables too:
        # for a plain F1-record (ref) param the readonly forced-const verdict and the
        # inferred const_borrow_params verdict coincide (both const iff the param is
        # not directly mutated / address-escaped -- see decide_param_const), so no
        # readonly carve-out is needed (and a readonly callable cannot mutate a param
        # anyway, so its record params are uniformly const).
        if not _f1_param_eligible(pt, analyzer):
            return False
    # A reassigned param of a type flagged param_needs_copy_for_reassign (owned
    # str/bytes/String, BigInt -- const-ref params that cannot reassign in
    # place) gets a mutable owned copy hoisted by the AST prologue
    # (`std::string p_ = std::string(p);` + body-wide rename) -- a shape the
    # slice does not reproduce. Reject the function; the flag is the exact AST
    # trigger (gen_function's scan.reassigned check), so by-value params
    # (scalars, Char, StrView) reassign in place on both paths and stay
    # routed. Non-value params cannot be reassigned at all (sema rejects the
    # rebind), so no pointer-local prologue arises here either.
    scan = analyzer.function_scan_results.get(id(func))
    if scan is not None and scan.reassigned:
        for name, ptype in func.params:
            pt = ptype if isinstance(ptype, TpyType) else None
            if (name in scan.reassigned and pt is not None
                    and pt.param_needs_copy_for_reassign()):
                return False
    rt = func.return_type if isinstance(func.return_type, TpyType) else None
    return _eligible_return(rt, analyzer) if func.return_type is not None else True


def _var_decl_type(stmt: TpyVarDecl, analyzer) -> TpyType | None:
    # Mirror codegen's _resolve_target_type (value-scalar subset): the binding
    # type captures sema's local deduction -- e.g. a literal-seeded local that
    # retro-widens to UInt64 from later usage -- which the init's type alone
    # (IntLiteralType) does not. Fall back to the init type, then resolve any
    # remaining int literal to the module default int.
    target = resolve_stmt_binding_type(stmt, analyzer, include_global_binding=False)
    if target is None and stmt.init is not None:
        target = analyzer.get_expr_type(stmt.init)
    if target is None:
        return None
    target = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target)))
    if isinstance(target, OwnType):
        target = target.wrapped
    return resolve_int_literals(target, analyzer.ctx.default_int_for_literal)


def _condition_eligible(cond: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    # An `if`/`while` condition: a bare bool local/param (`if flag:`), a scalar
    # comparison, a bool-result and/or, a bool-operand `not`, or an inline-arm
    # chained comparison. For each admitted shape the truthiness render
    # (gen_truthy_expr) equals the value render, so the emitter reuses
    # _emit_expr for conditions. A comparison/logical BinOp routes through
    # _binop_eligible so it gets the same mixed-sign / bool-operand gates as in
    # value position; an ARITH binop condition (`if a + b:`, int truthiness) is
    # excluded by the op-set check. A bool-literal condition stays on the AST
    # path (it may dead-branch-eliminate).
    if isinstance(cond, TpyName):
        rt = analyzer.get_expr_type(cond)
        return cond.name in declared and rt is not None and is_bool_type(rt)
    if isinstance(cond, TpyBinOp) and cond.op in _COMPARE_OPS | _LOGICAL_OPS:
        return _binop_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyUnaryOp):
        return _unary_not_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyChainedCompare):
        return _chained_compare_eligible(cond, declared, analyzer)
    return False


def _range_bound_literal_value(arg: TpyExpr) -> int | None:
    # The AST's inline-vs-hoist decision for a range bound (_is_literal_range_arg):
    # an inlinable bare int literal (possibly behind the int_literal coerce) vs a
    # name/expr hoisted to a temp. Only the bare-literal subset the slice admits is
    # mirrored, so it agrees with _extract_int_literal regardless of that helper's
    # evolution. The int32 bound keeps the value a bare token (no `ull`/cast).
    while isinstance(arg, TpyCoerce) and arg.coercion.name == _INT_LIT_COERCION:
        arg = arg.expr
    if isinstance(arg, TpyIntLiteral) and -2**31 <= arg.value <= 2**31 - 1:
        return arg.value
    # A negated literal (`range(-3, 3)`) is inlined by the AST too
    # (_extract_int_literal's negated arm).
    if (isinstance(arg, TpyUnaryOp) and arg.op == "-"
            and isinstance(arg.operand, TpyIntLiteral)
            and -2**31 <= -arg.operand.value <= 2**31 - 1):
        return -arg.operand.value
    return None


def _range_bound_eligible(arg: TpyExpr, declared: dict[str, TpyType],
                          analyzer) -> bool:
    # Tight slice: an inlinable int literal, or a bare name of an
    # already-declared fixed-int local/param (hoisted to a __start/__stop temp).
    # Binop/call bounds are deferred -- they need the byte-identical net to
    # confirm gen_range_args' _gen_expr_deref(arg, ptype) matches _emit_expr.
    if isinstance(arg, TpyName):
        # `declared` holds bool/float locals too, so the bound's resolved type
        # must be checked fixed-int (it renders into a `cpp_elem` temp).
        return is_fixed_int_type(declared.get(arg.name))
    # `range(len(c))` -- the Int32-valued len builtin, hoisted into a `__stop_N` temp
    # like any non-literal bound; unblocks the bounds-safe container-subscript branch.
    if _is_len_call(arg, declared, analyzer):
        return True
    return _range_bound_literal_value(arg) is not None


def _is_range_call(it: TpyExpr) -> bool:
    """The `range(...)` iterable form -- the for-loop cell's range-vs-container
    discriminator. Shared by `_for_range_eligible` and `_lower_stmt` so eligibility and
    lowering can't drift on which shape a for-loop takes."""
    return isinstance(it, TpyCall) and it.func_name == "range"


def _for_loop_shape_ok(stmt: TpyForEach, analyzer, declared: dict[str, TpyType]) -> bool:
    """The for-loop shape guards shared by the range-for and container-for cells: no
    async / tuple-unpack / for-else / enum / consuming / hoisted-loop-var; no branch-decl
    pre-declaration (`if_branch_decls`, set by `_promote_pending_loop_var` when a
    loop/body var is hoisted for post-loop use -- the emitter has no `_emit_branch_decls`
    equivalent); and a loop-scoped var (not shadowing an outer local, whose `was_declared`
    handling the emitter does not reproduce)."""
    if (stmt.is_async or stmt.is_tuple_unpack or stmt.orelse
            or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return False
    if analyzer.if_branch_decls.get(id(stmt)):
        return False
    return stmt.var not in declared


def _for_range_eligible(stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
                        prescan: _Prescan, pointers: set[str],
                        rebind_slots: set[str], storage_tuple_locals: set[str]) -> bool:
    # Only a plain `for v in range(stop | start, stop)` with step 1 over a fixed-int
    # counter (loop var not used after the loop). 3-arg/stepped range stays on the AST
    # path; the shared shape guards exclude the other richer for-shapes.
    it = stmt.iterable
    if not _is_range_call(it) or not _for_loop_shape_ok(stmt, analyzer, declared):
        return False
    if it.kwargs or it.double_star_unpack is not None or len(it.args) not in (1, 2):
        return False
    et = unwrap_ref_type(stmt.elem_type) if stmt.elem_type is not None else None
    if not _eligible_scalar(et):
        return False
    nargs = len(it.args)
    if nargs == 2 and not _range_bound_eligible(it.args[0], declared, analyzer):
        return False
    stop_arg = it.args[0] if nargs == 1 else it.args[1]
    if not _range_bound_eligible(stop_arg, declared, analyzer):
        return False
    body_declared = dict(declared)
    body_declared[stmt.var] = et  # loop var's resolved (fixed-int) type
    return _body_eligible(stmt.body, analyzer, body_declared, prescan,
                          in_branch=True, pointers=pointers,
                          rebind_slots=rebind_slots,
                          storage_tuple_locals=storage_tuple_locals)


def _for_each_container_eligible(stmt: TpyForEach, analyzer,
                                 declared: dict[str, TpyType], prescan: _Prescan,
                                 pointers: set[str], rebind_slots: set[str],
                                 storage_tuple_locals: set[str]) -> bool:
    # `for v in <container>` over a NativeIterable with a value-scalar (`list[scalar]` /
    # `dict[fixed-int-key]`, a typed copy; bytes/BytesView are
    # NativeIterable[UInt8] -- the same typed-copy loop var), Char (str/StrView,
    # NativeIterable[Char]),
    # str (a `list[str]` element / owned-str dict key -- the loop var is a fresh
    # view var, usage-resolved to `std::string_view` or an owned `std::string`
    # copy; `loop_var_binding` spells both), or F1-record (`list[record]`, a
    # borrow alias) loop var. A generator/user-iterator
    # (the `__iter__`/`__next__` fallback) and the
    # shared richer for-shapes stay on the AST path.
    if not _for_loop_shape_ok(stmt, analyzer, declared):
        return False
    it = stmt.iterable
    # A plain in-scope container name, or a str/bytes-family field off an
    # F1-record receiver (`for c in h.name:`) -- both C++ lvalues
    # (is_lvalue_iterable: a name, or a field access over one), taking the
    # `auto& __obj_N =` capture -- or an eligible STR-returning call
    # (`for c in full(s):` -- an rvalue, `is_lvalue_iterable`'s value-type
    # call arm, so the owning `auto __obj_N =` capture; the fact rides
    # `iterable_lvalue`). Bytes-returning calls and other non-name iterables
    # (subscript) ride a later cell.
    if isinstance(it, TpyCall) and not _is_range_call(it):
        if not _call_eligible(it, declared, analyzer):
            return False
        it_type = _resolved_str_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            return False
    elif isinstance(it, TpyName):
        if it.name not in declared:
            return False
        # declared (the binding type) rather than get_expr_type: a container-literal
        # local's use sites carry the pre-resolution PendingListType (see
        # _method_call_eligible). A param binding is Ref/readonly-wrapped -- unwrap
        # like _container_scalar_read does internally.
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[it.name])))
        # A str/bytes local's binding is a Pending view type the registry lookup
        # can't see through; resolve to the concrete view/owned nominal first.
        it_view = _resolved_viewfam_value(it_type, analyzer)
        if it_view is not None:
            it_type = it_view
    elif isinstance(it, TpyFieldAccess):
        if not _field_receiver_ok(it, declared, analyzer):
            return False
        it_type = _resolved_viewfam_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            return False
    else:
        return False
    if not is_native_iterable(it_type, analyzer.registry):
        return False
    # The loop var (list/set/Span/Array element, or dict key) is a value scalar (typed
    # copy) or an F1-record (a borrow alias -- `auto&&`/`const auto&`, read/written
    # `.field` exactly like a record param, so it flows through the body constructs
    # identically). No rebinding guard is needed: sema forbids reassigning a non-value
    # loop var (`_check_nonvalue_rebinding` -- `p = other` is a hard error), so an
    # eligible record loop var is only ever read or field-mutated through the alias, both
    # matching Python's reference semantics.
    # resolve_int_literals: a literal-seeded container's elem_type is still
    # IntLiteral (IntLiteralType.to_cpp() would emit the VALUE); the AST binding
    # emits the resolved default-int spelling.
    et = (resolve_int_literals(unwrap_ref_type(stmt.elem_type),
                               analyzer.ctx.default_int_for_literal)
          if stmt.elem_type is not None else None)
    if (not _eligible_scalar(et) and not _eligible_char(et)
            and _resolved_str_value(et, analyzer) is None
            and not _f1_record(et, analyzer)):
        return False
    body_declared = dict(declared)
    body_declared[stmt.var] = et
    return _body_eligible(stmt.body, analyzer, body_declared, prescan,
                          in_branch=True, pointers=pointers,
                          rebind_slots=rebind_slots,
                          storage_tuple_locals=storage_tuple_locals)


def _stmt_eligible(stmt: TpyStmt, analyzer, declared: dict[str, TpyType],
                   prescan: _Prescan, *, in_branch: bool,
                   pointers: set[str], rebind_slots: set[str],
                   storage_tuple_locals: set[str]) -> bool:
    if isinstance(stmt, TpyVarDecl):
        if stmt.linkage != VarLinkage.DEFAULT or stmt.init is None:
            return False
        is_reassign = stmt.name in declared
        # A var-decl inside a branch must reassign an already-declared local --
        # a name first-declared in a branch needs scope snapshot/restore (and
        # may hoist), which the slice does not reproduce.
        if in_branch and not is_reassign:
            return False
        if not is_reassign:
            # First decl of a non-value local (REF_ALIAS / OPTIONAL_TO_PTR /
            # POINTER), bound from a field read off an F1-record receiver. Its
            # non-value field init is not a value expression, so it is admitted
            # here, not via _expr_eligible (which rejects it). A POINTER local is
            # recorded so its later reassignments lower as reseats.
            binding = _borrow_local_binding(
                stmt, _var_decl_type(stmt, analyzer), declared, prescan, analyzer)
            if binding is not None:
                if binding is LocalBinding.POINTER:
                    pointers.add(stmt.name)
                elif binding is LocalBinding.REBIND_SLOT:
                    # An F2d rebind-slot local: in `pointers` for its `->` reads,
                    # in `rebind_slots` so its reseats lower as rvalue rebinds.
                    pointers.add(stmt.name)
                    rebind_slots.add(stmt.name)
                return True
            # F3 storage-tuple alias (`t = <storage tuple field>` -> `auto&& t = ...`):
            # a pointer-repr tuple local aliasing a storage tuple field off an
            # F1-record receiver. Tracked so its reads lift via tuple_to_pointer at
            # borrow boundaries (e.g. `return t`) and so the borrow-tuple write source
            # excludes it (it is storage form, a direct copy).
            if (is_storage_tuple_alias_decl(
                    _var_decl_type(stmt, analyzer), stmt.init, name=stmt.name,
                    reassigned=prescan.reassigned, hoisted=prescan.hoisted,
                    move_through=prescan.move_through)
                    and _field_receiver_ok(stmt.init, declared, analyzer)
                    and _f1_tuple(analyzer.get_expr_type(stmt.init), analyzer) is not None):
                storage_tuple_locals.add(stmt.name)
                return True
            # Container-literal local (`xs = [1, 2]` / `d = {k: v}`): the local
            # enters `declared` with its resolved container type, so the
            # receiver gates (subscript / len / iteration / method calls)
            # admit it exactly like a container param.
            if _container_literal_decl_ok(stmt, declared, prescan, analyzer):
                return True
        elif stmt.name in rebind_slots:
            # F2d rebind-slot reseat: an rvalue F1-record ctor / by-value source.
            return _is_record_rvalue_source(stmt.init, declared, analyzer)
        elif stmt.name in pointers:
            # F2a pointer-local reseat: an lvalue F1-record field source only.
            return _f2_reseat_ok(stmt.init, declared, analyzer)
        # A str literal in a Char-typed slot renders as a target-typed C++
        # char literal (`c: Char = 'x'` -> `char c = 'x';`, lowered to
        # THIRCharLiteral). Only the single-char annotated DECL converts;
        # a reassign (`c = 'y'`) or multi-char literal is a sema type error,
        # so those rejects are defensive.
        decl_tgt = (declared.get(stmt.name) if is_reassign
                    else _var_decl_type(stmt, analyzer))
        if isinstance(stmt.init, TpyStrLiteral) and _eligible_char(decl_tgt):
            if is_reassign or len(stmt.init.value) != 1:
                return False
        # `x = None` at a value-union binding renders `std::monostate{}`
        # (target-typed, like the Char literal above). None at any other
        # binding in the slice is ineligible (Optional locals are not
        # admitted, and a None into a POINTER-variant binding is a monostate
        # write arm the slice defers), so this is the only None-init arm.
        if isinstance(stmt.init, TpyNoneLiteral):
            return _eligible_value_union(decl_tgt) is not None
        # F4 U2: a pointer-variant union local. Sources are same-union names
        # (bare borrow copy) or -- for single-assignment locals -- a
        # value-variant field lvalue (the to_[const_]ptr_variant lift; a
        # reseat's const verdict would come from its own receiver, a chain
        # the slice does not reproduce).
        ptr_u = _eligible_ptr_union(decl_tgt, analyzer)
        if ptr_u is not None:
            return _ptr_union_source_ok(
                stmt.init, declared, analyzer, ptr_u,
                allow_field=stmt.name not in prescan.reassigned)
        if not _expr_eligible(stmt.init, declared, analyzer):
            return False
        # First declaration: the local's type must be an eligible scalar (a
        # bare-literal init analyzes as IntLiteralType, pinning no width -> out)
        # / Char, or a str-slice binding (a `PendingStrType` whose view/owned
        # resolution is final pre-lowering -- `std::string_view` or
        # `std::string`).
        # A reassignment targets an already-validated local (its value just
        # renders into the existing slot), so the type check does not apply.
        if is_reassign:
            return True
        vtype = _var_decl_type(stmt, analyzer)
        # A `String` local only ever arises from an already-validated eligible
        # init (a concat result or another String local -- the only String
        # producers in the slice); it declares as `std::string`, byte-identical
        # to an owned str local.
        return (_eligible_scalar(vtype) or _eligible_char(vtype)
                or _resolved_str_value(vtype, analyzer) is not None
                or _resolved_bytes_value(vtype, analyzer) is not None
                or _is_string_owned(vtype)
                or _eligible_value_union(vtype) is not None
                or _slice_object_type(vtype))
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyName):
            # Char-targeted str literal: target-typed `'x'` render -> AST path
            # (mirrors the var-decl reassign guard).
            if (isinstance(stmt.value, TpyStrLiteral)
                    and _eligible_char(declared.get(stmt.target.name))):
                return False
            # A bytes-literal value: this name-target TpyAssign only arises
            # from desugars (tuple-literal unpack), whose target-type threading
            # the owned/span flag does not mirror -> AST path. Plain
            # `name = b"..."` parses as TpyVarDecl and is handled there.
            if isinstance(stmt.value, TpyBytesLiteral):
                return False
            return (stmt.target.name in declared
                    and _expr_eligible(stmt.value, declared, analyzer))
        # F2b/F2c/F2e: an optional-field write `recv.field = <borrow>` / `= None`;
        # a plain scalar-field write `recv.field = <scalar>`; an F3 tuple-field
        # write `recv.field = <borrow tuple>` (tuple_to_storage); or an F4 U2
        # union-field write `recv.field = <borrow union name>` (to_value_variant).
        return (_f2b_optional_field_write_ok(stmt, declared, pointers, analyzer)
                or _scalar_field_write_ok(stmt, declared, analyzer)
                or _f1_tuple_field_write_ok(stmt, declared, storage_tuple_locals,
                                            analyzer)
                or _ptr_union_field_write_ok(stmt, declared, analyzer))
    if isinstance(stmt, TpyReturn):
        if stmt.value is None:
            return True
        if prescan.ret_borrow_tuple is not None:
            # A borrow-form tuple return lifts a storage tuple lvalue via
            # `tuple_to_pointer` (F3). The source is a storage tuple lvalue: a field
            # read off an F1-record receiver, or a storage-tuple alias local (`auto&&`).
            # Subscript / call sources ride later F3 cells and stay on the AST path.
            if isinstance(stmt.value, TpyName):
                return stmt.value.name in storage_tuple_locals
            return (_field_receiver_ok(stmt.value, declared, analyzer)
                    and _f1_tuple(analyzer.get_expr_type(stmt.value), analyzer)
                    is not None)
        if prescan.ret_storage_opt is not None:
            # A storage-form Optional[F1-record] return admits `None`
            # (-> std::nullopt, F2c) or a borrow `T*` lift (-> ptr_to_optional[_move],
            # copy or move per last-use; F2c copy / F2e move). Storage-field / call
            # sources stay on the AST path.
            return (isinstance(stmt.value, TpyNoneLiteral)
                    or _is_borrow_ptr_local(stmt.value, declared, pointers))
        # `return None` at a value-union return slot -> `std::monostate{}`
        # (F4 U1). Union names/literals flow through the generic arm below.
        if prescan.ret_union is not None and isinstance(stmt.value, TpyNoneLiteral):
            return True
        # A pointer-variant return (F4 U2) admits only same-union borrow
        # names (bare). A MEMBER record name takes the `&(...)` address-of
        # lift, a storage field the (miscompiled, BUGS.md) `&(h.u)` -- both
        # AST-path shapes.
        if prescan.ret_ptr_union is not None:
            return _ptr_union_source_ok(stmt.value, declared, analyzer,
                                        prescan.ret_ptr_union,
                                        allow_field=False)
        # `return "x"` at a Char return renders a target-typed char literal
        # (`'x'`) on the AST path -- not reproduced outside compare position.
        if prescan.ret_char and isinstance(stmt.value, TpyStrLiteral):
            return False
        return _expr_eligible(stmt.value, declared, analyzer)
    if isinstance(stmt, TpyIf):
        if not _condition_eligible(stmt.condition, declared, analyzer):
            return False
        # Branches do not extend the outer scope (no new-name decls allowed in
        # them), so each is checked against the same declared-so-far set.
        return (_body_eligible(stmt.then_body, analyzer, declared, prescan,
                               in_branch=True, pointers=pointers,
                               rebind_slots=rebind_slots,
                               storage_tuple_locals=storage_tuple_locals)
                and _body_eligible(stmt.else_body, analyzer, declared, prescan,
                                   in_branch=True, pointers=pointers,
                                   rebind_slots=rebind_slots,
                                   storage_tuple_locals=storage_tuple_locals))
    if isinstance(stmt, TpyWhile):
        # No while/else, and a comparison condition. break/continue are not in
        # the stmt set, so a body containing them is rejected by _body_eligible.
        if stmt.orelse or not _condition_eligible(stmt.condition, declared, analyzer):
            return False
        return _body_eligible(stmt.body, analyzer, declared, prescan,
                              in_branch=True, pointers=pointers,
                              rebind_slots=rebind_slots,
                              storage_tuple_locals=storage_tuple_locals)
    if isinstance(stmt, TpyAugAssign):
        return (_scalar_aug_assign_ok(stmt, declared, analyzer)
                or _str_aug_append_ok(stmt, declared, prescan, analyzer)
                or _bytes_aug_concat_ok(stmt, declared, prescan, analyzer))
    if isinstance(stmt, TpyForEach):
        return (_for_range_eligible(stmt, analyzer, declared, prescan, pointers,
                                    rebind_slots, storage_tuple_locals)
                or _for_each_container_eligible(stmt, analyzer, declared, prescan,
                                                pointers, rebind_slots,
                                                storage_tuple_locals))
    if isinstance(stmt, TpyExprStmt):
        # A bare expression statement: a builtin `print(...)` (common-arg subset)
        # or a same-module free-function call discarded for its side effects.
        if _is_builtin_print(stmt.expr, declared, analyzer):
            return _print_eligible(stmt.expr, declared, analyzer)
        if isinstance(stmt.expr, TpyCall):
            return _call_eligible(stmt.expr, declared, analyzer, stmt_position=True)
        if isinstance(stmt.expr, TpyMethodCall):
            # A container mutation call discarded for its side effect
            # (`xs.append(v)` / `xs.pop()` / ...).
            return _method_call_eligible(stmt.expr, declared, analyzer,
                                         stmt_position=True)
        return False
    return False


def _body_eligible(body, analyzer, declared: dict[str, TpyType],
                   prescan: _Prescan, *, in_branch: bool,
                   pointers: set[str], rebind_slots: set[str],
                   storage_tuple_locals: set[str]) -> bool:
    """Walk a statement list in source order, mirroring lowering's declared-scope
    growth: a top-level new-name var-decl extends scope; branch bodies don't. The
    map carries each name's resolved type (for the mixed-sign comparison gate and
    F1 field-receiver lookup); `pointers` carries the F2 pointer-local names a
    reseat reads, `rebind_slots` the F2d rebind-slot subset whose reseats are
    rvalue rebinds, `storage_tuple_locals` the F3 `auto&&` tuple aliases a borrow
    read lifts. All copied so sibling branches don't see each other."""
    declared = dict(declared)  # local copy -- sibling branches must not see each other
    pointers = set(pointers)
    rebind_slots = set(rebind_slots)
    storage_tuple_locals = set(storage_tuple_locals)
    for stmt in body:
        if not _stmt_eligible(stmt, analyzer, declared, prescan,
                              in_branch=in_branch, pointers=pointers,
                              rebind_slots=rebind_slots,
                              storage_tuple_locals=storage_tuple_locals):
            return False
        if (not in_branch and isinstance(stmt, TpyVarDecl)
                and stmt.name not in declared):  # first decl -- keep retro-widened type
            declared[stmt.name] = _var_decl_type(stmt, analyzer)
    return True


def _subscript_yields_borrow_ptr(sub: TpySubscript, lc: '_LowerCtx') -> bool:
    """Mirror ExpressionGenerator._tuple_subscript_yields_borrow_ptr: `std::get<N>(t)`
    is a bare `T*` (member access `->`) iff element N is a plain non-value BORROW_REF
    pointer-repr slot read from a borrow-form tuple. An owned (`Own`) or value element
    is held by value in the tuple (`std::get` yields a `T&`, `.` access), and a storage
    `auto&&` alias receiver likewise holds its elements by value -- both take `.`."""
    res = _subscript_index_and_tuple(sub, lc.analyzer)
    if res is None:
        return False
    recv_t, idx = res
    et = recv_t.element_types[idx]
    return (et.value_form() is ValueForm.BORROW_REF
            and TupleType._element_is_pointer_repr(et)
            and isinstance(sub.obj, TpyName)
            and sub.obj.name not in lc.storage_tuple_locals)


def _subscript_result_form(sub: TpySubscript, rtype: TpyType, lc: '_LowerCtx') -> Form:
    """The form a tuple subscript result renders as. A value scalar is VALUE; a record
    element is BORROW (a `T*`/`T&`). An Optional element read off a storage-tuple alias
    is STORAGE (`std::optional<T>`, lifted by the consumer via optional_to_ptr); off a
    borrow tuple param it is already `T*` (BORROW)."""
    if not _is_borrow_form_name(rtype):
        return Form.VALUE
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if (isinstance(inner, OptionalType) and isinstance(sub.obj, TpyName)
            and sub.obj.name in lc.storage_tuple_locals):
        return Form.STORAGE
    return Form.BORROW


def _field_is_arrow(e: TpyFieldAccess, lc: '_LowerCtx') -> bool:
    """`recv->field` vs `recv.field`: a plain `T*` pointer-local (F2) or the `self`
    receiver (a `this` pointer) renders `->`; a record param / `T&` alias receiver
    renders `.`. Decided from the pointer-local set lowering tracks (the same names
    eligibility recorded) plus the method receiver.

    A record-element tuple subscript receiver (`t[N].field`) renders `->` only when the
    element is a borrow `T*` (`_subscript_yields_borrow_ptr`): a bare-reference element
    off a borrow-form tuple param. An owned element (`std::get` yields `T&`) or a
    storage `auto&&` alias receiver reads `.`."""
    obj = e.obj
    if isinstance(obj, TpySubscript):
        return _subscript_yields_borrow_ptr(obj, lc)
    return (isinstance(obj, TpyName)
            and (obj.name in lc.pointers or obj.name == lc.self_receiver))


def _is_own_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is an `Own[...]`-declared param of the function being
    lowered (incl. the own-optional shapes) -- the storage-owning binding."""
    for n, t in lc.func.params:
        if n == name:
            return (isinstance(t, TpyType)
                    and unwrap_optional_own(unwrap_readonly(t)) is not None)
    return False


def _lower_expr(e: TpyExpr, lc: '_LowerCtx') -> THIRExpr:
    analyzer = lc.analyzer
    # A container-literal local's use sites keep the pre-resolution pending type
    # on the expr (the AST path unwraps it in TypeResolver.get_resolved_type);
    # THIR nodes must carry fully-resolved types. Same for a str local's
    # PendingStrType (sema's view/owned usage resolution is final pre-lowering).
    rtype = analyzer.get_expr_type(e)
    rtype = resolve_pending_container(rtype, analyzer) or rtype
    rtype = _resolve_pending_view(rtype, analyzer) or rtype
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        if e.name == lc.self_receiver:
            # The method receiver -> `this`. A borrow (pointer) receiver; only
            # ever reached as a field-access receiver (other `self` positions are
            # gated out), so its form tag is informational.
            return THIRSelf(result_type=rtype, form=Form.BORROW, loc=loc)
        # A non-value name (a record param / REF_ALIAS / POINTER local used as a
        # field receiver) is a borrow; scalars are value form. A pointer-repr tuple
        # name is a borrow tuple param (`std::tuple<..., T*>`) UNLESS it is an F3
        # storage-tuple alias local (`auto&& t = ...`, which aliases storage and reads
        # as STORAGE). The tag is informational for the field-access / convert emit,
        # but kept honest so a convert source is never mislabeled. A str-slice name
        # is the exception where the tag is LOAD-BEARING: BORROW (string_view param /
        # view local) drives the owned-sink `std::string(x)` copy, STORAGE (owned
        # local) suppresses it.
        str_t = _resolved_str_value(rtype, analyzer)
        if str_t is not None:
            return THIRName(result_type=str_t, name=e.name,
                            form=_str_name_form(e.name, str_t,
                                                lc.prescan.param_names),
                            loc=loc)
        # A bytes-slice name carries the same load-bearing view/owned form tag
        # as str: BORROW (span param / view local) drives the owned-sink
        # `::tpy::bytes_copy(x)`, STORAGE (owned vector local) suppresses it.
        bytes_t = _resolved_bytes_value(rtype, analyzer)
        if bytes_t is not None:
            return THIRName(result_type=bytes_t, name=e.name,
                            form=_bytes_name_form(e.name, bytes_t,
                                                  lc.prescan.param_names),
                            loc=loc)
        if _is_string_owned(rtype):
            # A String local (a concat-result binding): an owned std::string
            # lvalue, so STORAGE -- the owned-sink copy never fires on it and
            # the tag stays honest ( _is_borrow_form_name would mislabel it).
            return THIRName(result_type=rtype, name=e.name, form=Form.STORAGE,
                            loc=loc)
        if e.name in lc.storage_tuple_locals:
            form = Form.STORAGE
        elif _is_own_param(e.name, lc):
            # An `Own[...]` param owns its storage (a by-value / rvalue-ref
            # slot): STORAGE, not a borrow of someone else's -- keeps the MIL
            # move source and the validator's storage-sink rule honest.
            form = Form.STORAGE
        else:
            form = Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE
        return THIRName(result_type=rtype, name=e.name, form=form, loc=loc)
    if isinstance(e, TpyFieldAccess):
        if e.needs_optional_runtime_check and isinstance(e.obj, TpySubscript):
            # Unproven `Optional[record]`-element member access `t[N].field` ->
            # `deref_check(<T*>).field`. The subscript is a `T*` off a borrow tuple, or
            # a `std::optional<T>` off a storage alias lifted to `T*` via optional_to_ptr
            # (the STORAGE-form convert). Mirrors _gen_field_access's runtime-check path.
            sub = _lower_expr(e.obj, lc)
            recv = (THIRFormConvert(result_type=sub.result_type, value=sub,
                                    form=Form.BORROW, loc=loc)
                    if sub.form is Form.STORAGE else sub)
            return THIRFieldAccess(
                result_type=rtype, receiver=recv,
                field_cpp=escape_cpp_name(e.field), deref_check=True, loc=loc)
        # Scalar field read off a borrow receiver (value-form result). A plain
        # non-null `T*` pointer-local receiver renders `recv->field`; the non-value
        # field source for a borrow-local binding is built in _lower_field_source.
        return THIRFieldAccess(
            result_type=rtype,
            receiver=_lower_expr(e.obj, lc),
            field_cpp=escape_cpp_name(e.field),
            is_arrow=_field_is_arrow(e, lc),
            loc=loc,
        )
    if isinstance(e, TpySubscript):
        if e.slice_function_info is not None:
            # Str/bytes slice -> the resolved slice __getitem__'s @cpp_template
            # over a BasicSlice/Slice initializer (or a slice-typed variable
            # index rendered bare). The view result (string_view / span) is
            # BORROW -- an owned decl sink materializes it via the view->owned
            # THIRFormConvert (str: the strview_to_str coerce ->
            # `std::string(...)`; bytes: no coerce at a pending decl, the S6
            # decl-init BORROW wrap -> `::tpy::bytes_copy(...)`); the stepped /
            # slice-var owned result (std::string / std::vector<uint8_t>) is
            # STORAGE, landing bare in every sink. Absent bounds emit
            # std::nullopt.
            rt_view = _resolved_viewfam_value(rtype, analyzer)
            form = (Form.BORROW if rt_view is not None
                    and (is_str_view_type(rt_view) or is_bytes_view_type(rt_view))
                    else Form.STORAGE)
            recv = _lower_expr(e.obj, lc)
            tpl = e.slice_function_info.cpp_template
            if not isinstance(e.index, TpySlice):
                return THIRStrSlice(
                    result_type=rtype, receiver=recv, cpp_template=tpl,
                    index=_lower_expr(e.index, lc), form=form, loc=loc)
            sl = e.index
            return THIRStrSlice(
                result_type=rtype,
                receiver=recv,
                cpp_template=tpl,
                lower=_lower_expr(sl.lower, lc) if sl.lower is not None else None,
                upper=_lower_expr(sl.upper, lc) if sl.upper is not None else None,
                step=_lower_expr(sl.step, lc) if sl.step is not None else None,
                stepped=e.is_stepped_slice,
                form=form,
                loc=loc,
            )
        tup = _subscript_index_and_tuple(e, analyzer)
        if tup is not None:
            # Tuple subscript -> `std::get<N>(t)`. Eligibility guaranteed a const index
            # and an eligible-tuple receiver; the shared helper re-derives the
            # normalized index (negatives folded), mirroring _gen_subscript. The
            # normalized offset rides a synthesized `THIRLiteral` (only its value is
            # read, for the `std::get<N>` template arg). `form` records the result
            # shape for the consumer: a value scalar is VALUE, a record element is a
            # borrow (`T*`/`T&`), and an Optional element read off a storage-tuple alias
            # is `std::optional<T>` (STORAGE, lifted to `T*` by the consuming deref_check
            # via optional_to_ptr) -- off a borrow tuple it is already `T*` (BORROW).
            _recv_t, idx = tup
            form = _subscript_result_form(e, rtype, lc)
            return THIRSubscript(
                result_type=rtype,
                receiver=_lower_expr(e.obj, lc),
                index=THIRLiteral(result_type=analyzer.get_expr_type(e.index),
                                  value=idx, loc=loc),
                form=form,
                loc=loc,
            )
        # Container or str subscript -> the checked dunder
        # `::tpy::__getitem__(c, i)` (str's __getitem__ @cpp_template spells the
        # same) or, when sema proved the index in-bounds,
        # `c[static_cast<std::size_t>(i)]` (a literal index needs no cast). The
        # index is a fixed-int value-scalar expr (a runtime-BigInt index is out
        # of the scalar slice, so no `.to_fixed_check` narrow arises) or, for an
        # owned-str-keyed dict, a str-slice expr rendered bare in the key slot.
        # `form` is VALUE for a scalar / Char element; a str element/value read
        # (S5) carries its resolved shape -- BORROW when the read's view var
        # resolved `StrView` (the AST's `_is_str_view_source`, driving the
        # owned-sink `std::string(x)` copy), STORAGE when it resolved owned (the
        # `const std::string&` element lands in owned sinks via the implicit
        # copy ctor, bare on both paths).
        sub_str = _resolved_str_value(rtype, analyzer)
        return THIRSubscript(
            result_type=rtype,
            receiver=_lower_expr(e.obj, lc),
            index=_lower_expr(e.index, lc),
            bounds_safe=e.bounds_safe,
            form=(Form.VALUE if sub_str is None
                  else Form.BORROW if is_str_view_type(sub_str)
                  else Form.STORAGE),
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        return THIRLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyStrLiteral):
        # const char[N] via cpp_string_literal_expr; VALUE form -- implicitly
        # convertible to both string_view and string slots, never wrapped.
        return THIRStrLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyBytesLiteral):
        # Default owned render (bytes_literal_owned / empty vector) -- the
        # target-less positions (print/compare). View-targeted sinks (view
        # decl-init/reassign, bytes/BytesView call args) rewrite the flag at
        # their own lowering sites (_retag_bytes_literal_view / _lower_call_arg).
        return THIRBytesLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyFString):
        parts: list[str | THIRFStringArg] = []
        for part in e.parts:
            if isinstance(part, str):
                parts.append(part)
            else:
                wrap = _fstring_arg_wrap(part.expr, analyzer)
                assert wrap is not _FSTRING_INELIGIBLE
                parts.append(THIRFStringArg(expr=_lower_expr(part.expr, lc),
                                            wrap=wrap))
        # An owned std::string result: STORAGE form, so it lands bare in owned
        # sinks (no view->owned wrap), like an owned-str call result.
        return THIRFString(result_type=rtype, parts=tuple(parts),
                           form=Form.STORAGE, loc=loc)
    if isinstance(e, TpyBinOp):
        # and/or lower here too: sema leaves resolved_binop None for &&/||, so
        # the emit takes the bare-operator arm (`(l && r)`), matching the AST's
        # bool-result logical branch. Compare operands lower target-aware: a
        # str literal opposite a Char-typed operand renders as a char literal.
        # A str concat's String result and a bytes concat's owned `bytes`
        # result (`::tpy::bytes_concat`, std::vector<uint8_t> by value) are
        # owned rvalues (STORAGE): they land bare in every owned sink, never
        # wrapped.
        if e.op in _COMPARE_OPS:
            left = _lower_char_targeted(e.left, analyzer.get_expr_type(e.right), lc)
            right = _lower_char_targeted(e.right, analyzer.get_expr_type(e.left), lc)
        else:
            left = _lower_expr(e.left, lc)
            right = _lower_expr(e.right, lc)
        bt = _resolved_bytes_value(rtype, analyzer)
        return THIRBinOp(
            result_type=rtype,
            left=left,
            op=e.op,
            right=right,
            resolved=e.resolved_binop,
            divisor_non_zero=e.divisor_non_zero,
            form=(Form.STORAGE if _is_string_owned(rtype)
                  or (bt is not None and is_bytes_type(bt)) else Form.VALUE),
            loc=loc,
        )
    if isinstance(e, TpyUnaryOp):
        # A negated int literal folds to a plain literal (the AST's
        # _gen_unaryop literal-negation branch renders the negated value
        # directly); otherwise only logical `not` is admitted (bool operand).
        neg = _folded_neg_int_literal(e, analyzer)
        if neg is not None:
            return THIRLiteral(result_type=rtype, value=neg, loc=loc)
        return THIRUnaryNot(result_type=rtype, operand=_lower_expr(e.operand, lc),
                            loc=loc)
    if isinstance(e, TpyChainedCompare):
        # Inline arm of _gen_chained_compare: left-fold the sema pairs with the
        # bare && (resolved None), reproducing `((a < b) && (b < c))`. Each pair
        # is a full TpyBinOp (sema-analyzed), so it lowers like any comparison.
        assert e.pairs is not None
        folded = _lower_expr(e.pairs[0], lc)
        for pair in e.pairs[1:]:
            folded = THIRBinOp(result_type=rtype, left=folded, op="&&",
                               right=_lower_expr(pair, lc), resolved=None,
                               loc=loc)
        return folded
    if isinstance(e, TpyCall):
        fi = e.resolved_function_info
        if fi is not None and fi.is_method and fi.name == "__init__":
            # A scalar or slice-object type-constructor call (`Int32(x)` /
            # `basic_slice(1, 3)`): the emit is the resolved __init__ overload's
            # @cpp_template expanded over the args with no receiver. Sema
            # already substituted {cpp} / class type params, and the gate
            # admitted only positional-only templates, so the stored template is
            # carried verbatim. A `None` bound in a slice-ctor's value-repr
            # `Int32 | None` slot renders `std::nullopt` (the STORAGE-form None).
            return THIRCall(
                result_type=rtype,
                callee=e.func_name,
                args=tuple(
                    THIRLiteral(result_type=p.type, value=None,
                                form=Form.STORAGE, loc=loc)
                    if isinstance(a, TpyNoneLiteral) else _lower_expr(a, lc)
                    for a, p in zip(e.args, fi.params)),
                cpp_template=fi.cpp_template,
                loc=loc,
            )
        # A @native free-function builtin (currently `len` -> `tpy::__len__`) carries
        # its resolved symbol so the emit dispatches on it, not the source name.
        native_name = fi.native_name if _is_len_native(e) else None
        # A str/bytes-slice call result carries its C++ shape: a view-returning
        # call yields a string_view/span (BORROW -- an owned sink copies it), an
        # owned-returning call a string/vector by value (STORAGE -- lands bare).
        view_t = _resolved_str_value(rtype, analyzer)
        if view_t is None:
            view_t = _resolved_bytes_value(rtype, analyzer)
        form = (Form.VALUE if view_t is None
                else Form.BORROW if (is_str_view_type(view_t)
                                     or is_bytes_view_type(view_t))
                else Form.STORAGE)
        # Args lower against their param slots: a str literal in a Char slot
        # renders as a char literal, a bytes literal into a bytes/BytesView
        # slot takes gen_call_arg's static-span pin. A `len` call bypasses the
        # arity gate, so fall back to slot-less lowering there.
        params = (fi.params if fi is not None
                  and len(fi.params) == len(e.args) else None)
        return THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(
                _lower_call_arg(a, params[i].type if params else None, lc)
                for i, a in enumerate(e.args)),
            native_name=native_name,
            form=form,
            loc=loc,
        )
    if isinstance(e, (TpyArrayLiteral, TpySetLiteral)):
        # A container-literal decl init (the only position eligibility admits
        # it). result_type is the RESOLVED container (list vs Array already
        # decided by sema); the emit dispatches on its family. Elements lower
        # through the per-slot owned-str wrap (S5).
        args = getattr(rtype, "type_args", None)
        slot = args[0] if args else None
        return THIRContainerLiteral(
            result_type=rtype,
            elements=tuple(_lower_container_elem(x, slot, lc) for x in e.elements),
            loc=loc,
        )
    if isinstance(e, TpyDictLiteral):
        args = getattr(rtype, "type_args", None)
        kslot = args[0] if args else None
        vslot = args[1] if args and len(args) > 1 else None
        return THIRContainerLiteral(
            result_type=rtype,
            elements=tuple(_lower_container_elem(k, kslot, lc) for k in e.keys),
            values=tuple(_lower_container_elem(v, vslot, lc) for v in e.values),
            loc=loc,
        )
    if isinstance(e, TpyMethodCall):
        fi = e.resolved_function_info
        # The member name mirrors _gen_method_call's resolution: @native rename
        # over the escaped source name (the LiteralType-mangled overload form is
        # gated out). A void method call carries no resolved expr type (None);
        # normalize so the node keeps a non-None result_type.
        member = (fi.native_name if fi.native_name and not fi.native_function
                  else escape_cpp_name(e.method))
        # A str-slice result carries its C++ shape like a THIRCall's (S5): an
        # owned-str method result (`xs.pop()`, std::string by value) is STORAGE
        # and lands bare in owned sinks.
        m_str = _resolved_str_value(rtype, analyzer)
        return THIRMethodCall(
            result_type=rtype if rtype is not None else VoidType(),
            receiver=_lower_expr(e.obj, lc),
            method_cpp=member,
            args=tuple(_lower_expr(a, lc) for a in e.args),
            native_function_name=fi.native_name if fi.native_function else None,
            cpp_template=fi.cpp_template,
            form=(Form.VALUE if m_str is None
                  else Form.BORROW if is_str_view_type(m_str) else Form.STORAGE),
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        inner = _lower_expr(e.expr, lc)
        if _coerce_disposition(e) == "materialize":
            # The cross-type view->owned copy (`std::string(x)`) IS the S1
            # view->owned form transfer -- one emit chokepoint. The coerce
            # adds only the family-internal type respelling (StrView -> str /
            # String), carried on result_type.
            return THIRFormConvert(result_type=rtype, value=inner,
                                   form=Form.STORAGE, loc=loc)
        # Identity passthrough: the node's form is the wrapped expression's
        # form -- carried honestly (not the VALUE default) so the owned-sink
        # BORROW checks read the real source shape through the coerce (e.g.
        # string_to_str wraps a STORAGE String) -- EXCEPT a view-target coerce
        # (str_to_strview / string_to_strview): its value is a view into the
        # source's buffer whatever the source's form, so it sets BORROW
        # itself (an owned sink downstream must re-copy, like any view).
        vform = (Form.BORROW
                 if rtype is not None
                 and is_str_view_type(unwrap_readonly(unwrap_ref_type(
                     unwrap_send_sync(rtype))))
                 else inner.form)
        return THIRCoerce(
            result_type=rtype,
            expr=inner,
            coercion_name=e.coercion.name,
            form=vform,
            loc=loc,
        )
    raise AssertionError(f"ineligible expr reached lowering: {type(e).__name__}")


def _lower_container_elem(e: TpyExpr, slot: TpyType | None,
                          lc: '_LowerCtx') -> THIRExpr:
    """Lower one container-literal element / dict key / dict value into its
    slot. A view-form str source (BORROW -- a string_view param/local, a slice,
    a StrView-returning call) into an owned `std::string` slot copies
    explicitly via the S1 view->owned `THIRFormConvert` (`std::string(x)`) --
    the `_wrap_for_owned_slot`/`_view_source_to_owned` chokepoint at element
    positions. A literal (VALUE, const char[N]) and an owned source (STORAGE --
    an owned local, a String local, a concat/f-string rvalue) land bare, like
    the AST's brace-init pass-through; scalar slots never wrap."""
    el = _lower_expr(e, lc)
    st = _resolved_str_value(slot, lc.analyzer) if slot is not None else None
    if st is not None and is_str_type(st) and el.form is Form.BORROW:
        return THIRFormConvert(result_type=st, value=el, form=Form.STORAGE,
                               loc=getattr(e, "loc", None))
    return el


def _lower_call_arg(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx') -> THIRExpr:
    """Lower one call argument against its param slot. A str literal into a
    Char slot renders as a target-typed char literal (gen_expr's char arm,
    via `_lower_char_targeted`); a bytes literal into a bytes/BytesView slot
    takes gen_call_arg's static-storage span pin (`::tpy::bytes_literal(...)`,
    keyed on the RAW ptype exactly like the AST); every other arg lowers
    position-blind."""
    if isinstance(a, TpyStrLiteral) and _eligible_char(ptype):
        return _lower_char_targeted(a, ptype, lc)
    if isinstance(ptype, TpyType) and (is_bytes_type(ptype)
                                       or is_bytes_view_type(ptype)):
        # Peel coerce wrappers exactly like gen_call_arg's span pin (the pin
        # renders the bare literal; the coercion's own codegen never runs).
        lit = _peel_coerce(a)
        if isinstance(lit, TpyBytesLiteral):
            lowered = _lower_expr(lit, lc)
            return replace(lowered, form=Form.BORROW)
    return _lower_expr(a, lc)


def _retag_bytes_literal_view(value: THIRExpr, target: 'TpyType | None') -> THIRExpr:
    """Rewrite a bytes literal to its static-storage span render (BORROW) when
    the sink (a view-resolved binding / a BytesView return) is view-typed --
    the AST threads the target into gen_expr's TpyBytesLiteral arm."""
    if isinstance(value, THIRBytesLiteral) and is_bytes_view_type(target):
        return replace(value, form=Form.BORROW)
    return value


def _lower_char_targeted(e: TpyExpr, target: TpyType | None,
                         lc: '_LowerCtx') -> THIRExpr:
    """Lower an expression whose slot may be Char-typed, mirroring gen_expr's
    char-literal arm: a str literal in a Char slot renders as a target-typed
    C++ char literal (`'x'`). Shared by the three positions the AST threads a
    Char target into the render -- comparison operands opposite a Char-typed
    value (`_comparison_targets`' char arm), Char-annotated decl inits, and
    call args into Char param slots. The gates admitted the literal only
    single-char; the other `_comparison_targets` arms (Optional narrowing)
    cannot arise -- Optional operands are gated out of the slice."""
    if isinstance(e, TpyStrLiteral) and _eligible_char(target):
        return THIRCharLiteral(result_type=CHAR, value=e.value,
                               loc=getattr(e, "loc", None))
    return _lower_expr(e, lc)


def _lower_field_source(e: TpyFieldAccess, lc: '_LowerCtx') -> THIRFieldAccess:
    """The storage-form field read backing a borrow-local binding or an F3 tuple
    lift: `recv.field` where the field is a record (REF_ALIAS / POINTER), a
    storage-form `optional<T>` (OPTIONAL_TO_PTR), or a storage-form tuple (the F3
    `auto&&` alias decl + the borrow-tuple return source). form=STORAGE -- the bridge
    to borrow form is the `T&` reference bind (REF_ALIAS), the `auto&&` alias, or the
    wrapping THIRFormConvert (`&(...)` for POINTER, `optional_to_ptr` / `tuple_to_pointer`
    for the lifts). The receiver itself may be a pointer-local (a chained borrow), so
    `->` vs `.` is decided the same way as a value read."""
    return THIRFieldAccess(
        result_type=lc.analyzer.get_expr_type(e),
        receiver=_lower_expr(e.obj, lc),
        field_cpp=escape_cpp_name(e.field),
        is_arrow=_field_is_arrow(e, lc),
        form=Form.STORAGE,
        loc=getattr(e, "loc", None),
    )


class _LowerCtx:
    """Per-function lowering state threaded through `_lower_stmt`.

    `render_type` renders a decl C++ type the way codegen does
    (`TypeResolver.type_to_cpp`): it qualifies cross-module records and resolves
    the live module, which `TpyType.to_cpp()` does not, so it -- not `to_cpp()` --
    is the byte-identical source for an F1 borrow local's cpp_type. The default
    (`to_cpp`) is for analyzer-only callers (dump / standalone lowering) that
    never hit a non-value local."""
    __slots__ = ("analyzer", "func", "prescan", "render_type", "const_locals",
                 "pointers", "rebind_slot_locals", "movable_locals",
                 "self_receiver", "record_name", "storage_tuple_locals")

    def __init__(self, func: TpyFunction, analyzer, render_type,
                 self_receiver: str | None = None,
                 record_name: str | None = None) -> None:
        self.analyzer = analyzer
        self.func = func
        self.prescan = _Prescan(func, analyzer)
        self.render_type = render_type or (lambda t: t.to_cpp())
        # The receiver name (`self`) when `func` is an instance method, else
        # None: it lowers to a THIRSelf (`this`) and renders `->` field reads
        # like a pointer-local, but unlike `pointers` it is not a liftable
        # borrow source (`_is_borrow_ptr_local` must never treat it as one).
        self.self_receiver = self_receiver
        # The owning record's name when `func` is a method: `_param_is_const`
        # resolves a record param's const verdict from the method's FunctionInfo
        # on this record, not the free-function registry.
        self.record_name = record_name
        self.const_locals: set[str] = set()
        # F2 pointer-local names (reseatable `T*`), recorded at first decl so a
        # later reseat and any `->` read off them lower correctly.
        self.pointers: set[str] = set()
        # F2d rebind-slot subset of `pointers`: their reseats lower as rvalue
        # rebinds (`p = &*(__slot_N = ...)`), not lvalue `&(...)` reseats.
        self.rebind_slot_locals: set[str] = set()
        # F3 storage-tuple alias locals (`auto&& t = <storage tuple field>`): a read
        # off one is STORAGE form, lifted via `tuple_to_pointer` at borrow boundaries.
        self.storage_tuple_locals: set[str] = set()
        # F2e: sema's movable (owned) locals -- a borrow write/return source that
        # is one of these at last use moves (`ptr_to_optional_move`). The set only
        # grows during the body walk, so the final sema set matches the working
        # set at any post-decl write/return (see _is_move_source).
        self.movable_locals: set[str] = analyzer.function_movable_locals.get(
            id(func), set())
        # Param names live on `prescan.param_names` (the single copy): a
        # `str`-typed PARAM name is a `std::string_view` in the C++ signature
        # while an owned str LOCAL of the same resolved type is a `std::string`
        # -- the str name-form classifier needs the distinction (see
        # _str_name_form), and the aug-append gate excludes params the same way
        # (see _str_aug_append_ok).


def _is_move_source(value: TpyExpr, lc: _LowerCtx,
                    movable_names: 'set[str] | None' = None) -> bool:
    """Whether a write / return / MIL source moves rather than copies: the last use
    of a movable (owned) name. Mirrors the AST's `_is_last_use_movable(expr,
    movable_names)` (peel `TpyCoerce`; a `TpyName` in the movable set whose node is a
    last use). `movable_names` defaults to the function's `movable_locals` (the
    F2b/F2e write/return case -- only an F2d REBIND_SLOT local is owned there); the
    ctor MIL passes `own_param_names` instead (M3b-move), since no locals exist yet at
    MIL time (the MIL runs before the body) and its movable sources are the Own params."""
    names = lc.movable_locals if movable_names is None else movable_names
    inner = _peel_coerce(value)
    return (isinstance(inner, TpyName)
            and inner.name in names
            and id(inner) in lc.analyzer.ctx.all_last_uses)


def _lower_borrow_local(stmt: TpyVarDecl, vtype: TpyType, binding: 'LocalBinding',
                        is_const: bool, lc: _LowerCtx, loc) -> THIRVarDecl:
    """Lower a non-value borrow local's first declaration. REF_ALIAS binds a `T&`
    alias of the field's storage directly (no conversion node). POINTER lifts a
    plain-record lvalue to a reseatable `T*` via THIRFormConvert (`&(...)`).
    OPTIONAL_TO_PTR lifts the storage `optional<Inner>` to a borrow `Inner*` via
    THIRFormConvert (`::tpy::optional_to_ptr`), so its decl type is the inner.
    REBIND_SLOT (F2d) binds a plain-record rvalue (a ctor / by-value call) and
    the emitter materializes the two-slot `__slot_N` machinery; the init lowers
    as a plain value-form call (no conversion node)."""
    if binding is LocalBinding.REBIND_SLOT:
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=_lower_expr(stmt.init, lc),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    field = _lower_field_source(stmt.init, lc)
    if binding is LocalBinding.REF_ALIAS:
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=field,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if binding is LocalBinding.POINTER:
        convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                                  is_const=is_const, loc=loc)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    inner = vtype.inner  # OptionalType(Inner) -- the borrow points at Inner
    convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                              is_const=is_const, loc=loc)
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=convert,
        cpp_type=lc.render_type(inner), form=Form.BORROW, is_const=is_const,
        cpp_local_representation=binding, loc=loc)


def _lower_stmt(stmt: TpyStmt, lc: _LowerCtx, declared: dict[str, TpyType]) -> THIRStmt:
    # Single chokepoint: lower the statement, then carry the AST's
    # `no_source_comment` desugar flag onto the THIR node so the emitter dedups
    # the shared source comment (nested statements route through here too).
    result = _lower_stmt_dispatch(stmt, lc, declared)
    if getattr(stmt, "no_source_comment", False) and not result.no_source_comment:
        return replace(result, no_source_comment=True)
    return result


def _lower_stmt_dispatch(stmt: TpyStmt, lc: _LowerCtx,
                         declared: dict[str, TpyType]) -> THIRStmt:
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    if isinstance(stmt, TpyVarDecl):
        vtype = _var_decl_type(stmt, analyzer)
        # First decl of a non-value borrow local (REF_ALIAS / OPTIONAL_TO_PTR /
        # POINTER).
        if stmt.name not in declared:
            binding = _borrow_local_binding(stmt, vtype, declared, lc.prescan, analyzer)
            if binding is not None:
                if binding is LocalBinding.REBIND_SLOT:
                    # rvalue ctor source: an owned, mutable pointer-local (never
                    # const). Recorded in both sets, as eligibility did.
                    lc.pointers.add(stmt.name)
                    lc.rebind_slot_locals.add(stmt.name)
                    declared[stmt.name] = vtype
                    return _lower_borrow_local(stmt, vtype, binding, False, lc, loc)
                is_const = _f1_is_const(binding, vtype, stmt, lc.func, analyzer,
                                        lc.const_locals, lc.record_name)
                if is_const:
                    lc.const_locals.add(stmt.name)
                if binding is LocalBinding.POINTER:
                    lc.pointers.add(stmt.name)  # later assignments reseat this `T*`
                declared[stmt.name] = vtype
                return _lower_borrow_local(stmt, vtype, binding, is_const, lc, loc)
            # F3 storage-tuple alias: `auto&& t = <storage tuple field>`. The local
            # aliases the source's storage, so a read off it is STORAGE form (lifted
            # via tuple_to_pointer at a borrow boundary); the init is the storage tuple
            # field source (no conversion node -- `auto&&` binds it directly). The
            # `storage_tuple_locals` membership is what makes a later read lift.
            if is_storage_tuple_alias_decl(
                    vtype, stmt.init, name=stmt.name,
                    reassigned=lc.prescan.reassigned, hoisted=lc.prescan.hoisted,
                    move_through=lc.prescan.move_through):
                lc.storage_tuple_locals.add(stmt.name)
                # The alias aliases its source's const-ness (`auto&&` deduces it): a
                # const-receiver source makes reads lift to `const T*`. Tracked in
                # `const_locals` so the borrow read at a return picks the const helper.
                src_recv = stmt.init.obj  # TpyName (FieldAccess receiver)
                if (src_recv.name in lc.const_locals
                        or _param_is_const(src_recv.name, lc.func, analyzer,
                                           lc.record_name)):
                    lc.const_locals.add(stmt.name)
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=_lower_field_source(stmt.init, lc), form=Form.STORAGE,
                    cpp_local_representation=LocalBinding.STORAGE_TUPLE_ALIAS, loc=loc)
        # F2d rebind-slot reseat: an rvalue ctor / by-value source. It lowers as a
        # plain value-form call; emit wraps it as `p = &*(__slot_N = <value>)`
        # using the rebind slot allocated at the decl. Checked before the lvalue
        # POINTER reseat -- a rebind-slot local is in both `pointers` sets.
        if stmt.name in lc.rebind_slot_locals:
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=_lower_expr(stmt.init, lc), loc=loc)
        # F2a pointer-local reseat: lift the new lvalue field source to `T*` via
        # `&(...)` (the same storage->borrow convert as the first decl). Eligibility
        # admitted only an F1-record field source here. result_type is the stripped
        # `vtype` (the pointee), matching the first-decl path -- `get_expr_type`
        # would leave a ReadonlyType wrapper the THIR fully-resolved-type invariant
        # forbids (emit strips it either way, so this stays byte-identical).
        if stmt.name in lc.pointers:
            convert = THIRFormConvert(
                result_type=vtype,
                value=_lower_field_source(stmt.init, lc), form=Form.BORROW,
                is_const=stmt.name in lc.const_locals, loc=loc)
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=convert, loc=loc)
        # F4 U2: a pointer-variant union local -- first decl or reseat. A
        # same-union name copies bare (borrow -> borrow); a value-variant
        # field lvalue lifts via to_[const_]ptr_variant, const from the
        # receiver (the F1 OPTIONAL_TO_PTR const bump's union sibling).
        ptr_u = _eligible_ptr_union(
            declared[stmt.name] if stmt.name in declared else vtype,
            lc.analyzer)
        if ptr_u is not None and stmt.init is not None:
            if isinstance(stmt.init, TpyFieldAccess):
                recv = stmt.init.obj  # TpyName (validated by _field_receiver_ok)
                u_const = (recv.name in lc.const_locals
                           or _param_is_const(recv.name, lc.func, lc.analyzer,
                                              lc.record_name))
                u_init: THIRExpr = THIRFormConvert(
                    result_type=ptr_u, value=_lower_field_source(stmt.init, lc),
                    form=Form.BORROW, is_const=u_const, loc=loc)
            else:
                u_const = False
                u_init = _lower_expr(stmt.init, lc)
            if stmt.name in declared:
                return THIRAssign(
                    target=THIRName(result_type=ptr_u, name=stmt.name,
                                    form=Form.BORROW, loc=loc),
                    value=u_init, loc=loc)
            if u_const:
                lc.const_locals.add(stmt.name)
            declared[stmt.name] = vtype
            u_cpp = (ptr_u.to_cpp_const_ptr_variant() if u_const
                     else ptr_u.to_cpp_ptr_variant())
            return THIRVarDecl(
                name=stmt.name, resolved_type=ptr_u, init=u_init,
                cpp_type=u_cpp, form=Form.BORROW, is_const=u_const,
                cpp_local_representation=LocalBinding.PTR_VARIANT, loc=loc)
        # `x = x + y` self-append peephole (the AST's _try_str_inplace_append,
        # checked on every reassignment of an owned-str-family local before the
        # generic emit): the RHS concat's left operand is the target itself, so
        # the whole statement emits `x += y;` (buffer reuse) instead of the
        # concat-and-assign.
        if stmt.name in declared and _owned_str_append_target(vtype, analyzer):
            rhs = _str_self_append_rhs(stmt.name, stmt.init)
            if rhs is not None:
                return THIRStrAppend(target=stmt.name,
                                     value=_lower_expr(rhs, lc), loc=loc)
        # `x = None` at a value-union binding renders the monostate member --
        # target-typed at lowering, like the Char decl below (F4 U1).
        if isinstance(stmt.init, TpyNoneLiteral):
            ut = _eligible_value_union(declared[stmt.name]
                                       if stmt.name in declared else vtype)
            init: 'THIRExpr | None' = THIRLiteral(
                result_type=ut, value=None, form=Form.STORAGE, loc=loc)
        else:
            # A Char-annotated decl init lowers target-aware: `c: Char = 'x'` ->
            # `char c = 'x';` (the AST threads the decl type into the render).
            init = _lower_char_targeted(stmt.init, vtype, lc) if stmt.init else None
        # A str/bytes local's binding type is a Pending view type; carry the
        # RESOLVED view/owned type (string_view/string, span/vector) on the nodes.
        str_t = _resolved_str_value(vtype, analyzer)
        if str_t is not None:
            vtype = str_t
        bytes_t = _resolved_bytes_value(vtype, analyzer)
        if bytes_t is not None:
            vtype = bytes_t
        # A bytes-literal init renders per the binding: a view binding takes the
        # static-storage span. On a reassign the binding is the DECLARED
        # local/param type (`_var_decl_type` falls back to the init's own type
        # there, which for a literal is owned `bytes`).
        init = _retag_bytes_literal_view(
            init,
            _resolved_bytes_value(declared[stmt.name], analyzer)
            if stmt.name in declared else bytes_t)
        # The parser emits TpyVarDecl for every `name = expr`; the AST codegen
        # treats a write to an already-declared name as a reassignment, not a
        # re-declaration. Mirror that here so first-decl emits `T x = ...` and a
        # reassignment emits `x = ...`.
        if stmt.name in declared:
            assert init is not None  # eligibility requires a var-decl init
            # No view->owned wrap on a plain reassignment: std::string has an
            # implicit operator=(string_view), and the AST emits the bare
            # `t = s;` here (the wrap is a decl-init/return-boundary shape).
            # The AST emits the same bare assign for an owned-BYTES target fed
            # a view source, which is invalid C++ (vector has no span
            # operator=) -- a pre-existing AST bug (BUGS.md); mirrored
            # byte-identically rather than silently fixed on one path.
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=init,
                loc=loc,
            )
        # Owned-str/bytes decl init off a view-form source copies explicitly --
        # `std::string u = std::string(v);` / `std::vector<uint8_t> u =
        # ::tpy::bytes_copy(v);` -- the view->owned CONSTRUCTION being explicit.
        # Mirrors the AST's `_view_source_to_owned` chokepoint; a literal init
        # (VALUE form: const char[N] / an already-owned bytes render)
        # constructs directly and stays bare.
        if (init is not None and init.form is Form.BORROW
                and ((str_t is not None and is_str_type(str_t))
                     or (bytes_t is not None and is_bytes_type(bytes_t)))):
            init = THIRFormConvert(result_type=str_t if str_t is not None else bytes_t,
                                   value=init, form=Form.STORAGE, loc=loc)
        declared[stmt.name] = vtype
        return THIRVarDecl(name=stmt.name, resolved_type=vtype, init=init, loc=loc)
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyFieldAccess):
            # A borrow `T*` stored into a storage `optional<T>` field lifts
            # borrow->storage via THIRFormConvert (`ptr_to_optional`, F2b); a
            # `None` literal stores as a STORAGE-form None (`std::nullopt`, F2c).
            # The target field-access renders `recv.field` / `recv->field`.
            ftype = analyzer.get_expr_type(stmt.target)
            # A scalar / Char field is a plain value assign -- no
            # borrow<->storage lift.
            if _eligible_scalar(ftype) or _eligible_char(ftype):
                return THIRAssign(target=_lower_expr(stmt.target, lc),
                                  value=_lower_expr(stmt.value, lc), loc=loc)
            if isinstance(stmt.value, TpyNoneLiteral):
                fvalue: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                               form=Form.STORAGE, loc=loc)
            else:
                fvalue = THIRFormConvert(result_type=ftype,
                                         value=_lower_expr(stmt.value, lc),
                                         form=Form.STORAGE,
                                         move=_is_move_source(stmt.value, lc), loc=loc)
            return THIRAssign(target=_lower_expr(stmt.target, lc), value=fvalue, loc=loc)
        # Name-target assign: the same self-append peephole as the var-decl
        # reassignment (the AST checks it at both sites).
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            rhs = _str_self_append_rhs(stmt.target.name, stmt.value)
            if rhs is not None:
                return THIRStrAppend(target=stmt.target.name,
                                     value=_lower_expr(rhs, lc), loc=loc)
        return THIRAssign(
            target=_lower_expr(stmt.target, lc),
            value=_lower_expr(stmt.value, lc),
            loc=loc,
        )
    if isinstance(stmt, TpyAugAssign):
        # str `t += v`: the in-place append (the AST's string branch inside the
        # resolved-binop arm), not the synthetic binop below. The target-type
        # dispatch mirrors _str_aug_append_ok's admission.
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            return THIRStrAppend(target=stmt.target.name,
                                 value=_lower_expr(stmt.value, lc), loc=loc)
        # `target OP= value` lowers to `target = (target OP value)`, matching the
        # AST's `_gen_aug_assign_code` scalar branch -- and the bytes
        # concat-and-assign (`t = ::tpy::bytes_concat(t, v);`, the same
        # resolved-binop arm with the native emit, likewise unwrapped). The
        # target expr is lowered
        # twice (once as the assign lvalue, once as the binop's left operand) --
        # the AST likewise substitutes the same target string into both slots.
        # `divisor_non_zero=False`: the AST aug-assign path never swaps the
        # checked div/mod helper (no source `TpyBinOp` node carries the flag).
        # A bytes target's binding is a PendingBytesType; resolve it (and tag
        # the owned concat result STORAGE) so the nodes carry final types.
        tgt_type = analyzer.get_expr_type(stmt.target)
        tgt_type = _resolve_pending_view(tgt_type, analyzer) or tgt_type
        tgt_bytes = _resolved_bytes_value(tgt_type, analyzer)
        target = _lower_expr(stmt.target, lc)
        binop = THIRBinOp(
            result_type=tgt_type,
            left=_lower_expr(stmt.target, lc),
            op=stmt.op,
            right=_lower_expr(stmt.value, lc),
            resolved=stmt.resolved_binop,
            paren_wrap=False,
            form=(Form.STORAGE if tgt_bytes is not None
                  and is_bytes_type(tgt_bytes) else Form.VALUE),
            loc=loc,
        )
        return THIRAssign(target=target, value=binop, loc=loc)
    if isinstance(stmt, TpyReturn):
        ret_tuple = lc.prescan.ret_borrow_tuple
        if stmt.value is not None and ret_tuple is not None:
            # Lift a storage tuple lvalue into the borrow-form tuple return via
            # `tuple_to_pointer` (F3). The element pointers' const-ness tracks the
            # source, mirroring the F1 OPTIONAL_TO_PTR const bump; sema forces a
            # mutable source when the return borrows mutably, so the const arm only
            # fires for a const source returning a const-element tuple. The source is
            # a storage-tuple alias local (`return t`) or a field read (`return h.pair`).
            if isinstance(stmt.value, TpyName):
                is_const = stmt.value.name in lc.const_locals
                inner: THIRExpr = _lower_expr(stmt.value, lc)  # STORAGE-form alias
            else:
                recv = stmt.value.obj  # TpyName (validated by _field_receiver_ok)
                is_const = (recv.name in lc.const_locals
                            or _param_is_const(recv.name, lc.func, analyzer,
                                               lc.record_name))
                inner = _lower_field_source(stmt.value, lc)
            value: THIRExpr = THIRFormConvert(
                result_type=ret_tuple, value=inner,
                form=Form.BORROW, is_const=is_const, loc=loc)
            return THIRReturn(value=value, loc=loc)
        ret_opt = lc.prescan.ret_storage_opt
        if stmt.value is not None and ret_opt is not None:
            # Lift into a storage-form Optional[record] return slot. `None` lowers
            # to a STORAGE-form None literal (`std::nullopt`, F2c); a borrow `T*`
            # to the borrow->storage THIRFormConvert the F2b write uses --
            # `ptr_to_optional` (copy, F2c) or `ptr_to_optional_move` when the
            # source is an owned local at last use (move, F2e, via _is_move_source).
            if isinstance(stmt.value, TpyNoneLiteral):
                value: THIRExpr = THIRLiteral(result_type=ret_opt, value=None,
                                              form=Form.STORAGE, loc=loc)
            else:
                value = THIRFormConvert(result_type=ret_opt,
                                        value=_lower_expr(stmt.value, lc),
                                        form=Form.STORAGE,
                                        move=_is_move_source(stmt.value, lc), loc=loc)
            return THIRReturn(value=value, loc=loc)
        ret_union = lc.prescan.ret_union
        if stmt.value is not None and ret_union is not None \
                and isinstance(stmt.value, TpyNoneLiteral):
            # `return None` at a value-union slot -> `std::monostate{}` (F4 U1).
            return THIRReturn(
                value=THIRLiteral(result_type=ret_union, value=None,
                                  form=Form.STORAGE, loc=loc), loc=loc)
        value = _lower_expr(stmt.value, lc) if stmt.value else None
        # NB a bytes literal (or bytes value) at a BytesView return arrives
        # wrapped in the cross-type view coercion and is gate-rejected (the
        # deferred coercion cell), so no view retag is needed here.
        # An owned-str/bytes return (std::string / std::vector<uint8_t> by
        # value) fed a view-form source (view param / view local) copies
        # explicitly -- `return std::string(a);` / `return ::tpy::bytes_copy(a);`
        # -- the view->owned construction being explicit. Mirrors
        # _view_source_to_owned at the return boundary; a literal (VALUE) or
        # owned local / owned call result (STORAGE) returns bare.
        ret_str = lc.prescan.ret_str
        ret_bytes = lc.prescan.ret_bytes
        if value is not None and value.form is Form.BORROW:
            if ret_str is not None and is_str_type(ret_str):
                value = THIRFormConvert(result_type=ret_str, value=value,
                                        form=Form.STORAGE, loc=loc)
            elif ret_bytes is not None and is_bytes_type(ret_bytes):
                value = THIRFormConvert(result_type=ret_bytes, value=value,
                                        form=Form.STORAGE, loc=loc)
        return THIRReturn(value=value, loc=loc)
    if isinstance(stmt, TpyIf):
        # Branches share `declared`: eligibility guarantees they only reassign
        # already-declared locals (lowered to THIRAssign), so neither branch
        # extends the scope and order stays consistent with the AST path.
        return THIRIf(
            condition=_lower_expr(stmt.condition, lc),
            then_body=tuple(_lower_stmt(s, lc, declared) for s in stmt.then_body),
            else_body=tuple(_lower_stmt(s, lc, declared) for s in stmt.else_body),
            loc=loc,
        )
    if isinstance(stmt, TpyWhile):
        return THIRWhile(
            condition=_lower_expr(stmt.condition, lc),
            body=tuple(_lower_stmt(s, lc, declared) for s in stmt.body),
            loc=loc,
        )
    if isinstance(stmt, TpyForEach):
        it = stmt.iterable
        # Loop var is C++-for-scoped: visible in the body but not the outer scope
        # (a fresh declared copy, so a body decl can't leak past the loop).
        # resolve_int_literals mirrors the eligibility gate: a literal-seeded
        # container's elem_type is still IntLiteral, whose to_cpp() emits the
        # value -- the binding must spell the resolved default int. A str loop
        # var (list[str] element / owned-str dict key) is a PendingStrType;
        # resolve it to its concrete view/owned type like the AST's
        # `resolve_type` does before loop_var_binding spells the binding (S5).
        et = resolve_int_literals(unwrap_ref_type(stmt.elem_type),
                                  analyzer.ctx.default_int_for_literal)
        str_et = _resolved_str_value(et, analyzer)
        if str_et is not None:
            et = str_et
        body_declared = dict(declared)
        body_declared[stmt.var] = et
        body = tuple(_lower_stmt(s, lc, body_declared) for s in stmt.body)
        if _is_range_call(it):
            nargs = len(it.args)
            if nargs == 1:
                start = None
                start_is_literal = True
                stop_arg = it.args[0]
            else:
                start_arg = it.args[0]
                start = _lower_expr(start_arg, lc)
                start_is_literal = _range_bound_literal_value(start_arg) is not None
                stop_arg = it.args[1]
            return THIRForRange(
                var=stmt.var,
                elem_type=et,
                stop=_lower_expr(stop_arg, lc),
                start=start,
                start_is_literal=start_is_literal,
                stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
                body=body,
                loc=loc,
            )
        # Container iteration -> the begin/end loop. The admitted iterable
        # shapes decide lvalue-ness statically (mirrors is_lvalue_iterable
        # over them): a name / F1-field read is an lvalue (`auto&` capture);
        # a str-returning call is a value-type rvalue (owning `auto` capture).
        return THIRForEach(
            var=stmt.var,
            elem_type=et,
            iterable=_lower_expr(it, lc),
            body=body,
            const_loop_var=stmt.const_loop_var,
            iterable_lvalue=not isinstance(it, TpyCall),
            loc=loc,
        )
    if isinstance(stmt, TpyExprStmt):
        if _is_builtin_print(stmt.expr, declared, lc.analyzer):
            return THIRPrint(
                args=tuple(_lower_print_arg(a, lc) for a in stmt.expr.args),
                loc=loc)
        return THIRExprStmt(expr=_lower_expr(stmt.expr, lc), loc=loc)
    raise AssertionError(f"ineligible stmt reached lowering: {type(stmt).__name__}")


def _lower_print_arg(a: TpyExpr, lc: _LowerCtx) -> THIRPrintArg:
    """Lower one print arg + tag its `std::cout <<` wrapper form. A str literal
    lowers to a THIRStrLiteral (RAW: emitted via cpp_string_literal_expr); an
    eligible scalar lowers normally with its type-derived form."""
    if isinstance(a, TpyStrLiteral):
        return THIRPrintArg(
            THIRStrLiteral(value=a.value, result_type=lc.analyzer.get_expr_type(a)),
            PrintForm.RAW)
    if isinstance(a, TpyBytesLiteral):
        # gen_print threads no target, so the literal renders OWNED
        # (bytes_literal_owned / empty vector) inside the BytesPrinter wrap.
        return THIRPrintArg(
            THIRBytesLiteral(value=a.value, result_type=lc.analyzer.get_expr_type(a)),
            PrintForm.BYTES)
    # resolve_int_literals: an IntLiteral-typed arg (a literal-seeded container's
    # loop var / pop result) must derive its stream form from the resolved type.
    arg_type = resolve_int_literals(
        unwrap_readonly(lc.analyzer.get_expr_type(a)),
        lc.analyzer.ctx.default_int_for_literal)
    return THIRPrintArg(_lower_expr(a, lc), _print_arg_form(arg_type))


def lower_function(func: TpyFunction, analyzer, render_type=None,
                   self_type: 'TpyType | None' = None) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice.

    `render_type` (codegen's `TypeResolver.type_to_cpp`) renders F1 borrow-local
    decl types byte-identically; omit it only when no non-value local can arise
    (dump / value-scalar standalone lowering). `self_type` is the owning record's
    type when `func` is a record method: for kinds with a receiver (instance /
    property / dunder) `self` is seeded as an F1-record receiver (a `this`
    pointer) so its field reads route the same as a param's; a static method
    keeps only the record for its param-const lookups."""
    if not _function_eligible(func, analyzer, self_type):
        return None
    # Branch-local hoisting is not reproduced -- a function that hoists any
    # local out of a branch stays on the AST path.
    if analyzer.function_hoisted_vars.get(id(func)):
        return None
    is_record_method = self_type is not None and func.is_method
    # A static method has no receiver -- it lowers like a free function, but
    # keeps `record_name` so `_param_is_const` resolves its param verdicts from
    # the method's FunctionInfo on the owning record (the same lookup codegen's
    # `_get_method_mutated_params` uses).
    has_self = is_record_method and not func.is_staticmethod
    self_receiver = "self" if has_self else None
    record_name = (self_type.name
                   if is_record_method and isinstance(self_type, NominalType)
                   else None)
    lc = _LowerCtx(func, analyzer, render_type, self_receiver=self_receiver,
                   record_name=record_name)
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    if has_self:
        params_set["self"] = self_type  # the record receiver, a field source
        if func.is_readonly:
            # A readonly method's `this` is const, so a borrow local off `self.opt`
            # lifts to `const T*` (the OPTIONAL_TO_PTR const bump keys on the
            # receiver being in const_locals -- see _f1_is_const).
            lc.const_locals.add("self")
    if not _body_eligible(func.body, analyzer, params_set, lc.prescan,
                          in_branch=False, pointers=set(), rebind_slots=set(),
                          storage_tuple_locals=set()):
        return None
    params = tuple(THIRParam(name=n, type=t) for n, t in func.params)
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    # Seeded with params (and `self`): a write to such a name is a reassignment.
    declared: dict[str, TpyType] = dict(params_set)
    body = tuple(_lower_stmt(s, lc, declared) for s in func.body)
    fn = THIRFunction(
        name=func.name,
        params=params,
        return_type=rt,
        body=body,
        layout=THIRFunctionLayout(),
    )
    validate_function(fn)
    return fn


def _unwrap_copy(expr: TpyExpr, analyzer) -> TpyExpr:
    """Mirror of `CodeGenContext.unwrap_copy`: peel a `tpy.copy(x)` (the explicit
    field-copy acknowledgment) to `x`, so a `self.f = copy(p)` initializer lowers
    to the same `f(p)` direct-init the bare `self.f = p` does (the MIL copies
    implicitly). Analyzer-pure (reads `imported_names`), so lowering classifies
    without a CodeGenContext."""
    if isinstance(expr, TpyCoerce):
        inner = _unwrap_copy(expr.expr, analyzer)
        return inner if inner is not expr.expr else expr
    if (isinstance(expr, TpyCall) and len(expr.args) == 1
            and isinstance(expr.func, TpyName)
            and expr.func_name in analyzer.imported_names):
        mod, fn = analyzer.imported_names[expr.func_name]
        if mod == "tpy" and fn == "copy":
            return expr.args[0]
    return expr


def _is_record_value_source(source: TpyExpr, declared: dict[str, TpyType],
                            own_param_names: set[str], lc: _LowerCtx) -> bool:
    """A record-producing source that constructs an F1-record field (or its
    pointer-repr `Optional`) *directly* via an implicit copy/construct -- as opposed
    to a borrow `T*` that must lift through `ptr_to_optional`. Three shapes:

      * a non-own **F1-record param name** (`other`) -- an implicit MIL copy;
      * an **F1-record ctor-call rvalue** (`Inner(scalars)`) -- the F2d
        `_is_record_rvalue_source` shape, emitted as the bare `Name(args)` prvalue;
      * an **F1-record field-read off a param** receiver (`other.g`) -- a field copy.

    Own params (which move) and `self.<field>` reads (their pointee may be
    uninitialized at MIL time -- ordering-sensitive, deferred) are excluded."""
    analyzer = lc.analyzer
    if isinstance(source, TpyName):
        return (source.name not in own_param_names
                and _f1_record(declared.get(source.name), analyzer))
    if isinstance(source, TpyCall):
        return _is_record_rvalue_source(source, declared, analyzer)
    if isinstance(source, TpyFieldAccess):
        return (isinstance(source.obj, TpyName)
                and source.obj.name != lc.self_receiver
                and _field_receiver_ok(source, declared, analyzer)
                and _f1_record(analyzer.get_expr_type(source), analyzer))
    return False


def _ctor_field_init_ok(stmt: TpyStmt, own_field_names: set[str],
                        own_param_names: set[str], declared: dict[str, TpyType],
                        lc: _LowerCtx) -> bool:
    """A hoistable own-field initializer the ctor MIL slice admits -- a
    `self`-targeted own-field assign whose (field type, source) pair the tail
    emitter reproduces byte-for-byte.

    The `obj.name == "self"` guard is load-bearing -- `_field_receiver_ok` alone
    would also admit `other_record.field = ...`, which is not a member init. The
    own-field test matches `_extract_field_inits`. Routed shapes:

      * **scalar** (M3a): an eligible-scalar or Char value (`f(value)`).
      * **own-param move** (M3b-move): an `Own[...]` source consumed at its last use
        moves into a record / Optional[record] field (`f(std::move(p))`); checked
        before the copy arms because the cascade applies the move first and never
        also lifts via `ptr_to_optional`.
      * **pointer-repr `Optional[F1-record]`** (M3b-copy / -rvalue): a `None`
        (`f(std::nullopt)`), a non-own borrow source (`f(::tpy::ptr_to_optional(p))`),
        or a record-value source that constructs the optional directly (`f(Inner(v))`
        / `f(other.g)` / `f(other)`).
      * **plain F1-record** (M3b-copy / -rvalue): a record-value source --
        `copy()`-unwrapped param copy, ctor-call rvalue, or param field-read.

    `copy()` is unwrapped before the Optional check too (so `self.opt = copy(m)`
    routes like the record arm). A pointer-repr UNION field admits only the
    own-param move source (F4 U2). Field types beyond those (bytes / tuple /
    str / list -> F3+; cross-module / native / generic records) leave the ctor
    on the AST path."""
    analyzer = lc.analyzer
    if not (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names
            and _field_receiver_ok(stmt.target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(stmt.target)
    if _eligible_scalar(ftype) or _eligible_char(ftype):
        # A str-literal source into a Char field is a sema type error; the
        # reject is defensive (the target-typed `'x'` render would diverge).
        if _eligible_char(ftype) and isinstance(stmt.value, TpyStrLiteral):
            return False
        return _expr_eligible(stmt.value, declared, analyzer)
    pu = _eligible_ptr_union(ftype, analyzer)
    if pu is not None:
        # F4 U2: an `Own[A | B]` param moves into the value-variant field
        # (`u(std::move(v))` -- the M3b-move arm verbatim, type-agnostic at
        # lowering). Borrow lifts / member-value sources ride later cells.
        return _is_move_source(_unwrap_copy(stmt.value, analyzer), lc,
                               own_param_names)
    is_opt = (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()
              and _f1_record(ftype.inner, analyzer))
    if not (is_opt or _f1_record(ftype, analyzer)):
        return False
    source = _unwrap_copy(stmt.value, analyzer)
    # M3b-move: an own-param at its last use moves into the field.
    if _is_move_source(source, lc, own_param_names):
        return True
    if is_opt:
        # None / a non-own borrow `T*` (pointer-repr Optional param, lifts via
        # ptr_to_optional) / a record-value source (constructs the optional directly).
        # pointers empty: a ctor MIL has no locals.
        return (isinstance(source, TpyNoneLiteral)
                or _is_borrow_ptr_local(source, declared, set())
                or _is_record_value_source(source, declared, own_param_names, lc))
    return _is_record_value_source(source, declared, own_param_names, lc)


def _ctor_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """A ctor param the MIL slice can reference: the method-param set (value scalar /
    F1-record, incl. plain `Own`) plus two Optional shapes whose record is F1 -- a
    pointer-repr `Optional[F1-record]` (the borrow source for `ptr_to_optional`) and
    an **own-optional** (`Own[Inner | None]` / `Optional[Own[Inner]]`, which moves
    into an `Optional[F1-record]` field via the move arm). The raw types match what
    `declared` holds and `_is_borrow_ptr_local` tests. The field-init gate decides
    per-field whether the param is used in an admitted way; an unhandled use rejects
    the whole ctor (-> AST path)."""
    if _f1_param_eligible(ptype, analyzer):
        return True
    if (isinstance(ptype, OptionalType) and ptype.uses_pointer_repr()
            and _f1_record(ptype.inner, analyzer)):
        return True
    # Own-optional: peel Own (and the inner/outer Optional) to the underlying
    # record -- or, for the F4 U2 move cell, an eligible pointer-repr union
    # (`Own[A | B]` moves into the value-variant field, M3b-move).
    own = unwrap_optional_own(unwrap_readonly(ptype)) if isinstance(ptype, TpyType) else None
    if own is not None:
        inner = own.wrapped
        if isinstance(inner, OptionalType):
            inner = inner.inner
        return (_f1_record(inner, analyzer)
                or _eligible_ptr_union(inner, analyzer) is not None)
    return False


def lower_constructor(record, init_method: TpyFunction, analyzer,
                      render_type=None,
                      self_type: 'TpyType | None' = None) -> THIRConstructor | None:
    """Lower a constructor to a THIRConstructor, or None if outside the slice.

    Same-module non-generic record, flat or with same-module F1 base(s) (M3d: each
    `super().__init__` / `BaseN.__init__` call lowers to a base initializer, sorted by
    parent declaration order; a direct inherited-field write goes to the body). The
    leading run of hoistable own-field
    initializers (the M3a/M3b field-source slice) goes to the member-init-list; the rest
    of the body -- docstring / `pass` trivia (M3c-trivia), non-init statements, and field
    inits that cannot hoist or follow a chain break (M3c-demotion) -- lowers through the
    shared statement machinery (`_body_eligible` / `_lower_stmt`), the same path method
    bodies use. The ctor routes only when every non-trivia body statement is in the slice;
    otherwise it stays on the AST path, byte-identical. The signature stays on the AST path
    (the M1 method precedent); only the MIL + body tail routes here."""
    if self_type is None or not _f1_record(self_type, analyzer):
        return None
    # M3d: same-module F1 base(s) route -- each `super().__init__` / `BaseN.__init__`
    # call lowers to a base initializer (sorted by parent declaration order), and a
    # direct inherited-field write goes to the body. A non-F1 base (cross-module /
    # generic / native -- its `to_cpp()` would not match) keeps the ctor on the AST
    # path. Reject overloaded / native / generator / generic __init__ -- those take
    # emit paths the tail emitter does not reproduce.
    ri = analyzer.registry.get_record(record.name)
    if ri is None:
        return None
    if any(not _f1_record(p, analyzer) for p in ri.parents):
        return None
    if (init_method.is_overload_stub or init_method.native_function
            or init_method.is_async or init_method.is_generator
            or init_method.type_params):
        return None
    # Params must be value scalars, F1-records, or pointer-repr Optional[F1-record]
    # (see `_ctor_param_eligible`). This keeps the AST-emitted signature a plain ctor
    # (no protocol/dynamic template) so it pairs with the THIR tail; a param used in
    # an unhandled way is caught by the per-field init gate below.
    for _name, ptype in init_method.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _ctor_param_eligible(pt, analyzer):
            return None
    # Own[T] / Own[T]|None params: their MIL sources move (M3b-move), so M3b-copy
    # rejects them as record-field sources (mirror `_extract_field_inits`'s set).
    own_param_names = {pname for pname, ptype in init_method.params
                       if isinstance(ptype, TpyType)
                       and unwrap_optional_own(unwrap_readonly(ptype)) is not None}
    declared: dict[str, TpyType] = {n: t for n, t in init_method.params}
    declared["self"] = self_type
    own_field_names = {f.name for f in record.fields}
    # lc is built before the gate loop: the move check (`_is_move_source`) reads
    # `analyzer.ctx.all_last_uses` through it.
    lc = _LowerCtx(init_method, analyzer, render_type, self_receiver="self",
                   record_name=record.name)
    # Base initializers (`super().__init__` / `BaseN.__init__`), sorted by parent
    # declaration order (M3d); None if any is outside the slice -> AST path.
    base_inits = _lower_base_inits(init_method, ri, declared, lc)
    if base_inits is None:
        return None
    field_inits: list[TpyAssign] = []
    body_stmts: list[TpyStmt] = []  # demoted inits + non-init stmts + trivia, source order
    body_written_self_fields: set[str] = set()
    chain_broken = False
    for stmt in init_method.body:
        if is_base_init_call(stmt):  # handled above; breaks no chain
            continue
        # Docstring / `pass` (M3c-trivia): emit no code and break no hoist chain,
        # but stay in the body so its braces are non-empty (` {\n    }`, not ` {}`).
        if is_docstring(stmt) or isinstance(stmt, TpyPassStmt):
            body_stmts.append(stmt)
            continue
        # An inherited-field write (`self.<base field> = expr`, M3d) goes to the body --
        # the base ctor owns the MIL slot -- WITHOUT breaking the hoist chain. It is
        # tracked so a later own-field hoist that reads it demotes (below). A property
        # setter (also a non-own self field) lands here too and rejects via body
        # ineligibility (`_field_receiver_ok`). NB the AST checks this only on a live
        # chain (after `chain_broken` it demotes instead, skipping the tracking set); the
        # divergence is inert -- once the chain is broken every later own-field init
        # demotes regardless, so the set is never consulted.
        if _is_self_nonown_field_assign(stmt, own_field_names):
            body_written_self_fields.add(stmt.target.field)
            body_stmts.append(stmt)
            continue
        # A leading own-field init whose (field, source) the MIL reproduces hoists.
        # `_ctor_field_init_ok` already returns False for a non-init statement / a field
        # init with a non-hoistable source (body-local / bare-name RHS / ineligible
        # value), so the gate distinguishes hoist from demote. An init reading an
        # inherited field written earlier in the body must demote (the MIL runs first,
        # before that write) -- the `expr_reads_self_field` trigger (no-op until an
        # inherited-field write populates the set).
        if (not chain_broken
                and _ctor_field_init_ok(stmt, own_field_names, own_param_names,
                                        declared, lc)
                and not expr_reads_self_field(stmt.value, body_written_self_fields)):
            field_inits.append(stmt)
            continue
        # A clean leading own-field init (live chain, not reading an earlier
        # inherited-field write) the AST hoists into the MIL but THIR can't reproduce
        # there -- an F3+ field type (tuple / str / list / union) or a source outside
        # the MIL slice -- must keep the whole ctor on the AST path. Demoting it into
        # the body would diverge from the AST's MIL hoist (the AST never demotes a
        # clean leading own-field init). After a chain break, or when the init reads an
        # earlier inherited-field write, the AST demotes too -- those fall through.
        if (not chain_broken
                and _is_self_own_field_assign(stmt, own_field_names)
                and not expr_reads_self_field(stmt.value, body_written_self_fields)):
            return None
        # Demote to the body. Demoting breaks the chain (mirrors `_extract_field_inits`'s
        # `demote()`): the MIL runs before the body, so a later otherwise-hoistable init
        # must also demote to preserve source evaluation order.
        chain_broken = True
        body_stmts.append(stmt)
    # The demoted inits + non-init statements lower through THIR's statement machinery
    # (the trivia are admitted directly); a body statement outside the slice keeps the
    # whole ctor on the AST path. The trivia carry no `declared`-scope growth, so the
    # gate runs over the non-trivia subset.
    body_non_trivia = [s for s in body_stmts
                       if not (is_docstring(s) or isinstance(s, TpyPassStmt))]
    if not _body_eligible(body_non_trivia, analyzer, declared, lc.prescan,
                          in_branch=False, pointers=set(), rebind_slots=set(),
                          storage_tuple_locals=set()):
        return None
    body_declared = dict(declared)
    ctor = THIRConstructor(
        record_name=record.name,
        params=tuple(THIRParam(name=n, type=t) for n, t in init_method.params),
        mil_inits=tuple(_lower_ctor_mil_init(s, own_param_names, declared, lc)
                        for s in field_inits),
        base_inits=tuple(base_inits),
        body=tuple(_lower_ctor_body_stmt(s, lc, body_declared) for s in body_stmts),
    )
    validate_constructor(ctor)
    return ctor


def _is_self_nonown_field_assign(stmt: TpyStmt, own_field_names: set[str]) -> bool:
    """A `self.<field> = expr` whose field is not an own field -- an inherited-field
    write or a property setter (M3d). The base ctor owns its slot, so the write goes
    to the body (not the MIL), tracked so a later own-field hoist that reads it demotes."""
    return (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field not in own_field_names)


def _is_self_own_field_assign(stmt: TpyStmt, own_field_names: set[str]) -> bool:
    """A `self.<own field> = expr` -- a member initializer the AST hoists into the
    MIL. THIR must hoist it too or keep the whole ctor on the AST path; demoting it
    into the body (when THIR's MIL slice can't reproduce its field type / source)
    would diverge from the AST's MIL hoist."""
    return (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names)


def _lower_base_inits(init_method: TpyFunction, ri, declared: dict[str, TpyType],
                      lc: _LowerCtx) -> 'list[THIRBaseInit] | None':
    """Mirror `_extract_base_inits`: lower every `super().__init__` / `BaseN.__init__`
    call to a THIRBaseInit, sorted by parent declaration order (so a multi-base list
    emits in the order C++ runs the base ctors, avoiding -Wreorder). None if any base
    init is outside the slice -- the whole ctor then stays on the AST path."""
    analyzer = lc.analyzer
    parent_order: dict[int, int] = {}
    for idx, parent in enumerate(ri.parents):
        p_info = analyzer.registry.get_record_for_type(parent)
        if p_info is not None:
            parent_order[id(p_info)] = idx
    entries: list[tuple[int, THIRBaseInit]] = []
    for src_idx, stmt in enumerate(init_method.body):
        if not is_base_init_call(stmt):
            continue
        lowered = _lower_base_init(stmt, declared, lc)
        if lowered is None:
            return None
        bi, parent_type = lowered
        p_info = analyzer.registry.get_record_for_type(parent_type)
        rank = (parent_order.get(id(p_info), len(parent_order) + src_idx)
                if p_info is not None else len(parent_order) + src_idx)
        entries.append((rank, bi))
    entries.sort(key=lambda e: e[0])
    return [bi for _, bi in entries]


def _lower_base_init(stmt: TpyStmt, declared: dict[str, TpyType],
                     lc: _LowerCtx) -> 'tuple[THIRBaseInit, TpyType] | None':
    """Lower one base-init call to `(THIRBaseInit, parent_type)`, or None outside the
    slice (the caller reuses `parent_type` for the parent-order rank). Mirrors
    `_extract_base_inits`'s `{parent_type.to_cpp()}({args})` render for both the
    `super().__init__(args)` and the explicit `BaseN.__init__(self, args)` forms (sema
    strips `self` from the latter's args). The base must be F1 (so `to_cpp()` is
    byte-identical) and the args eligible scalars; kwargs / star args are out."""
    analyzer = lc.analyzer
    expr = stmt.expr
    # The only narrowing of `stmt.expr` to a TpyMethodCall (is_base_init_call holds at
    # the call site, but the type system doesn't carry that) -- guards `.super_parent_type`.
    if not isinstance(expr, TpyMethodCall):
        return None
    parent_type = expr.super_parent_type or expr.unbound_self_parent_type
    if parent_type is None or not _f1_record(parent_type, analyzer):
        return None
    if expr.kwargs or expr.double_star_unpack is not None:
        return None
    if not all(_eligible_scalar(analyzer.get_expr_type(a))
               and _expr_eligible(a, declared, analyzer) for a in expr.args):
        return None
    return (THIRBaseInit(base_cpp=parent_type.to_cpp(),
                         args=tuple(_lower_expr(a, lc) for a in expr.args)),
            parent_type)


def _lower_ctor_body_stmt(stmt: TpyStmt, lc: _LowerCtx,
                          declared: dict[str, TpyType]) -> THIRStmt:
    """Lower one ctor-body statement: a docstring / `pass` to a no-op (its `loc`
    drives the source comment as in M3c-trivia -- `pass` keeps it, a docstring
    drops it); everything else (a demoted field init or a non-init statement)
    through the shared `_lower_stmt`, the same machinery method bodies use."""
    if is_docstring(stmt):
        return THIRNoOpStmt()
    if isinstance(stmt, TpyPassStmt):
        return THIRNoOpStmt(loc=getattr(stmt, "loc", None))
    return _lower_stmt(stmt, lc, declared)


def _lower_ctor_mil_init(stmt: TpyAssign, own_param_names: set[str],
                         declared: dict[str, TpyType], lc: _LowerCtx) -> THIRMilInit:
    """Build one member-init-list entry from a hoisted field initializer (the gate
    already admitted it). Mirrors the record/Optional arms of `_extract_field_inits`:

      * an **own-param at last use** moves (`move=True`, plain source -- never
        `ptr_to_optional`, per the cascade) [M3b-move];
      * a **scalar / Char** -> the lowered value [M3a];
      * a pointer-repr **Optional[F1-record]** -> a STORAGE `None` literal
        (`std::nullopt`), a non-own borrow `T*` lifted via `ptr_to_optional` [M3b-copy],
        or a record-value source (ctor-call / field-read / param copy) that constructs
        the optional directly [M3b-rvalue];
      * a plain **F1-record** -> the `copy()`-unwrapped record-value source [M3b-copy/-rvalue]."""
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    loc = getattr(stmt, "loc", None)
    field_cpp = escape_cpp_name(stmt.target.field)
    source = _unwrap_copy(stmt.value, analyzer)
    if _is_move_source(source, lc, own_param_names):
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc), move=True)
    if _eligible_scalar(ftype) or _eligible_char(ftype):
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(stmt.value, lc))
    if isinstance(ftype, OptionalType):
        if isinstance(source, TpyNoneLiteral):
            v: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                      form=Form.STORAGE, loc=loc)
        elif _is_borrow_ptr_local(source, declared, set()):
            v = THIRFormConvert(result_type=ftype, value=_lower_expr(source, lc),
                                form=Form.STORAGE, move=False, loc=loc)
        else:
            # A record-value source constructs the optional directly -- no
            # ptr_to_optional (that lifts a borrow `T*`, not a record prvalue/copy).
            v = _lower_expr(source, lc)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc))


def _method_self_type(record, analyzer) -> 'TpyType | None':
    """The `self` receiver type for an M1 method feed: the record's canonical
    qualified `NominalType` for a non-generic record, else None (a generic
    record's `self` is templated, outside the F1-record slice). The qname is
    load-bearing -- a bare `NominalType(name)` has no registry entry, so
    `is_user_record` (hence `_f1_record`) is False. `_f1_record` applies the
    remaining native / cross-module gates at lowering."""
    if record.type_params:
        return None
    ri = analyzer.registry.get_record(record.name)
    if ri is None:
        return None
    return NominalType(record.name, _module_qname=ri.qualified_name())


def iter_module_callables(module: TpyModule, analyzer):
    """Yield `(callable, self_type)` for every function / method the slice may
    admit -- the single feed list shared by `lower_module` and codegen so the
    two never drift. The eligibility gate still has the final say; this only
    enumerates candidates. Free functions yield `self_type=None`; record methods
    (instance / static / property / dunder) yield the owning record's type
    (None-skipped for generic records). The constructor is excluded -- its body
    is emitted via the member-init-list driver (the M3 ctor frontier), not
    gen_method_def."""
    for func in module.functions:
        yield func, None
    for record in module.records:
        self_type = _method_self_type(record, analyzer)
        if self_type is None:
            continue
        init = record.init_method
        for method in record.methods:
            if method is not init:
                yield method, self_type


def iter_module_constructors(module: TpyModule, analyzer):
    """Yield `(record, init_method, self_type)` for every record that defines an
    `__init__` -- the ctor feed for the M3 frontier, the sibling of
    `iter_module_callables` (which excludes the ctor because its body is emitted by
    the member-init-list driver, not `gen_body`). `self_type` is the owning
    record's F1-record receiver (None-skipped for generic records, which
    `lower_constructor` also rejects). The eligibility gate in `lower_constructor`
    has the final say; this only enumerates candidates."""
    for record in module.records:
        init = record.init_method
        if init is None:
            continue
        self_type = _method_self_type(record, analyzer)
        if self_type is None:
            continue
        yield record, init, self_type


def lower_module(module: TpyModule, analyzer, render_type=None) -> THIRModule:
    """Lower every eligible function and instance method in `module`; skip the rest."""
    out = THIRModule(module_name=getattr(analyzer.ctx, "module_name", "generated"))
    for func, self_type in iter_module_callables(module, analyzer):
        thir_fn = lower_function(func, analyzer, render_type, self_type=self_type)
        if thir_fn is not None:
            out.functions.append(thir_fn)
    return out
