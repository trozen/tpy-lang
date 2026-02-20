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


# Native C++ name mapping for @native/@native_c records.
# Maps Python class name -> C++ name (e.g., "Rect" -> "SDL_Rect").
# Used by NamedType.to_cpp() so composite types like Ptr[Rect] resolve correctly.
# NOTE: Global mutable state -- safe because the compilation pipeline is sequential
# (each CodeGenerator.generate() call clears and repopulates before use).
# Would need to move into CodeGenContext if codegen ever runs concurrently.
_native_cpp_names: dict[str, str] = {}


def register_native_cpp_name(py_name: str, cpp_name: str) -> None:
    """Register a mapping from a Python class name to its native C++ name."""
    _native_cpp_names[py_name] = cpp_name


def clear_native_cpp_names() -> None:
    """Clear all native C++ name mappings (called per-compilation)."""
    _native_cpp_names.clear()


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
class FixedIntType(TpyType):
    """Fixed-width integer type (Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64)."""
    bits: int = 32
    signed: bool = True

    @property
    def min_value(self) -> int:
        if self.signed:
            return -(2 ** (self.bits - 1))
        return 0

    @property
    def max_value(self) -> int:
        if self.signed:
            return 2 ** (self.bits - 1) - 1
        return 2 ** self.bits - 1

    def to_cpp(self) -> str:
        prefix = "int" if self.signed else "uint"
        return f"{prefix}{self.bits}_t"

    def __str__(self) -> str:
        prefix = "Int" if self.signed else "UInt"
        return f"{prefix}{self.bits}"

    def qualified_name(self) -> Optional[str]:
        return f"tpy.{self}"

    def is_value_type(self) -> bool:
        return True


# Backward compat alias -- isinstance(x, Int32Type) matches any FixedIntType
Int32Type = FixedIntType


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
    """Range type: range() -> tpy::Range<T> (lazy iterator over T)."""
    elem: "TpyType"

    def to_cpp(self) -> str:
        return f"tpy::Range<{self.elem.to_cpp()}>"

    def __str__(self) -> str:
        return f"Range[{self.elem}]"

    def qualified_name(self) -> Optional[str]:
        return "builtins.Range"

    def get_element_type(self) -> Optional["TpyType"]:
        return self.elem

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class IntLiteralType(TpyType):
    """Unresolved integer literal - can coerce to Int32 or BigInt.

    This type represents integer literals before they're resolved to a
    concrete type. It coerces to Int32 or BigInt based on context:
    - Int32 + IntLiteral -> Int32
    - BigInt + IntLiteral -> BigInt
    - IntLiteral + IntLiteral -> configured default (Int32 by default)

    value tracks the known literal value (including computed results from
    constant-folded binops like 2+3). None means the value is unknown.
    """
    value: int | None = None

    def to_cpp(self) -> str:
        # Should be resolved before codegen; fallback to literal value
        if self.value is None:
            return "0"
        return str(self.value)

    def __str__(self) -> str:
        if self.value is None:
            return "IntLiteral"
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
        # Check for native C++ name mapping (@native/@native_c records)
        cpp_name = _native_cpp_names.get(self.name, self.name)
        if self.type_args:
            args = ", ".join(
                t.to_cpp() if isinstance(t, TpyType) else str(t)
                for t in self.type_args
            )
            return f"{cpp_name}<{args}>"
        return cpp_name

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

    def qualified_name(self) -> Optional[str]:
        return "tpy.Ptr"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"Ptr[{self.pointee}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Ptr"

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

    def qualified_name(self) -> Optional[str]:
        return "tpy.ConstPtr"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"ConstPtr[{self.pointee}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.ConstPtr"

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
class ReadonlyType(TpyType):
    """Readonly reference -- immutable view of T. Maps to const T& in C++."""
    wrapped: TpyType

    def to_cpp(self) -> str:
        return self.wrapped.to_cpp()

    def is_value_type(self) -> bool:
        return self.wrapped.is_value_type()

    def to_cpp_param(self, name: str) -> str:
        return self.wrapped.to_cpp_const_param(name)

    def to_cpp_const_param(self, name: str) -> str:
        return self.wrapped.to_cpp_const_param(name)

    def to_cpp_return(self) -> str:
        return self.wrapped.to_cpp_return_const()

    def to_cpp_return_const(self) -> str:
        return self.wrapped.to_cpp_return_const()

    def is_ref_param(self) -> bool:
        return False

    def get_element_type(self) -> Optional['TpyType']:
        return self.wrapped.get_element_type()

    def __str__(self) -> str:
        return f"readonly[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ReadonlyType(types[0])


