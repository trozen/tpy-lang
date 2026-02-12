"""
TurboPython Type System

Defines the core types available in TurboPython:
- Int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- ConstPtr[T]: Read-only pointer (maps to const T*)
- Array[T, N], Span[T], list[T]: Container types
- User-defined records (classes)
- ModuleType: Generic parameterized types defined in the module system
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class TypeParamKind(Enum):
    """Kind of type parameter in a generic type."""
    TYPE = "type"  # A type parameter like T
    INT = "int"    # An integer literal like N


@dataclass(frozen=True)
class TpyType:
    """Base class for all TurboPython types."""

    def to_cpp(self) -> str:
        """Return the C++ representation of this type."""
        raise NotImplementedError

    def is_pointer(self) -> bool:
        """Return True if this is a pointer type."""
        return False

    def qualified_name(self) -> Optional[str]:
        """Return the fully qualified type name for module lookup, or None if not a module type."""
        return None

    def is_value_type(self) -> bool:
        """Return True if this is a value type (copy semantics).

        Value types include primitive types like Int32, BigInt, Bool, Char, str.
        These types should be copied when accessed from containers.

        Object types (RecordType, ListType, etc.) return False and should
        use reference semantics when accessed from containers.
        """
        return False

    def to_cpp_return(self) -> str:
        """Return the C++ representation for function return types.

        Value types return by value (T).
        Object types return by reference (T&) to avoid hidden copies.
        Use Own[T] when returning newly constructed objects by value.
        """
        if self.is_value_type():
            return self.to_cpp()
        return f"{self.to_cpp()}&"

    def to_cpp_return_const(self) -> str:
        """Return the C++ representation for const method return types.

        Value types return by value (T).
        Object types return by const reference (const T&) since the method
        cannot return a mutable reference to a member of a const object.
        """
        if self.is_value_type():
            return self.to_cpp()
        return f"const {self.to_cpp()}&"

    def to_cpp_param(self, name: str) -> str:
        """Return the C++ parameter declaration for this type.

        Value types (primitives, views) are passed by value: T name
        Object types (containers, records) are passed by mutable reference: T& name
        """
        if self.is_value_type():
            return f"{self.to_cpp()} {name}"
        return f"{self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        """Return the C++ const parameter declaration for this type.

        Value types are passed by value: T name
        Object types are passed by const reference: const T& name
        Use for constructor params and other contexts where mutation is not needed.
        """
        if self.is_value_type():
            return f"{self.to_cpp()} {name}"
        return f"const {self.to_cpp()}& {name}"

    def is_ref_param(self) -> bool:
        """Return True if this type is passed by mutable reference as a parameter."""
        return not self.is_value_type()

    def get_element_type(self) -> Optional['TpyType']:
        """Return the element type for container types, or None for non-containers."""
        return None

    def needs_explicit_element_target(self) -> bool:
        """Return True if array literals need explicit element type targeting.

        Array and Span need explicit conversions in their initializer lists.
        Dynamic containers (list, etc.) handle implicit conversions.
        """
        return False

    def inner_types(self) -> tuple['TpyType', ...]:
        """Return inner/wrapped types for traversal.

        Override in wrapper types (Own, Ptr, List, etc.) to expose inner types.
        """
        return ()

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        """Return copy of this type with new inner types.

        Override in wrapper types (Own, Ptr, List, etc.) to support reconstruction.
        """
        return self

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        """Apply fn to all inner/wrapped types and return new type."""
        inner = self.inner_types()
        if not inner:
            return self
        return self.with_inner_types(tuple(fn(t) for t in inner))


@dataclass(frozen=True)
class Int32Type(TpyType):
    """32-bit integer type."""

    def to_cpp(self) -> str:
        return "int32_t"

    def __str__(self) -> str:
        return "Int32"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Int32"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class VoidType(TpyType):
    """Void type (for functions returning nothing)."""

    def to_cpp(self) -> str:
        return "void"

    def __str__(self) -> str:
        return "None"

    def qualified_name(self) -> Optional[str]:
        return "builtins.None"

    def to_cpp_return(self) -> str:
        return "void"


@dataclass(frozen=True)
class StrType(TpyType):
    """String type."""

    def to_cpp(self) -> str:
        return "std::string_view"

    def __str__(self) -> str:
        return "str"

    def qualified_name(self) -> Optional[str]:
        return "builtins.str"

    def is_value_type(self) -> bool:
        return True

    def get_element_type(self) -> Optional['TpyType']:
        from tpyc.typesys import CHAR
        return CHAR


@dataclass(frozen=True)
class CharType(TpyType):
    """Character type (single character)."""

    def to_cpp(self) -> str:
        return "char"

    def __str__(self) -> str:
        return "Char"

    def qualified_name(self) -> Optional[str]:
        return "builtins.Char"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class BoolType(TpyType):
    """Boolean type."""

    def to_cpp(self) -> str:
        return "bool"

    def __str__(self) -> str:
        return "bool"

    def qualified_name(self) -> Optional[str]:
        return "builtins.bool"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class FloatType(TpyType):
    """64-bit floating point type (IEEE 754 double precision)."""

    def to_cpp(self) -> str:
        return "double"

    def __str__(self) -> str:
        return "float"

    def qualified_name(self) -> Optional[str]:
        return "builtins.float"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class BigIntType(TpyType):
    """Arbitrary precision integer: int -> tpy::BigInt"""

    def to_cpp(self) -> str:
        return "tpy::BigInt"

    def __str__(self) -> str:
        return "int"

    def qualified_name(self) -> Optional[str]:
        return "builtins.int"

    def is_value_type(self) -> bool:
        return True

    def to_cpp_param(self, name: str) -> str:
        # BigInt is expensive to copy, pass by const reference
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        # Same as to_cpp_param - BigInt always uses const reference
        return f"const {self.to_cpp()}& {name}"


@dataclass(frozen=True)
class RangeType(TpyType):
    """Range type: range() -> tpy::Range (lazy Int32 iterator)."""

    def to_cpp(self) -> str:
        return "tpy::Range"

    def __str__(self) -> str:
        return "range"

    def qualified_name(self) -> Optional[str]:
        return "builtins.range"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class IntLiteralType(TpyType):
    """Unresolved integer literal - can coerce to Int32 or BigInt.

    This type represents integer literals before they're resolved to a
    concrete type. It coerces to Int32 or BigInt based on context:
    - Int32 + IntLiteral -> Int32
    - BigInt + IntLiteral -> BigInt
    - IntLiteral + IntLiteral -> BigInt (Python default)
    """
    value: int = 0  # Store value for potential range checking

    def to_cpp(self) -> str:
        # Should be resolved before codegen; fallback to literal value
        return str(self.value)

    def __str__(self) -> str:
        return f"IntLiteral({self.value})"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class TypeParamRef(TpyType):
    """Unresolved type parameter reference (e.g., T in class Stack[T]).

    Used during parsing and semantic analysis of generic class definitions.
    When the generic class is instantiated with concrete types, TypeParamRef
    is substituted with the actual type.

    For TYPE kind (default):
    - C++ Code Generation Semantics (using tpy::is_value_type trait):
      - Parameters: Use `tpy::param_val_or_ref_t<T>` which resolves to:
        - `const T&` for value types (immutable, compiler optimizes small types)
        - `T&` for object types (allows mutation per Python semantics)
      - Returns: Use `tpy::return_val_or_ref_t<T>` which resolves to:
        - `T` for value types (return by value)
        - `T&` for object types (mutable reference, Python semantics)
      - Const returns: Use `tpy::return_val_or_cref_t<T>` which resolves to:
        - `T` for value types
        - `const T&` for object types

    For INT kind:
    - Represents a compile-time integer constant (e.g., N in Matrix[T, N: int])
    - Maps to std::size_t in C++
    - Can be used as values in expressions (e.g., Int32(N))

    Bounded type parameters (e.g., T: Comparable) store the bound protocol.
    """
    name: str
    bound: Optional['NamedType'] = None  # Must be a protocol (is_protocol=True)
    kind: TypeParamKind = TypeParamKind.TYPE

    def to_cpp(self) -> str:
        return self.name  # Template parameter name (works for both TYPE and INT)

    def __str__(self) -> str:
        return self.name

    def is_value_type(self) -> bool:
        if self.kind == TypeParamKind.INT:
            # INT type params are std::size_t values
            return True
        # Unknown at definition time - the trait decides at C++ instantiation
        return False

    def to_cpp_param(self, name: str) -> str:
        if self.kind == TypeParamKind.INT:
            # INT params are passed by value (they're std::size_t)
            return f"std::size_t {name}"
        # Use trait-based param type: const T& for value types, T& for object types
        return f"tpy::param_val_or_ref_t<{self.name}> {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.kind == TypeParamKind.INT:
            return f"std::size_t {name}"
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_return(self) -> str:
        if self.kind == TypeParamKind.INT:
            return "std::size_t"
        # Use trait-based return type: T for value types, T& for object types
        return f"tpy::return_val_or_ref_t<{self.name}>"

    def to_cpp_return_const(self) -> str:
        if self.kind == TypeParamKind.INT:
            return "std::size_t"
        # Use trait-based return type: T for value types, const T& for object types
        return f"tpy::return_val_or_cref_t<{self.name}>"


@dataclass(frozen=True)
class NamedType(TpyType):
    """A user-defined type (record or protocol).

    During parsing, is_protocol defaults to False (unknown).
    After registration in sema, is_protocol is set correctly.

    For generic types like Stack[T] or Sequence[T]:
    - type_args stores the concrete type arguments (e.g., (Int32,) for Stack[Int32])

    For generic records with integer type parameters like Matrix[T, N: int]:
    - type_args can contain both TpyType and int values (e.g., (Int32, 8))
    """
    name: str
    type_args: tuple['TpyType | int', ...] = ()
    is_protocol: bool = False

    @property
    def is_record(self) -> bool:
        """Return True if this is a record type (not a protocol)."""
        return not self.is_protocol

    def with_protocol_flag(self, is_protocol: bool) -> 'NamedType':
        """Return a copy with is_protocol set."""
        if self.is_protocol == is_protocol:
            return self
        return NamedType(self.name, self.type_args, is_protocol)

    def to_cpp(self) -> str:
        if self.is_protocol:
            # Template parameter placeholder - actual type substituted at instantiation
            return "T"
        if self.type_args:
            args = ", ".join(
                t.to_cpp() if isinstance(t, TpyType) else str(t)
                for t in self.type_args
            )
            return f"{self.name}<{args}>"
        return self.name

    def __str__(self) -> str:
        if self.type_args:
            args = ", ".join(
                str(t) if isinstance(t, TpyType) else str(t)
                for t in self.type_args
            )
            return f"{self.name}[{args}]"
        return self.name

    def qualified_name(self) -> Optional[str]:
        if self.is_protocol:
            return f"typing.{self.name}"
        return None

    def is_value_type(self) -> bool:
        # Protocol-typed params are passed by const ref
        # Records are object types (not value types)
        return False

    def inner_types(self) -> tuple['TpyType', ...]:
        # Only return actual types, skip integer values
        return tuple(t for t in self.type_args if isinstance(t, TpyType))

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        # Reconstruct type_args preserving integer positions
        new_args: list[TpyType | int] = []
        type_iter = iter(types)
        for arg in self.type_args:
            if isinstance(arg, TpyType):
                new_args.append(next(type_iter))
            else:
                new_args.append(arg)  # Keep integer as-is
        return NamedType(self.name, tuple(new_args), self.is_protocol)


@dataclass(frozen=True)
class SelfType(TpyType):
    """Self type for protocol method signatures.

    Represents the implementing type in protocol method signatures.
    When checking if Int32 conforms to a protocol with Self, Self is
    substituted with Int32.
    """

    def to_cpp(self) -> str:
        # Template parameter in concepts - the implementing type
        return "T"

    def __str__(self) -> str:
        return "Self"


SELF = SelfType()


@dataclass(frozen=True)
class SuperType(TpyType):
    """Super proxy type returned by super() call.

    When super() is called in a method, it returns a SuperType that wraps
    the parent class type. Method calls on SuperType resolve to parent methods.

    For example, in:
        class Dog(Animal):
            def __init__(self, name: str):
                super().__init__(name)  # Returns SuperType(parent_type=Animal)

    The super().__init__(name) call resolves to calling Animal.__init__.

    The parent_type can be either a RecordType (user-defined class) or a
    builtin type like StaticListType.
    """
    parent_type: 'TpyType'  # Can be RecordType or builtin type
    child_record_name: str

    def to_cpp(self) -> str:
        return self.parent_type.to_cpp()

    def __str__(self) -> str:
        return f"super[{self.parent_type}]"


@dataclass(frozen=True)
class PtrType(TpyType):
    """Mutable pointer type: Ptr[T] -> T*"""
    pointee: TpyType

    def to_cpp(self) -> str:
        return f"{self.pointee.to_cpp()}*"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"Ptr[{self.pointee}]"

    def is_value_type(self) -> bool:
        # Pointers are small values, passed/returned by value
        return True

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.pointee,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return PtrType(types[0])


@dataclass(frozen=True)
class ConstPtrType(TpyType):
    """Read-only pointer type: ConstPtr[T] -> const T*"""
    pointee: TpyType

    def to_cpp(self) -> str:
        return f"const {self.pointee.to_cpp()}*"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"ConstPtr[{self.pointee}]"

    def is_value_type(self) -> bool:
        # Pointers are small values, passed/returned by value
        return True

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.pointee,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ConstPtrType(types[0])


@dataclass(frozen=True)
class OwnType(TpyType):
    """Owned type - passed/returned by value (ownership transfer).

    Own[T] wraps a type to indicate ownership transfer - the value is
    moved/copied, not referenced. Used for function parameters and returns.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        return self.wrapped.to_cpp()

    def is_value_type(self) -> bool:
        # Own[T] is always passed by value (ownership transfer)
        return True

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def get_element_type(self) -> Optional['TpyType']:
        # Forward to wrapped type for Own[list[T]] etc.
        return self.wrapped.get_element_type()

    def __str__(self) -> str:
        return f"Own[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return OwnType(types[0])


