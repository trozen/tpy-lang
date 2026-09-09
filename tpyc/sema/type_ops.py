"""
TurboPython Type Operations

Type validation, substitution, and inference operations.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import Literal, TYPE_CHECKING

from ..typesys import (
    TpyType, TypeParamRef, NominalType, RecursiveAliasInstanceType, PtrType, is_readonly_ptr, OwnType, ReadonlyType, AutoReadonlyType, AutoOwnType, InteriorMutableType,
    make_array, make_list, PendingListType, PendingViewType, GenExprType, SelfType, OptionalType, UnionType,
    TupleType, FinalType, ClassVarType,
    IntLiteralType, FloatLiteralType, TypeParamKind, BIGINT, NONE, UnknownElementType,
    NoneType, VoidType, CallableType, SendType, SyncType, unwrap_send_sync,
    RecordInfo, FunctionInfo, ParamInfo, is_protocol_type, unwrap_readonly,
    unwrap_ref_type, unwrap_qualifiers, RefType, is_dyn_protocol,
    is_polymorphic_class_type, is_dynamic_dispatch_inner,
    is_callable_type, is_integer_type, is_float_type, is_void_like_type,
    is_open_type_param_return, contains_type_param, coro_struct_owner,
    strip_template_repr,
)
from ..coercions import resolve_coercion, CoercionContext
from ..diagnostics import SemanticError, nocopy_container_elem_error
from .. import qnames
from ..type_def_registry import (
    is_copy_iter, is_own_iter, is_array, is_span, is_varargs, is_list, is_dict, is_set,
    is_enum_type, is_str_type, is_borrowing_view_type,
    get_type_def, find_factory_by_simple_name, protocol_info_of, is_subtype,
)
from ..parse import TpyFunction
from tpyc import modules as builtin_modules
from .overloads import resolve_overload

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import SemanticContext
    from .protocols import ProtocolChecker


_HASHABLE = NominalType("Hashable", is_protocol=True)
_EQUATABLE = NominalType("Equatable", is_protocol=True)


def signature_may_return_borrow(fi: 'FunctionInfo') -> bool:
    """Whether a function's declared return type could carry a reference
    into an argument's storage. Own[T] hands over a fresh value and value
    types are copied out, so neither can borrow; views (str/StrView/
    BytesView/Span) are value types that DO reference foreign storage, and
    a tuple may carry borrow-form elements. Unresolved generics stay
    conservative.
    """
    ret = unwrap_ref_type(unwrap_readonly(fi.return_type))
    if isinstance(ret, (OwnType, VoidType, NoneType)):
        return False
    if is_str_type(ret) or is_borrowing_view_type(ret):
        return True
    if isinstance(ret, TupleType) or is_open_type_param_return(ret):
        return True
    return not ret.is_value_type()


def partial_substitute(typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
    """Substitute known type params, preserve unknown TypeParamRefs as-is."""
    if isinstance(typ, TypeParamRef):
        return subst.get(typ.name, typ)
    return typ.map_inner_types(lambda t: partial_substitute(t, subst))


def to_owned_storage_form(typ: TpyType) -> TpyType:
    """Canonicalize an inference argument bound into an OWNED context.

    An owned slot -- an ``Own[T]`` parameter or a record constructor argument
    (records store fields by value) -- holds the storage form, never a borrow
    or a const view. A subscript of a non-value ``list[T]`` analyzes to the
    element *borrow form* (``Ref[T]``, recursing per-element for tuples), so
    without this an owned binding infers ``Ref[T]`` instead of ``T`` -- which
    then conflicts with the same param inferred as bare ``T`` elsewhere and
    defeats inference (e.g. ``Entry[Ref[T]]`` vs ``Entry[T]``). ``unwrap_ref_type``
    already recurses into tuples, so nested borrow elements are canonicalized
    too. Must NOT be used on the bare-param / Fn-return path, where ``Ref`` is
    deliberately preserved for reference passing (``map(identity, ...)``).
    Sibling rule for non-owned bare-T slots: ``to_bare_slot_form``."""
    return unwrap_readonly(unwrap_ref_type(typ))


def to_bare_slot_form(typ: TpyType) -> TpyType:
    """Canonicalize an inference argument bound into a bare-T template slot.

    Some generic positions render as bare ``T`` in C++ with no reference
    indirection of their own: ``varargs<T>`` elements (no val_or_ref
    indirection) and ``Ptr[T]`` pointees (the pointer already carries the
    indirection). A borrow leaking into such a slot renders as illegal
    ``varargs<T&>`` or redundant ``Ptr[T&]``, so strip ``Ref`` (recursing
    into tuples). Unlike ``to_owned_storage_form``, ``readonly`` is kept --
    it stays meaningful as constness in these slots. As there, the
    bare-param / Fn-return path must NOT use this: ``Ref`` is deliberately
    preserved there for reference passing."""
    return unwrap_ref_type(typ)


def post_substitute_hint(
    ptype: TpyType, subst: dict[str, TpyType],
) -> TpyType | None:
    """Substitute ``subst`` into ``ptype`` and unwrap to a usable hint shape.

    Strips Own/Readonly/Ref (via ``unwrap_qualifiers``) AND OptionalType so
    the hint reaches consumers (lambda ``is_callable_type`` check, literal
    branches, constructor LHS-hint preference) in a directly-usable inner
    shape -- an ``Optional[T]`` param accepts a bare T arg, so the hint that
    survives substitution should expose T to the inner record/function's seed.

    Returns None when the substitution is empty or leaves any unbound
    ``TypeParamRef`` -- callers fall back to hint-less analysis in that case.

    Shared between ``seeded_arg_hint`` (function/method call args, indexed
    via ``ParamInfo``) and ``_analyze_record_constructor``'s inline
    arg-seeding loop (indexed via ``record.init_params`` 3-tuples) so that
    qualifier-stripping rules stay symmetric across the two sites.
    """
    if not subst:
        return None
    hint = partial_substitute(ptype, subst)
    hint = unwrap_qualifiers(hint)
    if isinstance(hint, OptionalType):
        hint = hint.inner
        hint = unwrap_qualifiers(hint)
    if contains_type_param(hint):
        return None
    return hint


def seeded_arg_hint(
    params: list[ParamInfo], idx: int, subst: dict[str, TpyType],
) -> TpyType | None:
    """Per-arg ``expr_type_hint`` from a seeded type-param substitution.

    Resolves the param the call argument at position ``idx`` targets, then
    delegates to ``post_substitute_hint`` for the substitute+unwrap step.

    Variadic positions: every arg at or beyond the ``*args`` slot maps to
    the variadic param's element type (with the ``Span[readonly[T]]``
    packing stripped), mirroring how ``infer_type_params_for_function``
    matches variadic args.

    Returns None when there's no valid target param for the index, or when
    ``post_substitute_hint`` rejects the result. Caller dispatches on None
    to fall back to hint-less arg analysis.
    """
    if not subst:
        return None
    if idx < len(params):
        target = params[idx]
    elif params and params[-1].is_variadic:
        target = params[-1]
    else:
        return None

    ptype = unwrap_ref_type(target.type)
    if target.is_variadic and is_varargs(ptype):
        # *args: T has the varargs[T] (or varargs[readonly[T]]) body view; expose the element.
        ptype = unwrap_readonly(ptype.type_args[0])
    return post_substitute_hint(ptype, subst)


def _is_useful_seed_binding(t: object) -> bool:
    """True iff ``t`` is a meaningful pre-binding for an LHS-hint seed.

    Filtered out:
    - "No info" placeholders (None/Void/Unknown from reassignment-narrowing
      LHS like ``x = None; x = f(...)``).
    - Transient pending markers (PendingViewType / PendingListType, which
      ``infer_type_params_for_function`` resolves post-arg-inference anyway).
    - Bare TypeParamRef from an enclosing generic scope: ``seeded_arg_hint``
      itself rejects hints containing TPRefs, but the same binding also
      flows into ``partial_inferred`` in ``_infer_arg_types``' Fn-bearing
      branch -- there it could be compared against concrete arg types and
      silently reject arg-derived evidence.
    - Plain ``int`` values from INT-kind type-param bindings (e.g. Array's
      ``N`` matched against a concrete length): ``partial_substitute``
      ignores INT-kind params (``map_inner_types`` filters them out), so
      they never substitute into per-arg hints, but they would still leak
      into ``partial_inferred`` and conflict with arg-derived ``N`` during
      Phase-2 array matching.
    - ``IntLiteralType`` / ``FloatLiteralType`` literal markers: like the
      pending types, these are transient seed values that
      ``infer_type_params_for_function`` resolves post-arg-inference (to
      the default int/float type). Leaving them in the seed would pin a
      type param to a literal type before arg evidence widens it.
    """
    if isinstance(t, int):  # int-kind binding (Array[T, N])
        return False
    return not isinstance(
        t,
        (NoneType, VoidType, UnknownElementType, PendingViewType, PendingListType,
         TypeParamRef, IntLiteralType, FloatLiteralType),
    )


class TypeOperations:
    """Type validation, substitution, and inference operations."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # Set after construction (ProtocolChecker depends on TypeOperations,
        # so it can't be constructed before us). Mirrors the deferred-wiring
        # pattern in TypeCompatibility. Used by validate_hashable_container_elem
        # for Hashable + Equatable conformance.
        self.protocols: ProtocolChecker

    def resolve_type(self, typ: TpyType, *, protocols_only: bool = False) -> TpyType:
        """Sema-local type normalization.

        Runs on already-resolved `TpyType` values from the parser-side
        `TypeResolver` and applies the transformations that depend on
        sema state:

        - Bare `NominalType` matching a type parameter in the current
          function / record scope becomes `TypeParamRef` (parser emits
          `NominalType("T")` because type-param scope is scope-local
          to sema, not parser).
        - Compile-time-only aliases (e.g. `FStr` singleton) expand to
          their target type.
        - Structural wrappers (`PtrType`, `OwnType`, ...) recurse into
          their inner types so both transformations above reach deeply
          nested positions.

        Args:
            protocols_only: If True, skip TypeParamRef conversion.
                Used during record registration when the type-parameter
                scope is not yet active.
        """
        if isinstance(typ, NominalType):
            if not protocols_only and not typ.type_args and not typ.is_protocol:
                func = self.ctx.func.current_function
                if isinstance(func, TpyFunction) and typ.name in func.type_params:
                    bound = func.type_param_bounds.get(typ.name)
                    return TypeParamRef(typ.name, bound=bound)
                if typ.name in (self.ctx.record_ctx.type_params or []):
                    return TypeParamRef(typ.name)
            if not typ.type_args and self.ctx.registry.type_aliases:
                alias = self.ctx.registry.get_type_alias(typ.name)
                if alias is not None and alias.is_compile_time_only():
                    return alias
            if typ.type_args:
                new_args = tuple(
                    self.resolve_type(arg, protocols_only=protocols_only) if isinstance(arg, TpyType) else arg
                    for arg in typ.type_args
                )
                if any(new is not old for new, old in zip(new_args, typ.type_args)):
                    new_inner = tuple(a for a in new_args if isinstance(a, TpyType))
                    return typ.with_inner_types(new_inner)
            return typ
        if isinstance(typ, PtrType):
            resolved = self.resolve_type(typ.pointee, protocols_only=protocols_only)
            return PtrType(resolved, is_readonly=typ.is_readonly) if resolved is not typ.pointee else typ
        if isinstance(typ, (OwnType, ReadonlyType, AutoReadonlyType, AutoOwnType)):
            resolved = self.resolve_type(typ.wrapped, protocols_only=protocols_only)
            return type(typ)(resolved) if resolved is not typ.wrapped else typ
        if isinstance(typ, OptionalType):
            resolved = self.resolve_type(typ.inner, protocols_only=protocols_only)
            if resolved is typ.inner:
                return typ
            return typ.with_inner(resolved)
        if isinstance(typ, UnionType):
            resolved = tuple(self.resolve_type(m, protocols_only=protocols_only) for m in typ.members)
            return UnionType(resolved) if any(a is not b for a, b in zip(resolved, typ.members)) else typ
        if isinstance(typ, TupleType):
            resolved = tuple(self.resolve_type(e, protocols_only=protocols_only) for e in typ.element_types)
            return TupleType(resolved) if any(a is not b for a, b in zip(resolved, typ.element_types)) else typ
        return typ

    def validate_type(
        self, typ: TpyType, allow_type_param_ref: bool = False, loc: SourceLocation | None = None,
        allow_forward_ref: bool = True, check_hashable_constraints: bool = True,
        allow_pointer_repr_dynamic: bool = False,
    ) -> None:
        """Validate that a type is well-formed.

        Args:
            typ: The type to validate.
            allow_type_param_ref: If True, TypeParamRef is allowed (for generic class definitions).
            loc: Optional source location for error messages.
            allow_forward_ref: If True, unknown NominalType records are allowed (for class registration).
            allow_pointer_repr_dynamic: If True, a top-level `Optional[@dynamic protocol]`
                is allowed -- it lowers to a `const P*` borrow, which is sound at a
                parameter position. Reset for any nested position (storage / container /
                Own slot), where the value-repr `std::optional<P>` of an abstract base is
                not representable.
        """
        # Closure-captured recurse helper: every internal recursive call uses
        # this so the four contextual params (`allow_type_param_ref`, `loc`,
        # `allow_forward_ref`, `check_hashable_constraints`) propagate
        # automatically into nested wrapper / container / tuple branches.
        # Manual kwarg-forwarding at each site was a recurring source of
        # flag-drop bugs (`Own[set[Forward]]` field false-rejecting because
        # the OwnType recursion reset `check_hashable_constraints=False`
        # back to the default True). The closure makes the drop structurally
        # impossible -- adding a new wrapper branch in the future cannot
        # forget to forward.
        def _recurse(inner: TpyType) -> None:
            # allow_pointer_repr_dynamic is deliberately NOT forwarded here: a
            # nested position -- container element, Own slot, tuple member -- is
            # storage-shaped, where Optional[@dynamic P] has no valid value-repr,
            # so it must stay rejected. The transparent readonly wrappers are the
            # exception and preserve the flag explicitly in their own branch.
            self.validate_type(
                inner, allow_type_param_ref, loc,
                allow_forward_ref=allow_forward_ref,
                check_hashable_constraints=check_hashable_constraints,
            )

        # `unsafe_interior_mutable[T]` is a field-only marker; field registration strips
        # it before this runs, so reaching here means it was written on a param,
        # return, local, or container -- reject with a clear message instead of
        # letting the unstripped marker crash codegen.
        if isinstance(typ, InteriorMutableType):
            raise SemanticError(
                "unsafe_interior_mutable[...] is only valid on a class field declaration",
                loc)

        if isinstance(typ, TypeParamRef):
            if not allow_type_param_ref:
                raise SemanticError(f"Type parameter '{typ.name}' used outside of generic context", loc)
            if typ.kind == TypeParamKind.INT:
                raise SemanticError(f"Integer type parameter '{typ.name}' cannot be used as a type annotation", loc)
            return
        if isinstance(typ, NominalType) and typ.is_record:
            if typ.qualified_name() == "builtins.type":
                raise SemanticError("'type' cannot be used as a type annotation", loc)
            # Unified arity/validity check. See docs/ARCHITECTURE.md:
            # validity comes from qname/TypeDef/factory/record registry, not
            # from `_module_qname != None`. Prefer user-record info (may
            # shadow builtin names), then fall through to the factory
            # (covers bare-name NominalType like def f(x: list) that the
            # parser leaves without `_module_qname`).
            record_info = self.ctx.registry.get_record_for_type(typ)
            kinds: tuple | None = None
            if record_info is None:
                # Primary path: resolve via the fully qualified name the type
                # already knows. Parser leaves some bare-name builtins without
                # _module_qname (e.g. `def f(x: list)`), so qualified_name()
                # returns None or just the simple name; for those we scan
                # `builtins` + `tpy` by simple name as the factory-aware
                # fallback. Either way a single TypeDef lookup wins.
                qn = typ.qualified_name()
                td = get_type_def(qn) if qn else None
                if td is None or td.type_factory is None:
                    td = find_factory_by_simple_name(typ.name)
                if td is not None and td.type_factory is not None:
                    kinds = td.param_kinds
            if record_info is not None:
                if typ.type_args:
                    if not record_info.is_generic():
                        raise SemanticError(f"Record '{typ.name}' is not generic, but type arguments were provided", loc)
                    if len(typ.type_args) != len(record_info.type_params):
                        raise SemanticError(
                            f"Record '{typ.name}' expects {len(record_info.type_params)} type arguments, "
                            f"got {len(typ.type_args)}",
                            loc,
                        )
                    self.validate_record_type_args(
                        typ, record_info, allow_type_param_ref, loc,
                        allow_forward_ref=allow_forward_ref,
                        check_hashable_constraints=check_hashable_constraints,
                    )
                elif record_info.is_generic():
                    raise SemanticError(
                        f"Generic record '{typ.name}' requires type arguments: "
                        f"{typ.name}[{', '.join(record_info.type_params)}]",
                        loc,
                    )
            elif kinds is not None:
                # Factory-only builtin: primitive singleton (empty kinds) or
                # generic container (list, dict, set, Array, Span, ...). Arity
                # mismatch -- including the bare-generic case -- is an error.
                if len(kinds) != len(typ.type_args):
                    if not typ.type_args and kinds:
                        raise SemanticError(
                            f"Generic type '{typ.name}' requires "
                            f"{len(kinds)} type argument{'s' if len(kinds) != 1 else ''}",
                            loc,
                        )
                    if typ.type_args and not kinds:
                        raise SemanticError(
                            f"Type '{typ.name}' is not generic, but type arguments were provided",
                            loc,
                        )
                    raise SemanticError(
                        f"Type '{typ.name}' expects {len(kinds)} type argument"
                        f"{'s' if len(kinds) != 1 else ''}, got {len(typ.type_args)}",
                        loc,
                    )
            elif self.ctx.registry.get_enum(typ.name) is not None:
                pass  # imported enum -- NominalType will be resolved to EnumType
            elif self.ctx.registry.get_type_alias(typ.name) is not None:
                # Type alias name. Recursive aliases like
                # `type Tree = int | list[Tree]` leave a bare
                # NominalType("Tree") placeholder inside the body that
                # resolve_type expands but validate_type still encounters
                # during deep recursion -- accept it as a known alias
                # without further validation (the alias's body is itself
                # validated when registered).
                pass
            elif not allow_forward_ref:
                raise SemanticError(f"Unknown type: {typ.name}", loc)
            # Container element validation (containers are NominalType + TypeDef)
            elem_type = typ.get_element_type()
            if elem_type is not None:
                _recurse(elem_type)
                if is_protocol_type(elem_type):
                    raise SemanticError(
                        f"Protocol type '{elem_type}' cannot be used as a container element type",
                        loc,
                    )
            # Annotation-time gate for set/dict K. Deferred during early
            # record registration -- sibling records' methods aren't
            # populated yet, so we'd false-reject; validate_record_field_protocols
            # re-runs us post-registration with the default flag.
            if (is_set(typ) or is_dict(typ)) and check_hashable_constraints \
                    and not allow_type_param_ref:
                key_or_elem = typ.type_args[0] if typ.type_args else None
                if key_or_elem is not None and isinstance(key_or_elem, TpyType):
                    kind = "dict key" if is_dict(typ) else "set element"
                    self.validate_hashable_container_elem(key_or_elem, kind, loc)
        elif isinstance(typ, OptionalType):
            _recurse(typ.inner)
            # Reject `Optional[Own[Polymorphic]]`: a polymorphic Own lowers to
            # `unique_ptr<P>` (P abstract / a @dynamic protocol), so the
            # Optional slot is `optional<unique_ptr<P>>` -- a double indirection
            # the member-access, call-site, and isinstance lowerings do not
            # thread (a concrete Own collapses to `optional<P>` by value and
            # stays supported). Point users at `Optional[Box[P]]`, the
            # idiomatic nullable owned-polymorphic form. Mirrors the
            # `Own[Optional[Polymorphic]]` rejection in the OwnType branch.
            own_inner = unwrap_readonly(typ.inner)
            if isinstance(own_inner, OwnType):
                own_pointee = unwrap_readonly(own_inner.wrapped)
                if (isinstance(own_pointee, NominalType)
                        and (is_polymorphic_class_type(own_pointee, self.ctx.registry)
                             or is_dynamic_dispatch_inner(own_pointee, self.ctx.registry))):
                    raise SemanticError(
                        f"`Optional[Own[{own_pointee.name}]]` is not yet "
                        f"supported: a polymorphic `Own[{own_pointee.name}]` "
                        f"is already held through one indirection, so the "
                        f"optional slot is a double indirection that member "
                        f"access and isinstance do not thread today. Use "
                        f"`Optional[Box[{own_pointee.name}]]` for a nullable "
                        f"owned polymorphic value instead.",
                        loc,
                    )
            if is_protocol_type(typ.inner) and not allow_pointer_repr_dynamic:
                proto_def = protocol_info_of(typ.inner)
                if proto_def and proto_def.is_dynamic:
                    raise SemanticError(
                        f"Optional[{typ.inner}] is only supported at a parameter "
                        f"position (where it lowers to a `const {typ.inner}*` "
                        f"borrow). In a field, return, local, container, or owned "
                        f"slot it has no value representation; use `Optional"
                        f"[Box[{typ.inner}]]` for owned storage or a separate "
                        f"'has' flag instead",
                        loc,
                    )
        elif isinstance(typ, UnionType):
            non_none = [m for m in typ.members if not is_void_like_type(m)]
            protocols = [m for m in non_none if is_protocol_type(m)]
            concrete = [m for m in non_none if not is_protocol_type(m)]
            if protocols and concrete:
                proto_names = ", ".join(f"'{m}'" for m in protocols)
                raise SemanticError(
                    f"Cannot mix protocol types ({proto_names}) with concrete types in a union",
                    loc,
                )
            if protocols:
                for p in protocols:
                    proto_def = protocol_info_of(p)
                    if proto_def and proto_def.is_dynamic:
                        raise SemanticError(
                            f"@dynamic protocol '{p}' cannot be used in a protocol union; "
                            f"only static protocols are supported",
                            loc,
                        )
            for member in typ.members:
                _recurse(member)
        elif isinstance(typ, RefType):
            # Ref-wrapped param/return types -- recurse into the underlying
            # type. Without this branch, validate_type silently bottoms out
            # at the `get_element_type()` fallback (Ref forwards element
            # extraction to the wrapped, which returns None for class /
            # Own / Optional / etc.), bypassing all checks on method params.
            _recurse(typ.wrapped)
        elif isinstance(typ, TupleType):
            # Recurse element-wise. Without this branch, tuple element types
            # (e.g. `Own[Optional[Polymorphic]]` nested in `tuple[..., ...]`)
            # bypass every recursive rejection in this function, because
            # `TupleType.get_element_type()` returns None and the
            # `NominalType` container-element fallback at the tail of
            # this elif chain never fires for tuples.
            for member in typ.element_types:
                _recurse(member)
        elif isinstance(typ, OwnType):
            _recurse(typ.wrapped)
            # Reject `Own[Optional[Polymorphic]]`: an Own-Optional slot of
            # a polymorphic class is laid out for the base only, so a
            # derived value stored into it would slice, and isinstance
            # dispatch has no valid pointer lowering. `is_polymorphic_class_type`
            # returns False for unregistered records (forward-ref case),
            # so this gate naturally defers to the post-registration
            # revalidation pass like the Optional[@dynamic] check above.
            opt = unwrap_readonly(typ.wrapped)
            if isinstance(opt, OptionalType):
                opt_inner = unwrap_readonly(opt.inner)
            else:
                opt_inner = None
            if (isinstance(opt, OptionalType)
                    and isinstance(opt_inner, NominalType)
                    and is_polymorphic_class_type(opt_inner, self.ctx.registry)):
                cls = opt_inner.name
                raise SemanticError(
                    f"`Own[Optional[{cls}]]` is not yet supported: an "
                    f"Own-Optional slot of a polymorphic class is laid "
                    f"out for `{cls}` only, so storing a derived class "
                    f"into it would slice the dynamic type and isinstance "
                    f"dispatch on the slot has no valid lowering today. "
                    f"Use `Optional[{cls}]` (borrowed pointer that may "
                    f"be None) or `Box[{cls}]` (owned, polymorphism-"
                    f"preserving) instead.",
                    loc,
                )
        elif isinstance(typ, (ReadonlyType, AutoReadonlyType)):
            # readonly is a transparent same-position wrapper, so preserve
            # allow_pointer_repr_dynamic across it: readonly[Optional[@dynamic P]]
            # at a parameter is still a pointer-repr borrow (const P*), unlike
            # the container/Own/tuple positions where _recurse resets the flag.
            self.validate_type(
                typ.wrapped, allow_type_param_ref, loc,
                allow_forward_ref=allow_forward_ref,
                check_hashable_constraints=check_hashable_constraints,
                allow_pointer_repr_dynamic=allow_pointer_repr_dynamic,
            )
        elif isinstance(typ, AutoOwnType):
            # auto_own[T] is stripped before sema body analysis, but
            # registration may still call validate_type on a freshly-parsed
            # return type before the strip pass runs. Mirror the AutoReadonly
            # branch so any pre-strip residue is still validated.
            _recurse(typ.wrapped)
        elif isinstance(typ, (FinalType, ClassVarType)):
            # `Final[T]` / `ClassVar[T]` annotations. These are stripped
            # during sema body analysis, but variable type annotations are
            # validated BEFORE the strip (see `statements.py::_analyze_var_decl`),
            # so an unrecognized `Final[Unknown]` would otherwise fall
            # through to the tail `get_element_type` fallback as a no-op.
            _recurse(typ.wrapped)
        elif isinstance(typ, PtrType):
            _recurse(typ.pointee)
            if is_protocol_type(typ.pointee):
                # Only static protocols are rejected. @dynamic protocols
                # carry a runtime vtable, so `Ptr[P]` for @dynamic P is a
                # well-defined non-owning protocol reference. If
                # protocol_info_of is None here, the protocol isn't yet
                # registered (record fields validate before protocols);
                # defer to validate_record_field_protocols' re-pass,
                # which runs post-registration and re-fires this check.
                proto_def = protocol_info_of(typ.pointee)
                if proto_def is not None and not proto_def.is_dynamic:
                    raise SemanticError(
                        f"Static protocol type '{typ.pointee}' cannot be used as a pointer element type "
                        f"(only @dynamic protocols, which carry a runtime vtable, can)",
                        loc,
                    )
        elif (elem_type := typ.get_element_type()) is not None:
            _recurse(elem_type)
            if is_protocol_type(elem_type):
                raise SemanticError(
                    f"Protocol type '{elem_type}' cannot be used as a container element type",
                    loc,
                )

    def validate_hashable_container_elem(
        self, elem: TpyType, kind: Literal["set element", "dict key"],
        loc: SourceLocation | None,
    ) -> None:
        """Validate that `elem` can be used as a set element / dict key.

        `kind` is "set element" or "dict key" and feeds into diagnostics.
        Hash-table-backed ordered_set / ordered_map store entries in
        `std::pair<const K, ...>`, which requires copy-constructible K;
        @nocopy types are rejected first with a precise message so the
        user doesn't hit a wall of C++ template errors from container
        internals. Hashability is the second gate: a non-hashable K
        otherwise fails C++ build with `static_assert(__is_invocable<
        const _Hash&, const _Key&>{})`.

        Called both from annotation-time validation (validate_type, above)
        and from literal/comprehension expression analysis (4 callers in
        sema/expressions.py).
        """
        if isinstance(elem, OwnType):
            elem = elem.wrapped
        if isinstance(elem, IntLiteralType):
            return
        if is_enum_type(elem):
            return
        if isinstance(elem, PendingViewType):
            return
        if self.ctx.is_type_non_copyable(elem):
            raise SemanticError(nocopy_container_elem_error(elem, kind), loc)
        # Need BOTH Hashable AND Equatable -- ordered_set/ordered_map use
        # std::hash (requires __hash__) and std::equal_to (requires __eq__).
        # Conformance walks the inheritance chain, so `class Child(Base)`
        # where Base defines either dunder is accepted via Base.
        hashable_ok = self.protocols.type_conforms_to_protocol(elem, _HASHABLE)
        equatable_ok = self.protocols.type_conforms_to_protocol(elem, _EQUATABLE)
        if hashable_ok and equatable_ok:
            return
        # User-record specific guidance: tell the user exactly which dunder
        # is missing instead of the generic "not hashable" message.
        if isinstance(elem, NominalType) and elem.is_user_record:
            missing = []
            if not hashable_ok:
                missing.append("__hash__")
            if not equatable_ok:
                missing.append("__eq__")
            missing_str = " and ".join(missing)
            pronoun = "them" if len(missing) > 1 else "it"
            raise SemanticError(
                f"Type '{elem}' cannot be used as a {kind} "
                f"(missing {missing_str}; "
                f"use @dataclass(frozen=True) or define {pronoun} explicitly)",
                loc,
            )
        raise SemanticError(
            f"Type '{elem}' cannot be used as a {kind} (not hashable)", loc,
        )

    def validate_record_type_args(
        self, typ: NominalType, record_info: RecordInfo, allow_type_param_ref: bool = False,
        loc: SourceLocation | None = None,
        allow_forward_ref: bool = True, check_hashable_constraints: bool = True,
    ) -> None:
        """Validate that type arguments match their expected kinds (TYPE vs INT).

        Args:
            typ: The NominalType with type_args to validate.
            record_info: The RecordInfo with type_param_kinds.
            allow_type_param_ref: If True, allow TypeParamRef as valid types.
            loc: Optional source location for error messages.
            allow_forward_ref / check_hashable_constraints: forwarded to the
                recursive `validate_type` calls so wrapper-form type args
                (`MyGeneric[set[Forward]]`) honor the outer caller's deferral.
        """
        # Same closure pattern as `validate_type` -- one binding so flag
        # forwarding can't drift across the two recursive type-arg sites.
        def _recurse(inner: TpyType) -> None:
            self.validate_type(
                inner, allow_type_param_ref, loc,
                allow_forward_ref=allow_forward_ref,
                check_hashable_constraints=check_hashable_constraints,
            )

        if not record_info.type_param_kinds:
            # Legacy: no kinds specified, assume all TYPE
            for arg in typ.type_args:
                if isinstance(arg, TpyType):
                    _recurse(arg)
                else:
                    raise SemanticError(
                        f"Record '{typ.name}' does not accept integer type arguments",
                        loc,
                    )
            return

        for i, (arg, kind) in enumerate(zip(typ.type_args, record_info.type_param_kinds)):
            param_name = record_info.type_params[i]
            if kind == TypeParamKind.INT:
                # Expect an integer value or INT TypeParamRef (forwarding)
                if isinstance(arg, int):
                    continue  # Valid: integer literal
                if isinstance(arg, TypeParamRef) and arg.kind == TypeParamKind.INT:
                    continue  # Valid: forwarding an INT type param
                raise SemanticError(
                    f"Type parameter '{param_name}' of '{typ.name}' requires an integer, "
                    f"got {arg}",
                    loc,
                )
            else:
                # Expect a type value
                if isinstance(arg, int):
                    raise SemanticError(
                        f"Type parameter '{param_name}' of '{typ.name}' requires a type, "
                        f"got integer {arg}",
                        loc,
                    )
                if isinstance(arg, TpyType):
                    _recurse(arg)
                else:
                    raise SemanticError(
                        f"Invalid type argument for '{param_name}' of '{typ.name}': {arg}",
                        loc,
                    )

    def substitute_param_type(self, typ: TpyType, subst: dict[str, TpyType | int]) -> TpyType:
        """Substitute a PARAMETER slot's type.

        A generic `T | None` parameter is not committed to the template's `T*`:
        it is spelled as a runtime trait, so each instantiation takes the form
        the monomorphic twin would take -- the value form at a value T, the
        pointer form at a reference one. Returns are still committed, so they
        go through `substitute_type_params` and keep its pointer stamp.
        """
        resolved = self.substitute_type_params(typ, subst)
        # Readonly is the only wrapper an Optional param carries: make_ref is a
        # no-op over an OptionalType, so there is no Ref layer to peel.
        declared = unwrap_readonly(typ)
        if not (isinstance(declared, OptionalType)
                and declared.uses_generic_param_trait()):
            return resolved
        if isinstance(resolved, ReadonlyType):
            return ReadonlyType(strip_template_repr(resolved.wrapped))
        return strip_template_repr(resolved)

    def substitute_type_params(self, typ: TpyType, subst: dict[str, TpyType | int]) -> TpyType:
        """Substitute type parameters with concrete types.

        Args:
            typ: The type containing potential TypeParamRef instances.
            subst: Mapping from type parameter names to concrete types or integers.

        Returns:
            The type with all TypeParamRef instances replaced by their concrete types.
        """
        # PERF TODO: candidate for memoization by (typ, frozen(subst)) if profile
        # shows this is hot. Frozen dataclass types are hashable. Needs profiling
        # data post-mypyc before acting; not a top hotspot in current profiles.
        if isinstance(typ, TypeParamRef):
            if typ.name in subst:
                replacement = subst[typ.name]
                if isinstance(replacement, int):
                    # INT type params: keep as TypeParamRef for expression contexts (codegen uses the name)
                    # Type-level substitution for Array etc. is handled below
                    return typ
                return replacement
            raise SemanticError(f"Unknown type parameter '{typ.name}'")
        # Special handling for OptionalType: preserve T* repr from the template.
        # When the template used T* (unbounded TypeParamRef -> not a value type),
        # but the concrete type after substitution is a value type, force pointer
        # repr so the caller matches the template's representation.
        if isinstance(typ, OptionalType) and typ.uses_pointer_repr():
            new_inner = self.substitute_type_params(typ.inner, subst)
            if new_inner.is_value_type():
                return OptionalType(new_inner, force_pointer_repr=True)
            return OptionalType(new_inner)
        # Special handling for Array: substitute size if it's a TypeParamRef
        if is_array(typ):
            elem, size = typ.type_args[0], typ.type_args[1]
            new_elem = self.substitute_type_params(elem, subst)
            new_size = size
            if isinstance(size, TypeParamRef) and size.name in subst:
                new_size = subst[size.name]
            if new_elem != elem or new_size != size:
                return make_array(new_elem, new_size)
            return typ
        # NominalType (user records and module-defined generics like
        # UninitArrayStorage[T, N]) can have mixed TpyType/int type_args.
        # map_inner_types operates on TpyType -> TpyType, so it can't
        # substitute int-valued TypeParamRefs.  Same reason as Array above
        # needs direct handling.  Exact type check avoids catching NominalType
        # subclasses which don't have int args.
        if type(typ) is NominalType and typ.type_args:
            new_args: list[TpyType | int] = []
            changed = False
            for arg in typ.type_args:
                if isinstance(arg, TypeParamRef) and arg.name in subst:
                    new_args.append(subst[arg.name])
                    changed = True
                elif isinstance(arg, TpyType):
                    new_arg = self.substitute_type_params(arg, subst)
                    new_args.append(new_arg)
                    if new_arg is not arg:
                        changed = True
                else:
                    new_args.append(arg)  # already-concrete int value
            if changed:
                return NominalType(typ.name, tuple(new_args), typ.is_protocol,
                                 typ._module_qname, typ.is_dynamic_protocol)
            return typ
        # Use map_inner_types for types that have inner types
        result = typ.map_inner_types(lambda t: self.substitute_type_params(t, subst))
        # A pointee is a bare-T slot: a Ref bound from inference must not
        # nest as Ptr[Ref[T]] (see to_bare_slot_form).
        if isinstance(result, PtrType) and isinstance(result.pointee, RefType):
            result = PtrType(to_bare_slot_form(result.pointee), result.is_readonly)
        return result

    def build_type_substitution(self, record_type: TpyType) -> dict[str, TpyType | int]:
        """Build a type parameter substitution map for a generic record instantiation.

        Works for all types: user records and module types (NominalType) use
        RecordInfo.type_params + type_args; non-NominalType builtins (
        etc.) use extract_type_params from the module system.

        Returns:
            Mapping from type parameter names to concrete types or integers.
            For example: {"T": Int32, "N": 8} for Matrix[Int32, 8].
        """
        if isinstance(record_type, NominalType):
            record_info = self.ctx.registry.get_record_for_type(record_type)
            if not record_info or not record_info.is_generic():
                return {}
            if not record_type.type_args:
                return {}
            return dict(zip(record_info.type_params, record_type.type_args))
        return builtin_modules.extract_type_params(record_type)

    def substitute_types(self, typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
        """Recursively substitute types throughout a type structure.

        Substitutes SelfType and TypeParamRef according to the substitution map.
        Handles nested types like Own[Self], Ptr[T], list[T], etc.
        Uses map_inner_types for generic traversal of wrapper types.
        """
        if isinstance(typ, SelfType) and "Self" in subst:
            return subst["Self"]
        if isinstance(typ, TypeParamRef) and typ.name in subst:
            return subst[typ.name]
        return typ.map_inner_types(lambda t: self.substitute_types(t, subst))

    def substitute_self(self, typ: TpyType, actual: TpyType) -> TpyType:
        """Recursively substitute SelfType with actual type throughout a type structure.

        Handles nested types like Own[Self], Ptr[Self], list[Self], etc.
        Uses map_inner_types for generic traversal of wrapper types.
        """
        return self.substitute_types(typ, {"Self": actual})

    def is_forwarded_type_param(self, typ: TpyType, type_params: list[str]) -> bool:
        """Check if a type references one of the given type parameters.

        At parse time, type parameters in base class type args appear as NominalType
        (e.g., Container[T] has T as NominalType("T"), not TypeParamRef("T")).
        This function checks for both forms.
        """
        if isinstance(typ, TypeParamRef):
            return typ.name in type_params
        if isinstance(typ, NominalType):
            # A NominalType with no type_args and name matching a type param is a forwarded param
            if not typ.type_args and typ.name in type_params:
                return True
            # Also check nested type args (e.g., Container[list[T]] or Parent[Sequence[T]])
            for type_arg in typ.type_args:
                if isinstance(type_arg, TpyType) and self.is_forwarded_type_param(type_arg, type_params):
                    return True
        if isinstance(typ, PtrType):
            return self.is_forwarded_type_param(typ.pointee, type_params)
        if isinstance(typ, OwnType):
            return self.is_forwarded_type_param(typ.wrapped, type_params)
        if isinstance(typ, ReadonlyType):
            return self.is_forwarded_type_param(typ.wrapped, type_params)
        return False

    def get_type_param_bound(self, type_param_name: str) -> TpyType | None:
        """Look up the bound for a type parameter from current context.

        Checks current function's type_param_bounds first, then record's.
        Returns None if no bound is declared.
        """
        # Check current function's type param bounds
        if (self.ctx.func.current_function and isinstance(self.ctx.func.current_function, TpyFunction)
                and type_param_name in self.ctx.func.current_function.type_param_bounds):
            return self.ctx.func.current_function.type_param_bounds[type_param_name]
        # Check current record's type param bounds (for methods in generic classes)
        if (self.ctx.record_ctx.type_param_bounds
                and type_param_name in self.ctx.record_ctx.type_param_bounds):
            return self.ctx.record_ctx.type_param_bounds[type_param_name]
        return None

    def match_type_with_inference(
        self,
        param_type: TpyType,
        arg_type: TpyType,
        inferred: dict[str, TpyType]
    ) -> bool:
        """Match param_type against arg_type, collecting type param inferences.

        Returns True if types match (with inference), False otherwise.
        """
        # Send/Sync markers are sema-only slot assertions enforced at the
        # compatibility check, not here -- peel both sides so a marker never
        # blocks a match or binds into an inferred type param.
        if isinstance(param_type, (SendType, SyncType)):
            param_type = unwrap_send_sync(param_type)
        if isinstance(arg_type, (SendType, SyncType)):
            arg_type = unwrap_send_sync(arg_type)

        # RefType wrapper on param: strip Ref from param side and also strip
        # from arg if present (Ref[T] param accepts both T and Ref[T] args).
        if isinstance(param_type, RefType):
            return self.match_type_with_inference(param_type.wrapped, unwrap_ref_type(arg_type), inferred)
        # Ref on arg side is preserved for type param inference -- this lets
        # map(identity, pts) infer U=Ref[Point] from identity's return type,
        # so the template emits val_or_ref<Point> for reference preservation.

        # ReadonlyType wrapper: unwrap for matching (readonly[T] accepts T and readonly[T])
        if isinstance(param_type, ReadonlyType):
            arg_unwrapped = unwrap_readonly(arg_type)
            return self.match_type_with_inference(param_type.wrapped, arg_unwrapped, inferred)

        # Own[T] wrapper on argument -- unwrap before matching.
        # Own is an ownership marker (e.g. from copy()), not a distinct type.
        # Remember it was explicitly owned: an Own[T] param binding such an arg
        # may strip an incidental inner borrow (see the Own[T] param branch).
        arg_was_owned = isinstance(arg_type, OwnType)
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped

        # TypeParamRef -- infer or check consistency
        if isinstance(param_type, TypeParamRef):
            if param_type.name in inferred:
                existing = inferred[param_type.name]
                # UnknownElementType is a "no info" placeholder from an empty
                # container literal. Either side can be it; the concrete side
                # wins so `f([1], [])` and `f([], [1])` infer T identically.
                if isinstance(existing, UnknownElementType):
                    inferred[param_type.name] = arg_type
                    return True
                if isinstance(arg_type, UnknownElementType):
                    return True
                if isinstance(existing, IntLiteralType) and is_integer_type(arg_type):
                    inferred[param_type.name] = arg_type
                    return True
                if isinstance(existing, FloatLiteralType) and is_float_type(arg_type):
                    inferred[param_type.name] = arg_type
                    return True
                # Ref[T] matches bare T: the same type param can be inferred
                # as bare T (from iterables) and Ref[T] (from function returns).
                # Accept both directions without conflict.
                if isinstance(arg_type, RefType) and arg_type.wrapped == existing:
                    return True
                if isinstance(existing, RefType) and existing.wrapped == arg_type:
                    return True
                return self.types_match_for_inference(existing, arg_type)
            inferred[param_type.name] = arg_type
            return True

        # Protocol with TypeParamRef in type_args (e.g., NativeIterable[T],
        # NativeIterable[tuple[K, V]]) -- TypeParamRefs may be nested
        if is_protocol_type(param_type) and param_type.type_args:
            if any(isinstance(t, TpyType) and contains_type_param(t)
                   for t in param_type.type_args):
                return self._match_protocol_type_args_with_inference(param_type, arg_type, inferred)

        # PtrType with TypeParamRef pointee (e.g., Ptr[T], Ptr[readonly[T]])
        if isinstance(param_type, PtrType):
            if not isinstance(arg_type, PtrType):
                return False
            if arg_type.is_readonly and not param_type.is_readonly:
                return False
            return self.match_type_with_inference(
                param_type.pointee, arg_type.pointee, inferred
            )

        # list[T] with nested TypeParamRef (e.g., list[T])
        if is_list(param_type):
            if is_list(arg_type):
                return self.match_type_with_inference(
                    param_type.type_args[0], arg_type.type_args[0], inferred
                )
            if isinstance(arg_type, PendingListType):
                return self.match_type_with_inference(
                    param_type.type_args[0], arg_type.element_type, inferred
                )
            return False

        # TupleType with TypeParamRef elements (e.g., tuple[T, T])
        if isinstance(param_type, TupleType):
            if not isinstance(arg_type, TupleType):
                return False
            if len(param_type.element_types) != len(arg_type.element_types):
                return False
            return all(
                self.match_type_with_inference(p, a, inferred)
                for p, a in zip(param_type.element_types, arg_type.element_types)
            )

        # Array with TypeParamRef element or size (e.g., Array[T, N])
        if is_array(param_type):
            return self._match_array_with_inference(param_type, arg_type, inferred)

        # Span: recurse on element types directly so that readonly propagates into T.
        # Span[T] accepts both Span[X] (T=X) and Span[readonly[X]] (T=readonly[X]).
        # Done explicitly (not via the generic NominalType branch) so that readonly
        # and _match_record_with_inference would reject by name if it treated spans differently.
        if is_span(param_type):
            if is_span(arg_type):
                return self.match_type_with_inference(
                    param_type.type_args[0], arg_type.type_args[0], inferred
                )
            return False

        # NominalType (record) with type args (e.g., Box[T] nested)
        if isinstance(param_type, NominalType) and param_type.is_record and param_type.type_args:
            return self._match_record_with_inference(param_type, arg_type, inferred)

        # (PtrType handled above, before list)

        # Optional[T] -- unwrap and recurse (bare T can coerce to Optional[T])
        if isinstance(param_type, OptionalType):
            if isinstance(arg_type, OptionalType):
                inner_arg = arg_type.inner
            else:
                # A bare arg widening into the Optional slot: a Ref borrow
                # marker on it is incidental (a sema-NARROWED Optional field
                # read analyzes as Ref[T]) -- the slot's payload is by-value
                # storage, so binding the param to Ref[T] would emit a
                # val_or_ref<T> template arg against the T* the arg render
                # produces. The bare-T reference-preservation rule (Ref kept
                # for a bare TypeParamRef param) is unaffected.
                inner_arg = unwrap_ref_type(arg_type)
            return self.match_type_with_inference(
                param_type.inner, inner_arg, inferred
            )

        # CallableType with TypeParamRef in param/return (e.g. Fn[[T], U] or Callable[[T], U])
        if is_callable_type(param_type):
            if not is_callable_type(arg_type):
                return False
            if len(param_type.param_types) != len(arg_type.param_types):
                return False
            for pp, ap in zip(param_type.param_types, arg_type.param_types):
                if not self.match_type_with_inference(pp, ap, inferred):
                    return False
            return self.match_type_with_inference(
                param_type.return_type, arg_type.return_type, inferred)

        # Own[T] wrapper -- unwrap and match T against arg (any type can be owned).
        # arg_type has already had a top-level Own stripped above.
        if isinstance(param_type, OwnType):
            if arg_was_owned:
                # An explicitly-owned arg (copy() / a fresh constructor result)
                # holds the storage form, so an inner borrow is incidental:
                # canonicalize Own[Ref[T]] (e.g. copy() of a subscript) to T
                # rather than binding the param to Ref[T].
                inner_arg = to_owned_storage_form(arg_type)
            else:
                # A bare arg keeps its Ref: a val_or_ref value passed into an
                # Own[T] param is an implicit copy whose reference form the
                # copy-warning / reference-passing path still needs.
                inner_arg = unwrap_readonly(arg_type)
            return self.match_type_with_inference(
                param_type.wrapped, inner_arg, inferred
            )

        # Generic recursive alias instance (e.g. Tree[T]): match alias identity,
        # then recurse on type args to infer the params.
        if isinstance(param_type, RecursiveAliasInstanceType):
            if (not isinstance(arg_type, RecursiveAliasInstanceType)
                    or param_type.qname != arg_type.qname
                    or len(param_type.type_args) != len(arg_type.type_args)):
                return False
            return all(
                self.match_type_with_inference(p, a, inferred)
                for p, a in zip(param_type.type_args, arg_type.type_args)
            )

        # None literal (NoneType) matches None annotation (VoidType)
        if isinstance(param_type, VoidType) and isinstance(arg_type, NoneType):
            return True

        # Concrete type -- check compatibility
        if self.types_match_for_inference(param_type, arg_type):
            return True

        # For concrete parameters in generic inference, allow the same
        # argument coercions used by normal call checking (e.g. Int32 -> Int64).
        arg_unwrapped = unwrap_readonly(arg_type)
        return resolve_coercion(arg_unwrapped, param_type, CoercionContext.ARG) is not None

    def _match_protocol_type_args_with_inference(
        self,
        param_type: TpyType,
        arg_type: TpyType,
        inferred: dict[str, TpyType],
    ) -> bool:
        """Match a protocol with TypeParamRef type_args against arg_type (e.g., NativeIterable[T], Iterator[T])."""
        # Mirror classify_protocol_conformance: conformance is on the
        # underlying record, not its Ref/readonly/Own wrapper.
        arg_type = unwrap_qualifiers(arg_type)
        # GenExprType satisfies Iterable[T] and Iterator[T];
        # CopyIter/OwnIter satisfy Iterable[T] only.
        if ((isinstance(arg_type, GenExprType) and param_type.qualified_name() in (qnames.ITERABLE, qnames.ITERATOR))
                or ((is_copy_iter(arg_type) or is_own_iter(arg_type)) and param_type.qualified_name() == qnames.ITERABLE)):
            if len(param_type.type_args) == 1:
                elem = arg_type.element_type if isinstance(arg_type, GenExprType) else arg_type.type_args[0]
                return self.match_type_with_inference(param_type.type_args[0], elem, inferred)
            return True

        protocol_name = param_type.name
        # Resolve the protocol's OWN params positionally (aligned to the
        # protocol's type_params) from one of: the arg protocol's type_args,
        # an extends declaration (single-valued -- multi-param extends is not
        # resolved here), or structural inference from the conformer's methods.
        is_iterator = param_type.qualified_name() == "typing.Iterator"
        same_protocol = (
            is_protocol_type(arg_type) and arg_type.type_args
            and arg_type.qualified_name() == ("typing.Iterator" if is_iterator
                                              else param_type.qualified_name()))
        if same_protocol:
            elem_types: list[TpyType | None] | None = list(arg_type.type_args)
        else:
            ext = builtin_modules.get_extends_protocol_type_arg(
                arg_type, protocol_name, registry=self.ctx.registry)
            elem_types = [ext] if ext is not None else \
                self._infer_protocol_type_arg_structurally(arg_type, protocol_name)
        if not elem_types:
            return False
        # Single type_arg: recursive matching handles compound types like
        # NativeIterable[tuple[K, V]] where the type_arg is a TupleType.
        if len(param_type.type_args) == 1:
            elem = elem_types[0]
            return elem is not None and self.match_type_with_inference(
                param_type.type_args[0], elem, inferred)
        # Multi-param: bind each bound type_arg to its matching protocol param
        # positionally. A None entry (protocol param the conformer didn't pin)
        # leaves that bound arg unresolved for the caller's reject loop; but a
        # conformer that pins NOTHING (every entry None) provided no evidence,
        # so fail like the single-param gate above rather than matching vacuously.
        bound_any = False
        for ta, elem in zip(param_type.type_args, elem_types):
            if elem is None:
                continue
            bound_any = True
            if isinstance(ta, TypeParamRef):
                if ta.name in inferred:
                    if not self.types_match_for_inference(inferred[ta.name], elem):
                        return False
                else:
                    inferred[ta.name] = elem
            elif not self.match_type_with_inference(ta, elem, inferred):
                return False
        return bound_any

    def _infer_protocol_type_arg_structurally(
        self, arg_type: TpyType, protocol_name: str,
    ) -> list[TpyType | None] | None:
        """Infer a protocol's type args by matching method signatures structurally.

        Returns the protocol's params resolved positionally (aligned to the
        protocol's own type_params; an entry is None when the conformer's
        signatures don't pin that param), or None when arg_type conforms to
        nothing. Uses match_type_with_inference to unify protocol method
        signatures (containing TypeParamRefs like T) against the record's
        concrete method signatures. Handles all compound return/param types
        (Span, Optional, etc.).

        Also handles protocol-to-protocol inference: when arg_type is a protocol
        (e.g. Iterator[Int32]) matching a different protocol (e.g. Iterable[T]),
        looks up the arg protocol's method signatures and matches them.
        """
        protocol_info = self.ctx.registry.scan_by_short_name(protocol_name)
        if protocol_info is None or not protocol_info.type_params:
            return None

        # Protocol-to-protocol: arg is a protocol with concrete type args
        if is_protocol_type(arg_type) and isinstance(arg_type, NominalType) and arg_type.type_args:
            arg_protocol_info = protocol_info_of(arg_type)
            if arg_protocol_info is not None:
                return self._infer_protocol_type_arg_from_protocol(
                    arg_type, arg_protocol_info, protocol_info)

        record = self.ctx.registry.get_record_for_type(arg_type)
        if record is None:
            return None

        # NominalType carries type_args directly; pending containers
        # (PendingListType etc.) need extract_type_params to surface them.
        type_subst: dict[str, TpyType] = {}
        if record.type_params:
            if isinstance(arg_type, NominalType) and arg_type.type_args:
                type_subst = dict(zip(record.type_params, arg_type.type_args))
            else:
                extracted = builtin_modules.extract_type_params(arg_type)
                type_subst = {tp: extracted[tp] for tp in record.type_params if tp in extracted}

        def _substitute(t: TpyType) -> TpyType:
            # Propagate the record's class type params into nested
            # positions: e.g. for Future[Int32].poll's return type
            # `Poll[T]`, we want `Poll[Int32]`. Recursing here matters
            # for any generic record whose protocol-conforming method
            # returns or accepts a compound type wrapping the class T.
            t = unwrap_ref_type(t)
            if not type_subst:
                return t
            return self.substitute_types(t, type_subst)

        inferred: dict[str, TpyType] = {}
        for method_sig in protocol_info.methods:
            for method_info in self.ctx.registry.get_method_overloads_with_parents(record, method_sig.name):
                if len(method_info.params) != len(method_sig.params):
                    continue
                ret = _substitute(method_info.return_type)
                self.match_type_with_inference(method_sig.return_type, ret, inferred)
                for (_, proto_ptype), (_, record_ptype) in zip(
                    method_sig.params, method_info.params
                ):
                    self.match_type_with_inference(
                        proto_ptype, _substitute(record_ptype), inferred)
                break

        return [inferred.get(p) for p in protocol_info.type_params]

    def _infer_protocol_type_arg_from_protocol(
        self,
        arg_type: NominalType,
        arg_protocol: 'ProtocolInfo',
        target_protocol: 'ProtocolInfo',
    ) -> list[TpyType | None]:
        """Infer the target protocol's type args from an arg protocol's methods.

        Returns the target protocol's params resolved positionally (aligned to
        target_protocol.type_params; None per unpinned param). E.g. Iterator[Int32]
        matching Iterable[T]: looks up Iterator's __iter__ method (returns Self =
        Iterator[Int32]), matches against Iterable's __iter__ (returns Iterator[T])
        to infer T = Int32.
        """
        # Build substitution for arg protocol: resolve Self and type params
        arg_subst: dict[str, TpyType] = {"Self": arg_type}
        if arg_protocol.type_params and arg_type.type_args:
            arg_subst.update(dict(zip(arg_protocol.type_params, arg_type.type_args)))

        inferred: dict[str, TpyType] = {}
        for target_method in target_protocol.methods:
            for arg_method in arg_protocol.methods:
                if arg_method.name != target_method.name:
                    continue
                if len(arg_method.params) != len(target_method.params):
                    continue
                # Substitute arg protocol's type params + Self
                resolved_ret = self.substitute_types(arg_method.return_type, arg_subst)
                self.match_type_with_inference(
                    target_method.return_type, resolved_ret, inferred)
                for (_, target_ptype), (_, arg_ptype) in zip(
                    target_method.params, arg_method.params
                ):
                    resolved_ptype = self.substitute_types(arg_ptype, arg_subst)
                    self.match_type_with_inference(
                        target_ptype, resolved_ptype, inferred)
                break

        return [inferred.get(p) for p in target_protocol.type_params]

    def _match_array_with_inference(
        self,
        param_type: NominalType,
        arg_type: TpyType,
        inferred: dict[str, TpyType],
    ) -> bool:
        """Match Array with TypeParamRef element or size (e.g., Array[T, N])."""
        if not is_array(arg_type):
            return False
        p_elem, p_size = param_type.type_args[0], param_type.type_args[1]
        a_elem, a_size = arg_type.type_args[0], arg_type.type_args[1]
        if not self.match_type_with_inference(p_elem, a_elem, inferred):
            return False
        # Match sizes
        if isinstance(p_size, TypeParamRef):
            if isinstance(a_size, int):
                param_name = p_size.name
                if param_name in inferred:
                    if inferred[param_name] != a_size:
                        return False
                else:
                    inferred[param_name] = a_size
                return True
            if isinstance(a_size, TypeParamRef):
                return p_size.name == a_size.name
            return False
        return p_size == a_size

    def _match_record_with_inference(
        self,
        param_type: NominalType,
        arg_type: TpyType,
        inferred: dict[str, TpyType],
    ) -> bool:
        """Match a generic record NominalType against arg_type (e.g., Box[T])."""
        if not (isinstance(arg_type, NominalType) and arg_type.is_record and arg_type.name == param_type.name):
            return False
        # Two records sharing a short name across modules must not unify: when
        # both qnames are known and differ, they are distinct types even though
        # the short name matches. (Compared here rather than via
        # same_nominal_symbol_loose, which also checks type_args -- those are
        # exactly what inference is solving for, e.g. Box[T] vs Box[Int32].)
        if (arg_type._module_qname is not None and param_type._module_qname is not None
                and arg_type._module_qname != param_type._module_qname):
            return False
        if len(param_type.type_args) != len(arg_type.type_args):
            return False
        for pt, at in zip(param_type.type_args, arg_type.type_args):
            if isinstance(pt, TpyType) and isinstance(at, TpyType):
                if not self.match_type_with_inference(pt, at, inferred):
                    return False
            elif isinstance(pt, TypeParamRef) and pt.kind == TypeParamKind.INT and isinstance(at, int):
                if pt.name in inferred:
                    if inferred[pt.name] != at:
                        return False
                else:
                    inferred[pt.name] = at
            elif isinstance(pt, int) and isinstance(at, int):
                if pt != at:
                    return False
            elif isinstance(pt, TypeParamRef) and isinstance(at, TypeParamRef):
                if pt.name != at.name:
                    return False
            else:
                return False
        return True

    def types_match_for_inference(self, type_a: TpyType, type_b: TpyType) -> bool:
        """Check if two types match for inference consistency.

        Recurses element-wise through tuple/list/dict/set so that the
        literal-coercion rules below apply inside compound shapes -- not
        just at the top level.
        """
        if type_a == type_b:
            return True
        if isinstance(type_a, IntLiteralType) and is_integer_type(type_b):
            return True
        if isinstance(type_b, IntLiteralType) and is_integer_type(type_a):
            return True
        if isinstance(type_a, IntLiteralType) and isinstance(type_b, IntLiteralType):
            return True
        if isinstance(type_a, FloatLiteralType) and is_float_type(type_b):
            return True
        if isinstance(type_b, FloatLiteralType) and is_float_type(type_a):
            return True
        if isinstance(type_a, FloatLiteralType) and isinstance(type_b, FloatLiteralType):
            return True
        if isinstance(type_a, PendingViewType) and isinstance(type_b, PendingViewType):
            return type_a.family is type_b.family
        if isinstance(type_a, PendingViewType):
            return type_b in (type_a.family.owned_type, type_a.family.view_type)
        if isinstance(type_b, PendingViewType):
            return type_a in (type_b.family.owned_type, type_b.family.view_type)
        if isinstance(type_a, TupleType) and isinstance(type_b, TupleType):
            if len(type_a.element_types) != len(type_b.element_types):
                return False
            return all(self.types_match_for_inference(a, b)
                       for a, b in zip(type_a.element_types, type_b.element_types))
        if is_list(type_a) and is_list(type_b):
            return self.types_match_for_inference(type_a.type_args[0], type_b.type_args[0])
        if is_dict(type_a) and is_dict(type_b):
            return (self.types_match_for_inference(type_a.type_args[0], type_b.type_args[0])
                    and self.types_match_for_inference(type_a.type_args[1], type_b.type_args[1]))
        if is_set(type_a) and is_set(type_b):
            return self.types_match_for_inference(type_a.type_args[0], type_b.type_args[0])
        return False

    def infer_type_params_for_function(
        self,
        func: FunctionInfo,
        arg_types: list[TpyType],
        type_conforms_to_protocol: callable,
        expected_return_type: TpyType | None = None,
        explicit_type_args: 'tuple[TpyType | None, ...] | None' = None,
    ) -> dict[str, TpyType] | None:
        """Infer type parameters from function arguments.

        Returns dict of inferred type params (e.g., {"T": Int32}) on success, None on failure.
        If expected_return_type is provided, unresolved params are matched against the return type.
        If explicit_type_args is provided, pre-populates inferred with those (positional).
        """
        if len(arg_types) < func.min_args or len(arg_types) > func.max_args:
            return None

        inferred: dict[str, TpyType] = {}
        if explicit_type_args:
            if len(explicit_type_args) > len(func.type_params):
                return None
            for tp, arg in zip(func.type_params, explicit_type_args):
                if arg is not None:  # None = _ wildcard, skip
                    inferred[tp] = arg
        arg_idx = 0
        for p in func.params:
            if arg_idx >= len(arg_types):
                break
            ptype = p.type
            if p.is_variadic and is_varargs(unwrap_ref_type(ptype)):
                # Variadic param: match each remaining arg against element
                # type. `varargs<T>` elements are bare-T slots, so canonicalize
                # the arg side (see to_bare_slot_form) -- the non-variadic
                # ref-param path gets the equivalent strip via the Ref-vs-Ref
                # rule in match_type_with_inference.
                elem_type = unwrap_readonly(unwrap_ref_type(ptype).type_args[0])
                while arg_idx < len(arg_types):
                    arg_t = to_bare_slot_form(arg_types[arg_idx])
                    if not self.match_type_with_inference(elem_type, arg_t, inferred):
                        return None
                    arg_idx += 1
                continue
            if p.keyword_only:
                # Skip keyword-only params in arg_types matching (they were
                # appended by resolve_kwargs, not from positional args)
                continue
            # @value_ptr_coercion: Ptr[T] params accept T values, so match
            # the arg against the pointee type for inference purposes.
            match_type = ptype
            if func.value_ptr_coercion and isinstance(ptype, PtrType):
                match_type = ptype.pointee
            if not self.match_type_with_inference(match_type, arg_types[arg_idx], inferred):
                return None
            arg_idx += 1

        # Fallback: infer remaining params from expected return type
        if expected_return_type is not None:
            unresolved = [tp for tp in func.type_params if tp not in inferred]
            if unresolved:
                ret = func.return_type.wrapped if isinstance(func.return_type, OwnType) else func.return_type
                exp = expected_return_type.wrapped if isinstance(expected_return_type, OwnType) else expected_return_type
                self.match_type_with_inference(ret, exp, inferred)

        # LHS-hint @dynamic-protocol preference for function-call inference;
        # mirrors the equivalent block in `infer_type_params_for_record`.
        if expected_return_type is not None and func.return_type is not None:
            self._apply_lhs_hint_to_function_return(
                func.return_type, expected_return_type, inferred
            )

        # Associated-type inference: a param still unresolved after arg
        # matching may appear only inside a sibling's generic-protocol bound
        # (`T: ThreadTask[R]` -- T inferred, R not). Solve it by unifying the
        # protocol's member signatures against the inferred conformer, the
        # same machinery protocol-typed params use. Gated to bounds that
        # still name an unresolved param, so it cannot change the outcome of
        # a call that already infers.
        # MUST run before the resolve-pending loop below: a `-> None` conformer
        # binds a sibling to VoidType, which that loop canonicalizes to NONE so
        # it matches an explicit `None` type arg -- reordering silently
        # mis-instantiates the fire-and-forget case.
        if func.type_param_bounds:
            unresolved = {tp for tp in func.type_params if tp not in inferred}
            if unresolved:
                for carrier, bound in func.type_param_bounds.items():
                    if not (isinstance(bound, NominalType)
                            and is_protocol_type(bound) and bound.type_args):
                        continue
                    if not any(isinstance(ta, TpyType)
                               and contains_type_param(ta, unresolved)
                               for ta in bound.type_args):
                        continue
                    conformer = inferred.get(carrier)
                    if conformer is None or isinstance(conformer, UnknownElementType):
                        continue
                    self._match_protocol_type_args_with_inference(
                        bound, conformer, inferred)

        # Resolve pending types for codegen.
        for k, v in list(inferred.items()):
            if isinstance(v, IntLiteralType):
                inferred[k] = self.ctx.default_int_for_literal(v)
            elif isinstance(v, VoidType):
                # A `-> None` conformer method binds VoidType; the explicit
                # `None` type arg spells NoneType -- canonicalize so both
                # forms produce the same instantiation.
                inferred[k] = NONE
            elif isinstance(v, PendingListType):
                # Resolve PendingListType to list
                elem_type = v.element_type
                if isinstance(elem_type, IntLiteralType):
                    elem_type = self.ctx.default_int_for_literal(elem_type)
                inferred[k] = make_list(elem_type)

        # An empty container literal pins T to UnknownElementType but
        # carries no real evidence -- treat it like an absent arg so the
        # @type_param_default fallback applies.
        if func.type_param_defaults:
            for tp in func.type_params:
                if tp not in func.type_param_defaults:
                    continue
                current = inferred.get(tp)
                if current is not None and not isinstance(current, UnknownElementType):
                    continue
                sentinel = func.type_param_defaults[tp]
                if sentinel == qnames.DEFAULT_INT:
                    inferred[tp] = self.ctx.default_int_type
                else:
                    raise ValueError(f"Unknown type_param_default sentinel: {sentinel!r}")

        # `U: T` with U arg-inferred but T unconstrained: default T to U.
        # Sound because U trivially satisfies its own bound when used as T,
        # and gives hint-free bounded-factory calls (`x = f(v)` where f is
        # `def f[U: T](v: Own[U]) -> Own[Box[T]]`) a defined inference
        # outcome instead of "Cannot infer T". Multi-step chains
        # (`V: U`, `U: T`) iterate to a fixed point.
        if func.type_param_bounds:
            changed = True
            while changed:
                changed = False
                for u_name, bound in func.type_param_bounds.items():
                    if not isinstance(bound, TypeParamRef):
                        continue
                    t_name = bound.name
                    if t_name in inferred:
                        continue
                    src = inferred.get(u_name)
                    if src is not None and not isinstance(src, UnknownElementType):
                        inferred[t_name] = src
                        changed = True

        # Reject leftover UnknownElementType: letting it ride through
        # substituted param types surfaces later as the indirect
        # pending-list resolver error; failing here lets the caller emit
        # a clean "Cannot infer type arguments" diagnostic instead.
        for tp in func.type_params:
            if tp not in inferred:
                return None
            if isinstance(inferred[tp], UnknownElementType):
                return None

        # Substitute already-inferred params into the bound before validating
        # so a bound that names another param (`U: T`) is checked against the
        # resolved form, not the raw type-param.
        for param_name, type_arg in inferred.items():
            if param_name in func.type_param_bounds:
                bound = self.substitute_type_params(func.type_param_bounds[param_name], inferred)
                if not type_conforms_to_protocol(type_arg, bound):
                    # Return None to signal inference failure (allows overload resolution to try other candidates)
                    return None

        return inferred

    def infer_type_params_for_record(
        self,
        record: RecordInfo,
        arg_types: list[TpyType],
        expected_type: TpyType | None = None,
        explicit_type_args: tuple['TpyType | None', ...] | None = None,
    ) -> dict[str, TpyType] | None:
        """Infer type parameters from constructor arguments for user-defined generic record.

        Returns dict of inferred type params (e.g., {"T": Int32}) on success, None on failure.
        If expected_type is provided, unresolved params are matched against the record type pattern.
        If explicit_type_args is provided, pre-populates inferred with those (positional, None = skip).
        """
        min_args = sum(1 for _, _, default in record.init_params if default is None)
        if len(arg_types) < min_args or len(arg_types) > len(record.init_params):
            return None

        inferred: dict[str, TpyType] = {}
        if explicit_type_args:
            if len(explicit_type_args) > len(record.type_params):
                return None
            for tp, arg in zip(record.type_params, explicit_type_args):
                if arg is not None:
                    inferred[tp] = arg
        for (pname, ptype, _), arg_type in zip(record.init_params, arg_types):
            # A record field is owned storage, so a constructor arg binds the
            # storage form -- a borrowed element (Ref[T]) must not leak into the
            # record's type argument (Record[Ref[T]]).
            if not self.match_type_with_inference(
                    ptype, to_owned_storage_form(arg_type), inferred):
                return None

        # Fallback: infer remaining params from expected type
        if expected_type is not None:
            unresolved = [tp for tp in record.type_params if tp not in inferred]
            if unresolved:
                exp = expected_type.wrapped if isinstance(expected_type, OwnType) else expected_type
                record_pattern = NominalType(record.name, tuple(TypeParamRef(tp) for tp in record.type_params),
                                              _module_qname=record.qualified_name())
                self.match_type_with_inference(record_pattern, exp, inferred)

        # Verify all type params were inferred
        for tp in record.type_params:
            if tp not in inferred:
                return None

        # LHS-hint @dynamic-protocol preference (structural conformer -> Adapter
        # wrap at call site). See `_apply_dyn_hint_at_position` for the gate.
        if expected_type is not None:
            exp = unwrap_qualifiers(expected_type)
            if (isinstance(exp, NominalType)
                    and exp.qualified_name() == record.qualified_name()
                    and len(exp.type_args) == len(record.type_params)):
                for tp, hint_t in zip(record.type_params, exp.type_args):
                    self._apply_dyn_hint_at_position(tp, hint_t, inferred)

        return inferred

    def _apply_dyn_hint_at_position(
        self, tp_name: str, hint_t: TpyType, inferred: dict[str, TpyType]
    ) -> None:
        # Skip if the inferred concrete inherits the hint protocol -- Covariant[T]
        # uplift handles it. Otherwise generic records that allocate with raw
        # unsafe_alloc (e.g. Tagged in covariant_custom) would break since
        # unsafe_alloc can't allocate sizeof(abstract_base).
        if tp_name not in inferred:
            return
        if not (isinstance(hint_t, NominalType) and is_dyn_protocol(hint_t)):
            return
        inferred_t = inferred[tp_name]
        if isinstance(inferred_t, NominalType) and is_dyn_protocol(inferred_t):
            return
        if self._inherits_protocol(inferred_t, hint_t):
            return
        inferred[tp_name] = hint_t

    def _apply_lhs_hint_to_function_return(
        self, ret_pattern: TpyType, hint: TpyType, inferred: dict[str, TpyType]
    ) -> None:
        # Recurse so nested generics (e.g. `Own[List[Wrapper[T]]]`) get the
        # switch too, not just the top-level wrapper.
        ret_pattern = unwrap_qualifiers(ret_pattern)
        hint = unwrap_qualifiers(hint)
        if not (isinstance(ret_pattern, NominalType) and isinstance(hint, NominalType)):
            return
        if ret_pattern.qualified_name() != hint.qualified_name():
            return
        if len(ret_pattern.type_args) != len(hint.type_args):
            return
        for r_arg, h_arg in zip(ret_pattern.type_args, hint.type_args):
            if isinstance(r_arg, TypeParamRef):
                self._apply_dyn_hint_at_position(r_arg.name, h_arg, inferred)
            else:
                self._apply_lhs_hint_to_function_return(r_arg, h_arg, inferred)

    def _seed_subst(
        self, pattern: TpyType, hint: TpyType,
    ) -> dict[str, TpyType]:
        """Match a TPRef-bearing ``pattern`` against ``hint`` and return the
        useful inferred bindings.

        Internal helper shared by ``seed_subst_from_return_hint`` (function /
        method calls; pattern = ``func.return_type``) and
        ``seed_subst_from_record_pattern`` (record constructors; pattern =
        ``NominalType(record.name, [TypeParamRef(tp) for tp ...])``).

        Strips Own/Readonly/Ref qualifiers from both sides (mirrors
        ``_apply_lhs_hint_to_function_return``) so a ``readonly[Container[T]]``
        LHS hint or an ``Own[Container[T]]`` return type still seeds correctly.
        ``match_type_with_inference`` mutates its accumulator even on branches
        that ultimately return False, so the match runs into a temporary dict
        and the bindings are committed only on overall success -- prevents a
        structurally-shaped but inner-mismatched LHS hint from leaking partial
        seed bindings.
        """
        p = unwrap_qualifiers(pattern)
        h = unwrap_qualifiers(hint)
        tmp: dict[str, TpyType] = {}
        if not self.match_type_with_inference(p, h, tmp):
            return {}
        return {k: v for k, v in tmp.items() if _is_useful_seed_binding(v)}

    def compute_representational_subst_params(
        self, fi: FunctionInfo, inferred_type_args: tuple[TpyType, ...],
    ) -> 'frozenset[str] | None':
        """Decide which marked type-params need adapter substitution at codegen.

        Reads ``fi.root.representational_type_params`` (set by sema body
        analysis when ``Ptr[U] -> Ptr[T]`` coerces in the callee body) and,
        for each marked U, returns U iff its substituted bound is a @dynamic
        protocol structurally satisfied by the inferred U (i.e., the concrete
        u_sub does NOT C++-inherit the protocol -- the Adapter wrap is then
        required so the body's pointer upcast becomes valid).

        Result is stored on the call AST node
        (``TpyCall.representational_subst_params`` /
        ``TpyMethodCall.representational_subst_params``) so codegen reads
        the decision instead of re-deriving it across multiple emission sites.
        Returns ``None`` when no marked param needs the adapter detour.
        """
        canonical = fi.root
        marked = canonical.representational_type_params
        if not marked or not inferred_type_args or not fi.type_params:
            return None
        subst = dict(zip(fi.type_params, inferred_type_args))
        result: set[str] = set()
        for tp_name in fi.type_params:
            if tp_name not in marked:
                continue
            bound = canonical.type_param_bounds.get(tp_name)
            if bound is None:
                continue
            t_sub = self.substitute_type_params(bound, subst)
            if not is_dyn_protocol(t_sub):
                continue
            u_sub = subst[tp_name]
            if not isinstance(u_sub, NominalType) or not u_sub.is_user_record:
                continue
            if self.protocols.directly_implements_dynamic(u_sub, t_sub):
                continue
            result.add(tp_name)
        return frozenset(result) if result else None

    def seed_subst_from_return_hint(
        self, func: FunctionInfo, expected_return_type: TpyType | None,
    ) -> dict[str, TpyType]:
        """Pre-bind type params by matching ``expected_return_type`` against
        ``func.return_type``. Used to give nested generic call args a hint
        that reflects the LHS-derived outer type before arg-driven inference
        has any evidence to contribute.

        For each unbound method-local type param `U` with a bound `B`, if
        substituting the seed into `B` yields a concrete type, default `U =
        B[seed]`. U=B trivially satisfies the bound (B <: B); the arg-driven
        inference may later refine U to a more specific subtype of B.
        Without this, a bounded-factory call with a nested generic arg --
        `f[U: T](v: Own[U])` called with `f(Box(Cat(...)))` against an LHS
        `Box[Greeter]` -- would analyze the inner arg with no type-arg hint
        and infer `U=Box[Cat]` instead of `Box[Greeter]`.
        """
        if expected_return_type is None or func.return_type is None:
            return {}
        if not func.type_params:
            return {}
        seed = self._seed_subst(func.return_type, expected_return_type)
        if seed and func.type_param_bounds:
            for tp in func.type_params:
                if tp in seed:
                    continue
                bound = func.type_param_bounds.get(tp)
                if bound is None:
                    continue
                # A type param that is an Fn-param's return (e.g. K in
                # `key: Fn[[T], K]`) must stay unseeded so the lambda hint keeps
                # the TypeParamRef; seeding it to its bound pre-empts body-type
                # inference.
                if any(
                    is_callable_type(unwrap_ref_type(p.type))
                    and contains_type_param(unwrap_ref_type(p.type).return_type, {tp})
                    for p in func.params
                ):
                    continue
                substituted = self.substitute_type_params(bound, seed)
                if not contains_type_param(substituted):
                    seed[tp] = substituted
        return seed

    def seed_subst_from_record_pattern(
        self, record: RecordInfo, expected_type: TpyType | None,
    ) -> dict[str, TpyType]:
        """Pre-bind type params by matching ``expected_type`` against a
        ``NominalType(record.name, [TypeParamRef(tp) ...])`` pattern.

        Record-constructor analogue of ``seed_subst_from_return_hint``: gives
        constructor args a contextual hint reflecting the LHS-derived outer
        type before arg-driven inference has any evidence to contribute.
        Lets nested generic chains like ``Rc.new(Box(Box(Dog(...))))`` resolve
        through the inner Box's args, not just the outer Rc.new's.
        """
        if expected_type is None or not record.type_params:
            return {}
        # Carry kind + bound on each TPRef so the seed pattern matches the
        # record's actual type-param shape: INT-kind params (e.g. Array's N)
        # pair against integer values in ``_match_array_with_inference``, and
        # bounded type params surface the bound for downstream consumers
        # that inspect TPRef.bound.
        type_param_kinds = record.type_param_kinds or [TypeParamKind.TYPE] * len(record.type_params)
        pattern_args: tuple[TpyType, ...] = tuple(
            TypeParamRef(
                tp,
                bound=record.type_param_bounds.get(tp),
                kind=type_param_kinds[i],
            )
            for i, tp in enumerate(record.type_params)
        )
        pattern = NominalType(
            record.name,
            pattern_args,
            _module_qname=record.qualified_name(),
        )
        return self._seed_subst(pattern, expected_type)

    def candidate_arg_hints(
        self, func: FunctionInfo, n_args: int, lhs_hint: TpyType | None,
    ) -> list[TpyType | None] | None:
        """Per-arg ``expr_type_hint`` from one overload candidate + the LHS hint.

        Probe-time helper for overload pre-analysis: lets nested generic-call
        and record-ctor args see a hint on their first analysis pass, so the
        cache short-circuit at the top of ``_analyze_generic_function_call`` /
        ``_analyze_record_constructor`` doesn't lock in a hint-naive type that
        the post-selection retry can't refresh.

        Returns:
        - ``None`` when the candidate's return shape does NOT match ``lhs_hint``.
          Callers exclude these candidates from the LHS-matching count.
        - a list of length ``n_args`` when the candidate matches. Per-position
          entries may still be ``None`` when no useful hint can be derived
          (e.g. a generic candidate whose seed binds only return-type-only
          TPRefs, leaving every param's hint reduced via ``post_substitute_hint``
          to None).

        This split lets ``_probe_analyze_args`` / ``_probe_analyze_method_args``
        distinguish "doesn't LHS-match" from "matches but has no useful per-arg
        hint" -- a conflation that would otherwise let a non-contributing
        LHS-matching candidate slip past the single-LHS-matching gate and
        leave the seed candidate's hint cached against a different winner.
        """
        if lhs_hint is None:
            return None
        # Arity gate: skip candidates that can't actually accept ``n_args``
        # positional args. Otherwise an arity-mismatched-but-LHS-matching
        # candidate could be the sole contributor in the probe's
        # single-LHS-matching gate -- biasing arg analysis toward a
        # candidate that ``resolve_overload`` will reject anyway.
        # Conservative direction: ``func.max_args`` includes keyword-only
        # params (it's ``len(self.params)``), while ``n_args`` is positional
        # only. The gate may over-admit a candidate with required keyword-
        # only params -- safe because ``resolve_overload`` still catches it,
        # the probe just wastes work building a hint that ends up unused.
        if n_args > func.max_args or n_args < func.min_args:
            return None
        if func.type_params:
            seed = self.seed_subst_from_return_hint(func, lhs_hint)
            if not seed:
                return None
            return [seeded_arg_hint(func.params, i, seed) for i in range(n_args)]
        # Non-generic / substituted candidate. Gate on a structural match
        # between return type and LHS hint so we only bias toward LHS-aligned
        # overloads. Direction: ``lhs_hint`` is param-side, ``return_type`` is
        # arg-side -- asks "can the return fit into the LHS slot?". The
        # opposite direction is wrong because ``match_type_with_inference``
        # only unwraps ``Optional[T]`` on the param side, which would falsely
        # accept a candidate returning ``Optional[Foo]`` as matching
        # ``lhs_hint = Foo`` and bias arg analysis toward the wrong overload.
        if func.return_type is None:
            return None
        # Guard: when ``lhs_hint`` contains a ``TypeParamRef`` (e.g. the call
        # site is inside a generic function body whose return ``T`` is the
        # local's annotation), the matcher's param-side TPRef branch would
        # unconditionally bind-and-accept, falsely matching every candidate.
        # The generic branch above is defended via ``_is_useful_seed_binding``'s
        # TPRef filter; mirror the defense here for the non-generic branch.
        lhs_unwrapped = unwrap_qualifiers(lhs_hint)
        if contains_type_param(lhs_unwrapped):
            return None
        if not self.match_type_with_inference(
            lhs_unwrapped,
            unwrap_qualifiers(func.return_type),
            {},
        ):
            return None
        out: list[TpyType | None] = []
        for i in range(n_args):
            # Mirrors ``seeded_arg_hint``: trailing args beyond fixed positional
            # slots target the variadic param's element type.
            if i < len(func.params):
                target = func.params[i]
            elif func.params and func.params[-1].is_variadic:
                target = func.params[-1]
            else:
                out.append(None)
                continue
            ptype = unwrap_ref_type(target.type)
            if target.is_variadic and is_varargs(ptype):
                ptype = unwrap_readonly(ptype.type_args[0])
            hint = unwrap_qualifiers(ptype)
            # Mirror ``post_substitute_hint``: an ``Optional[T]`` param accepts
            # a bare T arg, so the seeded hint exposes the inner T to the
            # inner ctor / call's seed instead of forcing it to match the
            # Optional shape.
            if isinstance(hint, OptionalType):
                hint = unwrap_qualifiers(hint.inner)
            out.append(hint)
        return out

    def _inherits_protocol(self, concrete: TpyType, protocol: NominalType) -> bool:
        """Return True if `concrete` transitively implements a @dynamic protocol matching `protocol`.

        Decides whether arg-derived T should be kept (Covariant[T] handles the
        uplift) or replaced by the LHS-hint protocol T (structural-conform case
        that needs explicit heap wrapping). Same-named non-@dynamic protocols
        don't match -- only @dynamic protocols are relevant to the LHS-hint path.

        @native records are excluded: their C++ struct is hand-written and
        almost certainly does not inherit the codegen-emitted protocol base
        (see ``codegen_cpp.protocols.directly_implements_dynamic`` for the
        full reasoning). Routing through Adapter is the only safe lowering,
        so this helper must agree with codegen's branching to avoid sema
        keeping a native T that codegen would later refuse.
        """
        if not isinstance(concrete, NominalType) or not concrete.is_user_record:
            return False
        record_info = self.ctx.registry.get_record(concrete.name)
        if record_info is None or record_info.is_native:
            return False
        if not is_subtype(record_info, protocol.name):
            return False
        proto_info = protocol_info_of(protocol)
        return proto_info is not None and proto_info.is_dynamic

    def match_generic_constructor(
        self, params: list, arg_types: list[TpyType]
    ) -> dict[str, TpyType] | None:
        """Try to match constructor params against arg types and infer type parameters.

        Returns dict of inferred type params (e.g., {"T": Int32}) on success, None on failure.
        Supports protocol params like "NativeIterable[T]" which infer T from element type.

        Unlike match_type_with_inference, Ptr[TypeParam] params accept Ptr[readonly[X]] args,
        inferring T = readonly[X]. This lets Span(readonly_ptr, n) produce Span[readonly[T]]
        without needing an explicit type annotation. Function inference keeps the strict check
        to preserve helpful errors (e.g. "unsafe_store() requires a mutable pointer").
        """
        inferred: dict[str, TpyType] = {}
        for param, arg_type in zip(params, arg_types):
            pt = param.type
            # For Ptr[TypeParam] constructor params, allow T = readonly[X] inference
            # from Ptr[readonly[X]] arguments. This is safe here (constructor context)
            # because the resulting type (e.g. Span[readonly[T]]) captures readonly-ness.
            if (isinstance(pt, PtrType)
                    and isinstance(pt.pointee, TypeParamRef)
                    and isinstance(arg_type, PtrType)
                    and arg_type.is_readonly
                    and not pt.is_readonly):
                name = pt.pointee.name
                pointee = arg_type.pointee  # ReadonlyType(X)
                if name in inferred:
                    if not self.types_match_for_inference(inferred[name], pointee):
                        return None
                else:
                    inferred[name] = pointee
                continue
            if not self.match_type_with_inference(pt, arg_type, inferred):
                return None
        return inferred

    def pending_list_matches_array(self, actual: PendingListType, expected: NominalType) -> bool:
        """Check if a pending list literal can match an Array type (including nested arrays)."""
        if actual.size != expected.type_args[1]:
            return False

        actual_elem = actual.element_type
        expected_elem = expected.type_args[0]

        if isinstance(actual_elem, PendingListType) and is_array(expected_elem):
            return self.pending_list_matches_array(actual_elem, expected_elem)

        if actual_elem == expected_elem:
            return True

        if isinstance(actual_elem, IntLiteralType) and is_integer_type(expected_elem):
            info = self.ctx.list_literals.get(actual.literal_id)
            if info:
                info.coerced_element_type = expected_elem
            return True

        return False

    def substitute_method_type_params(
        self, method: FunctionInfo, type_subst: dict[str, TpyType]
    ) -> FunctionInfo:
        """Substitute type parameters in a method signature.

        Method's own type params (e.g. U on def transform[U]) are preserved
        as identity mappings (U -> TypeParamRef(U)).  This is needed because
        substitute_type_params raises SemanticError for unknown params, and
        method-level params aren't in the class substitution dict.
        """
        effective_subst = dict(type_subst)
        for tp in method.type_params:
            if tp not in effective_subst:
                effective_subst[tp] = TypeParamRef(tp)
        substituted_params = [
            dc_replace(p, type=self.substitute_param_type(p.type, effective_subst))
            for p in method.params
        ]
        substituted_return = self.substitute_type_params(method.return_type, effective_subst)

        # Substitute type params in bounds
        substituted_bounds = {}
        if method.type_param_bounds:
            for param_name, bound in method.type_param_bounds.items():
                substituted_bounds[param_name] = self.substitute_type_params(bound, effective_subst)

        return FunctionInfo(
            name=method.name,
            params=substituted_params,
            return_type=substituted_return,
            is_noalloc=method.is_noalloc,
            is_readonly=method.is_readonly,
            is_consuming=method.is_consuming,
            # Accessor identity is invariant under substitution; dropping
            # it left an inherited generic property's resolved fi
            # unrecognizable as a getter/setter.
            is_property_getter=method.is_property_getter,
            is_property_setter=method.is_property_setter,
            is_method=method.is_method,
            is_staticmethod=method.is_staticmethod,
            is_classmethod=method.is_classmethod,
            # is_async / is_generator are invariant under type-arg substitution.
            is_async=method.is_async,
            is_generator=method.is_generator,
            # Kept UNSUBSTITUTED by design: consumers classify the declared
            # return SHAPE (Own vs bare vs type-param) and read the
            # instantiated inner from the substituted return_type.
            async_inner_return=method.async_inner_return,
            is_builtin_function=method.is_builtin_function,
            type_params=method.type_params,
            type_param_bounds=substituted_bounds if substituted_bounds else method.type_param_bounds,
            linkage=method.linkage,
            native_name=method.native_name,
            native_function=method.native_function,
            native_preserves_refs=method.native_preserves_refs,
            copy_returns_warn=method.copy_returns_warn,
            cpp_template=method.cpp_template,
            value_ptr_coercion=method.value_ptr_coercion,
            error_return_type=method.error_return_type,
            qualified_name=method.qualified_name,
            # Propagate so the substituted signature still names its ``**kw``
            # slot -- call-site kwargs packing resolves the pack by this name.
            kwarg_name=method.kwarg_name,
            # Preserve analysis-derived facts -- indices are positional (unaffected by
            # type substitution) and needed by call-site borrow/mutation checks.
            return_borrows_from=method.return_borrows_from,
            mutated_params=method.mutated_params,
            structural_mutated_params=method.structural_mutated_params,
            # Needed at the call site to recognize the mutable half of an
            # @auto_readonly pair, whose receiver demotion is conditional on
            # whether the call result is actually mutated.
            is_auto_readonly_mutable_clone=method.is_auto_readonly_mutable_clone,
            # The defining-record identity is invariant under type-arg
            # substitution; codegen needs it to name a generic method's coro
            # struct after the base (e.g. __coro_Box_fetch) rather than the
            # subclass when the method is inherited.
            owning_type_qname=method.owning_type_qname,
            canonical_fi=method.root,
        )

    def bind_inherited_coro_method(
        self, method: FunctionInfo, receiver: TpyType,
        receiver_record: 'RecordInfo | None',
    ) -> 'tuple[NominalType, FunctionInfo]':
        """Resolve an async/iterator dunder fetched via the parent-walk
        (`get_method_overloads_with_parents`, which does not substitute type
        args) to its MRO-bound owner and signature.

        The bound owner (e.g. `Box[Int32]` for a `Box[T]` method inherited by a
        concrete subclass) supplies the concrete args codegen needs to name the
        base's templated coro struct, and substituting the signature gives the
        await/with/for bind variable the concrete element type instead of the
        base's unbound `T`. Returns the bound owner plus the signature
        substituted with that binding (unchanged when the owner has no args).
        """
        owner = coro_struct_owner(
            method.owning_type_qname, receiver, receiver_record)
        subst = self.build_type_substitution(owner)
        if subst:
            method = self.substitute_method_type_params(method, subst)
        return owner, method

    def get_deref_target_type(self, typ: TpyType, is_readonly: bool = False) -> TpyType | None:
        """If typ has __deref__(), return resolved return type. Else None."""
        record_info = self.ctx.registry.get_record_for_type(typ)
        if not record_info:
            return None
        overloads = record_info.get_method_overloads("__deref__")
        if not overloads:
            return None
        method = resolve_overload(
            overloads, [], is_readonly_receiver=is_readonly)
        if method is None:
            return None
        type_subst = self.build_type_substitution(typ)
        if type_subst:
            method = self.substitute_method_type_params(method, type_subst)
        return unwrap_ref_type(method.return_type)

    def get_deref_coercion_target(self, typ: TpyType) -> TpyType | None:
        """Get the deref target for coercion purposes.

        Like get_deref_target_type() but excludes Ptr[readonly[T]] -- record params
        are T& (mutable ref) but deref_check(const T*) returns const T&.

        Note: does not pass is_readonly because ReadonlyType is already stripped
        from the actual type before this is called (by check_type_compatible).
        For user types with @auto_readonly __deref__, this means the mutable
        overload is selected. This is correct for value types (copies) and
        returns, but could be wrong for non-value deref targets in assignment
        context. Low risk in practice since user deref types typically return
        value types.
        """
        if is_readonly_ptr(typ):
            return None
        return self.get_deref_target_type(typ)