def unwrap_readonly(typ: 'TpyType') -> 'TpyType':
    """Strip ReadonlyType wrapper if present, returning the inner type."""
    if isinstance(typ, ReadonlyType):
        return typ.wrapped
    return typ


def unwrap_optional_own(t: 'TpyType') -> 'OwnType | None':
    """Extract OwnType from Own[T] or Own[T] | None."""
    if isinstance(t, OwnType):
        return t
    if isinstance(t, OptionalType) and isinstance(t.inner, OwnType):
        return t.inner
    return None


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
class UnionType(TpyType):
    """Union of multiple types: A | B | C -> std::variant<A, B, C>.

    Members are stored in canonical sorted order for deterministic eq/hash.
    NoneType is always last if present (maps to std::monostate).
    """
    members: tuple[TpyType, ...]

    def to_cpp(self) -> str:
        cpp_members = [
            "std::monostate" if isinstance(m, (NoneType, VoidType)) else m.to_cpp()
            for m in self.members
        ]
        return f"std::variant<{', '.join(cpp_members)}>"

    def has_none_member(self) -> bool:
        return any(isinstance(m, (NoneType, VoidType)) for m in self.members)

    def is_value_type(self) -> bool:
        return all(m.is_value_type() for m in self.members)

    def to_cpp_param(self, name: str) -> str:
        if self.is_value_type():
            return f"const {self.to_cpp()}& {name}"
        return f"{self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        return self.to_cpp()

    def __str__(self) -> str:
        parts = [
            "None" if isinstance(m, (NoneType, VoidType)) else str(m)
            for m in self.members
        ]
        return " | ".join(parts)

    def inner_types(self) -> tuple['TpyType', ...]:
        return self.members

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return make_union(*types)


def make_union(*types: TpyType) -> TpyType:
    """Normalize a sequence of types into a canonical union form.

    - Flattens nested UnionType/OptionalType members
    - Deduplicates by structural equality
    - Single type collapses to itself
    - Single type + None -> OptionalType(T)
    - Multiple types +/- None -> UnionType(sorted..., [NoneType])
    """
    # Flatten nested unions and optionals
    flat: list[TpyType] = []
    has_none = False
    for t in types:
        if isinstance(t, UnionType):
            for m in t.members:
                if isinstance(m, (NoneType, VoidType)):
                    has_none = True
                else:
                    flat.append(m)
        elif isinstance(t, OptionalType):
            has_none = True
            flat.append(t.inner)
        elif isinstance(t, (NoneType, VoidType)):
            has_none = True
        else:
            flat.append(t)

    # Deduplicate preserving order
    seen: set[TpyType] = set()
    deduped: list[TpyType] = []
    for t in flat:
        if t not in seen:
            seen.add(t)
            deduped.append(t)

    # Sort by string representation for canonical order
    deduped.sort(key=lambda t: str(t))

    if len(deduped) == 0:
        # Only None members -- caller should handle this
        return VoidType()
    elif len(deduped) == 1 and not has_none:
        return deduped[0]
    elif len(deduped) == 1 and has_none:
        return OptionalType(deduped[0])
    else:
        members = ((NoneType(),) if has_none else ()) + tuple(deduped)
        return UnionType(members)


def union_none_narrow(union: UnionType) -> tuple[TpyType, TpyType]:
    """Compute narrowing facts for a nullable union's None check.

    Returns (non_none_type, none_type) where non_none_type is the union
    with NoneType/VoidType members removed.
    """
    non_none = [m for m in union.members if not isinstance(m, (NoneType, VoidType))]
    return make_union(*non_none), NoneType()


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
INT8 = FixedIntType(8, True)
INT16 = FixedIntType(16, True)
INT32 = FixedIntType(32, True)
INT64 = FixedIntType(64, True)
UINT8 = FixedIntType(8, False)
UINT16 = FixedIntType(16, False)
UINT32 = FixedIntType(32, False)
UINT64 = FixedIntType(64, False)
ALL_FIXED_INTS = [INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64]

VOID = VoidType()
STR = StrType()
CHAR = CharType()
BOOL = BoolType()
FLOAT = FloatType()
BIGINT = BigIntType()
NONE = NoneType()
RANGE = RangeType(INT32)

