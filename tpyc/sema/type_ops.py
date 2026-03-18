"""
TurboPython Type Operations

Type validation, substitution, and inference operations.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, TypeParamRef, NamedType, PtrType, is_readonly_ptr, OwnType, ReadonlyType, AutoReadonlyType,
    ArrayType, SpanType, ListType, PendingListType, GenExprType, SelfType, OptionalType, UnionType,
    TupleType,
    Int32Type, BigIntType, IntLiteralType, TypeParamKind, BIGINT,
    NoneType, VoidType,
    RecordInfo, FunctionInfo, ParamInfo, is_protocol_type, unwrap_readonly,
)
from ..coercions import resolve_coercion, CoercionContext
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import SemanticContext
    from tpyc import modules as builtin_modules


def _contains_type_param_ref(types: tuple[TpyType, ...]) -> bool:
    """Check if any type in the tuple contains a TypeParamRef (directly or nested)."""
    for t in types:
        if isinstance(t, TypeParamRef):
            return True
        inner = t.inner_types()
        if inner and _contains_type_param_ref(inner):
            return True
    return False


class TypeOperations:
    """Type validation, substitution, and inference operations."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def resolve_type(self, typ: TpyType, *, protocols_only: bool = False) -> TpyType:
        """Resolve a type, setting is_protocol flag on NamedType when needed.

        During parsing, NamedType may be created with is_protocol=False for
        names that are actually protocols. This method sets the flag correctly.
        Also converts NamedType("T") to TypeParamRef("T") when T is a type
        parameter in the current function or record scope (unless protocols_only).

        Args:
            protocols_only: If True, only resolve protocol flags -- skip
                TypeParamRef conversion. Used during record registration when
                type parameter scope is not yet active.
        """
        if isinstance(typ, NamedType):
            # Convert bare NamedType to TypeParamRef when it matches a type
            # parameter name in scope (method-level type params in annotations
            # are parsed as NamedType but should be TypeParamRef).
            if not protocols_only and not typ.type_args and not typ.is_protocol:
                from ..parse import TpyFunction
                func = self.ctx.current_function
                if isinstance(func, TpyFunction) and typ.name in func.type_params:
                    bound = func.type_param_bounds.get(typ.name)
                    return TypeParamRef(typ.name, bound=bound)
                if typ.name in (self.ctx.record_ctx.type_params or []):
                    return TypeParamRef(typ.name)
            # Check if this should have is_protocol set.
            # Only upgrade False -> True, never downgrade (parser may know about
            # same-file protocols not yet in the sema registry).
            protocol_info = self.ctx.registry.get_protocol(typ.name)
            # Generic protocols require type arguments (e.g., Sequence[T] not bare Sequence)
            if (protocol_info and protocol_info.type_params
                    and not typ.type_args and not protocols_only):
                from ..sema.diagnostics import SemanticError
                raise SemanticError(
                    f"Generic protocol '{typ.name}' requires type arguments: "
                    f"{typ.name}[{', '.join(protocol_info.type_params)}]")
            resolved_is_protocol = typ.is_protocol or (protocol_info is not None)
            resolved_is_dynamic = bool(protocol_info and protocol_info.is_dynamic)
            # Set _module_qname for protocols from their ProtocolInfo.module
            resolved_qname = typ._module_qname
            if resolved_is_protocol and not resolved_qname and protocol_info and protocol_info.module:
                resolved_qname = f"{protocol_info.module}.{typ.name}"
            needs_flag_update = (typ.is_protocol != resolved_is_protocol
                                 or typ.is_dynamic_protocol != resolved_is_dynamic
                                 or typ._module_qname != resolved_qname)
            # Recursively resolve type arguments
            if typ.type_args:
                new_args = tuple(
                    self.resolve_type(arg, protocols_only=protocols_only) if isinstance(arg, TpyType) else arg
                    for arg in typ.type_args
                )
                # Identity check: NamedType.__eq__ excludes is_dynamic_protocol
                # (compare=False), so == would miss flag-only changes.
                args_changed = any(
                    new is not old for new, old in zip(new_args, typ.type_args)
                )
                if args_changed or needs_flag_update:
                    if needs_flag_update:
                        # Protocol/qname flag changed -- only for user records/protocols
                        return NamedType(typ.name, new_args, resolved_is_protocol,
                                         resolved_qname, resolved_is_dynamic)
                    # Only type_args changed -- use with_inner_types to preserve subclass
                    new_inner = tuple(a for a in new_args if isinstance(a, TpyType))
                    return typ.with_inner_types(new_inner)
            elif needs_flag_update:
                return NamedType(typ.name, typ.type_args, resolved_is_protocol,
                                 resolved_qname, resolved_is_dynamic)
        elif isinstance(typ, PtrType):
            resolved_pointee = self.resolve_type(typ.pointee, protocols_only=protocols_only)
            if resolved_pointee is not typ.pointee:
                return PtrType(resolved_pointee, is_readonly=typ.is_readonly)
        elif isinstance(typ, OwnType):
            resolved_wrapped = self.resolve_type(typ.wrapped, protocols_only=protocols_only)
            if resolved_wrapped is not typ.wrapped:
                return OwnType(resolved_wrapped)
        elif isinstance(typ, ReadonlyType):
            resolved_wrapped = self.resolve_type(typ.wrapped, protocols_only=protocols_only)
            if resolved_wrapped is not typ.wrapped:
                return ReadonlyType(resolved_wrapped)
        elif isinstance(typ, AutoReadonlyType):
            resolved_wrapped = self.resolve_type(typ.wrapped, protocols_only=protocols_only)
            if resolved_wrapped is not typ.wrapped:
                return AutoReadonlyType(resolved_wrapped)
        elif isinstance(typ, OptionalType):
            resolved_inner = self.resolve_type(typ.inner, protocols_only=protocols_only)
            if resolved_inner is not typ.inner:
                return OptionalType(resolved_inner, force_pointer_repr=typ.uses_pointer_repr())
        elif isinstance(typ, UnionType):
            resolved_members = tuple(self.resolve_type(m, protocols_only=protocols_only) for m in typ.members)
            if any(new is not old for new, old in zip(resolved_members, typ.members)):
                return UnionType(resolved_members)
        return typ

    def validate_type(
        self, typ: TpyType, allow_type_param_ref: bool = False, loc: SourceLocation | None = None,
        allow_forward_ref: bool = True,
    ) -> None:
        """Validate that a type is well-formed.

        Args:
            typ: The type to validate.
            allow_type_param_ref: If True, TypeParamRef is allowed (for generic class definitions).
            loc: Optional source location for error messages.
            allow_forward_ref: If True, unknown NamedType records are allowed (for class registration).
        """
        if isinstance(typ, TypeParamRef):
            if not allow_type_param_ref:
                raise SemanticError(f"Type parameter '{typ.name}' used outside of generic context", loc)
            if typ.kind == TypeParamKind.INT:
                raise SemanticError(f"Integer type parameter '{typ.name}' cannot be used as a type annotation", loc)
            return
        if isinstance(typ, NamedType) and typ.is_record:
            record_info = self.ctx.registry.get_record_for_type(typ)
            if not record_info:
                if self.ctx.registry.get_enum(typ.name) is not None:
                    pass  # imported enum -- NamedType will be resolved to EnumType
                elif not allow_forward_ref:
                    raise SemanticError(f"Unknown type: {typ.name}", loc)
                pass
            elif typ.type_args:
                # Validate type arguments for generic record
                if not record_info.is_generic():
                    raise SemanticError(f"Record '{typ.name}' is not generic, but type arguments were provided", loc)
                if len(typ.type_args) != len(record_info.type_params):
                    raise SemanticError(
                        f"Record '{typ.name}' expects {len(record_info.type_params)} type arguments, "
                        f"got {len(typ.type_args)}",
                        loc,
                    )
                # Validate each type argument matches its expected kind
                self.validate_record_type_args(typ, record_info, allow_type_param_ref, loc)
            elif record_info.is_generic():
                # Generic record used without type arguments
                raise SemanticError(
                    f"Generic record '{typ.name}' requires type arguments: "
                    f"{typ.name}[{', '.join(record_info.type_params)}]",
                    loc,
                )
            # Container element validation (ListType, ArrayType, SpanType are NamedType subclasses)
            elem_type = typ.get_element_type()
            if elem_type is not None:
                self.validate_type(elem_type, allow_type_param_ref, loc)
                if is_protocol_type(elem_type):
                    raise SemanticError(
                        f"Protocol type '{elem_type.name}' cannot be used as a container element type",
                        loc,
                    )
        elif isinstance(typ, OptionalType):
            self.validate_type(typ.inner, allow_type_param_ref, loc)
            if is_protocol_type(typ.inner):
                proto_def = self.ctx.registry.get_protocol(typ.inner.name)
                if proto_def and proto_def.is_dynamic:
                    raise SemanticError(
                        f"Optional[{typ.inner.name}] is not supported for @dynamic protocols. "
                        f"Use a sentinel value or separate 'has' flag instead",
                        loc,
                    )
        elif isinstance(typ, UnionType):
            non_none = [m for m in typ.members if not isinstance(m, (NoneType, VoidType))]
            protocols = [m for m in non_none if is_protocol_type(m)]
            concrete = [m for m in non_none if not is_protocol_type(m)]
            if protocols and concrete:
                proto_names = ", ".join(f"'{m.name}'" for m in protocols)
                raise SemanticError(
                    f"Cannot mix protocol types ({proto_names}) with concrete types in a union",
                    loc,
                )
            if protocols:
                for p in protocols:
                    proto_def = self.ctx.registry.get_protocol(p.name)
                    if proto_def and proto_def.is_dynamic:
                        raise SemanticError(
                            f"@dynamic protocol '{p.name}' cannot be used in a protocol union; "
                            f"only static protocols are supported",
                            loc,
                        )
            for member in typ.members:
                self.validate_type(member, allow_type_param_ref, loc)
        elif isinstance(typ, ReadonlyType):
            self.validate_type(typ.wrapped, allow_type_param_ref, loc)
        elif isinstance(typ, AutoReadonlyType):
            self.validate_type(typ.wrapped, allow_type_param_ref, loc)
        elif isinstance(typ, PtrType):
            self.validate_type(typ.pointee, allow_type_param_ref, loc)
            if is_protocol_type(typ.pointee):
                raise SemanticError(
                    f"Protocol type '{typ.pointee.name}' cannot be used as a pointer element type",
                    loc,
                )
        elif (elem_type := typ.get_element_type()) is not None:
            self.validate_type(elem_type, allow_type_param_ref, loc)
            if is_protocol_type(elem_type):
                raise SemanticError(
                    f"Protocol type '{elem_type.name}' cannot be used as a container element type",
                    loc,
                )

    def validate_record_type_args(
        self, typ: NamedType, record_info: RecordInfo, allow_type_param_ref: bool = False,
        loc: SourceLocation | None = None,
    ) -> None:
        """Validate that type arguments match their expected kinds (TYPE vs INT).

        Args:
            typ: The NamedType with type_args to validate.
            record_info: The RecordInfo with type_param_kinds.
            allow_type_param_ref: If True, allow TypeParamRef as valid types.
            loc: Optional source location for error messages.
        """
        if not record_info.type_param_kinds:
            # Legacy: no kinds specified, assume all TYPE
            for arg in typ.type_args:
                if isinstance(arg, TpyType):
                    self.validate_type(arg, allow_type_param_ref, loc)
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
                    self.validate_type(arg, allow_type_param_ref, loc)
                else:
                    raise SemanticError(
                        f"Invalid type argument for '{param_name}' of '{typ.name}': {arg}",
                        loc,
                    )

    def substitute_type_params(self, typ: TpyType, subst: dict[str, TpyType | int]) -> TpyType:
        """Substitute type parameters with concrete types.

        Args:
            typ: The type containing potential TypeParamRef instances.
            subst: Mapping from type parameter names to concrete types or integers.

        Returns:
            The type with all TypeParamRef instances replaced by their concrete types.
        """
        if isinstance(typ, TypeParamRef):
            if typ.name in subst:
                replacement = subst[typ.name]
                if isinstance(replacement, int):
                    # INT type params: keep as TypeParamRef for expression contexts (codegen uses the name)
                    # Type-level substitution for ArrayType etc. is handled below
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
        # Special handling for ArrayType: substitute size if it's a TypeParamRef
        if isinstance(typ, ArrayType):
            new_elem = self.substitute_type_params(typ.element_type, subst)
            new_size = typ.size
            if isinstance(typ.size, TypeParamRef) and typ.size.name in subst:
                new_size = subst[typ.size.name]
            if new_elem != typ.element_type or new_size != typ.size:
                return ArrayType(new_elem, new_size)
            return typ
        # NamedType (user records and module-defined generics like
        # UninitArrayStorage[T, N]) can have mixed TpyType/int type_args.
        # map_inner_types operates on TpyType -> TpyType, so it can't
        # substitute int-valued TypeParamRefs.  Same reason ArrayType above
        # needs direct handling.  Exact type check avoids catching NamedType
        # subclasses (ListType, DictType, etc.) which don't have int args.
        if type(typ) is NamedType and typ.type_args:
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
                return NamedType(typ.name, tuple(new_args), typ.is_protocol,
                                 typ._module_qname, typ.is_dynamic_protocol)
            return typ
        # Use map_inner_types for types that have inner types
        return typ.map_inner_types(lambda t: self.substitute_type_params(t, subst))

    def build_type_substitution(self, record_type: TpyType) -> dict[str, TpyType | int]:
        """Build a type parameter substitution map for a generic record instantiation.

        Works for all types: user records and module types (NamedType) use
        RecordInfo.type_params + type_args; non-NamedType builtins (ListType,
        ArrayType, etc.) use extract_type_params from the module system.

        Returns:
            Mapping from type parameter names to concrete types or integers.
            For example: {"T": Int32, "N": 8} for Matrix[Int32, 8].
        """
        from tpyc import modules as builtin_modules

        if isinstance(record_type, NamedType):
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

    def is_type_param_ref(self, typ: TpyType) -> bool:
        """Check if a type is or contains a TypeParamRef."""
        if isinstance(typ, TypeParamRef):
            return True
        if isinstance(typ, PtrType):
            return self.is_type_param_ref(typ.pointee)
        if isinstance(typ, OwnType):
            return self.is_type_param_ref(typ.wrapped)
        if isinstance(typ, ReadonlyType):
            return self.is_type_param_ref(typ.wrapped)
        if isinstance(typ, ArrayType):
            if self.is_type_param_ref(typ.element_type):
                return True
            return isinstance(typ.size, TypeParamRef)
        if isinstance(typ, (ListType, SpanType)):
            return self.is_type_param_ref(typ.element_type)
        return False

    def is_forwarded_type_param(self, typ: TpyType, type_params: list[str]) -> bool:
        """Check if a type references one of the given type parameters.

        At parse time, type parameters in base class type args appear as NamedType
        (e.g., Container[T] has T as NamedType("T"), not TypeParamRef("T")).
        This function checks for both forms.
        """
        if isinstance(typ, TypeParamRef):
            return typ.name in type_params
        if isinstance(typ, NamedType):
            # A NamedType with no type_args and name matching a type param is a forwarded param
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
        from ..parse import TpyFunction
        # Check current function's type param bounds
        if (self.ctx.current_function and isinstance(self.ctx.current_function, TpyFunction)
                and type_param_name in self.ctx.current_function.type_param_bounds):
            return self.ctx.current_function.type_param_bounds[type_param_name]
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
        # ReadonlyType wrapper: unwrap for matching (readonly[T] accepts T and readonly[T])
        if isinstance(param_type, ReadonlyType):
            arg_unwrapped = unwrap_readonly(arg_type)
            return self.match_type_with_inference(param_type.wrapped, arg_unwrapped, inferred)

        # Own[T] wrapper on argument -- unwrap before matching.
        # Own is an ownership marker (e.g. from copy()), not a distinct type.
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped

        # TypeParamRef -- infer or check consistency
        if isinstance(param_type, TypeParamRef):
            if param_type.name in inferred:
                existing = inferred[param_type.name]
                if isinstance(existing, IntLiteralType) and isinstance(arg_type, (Int32Type, BigIntType)):
                    inferred[param_type.name] = arg_type
                    return True
                return self.types_match_for_inference(existing, arg_type)
            inferred[param_type.name] = arg_type
            return True

        # Protocol with TypeParamRef in type_args (e.g., NativeIterable[T],
        # NativeIterable[tuple[K, V]]) -- TypeParamRefs may be nested
        if is_protocol_type(param_type) and param_type.type_args:
            if _contains_type_param_ref(param_type.type_args):
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

        # ListType with nested TypeParamRef (e.g., list[T])
        if isinstance(param_type, ListType):
            if isinstance(arg_type, (ListType, PendingListType)):
                return self.match_type_with_inference(
                    param_type.element_type, arg_type.element_type, inferred
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

        # ArrayType with TypeParamRef element or size (e.g., Array[T, N])
        if isinstance(param_type, ArrayType):
            return self._match_array_with_inference(param_type, arg_type, inferred)

        # SpanType: recurse on element types directly so that readonly propagates into T.
        # Span[T] accepts both Span[X] (T=X) and Span[readonly[X]] (T=readonly[X]).
        # This must come before the NamedType check because SpanType is a NamedType subclass,
        # and _match_record_with_inference would reject by name if it treated spans differently.
        if isinstance(param_type, SpanType):
            if isinstance(arg_type, SpanType):
                return self.match_type_with_inference(
                    param_type.element_type, arg_type.element_type, inferred
                )
            return False

        # NamedType (record) with type args (e.g., Box[T] nested)
        if isinstance(param_type, NamedType) and param_type.is_record and param_type.type_args:
            return self._match_record_with_inference(param_type, arg_type, inferred)

        # (PtrType handled above, before ListType)

        # Optional[T] -- unwrap and recurse (bare T can coerce to Optional[T])
        if isinstance(param_type, OptionalType):
            inner_arg = arg_type.inner if isinstance(arg_type, OptionalType) else arg_type
            return self.match_type_with_inference(
                param_type.inner, inner_arg, inferred
            )

        # Own[T] wrapper -- unwrap and match T against arg (any type can be owned).
        # Also strip readonly: readonly[T] passed to Own[T] is an implicit copy.
        if isinstance(param_type, OwnType):
            inner_arg = arg_type.wrapped if isinstance(arg_type, OwnType) else arg_type
            if isinstance(inner_arg, ReadonlyType):
                inner_arg = inner_arg.wrapped
            return self.match_type_with_inference(
                param_type.wrapped, inner_arg, inferred
            )

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
        from tpyc import modules as builtin_modules

        # GenExprType satisfies Iterable[T] and Iterator[T]
        if isinstance(arg_type, GenExprType) and param_type.name in ("Iterable", "Iterator"):
            if len(param_type.type_args) == 1:
                return self.match_type_with_inference(param_type.type_args[0], arg_type.element_type, inferred)
            return True

        protocol_name = param_type.name
        if param_type.qualified_name() == "typing.Iterator":
            if is_protocol_type(arg_type) and arg_type.qualified_name() == "typing.Iterator" and arg_type.type_args:
                elem_type = arg_type.type_args[0]
            else:
                elem_type = builtin_modules.get_extends_protocol_type_arg(
                    arg_type, protocol_name, registry=self.ctx.registry)
                if elem_type is None:
                    elem_type = self._infer_protocol_type_arg_structurally(
                        arg_type, protocol_name)
        else:
            # General path: direct protocol match, extends declarations,
            # then structural inference from method signatures.
            if is_protocol_type(arg_type) and arg_type.qualified_name() == param_type.qualified_name() and arg_type.type_args:
                elem_type = arg_type.type_args[0]
            else:
                elem_type = builtin_modules.get_extends_protocol_type_arg(
                    arg_type, protocol_name, registry=self.ctx.registry)
                if elem_type is None:
                    elem_type = self._infer_protocol_type_arg_structurally(
                        arg_type, protocol_name)
        if elem_type is None:
            return False
        # Single type_arg: use recursive matching to handle compound types
        # like NativeIterable[tuple[K, V]] where the type_arg is a TupleType
        if len(param_type.type_args) == 1:
            return self.match_type_with_inference(param_type.type_args[0], elem_type, inferred)
        for ta in param_type.type_args:
            if isinstance(ta, TypeParamRef):
                if ta.name in inferred:
                    if not self.types_match_for_inference(inferred[ta.name], elem_type):
                        return False
                else:
                    inferred[ta.name] = elem_type
        return True

    def _infer_protocol_type_arg_structurally(
        self, arg_type: TpyType, protocol_name: str,
    ) -> TpyType | None:
        """Infer a protocol's type arg by matching method signatures structurally.

        Uses match_type_with_inference to unify protocol method signatures
        (containing TypeParamRefs like T) against the record's concrete method
        signatures. Handles all compound return/param types (Span, Optional, etc.).
        """
        protocol_info = self.ctx.registry.get_protocol(protocol_name)
        if protocol_info is None or not protocol_info.type_params:
            return None
        record = self.ctx.registry.get_record_for_type(arg_type)
        if record is None:
            return None

        type_subst: dict[str, TpyType] = {}
        if record.type_params and isinstance(arg_type, NamedType) and arg_type.type_args:
            type_subst = dict(zip(record.type_params, arg_type.type_args))

        def _substitute(t: TpyType) -> TpyType:
            if isinstance(t, TypeParamRef) and t.name in type_subst:
                return type_subst[t.name]
            return t

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

        first_param = protocol_info.type_params[0]
        return inferred.get(first_param)

    def _match_array_with_inference(
        self,
        param_type: ArrayType,
        arg_type: TpyType,
        inferred: dict[str, TpyType],
    ) -> bool:
        """Match ArrayType with TypeParamRef element or size (e.g., Array[T, N])."""
        if not isinstance(arg_type, ArrayType):
            return False
        if not self.match_type_with_inference(
            param_type.element_type, arg_type.element_type, inferred
        ):
            return False
        # Match sizes
        if isinstance(param_type.size, TypeParamRef):
            if isinstance(arg_type.size, int):
                param_name = param_type.size.name
                if param_name in inferred:
                    if inferred[param_name] != arg_type.size:
                        return False
                else:
                    inferred[param_name] = arg_type.size
                return True
            elif isinstance(arg_type.size, TypeParamRef):
                return param_type.size.name == arg_type.size.name
        else:
            return param_type.size == arg_type.size

    def _match_record_with_inference(
        self,
        param_type: NamedType,
        arg_type: TpyType,
        inferred: dict[str, TpyType],
    ) -> bool:
        """Match a generic record NamedType against arg_type (e.g., Box[T])."""
        if not (isinstance(arg_type, NamedType) and arg_type.is_record and arg_type.name == param_type.name):
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

        Handles IntLiteralType matching other integer types (BigInt, Int32).
        """
        if type_a == type_b:
            return True
        # IntLiteralType matches any integer type
        if isinstance(type_a, IntLiteralType) and isinstance(type_b, (BigIntType, Int32Type)):
            return True
        if isinstance(type_b, IntLiteralType) and isinstance(type_a, (BigIntType, Int32Type)):
            return True
        # Both IntLiteralType - they're compatible
        if isinstance(type_a, IntLiteralType) and isinstance(type_b, IntLiteralType):
            return True
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
        for (pname, ptype), arg_type in zip(func.params, arg_types):
            if not self.match_type_with_inference(ptype, arg_type, inferred):
                return None

        # Fallback: infer remaining params from expected return type
        if expected_return_type is not None:
            unresolved = [tp for tp in func.type_params if tp not in inferred]
            if unresolved:
                ret = func.return_type.wrapped if isinstance(func.return_type, OwnType) else func.return_type
                exp = expected_return_type.wrapped if isinstance(expected_return_type, OwnType) else expected_return_type
                self.match_type_with_inference(ret, exp, inferred)

        # Resolve pending types for codegen.
        for k, v in list(inferred.items()):
            if isinstance(v, IntLiteralType):
                inferred[k] = self.ctx.default_int_for_literal(v)
            elif isinstance(v, PendingListType):
                # Resolve PendingListType to ListType
                elem_type = v.element_type
                if isinstance(elem_type, IntLiteralType):
                    elem_type = self.ctx.default_int_for_literal(elem_type)
                inferred[k] = ListType(elem_type)

        # Fill in defaults for unresolved type params
        if func.type_param_defaults:
            for tp in func.type_params:
                if tp not in inferred and tp in func.type_param_defaults:
                    sentinel = func.type_param_defaults[tp]
                    if sentinel == "DEFAULT_INT":
                        inferred[tp] = self.ctx.default_int_type
                    else:
                        raise ValueError(f"Unknown type_param_default sentinel: {sentinel!r}")

        # Check all type params were inferred
        for tp in func.type_params:
            if tp not in inferred:
                return None

        # Validate type parameter bounds
        for param_name, type_arg in inferred.items():
            if param_name in func.type_param_bounds:
                bound = func.type_param_bounds[param_name]
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
            if not self.match_type_with_inference(ptype, arg_type, inferred):
                return None

        # Fallback: infer remaining params from expected type
        if expected_type is not None:
            unresolved = [tp for tp in record.type_params if tp not in inferred]
            if unresolved:
                exp = expected_type.wrapped if isinstance(expected_type, OwnType) else expected_type
                record_pattern = NamedType(record.name, tuple(TypeParamRef(tp) for tp in record.type_params))
                self.match_type_with_inference(record_pattern, exp, inferred)

        # Verify all type params were inferred
        for tp in record.type_params:
            if tp not in inferred:
                return None

        return inferred

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

    def pending_list_matches_array(self, actual: PendingListType, expected: ArrayType) -> bool:
        """Check if a pending list literal can match an Array type (including nested arrays)."""
        if actual.size != expected.size:
            return False

        actual_elem = actual.element_type
        expected_elem = expected.element_type

        if isinstance(actual_elem, PendingListType) and isinstance(expected_elem, ArrayType):
            return self.pending_list_matches_array(actual_elem, expected_elem)

        if actual_elem == expected_elem:
            return True

        if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (Int32Type, BigIntType)):
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
            ParamInfo(p.name, self.substitute_type_params(p.type, effective_subst),
                      p.requires_lvalue, p.requires_mutable, default_expr=p.default_expr)
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
            is_method=method.is_method,
            is_staticmethod=method.is_staticmethod,
            is_builtin_function=method.is_builtin_function,
            type_params=method.type_params,
            type_param_bounds=substituted_bounds if substituted_bounds else method.type_param_bounds,
            linkage=method.linkage,
            native_name=method.native_name,
            cpp_template=method.cpp_template,
            error_return_type=method.error_return_type,
            qualified_name=method.qualified_name,
            # Preserve analysis-derived facts -- indices are positional (unaffected by
            # type substitution) and needed by call-site borrow/mutation checks.
            return_borrows_from=method.return_borrows_from,
            mutated_params=method.mutated_params,
            structural_mutated_params=method.structural_mutated_params,
        )

    def get_deref_target_type(self, typ: TpyType) -> TpyType | None:
        """If typ has __deref__(), return resolved return type. Else None."""
        record_info = self.ctx.registry.get_record_for_type(typ)
        if not record_info:
            return None
        overloads = record_info.get_method_overloads("__deref__")
        if not overloads:
            return None
        method = overloads[0]
        type_subst = self.build_type_substitution(typ)
        if type_subst:
            method = self.substitute_method_type_params(method, type_subst)
        return method.return_type

    def get_deref_coercion_target(self, typ: TpyType) -> TpyType | None:
        """Get the deref target for coercion purposes.

        Like get_deref_target_type() but excludes Ptr[readonly[T]] -- record params
        are T& (mutable ref) but deref_check(const T*) returns const T&.
        """
        if is_readonly_ptr(typ):
            return None
        return self.get_deref_target_type(typ)


