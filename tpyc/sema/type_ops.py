"""
TurboPython Type Operations

Type validation, substitution, and inference operations.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, TypeParamRef, NamedType, PtrType, ConstPtrType, OwnType,
    ArrayType, SpanType, ListType, PendingListType, SelfType, OptionalType,
    Int32Type, BigIntType, IntLiteralType, TypeParamKind, BIGINT,
    RecordInfo, FunctionInfo, ParamInfo, is_protocol_type,
)
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import SemanticContext
    from tpyc import modules as builtin_modules


class TypeOperations:
    """Type validation, substitution, and inference operations."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def resolve_type(self, typ: TpyType) -> TpyType:
        """Resolve a type, setting is_protocol flag on NamedType when needed.

        During parsing, NamedType may be created with is_protocol=False for
        names that are actually protocols. This method sets the flag correctly.
        """
        if isinstance(typ, NamedType):
            # Check if this should have is_protocol set
            protocol_info = self.ctx.registry.get_protocol(typ.name)
            resolved_is_protocol = protocol_info is not None
            # Recursively resolve type arguments
            if typ.type_args:
                new_args = tuple(
                    self.resolve_type(arg) if isinstance(arg, TpyType) else arg
                    for arg in typ.type_args
                )
                if new_args != typ.type_args or typ.is_protocol != resolved_is_protocol:
                    return NamedType(typ.name, new_args, resolved_is_protocol)
            elif typ.is_protocol != resolved_is_protocol:
                return typ.with_protocol_flag(resolved_is_protocol)
        elif isinstance(typ, (ListType, ArrayType, SpanType)):
            elem = typ.get_element_type()
            if elem:
                resolved_elem = self.resolve_type(elem)
                if resolved_elem != elem:
                    return typ.with_inner_types((resolved_elem,))
        elif isinstance(typ, (PtrType, ConstPtrType)):
            resolved_pointee = self.resolve_type(typ.pointee)
            if resolved_pointee != typ.pointee:
                return type(typ)(resolved_pointee)
        elif isinstance(typ, OwnType):
            resolved_wrapped = self.resolve_type(typ.wrapped)
            if resolved_wrapped != typ.wrapped:
                return OwnType(resolved_wrapped)
        return typ

    def validate_type(
        self, typ: TpyType, allow_type_param_ref: bool = False, loc: SourceLocation | None = None,
    ) -> None:
        """Validate that a type is well-formed.

        Args:
            typ: The type to validate.
            allow_type_param_ref: If True, TypeParamRef is allowed (for generic class definitions).
            loc: Optional source location for error messages.
        """
        if isinstance(typ, TypeParamRef):
            if not allow_type_param_ref:
                raise SemanticError(f"Type parameter '{typ.name}' used outside of generic class definition", loc)
            if typ.kind == TypeParamKind.INT:
                raise SemanticError(f"Integer type parameter '{typ.name}' cannot be used as a type annotation", loc)
            return
        if isinstance(typ, NamedType) and typ.is_record:
            record_info = self.ctx.registry.get_record(typ.name)
            if not record_info:
                # Allow forward references during registration
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
        elif isinstance(typ, OptionalType):
            self.validate_type(typ.inner, allow_type_param_ref, loc)
        elif isinstance(typ, (PtrType, ConstPtrType)):
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
        # Special handling for ArrayType: substitute size if it's a TypeParamRef
        if isinstance(typ, ArrayType):
            new_elem = self.substitute_type_params(typ.element_type, subst)
            new_size = typ.size
            if isinstance(typ.size, TypeParamRef) and typ.size.name in subst:
                new_size = subst[typ.size.name]
            if new_elem != typ.element_type or new_size != typ.size:
                return ArrayType(new_elem, new_size)
            return typ
        # Use map_inner_types for types that have inner types
        return typ.map_inner_types(lambda t: self.substitute_type_params(t, subst))

    def build_type_substitution(self, record_type: NamedType) -> dict[str, TpyType | int]:
        """Build a type parameter substitution map for a generic record instantiation.

        Args:
            record_type: A NamedType with type_args (e.g., Stack[Int32] or Matrix[Int32, 8]).

        Returns:
            Mapping from type parameter names to concrete types or integers.
            For example: {"T": Int32, "N": 8} for Matrix[Int32, 8].
        """
        record_info = self.ctx.registry.get_record(record_type.name)
        if not record_info or not record_info.is_generic():
            return {}
        if not record_type.type_args:
            return {}
        return dict(zip(record_info.type_params, record_type.type_args))

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
        if isinstance(typ, (PtrType, ConstPtrType)):
            return self.is_type_param_ref(typ.pointee)
        if isinstance(typ, OwnType):
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
        if isinstance(typ, (PtrType, ConstPtrType)):
            return self.is_forwarded_type_param(typ.pointee, type_params)
        if isinstance(typ, OwnType):
            return self.is_forwarded_type_param(typ.wrapped, type_params)
        if isinstance(typ, (ListType, SpanType, ArrayType)):
            return self.is_forwarded_type_param(typ.element_type, type_params)
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
        # TypeParamRef — infer or check consistency
        if isinstance(param_type, TypeParamRef):
            if param_type.name in inferred:
                existing = inferred[param_type.name]
                if isinstance(existing, IntLiteralType) and isinstance(arg_type, (Int32Type, BigIntType)):
                    inferred[param_type.name] = arg_type
                    return True
                return self.types_match_for_inference(existing, arg_type)
            inferred[param_type.name] = arg_type
            return True

        # Protocol with TypeParamRef in type_args (e.g., NativeIterable[T])
        if is_protocol_type(param_type) and param_type.type_args:
            has_type_param = any(isinstance(ta, TypeParamRef) for ta in param_type.type_args)
            if has_type_param:
                return self._match_protocol_type_args_with_inference(param_type, arg_type, inferred)

        # ListType with nested TypeParamRef (e.g., list[T])
        if isinstance(param_type, ListType):
            if isinstance(arg_type, (ListType, PendingListType)):
                return self.match_type_with_inference(
                    param_type.element_type, arg_type.element_type, inferred
                )
            return False

        # ArrayType with TypeParamRef element or size (e.g., Array[T, N])
        if isinstance(param_type, ArrayType):
            return self._match_array_with_inference(param_type, arg_type, inferred)

        # NamedType (record) with type args (e.g., Box[T] nested)
        if isinstance(param_type, NamedType) and param_type.is_record and param_type.type_args:
            return self._match_record_with_inference(param_type, arg_type, inferred)

        # Pointer types (Ptr[T], ConstPtr[T])
        if isinstance(param_type, PtrType):
            if isinstance(arg_type, PtrType):
                return self.match_type_with_inference(
                    param_type.pointee, arg_type.pointee, inferred
                )
            return False

        if isinstance(param_type, ConstPtrType):
            if isinstance(arg_type, (ConstPtrType, PtrType)):
                return self.match_type_with_inference(
                    param_type.pointee, arg_type.pointee, inferred
                )
            return False

        # Own[T] wrapper
        if isinstance(param_type, OwnType):
            if isinstance(arg_type, OwnType):
                return self.match_type_with_inference(
                    param_type.wrapped, arg_type.wrapped, inferred
                )
            return False

        # Concrete type — check compatibility
        return self.types_match_for_inference(param_type, arg_type)

    def _match_protocol_type_args_with_inference(
        self,
        param_type: TpyType,
        arg_type: TpyType,
        inferred: dict[str, TpyType],
    ) -> bool:
        """Match a protocol with TypeParamRef type_args against arg_type (e.g., NativeIterable[T], OptIterator[T])."""
        from tpyc import modules as builtin_modules

        protocol_name = param_type.name
        if protocol_name == "OptIterator":
            elem_type = builtin_modules.get_native_iterator_element_type(
                arg_type, registry=self.ctx.registry)
        else:
            elem_type = self._get_iterable_element_type_or_none(arg_type)
            if elem_type is not None and not builtin_modules.type_extends_any(arg_type, protocol_name):
                elem_type = None
        if elem_type is None:
            return False
        for ta in param_type.type_args:
            if isinstance(ta, TypeParamRef):
                if ta.name in inferred:
                    if not self.types_match_for_inference(inferred[ta.name], elem_type):
                        return False
                else:
                    inferred[ta.name] = elem_type
        return True

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
        type_conforms_to_protocol: callable
    ) -> dict[str, TpyType] | None:
        """Infer type parameters from function arguments.

        Returns dict of inferred type params (e.g., {"T": Int32}) on success, None on failure.
        """
        if len(arg_types) != len(func.params):
            return None

        inferred: dict[str, TpyType] = {}
        for (pname, ptype), arg_type in zip(func.params, arg_types):
            if not self.match_type_with_inference(ptype, arg_type, inferred):
                return None

        # Resolve pending types for codegen (Python semantics)
        for k, v in list(inferred.items()):
            if isinstance(v, IntLiteralType):
                inferred[k] = BIGINT
            elif isinstance(v, PendingListType):
                # Resolve PendingListType to ListType
                elem_type = v.element_type
                if isinstance(elem_type, IntLiteralType):
                    elem_type = BIGINT
                inferred[k] = ListType(elem_type)

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
        arg_types: list[TpyType]
    ) -> dict[str, TpyType] | None:
        """Infer type parameters from constructor arguments for user-defined generic record.

        Returns dict of inferred type params (e.g., {"T": Int32}) on success, None on failure.
        """
        if len(arg_types) != len(record.init_params):
            return None

        inferred: dict[str, TpyType] = {}
        for (pname, ptype, _), arg_type in zip(record.init_params, arg_types):
            if not self.match_type_with_inference(ptype, arg_type, inferred):
                return None

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
        """
        inferred: dict[str, TpyType] = {}
        for param, arg_type in zip(params, arg_types):
            if not self.match_type_with_inference(param.type, arg_type, inferred):
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
        """Substitute type parameters in a method signature."""
        substituted_params = [
            ParamInfo(p.name, self.substitute_type_params(p.type, type_subst),
                      p.requires_lvalue, p.requires_mutable)
            for p in method.params
        ]
        substituted_return = self.substitute_type_params(method.return_type, type_subst)

        # Substitute type params in bounds
        substituted_bounds = {}
        if method.type_param_bounds:
            for param_name, bound in method.type_param_bounds.items():
                substituted_bounds[param_name] = self.substitute_type_params(bound, type_subst)

        return FunctionInfo(
            name=method.name,
            params=substituted_params,
            return_type=substituted_return,
            is_noalloc=method.is_noalloc,
            is_readonly=method.is_readonly,
            is_method=method.is_method,
            is_staticmethod=method.is_staticmethod,
            type_params=method.type_params,
            type_param_bounds=substituted_bounds if substituted_bounds else method.type_param_bounds,
            cpp_template=method.cpp_template,  # Preserve cpp_template for codegen
        )

    def get_deref_target_type(self, typ: TpyType) -> TpyType | None:
        """If typ has __deref__(), return resolved return type. Else None."""
        from tpyc import modules as builtin_modules

        record_info = self.ctx.registry.get_record_for_type(typ)
        if not record_info:
            return None
        overloads = record_info.get_method_overloads("__deref__")
        if not overloads:
            return None
        method = overloads[0]
        type_subst = builtin_modules.extract_type_params(typ)
        if not type_subst and isinstance(typ, NamedType) and typ.is_record:
            type_subst = self.build_type_substitution(typ)
        if type_subst:
            method = self.substitute_method_type_params(method, type_subst)
        return method.return_type

    def get_deref_coercion_target(self, typ: TpyType) -> TpyType | None:
        """Get the deref target for coercion purposes.

        Like get_deref_target_type() but excludes ConstPtr — record params
        are T& (mutable ref) but deref_ptr(const T*) returns const T&.
        """
        if isinstance(typ, ConstPtrType):
            return None
        return self.get_deref_target_type(typ)

    def _get_iterable_element_type_or_none(self, iterable_type: TpyType) -> TpyType | None:
        """Get the element type of an iterable, or None if not iterable.

        For types extending NativeIterable[T], returns T.
        """
        from ..typesys import StrType, CharType, CHAR

        # Handle NativeIterable[T] protocol type
        if is_protocol_type(iterable_type) and iterable_type.name == "NativeIterable":
            if iterable_type.type_args:
                first_arg = iterable_type.type_args[0]
                return first_arg if isinstance(first_arg, TpyType) else None
            return None

        # Handle str -> Char
        if isinstance(iterable_type, StrType):
            return CHAR

        # Use get_element_type() for container types (list, Array, Span, etc.)
        return iterable_type.get_element_type()
