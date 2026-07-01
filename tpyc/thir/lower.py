"""AST + sema -> THIR lowering.

`lower_function` converts one analyzed `TpyFunction` to a `THIRFunction`,
returning None when the function falls outside the supported slice (the
eligibility gate). Lowering reads the analyzer here so codegen never has to;
every fact codegen consumes is materialized onto the returned THIR nodes.

Eligible slice: non-generic, non-generator/async, plain-linkage free functions
-- and plain instance methods of same-module non-generic records (`self` as an
F1-record `this` receiver) -- whose params/locals/return are fixed-width-int
scalars (plus the F1/F2 non-value record forms for locals/returns), with
straight-line bodies (var-decl / assign / return) over names and literals.
Anything else -> None (stays on the AST codegen path). The gate is the safety
boundary: it must reject every construct the emitter cannot reproduce byte-
for-byte.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..parse.nodes import (
    FunctionLinkage,
    TpyAssign,
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyExprStmt,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyForEach,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyMethodCall,
    TpyModule,
    TpyName,
    TpyNoneLiteral,
    TpyPassStmt,
    TpyReturn,
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
    LiteralType, NominalType, OptionalType, OwnType, ReadonlyType, TpyType,
    TupleType, ValueForm, VoidType, is_float_type, is_void_like_type,
    resolve_int_literals, unwrap_optional_own, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync,
)
from ..type_def_registry import (
    int_traits_of, is_big_int_type, is_bool_type, is_dict, is_fixed_int_type,
    is_float32_type, is_list, is_set,
)
from ..codegen_cpp.type_resolution import resolve_stmt_binding_type
from ..modules.type_resolution import is_native_iterable
from ..codegen_cpp.forms import (
    LocalBinding, classify_local_binding, is_storage_tuple_alias_decl,
    reads_storage_form_optional,
)
from ..value_category import is_rvalue_source
from ..codegen_cpp.context import escape_cpp_name
from .nodes import (
    Form,
    PrintForm,
    THIRAssign,
    THIRBaseInit,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRConstructor,
    THIRExpr,
    THIRExprStmt,
    THIRFieldAccess,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRFunction,
    THIRFunctionLayout,
    THIRIf,
    THIRLiteral,
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
    THIRStrLiteral,
    THIRSubscript,
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
# Literal-into-typed-slot coercions the slice reproduces, both pass-throughs on
# the C++ side (the inner literal renders directly in the slot's type): a literal
# into a fixed-int slot, and a float literal into a double `float` slot.
_INT_LIT_COERCION = "int_literal_to_fixed_int"
_FLOAT_LIT_COERCION = "float_literal_to_float"


def _eligible_scalar(t: TpyType | None) -> bool:
    """A type the emitter can render and reason about without form facts.

    Fixed-width ints, `bool`, and double `float` (-> `double`): borrow/storage
    form never arises and the C++ spelling comes straight from `TpyType.to_cpp()`.
    Float32 is excluded -- its literals need a `f` suffix the slice does not emit.
    """
    return t is not None and (is_fixed_int_type(t) or is_bool_type(t)
                              or (is_float_type(t) and not is_float32_type(t)))


def _eligible_return(t: TpyType | None, analyzer) -> bool:
    return (t is None or isinstance(t, VoidType) or _eligible_scalar(t)
            or _storage_optional_return_type(t, analyzer) is not None
            or _borrow_tuple_return_type(t, analyzer) is not None)


# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "hoisted", "move_through",
                 "ret_storage_opt", "ret_borrow_tuple")

    def __init__(self, func: TpyFunction, analyzer) -> None:
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


def _container_scalar_read(t: TpyType | None) -> bool:
    """A container whose element/value read renders as a value scalar via the
    container subscript emit: `list[scalar]` or `dict[fixed-int-key, scalar-value]`.
    `set` has no `__getitem__`. A view-typed (str/bytes) key rides a later cell (its
    literal keys need static-storage handling); a BigInt key rides the same cell as
    BigInt indices (both need the `.to_fixed_check<int32_t>()` narrow, out of the
    fixed-int scalar slice), so cell 1 keeps to fixed-int keys -- read identically to a
    list index (`::tpy::__getitem__(c, i)`). An `Own[container]` (move-in `T&&` param)
    is excluded explicitly -- its ABI differs from the borrow shape this slice's emit
    assumes, and it rides a later cell (mirrors the Own unwrap in `_f1_record`, which
    admits Own where this deliberately does not)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if is_list(t):
        return bool(args) and _eligible_scalar(args[0])
    if is_dict(t):
        if not args or len(args) < 2:
            return False
        key, val = args[0], args[1]
        return is_fixed_int_type(key) and _eligible_scalar(val)
    return False


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
    element/value is a value scalar (`::tpy::__getitem__(c, i)`, or the bounds-safe
    `c[static_cast<std::size_t>(i)]`). The receiver is a plain name (a non-name or
    narrowed-Optional receiver rides a later cell); the index is any eligible
    value-scalar expr. A `readonly[container]` receiver routes too (byte-identical) --
    sema readonly-wraps only non-value elements, so a scalar element read is never
    `readonly[scalar]`; the result-scalar check is a defensive guard confirming the read
    yields a value scalar (redundant with the element-scalar check today, robust if the
    container predicate later widens)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return False
    return (_container_scalar_read(analyzer.get_expr_type(recv))
            and _eligible_scalar(analyzer.get_expr_type(e))
            and _expr_eligible(e.index, locals_, analyzer))


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
    # Args must be eligible SCALARS (mirror _call_eligible): a non-scalar arg
    # (a record pointer-local / Own[T]) needs the AST's `(*q)` deref or auto-move
    # `std::move(q)`, neither of which the bare THIRCall arg emit reproduces.
    return all(_eligible_scalar(analyzer.get_expr_type(a))
               and _expr_eligible(a, declared, analyzer) for a in init.args)


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
    markers reject it)."""
    target = stmt.target
    if not (_field_receiver_ok(target, declared, analyzer)
            or _field_over_subscript_ok(target, declared, analyzer)):
        return False
    if not _eligible_scalar(analyzer.get_expr_type(target)):
        return False
    return _expr_eligible(stmt.value, declared, analyzer)


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


def _binop_eligible(e: TpyBinOp, locals_: dict[str, TpyType], analyzer) -> bool:
    rb = e.resolved_binop
    rt = analyzer.get_expr_type(e)
    if e.op in _ARITH_OPS:
        # Same-width arithmetic: a templated dunder, scalar result. Excludes any
        # mixed/widening result the slice can't render without a coercion node.
        if rb is None or not getattr(rb.method, "cpp_template", None):
            return False
        if not _eligible_scalar(rt):
            return False
    elif e.op in _COMPARE_OPS:
        # A scalar comparison -> bool, usable as a value (`x = a < b`) or an
        # `if`/`while` condition. `<`/`==` carry a `{self} OP {0}` template; the
        # derived comparisons (`<= > >= !=`) have rb=None and emit as a bare C++
        # operator. A rb *with* a non-template would emit some other way -> reject.
        if rt is None or not is_bool_type(rt):
            return False
        if rb is not None and not getattr(rb.method, "cpp_template", None):
            return False
        if _mixed_sign_compare(_operand_type(e.left, locals_, analyzer),
                               _operand_type(e.right, locals_, analyzer)):
            return False
    else:
        # Logical &&/|| (narrowing + short-circuit-slot emit) and is/in/bitwise
        # are out of the slice -> AST path.
        return False
    return (_expr_eligible(e.left, locals_, analyzer)
            and _expr_eligible(e.right, locals_, analyzer))


def _is_len_native(e: TpyExpr) -> bool:
    """Whether `e` is the builtin `len(...)` call -- it resolves to the `tpy::__len__`
    @native free function. A user function named `len` has a different (or no)
    native_name and is excluded, so the emit dispatch keys on the symbol, not the name."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "len"):
        return False
    fi = e.resolved_function_info
    return fi is not None and fi.native_name == "tpy::__len__"