@dataclass(frozen=True)
class NoneType(TpyType):
    """The type of the None literal (distinct from VoidType which is for return types)."""

    def to_cpp(self) -> str:
        return "std::nullptr_t"

    def __str__(self) -> str:
        return "None"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class OptionalType(TpyType):
    """Nullable wrapper: T | None.

    For non-value inner types, maps to T* (nullable pointer) in locals/params/returns.
    The canonical storage form (std::optional<T>) is reserved for future class members.
    """
    inner: TpyType

    def to_cpp(self) -> str:
        return f"std::optional<{self.inner.to_cpp()}>"

    def is_value_type(self) -> bool:
        return self.inner.is_value_type()

    def to_cpp_return(self) -> str:
        if not self.inner.is_value_type():
            return f"{self.inner.to_cpp()}*"
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        if not self.inner.is_value_type():
            return f"const {self.inner.to_cpp()}*"
        return self.to_cpp()

    def to_cpp_param(self, name: str) -> str:
        if not self.inner.is_value_type():
            return f"{self.inner.to_cpp()}* {name}"
        return f"{self.to_cpp()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if not self.inner.is_value_type():
            return f"const {self.inner.to_cpp()}* {name}"
        return f"{self.to_cpp()} {name}"

    def is_ref_param(self) -> bool:
        # Optional params are T* (pointer), not T& (reference)
        return False

    def get_element_type(self) -> Optional['TpyType']:
        return self.inner.get_element_type()

    def __str__(self) -> str:
        return f"{self.inner} | None"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.inner,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return OptionalType(types[0])


