"""
TurboPython Type Compatibility

Type compatibility checking, coercions, and lvalue analysis.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Optional

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, ListRepeatType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingBytesType, PendingViewType, UnknownElementType,
    view_family_for_type,
    LiteralType, LiteralValue, LiteralTag, FLOAT, make_list, make_dict, make_set,
    OwnType, ReadonlyType, VoidType, PtrType, is_readonly_ptr, TupleType,
    NominalType, AliasRef, RecursiveAliasInstanceType, TypeParamRef, TypeParamKind, NoneType, AnyType, OptionalType, UnionType,
    is_protocol_type, is_dyn_protocol, unwrap_own, strip_own_type_args, unwrap_readonly, unwrap_optional_own,
    contains_type_param,
    is_any_str_type, get_covariant_params, PendingGenericInstanceType,
    CallableType, is_fn_type, RefType, unwrap_ref_type,
    is_callable_type, is_integer_type, is_any_float_type, is_readonly_span,
    is_polymorphic_class_type, SendType, SyncType, unwrap_send_sync)
from .frame_traits import frame_traits_of_function
from .send_chain import why_not_send, why_not_sync, render_chain
from ..parse import (
    TpyExpr, TpyName, TpyFieldAccess, TpySubscript, TpyArrayLiteral,
    TpyDictLiteral, TpySetLiteral, TpyListRepeat, TpyCall, TpyMethodCall, TpyUnaryOp,
    TpyBinOp, TpyCoerce, TpyNoneLiteral, TpyIntLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFunction, TpyIfExpr, TpyTupleLiteral, TpyLambda, TpyNamedExpr, TpyFString,
    SourceLocation
)


def _peel_value_wrappers(expr: TpyExpr) -> TpyExpr:
    """Unwrap coercions and walrus wrappers to the value expression a
    return/yield actually hands out -- `return (t := items[0])` hands out
    the subscript read, so provenance checks must see through the binding.
    """
    while True:
        if isinstance(expr, TpyCoerce):
            expr = expr.expr
        elif isinstance(expr, TpyNamedExpr):
            expr = expr.value
        else:
            return expr
from .literal_utils import literal_value_from_expr
from ..coercions import resolve_coercion, Coercion, CoercionContext, DEREF_COERCION, UPCAST_TO_PTR, UPCAST_TO_CONST_PTR, SPAN_METHOD_TO_SPAN_ARG, SPAN_METHOD_TO_SPAN, INTO_ANY, FROM_ANY
from ..modules import get_span_return_type
from .context import addr_taken_roots, _storage_root, BorrowKind, BorrowTracker
from .numeric_lattice import fixed_int_range_contains, numeric_info
from ..diagnostics import SemanticError, NOCOPY_REMEDIATION_HINT
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_varargs, is_span_iter, is_list,
    is_str_view_type, is_bytes_view_type, is_borrowing_view_type, int_traits_of,
    is_big_int_type, is_str_category, is_bytes_category, is_str_type, is_string_type,
    is_bytes_type, is_bytearray_type,
    protocol_info_of,
)
from .overloads import type_matches_numeric


def _literal_value_from_source(
    source_expr: TpyExpr | None, expected: LiteralType,
) -> LiteralValue | None:
    """LiteralValue from a literal AST source, gated on tag-vs-base agreement."""
    lv = literal_value_from_expr(source_expr)
    if lv is None:
        return None
    if lv.tag is LiteralTag.STR and expected.is_str_base():
        return lv
    if lv.tag is LiteralTag.BOOL and expected.is_bool_base():
        return lv
    if lv.tag is LiteralTag.INT and expected.is_int_base():
        return lv
    return None


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
    if is_varargs(return_type):
        return ("Cannot return a *args view referencing a local or temporary; "
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


def _inner_compat_with_ro_widening(source_inner: TpyType, dest_inner: TpyType) -> bool:
    """True when `source_inner` is compatible with `dest_inner` at a Ptr <-> Optional
    pointer-repr boundary, allowing mutable->readonly inner widening only.

    Used for both `Ptr[T] -> T | None` (source_inner = ptr pointee, dest_inner =
    optional inner) and `T | None -> Ptr[T]` (source_inner = optional inner,
    dest_inner = ptr pointee). The two directions are complementary: in each,
    `source_inner` is on the source side and `dest_inner` is on the destination
    side, regardless of which side is the Ptr.

    - Same inner: always allowed.
    - Mutable source -> readonly destination: allowed (adding const is safe).
    - Readonly source -> mutable destination: rejected (dropping const is unsafe).
    """
    if source_inner == dest_inner:
        return True
    if isinstance(dest_inner, ReadonlyType) and not isinstance(source_inner, ReadonlyType):
        return dest_inner.wrapped == source_inner
    return False


def _ptr_readonly_compatible(actual: PtrType, expected: PtrType) -> bool:
    """True if `actual`'s readonly-ness may flow into `expected` -- a
    mutable target from a readonly source would launder away the const."""
    return expected.is_readonly or not actual.is_readonly


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
    from .local_deduction import LocalTypeDeduction


class TypeCompatibility:
    """Type compatibility checking, coercions, and lvalue analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # Set after construction (compat is created before these exist)
        self.type_ops: TypeOperations
        self.protocols: ProtocolChecker
        self.methods: MethodAnalyzer
        self.deduction: 'LocalTypeDeduction'

    def _mark_addr_taken(self, expr: TpyExpr) -> None:
        """Mark all param roots of expr as mutated because their address is taken.

        Resolves alias / element / field / ptr borrow chains via
        `effective_storage` so element-borrowing aliases (`v = items[i]`)
        trace back to their underlying param. Loop variables also propagate
        to their source iterables -- both are storage that the address-take
        could mutate via the resulting pointer.
        """
        for name in addr_taken_roots(expr):
            root = self.ctx.func.borrow_tracker.effective_storage(name)
            self.ctx.mark_param_mutated(root)
            self.ctx.mark_loop_var_mutated(root)

    def is_type_compatible(self, actual: TpyType, expected: TpyType) -> bool:
        """Non-raising check: is actual assignable to expected?"""
        return not isinstance(self._check_compat(actual, expected, ""), CompatError)

    def _check_polymorphic_slicing(
        self,
        actual: TpyType,
        expected: TpyType,
        source_expr: TpyExpr | None,
        context: str,
        loc: SourceLocation | None,
    ) -> None:
        """Reject the Phase-20 slicing shape: a borrow of a polymorphic class
        being stored into an owned slot of the same (or strict-subclass-of)
        type. Without this rejection the dynamic type is silently dropped
        when the value is copied into the slot. Recommended fix points at
        `Box[Throwable]` (or `Box[Polymorphic]` for non-exception bases).

        Allowed shapes (no slicing risk):
          - fresh-rvalue constructor call of the exact destination type
          - `None` assigned to an Optional slot
          - any non-polymorphic destination
        """
        if source_expr is None:
            return
        # Own[T] source carries move semantics -- the rvalue's dynamic type
        # transfers fully to the destination. Skip the borrow-shape check.
        if isinstance(actual, OwnType):
            return
        # Unwrap to find the owned slot's inner concrete type, if any.
        target_inner = expected
        if isinstance(target_inner, OwnType):
            target_inner = target_inner.wrapped
        if isinstance(target_inner, OptionalType):
            target_inner = target_inner.inner
        if not isinstance(target_inner, NominalType):
            return
        if not is_polymorphic_class_type(target_inner, self.ctx.registry):
            return
        # Fresh-rvalue construction (TpyCall with a class-name func). The
        # rvalue's dynamic type matches its static type at the construction
        # site, so codegen's Phase-18/19 subclass-into-Optional machinery
        # can materialize the temp at the concrete class with no slicing.
        # Covers both exact-type construction and concrete-subclass
        # construction of a polymorphic root.
        if isinstance(source_expr, TpyCall) and isinstance(source_expr.func, TpyName):
            if self.ctx.registry.get_record(source_expr.func.name) is not None:
                return
        # None into Optional -- trivially fine.
        if isinstance(source_expr, TpyNoneLiteral):
            return
        # Source is a borrow-shape: name, attribute, method call, function
        # call result. The dynamic type could differ from the static type
        # (caught exception, field of a base-class-typed slot, ...).
        is_borrow_shape = isinstance(
            source_expr,
            (TpyName, TpyFieldAccess, TpyMethodCall, TpyCall),
        )
        if not is_borrow_shape:
            return
        # The actual type must itself be a polymorphic class (or Optional[Poly])
        # for slicing to be possible. Strip qualifiers + Optional + Ref.
        src_inner = actual
        for _ in range(4):
            if isinstance(src_inner, RefType):
                src_inner = src_inner.wrapped
            elif isinstance(src_inner, OwnType):
                src_inner = src_inner.wrapped
            elif isinstance(src_inner, ReadonlyType):
                src_inner = src_inner.wrapped
            elif isinstance(src_inner, OptionalType):
                src_inner = src_inner.inner
            else:
                break
        if not isinstance(src_inner, NominalType):
            return
        if not is_polymorphic_class_type(src_inner, self.ctx.registry):
            return
        raise SemanticError(
            f"cannot store '{src_inner.name}' borrow as owned "
            f"'{target_inner.name}' in {context} -- the dynamic type may "
            f"be a subclass and would be lost (slicing). Use "
            f"`Box[Throwable]` (or `Box[{target_inner.name}]` for "
            f"non-exception roots) for owned polymorphic storage: "
            f"`slot = Box(e.clone())`.",
            loc,
        )

    def check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: CoercionContext | None = None,
        target_is_storage_form: bool = False,
    ) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Raises SemanticError on incompatible types.
        Returns a Coercion if a conversion should be applied at codegen time,
        or None if compatible with no coercion needed.
        """
        # Phase 20 Stage 5a: reject the slicing shape -- storing a borrow
        # of a polymorphic class into an owned slot of the same type would
        # silently drop the dynamic type. Fires before _check_compat so the
        # user sees a targeted "use Box[Throwable]" diagnostic rather than
        # a generic type mismatch.
        self._check_polymorphic_slicing(actual, expected, source_expr, context, loc)
        # check_type_compatible raises on failure, so all callers are
        # commit points -- safe to apply the retro-widen side effect here
        # without leaking it into overload probes (which use _check_compat
        # / is_type_compatible directly).
        if isinstance(source_expr, TpyName):
            new_actual = self.deduction.try_retro_widen_literal_arg(
                source_expr.name, actual, expected,
                getattr(source_expr, "loc", None) or loc,
            )
            if new_actual is not None:
                actual = new_actual
                self.ctx.set_expr_type(source_expr, new_actual)
            else:
                self._maybe_raise_literal_local_range(source_expr, actual, expected, context)
        result = self._check_compat(actual, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)
        if isinstance(result, CompatError):
            # Surface where a previously retro-widened local's type got
            # pinned, so the user sees why a non-default type appears in
            # the message even though they wrote `a = 0`.
            prior_loc = (self.ctx.func.retro_widened_locs.get(source_expr.name)
                         if isinstance(source_expr, TpyName) else None)
            if prior_loc is not None:
                hint = (f" ('{source_expr.name}' was promoted to '{actual}' by "
                        f"earlier use at line {prior_loc.line})")
                raise SemanticError(result.message + hint, result.loc)
            raise SemanticError(result.message, result.loc)
        return result

    def _value_frame_traits(self, source_expr: TpyExpr | None) -> tuple[bool, bool] | None:
        """(is_send, is_sync) of the concrete captured-state frame behind a
        value expression, when sema knows it: a lambda's capture list, or a
        function reference's FunctionInfo (free function / nested def).
        None when no frame fact is available (conservative caller default).
        """
        if isinstance(source_expr, TpyLambda):
            frame = source_expr.frame_type
            if frame is not None:
                return (frame.is_send(), frame.is_sync())
            return None
        if isinstance(source_expr, TpyName):
            fi = getattr(source_expr, "function_ref_info", None)
            if fi is not None:
                return frame_traits_of_function(fi)
        return None

    def _resolve_recursive_refs(self, typ: TpyType) -> TpyType:
        """Resolve AliasRef self-references to recursive union aliases.

        Handles both bare AliasRef("Tree") and AliasRef nested inside
        containers (e.g. list[AliasRef("Tree")] -> list[UnionType(...)]).
        Treats recursive union types as opaque (does not recurse into their
        members) to prevent infinite expansion of self-referencing placeholders.
        """
        if isinstance(typ, AliasRef):
            # Module-aware: resolves a cross-module recursive alias the caller
            # didn't import (so a container arg matches the alias's
            # list[Alias] / dict[_, Alias] members). See resolve_alias_ref.
            alias = self.ctx.registry.resolve_alias_ref(typ)
            if alias is not None:
                return alias
        # Generic recursive alias instances are opaque, like the non-generic
        # UnionType wrapper below -- their alternatives live behind the
        # wrapper and must not be expanded here.
        if isinstance(typ, RecursiveAliasInstanceType):
            return typ
        if not self.ctx.recursive_union_names:
            return typ
        # Don't recurse into recursive union aliases -- their AliasRef
        # placeholders are structural and must not be expanded.
        if isinstance(typ, UnionType) and typ.needs_wrapper():
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
        coercion_ctx: CoercionContext | None = None,
        target_is_storage_form: bool = False,
    ) -> CompatResult:
        """Core type compatibility check.

        Returns Coercion or None on success, CompatError on failure.

        target_is_storage_form: True when the destination is a field or
        container element (value-storage form). Used to suppress address-
        take mutation marking that only applies to borrow-form destinations
        (params/locals/returns); storage-form assignments copy the value.
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
        # Send[T] / Sync[T] marker wrappers. The wrapper has no C++
        # representation; the conversion into a marker-typed slot is the
        # assertion site (markers persist post-resolution only around
        # erased types, where the trait is a per-value property).
        if isinstance(expected, (SendType, SyncType)):
            trait = "Send" if isinstance(expected, SendType) else "Sync"
            holds = actual.is_send() if isinstance(expected, SendType) \
                else actual.is_sync()
            if not holds:
                # Erased callables answer non-Send at the type level; the
                # concrete value may still qualify via its captured-state
                # frame (lambda capture list / function-ref FunctionInfo).
                frame_traits = self._value_frame_traits(source_expr)
                if frame_traits is not None:
                    holds = frame_traits[0] if isinstance(expected, SendType) \
                        else frame_traits[1]
            if not holds:
                is_send = isinstance(expected, SendType)
                chain = why_not_send(actual) if is_send else why_not_sync(actual)
                detail = f"\n{render_chain(chain, is_send)}" if chain is not None else ""
                return CompatError(
                    f"'{actual}' is not {trait} -- cannot use it where "
                    f"'{expected}' is expected in {context}{detail}", loc)
            return self._check_compat(
                unwrap_send_sync(actual), expected.wrapped, context, loc,
                source_expr, is_return, coercion_ctx, target_is_storage_form)
        if isinstance(actual, (SendType, SyncType)):
            # Marker-typed value into an unmarked slot: the marker only adds
            # a guarantee, so it converts freely to the bare type.
            return self._check_compat(
                actual.wrapped, expected, context, loc, source_expr,
                is_return, coercion_ctx, target_is_storage_form)
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
                    is_return, coercion_ctx, target_is_storage_form)
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
                source_expr, is_return, coercion_ctx, target_is_storage_form)
        # Strip Ref from actual too (Ref[T] is compatible with T)
        if isinstance(actual, RefType):
            return self._check_compat(
                actual.wrapped, expected, context, loc,
                source_expr, is_return, coercion_ctx, target_is_storage_form)

        # readonly[T] -> readonly[T]: unwrap and check inner types
        # T -> readonly[T]: always OK (adding const is safe)
        if isinstance(expected, ReadonlyType):
            actual_inner = unwrap_readonly(actual)
            return self._check_compat(
                actual_inner, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form
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
                actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form
            )

        # None -> Optional[T] / Ptr[T] / Ptr[readonly[T]]: always compatible
        if isinstance(actual, NoneType) and isinstance(expected, (OptionalType, PtrType)):
            return None

        # T -> Any: any copyable type stores into Any. Move-only sources
        # (Own[T] / @nocopy records / containers thereof) are rejected --
        # v1 supports copyable contents only. The INTO_ANY coercion wraps
        # the source as `tpy::Any{std::any{value}, &any_ops_for<T>}` and
        # upgrades view types (StrView, BytesView) to their owning form.
        if isinstance(expected, AnyType) and not isinstance(actual, AnyType):
            actual_for_check = unwrap_ref_type(actual)
            if self.ctx.is_type_nocopy(actual_for_check):
                return CompatError(
                    f"cannot store move-only type '{actual}' in Any "
                    f"(v1 supports copyable contents only)",
                    loc,
                )
            # Storing a reference type (record/list/dict/set) into Any
            # silently copies (Principle #1: Any owns its contents).
            # That contradicts TPy's reference-type baseline -- elsewhere a
            # copy of a record/container requires explicit copy(). Suppressed
            # for rvalue sources (no other handle), copy()-wrapped sources,
            # and last-use auto-move (matches the dict.update convention).
            inner = unwrap_own(unwrap_readonly(actual_for_check))
            if (source_expr is not None
                    and self.is_lvalue(source_expr)
                    and not self.is_copy_call(source_expr)
                    and not self._is_auto_moved(source_expr)
                    and self._warns_copy_into_any(inner)):
                self.ctx.warning(
                    f"copies {inner} into Any; use copy() to make this explicit",
                    source_expr,
                )
            return INTO_ANY

        # Any -> T: runtime-checked auto-coerce. Target T must be a concrete
        # type -- Union / Optional / generic-type-param / protocol targets
        # are too ambiguous (which member to extract? what concrete T to
        # any_cast against?). Users narrow first via isinstance or call
        # typing.cast(T, x) explicitly. Codegen emits any_cast_or_panic<T_cpp>;
        # typeid mismatches panic at runtime with the documented message.
        # Protocol targets fall through to the structural-conformance path
        # (Any satisfies Hashable / Equatable / Stringable / Representable
        # at the type-system level; runtime ops may panic if the contained
        # T lacks the capability). Own[Any] / readonly[Any] targets also
        # fall through (Any -> Any is a no-op, not an extraction).
        if isinstance(actual, AnyType):
            expected_inner = expected
            if isinstance(expected_inner, OwnType):
                expected_inner = expected_inner.wrapped
            expected_inner = unwrap_readonly(expected_inner)
            if isinstance(expected_inner, AnyType):
                return None
            if isinstance(expected_inner, (UnionType, OptionalType)):
                return CompatError(
                    f"cannot auto-coerce Any to {expected} -- "
                    f"narrow first via isinstance(x, T) or typing.cast(T, x)",
                    loc,
                )
            if isinstance(expected_inner, TypeParamRef):
                return CompatError(
                    f"cannot auto-coerce Any to generic type parameter "
                    f"'{expected_inner.name}' -- typing.cast(ConcreteT, x) first",
                    loc,
                )
            if not is_protocol_type(expected_inner):
                return FROM_ANY

        # Union[A, B] -> Union[A, B, C]: each actual member must match some expected member
        if isinstance(actual, UnionType) and isinstance(expected, UnionType):
            for member in actual.members:
                result = self._check_compat(member, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)
                if isinstance(result, CompatError):
                    return result
            return None

        # T -> Union[T, ...] (and T -> a generic recursive alias wrapper, whose
        # alternatives stand in for the union members): actual must match at
        # least one member. Iterate twice so a category-crossing widening (e.g.
        # int -> float) never wins when an in-category member exists. Without
        # this, an int going into `int | float` would silently coerce to float
        # when float happens to be earlier in the canonical member order.
        union_members: 'tuple[TpyType, ...] | None' = None
        if isinstance(expected, UnionType):
            union_members = expected.members
        elif isinstance(expected, RecursiveAliasInstanceType):
            union_members = expected.alternatives()
        if union_members is not None:
            actual_unwrapped = unwrap_own(actual)
            a_info = numeric_info(actual_unwrapped)
            for member in union_members:
                if not _is_natural_union_member(actual_unwrapped, a_info, member):
                    continue
                result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)
                if not isinstance(result, CompatError):
                    return result
            for member in union_members:
                if _is_natural_union_member(actual_unwrapped, a_info, member):
                    continue
                result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)
                if not isinstance(result, CompatError):
                    return result
            # A concrete container variable whose elements would each fit a union
            # member is NOT implicitly converted: an element-wise rebuild into the
            # wrapper representation is a hidden O(n) deep copy, which TPy declines
            # to insert silently (an aliasing reference type would also diverge
            # from CPython). Point at the explicit alternatives rather than the
            # bare type-mismatch. (Container literals are unaffected -- they
            # materialize into the wrapper directly.)
            if self.deep_convertible_to_union(actual_unwrapped, expected):
                return CompatError(
                    f"Type mismatch in {context}: a concrete container ({actual}) "
                    f"is not implicitly converted into the recursive-union type "
                    f"{expected} (it would be a hidden element-wise deep copy). "
                    f"Build it as the alias directly (`x: {expected} = {{...}}`) or "
                    f"pass a container literal.", loc)
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
            # T& so &(param) stays valid (not const T&). Skip when the destination
            # is in storage form (field / container element) -- the Optional there
            # lowers to std::optional<T> (value-storage) and the assignment is a
            # copy; no address-take happens, so the source's const-ness is fine.
            if (expected.uses_pointer_repr()
                    and source_expr is not None
                    and not actual_inner.is_value_type()
                    and not isinstance(actual_inner, (OptionalType, PtrType, NoneType))
                    and not target_is_storage_form):
                self._mark_addr_taken(source_expr)
            # Ptr[T] -> T | None (pointer-repr): byte-identical T* at C++ level.
            # Per-context idiom: Ptr[T] for storage, T | None for nullable returns.
            if (isinstance(actual_inner, PtrType)
                    and expected.uses_pointer_repr()
                    and _inner_compat_with_ro_widening(actual_inner.pointee, expected.inner)):
                return None
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
            # Optional[T] -> Optional[Own[T]]: ownership transfer wrapped in
            # Optional (e.g. returning an owned `Foo | None` local as
            # `Own[Foo] | None`). Compare the INNERS so the bare T -> Own[T]
            # coercion applies its ownership semantics (move at last use,
            # copy-warn / @nocopy-reject otherwise) to the optional source.
            # (`Own[Foo | None]` -- OwnType outer -- is handled by the OwnType
            # branch below, which matches the whole Optional after unwrap.)
            if (isinstance(actual_inner, OptionalType)
                    and isinstance(expected.inner, OwnType)
                    and not isinstance(actual_inner.inner, OwnType)):
                inner_result = self._check_compat(
                    actual_inner.inner, expected.inner, context, loc,
                    source_expr, is_return, coercion_ctx, target_is_storage_form)
                if inner_result is None:
                    return None
            result = self._check_compat(actual_inner, expected.inner, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)
            # Rewrap inner-mismatch errors with the declared Optional types so
            # the diagnostic reads `expected str | None, got StrView | None`
            # rather than the truncated `expected str, got StrView | None`.
            if isinstance(result, CompatError) and isinstance(actual, OptionalType):
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, got {actual}", loc)
            return result

        # Optional[T] -> Optional[T] already handled by == check above
        # Optional[T] -> T: error (cannot implicitly unwrap)

        # T | None (pointer-repr) -> Ptr[T]: byte-identical T* at C++ level.
        # Per-context idiom: T | None for nullable returns, Ptr[T] for storage.
        # Storage-form Optional sources (field access, container element) need
        # an `optional_to_ptr` lift at codegen, mirroring the existing
        # storage-form-to-borrow-form lift for OptionalType destinations.
        if isinstance(expected, PtrType):
            actual_for_opt = unwrap_own(actual)
            if (isinstance(actual_for_opt, OptionalType)
                    and actual_for_opt.uses_pointer_repr()
                    and _inner_compat_with_ro_widening(actual_for_opt.inner, expected.pointee)):
                return None

        # NominalType with Own[T] in type args signals copy semantics -- applies to both
        # protocols (Iterable[Own[T]]) and concrete containers (dict[K, Own[V]]).
        # Strip Own for conformance/equality check; warn when elements are implicitly copied.
        if (isinstance(expected, NominalType)
                and expected.inner_types()
                and any(isinstance(a, OwnType) for a in expected.inner_types())):
            stripped_expected = strip_own_type_args(expected)
            # Own[T] actual means caller acknowledged ownership transfer -- no warning.
            actual_inner = unwrap_own(actual)
            # Symmetric with the expected-side strip: an actual that itself
            # carries Own in its type args (e.g. Iterator[Own[T]]) already
            # yields owned elements, so strip those too -- otherwise it would be
            # compared element-wise (Own[T]) against the bare-T stripped
            # expected and wrongly fail. Provides-Own means no implicit copy.
            actual_provides_own = (
                isinstance(actual_inner, NominalType)
                and bool(actual_inner.inner_types())
                and any(isinstance(a, OwnType) for a in actual_inner.inner_types())
            )
            if actual_provides_own:
                actual_inner = strip_own_type_args(actual_inner)
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
                        and not actual_provides_own
                        and not self.is_copy_call(source_expr)):
                    is_lvalue_src = self.is_lvalue(source_expr)
                    # Suppress at last use: no observable semantic divergence from
                    # CPython when the source is dead after this point (Framing A).
                    is_auto_moved = self._is_auto_moved(source_expr)
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

        # Callable object -> Fn/Callable: record with a __call__ overload
        # satisfying the expected signature (first declared match wins).
        # Plain signature-to-signature matching, not resolve_overload --
        # that machinery scores call-site ARG types; here two signatures
        # are compared, with the same variance rule as Callable -> Fn.
        if isinstance(actual, NominalType) and is_callable_type(expected):
            record = self.ctx.registry.get_record_for_type(actual)
            if record:
                overloads = self.ctx.registry.get_method_overloads_with_parents(record, "__call__")
                if overloads:
                    for fi in overloads:
                        if (len(fi.params) == len(expected.param_types)
                                and self._callable_signature_satisfies(
                                    tuple(p.type for p in fi.params), fi.return_type,
                                    expected, loc)):
                            return None
                    candidates = "; ".join(
                        f"({', '.join(str(p.type) for p in fi.params)}) -> {fi.return_type}"
                        for fi in overloads)
                    return CompatError(
                        f"no '__call__' overload of '{actual}' matches {expected} "
                        f"in {context} (candidates: {candidates})",
                        loc,
                    )

        # Callable -> Fn: std::function satisfies template requires clauses in C++
        if isinstance(actual, CallableType) and is_fn_type(expected):
            if (len(actual.param_types) == len(expected.param_types)
                    and self._callable_signature_satisfies(
                        actual.param_types, actual.return_type, expected, loc)):
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
        # Covers both class-to-class inheritance and `@dynamic` protocol
        # implementation; C++ upcasts the pointer implicitly at the call
        # site via virtual inheritance.
        if isinstance(actual, PtrType) and isinstance(actual.inner_pointee, NominalType):
            if isinstance(expected, PtrType) and isinstance(expected.inner_pointee, NominalType):
                if _ptr_readonly_compatible(actual, expected):
                    if self._is_covariant_target(actual.inner_pointee, expected.inner_pointee):
                        return None

        # Ptr[U] -> Ptr[B] where U is a type param whose subtype bound reaches
        # B (`def f[U: B]`). Borrow-form pointer upcast only; the C++ upcast is
        # only valid when the concrete U is a real subtype of B, which the call
        # site enforces (see methods/calls bound validation).
        if (isinstance(actual, PtrType) and isinstance(expected, PtrType)
                and isinstance(actual.inner_pointee, TypeParamRef)
                and _ptr_readonly_compatible(actual, expected)
                and self._is_representational_subtype(
                    actual.inner_pointee, expected.inner_pointee)):
            return None

        # Covariant generic coercion: Box[Child] -> Box[Parent]
        # when Box extends Covariant[T] and Child conforms to @dynamic Parent.
        # C++ converting move ctor handles the actual conversion.
        if self.is_covariant_generic_upcast(actual, expected):
            return None

        # A concrete std container (list/dict/set) whose element type differs
        # from a recursive-union-alias element cannot be converted element-wise:
        # std::vector / tpy::ordered_map have no element-converting constructor
        # (unlike the single-element Box/Rc wrappers handled above, whose
        # template ctor does the conversion). Accepting it would emit an
        # ill-formed deep conversion (`ordered_map<string,int> -> JsonValue`),
        # so reject with a clean diagnostic. A literal takes the
        # PendingList/PendingDict branches below (materialized element-wise) and
        # is unaffected. Proper element-wise container->wrapper conversion is
        # tracked in BUGS.md.
        if ((is_list(actual) or is_dict(actual) or is_set(actual))
                and isinstance(expected, NominalType)
                and isinstance(actual, NominalType)
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and actual.type_args != expected.type_args):
            for a_arg, e_arg in zip(actual.type_args, expected.type_args):
                if a_arg == e_arg or not isinstance(e_arg, TpyType) or not isinstance(a_arg, TpyType):
                    continue
                e_res = (self._resolve_recursive_refs(e_arg)
                         if isinstance(e_arg, (NominalType, AliasRef)) else e_arg)
                a_res = (self._resolve_recursive_refs(a_arg)
                         if isinstance(a_arg, (NominalType, AliasRef)) else a_arg)
                # Only a *genuine* element difference needs a deep conversion;
                # an AliasRef-vs-resolved-union representation difference (the
                # element is already the union) resolves to equal and is fine.
                if (a_res != e_res and isinstance(e_res, UnionType)
                        and e_res.needs_wrapper()):
                    return CompatError(
                        f"Type mismatch in {context}: cannot convert {actual} "
                        f"element-wise into the expected recursive-union type. "
                        f"Annotate the value with the alias type "
                        f"(e.g. `x: JsonValue = ...`) or pass a literal.", loc)

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
            is_auto_moved = self._is_auto_moved(source_expr)
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
            return self._check_compat(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self._check_compat(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form)

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
                    coercion_ctx=coercion_ctx, target_is_storage_form=target_is_storage_form
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
                # The variable's binding may have been reverted to Unknown by
                # loop_scope even though an in-loop .append() recorded the
                # element on list_literals. Consult that canonical fact so an
                # in-loop-pinned list is validated against the use site exactly
                # as a straight-line one is; defer to resolve_all only when the
                # element is genuinely still unknown (which resolve_all errors on).
                actual_elem = actual.element_type
                if isinstance(actual_elem, UnknownElementType):
                    canon = self.ctx.list_literals.get(actual.literal_id)
                    if canon is not None and not isinstance(canon.element_type, UnknownElementType):
                        actual_elem = canon.element_type
                    else:
                        return None
                if actual_elem == e_elem:
                    return None
                if isinstance(actual_elem, IntLiteralType) and is_integer_type(e_elem):
                    return None
                # Element type widening (e.g. Int32 -> Int32|None, Int32 -> Int64).
                # Subclass coercion excluded: storing Child in list[Base] silently
                # slices objects (same invariance as dict/set).
                both_records = (
                    isinstance(actual_elem, NominalType) and actual_elem.is_user_record
                    and isinstance(e_elem, NominalType) and e_elem.is_user_record
                )
                if both_records:
                    # Explicit error: avoid leaking PendingList internal repr in the generic message.
                    return CompatError(
                        f"Type mismatch in {context}: expected {e_elem}, got {actual_elem}", loc)
                else:
                    # Element type widening (e.g. Int32 -> Int32|None, Int32 -> Int64).
                    # Container element slot is storage form -- no address-take
                    # mark should fire even if the inner type is pointer-repr Optional.
                    result = self._check_compat(
                        actual_elem, e_elem,
                        context, loc, source_expr, is_return, coercion_ctx,
                        target_is_storage_form=True,
                    )
                    if not isinstance(result, CompatError):
                        return None  # element coercion is a probe, not propagated
                    # Definite mismatch: report it cleanly (see both_records above).
                    return CompatError(
                        f"Type mismatch in {context}: expected {e_elem}, got {actual_elem}", loc)
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

        # Allow PendingDictType compatibility during first phase (before resolution).
        # Consult dict_literals for a key/value the binding lost to a loop_scope
        # revert (see the PendingList branch above); defer only when genuinely
        # unknown, else report the mismatch cleanly here.
        if isinstance(actual, PendingDictType) and is_dict(expected):
            e_k, e_v = expected.type_args[0], expected.type_args[1]
            canon = self.ctx.dict_literals.get(actual.literal_id)
            a_k = actual.key_type
            if isinstance(a_k, UnknownElementType) and canon is not None:
                a_k = canon.key_type
            a_v = actual.value_type
            if isinstance(a_v, UnknownElementType) and canon is not None:
                a_v = canon.value_type
            key_ok = isinstance(a_k, UnknownElementType) or _container_elem_matches(a_k, e_k)
            val_ok = isinstance(a_v, UnknownElementType) or _container_elem_matches(a_v, e_v)
            if key_ok and val_ok:
                return None
            return CompatError(
                f"Type mismatch in {context}: expected {expected}, got dict[{a_k}, {a_v}]", loc)

        # Allow PendingSetType compatibility during first phase (before resolution).
        if isinstance(actual, PendingSetType) and is_set(expected):
            canon = self.ctx.set_literals.get(actual.literal_id)
            a_e = actual.element_type
            if isinstance(a_e, UnknownElementType) and canon is not None:
                a_e = canon.element_type
            if isinstance(a_e, UnknownElementType) or _container_elem_matches(a_e, expected.type_args[0]):
                return None
            return CompatError(
                f"Type mismatch in {context}: expected {expected}, got set[{a_e}]", loc)

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
            # Reject explicitly: skipping the permissive str/int branches below
            # is what enforces value-set membership.
            return CompatError(
                f"Type mismatch in {context}: expected {expected}, got {actual}", loc)
        if isinstance(expected, LiteralType):
            src_lit = _literal_value_from_source(source_expr, expected)
            if src_lit is not None:
                if src_lit in expected.values:
                    return None
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, "
                    f"got {src_lit}", loc)
            if isinstance(actual, IntLiteralType) and actual.value is not None and expected.is_int_base():
                if expected.contains(LiteralTag.INT, actual.value):
                    return None
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, "
                    f"got {actual.value}", loc)
            # Non-literal source flowing into a Literal[...] LHS is rejected:
            # sema cannot prove the runtime value is in the declared set, and
            # the LHS's narrower type drives downstream Literal-specialized
            # dispatch (which would run the wrong arm on an out-of-set value).
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
            # Inheritance: Child -> Ptr[Parent] / Ptr[readonly[Parent]] (address-of with upcast).
            # `_is_covariant_target` covers both class inheritance (Child -> Ptr[ParentRecord])
            # and @dynamic-protocol implementation (Child -> Ptr[@dynamic Protocol]).
            #
            # Also accepts `actual` being a @dynamic protocol value (lowered as
            # `Base&` already): `&handle` produces `Base*` natively, matching
            # `Ptr[same protocol]`. Identity is the common case (`P -> Ptr[P]`
            # inside a function that took the protocol value as a param);
            # protocol-to-parent-protocol upcasts also work via the existing
            # covariance check.
            actual_is_addr_taker = (
                isinstance(actual, NominalType)
                and (actual.is_user_record or is_dyn_protocol(actual))
            )
            if actual_is_addr_taker:
                # Identity shortcut (actual == inner_pointee) only fires for
                # @dynamic protocol values: their `Base&`-style lowering needs
                # the value->Ptr path without going through resolve_coercion
                # (no `protocol_to_ptr` rule exists). User-record identity is
                # handled by the named `record_to_ptr` rule in resolve_coercion;
                # gating the shortcut keeps the coercion AST name stable so
                # downstream code can rely on it.
                actual_is_dyn = is_dyn_protocol(actual)
                if isinstance(expected, PtrType) and not expected.is_readonly and isinstance(expected.inner_pointee, NominalType):
                    if ((actual_is_dyn and actual == expected.inner_pointee)
                            or self._is_covariant_target(actual, expected.inner_pointee)):
                        coercion = UPCAST_TO_PTR
                elif is_readonly_ptr(expected) and isinstance(expected.inner_pointee, NominalType):
                    if ((actual_is_dyn and actual == expected.inner_pointee)
                            or self._is_covariant_target(actual, expected.inner_pointee)):
                        coercion = UPCAST_TO_CONST_PTR
        if coercion is None:
            # Address-of a reference-bound generic param: `&fp` yields `W*` =
            # `Ptr[W]`. Identity only (same type param), mirroring the @dynamic
            # value->Ptr shortcut above. A concrete user-record param already
            # takes this path via `record_to_ptr`; this extends it to the
            # abstract `W` so a generic holder can store `Ptr[W]` of its param.
            # An INT type param is a std::size_t value, not addressable here.
            # A value-bounded `W` is passed `const W&`, so `&fp` is `const W*`:
            # only the readonly Ptr is sound; a mutable `Ptr[W]` falls through
            # to the type-mismatch error rather than emitting `const W* -> W*`.
            if (isinstance(actual, TypeParamRef) and actual.kind != TypeParamKind.INT
                    and isinstance(expected, PtrType) and expected.inner_pointee == actual):
                if expected.is_readonly:
                    coercion = UPCAST_TO_CONST_PTR
                elif not actual.is_value_type():
                    coercion = UPCAST_TO_PTR
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
                    f"Cannot take address of a temporary or expression in {context}; "
                    f"assign to a variable first",
                    loc
                )
            # Mutable Span from a lvalue container (e.g. Array -> Span[T])
            # requires non-const source; codegen calls as_mut_span().
            # Mark source param as T&. Unlike the pointer-repr Optional
            # storage-form gate above, this mark applies even for
            # storage-form Span destinations -- the assignment still
            # emits as_mut_span(source), which can't bind to a const
            # source.
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
        coercion_ctx: CoercionContext, is_return: bool = False,
        target_is_storage_form: bool = False,
    ) -> TpyExpr:
        """Wrap expr in a coercion node if a conversion is needed.

        target_is_storage_form: see _check_compat docstring. Set by callers
        that know the destination is a field / container element so the
        pointer-repr-Optional address-take mark is suppressed.
        """
        coercion = self.check_type_compatible(
            actual, expected, context,
            getattr(expr, "loc", None),
            source_expr=expr,
            is_return=is_return,
            coercion_ctx=coercion_ctx,
            target_is_storage_form=target_is_storage_form,
        )
        if coercion is None:
            return expr
        # Value-to-mutable-Ptr coercions (`record_to_ptr`, `upcast_to_ptr`)
        # emit `&expr` and require the source storage to stay mutable -- same
        # propagation the explicit `take_ptr(x)` path gets via the
        # `value_ptr_coercion` handler in calls.py and the vararg pack handler.
        if coercion.name in ("record_to_ptr", "upcast_to_ptr"):
            self._mark_addr_taken(expr)
        runtime_bigint = False
        if coercion.name == "int_literal_to_fixed_int":
            runtime_bigint = self.is_runtime_bigint_expr(expr)
        # INTO_ANY needs a concrete actual_type for codegen (the storage
        # typeid). PendingListType / PendingDictType / PendingSetType
        # would normally resolve later via coerced_element_type, but that
        # signal is set by typed targets -- Any provides no element
        # constraint. Default-resolve here so the cell's typeid is stable.
        if coercion.name == "into_any":
            actual = self._resolve_pending_for_any_storage(actual)
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

    def coerce_reassignment(
        self, name: str, existing_type: TpyType, value_type: TpyType,
        value_expr: TpyExpr, err_node,
    ) -> tuple[TpyType, TpyExpr]:
        """Single entry point for local-reassignment compatibility, so the
        per-shape rules can't drift between the var-decl and walrus paths.

        Returns (resolved_target_type, coerced_value_expr); raises on an
        incompatible RHS. Callers keep their own var_types / declared-type
        sync and storage bookkeeping.
        """
        inner_existing = unwrap_readonly(existing_type)
        inner_value = unwrap_readonly(value_type)
        ctx = f"reassignment to '{name}'"

        if isinstance(inner_existing, PendingGenericInstanceType):
            raise self.ctx.error(
                f"Cannot reassign '{name}' while its generic type is still "
                f"being inferred; add explicit type arguments to the constructor",
                err_node)

        # PendingList: track size (list-vs-array decision) and check element
        # compatibility -- coerce_expr can't model pending-list-vs-pending-list,
        # so the element check lives here.
        if isinstance(inner_existing, PendingListType):
            if isinstance(inner_value, PendingListType):
                if inner_existing.size != inner_value.size:
                    self.deduction.mark_list_different_size(inner_existing.literal_id)
                    self.deduction.mark_list_different_size(inner_value.literal_id)
                else:
                    self.deduction.link_list_literals(
                        inner_existing.literal_id, inner_value.literal_id)
            err = self._reassign_list_element_compat(
                inner_existing, inner_value, value_expr, ctx)
            if err is not None:
                raise self.ctx.error(err.message, err_node)
            return existing_type, value_expr

        # str/bytes view family: track owned-vs-borrow provenance (so an owned
        # source promotes the local to owned storage) before coercing.
        if (isinstance(inner_existing, (PendingViewType, LiteralType))
                and (vf := view_family_for_type(inner_existing)) is not None):
            if vf.is_any_member(inner_value):
                if not self.deduction.is_view_compatible_source(value_expr, inner_value):
                    self.deduction.mark_view_reassigned_from_owned(name, vf)
                else:
                    self.deduction.track_view_reassign_source(name, inner_value, vf)
            coerced = self.coerce_expr(
                value_expr, inner_value, inner_existing, ctx,
                coercion_ctx=CoercionContext.ASSIGN)
            return existing_type, coerced

        # Non-pending: resolve the target type, then either upgrade an
        # IntLiteral-seeded local (no coerce -- caller syncs var_types) or
        # coerce the RHS against the RESOLVED type, not the annotation: a borrow
        # rebind of a per-element-Own tuple local must not warn a copy into
        # owned storage.
        var_type = self.deduction.resolve_reassignment_target_type(
            name, inner_existing, inner_value, init_expr=value_expr)
        coerced = value_expr
        if not (isinstance(inner_existing, IntLiteralType) and is_integer_type(var_type)):
            coerced = self.coerce_expr(
                value_expr, inner_value, var_type, ctx,
                coercion_ctx=CoercionContext.ASSIGN)
        # Readonly status flows from the value expression.
        if isinstance(value_type, ReadonlyType) and not var_type.is_value_type():
            if isinstance(var_type, OptionalType):
                var_type = OptionalType(ReadonlyType(var_type.inner))
            else:
                var_type = ReadonlyType(var_type)
        return var_type, coerced

    def _reassign_list_element_compat(
        self, existing_pl: PendingListType, value_type: TpyType,
        value_expr: TpyExpr, context: str,
    ) -> 'CompatError | None':
        """Element compatibility for reassigning a PendingList local.

        Permissive on widening (accepts either coercion direction) to avoid
        regressing the prior accept-everything behavior; the point is only to
        catch cross-category mismatches (list[int] <- list[str] / non-list).
        The display-typed message avoids leaking the internal PendingList repr.
        """
        loc = getattr(value_expr, "loc", None)
        new_elem_raw = self._list_like_element(value_type)
        if new_elem_raw is None:
            return CompatError(
                f"Type mismatch in {context}: expected "
                f"list[{self._default_resolve_element(existing_pl.element_type)}], "
                f"got {value_type}", loc)
        existing_elem = self._default_resolve_element(existing_pl.element_type)
        new_elem = self._default_resolve_element(new_elem_raw)
        if existing_elem == new_elem:
            return None
        # source_expr=None: these probe the *element* types, so the list RHS
        # node must not drive _check_compat's expr-identity side effects
        # (retro-widen / set_expr_type / mutable-lvalue marking).
        fwd = self._check_compat(new_elem, existing_elem, context, loc, None,
                                 False, CoercionContext.ASSIGN,
                                 target_is_storage_form=True)
        if not isinstance(fwd, CompatError):
            return None
        bwd = self._check_compat(existing_elem, new_elem, context, loc, None,
                                 False, CoercionContext.ASSIGN,
                                 target_is_storage_form=True)
        if not isinstance(bwd, CompatError):
            return None
        return CompatError(
            f"Type mismatch in {context}: expected list[{existing_elem}], "
            f"got list[{new_elem}]", loc)

    def _list_like_element(self, t: TpyType) -> 'TpyType | None':
        if isinstance(t, PendingListType):
            return t.element_type
        if is_list(t) or is_array(t):
            return t.type_args[0]
        return None

    def _maybe_raise_literal_local_range(
        self, expr: TpyName, actual: TpyType, expected: TpyType, context: str,
    ) -> None:
        """Raise a range error when a literal-seeded local would have
        retro-widened to `expected` except a recorded literal value falls
        outside the target's range. Surfaces the literal value (e.g. -1,
        or 300 against UInt8) instead of the bare "got Int32" mismatch.
        """
        cand = self.deduction.literal_retro_candidate(expr.name, actual, expected)
        if cand is None:
            return
        target, values = cand
        bad = [v for v in values if not fixed_int_range_contains(target, v)]
        if not bad:
            return
        tr = int_traits_of(target)
        raise self.ctx.error(
            f"Integer literal {bad[0]} assigned to '{expr.name}' is outside "
            f"{target} range [{tr.min_value}, {tr.max_value}] in {context}",
            expr,
        )

    def _resolve_pending_for_any_storage(self, actual: TpyType) -> TpyType:
        """Convert Pending{List,Dict,Set}Type to its concrete container
        form using the literal-info's inferred element types, defaulting
        IntLiteralType / FloatLiteralType to the configured defaults.
        """
        if isinstance(actual, PendingListType):
            elem = self._default_resolve_element(actual.element_type)
            info = self.ctx.list_literals.get(actual.literal_id)
            if info is not None and info.coerced_element_type is None:
                info.coerced_element_type = elem
            return make_list(elem)
        if isinstance(actual, PendingDictType):
            k = self._default_resolve_element(actual.key_type)
            v = self._default_resolve_element(actual.value_type)
            return make_dict(k, v)
        if isinstance(actual, PendingSetType):
            elem = self._default_resolve_element(actual.element_type)
            return make_set(elem)
        return actual

    def _default_resolve_element(self, t: TpyType) -> TpyType:
        if isinstance(t, IntLiteralType):
            return self.ctx.default_int_type
        if isinstance(t, FloatLiteralType):
            return FLOAT
        return t

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
        # A ternary of two lvalue arms is itself an lvalue (C++ binds it as a
        # reference) -- so passing it to an Own[T] slot copies the chosen arm,
        # which must route through the same "copies into owned storage" warning
        # a plain lvalue does (a mixed lvalue/rvalue ternary is a prvalue and is
        # handled by the reference-ternary copy diagnostic instead).
        if isinstance(expr, TpyIfExpr):
            return self.is_lvalue(expr.then_expr) and self.is_lvalue(expr.else_expr)
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

    def _is_auto_moved(self, source_expr: 'TpyExpr | None') -> bool:
        """Last-use of an owned local: auto-move makes the copy invisible."""
        return self.is_auto_move_use(source_expr)

    def is_auto_move_use(self, expr: 'TpyExpr | None') -> bool:
        """Single authority for "this name read auto-moves": a last-use mark
        on an owned local/param whose storage has no borrower that liveness
        cannot see. Bind-based aliases (b = a, a = o.inner, n = xs[0]) are
        in the prescan alias maps and modeled by the liveness walk itself
        with dead-alias precision; call-result borrows (return_borrows_from)
        and iterator borrows are not, so any such borrower demotes the move
        to the copy path. The mark is retracted on demotion so codegen
        (which reads all_last_uses directly) lands on the same decision.
        """
        if not (isinstance(expr, TpyName)
                and id(expr) in self.ctx.all_last_uses
                and self._is_owned_var(expr.name)):
            return False
        return not self.demoted_by_hidden_borrow(expr)

    def demoted_by_hidden_borrow(self, expr: TpyName) -> bool:
        """The borrow-gate primitive: when ``expr``'s storage has a borrower
        the liveness alias maps cannot see, retract the last-use mark (sema
        and codegen both read all_last_uses, so the copy fallback is
        consistent) and return True. Callers check the mark themselves --
        this only decides and applies the demotion.
        """
        known = (self.ctx.func.current_alias_sources.keys()
                 | self.ctx.func.current_chain_alias_sources.keys())
        if self.ctx.func.borrow_tracker.has_borrowers_outside(expr.name, known):
            self.ctx.all_last_uses.discard(id(expr))
            return True
        return False

    def _warns_copy_into_any(self, inner: TpyType) -> bool:
        """Reference types whose into-Any storage silently copies.

        Caller must pre-unwrap Ref/Readonly/Own. Returns True for non-value
        records, list, dict, set; False for primitives, value-type records,
        str (collapses to std::string), bytes/BytesView, Ptr[T].
        """
        if isinstance(inner, NominalType):
            return not inner.is_value_type() and not inner.is_protocol
        return is_list(inner) or is_dict(inner) or is_set(inner)

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
            if self.is_auto_move_use(expr):
                self.ctx.mark_own_param_consumed(expr.name)
            return
        if self.is_copy_call(expr) and isinstance(expr, TpyCall) and expr.args:
            inner = expr.args[0]
            if isinstance(inner, TpyName):
                self.ctx.mark_own_param_consumed(inner.name)

    def check_own_lvalue_into_own(
        self, own_type: OwnType, expr: TpyExpr, context: str,
        *, action: str = "return",
    ) -> None:
        """Check that an lvalue feeding an Own[T] slot is movable, an explicit
        copy(), or otherwise safe to consume.

        Used by:
        - return-statement Own[T] / per-element Own[tuple[T,...]] checks
          (action="return"); error message says "Cannot return borrowed
          value as ...".
        - call-arg Own[tuple[T,...]] per-element check (action="pass");
          error message says "Cannot pass borrowed value as ...".

        Args:
            own_type: The Own[T] slot type.
            expr: The source expression occupying the slot.
            context: Slot description for error messages, e.g. "return type",
                "tuple element 1", "argument 'pname' element 0".
            action: "return" or "pass" -- selects the verb in error messages.
        """
        if self.is_copy_call(expr):
            self.check_own_consumption(expr)
            return
        if not self.is_lvalue(expr):
            return
        # Value types are always safe -- copied, not aliased. OwnType.is_value_type
        # returns True (Own represents a moved value), so we have to peel Own
        # first to see whether the underlying T is genuinely a value type.
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        raw_type = self.ctx.get_raw_expr_type(inner)
        if raw_type is not None:
            unwrapped = unwrap_ref_type(raw_type)
            inner_after_own = unwrapped.wrapped if isinstance(unwrapped, OwnType) else unwrapped
            if inner_after_own.is_value_type():
                return
            # action="return": Own-typed lvalues at non-last-use are tolerated
            # (codegen falls back to copy, paired with the coercion-path warning).
            # action="pass": enforce last-use even for Own locals -- the
            # per-element check exists precisely because the user opted in to
            # ownership semantics by writing Own[tuple[...]] and the silent copy
            # is the bug being fixed.
            if action == "return" and isinstance(unwrapped, OwnType):
                return
        # Consuming method: self.field is owned and movable out of the struct.
        if (self.ctx.in_consuming_method
                and isinstance(expr, TpyFieldAccess)
                and isinstance(expr.obj, TpyName) and expr.obj.name == "self"):
            return
        if self.is_auto_move_use(expr):
            self.check_own_consumption(expr)
            return
        expr_type = self.ctx.get_expr_type(expr)
        is_nocopy = expr_type is not None and self.ctx.is_type_nocopy(expr_type)
        if is_nocopy:
            reason = self.ctx.nocopy_reason(expr_type)
            is_movable = (isinstance(expr, TpyName)
                          and self._is_owned_var(expr.name))
            if is_movable:
                raise self.ctx.error(
                    f"{reason} is used after this point "
                    f"and cannot be moved into {context} Own[{own_type.wrapped}]. "
                    f"Remove later uses or restructure the code.",
                    expr
                )
            verb = "returned" if action == "return" else "passed"
            raise self.ctx.error(
                f"{reason} cannot be {verb} as "
                f"{context} Own[{own_type.wrapped}]. "
                f"Only the original owner can be moved at its last use.",
                expr
            )
        verb = "return" if action == "return" else "pass"
        raise self.ctx.error(
            f"Cannot {verb} borrowed value as {context} Own[{own_type.wrapped}] "
            f"without explicit copy(). The source is borrowed (parameter, "
            f"attribute, or non-last-use variable); use 'copy(...)' to make "
            f"an owned copy.",
            expr
        )

    def _is_value_type_param(self, typ: TpyType) -> bool:
        """Check if a TypeParamRef has a ValueType bound in the current context."""
        if not isinstance(typ, TypeParamRef):
            return False
        bound = self.type_ops.get_type_param_bound(typ.name)
        return bound is not None and isinstance(bound, NominalType) and bound.qualified_name() == "tpy.ValueType"

    def deep_convertible_to_union(self, actual: TpyType, union: TpyType) -> bool:
        """Whether a concrete list/dict's (possibly nested) leaves would each
        coerce to a member of the recursive-union wrapper `union` -- i.e. the
        container could in principle be element-wise rebuilt into it. Pure
        check, no side effects. Used only to drive a clear rejection diagnostic:
        TPy does NOT implicitly convert (that would be a hidden O(n) deep copy);
        the user must build the value as the alias or pass a literal."""
        members: 'tuple[TpyType, ...] | None' = None
        if isinstance(union, UnionType):
            members = union.members
        elif isinstance(union, RecursiveAliasInstanceType):
            members = union.alternatives()
        if members is None:
            return False
        # A list literal local is still a PendingListType at call-analysis time
        # (list-vs-Array is decided at end-of-function); both resolutions rebuild
        # into the union's list member, so accept either.
        if is_list(actual) or is_array(actual) or isinstance(actual, PendingListType):
            if not any(is_list(m) for m in members):
                return False
            elem = (actual.element_type if isinstance(actual, PendingListType)
                    else actual.get_element_type())
            return self._elem_convertible_to_union(elem, union)
        if is_dict(actual) or isinstance(actual, PendingDictType):
            dict_member = next((m for m in members if is_dict(m) and m.type_args), None)
            if dict_member is None:
                return False
            if isinstance(actual, PendingDictType):
                key_t, val_t = actual.key_type, actual.value_type
            elif actual.type_args:
                key_t, val_t = actual.type_args[0], actual.type_args[1]
            else:
                return False
            # The union's dict member fixes the key type (e.g. str); the value
            # is what gets deep-converted, so only the key must already match.
            if key_t != dict_member.type_args[0]:
                return False
            return self._elem_convertible_to_union(val_t, union)
        return False

    def _elem_convertible_to_union(self, elem: TpyType, union: TpyType) -> bool:
        if (is_list(elem) or is_dict(elem) or is_array(elem)
                or isinstance(elem, (PendingListType, PendingDictType))):
            return self.deep_convertible_to_union(elem, union)
        # A rebuild is only NEEDED when the leaf is not already the union: a
        # literal materialized against the union hint already has union-typed
        # elements (no conversion, handled by the literal path), whereas a
        # concrete container variable (e.g. dict[str, Int32]) has a genuinely
        # different element. The leaf must also coerce to a union member.
        # `_check_compat` with no source_expr has no side effects.
        if self._resolve_recursive_refs(elem) == union:
            return False
        return not isinstance(
            self._check_compat(elem, union, "deep-union leaf"), CompatError)

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
            # Resolve recursive-alias placeholders (AliasRef from parser, or
            # forward NominalType refs from cross-module aliases) into their
            # underlying UnionType so the union-coercion path below applies.
            e_resolved = (self._resolve_recursive_refs(e_arg)
                          if isinstance(e_arg, (NominalType, AliasRef)) else e_arg)
            if isinstance(e_resolved, UnionType) and e_resolved.needs_wrapper():
                # Container type-arg slot is storage form (the container stores
                # values, no address-take fires for pointer-repr Optional).
                result = self._check_compat(
                    a_arg, e_resolved, context, loc, None, is_return, coercion_ctx,
                    target_is_storage_form=True,
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

    def is_covariant_generic_upcast(self, actual: TpyType, expected: TpyType) -> bool:
        """True if `actual -> expected` is a covariant-generic wrapper upcast
        (e.g. `Box[Child] -> Box[Parent]`): same user-record generic, differing
        type args, every differing position covariant and a valid C++ upcast
        target. This is a representation-preserving converting move, NOT
        slicing. Shared by `check_type_compatible` and the container-literal
        element guards so the covariant-vs-slice line is drawn in one place."""
        if not (isinstance(actual, NominalType) and actual.is_user_record
                and isinstance(expected, NominalType) and expected.is_user_record
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and actual.type_args != expected.type_args):
            return False
        record_info = self.ctx.registry.get_record(actual.name)
        if record_info is None or not record_info.type_params:
            return False
        covariant = get_covariant_params(record_info)
        return bool(covariant and self._check_covariant_args(
            record_info, covariant, actual, expected))

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

    def _is_representational_subtype(self, child: TpyType, parent: TpyType) -> bool:
        """True if a `Ptr[child] -> Ptr[parent]` C++ pointer upcast is valid,
        where `child` is a bounded type parameter.

        Matches `child`'s IMMEDIATE bound against the actual `parent` at each
        hop -- never flattens a bound to bound(parent), which would wrongly
        admit a sibling subtype (`U: T`, `T: Pet` does NOT make `U` any
        `Pet`). A `U: V`, `V: U` cycle is guarded by a seen-set.

        A protocol `parent` is declined: a structural conformer satisfies the
        bound without inheriting, so the plain C++ pointer upcast would be
        invalid. Those need the adapter path (out of scope here). Only a class
        or a (further) type-param bound proves the upcast. The concrete
        instantiation's soundness is enforced at the call site.
        """
        if is_protocol_type(parent):
            return False
        # Record the originating type-param as representational so codegen at
        # call sites knows to materialize structural conformers via Adapter.
        origin_param = child.name if isinstance(child, TypeParamRef) else None
        seen: set[str] = set()
        cur = child
        while isinstance(cur, TypeParamRef) and cur.name not in seen:
            seen.add(cur.name)
            bound = self.type_ops.get_type_param_bound(cur.name)
            if bound is None:
                return False
            if bound == parent:
                if origin_param is not None:
                    self.ctx.func.current_representational_params.add(origin_param)
                return True
            if isinstance(bound, NominalType) and bound.is_user_record:
                if self._is_covariant_target(bound, parent):
                    if origin_param is not None:
                        self.ctx.func.current_representational_params.add(origin_param)
                    return True
                return False
            if isinstance(bound, TypeParamRef):
                cur = bound
                continue
            return False
        return False

    def _callable_signature_satisfies(
        self, actual_params: tuple[TpyType, ...], actual_return: TpyType,
        expected: CallableType, loc: 'SourceLocation | None',
    ) -> bool:
        """Whether a concrete callable signature satisfies an expected
        Fn/Callable contract. Function params are CONTRAVARIANT: the callee
        must accept everything the contract may pass, so each expected param
        must be compatible with the callee's declared param (e.g. a callee
        taking Int32 | None satisfies Fn[[Int32], ...], not the reverse).
        Returns are covariant; a void contract accepts any return.
        """
        if len(actual_params) != len(expected.param_types):
            return False
        if not all(self._check_compat(e, a, "param", loc) is None
                   for a, e in zip(actual_params, expected.param_types)):
            return False
        return (self._check_compat(actual_return, expected.return_type,
                                   "return", loc) is None
                or isinstance(expected.return_type, VoidType))

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
            # Hoisted vars are not movable (T* pointer-locals). Require
            # owned_locals too: a borrow-producing init (reference-returning
            # call, ternary/and-or of reference lvalues) lands in rvalue_vars
            # for hoist eligibility but is a `T&` alias -- moving out of it
            # would corrupt the aliased source.
            if (name in self.ctx.func.rvalue_vars
                    and name in self.ctx.func.owned_locals
                    and name not in self.ctx.func.hoisted_vars):
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

        An `Own[T]` param is CALLEE-owned and dies at function exit, so it does
        NOT confer return-safety on borrows rooted in it; the same holds for
        `self` in a consuming method. (Returning the owned value itself moves
        by value through the OwnType return path, which never reaches the
        dangling checks.) Generic `Own[...T...]` params keep the legacy
        params-are-safe treatment: the body is analyzed pre-instantiation
        where value-ness (by-value vs by-reference return) is unknown --
        same TypeParamRef exemption the per-element tuple dangle check uses.
        """
        func = self.ctx.func.current_function
        if name == "self":
            return not (isinstance(func, TpyFunction) and func.is_consuming)
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    own_inner = unwrap_optional_own(unwrap_readonly(ptype))
                    return (own_inner is None
                            or contains_type_param(own_inner))
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
        # Walrus safety follows the wrapped value (superset invariant with
        # is_dangling_return's TpyNamedExpr case).
        if isinstance(expr, TpyNamedExpr):
            return self.is_safe_to_return_expr(expr.value)
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
        # Named variables: mutable unless the binding's type is `readonly[T]`
        # (parameter declared as `readonly[T]`, or `self` inside a @readonly
        # method). Without this check, a readonly binding could launder
        # away its const via `&x` / `take_ptr(x)` / record-to-Ptr upcasts.
        if isinstance(expr, TpyName):
            expr_type = self.ctx.get_expr_type(expr)
            if isinstance(expr_type, ReadonlyType):
                return False
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
        # A walrus hands out its value: `return (t := items[0])` returns the
        # subscript read, so provenance follows the wrapped expression.
        if isinstance(expr, TpyNamedExpr):
            return self.is_dangling_return(expr.value)
        # Array/dict literal - creates temporary
        if isinstance(expr, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            return True

        # f-string always materializes a fresh std::string temporary, so a view
        # of it (`return f"..."` as StrView, or `f"..."[a:b]`) dangles.
        if isinstance(expr, TpyFString):
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

            # Expression callees (e.g. a call through a returned callable) yield a
            # temporary we cannot prove outlives the return -- treat as dangling.
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
            if fi is not None and (is_str_type(fi.return_type) or is_string_type(fi.return_type)
                                   or is_bytes_type(fi.return_type) or is_bytearray_type(fi.return_type)):
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
            if fi is not None and (is_str_type(fi.return_type) or is_string_type(fi.return_type)
                                   or is_bytes_type(fi.return_type) or is_bytearray_type(fi.return_type)):
                return True
            # A user method returning Own[T] creates a by-value temporary;
            # taking its address (the Optional pointer-repr return path) would
            # emit `&(obj.method())`. Builtin container methods (list.pop, ...)
            # also return Own[T] but are exempt -- mirrors the free-function
            # branch's user-function-only OwnType guard above.
            if fi is not None and isinstance(fi.return_type, OwnType):
                obj_type = self.ctx.get_expr_type(expr.obj)
                if isinstance(obj_type, NominalType) and obj_type.is_user_record:
                    return True
            # Method whose return borrows from its receiver/args: dangles if
            # the borrowed source dangles -- mirrors the free-function branch;
            # index -1 is the receiver (the 8b convention).
            if fi is not None and fi.return_borrows_from:
                for idx in fi.return_borrows_from:
                    if idx == -1:
                        if self.is_dangling_return(expr.obj):
                            return True
                    elif 0 <= idx < len(expr.args):
                        if self.is_dangling_return(expr.args[idx]):
                            return True
            return False

        # Ternary - dangles if either branch dangles
        if isinstance(expr, TpyIfExpr):
            return (self.is_dangling_return(expr.then_expr)
                    or self.is_dangling_return(expr.else_expr))

        # Unary/Binary ops - might create temporaries, be conservative
        if isinstance(expr, (TpyUnaryOp, TpyBinOp)):
            return True

        # Fail closed: an unrecognized expression kind that produces a borrowing
        # view (str/bytes view, span) is of unknown provenance, so treat it as
        # dangling rather than silently safe -- otherwise the next new node kind
        # recreates the view-lifetime hole under fresh syntax. Recognized kinds
        # with defined provenance (names, calls, methods, subscripts, ...) return
        # above; only genuinely-unhandled view-typed exprs reach here.
        expr_type = self.ctx.get_expr_type(expr)
        if expr_type is not None and is_borrowing_view_type(unwrap_readonly(expr_type)):
            return True

        # Default: assume safe
        return False

    def check_view_return_dangle(self, expr: TpyExpr, return_type: TpyType,
                                 loc: SourceLocation | None) -> None:
        """Variant of check_dangling_reference for value-return contexts
        (lambda bodies, yield values). The full check_dangling_reference
        rejects local/temporary returns when the return type is a non-value
        object -- a false positive for value-return contexts where the C++
        callable/generator machinery moves/copies by value. Only the
        view-dangling and pointer-dangling sub-rules apply here.
        """
        if isinstance(return_type, PtrType) or is_borrowing_view_type(return_type):
            self.check_dangling_reference(expr, return_type, loc)

    def check_dangling_reference(self, expr: TpyExpr, return_type: TpyType,
                                 loc: SourceLocation | None,
                                 source_type: TpyType | None = None,
                                 *, for_yield: bool = False) -> None:
        """Check if returning (or yielding) expr as a reference would dangle.

        Object types are returned/yielded by reference. A local variable or
        newly constructed object would create a dangling reference. `source_type`
        is the analyzed type of `expr` before return-coercion; it lets the
        recursive-union-wrapper case tell a wrap-into-wrapper (fresh temporary)
        from a reference to an existing wrapper value.

        `for_yield`: the same rooting rule applies to a generator's borrow yield
        (`Iterator[T]`, T non-value) -- the generator frame survives suspension,
        so a yield must root in frame-held storage exactly as a return must root
        in caller-outliving storage. Only the diagnostic wording differs: the
        owning escape is `Iterator[Own[T]]` rather than `Own[T]`.
        """
        verb = "yield" if for_yield else "return"

        def _own_fix(t: object, *, cap: bool) -> str:
            s = (f"declare the generator 'Iterator[Own[{t}]]' to yield by value"
                 if for_yield else f"use Own[{t}] to return by value")
            return (s[0].upper() + s[1:]) if cap else s

        # Only check object types (value types are returned by value)
        # OwnType returns by value (ownership transfer), so no dangling risk
        # Pointer types need dangling checks (the pointer value may point to a local)
        if isinstance(return_type, PtrType):
            if self.is_dangling_return(expr):
                raise self.ctx.error(
                    f"Cannot {verb} pointer to local or temporary value; "
                    f"the {verb}ed pointer would dangle",
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
        # A tuple's borrow form (std::tuple<..., T*, ...>) stores each non-value
        # member by pointer. The check sees through a readonly wrap (the slot is
        # still borrow form). A borrow member nested inside an *inner* tuple is
        # rejected outright -- codegen's tuple borrow/storage conversion is flat
        # (doesn't recurse), so such a slot is miscompiled regardless of whether
        # the source dangles (BUGS.md). A top-level borrow member is fine when
        # rooted, so it gets the per-element fresh-source dangling check.
        tuple_rt = unwrap_readonly(return_type)
        if isinstance(tuple_rt, TupleType):
            nested_bad = self._nested_tuple_borrow_member(tuple_rt)
            if nested_bad is not None:
                raise self.ctx.error(
                    f"A tuple {verb} with a non-value member ('{nested_bad}') nested "
                    f"inside another tuple is not yet supported -- the nested borrow "
                    f"slot is miscompiled. Flatten the tuple, or make the member "
                    f"Own[{nested_bad}].",
                    expr
                )
            self._check_tuple_elem_dangle(tuple_rt, expr, verb, source_type)
            self._check_tuple_member_local(tuple_rt, expr, verb)
            if not for_yield:
                self._check_tuple_storage_return_root(tuple_rt, expr)
            return
        if (return_type.is_value_type()
                or isinstance(return_type, (VoidType, OwnType))):
            return

        # Recursive-union wrappers follow the reference-type convention: a bare
        # `X` return lowers to `X&`. Coercing a value / list / None into the
        # wrapper materializes a fresh wrapper temporary, so returning it by
        # reference would dangle -- only an existing wrapper-typed reference
        # (parameter / field / global / a ref-returning call) is safe. Require
        # Own[X] for fresh values, matching the list / dict / record rule.
        if unwrap_readonly(return_type).needs_wrapper():
            src = expr.expr if isinstance(expr, TpyCoerce) else expr
            # The value is wrapped into the wrapper (a fresh temporary) unless
            # its source type is already that wrapper -- i.e. a reference to
            # existing wrapper data. The wrap is implicit at codegen, so the
            # pre-coercion source_type is the only reliable signal.
            from_existing_wrapper = (source_type is not None
                and unwrap_readonly(unwrap_ref_type(source_type)).needs_wrapper())
            if not from_existing_wrapper or self.is_dangling_return(src):
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as reference. "
                    f"Object type '{return_type}' is {verb}ed by reference. "
                    f"{_own_fix(return_type, cap=True)}.",
                    expr
                )
            return

        # Optional[T] for non-value T returns T* -- returning a local would dangle.
        # But `return None` is always safe (returns nullptr).
        if isinstance(return_type, OptionalType):
            if isinstance(expr, TpyNoneLiteral):
                return
            if self.is_dangling_return(expr):
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as '{return_type}'. "
                    f"The {verb}ed pointer would dangle. "
                    f"{verb.capitalize()} a reference to parameter data, or {_own_fix(return_type, cap=False)}.",
                    expr
                )
            return

        # Check if the expression is safe to return as a reference
        if self.is_dangling_return(expr):
            if is_protocol_type(return_type):
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as '{return_type}'. "
                    f"Dynamic protocol return requires a value that outlives the caller "
                    f"(parameter or global).",
                    expr
                )
            raise self.ctx.error(
                f"Cannot {verb} local or temporary as reference. "
                f"Object type '{return_type}' is {verb}ed by reference. "
                f"{_own_fix(return_type, cap=True)}.",
                expr
            )

    def _tuple_literal_fresh_borrow_elem(
            self, tuple_type: TupleType, inner: TpyExpr) -> int | None:
        """First element index whose non-value (borrow-form) slot has a fresh /
        dangling source in this tuple literal, else None.

        Drives the owns-fresh-tuple-member assignment flag (see
        `update_tuple_member_local_facts`). The literal yield/return path has its
        own per-element loop (`_check_tuple_elem_dangle`), which is strictly
        stronger: it also catches a value coerced into a wrapper element via the
        pre-coercion source type, which this name-based flag cannot see.
        """
        if not isinstance(inner, TpyTupleLiteral):
            return None
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(inner.elements):
                break
            # Recursive-union wrappers are borrow form here too, so a fresh
            # wrapper member dangles like any other borrow element; the
            # is_dangling_return source check tells a fresh local from a
            # param/self-rooted wrapper, so there is no needs_wrapper() skip.
            if (not et.is_value_type() and not isinstance(et, (OwnType, TypeParamRef))
                    and self.is_dangling_return(inner.elements[i])):
                return i
        return None

    def _tuple_elem_still_borrow(self, tuple_type: TupleType, idx: int) -> bool:
        """Whether element `idx` of the *declared* boundary tuple is still
        borrow form. The hazard flag's index comes from the assignment-inferred
        type; the boundary type may differ (an `Own[T]` element -- the escape --
        is moved by value and safe), so re-check before rejecting. A
        recursive-union wrapper element is borrow form (`X&`), so a flagged fresh
        wrapper member is NOT exempt (the durable check never flags a wrapper)."""
        if idx >= len(tuple_type.element_types):
            return False
        et = tuple_type.element_types[idx]
        return not (et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)))

    def _check_tuple_elem_dangle(self, tuple_type: TupleType, expr: TpyExpr,
                                 verb: str, source_type: TpyType | None = None) -> None:
        """Per-element dangling check for a top-level tuple return/yield literal.

        Each non-value member is stored by pointer in the tuple's borrow form,
        so a freshly-constructed member would dangle. The owning fix is
        element-scoped (`Own[T]` on the member), not the whole return/iterator.
        Nested-tuple members are handled upstream by the unsupported-shape check.
        A recursive-union-wrapper member is also borrow form: a value / list /
        None coerced into it is a fresh temporary, so the per-element check uses
        the pre-coercion `source_type` member types to tell a wrap-into-wrapper
        leaf from a reference to an existing wrapper.
        """
        inner = _peel_value_wrappers(expr)
        # A ternary returns whichever arm is taken -- check both.
        if isinstance(inner, TpyIfExpr):
            self._check_tuple_elem_dangle(tuple_type, inner.then_expr, verb,
                                          source_type)
            self._check_tuple_elem_dangle(tuple_type, inner.else_expr, verb,
                                          source_type)
            return
        if not isinstance(inner, TpyTupleLiteral):
            return
        src_elems = (source_type.element_types
                     if isinstance(source_type, TupleType)
                     and len(source_type.element_types) == len(tuple_type.element_types)
                     else None)
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(inner.elements):
                break
            if et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)):
                continue
            bad = self.is_dangling_return(inner.elements[i])
            if not bad and et.needs_wrapper():
                src_et = src_elems[i] if src_elems is not None else None
                from_existing_wrapper = (src_et is not None
                    and unwrap_readonly(unwrap_ref_type(src_et)).needs_wrapper())
                bad = not from_existing_wrapper
            if bad:
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as tuple element {i}. "
                    f"Type '{et}' is {verb}ed by reference. "
                    f"Use Own[{et}] for this tuple element to {verb} by value.",
                    inner.elements[i]
                )

    def update_tuple_member_local_facts(
            self, name: str, var_type: TpyType | None,
            init_expr: TpyExpr | None) -> None:
        """Set/clear the owns-fresh tuple-member hazard fact for a (re)assigned
        local (see `sema.context`).

        A tuple local bound from a literal with a FRESHLY-constructed non-value
        member is unsafe to yield/return by NAME -- the member is a dying local
        even in pointer borrow form. The hazard is equally present when the
        unsafe local is reached through an alias (`u = t`) or a ternary of
        aliases/literals, so the fact is derived from the init expression's
        provenance, not just a literal at the binding. Recorded here (not
        rejected) so a pure local read stays valid -- only a later yield/return
        of the bare name is rejected, at the boundary. A DURABLE reference
        member needs no fact: the bound local is pointer borrow form and
        aliases the member like CPython.

        Derive before clearing so a self-assignment (`t = t`) re-installs its own
        fact instead of losing it to the pop.
        """
        fresh = None
        owning = False
        borrow_into_own: list[int] = []
        copies_into_own: list[int] = []
        if init_expr is not None and var_type is not None:
            tt = unwrap_readonly(var_type)
            if isinstance(tt, TupleType):
                fresh = self._derive_tuple_member_hazards(tt, init_expr)
                if tt.has_pointer_repr_element():
                    owning = self._derive_owning_storage(init_expr)
                borrow_into_own = self._derive_borrow_into_own_hazards(init_expr)
                copies_into_own = self._derive_copies_into_own_hazards(init_expr)
        self.ctx.func.owns_fresh_tuple_member_vars.pop(name, None)
        if fresh is not None:
            self.ctx.func.owns_fresh_tuple_member_vars[name] = fresh
        # Per-element plain-borrow hazard (rebind clears the name's old pairs
        # first). Checked at a later NAME return/arg/store into an Own[T] slot.
        self.ctx.func.borrow_into_own_hazards = {
            (n, i) for (n, i) in self.ctx.func.borrow_into_own_hazards if n != name
        }
        for i in borrow_into_own:
            self.ctx.func.borrow_into_own_hazards.add((name, i))
        # Per-element owned-source copy warning (warn analog of the reject).
        self.ctx.func.copies_into_own_hazards = {
            (n, i) for (n, i) in self.ctx.func.copies_into_own_hazards if n != name
        }
        for i in copies_into_own:
            self.ctx.func.copies_into_own_hazards.add((name, i))
        # Flow-sensitive (snapshot + UNION merge): a branch-mixed or rebinding
        # local is owning on the merge iff any reaching path bound it owning;
        # the boundary return-root check rejects a bare-name return then. An
        # owning-call RHS materializes into a function-local storage slot in
        # codegen, so the local itself stays borrow form across the mix.
        if owning:
            self.ctx.func.owning_storage_tuple_vars.add(name)
        else:
            self.ctx.func.owning_storage_tuple_vars.discard(name)

    @staticmethod
    def _is_owning_tuple_call(expr: TpyExpr) -> bool:
        """Whether `expr` is a call returning OWNING tuple storage --
        `Own[tuple[...]]` or a tuple with per-element `Own` slots. A local
        bound from one owns its element storage (the call's return ABI is
        the storage form), unlike a borrow-form tuple call result whose
        pointers the callee already proved durable.
        """
        inner = _peel_value_wrappers(expr)
        if not isinstance(inner, (TpyCall, TpyMethodCall)):
            return False
        fi = inner.resolved_function_info
        if fi is None:
            return False
        rt = unwrap_readonly(fi.return_type)
        if isinstance(rt, OwnType):
            return isinstance(unwrap_readonly(rt.wrapped), TupleType)
        return (isinstance(rt, TupleType)
                and any(isinstance(et, OwnType) for et in rt.element_types))

    def _derive_owning_storage(self, expr: TpyExpr) -> bool:
        """Whether a binding from `expr` makes the local OWN its tuple
        element storage: an owning-tuple call, a name already carrying the
        fact, or a ternary with an owning arm (the result aliases either,
        so it is hazardous if either is -- matching the fresh-fact merge).
        """
        inner = _peel_value_wrappers(expr)
        if isinstance(inner, TpyIfExpr):
            return (self._derive_owning_storage(inner.then_expr)
                    or self._derive_owning_storage(inner.else_expr))
        if isinstance(inner, TpyName):
            return inner.name in self.ctx.func.owning_storage_tuple_vars
        return self._is_owning_tuple_call(inner)

    def _derive_borrow_into_own_hazards(self, init_expr: TpyExpr) -> list[int]:
        """Indices whose element is a PLAIN borrowed reference (param /
        attribute / non-last-use local) -- a copy into an Own[T] slot, not a
        move. An element that is an explicit copy(), a fresh rvalue, a value
        type, an owned last-use (move), or an Own-typed source is NOT a hazard.

        Dispatches on provenance like `_derive_tuple_member_hazards`: a literal
        scans its elements; a bare-name alias inherits the source local's
        recorded hazards; a ternary UNIONs both arms (the result aliases
        either). Other inits (calls) carry no hazard -- a callee returns an
        owning/durable tuple, not a borrow of the caller's data.
        """
        inner = init_expr.expr if isinstance(init_expr, TpyCoerce) else init_expr
        if isinstance(inner, TpyIfExpr):
            return sorted(
                set(self._derive_borrow_into_own_hazards(inner.then_expr))
                | set(self._derive_borrow_into_own_hazards(inner.else_expr)))
        if isinstance(inner, TpyName):
            return sorted(i for (n, i) in self.ctx.func.borrow_into_own_hazards
                          if n == inner.name)
        if not isinstance(inner, TpyTupleLiteral):
            return []
        return [i for i, elem in enumerate(inner.elements)
                if self.elem_is_plain_borrow(elem)]

    def _derive_copies_into_own_hazards(self, init_expr: TpyExpr) -> list[int]:
        """Indices whose element is an OWNED source (Own-typed param/return or
        an owned local) bound by REFERENCE -- not moved (not at last use) and
        not an explicit copy(). Such an element COPIES into an Own[T] slot, the
        warned analog of `_derive_borrow_into_own_hazards`. Same provenance
        dispatch: literal scans, bare name inherits, ternary UNIONs."""
        inner = init_expr.expr if isinstance(init_expr, TpyCoerce) else init_expr
        if isinstance(inner, TpyIfExpr):
            return sorted(
                set(self._derive_copies_into_own_hazards(inner.then_expr))
                | set(self._derive_copies_into_own_hazards(inner.else_expr)))
        if isinstance(inner, TpyName):
            return sorted(i for (n, i) in self.ctx.func.copies_into_own_hazards
                          if n == inner.name)
        if not isinstance(inner, TpyTupleLiteral):
            return []
        return [i for i, elem in enumerate(inner.elements)
                if self._elem_copies_owned_into_own(elem)]

    def _elem_copies_owned_into_own(self, elem: TpyExpr) -> bool:
        """Whether `elem` is an owned source bound by reference that COPIES into
        an Own[T] slot: not copy(), an lvalue, not moved at last use, and OWNED
        (Own-typed, or an owned local of a reference type). A plain borrowed
        source is the reject case (`elem_is_plain_borrow`), not this warn case."""
        if self.is_copy_call(elem) or not self.is_lvalue(elem):
            return False
        if self.is_owned_last_use_move(elem):
            return False
        raw = self.ctx.get_raw_expr_type(_peel_value_wrappers(elem))
        if raw is None:
            return False
        unwrapped = unwrap_ref_type(raw)
        # An Own-typed source is owned-by-value (OwnType.is_value_type() is
        # True), so check it before the value-type gate below.
        if isinstance(unwrapped, OwnType):
            return True
        if unwrapped.is_value_type():
            return False
        return isinstance(elem, TpyName) and self._is_owned_var(elem.name)

    def is_owned_last_use_move(self, elem: TpyExpr) -> bool:
        """An owned local/param at its last use -- it MOVES into a tuple slot
        (the local then OWNS the moved element) rather than borrowing. Mirrors
        the scalar field/return auto-move; the local-context elem-capture and
        the owning-storage derivation share this condition."""
        return self.is_auto_move_use(elem)

    def elem_is_plain_borrow(self, elem: TpyExpr) -> bool:
        """Whether `elem` is a plain borrowed reference that would COPY (not
        move) into an Own[T] slot -- the per-element analog of the scalar
        `check_own_lvalue_into_own` borrowed-source rejection, scoped to plain
        references (Own-typed and nocopy sources are handled elsewhere)."""
        if self.is_copy_call(elem):
            return False
        if not self.is_lvalue(elem):
            return False
        raw = self.ctx.get_raw_expr_type(_peel_value_wrappers(elem))
        if raw is None:
            return False
        unwrapped = unwrap_ref_type(raw)
        if isinstance(unwrapped, OwnType) or unwrapped.is_value_type():
            return False
        # Owned local at its last use moves into the slot -- not a copy.
        if self.is_owned_last_use_move(elem):
            return False
        return True

    def check_name_borrow_into_own(self, name: str, tuple_type: TupleType,
                                   expr: TpyExpr, action: str) -> None:
        """Reject when a tuple LOCAL referenced by `name` feeds a plain-borrow
        element into an `Own[T]` slot of the contextual `tuple_type` -- the
        implicit copy the scalar `Own[T]` and the literal-tuple forms already
        gate. The literal forms check inline; this covers the deferred NAME
        path via the construction-time hazard fact (`borrow_into_own_hazards`).
        `action` is "return" or "pass" (verb only); the field-literal warning
        path lives inline in `_annotate_tuple_elem_capture`."""
        for i, et in enumerate(tuple_type.element_types):
            if not isinstance(et, OwnType):
                continue
            if (name, i) in self.ctx.func.borrow_into_own_hazards:
                verb = "return" if action == "return" else "pass"
                raise self.ctx.error(
                    f"Cannot {verb} borrowed value as tuple element {i} "
                    f"Own[{et.wrapped}] without explicit copy(). The source is "
                    f"borrowed (parameter, attribute, or non-last-use variable); "
                    f"use 'copy(...)' to make an owned copy.",
                    expr,
                )
            # An owned source bound by reference (not moved) copies into the Own
            # slot -- the deferred-name analog of the scalar T->Own[T] copy
            # warning. (A moved owned source is storage form and carries no such
            # hazard; a plain borrowed source is the reject case above.)
            elif (name, i) in self.ctx.func.copies_into_own_hazards:
                self.ctx.warning(
                    f"copies {et.wrapped} into owned storage (tuple element "
                    f"{i}); use copy() to make this explicit",
                    expr,
                )

    def _derive_tuple_member_hazards(
            self, tt: TupleType, expr: TpyExpr) -> int | None:
        """Fresh-dangle borrow-member hazard index for an init expr bound to a
        tuple local of type `tt`, or None.

        Dispatches on provenance:
        - tuple literal: scan its elements (`_tuple_literal_fresh_borrow_elem`).
        - bare name: inherit the source local's already-recorded fact, re-checked
          against `tt` so an `Own[T]` boundary element (the escape) is dropped.
        - ternary: UNION the two arms -- the result aliases either, so it is
          hazardous if either is (conservative over-rejection, matching the
          flow_facts branch merge).
        Other init shapes (calls, etc.) carry no fact: a call can only return a
        durable borrow (its own fresh members are rejected at its return), and
        durable members alias safely in pointer borrow form.
        """
        inner = expr.expr if isinstance(expr, TpyCoerce) else expr
        if isinstance(inner, TpyIfExpr):
            then_f = self._derive_tuple_member_hazards(tt, inner.then_expr)
            else_f = self._derive_tuple_member_hazards(tt, inner.else_expr)
            return then_f if then_f is not None else else_f
        if isinstance(inner, TpyName):
            fresh = self.ctx.func.owns_fresh_tuple_member_vars.get(inner.name)
            if fresh is not None and not self._tuple_elem_still_borrow(tt, fresh):
                fresh = None
            return fresh
        return self._tuple_literal_fresh_borrow_elem(tt, inner)

    def _check_tuple_member_local(self, tuple_type: TupleType,
                                  expr: TpyExpr, verb: str) -> None:
        """Reject a bare-name yield/return of a tuple local bound from a literal
        with a non-value member (the non-literal remainder of
        `_check_tuple_elem_dangle`).

        The literal check only sees a `TpyTupleLiteral` at the boundary; here the
        source is a `TpyName` whose binding was flagged at assignment. Loop /
        param-derived tuple locals are never flagged, so borrow composition
        (`for pair in src: yield pair`) is unaffected. The two facts are checked
        fresh-first: if a still-applicable fresh (dangle) hazard exists it is
        reported, otherwise the durable (silent-copy) one is -- so a local that
        trips both surfaces the dangle. The two facts may flag different element
        indices; the re-check below resolves each against the boundary type.
        """
        inner = _peel_value_wrappers(expr)
        # A ternary returns whichever arm is taken -- check both.
        if isinstance(inner, TpyIfExpr):
            self._check_tuple_member_local(tuple_type, inner.then_expr, verb)
            self._check_tuple_member_local(tuple_type, inner.else_expr, verb)
            return
        if not isinstance(inner, TpyName):
            return
        fresh = self.ctx.func.owns_fresh_tuple_member_vars.get(inner.name)
        if fresh is not None and self._tuple_elem_still_borrow(tuple_type, fresh):
            et = tuple_type.element_types[fresh]
            raise self.ctx.error(
                f"Cannot {verb} tuple local '{inner.name}': element {fresh} "
                f"('{et}') owns a freshly constructed value that is {verb}ed "
                f"by reference and would dangle. Use Own[{et}] for this tuple "
                f"element to {verb} by value.",
                inner
            )
        # The durable (non-dangling) reference-member case needs no check: a
        # tuple local is pointer borrow form (std::tuple<..., T*>), so
        # yielding/returning it ALIASES the durable member exactly as CPython
        # shares it. Only the fresh-dangle case above is rejected -- a locally
        # constructed member dangles even as a pointer.

    def _check_tuple_storage_return_root(self, tuple_type: TupleType,
                                         expr: TpyExpr) -> None:
        """Reject a borrow-form tuple RETURN read from non-durable storage.

        Returning a storage-form source (field / subscript) lifts element
        ADDRESSES into that storage (codegen's tuple_to_pointer), so the
        storage root must outlive the call: non-Own params, non-consuming
        self, and globals qualify; locals, temporaries, Own params, and
        consuming-method self die at exit. Yields are exempt -- generator
        storage is frame-rooted (alive while the consumer iterates) and the
        consumer-side ephemeral-escape checks cover retention.
        """
        if not tuple_type.has_pointer_repr_element():
            return
        inner = _peel_value_wrappers(expr)
        # A ternary returns whichever arm is taken -- check both.
        if isinstance(inner, TpyIfExpr):
            self._check_tuple_storage_return_root(tuple_type, inner.then_expr)
            self._check_tuple_storage_return_root(tuple_type, inner.else_expr)
            return
        dangles = False
        if isinstance(inner, (TpyFieldAccess, TpySubscript)):
            dangles = self.is_dangling_return(inner)
        elif isinstance(inner, (TpyCall, TpyMethodCall)):
            # A relayed call: the callee's return_borrows_from names which
            # receiver/args the returned pointers root in -- dangling iff
            # that source is. An owning-rvalue return (Own[tuple] or
            # per-element Own) is a dying temporary: lifting it dangles.
            dangles = (self.is_dangling_return(inner)
                       or self._is_owning_tuple_call(inner))
        elif isinstance(inner, TpyName):
            # A local that aliases STORAGE (t = items[0] / t = h.pair / loop
            # var over a container): climb the borrow chain / loop provenance
            # to the storage root. Only chains crossing an ELEMENT/FIELD/PTR/
            # ITER borrow point INTO storage that must outlive the call;
            # ALIAS-only chains and literal-bound locals merely copy the
            # tuple's pointer slots, whose safety the owns-fresh facts cover
            # -- UNLESS the chain bottoms out in a local that OWNS its
            # element storage (bound from an owning-tuple call), where the
            # lift would point into the dying owner.
            bt = self.ctx.func.borrow_tracker
            owning = self.ctx.func.owning_storage_tuple_vars
            # Owning takes precedence: a local bound from an owning-tuple call
            # on ANY reaching path (UNION-merged) points into a function-local
            # slot that dies at return -- unsafe even if another path aliases
            # param storage (a branch-mixed local also carries that path's
            # FIELD borrow, which would otherwise mask the owning path here).
            if (inner.name in owning
                    or bt.effective_storage(inner.name) in owning):
                dangles = True
            elif self._borrow_chain_enters_storage(bt, inner.name):
                src = bt.effective_storage_through_borrows(inner.name)
                dangles = not self._name_is_param_or_global(_storage_root(src))
            else:
                it = self.ctx.func.loop_var_iterable.get(inner.name)
                if it is not None:
                    dangles = not self._name_is_param_or_global(
                        _storage_root(it))
        if dangles:
            bad = next(et for et in tuple_type.element_types
                       if tuple_type._element_is_pointer_repr(et))
            raise self.ctx.error(
                f"Cannot return this tuple: its non-value elements are "
                f"returned by reference into storage owned by the function, "
                f"which dies when it returns. Return a tuple rooted in "
                f"parameter data, or use Own[{unwrap_readonly(bad)}] for "
                f"such elements to return by value.",
                inner
            )

    @staticmethod
    def _borrow_chain_enters_storage(bt: BorrowTracker, name: str) -> bool:
        """Whether `name`'s borrow chain crosses a storage-entering borrow
        (ELEMENT/FIELD/PTR/ITER) -- i.e. holds addresses INTO a container or
        object rather than a value-copy of another local. ALIAS links are
        followed transitively; chains are acyclic so the walk terminates.
        """
        seen: set[str] = set()
        cur = name
        while cur not in seen:
            seen.add(cur)
            kind = bt.borrow_kind_of(cur)
            if kind is None:
                return False
            if kind is not BorrowKind.ALIAS:
                return True
            nxt = bt.borrow_source(cur)
            if nxt is None:
                return False
            cur = nxt
        return False

    def _nested_tuple_borrow_member(self, tuple_type: TupleType,
                                    _nested: bool = False) -> TpyType | None:
        """Return a borrow-form member type nested inside an inner tuple, or None.

        Codegen's tuple borrow/storage conversion is flat: a non-value member
        carried by an *inner* tuple slot (`std::tuple<..., std::tuple<..., T*>>`)
        is miscompiled. A top-level borrow member (`_nested` False) is fine and
        excluded; `Own` / value / wrapper / type-param members are value-stored
        and excluded at any depth.
        """
        for et in tuple_type.element_types:
            bare = unwrap_readonly(et)
            if isinstance(bare, TupleType):
                deeper = self._nested_tuple_borrow_member(bare, _nested=True)
                if deeper is not None:
                    return deeper
            elif (_nested and not et.is_value_type()
                    and not isinstance(et, (OwnType, TypeParamRef))
                    and not et.needs_wrapper()):
                return et
        return None
