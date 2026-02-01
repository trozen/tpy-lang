"""
TurboPython Type System

Defines the types available in TurboPython:
- Int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- ConstPtr[T]: Read-only pointer (maps to const T*)
- StaticList[T, N]: Fixed-capacity container
- User-defined records (classes)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional


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

    def is_iterable(self) -> bool:
        """Return True if this type can be iterated over (for-each loops)."""
        return False

    def is_sequence(self) -> bool:
        """Return True if this type is a sequence container (list, Array, Span, StaticList).

        Unlike is_iterable(), this excludes str. Used for "Iterable" params in
        methods like extend() where element type compatibility matters.
        """
        return False

    def to_cpp_from_range(self, range_expr: str, elem_cpp: str) -> Optional[str]:
        """Return C++ expression to construct this type from a range, or None if not supported."""
        return None

    def needs_explicit_element_target(self) -> bool:
        """Return True if array literals need explicit element type targeting.

        Array and Span need explicit conversions in their initializer lists.
        List and StaticList handle implicit conversions.
        """
        return False

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        """Apply fn to all inner/wrapped types and return new type.

        Override in wrapper types (Own, Ptr, List, etc.) to transform inner types.
        Default implementation returns self (no inner types to transform).
        """
        return self


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

    def is_iterable(self) -> bool:
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

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class BoolType(TpyType):
    """Boolean type."""

    def to_cpp(self) -> str:
        return "bool"

    def __str__(self) -> str:
        return "Bool"

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
class RecordType(TpyType):
    """User-defined record type (class)."""
    name: str

    def to_cpp(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ProtocolType(TpyType):
    """Protocol type for structural subtyping.

    A protocol defines required methods. Any type implementing those methods
    satisfies the protocol. Compiles to C++20 concepts.

    For generic protocols like Sequence[T]:
    - type_args stores the concrete type arguments (e.g., (Int32,) for Sequence[Int32])
    """
    name: str
    type_args: tuple[TpyType, ...] = ()

    def to_cpp(self) -> str:
        # Template parameter placeholder - actual type substituted at instantiation
        return "T"

    def __str__(self) -> str:
        if self.type_args:
            args = ", ".join(str(t) for t in self.type_args)
            return f"{self.name}[{args}]"
        return self.name

    def qualified_name(self) -> Optional[str]:
        return f"typing.{self.name}"

    def is_value_type(self) -> bool:
        # Protocol-typed params are passed by const ref
        return False


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

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return PtrType(fn(self.pointee))


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

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return ConstPtrType(fn(self.pointee))


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

    def __str__(self) -> str:
        return f"Own[{self.wrapped}]"

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return OwnType(fn(self.wrapped))


@dataclass(frozen=True)
class StaticListType(TpyType):
    """Fixed-capacity container: StaticList[T, N] -> StaticList<T, N>"""
    element_type: TpyType
    capacity: int

    def to_cpp(self) -> str:
        return f"StaticList<{self.element_type.to_cpp()}, {self.capacity}>"

    def __str__(self) -> str:
        return f"StaticList[{self.element_type}, {self.capacity}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.StaticList"

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def is_iterable(self) -> bool:
        return True

    def is_sequence(self) -> bool:
        return True

    def to_cpp_from_range(self, range_expr: str, elem_cpp: str) -> Optional[str]:
        return f"{self.to_cpp()}({range_expr})"

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return StaticListType(fn(self.element_type), self.capacity)


@dataclass(frozen=True)
class ArrayType(TpyType):
    """Fixed-size array: Array[T, N] -> std::array<T, N>"""
    element_type: TpyType
    size: int

    def to_cpp(self) -> str:
        return f"std::array<{self.element_type.to_cpp()}, {self.size}>"

    def __str__(self) -> str:
        return f"Array[{self.element_type}, {self.size}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Array"

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def is_iterable(self) -> bool:
        return True

    def is_sequence(self) -> bool:
        return True

    def needs_explicit_element_target(self) -> bool:
        return True

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return ArrayType(fn(self.element_type), self.size)


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

    def is_iterable(self) -> bool:
        return True

    def is_sequence(self) -> bool:
        return True

    def needs_explicit_element_target(self) -> bool:
        return True

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return SpanType(fn(self.element_type))


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

    def is_iterable(self) -> bool:
        return True

    def is_sequence(self) -> bool:
        return True

    def to_cpp_from_range(self, range_expr: str, elem_cpp: str) -> Optional[str]:
        return f"tpy::to_vector<{elem_cpp}>({range_expr})"

    def map_inner_types(self, fn: Callable[['TpyType'], 'TpyType']) -> 'TpyType':
        return ListType(fn(self.element_type))


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

    def is_iterable(self) -> bool:
        return True

    def is_sequence(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"PendingList[{self.element_type}, {self.size}]#{self.literal_id}"

    def qualified_name(self) -> Optional[str]:
        return "builtins.list"


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


@dataclass
class FieldInfo:
    """Information about a record field."""
    name: str
    type: TpyType
    default_value: Optional[str] = None


@dataclass
class RecordInfo:
    """Information about a user-defined record (class)."""
    name: str
    fields: list[FieldInfo]
    has_init: bool = False
    init_params: list[tuple[str, TpyType, Optional[str]]] = None  # (name, type, default)
    methods: dict[str, 'FunctionInfo'] = None  # method_name -> FunctionInfo

    def __post_init__(self):
        if self.init_params is None:
            self.init_params = []
        if self.methods is None:
            self.methods = {}

    def get_method(self, name: str) -> Optional['FunctionInfo']:
        return self.methods.get(name)


@dataclass
class FunctionInfo:
    """Information about a function."""
    name: str
    params: list[tuple[str, TpyType]]  # (name, type)
    return_type: TpyType
    is_noalloc: bool = False
    is_method: bool = False


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
    """
    name: str
    methods: list[MethodSignature]
    type_params: list[str] = None

    def __post_init__(self):
        if self.type_params is None:
            self.type_params = []


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self):
        self.records: dict[str, RecordInfo] = {}
        self.functions: dict[str, FunctionInfo] = {}
        self.protocols: dict[str, ProtocolInfo] = {}
        # Built-in types
        self.builtins = {"Int32", "Bool", "Char", "Ptr", "ConstPtr", "Own", "StaticList", "Array", "Span", "str", "list", "int", "float"}

    def register_record(self, info: RecordInfo) -> None:
        self.records[info.name] = info

    def register_function(self, info: FunctionInfo) -> None:
        self.functions[info.name] = info

    def register_protocol(self, info: ProtocolInfo) -> None:
        self.protocols[info.name] = info

    def get_record(self, name: str) -> Optional[RecordInfo]:
        return self.records.get(name)

    def get_function(self, name: str) -> Optional[FunctionInfo]:
        return self.functions.get(name)

    def get_protocol(self, name: str) -> Optional[ProtocolInfo]:
        return self.protocols.get(name)

    def is_known_type(self, name: str) -> bool:
        return name in self.builtins or name in self.records or name in self.protocols