@dataclass(frozen=True)
class ArrayType(TpyType):
    """Fixed-size array: Array[T, N] -> std::array<T, N>"""
    element_type: TpyType
    size: "int | TypeParamRef"  # Can be literal int or forwarded INT type param

    def to_cpp(self) -> str:
        size_cpp = self.size.name if isinstance(self.size, TypeParamRef) else str(self.size)
        return f"std::array<{self.element_type.to_cpp()}, {size_cpp}>"

    def __str__(self) -> str:
        size_str = self.size.name if isinstance(self.size, TypeParamRef) else str(self.size)
        return f"Array[{self.element_type}, {size_str}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Array"

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def needs_explicit_element_target(self) -> bool:
        return True

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ArrayType(types[0], self.size)


@dataclass(frozen=True)
class SpanType(TpyType):
    """Non-owning read-only view: Span[T] -> std::span<const T>"""
    element_type: TpyType

    def to_cpp(self) -> str:
        return f"std::span<const {self.element_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"Span[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Span"

    def is_value_type(self) -> bool:
        # Spans are lightweight views (ptr + size), passed/returned by value
        return True

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def needs_explicit_element_target(self) -> bool:
        return True

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return SpanType(types[0])


@dataclass(frozen=True)
class ListType(TpyType):
    """Dynamic list: list[T] -> std::vector<T>"""
    element_type: TpyType

    def to_cpp(self) -> str:
        return f"std::vector<{self.element_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"list[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "builtins.list"

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ListType(types[0])


@dataclass(frozen=True)
class PendingListType(TpyType):
    """Unresolved list literal type - becomes Array or list based on usage.

    This type is assigned to list literals in function-local contexts during
    the first analysis phase. After analyzing the full function, we resolve
    pending types based on collected usage information (mutation, parameter passing).
    """
    element_type: TpyType
    size: int
    literal_id: int

    def to_cpp(self) -> str:
        raise RuntimeError(f"PendingListType should be resolved before codegen (literal_id={self.literal_id})")

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def __str__(self) -> str:
        return f"PendingList[{self.element_type}, {self.size}]#{self.literal_id}"

    def qualified_name(self) -> Optional[str]:
        return "builtins.list"


@dataclass(frozen=True)
class ModuleType(TpyType):
    """A parameterized type fully defined in the module system.

    This type gets all its behavior (to_cpp, methods, etc.) from the module
    definition rather than having it hardcoded in the class. Used for types
    that don't need special compiler treatment.
    """
    _qualified_name: str  # e.g., "mymodule.MyContainer"
    _type_args: tuple  # e.g., (Int32Type(), 8) for MyContainer[Int32, 8]

    def qualified_name(self) -> Optional[str]:
        return self._qualified_name

    def _get_type_def(self):
        """Look up the module definition for this type."""
        from tpyc.modules import lookup_type
        type_def = lookup_type(self._qualified_name)
        if type_def is None:
            raise RuntimeError(f"ModuleType '{self._qualified_name}' not found in module system")
        return type_def

    def _get_param_map(self) -> dict[str, 'TpyType | int']:
        """Build a mapping from type param names to their values."""
        type_def = self._get_type_def()
        return dict(zip(type_def.type_params, self._type_args))

    def to_cpp(self) -> str:
        type_def = self._get_type_def()
        cpp = type_def.cpp_type
        param_map = self._get_param_map()
        for name, value in param_map.items():
            if isinstance(value, TpyType):
                cpp = cpp.replace(f"{{{name}}}", value.to_cpp())
            else:
                cpp = cpp.replace(f"{{{name}}}", str(value))
        return cpp

    def __str__(self) -> str:
        # Extract simple name from qualified name (e.g., "MyType" from "mymodule.MyType")
        simple_name = self._qualified_name.split(".")[-1]
        args_str = ", ".join(
            str(arg) if isinstance(arg, TpyType) else str(arg)
            for arg in self._type_args
        )
        return f"{simple_name}[{args_str}]"

    def get_element_type(self) -> Optional['TpyType']:
        # Convention: first TYPE param is the element type
        from tpyc.modules import TypeParamKind
        type_def = self._get_type_def()
        for i, kind in enumerate(type_def.param_kinds):
            if kind == TypeParamKind.TYPE and i < len(self._type_args):
                arg = self._type_args[i]
                if isinstance(arg, TpyType):
                    return arg
        return None

    def inner_types(self) -> tuple['TpyType', ...]:
        from tpyc.modules import TypeParamKind
        type_def = self._get_type_def()
        result = []
        for i, arg in enumerate(self._type_args):
            if i < len(type_def.param_kinds) and type_def.param_kinds[i] == TypeParamKind.TYPE:
                if isinstance(arg, TpyType):
                    result.append(arg)
        return tuple(result)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        from tpyc.modules import TypeParamKind
        type_def = self._get_type_def()
        new_args = []
        type_idx = 0
        for i, arg in enumerate(self._type_args):
            if i < len(type_def.param_kinds) and type_def.param_kinds[i] == TypeParamKind.TYPE:
                if isinstance(arg, TpyType):
                    new_args.append(types[type_idx])
                    type_idx += 1
                else:
                    new_args.append(arg)
            else:
                new_args.append(arg)
        return ModuleType(self._qualified_name, tuple(new_args))


@dataclass
class ListLiteralInfo:
    """Tracks usage information for a list literal to determine its resolved type."""
    literal_id: int
    expr: 'TpyArrayLiteral'  # Forward reference to avoid circular import
    element_type: TpyType
    size: int
    variable_name: Optional[str] = None
    is_global: bool = False
    is_mutated: bool = False
    passed_to_list_param: bool = False
    passed_to_span_param: bool = False
    has_explicit_annotation: bool = False
    explicit_type: Optional[TpyType] = None
    coerced_element_type: Optional[TpyType] = None  # Element type from typed param (list[T] or Span[T])
    resolved_type: Optional[TpyType] = None


# Singleton instances for built-in types
INT32 = Int32Type()
VOID = VoidType()
STR = StrType()
CHAR = CharType()
BOOL = BoolType()
FLOAT = FloatType()
BIGINT = BigIntType()
NONE = NoneType()
RANGE = RangeType()

# Int32 range limits
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


def is_protocol_type(typ: TpyType) -> bool:
    """Check if a type is a protocol type."""
    return isinstance(typ, NamedType) and typ.is_protocol


@dataclass
class FieldInfo:
    """Information about a record field."""
    name: str
    type: TpyType
    default_value: Optional[str] = None
    loc: Optional[Any] = None  # SourceLocation from parse.py (avoid circular import)


@dataclass
class RecordInfo:
    """Information about a user-defined record (class) or builtin type.

    For generic records like Stack[T]:
    - type_params stores the type parameter names (e.g., ["T"])
    - type_param_kinds stores the kind of each type param (TYPE or INT)
    - type_param_bounds stores bounds for each type param (e.g., {"T": Comparable})

    For generic records with integer type params like Matrix[T, N: int]:
    - type_params = ["T", "N"]
    - type_param_kinds = [TYPE, INT]

    For class inheritance:
    - parent stores the parent type (user-defined RecordType or builtin TpyType)
    - implemented_protocols stores explicitly declared protocol implementations

    For builtin types:
    - cpp_type stores the C++ type template (e.g., "std::vector<{T}>")
    - extends_protocols stores protocol implementations (e.g., ["NativeIterable[T]"])
    - Methods have cpp_template for codegen
    - constructors stores constructor overloads with cpp_template
    """
    name: str
    fields: list[FieldInfo]
    has_init: bool = False
    init_params: list[tuple[str, TpyType, Optional[str]]] = field(default_factory=list)  # (name, type, default)
    methods: dict[str, list['FunctionInfo']] = field(default_factory=dict)  # method_name -> list of overloads
    constructors: list['FunctionInfo'] = field(default_factory=list)  # Constructor overloads (for unified handling)
    type_params: list[str] = field(default_factory=list)  # ["T", "U"] for class Stack[T, U]
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)  # [TYPE, INT] for class Matrix[T, N: int]
    type_param_bounds: dict[str, 'NamedType'] = field(default_factory=dict)  # {"T": Comparable} (must be protocols)
    parent: Optional['TpyType'] = None  # Parent type (NamedType or builtin TpyType)
    implemented_protocols: list['NamedType'] = field(default_factory=list)  # Explicit protocol implementations
    extends_protocols: list[str] = field(default_factory=list)  # Protocol extensions: ["NativeIterable[T]"]
    cpp_type: Optional[str] = None  # C++ type template for builtins

    def get_method(self, name: str) -> Optional['FunctionInfo']:
        """Get first overload of a method (for single-overload cases)."""
        overloads = self.methods.get(name)
        return overloads[0] if overloads else None

    def get_method_overloads(self, name: str) -> list['FunctionInfo']:
        """Get all overloads for a method."""
        return self.methods.get(name, [])

    def is_generic(self) -> bool:
        """Return True if this is a generic record with type parameters."""
        return bool(self.type_params)


@dataclass
class FunctionInfo:
    """Information about a function.

    For generic functions like def first[T](items: list[T]) -> T:
    - type_params stores the type parameter names (e.g., ["T"])
    - type_param_bounds stores bounds for each type param (e.g., {"T": Comparable})

    For builtin methods:
    - cpp_template stores the C++ code template (e.g., "{self}.push_back({0})")

    For builtin global functions (len, chr, etc.):
    - is_builtin_function = True
    - special_handling = True if sema/codegen handle it specially (skip overload matching)
    """
    name: str
    params: list[tuple[str, TpyType]]  # (name, type)
    return_type: TpyType
    is_noalloc: bool = False
    is_readonly: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, 'NamedType'] = field(default_factory=dict)
    cpp_template: Optional[str] = None  # For builtins: "{self}.push_back({0})"
    is_builtin_function: bool = False  # True for global builtins (len, chr, etc.)
    special_handling: bool = False  # True if sema/codegen handle specially

    def is_generic(self) -> bool:
        """Return True if this is a generic function with type parameters."""
        return bool(self.type_params)


