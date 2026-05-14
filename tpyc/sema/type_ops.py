"""
TurboPython Type Operations

Type validation, substitution, and inference operations.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, TypeParamRef, NominalType, PtrType, is_readonly_ptr, OwnType, ReadonlyType, AutoReadonlyType, AutoOwnType,
    make_array, make_list, PendingListType, PendingViewType, GenExprType, SelfType, OptionalType, UnionType,
    TupleType,
    IntLiteralType, FloatLiteralType, TypeParamKind, BIGINT, UnknownElementType,
    NoneType, VoidType, CallableType,
    RecordInfo, FunctionInfo, ParamInfo, is_protocol_type, unwrap_readonly,
    unwrap_ref_type, RefType,
    is_callable_type, is_integer_type, is_float_type, is_void_like_type,
)
from ..coercions import resolve_coercion, CoercionContext
from ..diagnostics import SemanticError
from .. import qnames
from ..type_def_registry import (
    is_copy_iter, is_own_iter, is_array, is_span, is_list, is_dict, is_set,
    get_type_def, find_factory_by_simple_name, protocol_info_of,
)
from ..parse import TpyFunction
from tpyc import modules as builtin_modules
from .overloads import resolve_overload

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import SemanticContext


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
        allow_forward_ref: bool = True,
    ) -> None:
        """Validate that a type is well-formed.

        Args:
            typ: The type to validate.
            allow_type_param_ref: If True, TypeParamRef is allowed (for generic class definitions).
            loc: Optional source location for error messages.
            allow_forward_ref: If True, unknown NominalType records are allowed (for class registration).
        """
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
                    self.validate_record_type_args(typ, record_info, allow_type_param_ref, loc)
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
            elif not allow_forward_ref:
                raise SemanticError(f"Unknown type: {typ.name}", loc)
            # Container element validation (containers are NominalType + TypeDef)
            elem_type = typ.get_element_type()
            if elem_type is not None:
                self.validate_type(elem_type, allow_type_param_ref, loc)
                if is_protocol_type(elem_type):
                    raise SemanticError(
                        f"Protocol type '{elem_type}' cannot be used as a container element type",
                        loc,
                    )
        elif isinstance(typ, OptionalType):
            self.validate_type(typ.inner, allow_type_param_ref, loc)
            if is_protocol_type(typ.inner):
                proto_def = protocol_info_of(typ.inner)
                if proto_def and proto_def.is_dynamic:
                    raise SemanticError(
                        f"Optional[{typ.inner}] is not supported for @dynamic protocols. "
                        f"Use a sentinel value or separate 'has' flag instead",
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
                self.validate_type(member, allow_type_param_ref, loc)
        elif isinstance(typ, ReadonlyType):
            self.validate_type(typ.wrapped, allow_type_param_ref, loc)
        elif isinstance(typ, AutoReadonlyType):
            self.validate_type(typ.wrapped, allow_type_param_ref, loc)
        elif isinstance(typ, PtrType):
            self.validate_type(typ.pointee, allow_type_param_ref, loc)
            if is_protocol_type(typ.pointee):
                raise SemanticError(
                    f"Protocol type '{typ.pointee}' cannot be used as a pointer element type",
                    loc,
                )
        elif (elem_type := typ.get_element_type()) is not None:
            self.validate_type(elem_type, allow_type_param_ref, loc)
            if is_protocol_type(elem_type):
                raise SemanticError(
                    f"Protocol type '{elem_type}' cannot be used as a container element type",
                    loc,
                )

    def validate_record_type_args(
        self, typ: NominalType, record_info: RecordInfo, allow_type_param_ref: bool = False,
        loc: SourceLocation | None = None,
    ) -> None:
        """Validate that type arguments match their expected kinds (TYPE vs INT).

        Args:
            typ: The NominalType with type_args to validate.
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
        # Ptr[Ref[T]] -> Ptr[T]: Ref inside Ptr is redundant (Ptr is
        # already a pointer; the Ref from make_ref should not nest).
        if isinstance(result, PtrType) and isinstance(result.pointee, RefType):
            result = PtrType(result.pointee.wrapped, result.is_readonly)
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
        if is_array(typ):
            if self.is_type_param_ref(typ.type_args[0]):
                return True
            return isinstance(typ.type_args[1], TypeParamRef)
        if is_list(typ):
            return self.is_type_param_ref(typ.type_args[0])
        return False

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
            inner_arg = arg_type.inner if isinstance(arg_type, OptionalType) else arg_type
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
        # Also strip readonly: readonly[T] passed to Own[T] is an implicit copy.
        if isinstance(param_type, OwnType):
            inner_arg = arg_type.wrapped if isinstance(arg_type, OwnType) else arg_type
            if isinstance(inner_arg, ReadonlyType):
                inner_arg = inner_arg.wrapped
            return self.match_type_with_inference(
                param_type.wrapped, inner_arg, inferred
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
        # GenExprType satisfies Iterable[T] and Iterator[T];
        # CopyIter/OwnIter satisfy Iterable[T] only.
        if ((isinstance(arg_type, GenExprType) and param_type.qualified_name() in (qnames.ITERABLE, qnames.ITERATOR))
                or ((is_copy_iter(arg_type) or is_own_iter(arg_type)) and param_type.qualified_name() == qnames.ITERABLE)):
            if len(param_type.type_args) == 1:
                elem = arg_type.element_type if isinstance(arg_type, GenExprType) else arg_type.type_args[0]
                return self.match_type_with_inference(param_type.type_args[0], elem, inferred)
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

        first_param = protocol_info.type_params[0]
        return inferred.get(first_param)

    def _infer_protocol_type_arg_from_protocol(
        self,
        arg_type: NominalType,
        arg_protocol: 'ProtocolInfo',
        target_protocol: 'ProtocolInfo',
    ) -> TpyType | None:
        """Infer target protocol's type arg from an arg protocol's method signatures.

        E.g. Iterator[Int32] matching Iterable[T]: looks up Iterator's __iter__
        method (returns Self = Iterator[Int32]), matches against Iterable's
        __iter__ (returns Iterator[T]) to infer T = Int32.
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

        # Returns only the first type param -- callers handle multi-param
        # protocols via match_type_with_inference on the full type_args tuple.
        first_param = target_protocol.type_params[0]
        return inferred.get(first_param)

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
            if p.is_variadic and is_span(unwrap_ref_type(ptype)):
                # Variadic param: match each remaining arg against element type
                elem_type = unwrap_readonly(unwrap_ref_type(ptype).type_args[0])
                while arg_idx < len(arg_types):
                    if not self.match_type_with_inference(elem_type, arg_types[arg_idx], inferred):
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

        # Resolve pending types for codegen.
        for k, v in list(inferred.items()):
            if isinstance(v, IntLiteralType):
                inferred[k] = self.ctx.default_int_for_literal(v)
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

        # Reject leftover UnknownElementType: letting it ride through
        # substituted param types surfaces later as the indirect
        # pending-list resolver error; failing here lets the caller emit
        # a clean "Cannot infer type arguments" diagnostic instead.
        for tp in func.type_params:
            if tp not in inferred:
                return None
            if isinstance(inferred[tp], UnknownElementType):
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
                record_pattern = NominalType(record.name, tuple(TypeParamRef(tp) for tp in record.type_params),
                                              _module_qname=record.qualified_name())
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
            dc_replace(p, type=self.substitute_type_params(p.type, effective_subst))
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
            native_function=method.native_function,
            native_preserves_refs=method.native_preserves_refs,
            cpp_template=method.cpp_template,
            value_ptr_coercion=method.value_ptr_coercion,
            error_return_type=method.error_return_type,
            qualified_name=method.qualified_name,
            # Preserve analysis-derived facts -- indices are positional (unaffected by
            # type substitution) and needed by call-site borrow/mutation checks.
            return_borrows_from=method.return_borrows_from,
            mutated_params=method.mutated_params,
            structural_mutated_params=method.structural_mutated_params,
            canonical_fi=method.root,
        )

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


