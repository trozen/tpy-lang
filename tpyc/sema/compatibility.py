"""
TurboPython Type Compatibility

Type compatibility checking, coercions, and lvalue analysis.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Optional

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, ListRepeatType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingBytesType, UnknownElementType,
    LiteralType,
    OwnType, ReadonlyType, VoidType, PtrType, is_readonly_ptr, TupleType,
    NominalType, TypeParamRef, NoneType, OptionalType, UnionType,
    is_protocol_type, unwrap_own, unwrap_readonly, unwrap_optional_own,
    is_any_str_type, get_covariant_params, PendingGenericInstanceType,
    CallableType, is_fn_type, RefType, unwrap_ref_type,
    is_callable_type, is_integer_type, is_any_float_type, is_readonly_span)
from ..parse import (
    TpyExpr, TpyName, TpyFieldAccess, TpySubscript, TpyArrayLiteral,
    TpyDictLiteral, TpySetLiteral, TpyListRepeat, TpyCall, TpyMethodCall, TpyUnaryOp,
    TpyBinOp, TpyCoerce, TpyNoneLiteral, TpyIntLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFunction, TpyIfExpr, TpyTupleLiteral, SourceLocation
)
from ..coercions import resolve_coercion, Coercion, CoercionContext, DEREF_COERCION, UPCAST_TO_PTR, UPCAST_TO_CONST_PTR, SPAN_METHOD_TO_SPAN_ARG, SPAN_METHOD_TO_SPAN
from ..modules import get_span_return_type
from .context import addr_taken_roots
from .numeric_lattice import numeric_info
from ..diagnostics import SemanticError, NOCOPY_REMEDIATION_HINT
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_span_iter, is_list,
    is_str_view_type, is_bytes_view_type, is_borrowing_view_type, int_traits_of,
    is_big_int_type, is_str_category, is_bytes_category, is_str_type, is_string_type,
    protocol_info_of,
)
from .overloads import type_matches_numeric


def _dangling_view_message(return_type: TpyType) -> str | None:
    """Error message for returning a view that borrows from a local, or None
    if return_type is not a borrowing-view type.
    """
    if is_str_view_type(return_type):
        return ("Cannot return StrView referencing a local or temporary; "
                "use str or String to return an owned copy")
    if is_bytes_view_type(return_type):
        return ("Cannot return BytesView referencing a local or temporary; "
                "use bytes or bytearray to return an owned copy")
    if is_span(return_type):
        return ("Cannot return Span referencing a local or temporary; "
                "use list or Array to return an owned copy")
    if is_span_iter(return_type):
        return ("Cannot return SpanIter referencing a local or temporary; "
                "the underlying Span would dangle after the function returns")
    return None


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


def _contains_semantic_ref(t: TpyType) -> bool:
    """True if t contains a RefType wrapper (explicit borrowed reference).

    Unlike _contains_ref_type which checks is_value_type() (structural),
    this checks for RefType specifically -- the semantic marker that sema
    inserts for borrowed references. Used to detect iterator-to-container
    copies where the source is an rvalue but yields borrowed elements.
    """
    if isinstance(t, RefType):
        return True
    return any(_contains_semantic_ref(inner) for inner in t.inner_types())


def _is_natural_union_member(actual: TpyType, a_info, member: TpyType) -> bool:
    """True if `member` is the natural target for `actual` in a union match,
    i.e. selecting it does not require a category-crossing widening.

    `a_info` must be `numeric_info(actual)` (passed in to avoid recomputing).
    """
    if member == actual:
        return True
    m_info = numeric_info(member)
    if a_info is None or m_info is None:
        return False
    if a_info.family == m_info.family:
        return True
    # Literal families naturally land on their concrete counterpart.
    if a_info.family == "int_literal" and m_info.family == "int":
        return True
    if a_info.family == "float_literal" and m_info.family == "float":
        return True
    return False


class CompatError:
    """Type compatibility check failure (returned by _check_compat, not raised)."""
    __slots__ = ('message', 'loc')

    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc


# Result type for _check_compat: Coercion | None (compatible) or CompatError (incompatible)
CompatResult = Coercion | CompatError | None


if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .methods import MethodAnalyzer


class TypeCompatibility:
    """Type compatibility checking, coercions, and lvalue analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # Set after construction (compat is created before these exist)
        self.type_ops: TypeOperations
        self.protocols: ProtocolChecker
        self.methods: MethodAnalyzer

    def _mark_addr_taken(self, expr: TpyExpr) -> None:
        """Mark all param roots of expr as mutated because their address is taken."""
        for r in addr_taken_roots(expr):
            self.ctx.mark_param_mutated(r)

    def is_type_compatible(self, actual: TpyType, expected: TpyType) -> bool:
        """Non-raising check: is actual assignable to expected?"""
        return not isinstance(self._check_compat(actual, expected, ""), CompatError)

    def check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: CoercionContext | None = None
    ) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Raises SemanticError on incompatible types.
        Returns a Coercion if a conversion should be applied at codegen time,
        or None if compatible with no coercion needed.
        """
        result = self._check_compat(actual, expected, context, loc, source_expr, is_return, coercion_ctx)
        if isinstance(result, CompatError):
            raise SemanticError(result.message, result.loc)
        return result

    def _resolve_recursive_refs(self, typ: TpyType) -> TpyType:
        """Resolve NominalType references to recursive union aliases.

        Handles both bare NominalType("Tree") and NominalType nested inside
        containers (e.g. list[NominalType("Tree")] -> list[UnionType(...)]).
        Treats recursive union types as opaque (does not recurse into their
        members) to prevent infinite expansion of self-referencing placeholders.
        """
        # Recursive-union alias placeholders are bare NominalType (no qname)
        # emitted by the parser; they never carry a TypeDef entry, so the
        # redefined is_user_record would return False here. Match by name
        # against recursive_union_names directly.
        if isinstance(typ, NominalType) and not typ.is_protocol:
            if typ.name in self.ctx.recursive_union_names:
                alias = self.ctx.registry.get_type_alias(typ.name)
                if alias is not None:
                    return alias
        if not self.ctx.recursive_union_names:
            return typ
        # Don't recurse into recursive union aliases -- their NominalType
        # placeholders are structural and must not be expanded.
        if isinstance(typ, UnionType) and self.ctx.is_recursive_union(typ):
            return typ
        inner = typ.inner_types()
        if not inner:
            return typ
        new_inner = tuple(self._resolve_recursive_refs(t) for t in inner)
        if all(new is old for new, old in zip(new_inner, inner)):
            return typ
        return typ.with_inner_types(new_inner)

    def _check_compat(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: CoercionContext | None = None
    ) -> CompatResult:
        """Core type compatibility check.

        Returns Coercion or None on success, CompatError on failure.
        """
        # Resolve NominalType self-references from recursive union members.
        # e.g. NominalType("Tree") -> UnionType, and also inside containers:
        # list[NominalType("Tree")] -> list[UnionType(...)].
        # Uses seen-set guard to prevent infinite recursion.
        actual = self._resolve_recursive_refs(actual)
        expected = self._resolve_recursive_refs(expected)

        # OwnType from name lookup (implicit owned local) should not
        # shortcircuit the Own[T] coercion path -- that path emits copy
        # warnings when storing at non-last-use.
        if actual == expected:
            if not (isinstance(actual, OwnType) and isinstance(source_expr, TpyName)):
                return None
        # CallableType (Fn and Callable): inner param/return types may carry
        # Own/Ref qualifiers from FI that don't affect callable contract compatibility.
        if (is_callable_type(actual)
                and is_callable_type(expected)
                and len(actual.param_types) == len(expected.param_types)):
            stripped_actual = type(actual)(
                tuple(unwrap_ref_type(unwrap_own(p)) for p in actual.param_types),
                unwrap_ref_type(unwrap_own(actual.return_type)))
            stripped_expected = type(expected)(
                tuple(unwrap_ref_type(unwrap_own(p)) for p in expected.param_types),
                unwrap_ref_type(unwrap_own(expected.return_type)))
            if stripped_actual == stripped_expected:
                return None
        # None literal (NoneType) is compatible with None annotation (VoidType)
        if isinstance(actual, NoneType) and isinstance(expected, VoidType):
            return None

        # Pending generic instance: try to resolve from the expected type
        if isinstance(actual, PendingGenericInstanceType):
            resolved = self.methods.try_resolve_pending_from_expected_type(
                actual, expected, loc)
            if resolved is not None:
                if source_expr is not None:
                    self.ctx.set_expr_type(source_expr, resolved)
                return self._check_compat(
                    resolved, expected, context, loc, source_expr,
                    is_return, coercion_ctx)
            return CompatError(
                f"Type mismatch in {context}: '{actual.record_name}' has unresolved type "
                f"arguments; call a constraining method first or add explicit type arguments",
                loc,
            )

        # Ref[T] in expected: strip Ref and check inner type.
        # Ref is a codegen annotation (reference semantics); for type
        # compatibility, T is compatible with Ref[T].
        if isinstance(expected, RefType):
            return self._check_compat(
                unwrap_ref_type(actual), expected.wrapped, context, loc,
                source_expr, is_return, coercion_ctx)
        # Strip Ref from actual too (Ref[T] is compatible with T)
        if isinstance(actual, RefType):
            return self._check_compat(
                actual.wrapped, expected, context, loc,
                source_expr, is_return, coercion_ctx)

        # readonly[T] -> readonly[T]: unwrap and check inner types
        # T -> readonly[T]: always OK (adding const is safe)
        if isinstance(expected, ReadonlyType):
            actual_inner = unwrap_readonly(actual)
            return self._check_compat(
                actual_inner, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx
            )

        # readonly[T] -> T: error for non-value types (stripping const is unsafe)
        # Exceptions: value types (copies), readonly protocols (Sized, Sequence),
        # return values (C++ const method handles safety via const propagation)
        # Note: mutable Span is NOT excepted even for returns -- you cannot
        # construct a mutable span from a const container.
        if isinstance(actual, ReadonlyType) and not isinstance(expected, ReadonlyType):
            is_mutable_span = is_span(expected) and not is_readonly_span(expected)
            if (not expected.is_value_type() or is_mutable_span) and (not is_return or is_mutable_span):
                allow = False
                if is_protocol_type(expected) and self.protocols:
                    proto_info = protocol_info_of(expected)
                    if proto_info and self.protocols.is_all_readonly(proto_info):
                        allow = True
                if not allow:
                    return CompatError(
                        f"Cannot return readonly[{actual.wrapped}] as mutable {expected}; "
                        f"use Span[readonly[T]] or annotate return type with auto_readonly[T]"
                        if is_return else
                        f"Cannot pass readonly[{actual.wrapped}] as mutable {expected} in {context}",
                        loc,
                    )
            return self._check_compat(
                actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx
            )

        # None -> Optional[T] / Ptr[T] / Ptr[readonly[T]]: always compatible
        if isinstance(actual, NoneType) and isinstance(expected, (OptionalType, PtrType)):
            return None

        # Union[A, B] -> Union[A, B, C]: each actual member must match some expected member
        if isinstance(actual, UnionType) and isinstance(expected, UnionType):
            for member in actual.members:
                result = self._check_compat(member, expected, context, loc, source_expr, is_return, coercion_ctx)
                if isinstance(result, CompatError):
                    return result
            return None

        # T -> Union[T, ...]: actual must match at least one member.
        # Iterate twice so a category-crossing widening (e.g. int -> float)
        # never wins when an in-category member exists. Without this, an int
        # going into `int | float` would silently coerce to float when float
        # happens to be earlier in the canonical member order.
        if isinstance(expected, UnionType):
            actual_unwrapped = unwrap_own(actual)
            a_info = numeric_info(actual_unwrapped)
            for member in expected.members:
                if not _is_natural_union_member(actual_unwrapped, a_info, member):
                    continue
                result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx)
                if not isinstance(result, CompatError):
                    return result
            for member in expected.members:
                if _is_natural_union_member(actual_unwrapped, a_info, member):
                    continue
                result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx)
                if not isinstance(result, CompatError):
                    return result
            return CompatError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

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
            # Optional[A] -> Optional[B] via a whole-Optional coercion rule
            # (e.g. str <-> StrView at arg position, where both lower to
            # `std::optional<std::string_view>`). Try before stripping to the
            # inner, since the inner check can't see that the wrappers share
            # a C++ representation.
            if isinstance(actual_inner, OptionalType):
                ctx_for_coerce = coercion_ctx or context
                if isinstance(ctx_for_coerce, CoercionContext):
                    whole = resolve_coercion(actual_inner, expected, ctx_for_coerce)
                    if whole is not None:
                        return whole
            result = self._check_compat(actual_inner, expected.inner, context, loc, source_expr, is_return, coercion_ctx)
            # Rewrap inner-mismatch errors with the declared Optional types so
            # the diagnostic reads `expected str | None, got StrView | None`
            # rather than the truncated `expected str, got StrView | None`.
            if isinstance(result, CompatError) and isinstance(actual, OptionalType):
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, got {actual}", loc)
            return result

        # Optional[T] -> Optional[T] already handled by == check above
        # Optional[T] -> T: error (cannot implicitly unwrap)

        # NominalType with Own[T] in type args signals copy semantics -- applies to both
        # protocols (Iterable[Own[T]]) and concrete containers (dict[K, Own[V]]).
        # Strip Own for conformance/equality check; warn when elements are implicitly copied.
        if (isinstance(expected, NominalType)
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
                # Own[T] from explicit return/param means caller acknowledged
                # ownership -- skip warning.  Own[T] from name lookup (owned
                # local) is implicit and still needs the warning.
                explicit_own = (isinstance(actual, OwnType)
                                and not isinstance(source_expr, TpyName))
                if (source_expr is not None
                        and not explicit_own
                        and not self.is_copy_call(source_expr)):
                    is_lvalue_src = self.is_lvalue(source_expr)
                    # Suppress at last use: no observable semantic divergence from
                    # CPython when the source is dead after this point (Framing A).
                    is_auto_moved = (is_lvalue_src
                                     and isinstance(source_expr, TpyName)
                                     and id(source_expr) in self.ctx.all_last_uses
                                     and self._is_owned_var(source_expr.name))
                    if not is_auto_moved:
                        for inner_t in expected.inner_types():
                            if not isinstance(inner_t, OwnType):
                                continue
                            elem_type = inner_t.wrapped
                            # Rvalue iterators that yield Ref elements (e.g.
                            # map(identity, pts)) copy on materialization.
                            # RefType in the Own-wrapped element is the proof.
                            has_ref_elements = _contains_semantic_ref(elem_type)
                            if is_lvalue_src or has_ref_elements:
                                if not _contains_ref_type(elem_type):
                                    continue
                                display_type = unwrap_ref_type(elem_type)
                                # Lvalue source (container): suggest copy_iter or copy.
                                # Rvalue with Ref elements (iterator): only copy_iter.
                                if is_lvalue_src:
                                    hint = "use copy_iter() to make this explicit (or copy() to copy the entire container)"
                                else:
                                    hint = "use copy_iter() to make this explicit"
                                if _is_definitely_ref_type(elem_type):
                                    self.ctx.warning(
                                        f"copies {display_type} elements; {hint}",
                                        source_expr,
                                    )
                                else:
                                    self.ctx.warning(
                                        f"may copy {display_type} elements if not a value type; {hint}",
                                        source_expr,
                                    )
                return None
            if is_protocol_type(expected):
                return CompatError(
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
            return CompatError(
                f"Type {check_actual} does not conform to protocol {expected} in {context}",
                loc
            )

        # Callable object -> Fn/Callable: record with __call__ matching the signature
        if isinstance(actual, NominalType) and is_callable_type(expected):
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
                    return CompatError(
                        f"'__call__' signature ({', '.join(str(p.type) for p in fi.params)}) -> {fi.return_type} "
                        f"does not match {expected} in {context}",
                        loc,
                    )

        # Callable -> Fn: std::function satisfies template requires clauses in C++
        if isinstance(actual, CallableType) and is_fn_type(expected):
            if (len(actual.param_types) == len(expected.param_types)
                    and all(self._check_compat(a, e, "param", loc) is None
                            for a, e in zip(actual.param_types, expected.param_types))
                    and (self._check_compat(actual.return_type, expected.return_type, "return", loc) is None
                         or isinstance(expected.return_type, VoidType))):
                return None

        # Inheritance: Child -> Parent (implicit value upcast, C++ handles slicing/ref binding)
        if (isinstance(actual, NominalType) and actual.is_user_record
                and isinstance(expected, NominalType) and expected.is_user_record):
            if self.ctx.registry.is_subclass_of(actual, expected):
                if coercion_ctx in (CoercionContext.ASSIGN,
                                    CoercionContext.INIT,
                                    CoercionContext.RETURN):
                    msg = (f"upcast narrows '{actual}' to '{expected}' -- "
                           f"only '{expected}' fields and methods will be accessible; "
                           f"keep the concrete type or make '{expected}' a @dynamic protocol")
                    if source_expr is not None:
                        self.ctx.warning(msg, source_expr)
                    else:
                        self.ctx.warning_from_loc(msg, loc)
                return None

        # Inheritance: Ptr[Child] -> Ptr[Parent] / Ptr[readonly[Parent]]
        # Ptr[readonly[Child]] -> Ptr[readonly[Parent]]
        if isinstance(actual, PtrType) and isinstance(actual.inner_pointee, NominalType):
            if isinstance(expected, PtrType) and isinstance(expected.inner_pointee, NominalType):
                # Mutable Ptr can coerce to both Ptr and Ptr[readonly[...]] parent;
                # Ptr[readonly[...]] can only coerce to Ptr[readonly[...]] parent
                if not actual.is_readonly or expected.is_readonly:
                    if self.ctx.registry.is_subclass_of(actual.inner_pointee, expected.inner_pointee):
                        return None

        # Covariant generic coercion: Box[Child] -> Box[Parent]
        # when Box extends Covariant[T] and Child conforms to @dynamic Parent.
        # C++ converting move ctor handles the actual conversion.
        if (isinstance(actual, NominalType) and actual.is_user_record
                and isinstance(expected, NominalType) and expected.is_user_record
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

        # Union-member coercion through generic containers:
        # Container[Lit] -> Container[Expr] when Expr is a recursive union alias
        # and Lit is a valid member. Works for any generic container.
        # The C++ Expr wrapper struct's template constructor handles implicit
        # conversion from Lit to Expr, so Box<Expr>(Lit{...}) compiles.
        if (isinstance(actual, NominalType) and isinstance(expected, NominalType)
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and len(actual.type_args) == len(expected.type_args)
                and actual.type_args != expected.type_args):
            if self._check_union_member_type_args(
                actual, expected, context, loc, source_expr, is_return, coercion_ctx
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
                is_auto_moved = self._is_owned_var(source_expr.name)
            self.check_own_consumption(source_expr)
            # Mark loop variables as consumed for auto-consuming heuristic
            if isinstance(source_expr, TpyName):
                self.ctx.mark_loop_var_consumed(source_expr.name)
            if (not is_return and source_expr is not None
                    and not expected.wrapped.is_value_type()
                    and not self._is_value_type_param(expected.wrapped)
                    and self.is_lvalue(source_expr)
                    and not self.is_copy_call(source_expr)
                    and not is_auto_moved):
                value_type = self.ctx.get_expr_type(source_expr)
                if self.ctx.is_type_non_copyable(expected.wrapped):
                    verb = "may copy" if isinstance(expected.wrapped, TypeParamRef) else "cannot copy"
                    if value_type == expected.wrapped:
                        msg = (f"{verb} non-copyable type '{expected.wrapped}' "
                               f"into owned storage{NOCOPY_REMEDIATION_HINT}")
                    else:
                        msg = (f"{verb} {value_type} into owned storage of type "
                               f"'{expected.wrapped}'; target is non-copyable{NOCOPY_REMEDIATION_HINT}")
                    raise self.ctx.error(msg, source_expr)
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
                # For loop variables at last use, the consuming decision is
                # made post-body. Record the diagnostic index so it can be
                # suppressed if the loop activates consuming iteration
                # (elements at last use will be moved, not copied).
                if (isinstance(source_expr, TpyName)
                        and source_expr.name in self.ctx.func.loop_vars
                        and id(source_expr) in self.ctx.all_last_uses):
                    diag_idx = len(self.ctx.diagnostics) - 1
                    self.ctx.func.deferred_loop_copy_warnings.setdefault(
                        source_expr.name, []).append(diag_idx)
            # Subclass coercion excluded: Child -> Own[Base] stores Child by value as
            # Base, silently slicing the object. Same invariance as container elements.
            # Only applies when record names differ (different types, not parametric covariance).
            if (isinstance(actual, NominalType) and actual.is_user_record
                    and isinstance(expected.wrapped, NominalType) and expected.wrapped.is_user_record
                    and actual.name != expected.wrapped.name):
                return CompatError(
                    f"Type mismatch in {context}: expected {expected.wrapped}, got {actual}", loc)
            return self._check_compat(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self._check_compat(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx)

        # Tuple-to-tuple: same length, element-wise compatible
        if isinstance(actual, TupleType) and isinstance(expected, TupleType):
            if len(actual.element_types) != len(expected.element_types):
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, got {actual} "
                    f"(different tuple lengths)", loc
                )
            for i, (a, e) in enumerate(zip(actual.element_types, expected.element_types)):
                result = self._check_compat(
                    a, e, f"{context} (tuple element {i})", loc,
                    source_expr=source_expr, is_return=is_return,
                    coercion_ctx=coercion_ctx
                )
                if isinstance(result, CompatError):
                    return result
            return None

        # IntLiteral can coerce to BigInt or stay unresolved
        if isinstance(actual, IntLiteralType):
            if is_big_int_type(expected) or isinstance(expected, IntLiteralType):
                return None

        # FloatLiteral can coerce to float/Float32 or stay unresolved
        if isinstance(actual, FloatLiteralType):
            if is_any_float_type(expected):
                return None

        # Allow Array element type coercion if sizes match
        if is_array(actual) and is_array(expected):
            if actual.type_args[1] == expected.type_args[1]:
                if isinstance(actual.type_args[0], IntLiteralType) and is_integer_type(expected.type_args[0]):
                    return None

        # Allow list element type coercion
        if is_list(actual) and is_list(expected):
            if isinstance(actual.type_args[0], IntLiteralType) and is_integer_type(expected.type_args[0]):
                return None

        # Allow list -> Array only for literal expressions
        # (global array literals and list repeats become list[T] but can be assigned to Array variables)
        # List *variables* cannot be coerced to Array - codegen can't handle std::vector -> std::array
        if is_list(actual) and is_array(expected):
            a_elem = actual.type_args[0]
            e_elem, e_size = expected.type_args[0], expected.type_args[1]
            if isinstance(source_expr, (TpyArrayLiteral, TpyListRepeat)):
                # Validate size for list repeats with known count
                if isinstance(source_expr, TpyListRepeat) and isinstance(source_expr.count, TpyIntLiteral):
                    repeat_size = len(source_expr.elements) * source_expr.count.value
                    if repeat_size != e_size:
                        return CompatError(
                            f"List repeat produces {repeat_size} elements but Array[..., {e_size}] expects {e_size}",
                            loc
                        )
                if a_elem == e_elem:
                    return None
                if isinstance(a_elem, IntLiteralType) and is_integer_type(e_elem):
                    return None

        # Allow PendingListType compatibility during first phase (before resolution)
        if isinstance(actual, PendingListType):
            # Compatible with list[T] if element types are compatible
            if is_list(expected):
                e_elem = expected.type_args[0]
                if actual.element_type == e_elem:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and is_integer_type(e_elem):
                    return None
                # Element type widening (e.g. Int32 -> Int32|None, Int32 -> Int64).
                # Subclass coercion excluded: storing Child in list[Base] silently
                # slices objects (same invariance as dict/set).
                both_records = (
                    isinstance(actual.element_type, NominalType) and actual.element_type.is_user_record
                    and isinstance(e_elem, NominalType) and e_elem.is_user_record
                )
                if both_records:
                    # Explicit error: avoid leaking PendingList internal repr in the generic message.
                    return CompatError(
                        f"Type mismatch in {context}: expected {e_elem}, got {actual.element_type}", loc)
                else:
                    # Element type widening (e.g. Int32 -> Int32|None, Int32 -> Int64)
                    result = self._check_compat(
                        actual.element_type, e_elem,
                        context, loc, source_expr, is_return, coercion_ctx
                    )
                    if not isinstance(result, CompatError):
                        return None  # element coercion is a probe, not propagated
            # Compatible with Array[T, N] if element types and sizes match
            if is_array(expected):
                if self.type_ops and self.type_ops.pending_list_matches_array(actual, expected):
                    return None
                # Specific error for list repeat size mismatch
                e_size = expected.type_args[1]
                if (isinstance(source_expr, TpyListRepeat)
                        and actual.size >= 0 and actual.size != e_size):
                    return CompatError(
                        f"List repeat produces {actual.size} elements but "
                        f"Array[..., {e_size}] expects {e_size}",
                        loc,
                    )
            # Inline repeat cannot be passed directly to Span -- assign to a variable first
            if is_span(expected) and isinstance(source_expr, TpyListRepeat):
                return CompatError(
                    f"Cannot pass list repeat directly to {expected}: "
                    f"assign to a variable first",
                    loc,
                )

        # ListRepeatType materializes to list[T] or Array[T, N]
        if isinstance(actual, ListRepeatType):
            if is_list(expected):
                e_elem = expected.type_args[0]
                if actual.element_type == e_elem:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and is_integer_type(e_elem):
                    return None
            if is_array(expected):
                if actual.element_type == expected.type_args[0]:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and is_integer_type(expected.type_args[0]):
                    return None

        # Allow PendingDictType compatibility during first phase (before resolution)
        if isinstance(actual, PendingDictType) and is_dict(expected):
            e_k, e_v = expected.type_args[0], expected.type_args[1]
            key_ok = isinstance(actual.key_type, UnknownElementType) or _container_elem_matches(actual.key_type, e_k)
            val_ok = isinstance(actual.value_type, UnknownElementType) or _container_elem_matches(actual.value_type, e_v)
            if key_ok and val_ok:
                return None

        # Allow PendingSetType compatibility during first phase (before resolution)
        if isinstance(actual, PendingSetType) and is_set(expected):
            if isinstance(actual.element_type, UnknownElementType) or _container_elem_matches(actual.element_type, expected.type_args[0]):
                return None

        # DictType compatibility: key and value types must match exactly.
        # Own[V] stripping is handled by _container_elem_matches.
        # Subclass coercion is intentionally excluded: dict values are stored by value,
        # and tpy::dict_update/tpy::dict_ctor are non-converting templates (invariant V).
        if is_dict(actual) and is_dict(expected):
            a_k, a_v = actual.type_args[0], actual.type_args[1]
            e_k, e_v = expected.type_args[0], expected.type_args[1]
            key_ok = _container_elem_matches(a_k, e_k)
            val_ok = _container_elem_matches(a_v, e_v)
            if key_ok and val_ok:
                return None
            # Raise a specific error pointing at the mismatching element type.
            # Strip Own[V] from the message to avoid leaking implementation details.
            check_val = e_v.wrapped if isinstance(e_v, OwnType) else e_v
            if not key_ok:
                return CompatError(
                    f"Type mismatch in {context}: expected {e_k}, got {a_k}", loc)
            return CompatError(
                f"Type mismatch in {context}: expected {check_val}, got {a_v}", loc)

        # SetType compatibility: element types must match exactly.
        # Subclass coercion is intentionally excluded: tpy::ordered_set<T> is a
        # non-converting template (invariant T).
        if is_set(actual) and is_set(expected):
            a_elem, e_elem = actual.type_args[0], expected.type_args[0]
            if _container_elem_matches(a_elem, e_elem):
                return None
            check_elem = e_elem.wrapped if isinstance(e_elem, OwnType) else e_elem
            return CompatError(
                f"Type mismatch in {context}: expected {check_elem}, got {a_elem}", loc)

        # Allow PendingStrType compatibility during first phase (before resolution)
        if isinstance(actual, PendingStrType):
            if is_any_str_type(expected):
                return None
            if isinstance(expected, LiteralType) and expected.is_str_base():
                return None
        if isinstance(expected, PendingStrType):
            if is_str_category(actual):
                return None
        # LiteralType is compatible with its base type family
        if isinstance(actual, LiteralType) and isinstance(expected, LiteralType):
            if all(v in expected.values for v in actual.values):
                return None
        if isinstance(expected, LiteralType):
            if expected.is_str_base() and is_str_category(actual):
                return None
            if expected.base_type == actual:
                return None
            if expected.is_int_base() and isinstance(actual, IntLiteralType):
                return None
        if isinstance(actual, LiteralType):
            if actual.is_str_base() and is_str_category(expected):
                return None
            if actual.base_type == expected:
                return None
            if actual.is_int_base() and is_integer_type(expected):
                return None
        # Allow PendingBytesType compatibility during first phase (before resolution)
        if isinstance(actual, PendingBytesType):
            if is_bytes_category(expected) or isinstance(expected, PendingBytesType):
                return None
        if isinstance(expected, PendingBytesType):
            if is_bytes_category(actual):
                return None

        ctx = coercion_ctx or context
        coercion = resolve_coercion(actual, expected, ctx)
        if coercion is None:
            # Inheritance: Child -> Ptr[Parent] / Ptr[readonly[Parent]] (address-of with upcast)
            if isinstance(actual, NominalType) and actual.is_user_record:
                if isinstance(expected, PtrType) and not expected.is_readonly and isinstance(expected.inner_pointee, NominalType):
                    if self.ctx.registry.is_subclass_of(actual, expected.inner_pointee):
                        coercion = UPCAST_TO_PTR
                elif is_readonly_ptr(expected) and isinstance(expected.inner_pointee, NominalType):
                    if self.ctx.registry.is_subclass_of(actual, expected.inner_pointee):
                        coercion = UPCAST_TO_CONST_PTR
        if coercion is None:
            # __span__() method coercion: type with __span__() -> Span[T] coerces to Span/Span[readonly[T]]
            if isinstance(actual, NominalType) and is_span(expected):
                span_ret = get_span_return_type(actual, registry=self.ctx.registry)
                if span_ret is not None and unwrap_readonly(span_ret.type_args[0]) == unwrap_readonly(expected.type_args[0]):
                    # Span[readonly[T]] cannot coerce to mutable Span
                    if not is_readonly_span(span_ret) or is_readonly_span(expected):
                        if ctx == CoercionContext.ARG:
                            coercion = SPAN_METHOD_TO_SPAN_ARG
                        else:
                            coercion = SPAN_METHOD_TO_SPAN
        if coercion is None:
            # Spannable[T] protocol -> Span[readonly[T]] coercion via __span__()
            if (is_protocol_type(actual) and actual.qualified_name() == "tpy.Spannable"
                    and actual.type_args and is_span(expected)
                    and is_readonly_span(expected) and actual.type_args[0] == unwrap_readonly(expected.type_args[0])):
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
                    coercion = DEREF_COERCION
        if coercion is None:
            return CompatError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

        if isinstance(actual, PendingListType) and is_span(expected):
            info = self.ctx.list_literals.get(actual.literal_id)
            if info:
                info.coerced_element_type = expected.type_args[0]
                info.passed_to_span_param = True

        if coercion.check_range and not coercion.check_range(actual, expected):
            tr = int_traits_of(expected)
            return CompatError(
                f"Integer literal {actual.value} is outside {expected} range "
                f"[{tr.min_value}, {tr.max_value}] in {context}",
                loc
            )

        if coercion.requires_mutable_lvalue:
            if source_expr is None or not self.is_mutable_lvalue(source_expr):
                return CompatError(
                    f"Cannot take mutable pointer to read-only or temporary value in {context}; "
                    f"use a read-only pointer for read-only access, or assign to a variable first",
                    loc
                )
            # Address-taking coercion (record -> Ptr[Record]) requires T& binding.
            # Track so that codegen can't safely emit const T& for this param.
            if isinstance(source_expr, TpyName):
                root = self.ctx.func.borrow_tracker.effective_storage(source_expr.name)
                self.ctx.mark_param_mutated(root)
        elif coercion.requires_lvalue:
            if source_expr is None or not self.is_lvalue(source_expr):
                return CompatError(
                    f"Cannot take address of temporary or rvalue in {context}; "
                    f"assign to a variable first",
                    loc
                )
            # Mutable Span from a lvalue container (e.g. Array -> Span[T]) requires
            # non-const source; codegen calls as_mut_span(). Mark source param as T&.
            if (is_span(expected) and not is_readonly_span(expected)
                    and source_expr is not None):
                self._mark_addr_taken(source_expr)

        if coercion.forbid_return_local and is_return:
            if source_expr is not None and self.is_dangling_return(source_expr):
                return CompatError(
                    f"Cannot return local or temporary value; "
                    f"the returned pointer/reference would dangle",
                    loc
                )

        return coercion

    def coerce_expr(
        self, expr: TpyExpr, actual: TpyType, expected: TpyType, context: str,
        coercion_ctx: CoercionContext, is_return: bool = False
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
                if is_span(unwrapped) and is_readonly_span(unwrapped):
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
        if not isinstance(expr.func, TpyName):
            return False
        if expr.func_name in self.ctx.imported_names:
            module_name, func_name = self.ctx.imported_names[expr.func_name]
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
                    and self._is_owned_var(expr.name)):
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
        bound = self.type_ops.get_type_param_bound(typ.name)
        return bound is not None and isinstance(bound, NominalType) and bound.qualified_name() == "tpy.ValueType"

    def _check_union_member_type_args(
        self, actual: NominalType, expected: NominalType,
        context: str, loc: object,
        source_expr: 'TpyExpr | None',
        is_return: bool, coercion_ctx: CoercionContext,
    ) -> bool:
        """Check Container[Lit] -> Container[Expr] union-member coercion.

        Returns True if all differing type args have expected = recursive union
        and actual is a valid member of that union.
        """
        for a_arg, e_arg in zip(actual.type_args, expected.type_args):
            if a_arg == e_arg:
                continue
            if not isinstance(a_arg, TpyType) or not isinstance(e_arg, TpyType):
                return False
            # Resolve NominalType("Expr") -> union type alias (recursive only)
            e_resolved = self._resolve_recursive_refs(e_arg) if isinstance(e_arg, NominalType) else e_arg
            if isinstance(e_resolved, UnionType) and self.ctx.is_recursive_union(e_resolved):
                result = self._check_compat(
                    a_arg, e_resolved, context, loc, None, is_return, coercion_ctx,
                )
                if isinstance(result, CompatError):
                    return False
            else:
                return False
        # Rewrite expression type so codegen emits correct template args
        # (e.g. Box<Expr> not Box<Lit>)
        if source_expr is not None:
            self.ctx.set_expr_type(source_expr, expected)
            # Also rewrite call_type on constructor calls (codegen uses this
            # for template args: Box<Expr> vs Box<Lit>)
            if isinstance(source_expr, TpyCall) and source_expr.call_type is not None:
                source_expr.call_type = expected
        return True

    def _check_covariant_args(
        self, record_info: 'RecordInfo', covariant: set[str],
        actual: NominalType, expected: NominalType
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
        if not isinstance(child, NominalType) or not isinstance(parent, NominalType):
            return False
        # @dynamic protocol: child implements parent
        if is_protocol_type(parent):
            proto_info = protocol_info_of(parent)
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
        scope = self.ctx.func.current_scope
        while scope and scope is not self.ctx.global_scope:
            if name in scope.bindings:
                return True
            scope = scope.parent
        return False

    def _is_owned_var(self, name: str) -> bool:
        """Check if a variable has owned storage (eligible for auto-move).

        Uses OwnType in scope as the primary authority.  Falls back to
        rvalue_vars for variables excluded from OwnType wrapping
        (reassigned vars, move-through vars).
        """
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            # Own[T] params
            for pname, ptype in func.params:
                if pname == name:
                    return unwrap_optional_own(unwrap_readonly(ptype)) is not None
            # Locals: check scope type.
            # Exclude for-loop vars: they get Own[T] from consuming iteration
            # element types but are not "rvalue-initialized" in the same sense
            # as constructor calls. Their movability is handled by the
            # consuming iteration system (consumed_loop_vars).
            scope_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
            if scope_type and isinstance(scope_type, OwnType) and name not in self.ctx.func.loop_vars:
                return True
            # Fallback: rvalue_vars covers move-through vars and
            # reassigned-but-all-rvalue vars not wrapped with OwnType.
            # Hoisted vars are not movable (T* pointer-locals).
            if name in self.ctx.func.rvalue_vars and name not in self.ctx.func.hoisted_vars:
                if name in self.ctx.func.current_reassigned_vars:
                    return name not in self.ctx.func.current_lvalue_reassigned
                return True
            return False
        # Top-level: non-value-type vars become pointer-globals, can't be moved
        var_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
        if var_type:
            inner = var_type.wrapped if isinstance(var_type, OwnType) else var_type
            if not inner.is_value_type():
                return False
        return True

    def _view_constructor_arg(self, expr: TpyExpr) -> TpyExpr | None:
        """If expr is a borrowing-view constructor call, return the borrowed-from arg.

        StrView / BytesView / Span[T] / SpanIter[T] all borrow from their
        first argument, so provenance/dangling predicates should recurse
        into that argument rather than treating the constructor call as
        an opaque value.
        """
        if not isinstance(expr, TpyCall) or not expr.args:
            return None
        if expr.call_type is not None and is_borrowing_view_type(expr.call_type):
            return expr.args[0]
        return None

    def _name_is_param_or_global(self, name: str) -> bool:
        """True if the name refers to caller-owned storage that outlives the call.

        Used by is_safe_to_return_expr and is_dangling_return to keep the
        self/param/non-shadowed-global checks in one place. is_param_derived_expr
        intentionally diverges by omitting the self check (self is the receiver,
        not a "parameter" for borrow-contract purposes).
        """
        if name == "self":
            return True
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, _ptype in func.params:
                if pname == name:
                    return True
        if name in self.ctx.global_scope.bindings:
            if not self._is_local_shadow(name):
                return True
        return False

    def is_param_derived_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression's root storage derives from parameters or globals."""
        if isinstance(expr, TpyCoerce):
            return self.is_param_derived_expr(expr.expr)
        # String / bytes literals live in rodata; None literal is a nullptr.
        # All three have non-local, permanent storage -- safe to borrow from.
        if isinstance(expr, (TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
            return True
        if isinstance(expr, TpyName):
            # Parameters are param-derived
            func = self.ctx.func.current_function
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
            if expr.name in self.ctx.func.param_provenance_vars:
                return True
            return False
        if isinstance(expr, TpyFieldAccess):
            return self.is_param_derived_expr(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.is_param_derived_expr(expr.obj)
        if isinstance(expr, TpyIfExpr):
            return (self.is_param_derived_expr(expr.then_expr)
                    and self.is_param_derived_expr(expr.else_expr))
        # Pointer constructors derive provenance from their argument (address-taking)
        if isinstance(expr, TpyCall) and expr.call_type is not None and expr.call_type.is_pointer() and expr.args:
            return self.is_param_derived_expr(expr.args[0])
        view_arg = self._view_constructor_arg(expr)
        if view_arg is not None:
            return self.is_param_derived_expr(view_arg)
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

    def is_safe_to_return_expr(self, expr: TpyExpr) -> bool:
        """Whether binding a local to this RHS makes the local safe to return.

        Recurses through composite forms so a per-call-site OR doesn't miss
        ``sv = p if cond else pick(q)`` (neither arm uniformly param-derived
        nor uniformly a trusted call). Must be a superset of
        is_param_derived_expr to maintain the subset invariant on which
        flow-merge intersection relies.
        """
        if isinstance(expr, TpyCoerce):
            return self.is_safe_to_return_expr(expr.expr)
        # String / bytes / None literals live in rodata / are nullptr --
        # permanent storage, safe to return. Mirrors is_param_derived_expr.
        if isinstance(expr, (TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
            return True
        if isinstance(expr, TpyName):
            return (self._name_is_param_or_global(expr.name)
                    or expr.name in self.ctx.func.safe_to_return_vars)
        if isinstance(expr, TpyFieldAccess):
            return self.is_safe_to_return_expr(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.is_safe_to_return_expr(expr.obj)
        if isinstance(expr, TpyIfExpr):
            return (self.is_safe_to_return_expr(expr.then_expr)
                    and self.is_safe_to_return_expr(expr.else_expr))
        # Pointer constructors derive safety from their argument (address-taking).
        if isinstance(expr, TpyCall) and expr.call_type is not None and expr.call_type.is_pointer() and expr.args:
            return self.is_safe_to_return_expr(expr.args[0])
        view_arg = self._view_constructor_arg(expr)
        if view_arg is not None:
            return self.is_safe_to_return_expr(view_arg)
        # Calls with return_borrows_from are not handled explicitly here:
        # is_dangling_return already recurses into the borrowed args, so a
        # call whose return borrows from a param-derived (or otherwise safe)
        # arg is non-dangling and lands in this branch as safe. If
        # is_dangling_return is ever tightened for return_borrows_from, mirror
        # the explicit arm from is_param_derived_expr to preserve the
        # superset invariant.
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            return not self.is_dangling_return(expr)
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
            if is_span(obj_type) and is_readonly_span(obj_type):
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

            view_arg = self._view_constructor_arg(expr)
            if view_arg is not None:
                return self.is_dangling_return(view_arg)

            # @value_ptr_coercion functions (e.g. take_ptr): result borrows
            # from the arg value, so dangling depends on the arg.
            fi = expr.resolved_function_info
            if fi is not None and fi.value_ptr_coercion and expr.args:
                return self.is_dangling_return(expr.args[0])

            # Generic type constructor creates a temporary
            if expr.call_type is not None:
                return True

            # Expression callees return temporaries (not dangling)
            if not isinstance(expr.func, TpyName):
                return True

            # Record constructor
            if expr.func_name in self.ctx.registry.records:
                return True

            # Function returning Own[T] creates a temporary (by-value return).
            # Only check user-defined functions (builtins don't appear in
            # registry.functions).
            if self.ctx.registry.get_function(expr.func_name) is not None:
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

            # A function returning owned str/String creates a temporary
            # std::string that dangles if returned as StrView.
            if fi is not None and (is_str_type(fi.return_type) or is_string_type(fi.return_type)):
                return True

            # Regular function call - assume it returns something safe
            # (the callee is responsible for not returning dangling refs)
            return False

        # Locals dangle unless their root or current binding is safe.
        if isinstance(expr, TpyName):
            if self._name_is_param_or_global(expr.name):
                return False
            if expr.name in self.ctx.func.safe_to_return_vars:
                return False
            return True

        # Field access - safe only if the object itself is safe
        if isinstance(expr, TpyFieldAccess):
            return self.is_dangling_return(expr.obj)

        # Subscript - safe only if the container itself is safe
        if isinstance(expr, TpySubscript):
            return self.is_dangling_return(expr.obj)

        # Method call returning owned str/String creates a temporary
        # std::string that dangles if returned as StrView.
        if isinstance(expr, TpyMethodCall):
            fi = expr.resolved_function_info
            if fi is not None and (is_str_type(fi.return_type) or is_string_type(fi.return_type)):
                return True
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
        # Value types that hold an interior pointer -- returning one that
        # borrows from a local would dangle after the function returns.
        view_msg = _dangling_view_message(return_type)
        if view_msg is not None:
            inner = expr.expr if isinstance(expr, TpyCoerce) else expr
            view_arg = self._view_constructor_arg(inner)
            if view_arg is not None:
                if self.is_dangling_return(view_arg):
                    raise self.ctx.error(view_msg, inner)
            elif self.is_dangling_return(expr):
                raise self.ctx.error(view_msg, expr)
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