@dataclass
class ResolvedBinop:
    """Result of binary operator resolution in sema.

    Stores all info needed by codegen to generate the operation.
    """
    method: FunctionInfo
    left_wrapper: str   # cpp template for left, e.g., "tpy::BigInt({expr})" or "{expr}"
    right_wrapper: str  # cpp template for right
    is_reverse: bool = False  # True if using reverse operator (swap {self} and {0})
    receiver_type: 'TpyType | None' = None  # Type of the receiver ({self})


@dataclass
class ResolvedUnaryop:
    """Result of unary operator resolution in sema."""
    method: FunctionInfo


@dataclass
class MethodSignature:
    """Method signature required by a protocol."""
    name: str
    params: list[tuple[str, TpyType]]  # (param_name, param_type)
    return_type: TpyType


@dataclass
class ProtocolInfo:
    """Information about a protocol definition.

    For generic protocols like Sequence[T]:
    - type_params stores the type parameter names (e.g., ["T"])

    For builtin protocols:
    - cpp_concept stores the C++ concept name (e.g., "tpy::Sized")
    - is_marker indicates protocols with no methods (require explicit extends)

    For protocol inheritance:
    - parent_protocols stores names of parent protocols this protocol extends
    """
    name: str
    methods: list[MethodSignature]
    fields: list[tuple[str, TpyType]] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)
    parent_protocols: list[str] = field(default_factory=list)
    cpp_concept: str | None = None  # C++ concept name for builtin protocols
    is_marker: bool = False  # Marker protocols require explicit extends


