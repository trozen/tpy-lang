"""
TurboPython Type Compatibility

Type compatibility checking, coercions, and lvalue analysis.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Optional

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, BigIntType, Int32Type, ArrayType, ListType, ListRepeatType, DictType, SetType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingBytesType, UnknownElementType,
    SpanType, StrType, StringType, StrViewType, BytesType, ByteArrayType, BytesViewType, FloatType, Float32Type,
    OwnType, ReadonlyType, VoidType, PtrType, is_readonly_ptr, TupleType,
    NamedType, TypeParamRef, NoneType, OptionalType, UnionType,
    is_protocol_type, unwrap_readonly, unwrap_optional_own, local_var_is_movable,
    is_any_str_type, get_covariant_params, PendingGenericInstanceType,
    FnType, CallableType,
)
from ..parse import (
    TpyExpr, TpyName, TpyFieldAccess, TpySubscript, TpyArrayLiteral,
    TpyDictLiteral, TpySetLiteral, TpyListRepeat, TpyCall, TpyMethodCall, TpyUnaryOp,
    TpyBinOp, TpyCoerce, TpyNoneLiteral, TpyIntLiteral, TpyFunction,
    TpyIfExpr, TpyTupleLiteral, SourceLocation
)
from ..coercions import resolve_coercion, Coercion, CoercionContext, UPCAST_TO_PTR, UPCAST_TO_CONST_PTR, SPAN_METHOD_TO_SPAN_ARG, SPAN_METHOD_TO_SPAN
from .context import addr_taken_roots
from .diagnostics import SemanticError
from .overloads import type_matches_numeric


def _container_elem_matches(actual_elem: TpyType, expected_elem: TpyType) -> bool:
    """Strict element type check for container assignment.

    Allows Own[T] stripping and numeric literal coercions only.
    Subclass coercion is excluded: C++ containers are non-converting templates
    (invariant T) -- set[Child] cannot be used where set[Base] is expected.
    """
    check = expected_elem.wrapped if isinstance(expected_elem, OwnType) else expected_elem
    return actual_elem == check or type_matches_numeric(actual_elem, check)


def _contains_ref_type(t: TpyType) -> bool:
    """True if t or any nested type is or may be a reference type.

    Recurses into inner_types() so that structural types like TupleType
    (whose is_value_type() is hardcoded True) are checked element by element.
    TypeParamRef with no ValueType bound is treated as potentially a reference type.
    """
    if not t.is_value_type():
        return True
    # is_value_type() True -- recurse into inner types to catch e.g. tuple[str, Node]
    return any(_contains_ref_type(inner) for inner in t.inner_types())


def _is_definitely_ref_type(t: TpyType) -> bool:
    """True if t definitely contains a reference type (no unresolved TypeParamRefs).

    Returns False when the ref-ness depends on a TypeParamRef, in which case
    the caller should use a "may copy" warning instead of "copies".
    """
    if isinstance(t, TypeParamRef):
        return False  # unknown until instantiated
    if not t.is_value_type():
        return True
    return any(_is_definitely_ref_type(inner) for inner in t.inner_types())


if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .methods import MethodAnalyzer


class TypeCompatibility:
    """Type compatibility checking, coercions, and lvalue analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # Set via set_deps() / set_methods() to break circular dependencies
        self.type_ops: TypeOperations | None = None
        self.protocols: ProtocolChecker | None = None
        self.methods: MethodAnalyzer | None = None

    def set_deps(self, type_ops: TypeOperations, protocols: ProtocolChecker) -> None:
        """Wire deferred dependencies (must be called before use)."""
        self.type_ops = type_ops
        self.protocols = protocols

    def set_methods(self, methods: MethodAnalyzer) -> None:
        """Wire methods dependency (created after compat, wired later)."""
        self.methods = methods

    def _mark_addr_taken(self, expr: TpyExpr) -> None:
        """Mark all param roots of expr as mutated because their address is taken."""
        for r in addr_taken_roots(expr):
            self.ctx.mark_param_mutated(r)

    def is_type_compatible(self, actual: TpyType, expected: TpyType) -> bool:
        """Non-raising check: is actual assignable to expected?

        TODO: refactor check_type_compatible to separate compatibility logic
        from error reporting, so this doesn't need a try/except wrapper.
        """
        try:
            self.check_type_compatible(actual, expected, "")
            return True
        except SemanticError:
            return False

    def check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: str | None = None
    ) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Returns a Coercion if a conversion should be applied at codegen time.

        Args:
            source_expr: The expression being converted (for lvalue checks)
            is_return: True if this is a return statement (affects lifetime checks)
        """
        if actual == expected:
            return None

        # Pending generic instance: try to resolve from the expected type
        if isinstance(actual, PendingGenericInstanceType):
            if self.methods is not None:
                resolved = self.methods.try_resolve_pending_from_expected_type(
                    actual, expected, loc)
                if resolved is not None:
                    if source_expr is not None:
                        self.ctx.set_expr_type(source_expr, resolved)
                    return self.check_type_compatible(
                        resolved, expected, context, loc, source_expr,
                        is_return, coercion_ctx)
            raise SemanticError(
                f"Type mismatch in {context}: '{actual.record_name}' has unresolved type "
                f"arguments; call a constraining method first or add explicit type arguments",
                loc,
            )

        # readonly[T] -> readonly[T]: unwrap and check inner types
        # T -> readonly[T]: always OK (adding const is safe)
        if isinstance(expected, ReadonlyType):
            actual_inner = unwrap_readonly(actual)
            return self.check_type_compatible(
                actual_inner, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx
            )

        # readonly[T] -> T: error for non-value types (stripping const is unsafe)
        # Exceptions: value types (copies), readonly protocols (Sized, Sequence),
        # return values (C++ const method handles safety via const propagation)
        # Note: mutable Span is NOT excepted even for returns -- you cannot
        # construct a mutable span from a const container.
        if isinstance(actual, ReadonlyType) and not isinstance(expected, ReadonlyType):
            is_mutable_span = isinstance(expected, SpanType) and not expected.is_readonly
            if (not expected.is_value_type() or is_mutable_span) and (not is_return or is_mutable_span):
                allow = False
                if is_protocol_type(expected) and self.protocols:
                    proto_info = self.ctx.registry.get_protocol(expected.name)
                    if proto_info and self.protocols.is_all_readonly(proto_info):
                        allow = True
                if not allow:
                    raise SemanticError(
                        f"Cannot return readonly[{actual.wrapped}] as mutable {expected}; "
                        f"use Span[readonly[T]] or annotate return type with auto_readonly[T]"
                        if is_return else
                        f"Cannot pass readonly[{actual.wrapped}] as mutable {expected} in {context}",
                        loc,
                    )
            return self.check_type_compatible(
                actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx
            )

        # None -> Optional[T] / Ptr[T] / Ptr[readonly[T]]: always compatible
        if isinstance(actual, NoneType) and isinstance(expected, (OptionalType, PtrType)):
            return None

        # Union[A, B] -> Union[A, B, C]: each actual member must match some expected member
        if isinstance(actual, UnionType) and isinstance(expected, UnionType):
            for member in actual.members:
                self.check_type_compatible(member, expected, context, loc, source_expr, is_return, coercion_ctx)
            return None

        # T -> Union[T, ...]: actual must match at least one member
        if isinstance(expected, UnionType):
            for member in expected.members:
                try:
                    return self.check_type_compatible(actual, member, context, loc, source_expr, is_return, coercion_ctx)
                except SemanticError:
                    pass
            raise SemanticError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

        # T -> Optional[T]: implicit wrapping
        if isinstance(expected, OptionalType):
            if isinstance(actual, OwnType):
                actual_inner = actual.wrapped
            else:
                actual_inner = actual
            # Own[Optional[T]] -> Optional[T]: exact match after unwrap
            if actual_inner == expected:
                return None
            # Optional[Own[T]] -> Optional[T]: unwrap Own inside Optional
            if isinstance(actual_inner, OptionalType) and isinstance(actual_inner.inner, OwnType):
                actual_inner = OptionalType(actual_inner.inner.wrapped)
                if actual_inner == expected:
                    return None
            # For pointer-repr Optional[T], codegen takes &(source) when source is a
            # non-value record (not already Optional/Ptr). Mark source params as needing
            # T& so &(param) stays valid (not const T&).
            if (expected.uses_pointer_repr()
                    and source_expr is not None
                    and not actual_inner.is_value_type()
                    and not isinstance(actual_inner, (OptionalType, PtrType, NoneType))):
                self._mark_addr_taken(source_expr)
            return self.check_type_compatible(actual_inner, expected.inner, context, loc, source_expr, is_return, coercion_ctx)

        # Optional[T] -> Optional[T] already handled by == check above
        # Optional[T] -> T: error (cannot implicitly unwrap)

        # NamedType with Own[T] in type args signals copy semantics -- applies to both
        # protocols (Iterable[Own[T]]) and concrete containers (dict[K, Own[V]]).
        # Strip Own for conformance/equality check; warn when elements are implicitly copied.
        if (isinstance(expected, NamedType)
                and expected.inner_types()
                and any(isinstance(a, OwnType) for a in expected.inner_types())):
            stripped_inner = tuple(a.wrapped if isinstance(a, OwnType) else a for a in expected.inner_types())
            stripped_expected = expected.with_inner_types(stripped_inner)
            # Own[T] actual means caller acknowledged ownership transfer -- no warning.
            actual_inner = actual.wrapped if isinstance(actual, OwnType) else actual
            if is_protocol_type(expected):
                structurally_ok = bool(
                    self.protocols and self.protocols.type_conforms_to_protocol(actual_inner, stripped_expected)
                )
            else:
                # Concrete type: exact match after Own stripping
                structurally_ok = (actual_inner == stripped_expected)
            if structurally_ok:
                if (source_expr is not None
                        and not isinstance(actual, OwnType)
                        and self.is_lvalue(source_expr)
                        and not self.is_copy_call(source_expr)):
                    # Suppress at last use: no observable semantic divergence from
                    # CPython when the source is dead after this point (Framing A).
                    is_auto_moved = (isinstance(source_expr, TpyName)
                                     and id(source_expr) in self.ctx.all_last_uses
                                     and self._is_movable_var(source_expr.name))
                    if not is_auto_moved:
                        for inner_t in expected.inner_types():
                            if not isinstance(inner_t, OwnType):
                                continue
                            elem_type = inner_t.wrapped
                            if not _contains_ref_type(elem_type):
                                continue
                            if _is_definitely_ref_type(elem_type):
                                self.ctx.warning(
                                    f"copies {elem_type} elements; "
                                    f"use copy_iter() to make this explicit"
                                    f" (or copy() to copy the entire container)",
                                    source_expr,
                                )
                            else:
                                self.ctx.warning(
                                    f"may copy {elem_type} elements if not a value type; "
                                    f"use copy_iter() to make this explicit"
                                    f" (or copy() to copy the entire container)",
                                    source_expr,
                                )
                return None
            if is_protocol_type(expected):
                raise SemanticError(
                    f"Type {actual_inner} does not conform to protocol {stripped_expected} in {context}",
                    source_expr.loc if source_expr is not None else loc
                )
            # Concrete type mismatch: fall through to generic error handling

        # Protocol matching (structural subtyping)
        if is_protocol_type(expected):
            # Own[T] conforms to any protocol that T conforms to
            check_actual = actual.wrapped if isinstance(actual, OwnType) else actual
            if self.protocols and self.protocols.type_conforms_to_protocol(check_actual, expected):
                return None  # No coercion needed, structural match
            raise SemanticError(
                f"Type {check_actual} does not conform to protocol {expected} in {context}",
                loc
            )

        # Callable object -> Fn/Callable: record with __call__ matching the signature
        if isinstance(actual, NamedType) and isinstance(expected, (FnType, CallableType)):
            record = self.ctx.registry.get_record_for_type(actual)
            if record:
                overloads = self.ctx.registry.get_method_overloads_with_parents(record, "__call__")
                if overloads:
                    assert len(overloads) == 1, f"multiple __call__ overloads not supported"
                    fi = overloads[0]
                    if (len(fi.params) == len(expected.param_types)
                            and all(p.type == e for (p, e) in zip(fi.params, expected.param_types))
                            and (fi.return_type == expected.return_type
                                 or isinstance(expected.return_type, VoidType))):
                        return None
                    raise SemanticError(
                        f"'__call__' signature ({', '.join(str(p.type) for p in fi.params)}) -> {fi.return_type} "
                        f"does not match {expected} in {context}",
                        loc,
                    )

        # Inheritance: Child -> Parent (implicit value upcast, C++ handles slicing/ref binding)
        if (isinstance(actual, NamedType) and actual.is_user_record
                and isinstance(expected, NamedType) and expected.is_user_record):
            if self.ctx.registry.is_subclass_of(actual, expected):
                return None

        # Inheritance: Ptr[Child] -> Ptr[Parent] / Ptr[readonly[Parent]]
        # Ptr[readonly[Child]] -> Ptr[readonly[Parent]]
        if isinstance(actual, PtrType) and isinstance(actual.inner_pointee, NamedType):
            if isinstance(expected, PtrType) and isinstance(expected.inner_pointee, NamedType):
                # Mutable Ptr can coerce to both Ptr and Ptr[readonly[...]] parent;
                # Ptr[readonly[...]] can only coerce to Ptr[readonly[...]] parent
                if not actual.is_readonly or expected.is_readonly:
                    if self.ctx.registry.is_subclass_of(actual.inner_pointee, expected.inner_pointee):
                        return None

        # Covariant generic coercion: Box[Child] -> Box[Parent]
        # when Box extends Covariant[T] and Child conforms to @dynamic Parent.
        # C++ converting move ctor handles the actual conversion.
        if (isinstance(actual, NamedType) and actual.is_user_record
                and isinstance(expected, NamedType) and expected.is_user_record
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and actual.type_args != expected.type_args):
            record_info = self.ctx.registry.get_record(actual.name)
            if record_info and record_info.type_params:
                covariant = get_covariant_params(record_info)
                if covariant and self._check_covariant_args(
                    record_info, covariant, actual, expected
                ):
                    return None

        # Allow T -> Own[T] coercion (ownership transfer)
        if isinstance(expected, OwnType):
            # Warn when lvalue is implicitly copied into owned storage
            # (returns are handled separately as errors in statements.py)
            # Skip warning when auto-move applies (last use of an owned variable).
            # Only locals and Own[T] params are movable -- regular params are borrowed.
            is_auto_moved = False
            if isinstance(source_expr, TpyName) and id(source_expr) in self.ctx.all_last_uses:
                is_auto_moved = self._is_movable_var(source_expr.name)
            self.check_own_consumption(source_expr)
            if (not is_return and source_expr is not None
                    and not expected.wrapped.is_value_type()
                    and not self._is_value_type_param(expected.wrapped)
                    and self.is_lvalue(source_expr)
                    and not self.is_copy_call(source_expr)
                    and not is_auto_moved):
                value_type = self.ctx.get_expr_type(source_expr)
                if isinstance(expected.wrapped, TypeParamRef):
                    self.ctx.warning(
                        f"may copy {value_type} into owned storage if not a value type; use copy() to make this explicit",
                        source_expr
                    )
                else:
                    self.ctx.warning(
                        f"copies {value_type} into owned storage; use copy() to make this explicit",
                        source_expr
                    )
            # Subclass coercion excluded: Child -> Own[Base] stores Child by value as
            # Base, silently slicing the object. Same invariance as container elements.
            # Only applies when record names differ (different types, not parametric covariance).
            if (isinstance(actual, NamedType) and actual.is_user_record
                    and isinstance(expected.wrapped, NamedType) and expected.wrapped.is_user_record
                    and actual.name != expected.wrapped.name):
                raise SemanticError(
                    f"Type mismatch in {context}: expected {expected.wrapped}, got {actual}", loc)
            return self.check_type_compatible(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self.check_type_compatible(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx)

        # Tuple-to-tuple: same length, element-wise compatible
        if isinstance(actual, TupleType) and isinstance(expected, TupleType):
            if len(actual.element_types) != len(expected.element_types):
                raise SemanticError(
                    f"Type mismatch in {context}: expected {expected}, got {actual} "
                    f"(different tuple lengths)", loc
                )
            for i, (a, e) in enumerate(zip(actual.element_types, expected.element_types)):
                self.check_type_compatible(
                    a, e, f"{context} (tuple element {i})", loc,
                    source_expr=source_expr, is_return=is_return,
                    coercion_ctx=coercion_ctx
                )
            return None

        # IntLiteral can coerce to BigInt or stay unresolved
        if isinstance(actual, IntLiteralType):
            if isinstance(expected, (BigIntType, IntLiteralType)):
                return None

        # FloatLiteral can coerce to float/Float32 or stay unresolved
        if isinstance(actual, FloatLiteralType):
            if isinstance(expected, (FloatType, Float32Type, FloatLiteralType)):
                return None

        # Allow Array element type coercion if sizes match
        if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
            if actual.size == expected.size:
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None

        # Allow list element type coercion
        if isinstance(actual, ListType) and isinstance(expected, ListType):
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return None

        # Allow ListType -> ArrayType only for literal expressions
        # (global array literals and list repeats become ListType but can be assigned to Array variables)
        # List *variables* cannot be coerced to Array - codegen can't handle std::vector -> std::array
        if isinstance(actual, ListType) and isinstance(expected, ArrayType):
            if isinstance(source_expr, (TpyArrayLiteral, TpyListRepeat)):
                # Validate size for list repeats with known count
                if isinstance(source_expr, TpyListRepeat) and isinstance(source_expr.count, TpyIntLiteral):
                    repeat_size = len(source_expr.elements) * source_expr.count.value
                    if repeat_size != expected.size:
                        raise SemanticError(
                            f"List repeat produces {repeat_size} elements but Array[..., {expected.size}] expects {expected.size}",
                            loc
                        )
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None

        # Allow PendingListType compatibility during first phase (before resolution)
        if isinstance(actual, PendingListType):
            # Compatible with list[T] if element types are compatible
            if isinstance(expected, ListType):
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None
                # Element type widening (e.g. Int32 -> Int32|None, Int32 -> Int64).
                # Subclass coercion excluded: storing Child in list[Base] silently
                # slices objects (same invariance as dict/set).
                both_records = (
                    isinstance(actual.element_type, NamedType) and actual.element_type.is_user_record
                    and isinstance(expected.element_type, NamedType) and expected.element_type.is_user_record
                )
                if both_records:
                    # Explicit error: avoid leaking PendingList internal repr in the generic message.
                    raise SemanticError(
                        f"Type mismatch in {context}: expected {expected.element_type}, got {actual.element_type}", loc)
                else:
                    # Element type widening (e.g. Int32 -> Int32|None, Int32 -> Int64)
                    try:
                        self.check_type_compatible(
                            actual.element_type, expected.element_type,
                            context, loc, source_expr, is_return, coercion_ctx
                        )
                        return None
                    except SemanticError:
                        pass
            # Compatible with Array[T, N] if element types and sizes match
            if isinstance(expected, ArrayType):
                if self.type_ops and self.type_ops.pending_list_matches_array(actual, expected):
                    return None
                # Specific error for list repeat size mismatch
                if (isinstance(source_expr, TpyListRepeat)
                        and actual.size >= 0 and actual.size != expected.size):
                    raise SemanticError(
                        f"List repeat produces {actual.size} elements but "
                        f"Array[..., {expected.size}] expects {expected.size}",
                        loc,
                    )
            # Inline repeat cannot be passed directly to Span -- assign to a variable first
            if isinstance(expected, SpanType) and isinstance(source_expr, TpyListRepeat):
                raise SemanticError(
                    f"Cannot pass list repeat directly to {expected}: "
                    f"assign to a variable first",
                    loc,
                )

        # ListRepeatType materializes to list[T] or Array[T, N]
        if isinstance(actual, ListRepeatType):
            if isinstance(expected, ListType):
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None
            if isinstance(expected, ArrayType):
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None

        # Allow PendingDictType compatibility during first phase (before resolution)
        if isinstance(actual, PendingDictType) and isinstance(expected, DictType):
            key_ok = isinstance(actual.key_type, UnknownElementType) or _container_elem_matches(actual.key_type, expected.key_type)
            val_ok = isinstance(actual.value_type, UnknownElementType) or _container_elem_matches(actual.value_type, expected.value_type)
            if key_ok and val_ok:
                return None

        # Allow PendingSetType compatibility during first phase (before resolution)
        if isinstance(actual, PendingSetType) and isinstance(expected, SetType):
            if isinstance(actual.element_type, UnknownElementType) or _container_elem_matches(actual.element_type, expected.element_type):
                return None

        # DictType compatibility: key and value types must match exactly.
        # Own[V] stripping is handled by _container_elem_matches.
        # Subclass coercion is intentionally excluded: dict values are stored by value,
        # and tpy::dict_update/tpy::dict_ctor are non-converting templates (invariant V).
        if isinstance(actual, DictType) and isinstance(expected, DictType):
            key_ok = _container_elem_matches(actual.key_type, expected.key_type)
            val_ok = _container_elem_matches(actual.value_type, expected.value_type)
            if key_ok and val_ok:
                return None
            # Raise a specific error pointing at the mismatching element type.
            # Strip Own[V] from the message to avoid leaking implementation details.
            check_val = expected.value_type.wrapped if isinstance(expected.value_type, OwnType) else expected.value_type
            if not key_ok:
                raise SemanticError(
                    f"Type mismatch in {context}: expected {expected.key_type}, got {actual.key_type}", loc)
            raise SemanticError(
                f"Type mismatch in {context}: expected {check_val}, got {actual.value_type}", loc)

        # SetType compatibility: element types must match exactly.
        # Subclass coercion is intentionally excluded: tpy::ordered_set<T> is a
        # non-converting template (invariant T).
        if isinstance(actual, SetType) and isinstance(expected, SetType):
            if _container_elem_matches(actual.element_type, expected.element_type):
                return None
            check_elem = expected.element_type.wrapped if isinstance(expected.element_type, OwnType) else expected.element_type
            raise SemanticError(
                f"Type mismatch in {context}: expected {check_elem}, got {actual.element_type}", loc)

        # Allow PendingStrType compatibility during first phase (before resolution)
        if isinstance(actual, PendingStrType):
            if isinstance(expected, (StrType, StringType, StrViewType, PendingStrType)):
                return None
        if isinstance(expected, PendingStrType):
            if isinstance(actual, (StrType, StringType, StrViewType)):
                return None
        # Allow PendingBytesType compatibility during first phase (before resolution)
        if isinstance(actual, PendingBytesType):
            if isinstance(expected, (BytesType, ByteArrayType, BytesViewType, PendingBytesType)):
                return None
        if isinstance(expected, PendingBytesType):
            if isinstance(actual, (BytesType, ByteArrayType, BytesViewType)):
                return None

        ctx = coercion_ctx or context
        coercion = resolve_coercion(actual, expected, ctx)
        if coercion is None:
            # Inheritance: Child -> Ptr[Parent] / Ptr[readonly[Parent]] (address-of with upcast)
            if isinstance(actual, NamedType) and actual.is_user_record:
                if isinstance(expected, PtrType) and not expected.is_readonly and isinstance(expected.inner_pointee, NamedType):
                    if self.ctx.registry.is_subclass_of(actual, expected.inner_pointee):
                        coercion = UPCAST_TO_PTR
                elif is_readonly_ptr(expected) and isinstance(expected.inner_pointee, NamedType):
                    if self.ctx.registry.is_subclass_of(actual, expected.inner_pointee):
                        coercion = UPCAST_TO_CONST_PTR
        if coercion is None:
            # __span__() method coercion: type with __span__() -> Span[T] coerces to Span/Span[readonly[T]]
            if isinstance(actual, NamedType) and isinstance(expected, SpanType):
                from tpyc.modules import get_span_return_type
                span_ret = get_span_return_type(actual, registry=self.ctx.registry)
                if span_ret is not None and span_ret.inner_element_type == expected.inner_element_type:
                    # Span[readonly[T]] cannot coerce to mutable Span
                    if not span_ret.is_readonly or expected.is_readonly:
                        if ctx == CoercionContext.ARG:
                            coercion = SPAN_METHOD_TO_SPAN_ARG
                        else:
                            coercion = SPAN_METHOD_TO_SPAN
        if coercion is None:
            # ReadOnlySpanLike[T] protocol -> Span[readonly[T]] coercion via __span__()
            if (is_protocol_type(actual) and actual.qualified_name() == "tpy.ReadOnlySpanLike"
                    and actual.type_args and isinstance(expected, SpanType)
                    and expected.is_readonly and actual.type_args[0] == expected.inner_element_type):
                if ctx == CoercionContext.ARG:
                    coercion = SPAN_METHOD_TO_SPAN_ARG
                else:
                    coercion = SPAN_METHOD_TO_SPAN
        if coercion is None:
            # Generic deref coercion: any type with __deref__() -> T coerces to T
            if self.type_ops:
                deref_target = self.type_ops.get_deref_coercion_target(actual)
                if deref_target is None and is_readonly_ptr(actual):
                    # Ptr[readonly[T]] -> T via deref is safe for returns and
                    # assignments (the value is copied); reject only in ARG
                    # context where the callee may need a mutable reference.
                    if ctx != CoercionContext.ARG:
                        deref_target = self.type_ops.get_deref_target_type(actual)
                if deref_target is not None and unwrap_readonly(deref_target) == expected:
                    from ..coercions import DEREF_COERCION
                    coercion = DEREF_COERCION
        if coercion is None:
            raise SemanticError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

        if isinstance(actual, PendingListType) and isinstance(expected, SpanType):
            info = self.ctx.list_literals.get(actual.literal_id)
            if info:
                info.coerced_element_type = expected.element_type
                info.passed_to_span_param = True

        if coercion.check_range and not coercion.check_range(actual, expected):
            raise SemanticError(
                f"Integer literal {actual.value} is outside {expected} range "
                f"[{expected.min_value}, {expected.max_value}] in {context}",
                loc
            )

        if coercion.requires_mutable_lvalue:
            if source_expr is None or not self.is_mutable_lvalue(source_expr):
                raise SemanticError(
                    f"Cannot take mutable pointer to read-only or temporary value in {context}; "
                    f"use a read-only pointer for read-only access, or assign to a variable first",
                    loc
                )
            # Address-taking coercion (record -> Ptr[Record]) requires T& binding.
            # Track so that codegen can't safely emit const T& for this param.
            if isinstance(source_expr, TpyName):
                root = self.ctx.borrow_tracker.effective_storage(source_expr.name)
                self.ctx.mark_param_mutated(root)
        elif coercion.requires_lvalue:
            if source_expr is None or not self.is_lvalue(source_expr):
                raise SemanticError(
                    f"Cannot take address of temporary or rvalue in {context}; "
                    f"assign to a variable first",
                    loc
                )
            # Mutable Span from a lvalue container (e.g. Array -> Span[T]) requires
            # non-const source; codegen calls as_mut_span(). Mark source param as T&.
            if (isinstance(expected, SpanType) and not expected.is_readonly
                    and source_expr is not None):
                self._mark_addr_taken(source_expr)

        if coercion.forbid_return_local and is_return:
            if source_expr is not None and self.is_dangling_return(source_expr):
                raise SemanticError(
                    f"Cannot return local or temporary value; "
                    f"the returned pointer/reference would dangle",
                    loc
                )

        return coercion

    def coerce_expr(
        self, expr: TpyExpr, actual: TpyType, expected: TpyType, context: str,
        coercion_ctx: str, is_return: bool = False
    ) -> TpyExpr:
        """Wrap expr in a coercion node if a conversion is needed."""
        coercion = self.check_type_compatible(
            actual, expected, context,
            getattr(expr, "loc", None),
            source_expr=expr,
            is_return=is_return,
            coercion_ctx=coercion_ctx
        )
        if coercion is None:
            return expr
        runtime_bigint = False
        if coercion.name == "int_literal_to_fixed_int":
            runtime_bigint = self.is_runtime_bigint_expr(expr)
        coerced = TpyCoerce(
            expr=expr,
            actual_type=actual,
            expected_type=expected,
            coercion=coercion,
            context_kind=coercion_ctx,
            context_msg=context,
            runtime_bigint=runtime_bigint,
            loc=expr.loc
        )
        self.ctx.set_expr_type(coerced, expected)
        return coerced

    def is_runtime_bigint_expr(self, expr: TpyExpr) -> bool:
        """Check if an IntLiteralType expression could be BigInt at runtime."""
        if isinstance(expr, TpyCoerce):
            return self.is_runtime_bigint_expr(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return self.is_runtime_bigint_expr(expr.left) or self.is_runtime_bigint_expr(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self.is_runtime_bigint_expr(expr.operand)
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            return True
        return True

    def is_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is an lvalue (can have its address taken)."""
        if isinstance(expr, TpyCoerce):
            return self.is_lvalue(expr.expr)
        # Named variables are lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on an lvalue is also an lvalue (e.g., obj.field)
        if isinstance(expr, TpyFieldAccess):
            return self.is_lvalue(expr.obj)
        # Subscript on an lvalue is also an lvalue (e.g., arr[i])
        if isinstance(expr, TpySubscript):
            return self.is_lvalue(expr.obj)
        # Everything else (calls, literals, operators) are rvalues
        return False

    def is_const_ref_source(self, expr: TpyExpr) -> bool:
        """Check if expression provides a const reference (can't be captured as T&).

        Returns True for sources that are inherently const in C++:
        - Field access / subscript through a readonly-typed object
        """
        if isinstance(expr, TpyCoerce):
            return self.is_const_ref_source(expr.expr)
        if isinstance(expr, TpySubscript):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if obj_type is not None:
                unwrapped = unwrap_readonly(obj_type)
                if isinstance(unwrapped, SpanType) and unwrapped.is_readonly:
                    return True
            return self.is_const_ref_source(expr.obj)
        if isinstance(expr, TpyFieldAccess):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if obj_type is not None and isinstance(obj_type, ReadonlyType):
                return True
            return self.is_const_ref_source(expr.obj)
        if isinstance(expr, TpyName):
            expr_type = self.ctx.get_expr_type(expr)
            if expr_type is not None and isinstance(expr_type, ReadonlyType):
                return True
        return False

    def is_copy_call(self, expr: TpyExpr) -> bool:
        """Check if expression is a copy() or copy_iter() call from the tpy module."""
        if isinstance(expr, TpyCoerce):
            return self.is_copy_call(expr.expr)
        if not isinstance(expr, TpyCall):
            return False
        # Check if this function name maps to tpy.copy or tpy.copy_iter
        # (handles aliases like "from tpy import copy as c")
        if expr.func in self.ctx.imported_names:
            module_name, func_name = self.ctx.imported_names[expr.func]
            return module_name == "tpy" and func_name in ("copy", "copy_iter")
        return False

    def check_own_consumption(self, expr: TpyExpr) -> None:
        """Mark Own[T] param as consumed if expr transfers ownership.

        Handles two patterns:
        - Auto-move: bare name at last use of a movable variable
        - copy(): explicit copy transfers ownership of the param's value
        """
        if isinstance(expr, TpyName):
            if (id(expr) in self.ctx.all_last_uses
                    and self._is_movable_var(expr.name)):
                self.ctx.mark_own_param_consumed(expr.name)
            return
        if self.is_copy_call(expr) and isinstance(expr, TpyCall) and expr.args:
            inner = expr.args[0]
            if isinstance(inner, TpyName):
                self.ctx.mark_own_param_consumed(inner.name)

    def _is_value_type_param(self, typ: TpyType) -> bool:
        """Check if a TypeParamRef has a ValueType bound in the current context."""
        if not isinstance(typ, TypeParamRef):
            return False
        if not self.type_ops:
            return False
        bound = self.type_ops.get_type_param_bound(typ.name)
        return bound is not None and isinstance(bound, NamedType) and bound.qualified_name() == "tpy.ValueType"

    def _check_covariant_args(
        self, record_info: 'RecordInfo', covariant: set[str],
        actual: NamedType, expected: NamedType
    ) -> bool:
        """Check if all type args are compatible under covariance rules."""
        for i, param_name in enumerate(record_info.type_params):
            if i >= len(actual.type_args) or i >= len(expected.type_args):
                return False
            actual_arg = actual.type_args[i]
            expected_arg = expected.type_args[i]
            if actual_arg == expected_arg:
                continue
            if param_name not in covariant:
                return False  # invariant position must match exactly
            if not isinstance(actual_arg, TpyType) or not isinstance(expected_arg, TpyType):
                return False
            # Target must be a @dynamic protocol or class parent for C++ pointer upcast
            if not self._is_covariant_target(actual_arg, expected_arg):
                return False
        return True

    def _is_covariant_target(self, child: TpyType, parent: TpyType) -> bool:
        """Check if child -> parent is valid for covariant conversion.

        Requires C++ struct inheritance: @dynamic protocol implementation
        or class inheritance.
        """
        if not isinstance(child, NamedType) or not isinstance(parent, NamedType):
            return False
        # @dynamic protocol: child implements parent
        if is_protocol_type(parent):
            proto_info = self.ctx.registry.get_protocol(parent.name)
            if proto_info and proto_info.is_dynamic:
                if self.protocols and self.protocols.type_conforms_to_protocol(child, parent):
                    return True
            return False
        # Class inheritance
        if child.is_user_record and parent.is_user_record:
            return self.ctx.registry.is_subclass_of(child, parent)
        return False

    def _is_local_shadow(self, name: str) -> bool:
        """Check if a name is bound in a local scope, shadowing a global."""
        scope = self.ctx.current_scope
        while scope and scope is not self.ctx.global_scope:
            if name in scope.bindings:
                return True
            scope = scope.parent
        return False

    def _is_movable_var(self, name: str) -> bool:
        """Check if a variable is eligible for auto-move (owned, not borrowed).

        Delegates local-variable logic to local_var_is_movable() which is
        also used by codegen (single source of truth).
        Movable: Own[T] params, rvalue-init locals (not hoisted, not lvalue-reassigned).
        NOT movable: lvalue-init locals, hoisted locals, top-level vars.
        """
        func = self.ctx.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    return unwrap_optional_own(unwrap_readonly(ptype)) is not None
            return local_var_is_movable(
                name,
                self.ctx.hoisted_vars,
                self.ctx.current_reassigned_vars,
                self.ctx.current_lvalue_reassigned,
                name in self.ctx.rvalue_vars,
            )
        # Top-level: non-value-type vars become pointer-globals, can't be moved
        var_type = self.ctx.current_scope.lookup(name) if self.ctx.current_scope else None
        if var_type and not var_type.is_value_type():
            return False
        return True

    def is_param_derived_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression's root storage derives from parameters or globals."""
        if isinstance(expr, TpyCoerce):
            return self.is_param_derived_expr(expr.expr)
        if isinstance(expr, TpyName):
            # Parameters are param-derived
            func = self.ctx.current_function
            if isinstance(func, TpyFunction):
                for pname, _ptype in func.params:
                    if pname == expr.name:
                        return True
            # Globals are param-derived (live forever), but only if
            # the name isn't shadowed by a local binding
            if expr.name in self.ctx.global_scope.bindings:
                if not self._is_local_shadow(expr.name):
                    return True
            # Variables tracked as param-derived
            if expr.name in self.ctx.param_provenance_vars:
                return True
            return False
        if isinstance(expr, TpyFieldAccess):
            return self.is_param_derived_expr(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.is_param_derived_expr(expr.obj)
        # Pointer constructors derive provenance from their argument (address-taking)
        if isinstance(expr, TpyCall) and expr.call_type is not None and expr.call_type.is_pointer() and expr.args:
            return self.is_param_derived_expr(expr.args[0])
        # Function/method calls with return_borrows_from: result is param-derived if
        # the borrowed-from argument(s) are themselves param-derived.
        # None = unanalyzed (skip); frozenset() = returns new value (loop body never
        # runs, falls through to return False below -- correct).
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            fi = expr.resolved_function_info
            if fi is not None and fi.return_borrows_from is not None:
                args = expr.args
                obj = expr.obj if isinstance(expr, TpyMethodCall) else None
                for idx in fi.return_borrows_from:
                    if idx == -1 and obj is not None:
                        if self.is_param_derived_expr(obj):
                            return True
                    elif 0 <= idx < len(args):
                        if self.is_param_derived_expr(args[idx]):
                            return True
        # Constructors, function calls, literals -- local storage
        return False

    def needs_copy_warning(self, expr: TpyExpr, target_type: TpyType) -> bool:
        """Check if assigning expr to inline storage (field) needs a copy warning.

        Returns True when the assignment silently copies in C++ but would share
        in CPython, and the programmer hasn't made intent explicit with copy().
        """
        if target_type.is_value_type():
            return False
        if self._is_value_type_param(target_type):
            return False
        if self.is_copy_call(expr):
            return False
        if self.is_lvalue(expr):
            # Skip warning when auto-move applies (last use of a movable var)
            if isinstance(expr, TpyName) and id(expr) in self.ctx.all_last_uses:
                if self._is_movable_var(expr.name):
                    return False
            return True
        # T|None function returns always alias an existing object
        if isinstance(target_type, OptionalType) and not target_type.inner.is_value_type():
            val_type = self.ctx.get_expr_type(expr)
            if isinstance(val_type, OptionalType) and not val_type.inner.is_value_type():
                return True
        # Non-value union function returns: pointer-variant -> value-variant field copies
        if isinstance(target_type, UnionType) and target_type.uses_pointer_repr():
            val_type = self.ctx.get_expr_type(expr)
            if isinstance(val_type, UnionType) and val_type.uses_pointer_repr():
                return True
        return False

    def is_mutable_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is a mutable lvalue (can get a mutable Ptr).

        This is like is_lvalue but also rejects read-only sources like Span elements.
        """
        if isinstance(expr, TpyCoerce):
            return self.is_mutable_lvalue(expr.expr)
        # Named variables are mutable lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on a mutable lvalue is also mutable
        if isinstance(expr, TpyFieldAccess):
            return self.is_mutable_lvalue(expr.obj)
        # Subscript: check if the base is a read-only type (Span[readonly[T]], str)
        if isinstance(expr, TpySubscript):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if isinstance(obj_type, SpanType) and obj_type.is_readonly:
                return False  # Span[readonly[T]] elements are read-only
            if is_any_str_type(obj_type):
                return False
            return self.is_mutable_lvalue(expr.obj)
        return False

    def is_dangling_return(self, expr: TpyExpr) -> bool:
        """Check if returning this expression would create a dangling reference."""
        if isinstance(expr, TpyCoerce):
            return self.is_dangling_return(expr.expr)
        # Array/dict literal - creates temporary
        if isinstance(expr, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            return True

        # List repeat - creates temporary
        if isinstance(expr, TpyListRepeat):
            return True

        # Constructor call - creates temporary
        if isinstance(expr, TpyCall):
            # Pointer constructors: dangling depends on the argument, not the pointer itself
            if isinstance(expr.call_type, PtrType):
                if not expr.args:
                    return False  # Ptr[T]() -> nullptr, always safe
                return self.is_dangling_return(expr.args[0])

            # @value_ptr_coercion functions (e.g. take_ptr): result borrows
            # from the arg value, so dangling depends on the arg.
            fi = expr.resolved_function_info
            if fi is not None and fi.value_ptr_coercion and expr.args:
                return self.is_dangling_return(expr.args[0])

            # Generic type constructor creates a temporary
            if expr.call_type is not None:
                return True

            # Record constructor
            if expr.func in self.ctx.registry.records:
                return True

            # Function returning Own[T] creates a temporary (by-value return).
            # Only check user-defined functions (builtins don't appear in
            # registry.functions).
            if self.ctx.registry.get_function(expr.func) is not None:
                fi = expr.resolved_function_info
                if fi and isinstance(fi.return_type, OwnType):
                    return True

            # Function whose return borrows from args (e.g. generators storing
            # non-value params as T& references): dangles if any borrowed arg dangles
            if fi is not None and fi.return_borrows_from:
                for idx in fi.return_borrows_from:
                    if 0 <= idx < len(expr.args):
                        if self.is_dangling_return(expr.args[idx]):
                            return True

            # Regular function call - assume it returns something safe
            # (the callee is responsible for not returning dangling refs)
            return False

        # Local variable (not a parameter or global) - would dangle after function returns
        if isinstance(expr, TpyName):
            # 'self' in a method is safe - refers to the receiver object
            # (its lifetime is managed by the caller)
            if expr.name == "self":
                return False

            # Check if it's a parameter (safe)
            if self.ctx.current_function:
                for pname, ptype in self.ctx.current_function.params:
                    if pname == expr.name:
                        return False  # Parameter - safe to return reference

            # Check if it's a global (safe - lives forever), but only
            # if the name isn't shadowed by a local binding
            if expr.name in self.ctx.global_scope.bindings:
                if not self._is_local_shadow(expr.name):
                    return False

            # Storage derives from parameter/global -- safe
            if expr.name in self.ctx.param_provenance_vars:
                return False

            # Local variable - dangling
            return True

        # Field access - safe only if the object itself is safe
        if isinstance(expr, TpyFieldAccess):
            return self.is_dangling_return(expr.obj)

        # Subscript - safe only if the container itself is safe
        if isinstance(expr, TpySubscript):
            return self.is_dangling_return(expr.obj)

        # Method call - assume safe (callee's responsibility)
        if isinstance(expr, TpyMethodCall):
            return False

        # Ternary - dangles if either branch dangles
        if isinstance(expr, TpyIfExpr):
            return (self.is_dangling_return(expr.then_expr)
                    or self.is_dangling_return(expr.else_expr))

        # Unary/Binary ops - might create temporaries, be conservative
        if isinstance(expr, (TpyUnaryOp, TpyBinOp)):
            return True

        # Default: assume safe
        return False

    def check_dangling_reference(self, expr: TpyExpr, return_type: TpyType, loc: SourceLocation | None) -> None:
        """Check if returning expr as a reference would be a dangling reference.

        Object types are returned by reference. Returning a local variable or
        newly constructed object would create a dangling reference.
        """
        # Only check object types (value types are returned by value)
        # OwnType returns by value (ownership transfer), so no dangling risk
        # Pointer types need dangling checks (the pointer value may point to a local)
        if isinstance(return_type, PtrType):
            if self.is_dangling_return(expr):
                raise self.ctx.error(
                    "Cannot return pointer to local or temporary value; "
                    "the returned pointer would dangle",
                    expr
                )
            return
        # StrView is a value type but holds an interior pointer -- returning
        # a StrView referencing a local would dangle after the function returns.
        if isinstance(return_type, StrViewType):
            inner = expr.expr if isinstance(expr, TpyCoerce) else expr
            # StrView(x) constructor: check the wrapped argument
            if isinstance(inner, TpyCall) and inner.args:
                if self.is_dangling_return(inner.args[0]):
                    raise self.ctx.error(
                        "Cannot return StrView referencing a local or temporary; "
                        "use str or String to return an owned copy",
                        inner
                    )
            elif self.is_dangling_return(expr):
                raise self.ctx.error(
                    "Cannot return StrView referencing a local or temporary; "
                    "use str or String to return an owned copy",
                    expr
                )
            return
        if isinstance(return_type, TupleType):
            if isinstance(expr, TpyTupleLiteral):
                for i, et in enumerate(return_type.element_types):
                    if (not et.is_value_type() and not isinstance(et, (OwnType, TypeParamRef))
                            and i < len(expr.elements)):
                        if self.is_dangling_return(expr.elements[i]):
                            raise self.ctx.error(
                                f"Cannot return local or temporary as tuple element {i}. "
                                f"Type '{et}' is returned by reference. "
                                f"Use Own[{et}] to return by value.",
                                expr.elements[i]
                            )
            return
        if return_type.is_value_type() or isinstance(return_type, (VoidType, OwnType)):
            return

        # Optional[T] for non-value T returns T* -- returning a local would dangle.
        # But `return None` is always safe (returns nullptr).
        if isinstance(return_type, OptionalType):
            if isinstance(expr, TpyNoneLiteral):
                return
            if self.is_dangling_return(expr):
                raise self.ctx.error(
                    f"Cannot return local or temporary as '{return_type}'. "
                    f"The returned pointer would dangle. "
                    f"Return a reference to parameter data, or use Own[{return_type.inner}] "
                    f"to return by value.",
                    expr
                )
            return

        # Check if the expression is safe to return as a reference
        if self.is_dangling_return(expr):
            if is_protocol_type(return_type):
                raise self.ctx.error(
                    f"Cannot return local or temporary as '{return_type}'. "
                    f"Dynamic protocol return requires a value that outlives the caller "
                    f"(parameter or global).",
                    expr
                )
            raise self.ctx.error(
                f"Cannot return local or temporary as reference. "
                f"Object type '{return_type}' is returned by reference. "
                f"Use Own[{return_type}] to return by value.",
                expr
            )