def _is_len_call(e: TpyExpr, locals_: dict[str, TpyType]) -> bool:
    """The eligible `len(name)` form: the builtin len over a single in-scope name of a
    builtin container type (`::tpy::__len__(name)`, Int32). The container restriction is
    load-bearing, not cosmetic: a container is a by-ref/by-value param that emits as the
    bare name, but a record (or `Optional`) with `__len__` bound to a pointer-local
    would need `(*p)` (the AST's is_indirect_name deref) that the bare emit misses -- so
    only list/dict/set (never pointer-locals) are admitted. A non-name arg (literal,
    subscript, call) rides a later cell."""
    if not _is_len_native(e):
        return False
    if e.kwargs or e.double_star_unpack is not None or len(e.args) != 1:
        return False
    arg = e.args[0]
    if not (isinstance(arg, TpyName) and arg.name in locals_):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[arg.name])))
    return is_list(t) or is_dict(t) or is_set(t)


def _call_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer,
                   *, stmt_position: bool = False) -> bool:
    if _is_len_call(e, locals_):
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
    # In value position the result must be an eligible scalar; as a bare statement
    # the result is discarded, so a `void` (None) return is admitted too. The emit
    # (`callee(args);`) is identical either way.
    ret = analyzer.get_expr_type(e)
    if not (_eligible_scalar(ret) or (stmt_position and is_void_like_type(ret))):
        return False
    # Every argument must be an eligible SCALAR. A non-value arg (a record /
    # Own[record] param passed positionally) crosses an ownership boundary --
    # an Own param at its last use auto-moves (`f(std::move(p))`), a borrow param
    # may lift -- which the bare-name THIRCall emit does not reproduce. Scalars
    # are value types: copied, never moved, so the bare call is byte-identical.
    return all(_eligible_scalar(analyzer.get_expr_type(a))
               and _expr_eligible(a, locals_, analyzer) for a in e.args)


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
    tr = int_traits_of(t)
    if tr is not None and tr.bits == 8:
        return PrintForm.INT8
    return PrintForm.RAW