@dataclass
class ModuleVarInfo:
    """Information about a module-level variable."""
    name: str
    type: TpyType
    cpp_expr: str  # C++ expression to access the variable


@dataclass
class ModuleInfo:
    """Information about a module (builtin or user-defined)."""
    name: str
    is_builtin: bool = True  # True for builtin modules (time/sys/math), False for user modules
    functions: dict[str, list[FunctionInfo]] = field(default_factory=dict)  # func_name -> overloads
    variables: dict[str, ModuleVarInfo] = field(default_factory=dict)  # var_name -> ModuleVarInfo
    records: dict[str, RecordInfo] = field(default_factory=dict)  # type_name -> RecordInfo (exported types)
    protocols: dict[str, ProtocolInfo] = field(default_factory=dict)  # protocol_name -> ProtocolInfo


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self):
        self.records: dict[str, RecordInfo] = {}
        self.builtin_records: dict[str, RecordInfo] = {}  # By qualified name (e.g., "builtins.list")
        self.functions: dict[str, FunctionInfo] = {}  # User-defined functions
        self.builtin_function_overloads: dict[str, list[FunctionInfo]] = {}  # Builtin function overloads
        self.protocols: dict[str, ProtocolInfo] = {}
        self.modules: dict[str, ModuleInfo] = {}  # module_name -> ModuleInfo
        # Fundamental types not in module system (pointer wrappers)
        self._fundamental_types = {"Ptr", "ConstPtr", "Own"}

    def register_record(self, info: RecordInfo, name: str | None = None) -> None:
        """Register a record type.

        Args:
            info: The record info to register.
            name: Optional name to register under (defaults to info.name).
                  Used for imported records that may have a local alias.
        """
        self.records[name or info.name] = info

    def register_builtin_record(self, qname: str, info: RecordInfo) -> None:
        """Register a builtin type's RecordInfo by its qualified name."""
        self.builtin_records[qname] = info

    def register_function(self, info: FunctionInfo, name: str | None = None) -> None:
        """Register a function.

        Args:
            info: The function info to register.
            name: Optional name to register under (defaults to info.name).
                  Used for imported functions that may have a local alias.
        """
        self.functions[name or info.name] = info

    def register_builtin_function_overloads(self, name: str, overloads: list[FunctionInfo]) -> None:
        """Register builtin function overloads by name."""
        self.builtin_function_overloads[name] = overloads

    def get_builtin_function_overloads(self, name: str) -> list[FunctionInfo]:
        """Get builtin function overloads by name."""
        return self.builtin_function_overloads.get(name, [])

    def register_protocol(self, info: ProtocolInfo, name: str | None = None) -> None:
        """Register a protocol type.

        Args:
            info: The protocol info to register.
            name: Optional name to register under (defaults to info.name).
                  Used for imported protocols that may have a local alias.
        """
        self.protocols[name or info.name] = info

    def register_module(self, info: ModuleInfo) -> None:
        """Register a module by name."""
        self.modules[info.name] = info

    def get_module(self, name: str) -> Optional[ModuleInfo]:
        """Get a module by name."""
        return self.modules.get(name)

    def get_record(self, name: str) -> Optional[RecordInfo]:
        return self.records.get(name)

    def get_builtin_record(self, qname: str) -> Optional[RecordInfo]:
        """Get a builtin record by qualified name."""
        return self.builtin_records.get(qname)

    def get_record_for_type(self, tpy_type: 'TpyType') -> Optional[RecordInfo]:
        """Unified lookup for any type's RecordInfo.

        For user records (NamedType with is_record), looks up by name in self.records.
        For builtin types, looks up by qualified_name in self.builtin_records.
        """
        if isinstance(tpy_type, NamedType) and tpy_type.is_record:
            return self.records.get(tpy_type.name)
        qname = tpy_type.qualified_name()
        if qname:
            return self.builtin_records.get(qname)
        return None

    def get_function(self, name: str) -> Optional[FunctionInfo]:
        return self.functions.get(name)

    def get_protocol(self, name: str) -> Optional[ProtocolInfo]:
        return self.protocols.get(name)

    def is_known_type(self, name: str) -> bool:
        """Check if a name refers to a known type."""
        if name in self._fundamental_types:
            return True
        if name in self.records or name in self.protocols:
            return True
        # Check module system for registered types
        from tpyc.modules import get_builtins, get_tpy
        for module in [get_builtins(), get_tpy()]:
            qualified = f"{module.name}.{name}"
            if qualified in module.types:
                return True
        return False
