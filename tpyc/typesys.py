"""
TurboPython Type System

Defines the core types available in TurboPython:
- Int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- ReadOnlyPtr[T]: Read-only pointer (maps to const T*)
- Array[T, N], Span[T], list[T], dict[K, V]: Container types
- User-defined records (classes)
- NamedType: User-defined records/protocols and module-defined generics
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
_union_alias_names: dict[tuple['TpyType', ...], str] = {}
_value_type_record_names: set[str] = set()
_protocol_modules: dict[str, str] = {}  # protocol_name -> module_name


def register_native_cpp_name(py_name: str, cpp_name: str) -> None:
    """Register a mapping from a Python class name to its native C++ name."""
    _native_cpp_names[py_name] = cpp_name


def register_union_alias(members: tuple['TpyType', ...], alias_name: str) -> None:
    """Register a union type -> alias name mapping for codegen."""
    _union_alias_names[members] = alias_name


def register_value_type_record(name: str) -> None:
    """Register a record as a value type (ValueType marker protocol)."""
    _value_type_record_names.add(name)


def register_protocol_module(protocol_name: str, module_name: str) -> None:
    """Register the module that defines a protocol, for qualified_name() lookups.

    Builtins are registered first and never overwritten by user protocols.
    """
    if protocol_name not in _protocol_modules:
        _protocol_modules[protocol_name] = module_name


def get_protocol_qname(protocol_name: str) -> str | None:
    """Get the qualified name for a protocol from the global registry."""
    mod = _protocol_modules.get(protocol_name)
    if mod:
        return f"{mod}.{protocol_name}"
    return None


def impl_proto_matches_name(impl_proto: 'NamedType', protocol_name: str,
                            target_qname: str | None = None) -> bool:
    """Check if an implemented protocol matches a target by qualified name.

    Uses qualified_name() on impl_proto and the _protocol_modules registry
    to avoid false matches with user protocols that shadow builtin names.
    Falls back to short name if qualified names aren't available.

    Args:
        target_qname: Pre-computed qualified name for the target protocol.
            If None, computed from protocol_name via _protocol_modules.
    """
    impl_qname = impl_proto.qualified_name()
    if impl_qname:
        if target_qname is None:
            target_qname = get_protocol_qname(protocol_name)
        if target_qname:
            return impl_qname == target_qname
    return impl_proto.name == protocol_name


def clear_codegen_state() -> None:
    """Clear per-module codegen state (called before each module's codegen).

    Does NOT clear _value_type_record_names -- those are accumulated during
    sema across all modules and must persist for the full build.
    """
    _native_cpp_names.clear()
    _union_alias_names.clear()


def clear_all_compilation_state() -> None:
    """Full reset for a new compilation (called once per tpyc invocation)."""
    _native_cpp_names.clear()
    _union_alias_names.clear()
    _value_type_record_names.clear()
    _protocol_modules.clear()


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

    def is_expensive_copy(self) -> bool:
        """Return True if copying this value type involves heap allocation."""
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

    def param_needs_copy_for_reassign(self) -> bool:
        """Return True if reassigned params need a mutable local copy.

        Types passed as const ref (BigInt, str) cannot be reassigned in-place,
        so the codegen renames the param and emits a local mutable copy.
        """
        return False

    def get_element_type(self) -> Optional['TpyType']:
        """Return the element type for container types, or None for non-containers."""
        return None

    def get_iteration_element_type(self) -> Optional['TpyType']:
        """Return the element type for for-loop iteration.

        For most containers this is the same as get_element_type().
        Dict overrides: iteration yields keys (K), not values (V).
        """
        return self.get_element_type()

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
    """String type -- context-dependent C++ mapping.

    Default (locals, fields, returns, type args): std::string (owned).
    Parameters: std::string_view (zero-copy).
    """

    def to_cpp(self) -> str:
        return "std::string"

    def __str__(self) -> str:
        return "str"

    def qualified_name(self) -> Optional[str]:
        return "builtins.str"

    def is_value_type(self) -> bool:
        return True

    def to_cpp_param(self, name: str) -> str:
        return f"std::string_view {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"std::string_view {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        return True

    def get_element_type(self) -> Optional['TpyType']:
        from tpyc.typesys import CHAR
        return CHAR


@dataclass(frozen=True)
class StringType(TpyType):
    """Explicit owned string type: tpy.String -> std::string."""

    def to_cpp(self) -> str:
        return "std::string"

    def __str__(self) -> str:
        return "String"

    def qualified_name(self) -> Optional[str]:
        return "tpy.String"

    def is_value_type(self) -> bool:
        return True

    def is_expensive_copy(self) -> bool:
        return True

    def to_cpp_param(self, name: str) -> str:
        return f"const std::string& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"const std::string& {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        return True

    def get_element_type(self) -> Optional['TpyType']:
        from tpyc.typesys import CHAR
        return CHAR


@dataclass(frozen=True)
class StrViewType(TpyType):
    """Explicit string view type: tpy.StrView -> std::string_view."""

    def to_cpp(self) -> str:
        return "std::string_view"

    def __str__(self) -> str:
        return "StrView"

    def qualified_name(self) -> Optional[str]:
        return "tpy.StrView"

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
class Float32Type(TpyType):
    """32-bit floating point type (IEEE 754 single precision)."""

    def to_cpp(self) -> str:
        return "float"

    def __str__(self) -> str:
        return "Float32"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Float32"

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

    def is_expensive_copy(self) -> bool:
        return True

    def to_cpp_param(self, name: str) -> str:
        # BigInt is expensive to copy, pass by const reference
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        # Same as to_cpp_param - BigInt always uses const reference
        return f"const {self.to_cpp()}& {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        return True


@dataclass(frozen=True)
class EnumType(TpyType):
    """Enum type -- symbolic constants grouped under a named type.

    Maps to C++ enum class. Members are integer-valued constants.
    """
    name: str
    members: tuple[str, ...] = ()                     # ("Red", "Green", "Blue")
    member_values: tuple[tuple[str, int], ...] = ()   # (("Red", 0), ("Green", 1), ("Blue", 2))
    underlying_type: 'TpyType' = None  # type: ignore[assignment]  # Defaults to INT32 at runtime
    module_name: str | None = None

    def __post_init__(self) -> None:
        # Frozen dataclass -- use object.__setattr__ to set default
        if self.underlying_type is None:
            object.__setattr__(self, 'underlying_type', INT32)

    @property
    def member_value_map(self) -> dict[str, int]:
        """Dict-like lookup for member values."""
        return dict(self.member_values)

    def to_cpp(self) -> str:
        return _native_cpp_names.get(self.name, self.name)

    def __str__(self) -> str:
        return self.name

    def qualified_name(self) -> str | None:
        if self.module_name:
            return f"{self.module_name}.{self.name}"
        return None

    def is_value_type(self) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        """Type identity is by name only -- module_name is codegen metadata."""
        if not isinstance(other, EnumType):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


@dataclass(frozen=True, eq=False)
class IntEnumType(EnumType):
    """IntEnum type -- enum that also behaves as an integer.

    Supports arithmetic with integers, ordering comparisons, and int coercion.
    isinstance(t, EnumType) catches both EnumType and IntEnumType.
    """
    pass


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
      - Returns: Use `tpy::val_or_ref_t<T>` which resolves to:
        - `T` for value types (return by value)
        - `T&` for object types (mutable reference, Python semantics)
      - Const returns: Use `tpy::val_or_cref_t<T>` which resolves to:
        - `T` for value types
        - `const T&` for object types

    For INT kind:
    - Represents a compile-time integer constant (e.g., N in Matrix[T, N: int])
    - Maps to std::size_t in C++
    - Can be used as values in expressions (e.g., Int32(N))

    Bounded type parameters (e.g., T: Comparable) store the bound protocol.
    """
    name: str
    # Codegen concern only: excluded from eq/hash so TypeParamRef("T") with and
    # without bound are considered the same type for type-checking purposes.
    bound: Optional['NamedType'] = field(default=None, compare=False, hash=False)
    kind: TypeParamKind = TypeParamKind.TYPE

    def to_cpp(self) -> str:
        return self.name  # Template parameter name (works for both TYPE and INT)

    def __str__(self) -> str:
        return self.name

    def is_value_type(self) -> bool:
        if self.kind == TypeParamKind.INT:
            # INT type params are std::size_t values
            return True
        if self.bound is not None and isinstance(self.bound, NamedType) and self.bound.qualified_name() == "tpy.ValueType":
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
        return f"tpy::val_or_ref_t<{self.name}>"

    def to_cpp_return_const(self) -> str:
        if self.kind == TypeParamKind.INT:
            return "std::size_t"
        # Use trait-based return type: T for value types, const T& for object types
        return f"tpy::val_or_cref_t<{self.name}>"


@dataclass(frozen=True)
class NamedType(TpyType):
    """A named type: user-defined record/protocol, or module-defined generic.

    During parsing, is_protocol defaults to False (unknown).
    After registration in sema, is_protocol is set correctly.

    For generic types like Stack[T] or Sequence[T]:
    - type_args stores the concrete type arguments (e.g., (Int32,) for Stack[Int32])

    For generic records with integer type parameters like Matrix[T, N: int]:
    - type_args can contain both TpyType and int values (e.g., (Int32, 8))

    For module-defined types (e.g. user-library generics):
    - _module_qname stores the qualified name
    - Behavior (methods, constructors) is looked up via the module system
    """
    name: str
    type_args: tuple['TpyType | int', ...] = ()
    is_protocol: bool = False
    _module_qname: str | None = field(default=None, compare=False, hash=False)
    is_dynamic_protocol: bool = field(default=False, compare=False, hash=False)

    @property
    def is_record(self) -> bool:
        """Return True if this is a record type (not a protocol)."""
        return not self.is_protocol

    @property
    def is_user_record(self) -> bool:
        """Return True if this is a user-defined record (not a module-defined builtin)."""
        return not self.is_protocol and not self._module_qname

    @property
    def is_module_type(self) -> bool:
        """Return True if this is a module-defined builtin type.

        Transitional -- should go away when builtin/user lookup paths are unified.
        """
        return self._module_qname is not None and not self.is_protocol

    def with_protocol_flag(self, is_protocol: bool) -> 'NamedType':
        """Return a copy with is_protocol set."""
        if self.is_protocol == is_protocol:
            return self
        return NamedType(self.name, self.type_args, is_protocol, self._module_qname,
                         self.is_dynamic_protocol)

    def to_cpp_base_name(self) -> str:
        """Return the C++ name without type arguments."""
        return _native_cpp_names.get(self.name, self.name)

    def to_cpp(self) -> str:
        if self.is_protocol and not self.is_dynamic_protocol:
            # Structural protocol: template parameter placeholder
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
        if self._module_qname:
            return self._module_qname
        if self.is_protocol:
            mod = _protocol_modules.get(self.name)
            if mod:
                return f"{mod}.{self.name}"
        return None

    def is_value_type(self) -> bool:
        # Records implementing ValueType marker protocol are value types
        if self.name in _value_type_record_names:
            return True
        return False

    def get_element_type(self) -> Optional['TpyType']:
        if self._module_qname:
            # Convention: first type param is the element type
            for arg in self.type_args:
                if isinstance(arg, TpyType):
                    return arg
        return None

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
        return NamedType(self.name, tuple(new_args), self.is_protocol, self._module_qname,
                         self.is_dynamic_protocol)


@dataclass(frozen=True)
class SelfType(TpyType):
    """Self type for method signatures.

    In protocol method signatures, Self represents the implementing type.
    When checking if Int32 conforms to a protocol with Self, Self is
    substituted with Int32.

    In record method signatures, Self represents the record's own type
    (e.g. Self in class Foo -> NamedType("Foo")). Substituted at
    registration time so it never reaches codegen for record methods.
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
    builtin type.
    """
    parent_type: 'TpyType'  # Can be RecordType or builtin type
    child_record_name: str

    def to_cpp(self) -> str:
        return self.parent_type.to_cpp()

    def __str__(self) -> str:
        return f"super[{self.parent_type}]"


@dataclass(frozen=True)
class PtrType(TpyType):
    """Pointer type: Ptr[T] -> T*, ReadOnlyPtr[T] -> const T*

    When is_readonly=True, represents a read-only pointer (ReadOnlyPtr[T]).
    """
    pointee: TpyType
    is_readonly: bool = False

    def to_cpp(self) -> str:
        if self.is_readonly:
            return f"const {self.pointee.to_cpp()}*"
        return f"{self.pointee.to_cpp()}*"

    def qualified_name(self) -> Optional[str]:
        return "tpy.ReadOnlyPtr" if self.is_readonly else "tpy.Ptr"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        if self.is_readonly:
            return f"ReadOnlyPtr[{self.pointee}]"
        return f"Ptr[{self.pointee}]"

    def is_value_type(self) -> bool:
        return True

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.pointee,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return PtrType(types[0], is_readonly=self.is_readonly)

    def as_const(self) -> 'PtrType':
        """Return a const version of this pointer."""
        if self.is_readonly:
            return self
        return PtrType(self.pointee, is_readonly=True)

    def as_mutable(self) -> 'PtrType':
        """Return a mutable version of this pointer."""
        if not self.is_readonly:
            return self
        return PtrType(self.pointee, is_readonly=False)


def is_readonly_ptr(typ: 'TpyType') -> bool:
    """Check if a type is a read-only pointer (ReadOnlyPtr[T])."""
    return isinstance(typ, PtrType) and typ.is_readonly


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

    def param_needs_copy_for_reassign(self) -> bool:
        return self.wrapped.param_needs_copy_for_reassign()

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


@dataclass(frozen=True)
class FinalType(TpyType):
    """Final type modifier -- marks a binding as immutable constant (Final[T]).

    Thin wrapper stripped during semantic analysis. The inner type is
    registered in the scope; finality is tracked via is_final on the
    VarDecl node and the final_globals set in SemanticContext.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        return self.wrapped.to_cpp()

    def is_value_type(self) -> bool:
        return self.wrapped.is_value_type()

    def __str__(self) -> str:
        return f"Final[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return FinalType(types[0])


def unwrap_final(typ: 'TpyType') -> 'TpyType':
    """Strip FinalType wrapper if present, returning the inner type."""
    if isinstance(typ, FinalType):
        return typ.wrapped
    return typ


def is_any_str_type(typ: 'TpyType') -> bool:
    """Check if a type is any string type (str, String, StrView, PendingStr)."""
    return isinstance(typ, (StrType, StringType, StrViewType, PendingStrType))


def is_constexpr_eligible(typ: 'TpyType') -> bool:
    """Check if a type can use C++ constexpr (vs runtime const).

    Constexpr-eligible: fixed-width integers, float, bool, char, str, StrView.
    Non-constexpr (needs const): BigInt (non-trivial constructor), String (std::string).
    Note: StrType uses std::string_view for Final[str] (overridden in codegen).
    """
    return isinstance(typ, (FixedIntType, FloatType, Float32Type, BoolType, CharType, StrType, StrViewType))


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


def contains_type_param(t: TpyType) -> bool:
    """Return True if the type contains any TypeParamRef (recursively)."""
    if isinstance(t, TypeParamRef):
        return True
    return any(contains_type_param(inner) for inner in t.inner_types())


def attach_type_param_bounds(t: TpyType, bounds: dict[str, 'NamedType']) -> TpyType:
    """Attach bounds to TypeParamRef instances in a type tree.

    Returns a new type with bounds set on matching TypeParamRef nodes.
    Used during registration to propagate class/function-level bounds
    into the types stored in method signatures.
    """
    if isinstance(t, TypeParamRef):
        if t.bound is None and t.name in bounds:
            return TypeParamRef(t.name, bound=bounds[t.name], kind=t.kind)
        return t
    new_inners = tuple(attach_type_param_bounds(inner, bounds) for inner in t.inner_types())
    if any(new is not old for new, old in zip(new_inners, t.inner_types())):
        return t.with_inner_types(new_inners)
    return t


@dataclass(frozen=True)
class OptionalType(TpyType):
    """Nullable wrapper: T | None.

    For non-value inner types, maps to T* (nullable pointer) in locals/params/returns.
    The canonical storage form (std::optional<T>) is reserved for future class members.
    """
    inner: TpyType
    # Locks representation to T* even when the concrete inner type is a value type.
    # Set during generic type substitution when the template used T* (unbounded
    # TypeParamRef) but the concrete type would normally use std::optional<T>.
    # Excluded from eq/hash: codegen concern only.
    force_pointer_repr: bool = field(default=False, compare=False, hash=False)

    def to_cpp(self) -> str:
        return f"std::optional<{self.inner.to_cpp()}>"

    def is_value_type(self) -> bool:
        return self.inner.is_value_type()

    def uses_pointer_repr(self) -> bool:
        """Whether this Optional uses T* (pointer) repr instead of std::optional<T>.

        True when inner is not a value type: records, unbounded TypeParamRef.
        False for value types (Int32, bool) and ValueType-bounded TypeParamRef.
        force_pointer_repr overrides: set during generic substitution when the
        template used T* but the concrete inner is a value type (e.g. Container[Int32]
        where the template committed to T* for all instantiations).
        """
        if self.force_pointer_repr:
            return True
        return not self.inner.is_value_type()

    def to_cpp_return(self) -> str:
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}*"
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        if self.uses_pointer_repr():
            return f"const {self.inner.to_cpp()}*"
        return self.to_cpp()

    def to_cpp_param(self, name: str) -> str:
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}* {name}"
        return f"{self.to_cpp()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.uses_pointer_repr():
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
        return OptionalType(types[0], force_pointer_repr=self.force_pointer_repr)


@dataclass(frozen=True)
class UnionType(TpyType):
    """Union of multiple types: A | B | C -> std::variant<A, B, C>.

    Members are stored in canonical sorted order for deterministic eq/hash.
    NoneType is always last if present (maps to std::monostate).
    """
    members: tuple[TpyType, ...]

    def to_cpp(self) -> str:
        alias = _union_alias_names.get(self.members)
        if alias is not None:
            return alias
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


@dataclass(frozen=True)
class TupleType(TpyType):
    """Fixed-length tuple: tuple[T1, T2, ...] -> std::tuple<T1, T2, ...>."""
    element_types: tuple[TpyType, ...]

    def to_cpp(self) -> str:
        args = ", ".join(t.to_cpp() for t in self.element_types)
        return f"std::tuple<{args}>"

    def is_value_type(self) -> bool:
        return True

    def is_expensive_copy(self) -> bool:
        return any(t.is_expensive_copy() for t in self.element_types)

    def to_cpp_return(self) -> str:
        args = ", ".join(t.to_cpp_return() for t in self.element_types)
        return f"std::tuple<{args}>"

    def to_cpp_return_const(self) -> str:
        args = ", ".join(t.to_cpp_return_const() for t in self.element_types)
        return f"std::tuple<{args}>"

    def has_ref_elements(self) -> bool:
        return any(
            not et.is_value_type() and not isinstance(et, (OwnType, TypeParamRef))
            for et in self.element_types
        )

    def to_cpp_param(self, name: str) -> str:
        args = ", ".join(t.to_cpp_return() for t in self.element_types)
        return f"const std::tuple<{args}>& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        args = ", ".join(t.to_cpp_return_const() for t in self.element_types)
        return f"const std::tuple<{args}>& {name}"

    def __str__(self) -> str:
        parts = ", ".join(str(t) for t in self.element_types)
        return f"tuple[{parts}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return self.element_types

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return TupleType(types)


def resolve_int_literals(
    typ: TpyType,
    resolver: 'TpyType | Callable[[TpyType], TpyType]',
) -> TpyType:
    """Recursively resolve IntLiteralType inside composite types.

    resolver can be a fixed type or a callable (e.g. default_int_for_literal)
    that maps IntLiteralType -> concrete int type.
    Handles TupleType, ArrayType, ListType at arbitrary nesting depth.
    """
    def _resolve(t: TpyType) -> TpyType:
        if isinstance(t, IntLiteralType):
            return resolver(t) if callable(resolver) else resolver
        if isinstance(t, TupleType):
            return t.map_inner_types(_resolve)
        if isinstance(t, ArrayType) and isinstance(t.element_type, IntLiteralType):
            elem = resolver(t.element_type) if callable(resolver) else resolver
            return ArrayType(elem, t.size)
        if isinstance(t, ListType) and isinstance(t.element_type, IntLiteralType):
            elem = resolver(t.element_type) if callable(resolver) else resolver
            return ListType(elem)
        return t
    return _resolve(typ)


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


class ArrayType(NamedType):
    """Fixed-size array: Array[T, N] -> std::array<T, N>"""

    def __init__(self, element_type: TpyType, size: "int | TypeParamRef"):
        NamedType.__init__(self, name="Array", type_args=(element_type, size),
                           _module_qname="tpy.Array")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def size(self) -> "int | TypeParamRef":
        return self.type_args[1]

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


class SpanType(NamedType):
    """Non-owning view: Span[T] -> std::span<T>, ReadOnlySpan[T] -> std::span<const T>

    When is_readonly=True, represents a read-only span (ReadOnlySpan[T]).
    """

    def __init__(self, element_type: TpyType, is_readonly: bool = False):
        name = "ReadOnlySpan" if is_readonly else "Span"
        qname = "tpy.ReadOnlySpan" if is_readonly else "tpy.Span"
        NamedType.__init__(self, name=name, type_args=(element_type,),
                           _module_qname=qname)
        object.__setattr__(self, 'is_readonly', is_readonly)

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    def to_cpp(self) -> str:
        if self.is_readonly:
            return f"std::span<const {self.element_type.to_cpp()}>"
        return f"std::span<{self.element_type.to_cpp()}>"

    def __str__(self) -> str:
        if self.is_readonly:
            return f"ReadOnlySpan[{self.element_type}]"
        return f"Span[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.ReadOnlySpan" if self.is_readonly else "tpy.Span"

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
        return SpanType(types[0], is_readonly=self.is_readonly)

    def as_const(self) -> 'SpanType':
        """Return a const (ReadOnlySpan) version of this span."""
        if self.is_readonly:
            return self
        return SpanType(self.element_type, is_readonly=True)

    def as_mutable(self) -> 'SpanType':
        """Return a mutable (Span) version of this span."""
        if not self.is_readonly:
            return self
        return SpanType(self.element_type, is_readonly=False)


def is_readonly_span(typ: 'TpyType') -> bool:
    """Check if a type is a read-only span (ReadOnlySpan[T])."""
    return isinstance(typ, SpanType) and typ.is_readonly


class ListType(NamedType):
    """Dynamic list: list[T] -> std::vector<T>"""

    def __init__(self, element_type: TpyType):
        NamedType.__init__(self, name="list", type_args=(element_type,),
                           _module_qname="builtins.list")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

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


class DictType(NamedType):
    """Dict type: dict[K, V] -> tpy::ordered_map<K, V>"""

    def __init__(self, key_type: TpyType, value_type: TpyType):
        NamedType.__init__(self, name="dict", type_args=(key_type, value_type),
                           _module_qname="builtins.dict")

    @property
    def key_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def value_type(self) -> TpyType:
        return self.type_args[1]

    def to_cpp(self) -> str:
        return f"tpy::ordered_map<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"dict[{self.key_type}, {self.value_type}]"

    def qualified_name(self) -> Optional[str]:
        return "builtins.dict"

    def get_element_type(self) -> Optional[TpyType]:
        # Subscript result type: d[k] -> V
        return self.value_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        # For-loop variable type: for k in d -> K
        return self.key_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictType(types[0], types[1])


class DictKeysViewType(NamedType):
    """Dict keys view: d.keys() -> tpy::dict_keys_view<K, V>"""

    def __init__(self, key_type: TpyType, value_type: TpyType):
        NamedType.__init__(self, name="dict_keys", type_args=(key_type, value_type),
                           _module_qname="builtins.dict_keys")

    @property
    def key_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def value_type(self) -> TpyType:
        return self.type_args[1]

    def to_cpp(self) -> str:
        return f"tpy::dict_keys_view<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.key_type

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"dict_keys[{self.key_type}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictKeysViewType(types[0], types[1])


class DictValuesViewType(NamedType):
    """Dict values view: d.values() -> tpy::dict_values_view<K, V>"""

    def __init__(self, key_type: TpyType, value_type: TpyType):
        NamedType.__init__(self, name="dict_values", type_args=(key_type, value_type),
                           _module_qname="builtins.dict_values")

    @property
    def key_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def value_type(self) -> TpyType:
        return self.type_args[1]

    def to_cpp(self) -> str:
        return f"tpy::dict_values_view<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.value_type

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"dict_values[{self.value_type}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictValuesViewType(types[0], types[1])


class DictItemsViewType(NamedType):
    """Dict items view: d.items() -> tpy::dict_items_view<K, V>"""

    def __init__(self, key_type: TpyType, value_type: TpyType):
        NamedType.__init__(self, name="dict_items", type_args=(key_type, value_type),
                           _module_qname="builtins.dict_items")

    @property
    def key_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def value_type(self) -> TpyType:
        return self.type_args[1]

    def to_cpp(self) -> str:
        return f"tpy::dict_items_view<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return TupleType((self.key_type, self.value_type))

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"dict_items[{self.key_type}, {self.value_type}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictItemsViewType(types[0], types[1])


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
class ListRepeatType(TpyType):
    """Lazy list repeat type -- [val]*N that hasn't been materialized.

    Produced by _resolve_pending_list_types when a list repeat with variable
    count is never mutated or passed to a container param. Conforms to
    Iterable[T], Sized, NativeIterable[T]. Materializes to list[T] or
    Array[T, N] when assigned to those types.
    """
    element_type: TpyType

    def to_cpp(self) -> str:
        return f"tpy::repeat_range<{self.element_type.to_cpp()}>"

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def __str__(self) -> str:
        return f"repeat[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


@dataclass
class ListLiteralInfo:
    """Tracks usage information for a list literal to determine its resolved type."""
    literal_id: int
    expr: 'TpyArrayLiteral | TpyListRepeat'  # Forward reference to avoid circular import
    element_type: TpyType
    size: int  # -1 for unknown (variable count repeat)
    variable_name: Optional[str] = None
    decl_line: Optional[int] = None
    is_global: bool = False
    is_mutated: bool = False
    needs_indexing: bool = False
    passed_to_list_param: bool = False
    passed_to_span_param: bool = False
    has_explicit_annotation: bool = False
    explicit_type: Optional[TpyType] = None
    coerced_element_type: Optional[TpyType] = None  # Element type from typed param (list[T] or Span[T])
    source_literal_id: Optional[int] = None  # Alias tracking: b = a
    resolved_type: Optional[TpyType] = None


@dataclass(frozen=True)
class PendingStrType(TpyType):
    """Unresolved string local -- becomes StrView or str based on usage.

    Assigned to str-typed locals in function contexts during the first
    analysis phase. After the full function body is analyzed, resolved
    based on collected usage (augmented assignment, owned source, etc.).
    """
    str_var_id: int

    def to_cpp(self) -> str:
        raise RuntimeError(
            f"PendingStrType should be resolved before codegen (str_var_id={self.str_var_id})"
        )

    def __str__(self) -> str:
        return "str"

    def qualified_name(self) -> Optional[str]:
        return "builtins.str"

    def is_value_type(self) -> bool:
        return True

    def get_element_type(self) -> Optional[TpyType]:
        from tpyc.typesys import CHAR
        return CHAR


@dataclass
class StrVarInfo:
    """Tracks usage of a string local to decide StrView vs str."""
    str_var_id: int
    variable_name: str
    decl_line: Optional[int] = None
    initialized_from_owned: bool = False
    used_in_augassign: bool = False
    passed_to_string_param: bool = False
    reassigned_from_owned: bool = False
    source_str_var_id: Optional[int] = None  # if initialized from another PendingStrType local
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
STRING = StringType()
STRVIEW = StrViewType()
CHAR = CharType()
BOOL = BoolType()
FLOAT = FloatType()
FLOAT32 = Float32Type()
BIGINT = BigIntType()
NONE = NoneType()
RANGE = RangeType(INT32)

# Backward-compat range limits (use type.min_value / type.max_value instead)
INT32_MIN = INT32.min_value
INT32_MAX = INT32.max_value


def is_protocol_type(typ: TpyType) -> bool:
    """Check if a type is a protocol type."""
    return isinstance(typ, NamedType) and typ.is_protocol


def is_protocol_union(typ: TpyType) -> bool:
    """Check if a type is a union where all non-None members are static protocols.

    Matches UnionType with 2+ protocol members (with or without None).
    Does NOT match OptionalType(protocol) -- that has its own codegen paths.
    """
    if not isinstance(typ, UnionType):
        return False
    non_none = [m for m in typ.members if not isinstance(m, (NoneType, VoidType))]
    return len(non_none) >= 2 and all(is_protocol_type(m) for m in non_none)


def protocol_union_protocols(typ: UnionType) -> list[TpyType]:
    """Get non-None protocol members from a protocol union."""
    return [m for m in typ.members if not isinstance(m, (NoneType, VoidType))]


def protocol_union_has_none(typ: UnionType) -> bool:
    """Check if a protocol union includes None."""
    return any(isinstance(m, (NoneType, VoidType)) for m in typ.members)


def container_to_str_template(typ: TpyType) -> str | None:
    """Return the C++ to_str template for a container type, or None."""
    if isinstance(typ, TupleType):
        return "tpy::tuple_to_str({0})"
    if isinstance(typ, (ListType, ArrayType, SpanType)):
        return "tpy::list_to_str({0})"
    if isinstance(typ, DictType):
        return "tpy::dict_to_str({0})"
    return None


def get_covariant_params(record_info: 'RecordInfo') -> set[str]:
    """Get type param names marked covariant via Covariant[T] protocol."""
    result: set[str] = set()
    for proto in record_info.implemented_protocols:
        if proto.qualified_name() == "tpy.Covariant" and proto.type_args:
            for arg in proto.type_args:
                if isinstance(arg, TypeParamRef):
                    result.add(arg.name)
    return result


@dataclass
class FieldInfo:
    """Information about a record field."""
    name: str
    type: TpyType
    default_value: Optional[str] = None
    default_expr: Optional[Any] = None  # TpyExpr from parser (avoid circular import)
    is_factory_default: bool = False  # True for field(default_factory=...)
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
    is_dataclass: bool = False    # True for @dataclass classes
    dataclass_fields: list[FieldInfo] = field(default_factory=list)  # All dataclass fields (parent + own)
    is_frozen: bool = False       # True for @dataclass(frozen=True) (field mutation rejected)
    is_ordered: bool = False      # True for @dataclass(order=True) (auto __lt__ etc.)
    is_value_type: bool = False   # True for ValueType marker protocol
    has_del: bool = False           # True if class declares __del__ (needs drop flag)
    has_copy: bool = False          # True if class defines __copy__ (custom copy semantics)

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
    default_expr: 'Any | None' = None  # TpyExpr from parser; None = required param

    @property
    def has_default(self) -> bool:
        return self.default_expr is not None

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
    is_consuming: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: Optional[str] = None
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, 'NamedType'] = field(default_factory=dict)
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "DEFAULT_INT"}
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

    @property
    def min_args(self) -> int:
        """Minimum number of arguments (params without defaults)."""
        return sum(1 for p in self.params if not p.has_default)

    @property
    def max_args(self) -> int:
        """Maximum number of arguments (all params)."""
        return len(self.params)


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
    "__bool__", "__len__", "__getitem__", "__contains__", "__str__", "__repr__", "__hash__", "__eq__", "__ne__",
    "__lt__", "__le__", "__gt__", "__ge__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
    "__and__", "__or__", "__xor__", "__lshift__", "__rshift__",
    "__radd__", "__rsub__", "__rmul__", "__rtruediv__", "__rfloordiv__", "__rmod__", "__rpow__",
    "__neg__", "__pos__", "__invert__",
    "__copy__", "__deref__",
})


@dataclass
class MethodSignature:
    """Method signature required by a protocol."""
    name: str
    params: list[tuple[str, TpyType]]  # (param_name, param_type)
    return_type: TpyType
    is_readonly: bool = False
    readonly_opt_out: bool = False
    cpp_template: str | None = None


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
    is_dynamic: bool = False  # Supports runtime dispatch via base/adapter
    module: str = ""  # Module that defines this protocol (e.g. "typing", "tpy")


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
    type_aliases: dict[str, 'TpyType'] = field(default_factory=dict)  # alias_name -> resolved type
    enums: dict[str, 'EnumType'] = field(default_factory=dict)  # enum_name -> EnumType


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self):
        self.records: dict[str, RecordInfo] = {}
        self.builtin_records: dict[str, RecordInfo] = {}  # By qualified name (e.g., "builtins.list")
        self.functions: dict[str, FunctionInfo] = {}  # User-defined functions
        self.builtin_function_overloads: dict[str, list[FunctionInfo]] = {}  # Builtin function overloads
        self.protocols: dict[str, ProtocolInfo] = {}
        self.modules: dict[str, ModuleInfo] = {}  # module_name -> ModuleInfo
        self.type_aliases: dict[str, 'TpyType'] = {}  # alias_name -> resolved type
        self.enums: dict[str, 'EnumType'] = {}  # enum_name -> EnumType
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
        if info.module:
            register_protocol_module(info.name, info.module)

    def register_enum(self, info: 'EnumType', name: str | None = None) -> None:
        """Register an enum type.

        Args:
            info: The enum type to register.
            name: Optional name to register under (defaults to info.name).
                  Used for imported enums that may have a local alias.
        """
        self.enums[name or info.name] = info

    def register_type_alias(self, name: str, typ: 'TpyType') -> None:
        """Register a type alias (e.g., Shape = Circle | Rect)."""
        self.type_aliases[name] = typ

    def get_type_alias(self, name: str) -> 'TpyType | None':
        """Get a type alias by name, or None if not found."""
        return self.type_aliases.get(name)

    def register_module(self, info: ModuleInfo) -> None:
        """Register a module by name."""
        self.modules[info.name] = info

    def get_module(self, name: str) -> Optional[ModuleInfo]:
        """Get a module by name."""
        return self.modules.get(name)

    def get_record(self, name: str) -> Optional[RecordInfo]:
        return self.records.get(name)

    def find_record(self, name: str) -> Optional[RecordInfo]:
        """Find a record by name, searching local records and registered modules."""
        result = self.records.get(name)
        if result is not None:
            return result
        for mod in self.modules.values():
            if name in mod.records:
                return mod.records[name]
        return None

    def get_builtin_record(self, qname: str) -> Optional[RecordInfo]:
        """Get a builtin record by qualified name."""
        return self.builtin_records.get(qname)

    def is_subclass_of(self, child: 'TpyType', parent: 'TpyType') -> bool:
        """Check if child is a subclass of parent (walking the inheritance chain).

        Compares name + type_args at each level so generic parents are
        matched correctly (e.g. IntContainer -> Container[Int32]).
        """
        if not (isinstance(child, NamedType) and child.is_user_record
                and isinstance(parent, NamedType) and parent.is_user_record):
            return False
        current_info = self.records.get(child.name)
        visited: set[str] = set()
        while current_info and current_info.parent and current_info.name not in visited:
            visited.add(current_info.name)
            p = current_info.parent
            if isinstance(p, NamedType) and p.name == parent.name and p.type_args == parent.type_args:
                return True
            if isinstance(p, NamedType) and p.is_user_record:
                current_info = self.records.get(p.name)
            else:
                break
        return False

    def get_record_for_type(self, tpy_type: 'TpyType') -> Optional[RecordInfo]:
        """Unified lookup for any type's RecordInfo.

        For user records (NamedType with is_record), looks up by name in self.records.
        For builtin types, looks up by qualified_name in self.builtin_records.
        """
        if isinstance(tpy_type, NamedType) and tpy_type.is_user_record:
            return self.records.get(tpy_type.name)
        qname = tpy_type.qualified_name()
        if qname:
            return self.builtin_records.get(qname)
        return None

    def get_function(self, name: str) -> Optional[FunctionInfo]:
        return self.functions.get(name)

    def get_protocol(self, name: str) -> Optional[ProtocolInfo]:
        return self.protocols.get(name)

    def get_enum(self, name: str) -> Optional['EnumType']:
        return self.enums.get(name)

    def is_known_type(self, name: str) -> bool:
        """Check if a name refers to a known type."""
        if name in self._fundamental_types:
            return True
        if name in self.records or name in self.protocols or name in self.type_aliases or name in self.enums:
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