def _print_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer) -> bool:
    """A `print(<args>)` in the no-kwargs common-arg subset: every arg is a str
    literal or an eligible scalar (fixed-int / bool / double). Any `sep=`/`end=`/
    `file=`/`flush=` kwarg, `**`-unpack, f-string, or non-scalar arg falls back to
    the AST path (gen_print's richer cases)."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    for a in e.args:
        if isinstance(a, TpyStrLiteral):
            continue
        if (_eligible_scalar(analyzer.get_expr_type(a))
                and _expr_eligible(a, locals_, analyzer)):
            continue
        return False
    return True


def _expr_eligible(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    if isinstance(e, TpyName):
        # A name outside the local/param set is a module/native/cross-module
        # global: the AST path resolves it to a qualified C++ symbol, which the
        # slice does not yet materialize. Reject -> stays on the AST path.
        return e.name in locals_
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
    if isinstance(e, TpyFieldAccess):
        # A scalar field read off an F1-record receiver (`recv.field`, value
        # form). The non-value field source for a borrow-local binding is handled
        # in the var-decl branch, not here -- a non-value field read is not a
        # value expression.
        return (_eligible_scalar(analyzer.get_expr_type(e))
                and (_field_receiver_ok(e, locals_, analyzer)
                     or _field_over_subscript_ok(e, locals_, analyzer)
                     or _optional_field_over_subscript_ok(e, locals_, analyzer)))
    if isinstance(e, TpySubscript):
        # A value-result tuple subscript read `t[N]` (`std::get<N>(t)`) off an
        # eligible tuple receiver, or a container subscript read `c[i]`
        # (`::tpy::__getitem__(c, i)` / bounds-safe operator[]) off a
        # list[scalar] / dict[int, scalar] receiver. Both value-scalar results.
        return (_tuple_subscript_value_read(e, locals_, analyzer) is not None
                or _container_subscript_value_read(e, locals_, analyzer))
    if isinstance(e, TpyBinOp):
        return _binop_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCall):
        return _call_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCoerce):
        # Only the literal-into-typed-slot passthroughs; other coercions
        # (widening, bigint, int<->float, float32, optional-wrap, ...) take
        # their own emit paths.
        return (e.coercion.name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION)
                and _expr_eligible(e.expr, locals_, analyzer))
    return False


def _f1_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """An F1-eligible param: a value scalar, an F1-record passed by reference
    (`T&` / `const T&`, accessed `.`), an F3 borrow-form pointer-repr tuple
    (`std::tuple<..., T*>`, a borrow source for a `tuple_to_storage` field write), a
    pure value-scalar tuple (`const std::tuple<...>&`, read by subscript), a
    scalar-element container (`list[scalar]` / `dict[int, scalar]`, read by subscript),
    or a record-element list (`list[record]`, iterated by `for x in c` -- the signature
    stays on the AST path per M1). Optional/view-keyed-container/cross-module/native
    record params stay on the AST path."""
    return (_eligible_scalar(ptype) or _f1_record(ptype, analyzer)
            or _f1_tuple(ptype, analyzer) is not None
            or _value_scalar_tuple(ptype)
            or _container_scalar_read(ptype)
            or _container_record_iter(ptype, analyzer))


def _function_eligible(func: TpyFunction, analyzer,
                       self_type: 'TpyType | None' = None) -> bool:
    # A plain instance method is admitted when its receiver is an F1-record
    # (`self_type` passed by the caller from the owning record). Static/property
    # methods take other emit paths. An instance method takes value-scalar and
    # F1-record params; a record param's const verdict comes from the method's own
    # const_borrow_params, resolved at lowering -- see the param loop below and
    # `_param_is_const`.
    is_instance_method = (func.is_method and not func.is_staticmethod
                          and not func.is_property_getter
                          and not func.is_property_setter)
    if func.is_method and not is_instance_method:
        return False
    # Defensive: the parser sets is_method=True on staticmethods too, so the
    # check above already excludes them -- this guards against a free function
    # ever carrying is_staticmethod without is_method.
    if func.is_staticmethod:
        return False
    if is_instance_method and (self_type is None
                               or not _f1_record(self_type, analyzer)):
        return False
    if func.is_property_getter or func.is_property_setter:
        return False
    if func.is_overload_stub or func.native_function or func.is_consuming:
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
    # An `if`/`while` condition: a bare bool local/param (`if flag:`) or a single
    # scalar comparison (bool result), the latter routed through _binop_eligible
    # so it gets the same mixed-sign gate as comparison-as-value. and/or/not,
    # chained compares, and a bool-literal condition (the AST path may
    # dead-branch-eliminate it) are not handled here -> AST path.
    if isinstance(cond, TpyName):
        rt = analyzer.get_expr_type(cond)
        return cond.name in declared and rt is not None and is_bool_type(rt)
    if isinstance(cond, TpyBinOp) and cond.op in _COMPARE_OPS:
        return _binop_eligible(cond, declared, analyzer)
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
    return None


def _range_bound_eligible(arg: TpyExpr, declared: dict[str, TpyType]) -> bool:
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
    if _is_len_call(arg, declared):
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
    if nargs == 2 and not _range_bound_eligible(it.args[0], declared):
        return False
    stop_arg = it.args[0] if nargs == 1 else it.args[1]
    if not _range_bound_eligible(stop_arg, declared):
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
    # `dict[fixed-int-key]`, a typed copy) or F1-record (`list[record]`, a borrow alias)
    # loop var. A str/bytes dict key (a view), a generator/user-iterator (the
    # `__iter__`/`__next__` fallback), and the shared richer for-shapes stay on the AST
    # path.
    if not _for_loop_shape_ok(stmt, analyzer, declared):
        return False
    it = stmt.iterable
    # A plain in-scope container name (an lvalue -> `auto&`); a non-name iterable
    # (range() call, subscript, attribute) rides a later cell.
    if not isinstance(it, TpyName) or it.name not in declared:
        return False
    if not is_native_iterable(analyzer.get_expr_type(it), analyzer.registry):
        return False
    # The loop var (list/set/Span/Array element, or dict key) is a value scalar (typed
    # copy) or an F1-record (a borrow alias -- `auto&&`/`const auto&`, read/written
    # `.field` exactly like a record param, so it flows through the body constructs
    # identically). No rebinding guard is needed: sema forbids reassigning a non-value
    # loop var (`_check_nonvalue_rebinding` -- `p = other` is a hard error), so an
    # eligible record loop var is only ever read or field-mutated through the alias, both
    # matching Python's reference semantics.
    et = unwrap_ref_type(stmt.elem_type) if stmt.elem_type is not None else None
    if not _eligible_scalar(et) and not _f1_record(et, analyzer):
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
        elif stmt.name in rebind_slots:
            # F2d rebind-slot reseat: an rvalue F1-record ctor / by-value source.
            return _is_record_rvalue_source(stmt.init, declared, analyzer)
        elif stmt.name in pointers:
            # F2a pointer-local reseat: an lvalue F1-record field source only.
            return _f2_reseat_ok(stmt.init, declared, analyzer)
        if not _expr_eligible(stmt.init, declared, analyzer):
            return False
        # First declaration: the local's type must be an eligible scalar (a
        # bare-literal init analyzes as IntLiteralType, pinning no width -> out).
        # A reassignment targets an already-validated local (its value just
        # renders into the existing slot), so the type check does not apply.
        return is_reassign or _eligible_scalar(_var_decl_type(stmt, analyzer))
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyName):
            return (stmt.target.name in declared
                    and _expr_eligible(stmt.value, declared, analyzer))
        # F2b/F2c/F2e: an optional-field write `recv.field = <borrow>` / `= None`;
        # a plain scalar-field write `recv.field = <scalar>`; or an F3 tuple-field
        # write `recv.field = <borrow tuple>` (tuple_to_storage).
        return (_f2b_optional_field_write_ok(stmt, declared, pointers, analyzer)
                or _scalar_field_write_ok(stmt, declared, analyzer)
                or _f1_tuple_field_write_ok(stmt, declared, storage_tuple_locals,
                                            analyzer))
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
        return _scalar_aug_assign_ok(stmt, declared, analyzer)
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


def _lower_expr(e: TpyExpr, lc: '_LowerCtx') -> THIRExpr:
    analyzer = lc.analyzer
    rtype = analyzer.get_expr_type(e)
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
        # but kept honest so a convert source is never mislabeled.
        if e.name in lc.storage_tuple_locals:
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
        # Container subscript -> the checked dunder `::tpy::__getitem__(c, i)` or, when
        # sema proved the index in-bounds, `c[static_cast<std::size_t>(i)]`. The index
        # is a fixed-int value-scalar expr (a runtime-BigInt index is out of the scalar
        # slice, so no `.to_fixed_check` narrow arises). `form` stays VALUE.
        return THIRSubscript(
            result_type=rtype,
            receiver=_lower_expr(e.obj, lc),
            index=_lower_expr(e.index, lc),
            bounds_safe=e.bounds_safe,
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        return THIRLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyBinOp):
        return THIRBinOp(
            result_type=rtype,
            left=_lower_expr(e.left, lc),
            op=e.op,
            right=_lower_expr(e.right, lc),
            resolved=e.resolved_binop,
            divisor_non_zero=e.divisor_non_zero,
            loc=loc,
        )
    if isinstance(e, TpyCall):
        # A @native free-function builtin (currently `len` -> `tpy::__len__`) carries
        # its resolved symbol so the emit dispatches on it, not the source name.
        native_name = e.resolved_function_info.native_name if _is_len_native(e) else None
        return THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(_lower_expr(a, lc) for a in e.args),
            native_name=native_name,
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        return THIRCoerce(
            result_type=rtype,
            expr=_lower_expr(e.expr, lc),
            coercion_name=e.coercion.name,
            loc=loc,
        )
    raise AssertionError(f"ineligible expr reached lowering: {type(e).__name__}")


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
    inner = value
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
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
        init = _lower_expr(stmt.init, lc) if stmt.init else None
        # The parser emits TpyVarDecl for every `name = expr`; the AST codegen
        # treats a write to an already-declared name as a reassignment, not a
        # re-declaration. Mirror that here so first-decl emits `T x = ...` and a
        # reassignment emits `x = ...`.
        if stmt.name in declared:
            assert init is not None  # eligibility requires a var-decl init
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=init,
                loc=loc,
            )
        declared[stmt.name] = vtype
        return THIRVarDecl(name=stmt.name, resolved_type=vtype, init=init, loc=loc)
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyFieldAccess):
            # A borrow `T*` stored into a storage `optional<T>` field lifts
            # borrow->storage via THIRFormConvert (`ptr_to_optional`, F2b); a
            # `None` literal stores as a STORAGE-form None (`std::nullopt`, F2c).
            # The target field-access renders `recv.field` / `recv->field`.
            ftype = analyzer.get_expr_type(stmt.target)
            # A scalar field is a plain value assign -- no borrow<->storage lift.
            if _eligible_scalar(ftype):
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
        return THIRAssign(
            target=_lower_expr(stmt.target, lc),
            value=_lower_expr(stmt.value, lc),
            loc=loc,
        )
    if isinstance(stmt, TpyAugAssign):
        # `target OP= value` lowers to `target = (target OP value)`, matching the
        # AST's `_gen_aug_assign_code` scalar branch. The target expr is lowered
        # twice (once as the assign lvalue, once as the binop's left operand) --
        # the AST likewise substitutes the same target string into both slots.
        # `divisor_non_zero=False`: the AST aug-assign path never swaps the
        # checked div/mod helper (no source `TpyBinOp` node carries the flag).
        target = _lower_expr(stmt.target, lc)
        binop = THIRBinOp(
            result_type=analyzer.get_expr_type(stmt.target),
            left=_lower_expr(stmt.target, lc),
            op=stmt.op,
            right=_lower_expr(stmt.value, lc),
            resolved=stmt.resolved_binop,
            paren_wrap=False,
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
        return THIRReturn(
            value=_lower_expr(stmt.value, lc) if stmt.value else None,
            loc=loc,
        )
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
        et = unwrap_ref_type(stmt.elem_type)
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
        # Container iteration -> the begin/end loop over an in-scope container name.
        return THIRForEach(
            var=stmt.var,
            elem_type=et,
            iterable=_lower_expr(it, lc),
            body=body,
            const_loop_var=stmt.const_loop_var,
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
    arg_type = unwrap_readonly(lc.analyzer.get_expr_type(a))
    return THIRPrintArg(_lower_expr(a, lc), _print_arg_form(arg_type))


def lower_function(func: TpyFunction, analyzer, render_type=None,
                   self_type: 'TpyType | None' = None) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice.

    `render_type` (codegen's `TypeResolver.type_to_cpp`) renders F1 borrow-local
    decl types byte-identically; omit it only when no non-value local can arise
    (dump / value-scalar standalone lowering). `self_type` is the owning record's
    type when `func` is an instance method (M1): `self` is seeded as an F1-record
    receiver (a `this` pointer) so its field reads route the same as a param's."""
    if not _function_eligible(func, analyzer, self_type):
        return None
    # Branch-local hoisting is not reproduced -- a function that hoists any
    # local out of a branch stays on the AST path.
    if analyzer.function_hoisted_vars.get(id(func)):
        return None
    is_method = self_type is not None and func.is_method
    self_receiver = "self" if is_method else None
    record_name = self_type.name if is_method and isinstance(self_type, NominalType) else None
    lc = _LowerCtx(func, analyzer, render_type, self_receiver=self_receiver,
                   record_name=record_name)
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    if is_method:
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
    return THIRFunction(
        name=func.name,
        params=params,
        return_type=rt,
        body=body,
        layout=THIRFunctionLayout(),
    )


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

      * **scalar** (M3a): an eligible-scalar value (`f(value)`).
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
    routes like the record arm). Field types beyond scalar / record / Optional[record]
    (bytes / tuple / union / str / list -> F3+; cross-module / native / generic
    records) leave the ctor on the AST path."""
    analyzer = lc.analyzer
    if not (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names
            and _field_receiver_ok(stmt.target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(stmt.target)
    if _eligible_scalar(ftype):
        return _expr_eligible(stmt.value, declared, analyzer)
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
    # Own-optional: peel Own (and the inner/outer Optional) to the underlying record.
    own = unwrap_optional_own(unwrap_readonly(ptype)) if isinstance(ptype, TpyType) else None
    if own is not None:
        inner = own.wrapped
        if isinstance(inner, OptionalType):
            inner = inner.inner
        return _f1_record(inner, analyzer)
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
    return THIRConstructor(
        record_name=record.name,
        params=tuple(THIRParam(name=n, type=t) for n, t in init_method.params),
        mil_inits=tuple(_lower_ctor_mil_init(s, own_param_names, declared, lc)
                        for s in field_inits),
        base_inits=tuple(base_inits),
        body=tuple(_lower_ctor_body_stmt(s, lc, body_declared) for s in body_stmts),
    )


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
      * a **scalar** -> the lowered value [M3a];
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
    if _eligible_scalar(ftype):
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
    """Yield `(callable, self_type)` for every function / instance method the slice
    may admit -- the single feed list shared by `lower_module` and codegen so the
    two never drift. The eligibility gate still has the final say; this only
    enumerates candidates. Free functions yield `self_type=None`; instance methods
    yield the owning record's type (None-skipped for generic records). The
    constructor is excluded -- its body is emitted via the member-init-list driver
    (the M3 ctor frontier), not gen_method_def."""
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