# Backward-compat range limits (use type.min_value / type.max_value instead)
INT32_MIN = INT32.min_value
INT32_MAX = INT32.max_value


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
    native_name: Optional[str] = None  # C++ name for @native/@native_c records (e.g., "SDL_Rect")
    is_native: bool = False       # True for @native or @native_c records
    is_native_c: bool = False     # True for @native_c specifically
    is_nocopy: bool = False       # True for @nocopy records (copy deleted, move-only)
    has_del: bool = False          # True if class declares __del__ (needs drop flag)

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


class FunctionLinkage(Enum):
    """Linkage mode for functions."""
    DEFAULT = "default"
    NATIVE = "native"        # C++ import (stub, no body)
    NATIVE_C = "native_c"    # C import (stub, no body)
    EXTERN_C = "extern_c"    # C export (has body)


@dataclass
class ParamInfo:
    """Function parameter with optional constraints.

    Tuple-compatible: unpacks as (name, type) for backward compat with
    existing ``for pname, ptype in func.params`` patterns.
    """
    name: str
    type: TpyType
    requires_lvalue: bool = False
    requires_mutable: bool = False

    def __iter__(self):
        yield self.name
        yield self.type


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
    params: list[ParamInfo]
    return_type: TpyType
    is_noalloc: bool = False
    is_readonly: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: Optional[str] = None
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, 'NamedType'] = field(default_factory=dict)
    cpp_template: Optional[str] = None  # For builtins: "{self}.push_back({0})"
    is_builtin_function: bool = False  # True for global builtins (len, chr, etc.)
    special_handling: bool = False  # True if sema/codegen handle specially
    qualified_name: str = ""  # Full dotted path, e.g. "builtins.print", "tpy.copy", "__main__.foo"

    @property
    def is_native_import(self) -> bool:
        return self.linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C)

    @property
    def is_stub(self) -> bool:
        return self.is_native_import

    @property
    def is_extern_c(self) -> bool:
        return self.linkage == FunctionLinkage.EXTERN_C

    @property
    def is_native_c(self) -> bool:
        return self.linkage == FunctionLinkage.NATIVE_C

    @property
    def is_native(self) -> bool:
        return self.linkage == FunctionLinkage.NATIVE

    @property
    def is_extern_cpp(self) -> bool:
        """Backward compat: @native maps to old is_extern_cpp."""
        return self.linkage == FunctionLinkage.NATIVE

    @property
    def extern_name(self) -> Optional[str]:
        """Backward compat alias for native_name."""
        return self.native_name

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


IMPLICIT_READONLY_METHODS = frozenset({
    "__len__", "__getitem__", "__str__", "__repr__", "__hash__", "__eq__", "__ne__",
    "__lt__", "__le__", "__gt__", "__ge__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
    "__and__", "__or__", "__xor__", "__lshift__", "__rshift__",
    "__radd__", "__rsub__", "__rmul__", "__rtruediv__", "__rfloordiv__", "__rmod__", "__rpow__",
    "__neg__", "__pos__", "__invert__",
})


@dataclass
class MethodSignature:
    """Method signature required by a protocol."""
    name: str
    params: list[tuple[str, TpyType]]  # (param_name, param_type)
    return_type: TpyType
    is_readonly: bool = False
    readonly_opt_out: bool = False


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
    is_readonly: bool = False  # All methods are read-only (safe for readonly[T] args)


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
        self._fundamental_types = {"Own"}

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


def local_var_is_movable(
        name: str,
        hoisted: set[str],
        reassigned: set[str],
        lvalue_reassigned: set[str],
        init_is_rvalue: bool) -> bool:
    """Return True if a local variable has owned (movable) storage.

    Single source of truth for auto-move eligibility shared between sema
    (tpyc/sema/compatibility.py) and codegen (tpyc/codegen_cpp/statements.py).

    Parameters
    ----------
    name : variable name
    hoisted : vars that escaped to outer scope (become T* pointer-locals)
    reassigned : vars assigned more than once (prescan)
    lvalue_reassigned : reassigned vars with at least one lvalue binding
    init_is_rvalue : True when the variable's initial/declaring binding is
        an rvalue (owned) -- callers supply this from their own context:
        sema uses ``name in ctx.rvalue_vars``,
        codegen uses ``init is None or ctx.is_rvalue_source(init)``.
    """
    if name in hoisted:
        return False
    if name in reassigned:
        # Movable only when ALL bindings are rvalue-owned
        return name not in lvalue_reassigned and init_is_rvalue
    return init_is_rvalue
