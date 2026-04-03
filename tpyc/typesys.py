"""
TurboPython Type System

Defines the core types available in TurboPython:
- Int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- Ptr[readonly[T]]: Read-only pointer (maps to const T*)
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


def qualify_exception_name(name: str, registry: 'TypeRegistry') -> str:
    """Qualify a bare exception name with its defining module.

    Returns 'module.Name' for cross-module error_return matching.
    Prefers the builtins module (the public namespace) over internal
    submodules that happen to be compiled first.
    """
    if '.' in name:
        return name
    # Prefer builtins -- it's the public namespace for built-in exceptions.
    # Without this, iteration order would find them in tpy._builtins._exceptions
    # (compiled first) instead of the public builtins module.
    builtins_mod = registry.get_module("builtins")
    if builtins_mod and name in builtins_mod.records:
        local_record = registry.get_record(name)
        if local_record is None or local_record is builtins_mod.records[name]:
            return f"builtins.{name}"
        # User shadowed the builtin -- fall through to local resolution
    # Find the defining module. Skip modules whose record is shadowed
    # by a local definition.
    local_record = registry.get_record(name)
    for mod_name, mod_info in registry.modules.items():
        if name in mod_info.records:
            if local_record is not None and local_record is not mod_info.records[name]:
                continue
            return f"{mod_name}.{name}"
    return name


def error_return_to_cpp(name: str, current_module: str | None,
                        registry: 'TypeRegistry') -> str:
    """Map a qualified exception name to its C++ type name.

    Uses the same resolution as any other type:
    - @native types: record.native_name (works for builtins AND user native types)
    - User types in current module: bare name
    - User types in other modules: qualified_cpp_name(module, name)
    """
    bare = name.rsplit(".", 1)[-1] if "." in name else name
    record = registry.find_record(bare)
    if record and record.native_name:
        return record.native_name
    if "." in name:
        module_path, bare_name = name.rsplit(".", 1)
        if module_path == current_module:
            return bare_name
        from tpyc.codegen_cpp.context import qualified_cpp_name
        return qualified_cpp_name(module_path, bare_name)
    return name


# Native C++ name mapping for @native/@native_c records.
# Maps Python class name -> C++ name (e.g., "Rect" -> "SDL_Rect").
# Used by NamedType.to_cpp() so composite types like Ptr[Rect] resolve correctly.
# NOTE: Global mutable state -- safe because the compilation pipeline is sequential
# (each CodeGenerator.generate() call clears and repopulates before use).
# Would need to move into CodeGenContext if codegen ever runs concurrently.
_native_cpp_names: dict[str, str] = {}
_union_alias_names: dict[tuple['TpyType', ...], str] = {}
_value_type_record_names: set[str] = set()
_send_record_names: set[str] = set()
_sync_record_names: set[str] = set()
_protocol_modules: dict[str, str] = {}  # protocol_name -> module_name


def ensure_qualified(name: str) -> str:
    """Ensure a namespaced C++ name is fully qualified (prefixed with ::).

    Names without :: (e.g. "abs") are returned as-is.
    Names already starting with :: are returned as-is.
    Other namespaced names get :: prepended (e.g. "tpy::Foo" -> "::tpy::Foo").
    """
    if "::" in name and not name.startswith("::"):
        return f"::{name}"
    return name


def register_native_cpp_name(py_name: str, cpp_name: str) -> None:
    """Register a mapping from a Python class name to its native C++ name."""
    _native_cpp_names[py_name] = ensure_qualified(cpp_name)


def register_union_alias(members: tuple['TpyType', ...], alias_name: str) -> None:
    """Register a union type -> alias name mapping for codegen."""
    _union_alias_names[members] = alias_name


def register_value_type_record(name: str) -> None:
    """Register a record as a value type (ValueType marker protocol)."""
    _value_type_record_names.add(name)


def register_send_record(name: str) -> None:
    """Register a record as Send (safe to transfer across threads)."""
    _send_record_names.add(name)


def register_sync_record(name: str) -> None:
    """Register a record as Sync (safe to share references across threads)."""
    _sync_record_names.add(name)


# Builtins that are always ReturnException. Pre-seeded because _funcs.py
# (which uses @error_return(StopIteration)) may be compiled before
# _exceptions.py registers StopIteration as ReturnException.
# Stored as bare names -- is_return_exception strips module prefixes.
_BUILTIN_RETURN_EXCEPTIONS: frozenset[str] = frozenset({"StopIteration"})
_return_exception_names: set[str] = set(_BUILTIN_RETURN_EXCEPTIONS)


def register_return_exception(name: str) -> None:
    """Register an exception type as ReturnException (return-only, used with @error_return)."""
    _return_exception_names.add(name)


def error_return_matches(a: str | None, b: str | None) -> bool:
    """Check if two module-qualified error_return type names refer to the same type.

    Compares bare names since a type may be qualified via different modules
    (e.g. 'tplib.json.JsonError' vs 'tplib.json.parser.JsonError' when
    the package re-exports the type).
    """
    if a is None or b is None:
        return a is b
    bare_a = a.rsplit(".", 1)[-1] if "." in a else a
    bare_b = b.rsplit(".", 1)[-1] if "." in b else b
    return bare_a == bare_b


def is_return_exception(name: str) -> bool:
    """Check if an exception type name is registered as ReturnException.

    Accepts both bare ('JsonError') and module-qualified ('tplib.json.JsonError')
    names -- extracts the bare name for matching since a ReturnException type
    is ReturnException regardless of which module references it.
    """
    bare = name.rsplit(".", 1)[-1] if "." in name else name
    return bare in _return_exception_names


def is_exception_type(name: str, registry: 'TypeRegistry') -> bool:
    """Check if a record type inherits from Exception or BaseException."""
    from tpyc import qnames
    child = registry.find_record(name)
    if child is None:
        return False
    for qname in (qnames.EXCEPTION, qnames.BASE_EXCEPTION):
        base = registry.find_record_by_qname(qname)
        if base is not None and (child is base or registry.is_subclass_of_record(child, base)):
            return True
    return False


def public_module_name(module_name: str, cpp_namespace: str | None = None) -> str:
    """Map a private submodule name to its public module identity.

    e.g. "tpy._core._types" -> "tpy", "tpy._builtins._list" -> "tpy"

    When cpp_namespace is provided (e.g. "tpystd::typing" for tpy._typing),
    derives the public name from the namespace instead of the module path.
    This handles cross-package implementations like typing protocols defined
    in tpy._typing.
    """
    if cpp_namespace and "._" in module_name:
        # Derive from namespace: "tpystd::typing" -> "typing", "tpystd::tpy" -> "tpy"
        ns_parts = cpp_namespace.split("::")
        # Skip the common prefix (e.g. "tpystd") and join the rest
        if len(ns_parts) >= 2 and ns_parts[0] == "tpystd":
            return ".".join(ns_parts[1:])
    parts = module_name.split(".")
    # Keep only parts up to (but not including) the first private component
    public_parts = []
    for part in parts:
        if part.startswith("_"):
            break
        public_parts.append(part)
    return ".".join(public_parts) if public_parts else module_name


def register_protocol_module(protocol_name: str, module_name: str) -> None:
    """Register the module that defines a protocol, for qualified_name() lookups.

    Builtins are registered first and never overwritten by user protocols.
    Private submodule paths are mapped to their public parent
    (e.g. "tpy._core._types" -> "tpy", "tpy._builtins._list" -> "tpy").
    """
    if protocol_name not in _protocol_modules:
        _protocol_modules[protocol_name] = public_module_name(module_name)


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

    Does NOT clear _value_type_record_names, _send_record_names, or
    _sync_record_names -- those are accumulated during sema across all
    modules and must persist for the full build.
    """
    _native_cpp_names.clear()
    _union_alias_names.clear()


def clear_all_compilation_state() -> None:
    """Full reset for a new compilation (called once per tpyc invocation)."""
    _native_cpp_names.clear()
    _union_alias_names.clear()
    _value_type_record_names.clear()
    _send_record_names.clear()
    _sync_record_names.clear()
    _return_exception_names.clear()
    _return_exception_names.update(_BUILTIN_RETURN_EXCEPTIONS)
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

    def subscript_borrows(self) -> bool:
        """Return True if a subscript result can borrow from this container's element
        storage (i.e. the result is a view into the container, not an owned copy).
        Source-mutation tracking at the call site ensures the borrow stays valid.
        User-defined types can opt in once borrow-source annotation (6.7) is implemented."""
        return False

    def is_send(self) -> bool:
        """Return True if this type is safe to transfer across threads.

        Default: value types are Send (copied, no aliasing). Override for
        pointer-like types (Ptr, Span) and containers (list, dict).
        """
        return self.is_value_type()

    def is_sync(self) -> bool:
        """Return True if this type is safe to share references across threads.

        Default: value types are Sync (no mutable shared state). Override for
        mutable containers (list, dict) and pointer types.
        """
        return self.is_value_type()

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

    def to_cpp_param_type(self) -> str:
        """Return just the C++ parameter type (no variable name).

        Matches the type used by to_cpp_param(). Subclasses that override
        to_cpp_param() should also override this if the type differs from
        the default (e.g. str -> std::string_view, BigInt -> const BigInt&).
        """
        if self.is_value_type():
            return self.to_cpp()
        return f"{self.to_cpp()}&"

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

    def to_cpp_stored(self) -> str:
        """Return the C++ type for storage in containers that cannot hold
        references (std::expected, std::optional).

        Default: same as to_cpp(). Overridden by RefType to produce
        val_or_ref<T> which stores non-value types as pointers.
        """
        return self.to_cpp()

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

    def to_cpp_return_const(self) -> str:
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

    def is_expensive_copy(self) -> bool:
        return True

    def to_cpp_param_type(self) -> str:
        return "std::string_view"

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

    def to_cpp_param_type(self) -> str:
        return "const std::string&"

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

    def is_send(self) -> bool:
        # StrView borrows from another string -- not safe to transfer
        return False

    def is_sync(self) -> bool:
        # Read-only view -- safe to share
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
        return "tpy.Char"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class BytesType(TpyType):
    """Bytes type -- context-dependent C++ mapping.

    Default (locals, fields, returns, type args): std::vector<uint8_t> (owned).
    Parameters: std::span<const uint8_t> (zero-copy).
    """

    def to_cpp(self) -> str:
        return "std::vector<uint8_t>"

    def __str__(self) -> str:
        return "bytes"

    def qualified_name(self) -> Optional[str]:
        return "builtins.bytes"

    def is_value_type(self) -> bool:
        return True

    def is_expensive_copy(self) -> bool:
        return True

    def to_cpp_param_type(self) -> str:
        return "std::span<const uint8_t>"

    def to_cpp_param(self, name: str) -> str:
        return f"std::span<const uint8_t> {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"std::span<const uint8_t> {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        return True

    def get_element_type(self) -> Optional['TpyType']:
        from tpyc.typesys import UINT8
        return UINT8


@dataclass(frozen=True)
class ByteArrayType(TpyType):
    """Explicit owned mutable bytes type: tpy.bytearray -> std::vector<uint8_t>."""

    def to_cpp(self) -> str:
        return "std::vector<uint8_t>"

    def __str__(self) -> str:
        return "bytearray"

    def qualified_name(self) -> Optional[str]:
        return "builtins.bytearray"

    def is_value_type(self) -> bool:
        return True

    def is_expensive_copy(self) -> bool:
        return True

    def to_cpp_param_type(self) -> str:
        return "const std::vector<uint8_t>&"

    def to_cpp_param(self, name: str) -> str:
        return f"const std::vector<uint8_t>& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"const std::vector<uint8_t>& {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        return True

    def get_element_type(self) -> Optional['TpyType']:
        from tpyc.typesys import UINT8
        return UINT8


@dataclass(frozen=True)
class BytesViewType(TpyType):
    """Bytes view type: tpy.BytesView -> std::span<const uint8_t>.

    Same C++ type as Span[readonly[UInt8]], but carries bytes semantics
    (prints as b'...', has .decode()/.hex() methods).
    """

    def to_cpp(self) -> str:
        return "std::span<const uint8_t>"

    def __str__(self) -> str:
        return "BytesView"

    def qualified_name(self) -> Optional[str]:
        return "tpy.BytesView"

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return True

    def get_element_type(self) -> Optional['TpyType']:
        from tpyc.typesys import UINT8
        return UINT8


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
    """Arbitrary precision integer: int -> ::tpy::BigInt"""

    def to_cpp(self) -> str:
        return "::tpy::BigInt"

    def __str__(self) -> str:
        return "int"

    def qualified_name(self) -> Optional[str]:
        return "builtins.int"

    def is_value_type(self) -> bool:
        return True

    def is_expensive_copy(self) -> bool:
        return True

    def to_cpp_param_type(self) -> str:
        return f"const {self.to_cpp()}&"

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
    """Range type: range() -> ::tpy::Range<T> (lazy iterator over T)."""
    elem: "TpyType"

    def to_cpp(self) -> str:
        return f"::tpy::Range<{self.elem.to_cpp()}>"

    def __str__(self) -> str:
        return f"Range[{self.elem}]"

    def qualified_name(self) -> Optional[str]:
        return "builtins.Range"

    def get_element_type(self) -> Optional["TpyType"]:
        return self.elem

    def is_value_type(self) -> bool:
        return True

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.elem,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return RangeType(types[0])


@dataclass(frozen=True)
class SliceType(TpyType):
    """Slice type: slice(start, stop) for subscript ranges."""

    def to_cpp(self) -> str:
        return "::tpy::Slice"

    def __str__(self) -> str:
        return "slice"

    def qualified_name(self) -> Optional[str]:
        return "builtins.slice"

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
class LiteralValue:
    """A typed literal value. Distinguishes True from 1 via tag."""
    tag: str           # "str", "int", "bool"
    value: str | int | bool

    def __str__(self) -> str:
        if self.tag == "str":
            return f'"{self.value}"'
        return str(self.value)


@dataclass(frozen=True)
class LiteralType(TpyType):
    """Literal[value1, value2, ...] -- unified annotation and enrichment type.

    Single-value instances (from enrichment at call sites) carry one value.
    Multi-value instances (from Literal["r", "w"] annotations) carry the set.

    Delegates all C++ codegen methods to base_type, so Literal["r", "w"]
    behaves identically to str for code generation.
    """
    base_type: TpyType
    values: tuple[LiteralValue, ...]

    def to_cpp(self) -> str:
        return self.base_type.to_cpp()

    def __str__(self) -> str:
        vals = ", ".join(str(v) for v in self.values)
        return f"Literal[{vals}]"

    def qualified_name(self) -> Optional[str]:
        return self.base_type.qualified_name()

    def is_value_type(self) -> bool:
        return self.base_type.is_value_type()

    def is_expensive_copy(self) -> bool:
        return self.base_type.is_expensive_copy()

    def to_cpp_param_type(self) -> str:
        return self.base_type.to_cpp_param_type()

    def to_cpp_param(self, name: str) -> str:
        return self.base_type.to_cpp_param(name)

    def to_cpp_const_param(self, name: str) -> str:
        return self.base_type.to_cpp_const_param(name)

    def param_needs_copy_for_reassign(self) -> bool:
        return self.base_type.param_needs_copy_for_reassign()

    def is_str_base(self) -> bool:
        """True when this Literal is over string values."""
        return isinstance(self.base_type, StrType)

    def is_int_base(self) -> bool:
        """True when this Literal is over integer values."""
        return isinstance(self.base_type, (FixedIntType, BigIntType))

    def is_bool_base(self) -> bool:
        """True when this Literal is over bool values."""
        return isinstance(self.base_type, BoolType)

    def contains(self, tag: str, value: str | int | bool) -> bool:
        """Check if a tagged value is in this Literal's value set."""
        return LiteralValue(tag, value) in self.values


@dataclass(frozen=True)
class FloatLiteralType(TpyType):
    """Unresolved float literal - adapts to Float32 or float64 based on context.

    Like IntLiteralType, this represents a float literal (2.0, 1.5) before context
    determines whether it's float64 or Float32. Default is float64.

    - Float32 * FloatLiteral -> Float32 (literal adapts to context)
    - float * FloatLiteral -> float
    - FloatLiteral * FloatLiteral -> float (default)
    """
    value: float | None = None

    def to_cpp(self) -> str:
        # Should be resolved before codegen; fallback to literal value
        if self.value is None:
            return "0.0"
        return repr(self.value)

    def __str__(self) -> str:
        return "float"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class TypeParamRef(TpyType):
    """Unresolved type parameter reference (e.g., T in class Stack[T]).

    Used during parsing and semantic analysis of generic class definitions.
    When the generic class is instantiated with concrete types, TypeParamRef
    is substituted with the actual type.

    For TYPE kind (default):
    - C++ Code Generation Semantics (using ::tpy::is_value_type trait):
      - Parameters: Use `::tpy::param_val_or_ref_t<T>` which resolves to:
        - `const T&` for value types (immutable, compiler optimizes small types)
        - `T&` for object types (allows mutation per Python semantics)
      - Returns: Use `::tpy::val_or_ref_t<T>` which resolves to:
        - `T` for value types (return by value)
        - `T&` for object types (mutable reference, Python semantics)
      - Const returns: Use `::tpy::val_or_cref_t<T>` which resolves to:
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

    def to_cpp_param_type(self) -> str:
        if self.kind == TypeParamKind.INT:
            return "std::size_t"
        return f"::tpy::param_val_or_ref_t<{self.name}>"

    def to_cpp_param(self, name: str) -> str:
        if self.kind == TypeParamKind.INT:
            # INT params are passed by value (they're std::size_t)
            return f"std::size_t {name}"
        # Use trait-based param type: const T& for value types, T& for object types
        return f"::tpy::param_val_or_ref_t<{self.name}> {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.kind == TypeParamKind.INT:
            return f"std::size_t {name}"
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_return(self) -> str:
        if self.kind == TypeParamKind.INT:
            return "std::size_t"
        # Use trait-based return type: T for value types, T& for object types
        return f"::tpy::val_or_ref_t<{self.name}>"

    def to_cpp_return_const(self) -> str:
        if self.kind == TypeParamKind.INT:
            return "std::size_t"
        # Use trait-based return type: T for value types, const T& for object types
        return f"::tpy::val_or_cref_t<{self.name}>"


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

    def is_send(self) -> bool:
        if self.name in _send_record_names:
            return True
        return self.is_value_type()

    def is_sync(self) -> bool:
        if self.name in _sync_record_names:
            return True
        return self.is_value_type()

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


@dataclass(frozen=True, init=False)
class PtrType(TpyType):
    """Pointer type: Ptr[T] -> T*, Ptr[readonly[T]] -> const T*

    Const-ness is encoded in the pointee: Ptr[readonly[T]] stores
    ReadonlyType(T) as the pointee, giving const T* in C++.
    Ptr[readonly[T]] stores ReadonlyType(T) as the pointee.
    """
    pointee: TpyType

    def __init__(self, pointee: TpyType, is_readonly: bool = False):
        # Normalize: is_readonly=True wraps pointee in ReadonlyType.
        # Also accepts ReadonlyType(T) directly as pointee.
        if is_readonly and not isinstance(pointee, ReadonlyType):
            pointee = ReadonlyType(pointee)
        object.__setattr__(self, 'pointee', pointee)

    @property
    def is_readonly(self) -> bool:
        return isinstance(self.pointee, ReadonlyType)

    @property
    def inner_pointee(self) -> TpyType:
        """Unwrapped pointee type (strips ReadonlyType if present)."""
        return unwrap_readonly(self.pointee)

    def to_cpp(self) -> str:
        if self.is_readonly:
            return f"const {self.inner_pointee.to_cpp()}*"
        return f"{self.pointee.to_cpp()}*"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Ptr"

    def is_pointer(self) -> bool:
        return True

    def __str__(self) -> str:
        if self.is_readonly:
            return f"Ptr[readonly[{self.inner_pointee}]]"
        return f"Ptr[{self.pointee}]"

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        if self.is_readonly:
            return self.inner_pointee.is_sync()
        return False

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.pointee,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return PtrType(types[0])

    def as_const(self) -> 'PtrType':
        """Return a const version of this pointer."""
        if self.is_readonly:
            return self
        return PtrType(ReadonlyType(self.pointee))

    def as_mutable(self) -> 'PtrType':
        """Return a mutable version of this pointer."""
        if not self.is_readonly:
            return self
        return PtrType(self.inner_pointee)


def is_readonly_ptr(typ: 'TpyType') -> bool:
    """Check if a type is a read-only pointer (Ptr[readonly[T]])."""
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
        # Own[T] uses T&& at param boundaries; treat as value type for other purposes
        return True

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        return self.wrapped.is_sync()

    def to_cpp_param_type(self) -> str:
        if self.wrapped.is_value_type():
            return self.wrapped.to_cpp()
        cpp_type = self.wrapped.to_cpp()
        if isinstance(self.wrapped, TypeParamRef):
            return f"std::type_identity_t<{cpp_type}>&&"
        return f"{cpp_type}&&"

    def to_cpp_param(self, name: str) -> str:
        # Value types (int32_t, bool, float, Char, Ptr, Span, str, etc.) are
        # trivially movable — T by value is optimal, no T&& needed.
        if self.wrapped.is_value_type():
            return f"{self.wrapped.to_cpp()} {name}"
        cpp_type = self.wrapped.to_cpp()
        # Bare TypeParamRef needs std::type_identity_t to prevent forwarding-ref
        # deduction in free function templates. For nested types (list[T], etc.),
        # T is in a non-deduced context so plain T&& is fine.
        if isinstance(self.wrapped, TypeParamRef):
            return f"std::type_identity_t<{cpp_type}>&& {name}"
        return f"{cpp_type}&& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.wrapped.is_value_type():
            return f"{self.wrapped.to_cpp()} {name}"
        return self.to_cpp_param(name)

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

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        # readonly prevents mutation, so a Send type frozen by readonly is
        # safe to share (effectively Sync). Already-Sync types stay Sync.
        return self.wrapped.is_send() or self.wrapped.is_sync()

    def to_cpp_param_type(self) -> str:
        # Readonly params use the const version of the wrapped type
        dummy = self.wrapped.to_cpp_const_param("__x")
        return dummy.rsplit(" __x", 1)[0]

    def to_cpp_param(self, name: str) -> str:
        return self.wrapped.to_cpp_const_param(name)

    def to_cpp_const_param(self, name: str) -> str:
        return self.wrapped.to_cpp_const_param(name)

    def to_cpp_return(self) -> str:
        return self.wrapped.to_cpp_return_const()

    def to_cpp_return_const(self) -> str:
        return self.wrapped.to_cpp_return_const()

    def to_cpp_stored(self) -> str:
        """Readonly non-value types need val_or_ref<const T> for storage
        in containers that cannot hold references."""
        if not self.wrapped.is_value_type():
            return f"::tpy::val_or_ref<const {self.wrapped.to_cpp()}>"
        return self.to_cpp()

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


def unwrap_own(typ: 'TpyType') -> 'TpyType':
    """Strip OwnType wrapper if present, returning the inner type."""
    if isinstance(typ, OwnType):
        return typ.wrapped
    return typ


def unwrap_qualifiers(typ: 'TpyType') -> 'TpyType':
    """Strip ReadonlyType, OwnType, and RefType wrappers."""
    if isinstance(typ, RefType):
        typ = typ.wrapped
    if isinstance(typ, ReadonlyType):
        typ = typ.wrapped
    if isinstance(typ, OwnType):
        typ = typ.wrapped
    return typ


@dataclass(frozen=True)
class RefType(TpyType):
    """Borrowed reference to T.

    Auto-inserted by sema for non-value types at function param/return
    boundaries and iterator element positions. The user never writes this.

    Ref[T] is the internal semantic fact that a value is borrowed, not owned.
    Codegen lowers it differently depending on context:
    - Function param/return: T& (or trait-based for generics)
    - Storage in std::expected / iterators: val_or_ref<T>
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        if isinstance(self.wrapped, TypeParamRef):
            return f"::tpy::val_or_ref_t<{self.wrapped.name}>"
        return f"{self.wrapped.to_cpp()}&"

    def to_cpp_return(self) -> str:
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        if isinstance(self.wrapped, TypeParamRef):
            return f"::tpy::val_or_cref_t<{self.wrapped.name}>"
        return f"const {self.wrapped.to_cpp()}&"

    def to_cpp_param_type(self) -> str:
        if isinstance(self.wrapped, TypeParamRef):
            return f"::tpy::param_val_or_ref_t<{self.wrapped.name}>"
        return f"{self.wrapped.to_cpp()}&"

    def to_cpp_param(self, name: str) -> str:
        return f"{self.to_cpp_param_type()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return f"const {self.wrapped.to_cpp()}& {name}"

    def to_cpp_stored(self) -> str:
        """C++ type for storage in containers that cannot hold references
        (std::expected, std::optional)."""
        return f"::tpy::val_or_ref<{self.wrapped.to_cpp()}>"

    def is_value_type(self) -> bool:
        return False

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        return self.wrapped.is_sync()

    def is_ref_param(self) -> bool:
        return True

    def param_needs_copy_for_reassign(self) -> bool:
        return False

    def get_element_type(self) -> Optional['TpyType']:
        return self.wrapped.get_element_type()

    def __str__(self) -> str:
        return f"Ref[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return make_ref(types[0])


def make_ref(t: 'TpyType') -> 'TpyType':
    """Wrap non-value types in Ref[T] to make reference semantics explicit.

    No-op for value types and types that already carry their own
    ownership/reference semantics (Own, Readonly, Optional, Union).
    TypeParamRef is always wrapped -- the C++ trait aliases handle
    value-vs-ref dispatch at template instantiation time.
    """
    if isinstance(t, (OwnType, RefType, ReadonlyType,
                      VoidType, NoneType, OptionalType, UnionType)):
        return t
    # AutoReadonlyType / AutoOwnType are stripped before sema normalization
    # runs, so they should never reach here. Guard defensively.
    if isinstance(t, (AutoReadonlyType, AutoOwnType)):
        return t
    # Tuples are value types but may contain non-value elements that need
    # Ref wrapping (e.g. tuple[str, Point] -> tuple[str, Ref[Point]]).
    if isinstance(t, TupleType):
        new_elems = tuple(make_ref(e) for e in t.element_types)
        if all(n is o for n, o in zip(new_elems, t.element_types)):
            return t
        return TupleType(new_elems)
    # Value types don't need Ref. TypeParamRef.is_value_type() returns
    # the right answer: False for TYPE kind (needs wrapping -- the C++ trait
    # aliases handle value-vs-ref at instantiation), True for INT kind and
    # ValueType-bounded (genuinely value types, no wrapping).
    if t.is_value_type():
        return t
    # Protocol types are abstract (Iterator[T], Iterable[T], etc.) -- they
    # don't represent concrete C++ storage, so Ref doesn't apply.  The Ref
    # belongs on the element type inside the protocol, not on the protocol.
    if is_protocol_type(t):
        return t
    return RefType(t)


def unwrap_ref_type(t: 'TpyType') -> 'TpyType':
    """Strip Ref wrapper if present, recursing into tuples."""
    if isinstance(t, RefType):
        return t.wrapped
    if isinstance(t, TupleType):
        new_elems = tuple(unwrap_ref_type(e) for e in t.element_types)
        if all(n is o for n, o in zip(new_elems, t.element_types)):
            return t
        return TupleType(new_elems)
    return t



def is_ref_type(t: 'TpyType') -> bool:
    """Return True if t is a RefType."""
    return isinstance(t, RefType)


@dataclass(frozen=True)
class AutoReadonlyType(TpyType):
    """Type annotation for auto_readonly methods.

    auto_readonly[T] in a return type or parameter means:
    - mutable overload: strip to T        (via strip_auto_readonly)
    - const overload:   replace with readonly[T] (via apply_auto_readonly)

    Valid in return types and parameter types of auto_readonly methods,
    and as self: auto_readonly[Self] to trigger per-param cloning.
    Stripped by _clone_auto_readonly before reaching sema body analysis or codegen.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        raise RuntimeError("AutoReadonlyType must be stripped before codegen")

    def is_value_type(self) -> bool:
        return self.wrapped.is_value_type()

    def __str__(self) -> str:
        return f"auto_readonly[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return AutoReadonlyType(types[0])


def strip_auto_readonly(t: 'TpyType') -> 'TpyType':
    """Replace AutoReadonlyType(X) -> X recursively (mutable overload return type)."""
    if isinstance(t, AutoReadonlyType):
        return strip_auto_readonly(t.wrapped)
    inner = t.inner_types()
    if not inner:
        return t
    new_inner = tuple(strip_auto_readonly(i) for i in inner)
    if all(n is o for n, o in zip(new_inner, inner)):
        return t
    return t.with_inner_types(new_inner)


def apply_auto_readonly(t: 'TpyType') -> 'TpyType':
    """Replace AutoReadonlyType(X) -> readonly[X] recursively (const overload return type)."""
    if isinstance(t, AutoReadonlyType):
        return ReadonlyType(apply_auto_readonly(t.wrapped))
    inner = t.inner_types()
    if not inner:
        return t
    new_inner = tuple(apply_auto_readonly(i) for i in inner)
    if all(n is o for n, o in zip(new_inner, inner)):
        return t
    return t.with_inner_types(new_inner)


def has_auto_readonly(t: 'TpyType') -> bool:
    """Return True if t contains any AutoReadonlyType node."""
    if isinstance(t, AutoReadonlyType):
        return True
    return any(has_auto_readonly(i) for i in t.inner_types())


@dataclass(frozen=True)
class AutoOwnType(TpyType):
    """Return-type annotation for auto_own methods.

    auto_own[T] in a return type means:
    - borrowing overload: strip to T     (via strip_auto_own)
    - consuming overload: replace with Own[T] (via apply_auto_own)

    Only valid in return type annotations of methods with self: auto_own[Self].
    Stripped by registration before reaching sema body analysis or codegen.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        raise RuntimeError("AutoOwnType must be stripped before codegen")

    def is_value_type(self) -> bool:
        return self.wrapped.is_value_type()

    def __str__(self) -> str:
        return f"auto_own[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return AutoOwnType(types[0])


def strip_auto_own(t: 'TpyType') -> 'TpyType':
    """Replace AutoOwnType(X) -> X recursively (borrowing overload return type)."""
    if isinstance(t, AutoOwnType):
        return strip_auto_own(t.wrapped)
    inner = t.inner_types()
    if not inner:
        return t
    new_inner = tuple(strip_auto_own(i) for i in inner)
    if all(n is o for n, o in zip(new_inner, inner)):
        return t
    return t.with_inner_types(new_inner)


def apply_auto_own(t: 'TpyType') -> 'TpyType':
    """Replace AutoOwnType(X) -> Own[X] recursively (consuming overload return type)."""
    if isinstance(t, AutoOwnType):
        return OwnType(apply_auto_own(t.wrapped))
    inner = t.inner_types()
    if not inner:
        return t
    new_inner = tuple(apply_auto_own(i) for i in inner)
    if all(n is o for n, o in zip(new_inner, inner)):
        return t
    return t.with_inner_types(new_inner)


def has_auto_own(t: 'TpyType') -> bool:
    """Return True if t contains any AutoOwnType node."""
    if isinstance(t, AutoOwnType):
        return True
    return any(has_auto_own(i) for i in t.inner_types())



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

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        return self.wrapped.is_sync()

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
    """Check if a type is any string type (str, String, StrView, PendingStr, Literal[str]).

    Includes LiteralType with str base (the Literal["r", "w"] annotation type).
    Single-value LiteralType instances from enrichment never reach storage/codegen.
    """
    if isinstance(typ, (StrType, StringType, StrViewType, PendingStrType)):
        return True
    return isinstance(typ, LiteralType) and typ.is_str_base()


def is_any_bytes_type(typ: 'TpyType') -> bool:
    """Check if a type is any bytes type (bytes, bytearray, BytesView, PendingBytes)."""
    return isinstance(typ, (BytesType, ByteArrayType, BytesViewType, PendingBytesType))


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
        if t.name in bounds:
            resolved_bound = bounds[t.name]
            # Replace bound if missing or if the resolved version has better info
            # (e.g. is_protocol=True from sema vs is_protocol=False from parser
            # for protocols defined in implicit stdlib .py modules)
            if t.bound is None or (isinstance(t.bound, NamedType) and not t.bound.is_protocol
                                   and resolved_bound.is_protocol):
                return TypeParamRef(t.name, bound=resolved_bound, kind=t.kind)
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

    def is_send(self) -> bool:
        return self.inner.is_send()

    def is_sync(self) -> bool:
        return self.inner.is_sync()

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

    def to_cpp_param_type(self) -> str:
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}*"
        if isinstance(self.inner, StrType):
            return "std::optional<std::string_view>"
        return self.to_cpp()

    def to_cpp_param(self, name: str) -> str:
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}* {name}"
        if isinstance(self.inner, StrType):
            return f"std::optional<std::string_view> {name}"
        return f"{self.to_cpp()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.uses_pointer_repr():
            return f"const {self.inner.to_cpp()}* {name}"
        if isinstance(self.inner, StrType):
            return f"std::optional<std::string_view> {name}"
        return f"{self.to_cpp()} {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        return isinstance(self.inner, StrType)

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

    def is_send(self) -> bool:
        return all(m.is_send() for m in self.members)

    def is_sync(self) -> bool:
        return all(m.is_sync() for m in self.members)

    def uses_pointer_repr(self) -> bool:
        """Whether this union uses pointer-variant repr for params/returns/locals.

        True when any non-None member is not a value type (e.g. Dog | Cat with records).
        Pointer variants use std::variant<Dog*, Cat*> instead of std::variant<Dog, Cat>.
        """
        return not self.is_value_type()

    def to_cpp_ptr_variant(self) -> str:
        """Return the pointer-variant type: std::variant<Dog*, Cat*>.

        Monostate members (None) stay as std::monostate.
        Does not use type aliases (aliases are for value variants only).
        """
        cpp_members = [
            "std::monostate" if isinstance(m, (NoneType, VoidType))
            else f"{m.to_cpp()}*"
            for m in self.members
        ]
        return f"std::variant<{', '.join(cpp_members)}>"

    def to_cpp_const_ptr_variant(self) -> str:
        """Return the const pointer-variant type: std::variant<const Dog*, const Cat*>."""
        cpp_members = [
            "std::monostate" if isinstance(m, (NoneType, VoidType))
            else f"const {m.to_cpp()}*"
            for m in self.members
        ]
        return f"std::variant<{', '.join(cpp_members)}>"

    def to_cpp_param_type(self) -> str:
        if self.uses_pointer_repr():
            return self.to_cpp_ptr_variant()
        return f"const {self.to_cpp()}&"

    def to_cpp_param(self, name: str) -> str:
        if self.uses_pointer_repr():
            return f"{self.to_cpp_ptr_variant()} {name}"
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.uses_pointer_repr():
            # Shallow const: const on the variant, not on the pointers.
            # Constructors and non-mutated params use this for efficiency
            # without changing the pointer types (which would break callers).
            return f"const {self.to_cpp_ptr_variant()} {name}"
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_return(self) -> str:
        if self.uses_pointer_repr():
            return self.to_cpp_ptr_variant()
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        if self.uses_pointer_repr():
            return self.to_cpp_const_ptr_variant()
        return self.to_cpp()

    def is_ref_param(self) -> bool:
        if self.uses_pointer_repr():
            return False  # pointer variant passed by value
        return not self.is_value_type()

    def param_needs_copy_for_reassign(self) -> bool:
        return False

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

    def to_cpp_stored(self) -> str:
        args = ", ".join(t.to_cpp_stored() for t in self.element_types)
        return f"std::tuple<{args}>"

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return all(t.is_send() for t in self.element_types)

    def is_sync(self) -> bool:
        return all(t.is_sync() for t in self.element_types)

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

    def to_cpp_param_type(self) -> str:
        args = ", ".join(t.to_cpp_return() for t in self.element_types)
        return f"const std::tuple<{args}>&"

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
    """Recursively resolve IntLiteralType (and FloatLiteralType) inside composite types.

    resolver can be a fixed type or a callable (e.g. default_int_for_literal)
    that maps IntLiteralType -> concrete int type.
    FloatLiteralType always resolves to FloatType (float64).
    Handles TupleType, ArrayType, ListType at arbitrary nesting depth.
    """
    def _resolve(t: TpyType) -> TpyType:
        if isinstance(t, IntLiteralType):
            return resolver(t) if callable(resolver) else resolver
        if isinstance(t, FloatLiteralType):
            return FloatType()
        if isinstance(t, TupleType):
            return t.map_inner_types(_resolve)
        if isinstance(t, ArrayType) and isinstance(t.element_type, (IntLiteralType, FloatLiteralType)):
            elem = _resolve(t.element_type)
            return ArrayType(elem, t.size)
        if isinstance(t, ListType) and isinstance(t.element_type, (IntLiteralType, FloatLiteralType)):
            elem = _resolve(t.element_type)
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

    def is_send(self) -> bool:
        return self.element_type.is_send()

    def is_sync(self) -> bool:
        return self.element_type.is_sync()

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def subscript_borrows(self) -> bool:
        return True

    def needs_explicit_element_target(self) -> bool:
        return True

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ArrayType(types[0], self.size)


class SpanType(NamedType):
    """Non-owning view: Span[T] -> std::span<T>, Span[readonly[T]] -> std::span<const T>

    Const-ness is encoded in the element type: Span[readonly[T]] stores
    ReadonlyType(T) as the element, giving std::span<const T> in C++.
    """

    def __init__(self, element_type: TpyType, is_readonly: bool = False):
        # Normalize: is_readonly=True wraps element in ReadonlyType.
        # Also accepts ReadonlyType(T) directly as element_type.
        if is_readonly and not isinstance(element_type, ReadonlyType):
            element_type = ReadonlyType(element_type)
        NamedType.__init__(self, name="Span", type_args=(element_type,),
                           _module_qname="tpy.Span")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def is_readonly(self) -> bool:
        return isinstance(self.element_type, ReadonlyType)

    @property
    def inner_element_type(self) -> TpyType:
        """Unwrapped element type (strips ReadonlyType if present)."""
        return unwrap_readonly(self.element_type)

    def to_cpp(self) -> str:
        if self.is_readonly:
            return f"std::span<const {self.inner_element_type.to_cpp()}>"
        return f"std::span<{self.element_type.to_cpp()}>"

    def __str__(self) -> str:
        if self.is_readonly:
            return f"Span[readonly[{self.inner_element_type}]]"
        return f"Span[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.Span"

    def is_value_type(self) -> bool:
        # Spans are lightweight views (ptr + size), passed/returned by value
        return True

    def is_send(self) -> bool:
        # Spans borrow from another container -- not safe to transfer
        return False

    def is_sync(self) -> bool:
        if self.is_readonly:
            return self.inner_element_type.is_sync()
        return False

    def get_element_type(self) -> Optional[TpyType]:
        # Return unwrapped element type for iteration/subscript type inference.
        return self.inner_element_type

    def needs_explicit_element_target(self) -> bool:
        return True

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        # readonly preserved if types[0] is ReadonlyType(...)
        return SpanType(types[0])

    def as_const(self) -> 'SpanType':
        """Return a const (Span[readonly[T]]) version of this span."""
        if self.is_readonly:
            return self
        return SpanType(ReadonlyType(self.element_type))

    def as_mutable(self) -> 'SpanType':
        """Return a mutable (Span) version of this span."""
        if not self.is_readonly:
            return self
        return SpanType(self.inner_element_type)


class SpanIterType(NamedType):
    """Iterator over a contiguous span.

    SpanIter[T] -> ::tpy::SpanIter<T> (mutable elements T&)
    SpanIter[readonly[T]] -> ::tpy::SpanIter<const T> (const elements)

    Const-ness follows the same pattern as SpanType: ReadonlyType in the
    element encodes the const variant. SpanIter<T> holds span<T> internally.
    """

    def __init__(self, element_type: TpyType):
        NamedType.__init__(self, name="SpanIter", type_args=(element_type,),
                           _module_qname="tpy.SpanIter")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def is_readonly(self) -> bool:
        return isinstance(self.element_type, ReadonlyType)

    @property
    def inner_element_type(self) -> TpyType:
        """Unwrapped element type (strips ReadonlyType if present)."""
        return unwrap_readonly(self.element_type)

    def to_cpp(self) -> str:
        if self.is_readonly:
            return f"::tpy::SpanIter<const {self.inner_element_type.to_cpp()}>"
        return f"::tpy::SpanIter<{self.element_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"SpanIter[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.SpanIter"

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return False

    def get_element_type(self) -> Optional[TpyType]:
        return self.inner_element_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return SpanIterType(types[0])


class CopyIterType(NamedType):
    """Iterator adapter that copies each element from a borrowing iterator.

    CopyIter[T] -> tpy::CopyIter<T, Inner> at C++ level.
    The Inner type is deduced by the C++ compiler; sema only tracks T.
    """

    def __init__(self, element_type: TpyType):
        NamedType.__init__(self, name="CopyIter", type_args=(element_type,),
                           _module_qname="tpy.CopyIter")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    def to_cpp(self) -> str:
        # Full C++ type requires the Inner param which is auto-deduced.
        # Use auto for variable declarations; codegen produces the factory call.
        return "auto"

    def __str__(self) -> str:
        return f"CopyIter[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.CopyIter"

    def is_value_type(self) -> bool:
        return True

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return CopyIterType(types[0])


class OwnIterType(NamedType):
    """Consuming iterator that owns a moved container and iterates with moves.

    OwnIter[T] -> tpy::OwnIter<T> at C++ level.
    Created by own_iter(container) which moves the container into the iterator.
    """

    def __init__(self, element_type: TpyType):
        NamedType.__init__(self, name="OwnIter", type_args=(element_type,),
                           _module_qname="tpy.OwnIter")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    def to_cpp(self) -> str:
        return "auto"

    def __str__(self) -> str:
        return f"OwnIter[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "tpy.OwnIter"

    def is_value_type(self) -> bool:
        return True

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return OwnIterType(types[0])


def is_readonly_span(typ: 'TpyType') -> bool:
    """Check if a type is a read-only span (Span[readonly[T]])."""
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

    def is_send(self) -> bool:
        return self.element_type.is_send()

    def is_sync(self) -> bool:
        # Mutable container -- not safe to share references across threads
        return False

    def subscript_borrows(self) -> bool:
        return True

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ListType(types[0])


class DictType(NamedType):
    """Dict type: dict[K, V] -> ::tpy::ordered_map<K, V>"""

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
        return f"::tpy::ordered_map<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"dict[{self.key_type}, {self.value_type}]"

    def qualified_name(self) -> Optional[str]:
        return "builtins.dict"

    def is_send(self) -> bool:
        return self.key_type.is_send() and self.value_type.is_send()

    def is_sync(self) -> bool:
        # Mutable container -- not safe to share references across threads
        return False

    def subscript_borrows(self) -> bool:
        return True

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


class SetType(NamedType):
    """Set type: set[T] -> ::tpy::ordered_set<T>"""

    def __init__(self, element_type: TpyType):
        NamedType.__init__(self, name="set", type_args=(element_type,),
                           _module_qname="builtins.set")

    @property
    def element_type(self) -> TpyType:
        return self.type_args[0]

    def to_cpp(self) -> str:
        return f"::tpy::ordered_set<{self.element_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"set[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return "builtins.set"

    def is_send(self) -> bool:
        return self.element_type.is_send()

    def is_sync(self) -> bool:
        return False

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.element_type,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return SetType(types[0])


class DictKeysViewType(NamedType):
    """Dict keys view: d.keys() -> ::tpy::dict_keys_view<K, V>"""

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
        return f"::tpy::dict_keys_view<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.key_type

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"dict_keys[{self.key_type}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictKeysViewType(types[0], types[1])


class DictValuesViewType(NamedType):
    """Dict values view: d.values() -> ::tpy::dict_values_view<K, V>"""

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
        return f"::tpy::dict_values_view<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.value_type

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"dict_values[{self.value_type}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictValuesViewType(types[0], types[1])


class DictItemsViewType(NamedType):
    """Dict items view: d.items() -> ::tpy::dict_items_view<K, V>"""

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
        return f"::tpy::dict_items_view<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return TupleType((self.key_type, self.value_type))

    def is_value_type(self) -> bool:
        return True

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"dict_items[{self.key_type}, {self.value_type}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return DictItemsViewType(types[0], types[1])


@dataclass(frozen=True)
class UnknownElementType(TpyType):
    """Sentinel for empty container literals whose element type is not yet known.

    Used as the element/key/value type for PendingListType and PendingDictType
    created from empty literals ([], {}, list(), dict()). The actual type is
    inferred from subsequent usage and stored in the corresponding info object.
    """

    def to_cpp(self) -> str:
        raise RuntimeError("UnknownElementType should be resolved before codegen")

    def __str__(self) -> str:
        return "???"


UNKNOWN_ELEMENT = UnknownElementType()


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
        return f"::tpy::repeat_range<{self.element_type.to_cpp()}>"

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def __str__(self) -> str:
        return f"repeat[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


@dataclass(frozen=True)
class GenExprType(TpyType):
    """Generator expression type -- lazy iterable producing T.

    Internal type, not user-facing. Satisfies Iterable[T].
    C++ representation is ::tpy::generator_wrapper<T, lambda> (auto-deduced).
    """
    element_type: TpyType

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def to_cpp(self) -> str:
        return "auto"

    def __str__(self) -> str:
        return f"<genexpr>[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


@dataclass(frozen=True)
class FnType(TpyType):
    """Fn[[ParamType, ...], ReturnType] -- zero-cost callable (template).

    Valid only in function parameter position. Generates a C++ template parameter
    with a requires clause constraining the call signature.
    """
    param_types: tuple[TpyType, ...]
    return_type: TpyType

    def to_cpp(self) -> str:
        raise RuntimeError(
            "FnType.to_cpp() should not be called directly; "
            "Fn params use template codegen"
        )

    def is_value_type(self) -> bool:
        return True

    def inner_types(self) -> tuple[TpyType, ...]:
        return self.param_types + (self.return_type,)

    def with_inner_types(self, types: tuple[TpyType, ...]) -> 'TpyType':
        return FnType(types[:-1], types[-1])

    def __str__(self) -> str:
        params = ", ".join(str(t) for t in self.param_types)
        return f"Fn[[{params}], {self.return_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


def contains_fn_type(typ: TpyType) -> bool:
    """Check if a type contains FnType anywhere in its structure."""
    if isinstance(typ, FnType):
        return True
    return any(contains_fn_type(inner) for inner in typ.inner_types())


@dataclass(frozen=True)
class CallableType(TpyType):
    """Callable[[ParamType, ...], ReturnType] -- type-erased callable (std::function).

    Valid in all positions: params, fields, returns, containers, locals.
    """
    param_types: tuple[TpyType, ...]
    return_type: TpyType

    @staticmethod
    def _callable_param_cpp(t: 'TpyType') -> str:
        """C++ type for a parameter in a std::function signature.

        Uses to_cpp_param_type() for types that override it (str -> string_view,
        BigInt -> const BigInt&), but ensures non-value types get const ref
        (regular function params can be mutable ref, but std::function params
        must accept rvalues and const-qualified arguments).
        """
        cpp = t.to_cpp_param_type()
        # Ensure non-value types are const ref, not mutable ref
        if not t.is_value_type() and not cpp.startswith("const "):
            return f"const {t.to_cpp()}&"
        return cpp

    def _std_function_sig(self) -> str:
        ret = "void" if isinstance(self.return_type, VoidType) else self.return_type.to_cpp()
        params = ", ".join(self._callable_param_cpp(t) for t in self.param_types)
        return f"std::function<{ret}({params})>"

    def to_cpp(self) -> str:
        return self._std_function_sig()

    def to_cpp_param_type(self) -> str:
        return f"const {self._std_function_sig()}&"

    def to_cpp_param(self, name: str) -> str:
        return f"const {self._std_function_sig()}& {name}"

    def is_value_type(self) -> bool:
        return True

    def inner_types(self) -> tuple[TpyType, ...]:
        return self.param_types + (self.return_type,)

    def with_inner_types(self, types: tuple[TpyType, ...]) -> 'TpyType':
        return CallableType(types[:-1], types[-1])

    def __str__(self) -> str:
        params = ", ".join(str(t) for t in self.param_types)
        return f"Callable[[{params}], {self.return_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


@dataclass
class ListLiteralInfo:
    """Tracks usage information for a list literal to determine its resolved type."""
    literal_id: int
    expr: 'TpyArrayLiteral | TpyListRepeat | TpyListComprehension | TpyCall'  # Forward reference to avoid circular import
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
    needs_list_type: bool = False  # Used in or/and/ternary with another list -- cannot become Array
    source_literal_id: Optional[int] = None  # Alias tracking: b = a
    resolved_type: Optional[TpyType] = None


@dataclass(frozen=True)
class PendingDictType(TpyType):
    """Unresolved empty dict literal -- key/value types inferred from usage.

    Assigned to empty dict literals ({}) or dict() calls in function-local
    contexts. After full function analysis, resolved to DictType based on
    collected usage facts (primarily d[k] = v subscript assignment).
    """
    key_type: TpyType
    value_type: TpyType
    literal_id: int

    def to_cpp(self) -> str:
        raise RuntimeError(f"PendingDictType should be resolved before codegen (literal_id={self.literal_id})")

    def get_element_type(self) -> Optional[TpyType]:
        return self.value_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.key_type

    def __str__(self) -> str:
        return f"PendingDict[{self.key_type}, {self.value_type}]#{self.literal_id}"

    def qualified_name(self) -> Optional[str]:
        return "builtins.dict"


@dataclass
class DictLiteralInfo:
    """Tracks usage information for an empty dict literal to determine its resolved type."""
    literal_id: int
    expr: 'TpyDictLiteral | TpyCall'
    key_type: TpyType
    value_type: TpyType
    variable_name: Optional[str] = None
    decl_line: Optional[int] = None
    resolved_type: Optional[TpyType] = None


@dataclass(frozen=True)
class PendingSetType(TpyType):
    """Unresolved empty set -- element type inferred from usage.

    Assigned to set() calls in function-local contexts. After full function
    analysis, resolved to SetType based on collected usage facts (primarily
    s.add(v) calls).
    """
    element_type: TpyType
    literal_id: int

    def to_cpp(self) -> str:
        raise RuntimeError(f"PendingSetType should be resolved before codegen (literal_id={self.literal_id})")

    def get_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def get_iteration_element_type(self) -> Optional[TpyType]:
        return self.element_type

    def __str__(self) -> str:
        return f"PendingSet[{self.element_type}]#{self.literal_id}"

    def qualified_name(self) -> Optional[str]:
        return "builtins.set"


@dataclass
class SetLiteralInfo:
    """Tracks usage information for an empty set to determine its resolved type."""
    literal_id: int
    expr: 'TpyCall'
    element_type: TpyType
    variable_name: Optional[str] = None
    decl_line: Optional[int] = None
    resolved_type: Optional[TpyType] = None


@dataclass(frozen=True)
class PendingGenericInstanceType(TpyType):
    """Unresolved generic record instance -- type params inferred from usage.

    Created when a generic record is constructed without explicit type args
    and without enough context to infer them. Resolved eagerly once all
    type params are constrained by subsequent method calls.
    """
    record_name: str
    instance_id: int

    def to_cpp(self) -> str:
        raise RuntimeError(
            f"PendingGenericInstanceType should be resolved before codegen "
            f"(record={self.record_name}, instance_id={self.instance_id})"
        )

    def __str__(self) -> str:
        return f"{self.record_name}[?]"

    def qualified_name(self) -> Optional[str]:
        return None

    def is_value_type(self) -> bool:
        return False


@dataclass
class PendingGenericInstanceInfo:
    """Tracks a deferred generic instance for type parameter inference."""
    instance_id: int
    variable_name: str
    record_info: 'RecordInfo'
    record_name: str
    type_params: list[str]
    inferred: dict[str, 'TpyType']
    expr: 'TpyCall'
    decl_line: Optional[int] = None


@dataclass(frozen=True)
class ViewTypeFamily:
    """Descriptor for a view-type family (str or bytes).

    Parameterizes the shared pending-view-type infrastructure so that
    str and bytes paths share one implementation.
    """
    owned_type: TpyType
    view_type: TpyType
    promote_param_type: type  # isinstance target: StringType or ByteArrayType
    pending_type_class: type  # PendingStrType or PendingBytesType
    element_type: TpyType
    display_name: str
    qualified: str


@dataclass(frozen=True)
class PendingViewType(TpyType):
    """Base for unresolved view-type locals (str or bytes).

    Assigned to str/bytes-typed locals in function contexts during the
    first analysis phase. After the full function body is analyzed,
    resolved based on collected usage (augmented assignment, owned
    source, etc.).
    """
    var_id: int

    @property
    def family(self) -> ViewTypeFamily:
        raise NotImplementedError

    def to_cpp(self) -> str:
        raise RuntimeError(
            f"{type(self).__name__} should be resolved before codegen (var_id={self.var_id})"
        )

    def __str__(self) -> str:
        return self.family.display_name

    def qualified_name(self) -> Optional[str]:
        return self.family.qualified

    def is_value_type(self) -> bool:
        return True

    def get_element_type(self) -> Optional[TpyType]:
        return self.family.element_type


@dataclass(frozen=True)
class PendingStrType(PendingViewType):
    """Unresolved string local -- becomes StrView or str based on usage."""

    @property
    def family(self) -> ViewTypeFamily:
        return STR_FAMILY


@dataclass(frozen=True)
class PendingBytesType(PendingViewType):
    """Unresolved bytes local -- becomes BytesView or bytes based on usage."""

    @property
    def family(self) -> ViewTypeFamily:
        return BYTES_FAMILY


@dataclass
class ViewVarInfo:
    """Tracks usage of a view-type local to decide view vs owned."""
    var_id: int
    variable_name: str
    decl_line: Optional[int] = None
    initialized_from_owned: bool = False
    used_in_augassign: bool = False
    passed_to_promote_param: bool = False
    reassigned_from_owned: bool = False
    source_var_ids: list[int] = field(default_factory=list)
    source_storage: Optional[str] = None
    source_mutated: bool = False
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
BYTES = BytesType()
BYTEARRAY = ByteArrayType()
BYTESVIEW = BytesViewType()
BOOL = BoolType()
FLOAT = FloatType()
FLOAT32 = Float32Type()

BIGINT = BigIntType()
NONE = NoneType()
SLICE = SliceType()

# View-type family descriptors (must follow singleton definitions)
STR_FAMILY = ViewTypeFamily(
    owned_type=STR, view_type=STRVIEW, promote_param_type=StringType,
    pending_type_class=PendingStrType, element_type=CHAR,
    display_name="str", qualified="builtins.str",
)
BYTES_FAMILY = ViewTypeFamily(
    owned_type=BYTES, view_type=BYTESVIEW, promote_param_type=ByteArrayType,
    pending_type_class=PendingBytesType, element_type=UINT8,
    display_name="bytes", qualified="builtins.bytes",
)
VIEW_TYPE_FAMILIES = (STR_FAMILY, BYTES_FAMILY)

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
        return "::tpy::tuple_to_str({0})"
    if isinstance(typ, (ListType, ArrayType, SpanType)):
        return "::tpy::list_to_str({0})"
    if isinstance(typ, DictType):
        return "::tpy::dict_to_str({0})"
    if isinstance(typ, SetType):
        return "::tpy::set_to_str({0})"
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
class PropertyInfo:
    """Descriptor for a @property on a record."""
    name: str
    getter: 'FunctionInfo'
    setter: Optional['FunctionInfo'] = None

    @property
    def type(self) -> TpyType:
        return self.getter.return_type


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
    - extends_protocols stores protocol implementations (e.g., ["NativeIterable[T]"])
    - Methods have cpp_template or native_function for codegen
    - __init__ overloads in methods store constructor signatures with cpp_template
    """
    name: str
    fields: list[FieldInfo]
    has_init: bool = False
    init_params: list[tuple[str, TpyType, Optional[str]]] = field(default_factory=list)  # (name, type, default)
    methods: dict[str, list['FunctionInfo']] = field(default_factory=dict)  # method_name -> list of overloads
    properties: dict[str, 'PropertyInfo'] = field(default_factory=dict)  # property_name -> PropertyInfo
    type_params: list[str] = field(default_factory=list)  # ["T", "U"] for class Stack[T, U]
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)  # [TYPE, INT] for class Matrix[T, N: int]
    type_param_bounds: dict[str, 'NamedType'] = field(default_factory=dict)  # {"T": Comparable} (must be protocols)
    type_factory: "Optional[Callable[..., TpyType]]" = None  # Factory to create concrete type from params
    parent: Optional['TpyType'] = None  # Parent type (NamedType or builtin TpyType)
    implemented_protocols: list['NamedType'] = field(default_factory=list)  # Explicit protocol implementations
    extends_protocols: list[str] = field(default_factory=list)  # Protocol extensions: ["NativeIterable[T]"]
    native_name: Optional[str] = None  # C++ name for @native/@native_c records (e.g., "SDL_Rect")
    is_native: bool = False       # True for @native or @native_c records
    is_native_c: bool = False     # True for @native_c specifically
    is_nocopy: bool = False       # True for @nocopy records (copy deleted, move-only)
    match_args: tuple[str, ...] | None = None  # Positional match arg names (set by macro, mirrors __match_args__)
    is_frozen: bool = False       # True for @dataclass(frozen=True) (field mutation rejected)
    is_value_type: bool = False   # True for ValueType marker protocol
    has_del: bool = False           # True if class declares __del__ (needs drop flag)
    has_copy: bool = False          # True if class defines __copy__ (custom copy semantics)
    builtin_type_key: str | None = None  # e.g. "builtins.list" -- links .py class to type_factory

    @property
    def is_keyword_stub(self) -> bool:
        """True for @builtin_type stubs with no methods or fields.

        These exist only for parser/import resolution (e.g. typing.Protocol,
        typing.overload) and should not generate C++ code.
        """
        return self.builtin_type_key is not None and not self.methods and not self.fields

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
    requires_mutable_lvalue: bool = False
    default_expr: 'Any | None' = None  # TpyExpr from parser; None = required param

    @property
    def has_default(self) -> bool:
        return self.default_expr is not None

    def __iter__(self):
        yield self.name
        yield self.type


@dataclass
class MutationCallEdge:
    """Records parameter flow through a function call (for mutation propagation)."""
    callee_fi: 'FunctionInfo'
    param_map: dict[int, int]  # callee_param_idx -> caller_param_idx
    receiver_is_self: bool = False  # True when callee is called as self.method()


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
    is_pure: bool = False
    is_consuming: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    is_property_getter: bool = False
    is_property_setter: bool = False
    property_name: Optional[str] = None  # for setter: which property it belongs to
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: Optional[str] = None
    native_function: bool = False  # @native("func", function=True) -> generates func(self, args)
    native_preserves_refs: bool = False  # non-readonly but doesn't invalidate iterators/refs
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, 'NamedType'] = field(default_factory=dict)
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "tpy.extern.DefaultInt"}
    cpp_template: Optional[str] = None  # For builtins: "{self}.push_back({0})"
    value_ptr_coercion: bool = False  # @value_ptr_coercion: Ptr[T] params accept T values
    is_builtin_function: bool = False  # True for global builtins (len, chr, etc.)
    special_handling: bool = False  # True if sema/codegen handle specially
    error_return_type: Optional[str] = None  # @error_return(E) exception type name
    builtin_decorator_key: Optional[str] = None  # e.g. "tpy.readonly" -- links .py function to decorator semantics
    qualified_name: str = ""  # Full dotted path, e.g. "builtins.print", "tpy.copy", "__main__.foo"
    mutated_params: Optional[frozenset[int]] = None  # Param indices proven mutated; None = unknown (conservative)
    structural_mutated_params: Optional[frozenset[int]] = None
    # Like mutated_params, but only structural mutations (append/insert/clear/del/etc.) that
    # invalidate element references. Excludes element-ref taking (a=items[0]) and field writes.
    # None = not yet analyzed; frozenset() = no structural mutation.
    return_borrows_from: Optional[frozenset[int]] = None
    # Param indices whose storage the return value borrows from (8b).
    # -1 = self (methods only); 0, 1, ... = regular params.
    # None = not yet analyzed; frozenset() = no borrow (value/local return).
    # Phase 1 local facts (set during sema, consumed by Phase 2 propagation)
    direct_mutated_params: Optional[frozenset[int]] = None
    direct_structural_mutated_params: Optional[frozenset[int]] = None
    call_edges: Optional[list['MutationCallEdge']] = None
    # Self-mutation inference (Phase 1 + Phase 2, methods only)
    # None = not yet analyzed; True/False = Phase 1 direct fact; finalized by Phase 2.
    direct_self_mutated: Optional[bool] = None
    self_mutated: bool = True  # conservative default until Phase 2 resolves

    @property
    def is_decorator_stub(self) -> bool:
        """True for @builtin_decorator stubs (no C++ code needed)."""
        return self.builtin_decorator_key is not None

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
    left_wrapper: str   # cpp template for left, e.g., "::tpy::BigInt({expr})" or "{expr}"
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
    "__copy__", "__deref__", "__span__",
})

# Methods that mutate self but should take const params (params are read-only).
# Keep in sync with AUGOP_TO_IMETHOD in modules/defs.py.
CONST_PARAMS_METHODS = frozenset({
    "__iadd__", "__isub__", "__imul__", "__itruediv__", "__ifloordiv__", "__imod__",
    "__iand__", "__ior__", "__ixor__", "__ilshift__", "__irshift__",
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
    - cpp_concept stores the C++ concept name (e.g., "tpy::Sized", auto-qualified to "::tpy::Sized")
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
    is_pointer: bool = False  # True for non-value-type module globals (stored as T* in C++)


@dataclass
class ModuleInfo:
    """Information about a module (builtin or user-defined)."""
    name: str
    is_builtin: bool = True  # True for hardcoded builtin modules (e.g. sys), False for user/.py modules
    is_native_module: bool = False  # True for # tpy: native_module (no __tpy_init, no .cpp)
    generates_header: bool = True  # False for native_modules that produce no C++ output
    functions: dict[str, list[FunctionInfo]] = field(default_factory=dict)  # func_name -> overloads
    variables: dict[str, ModuleVarInfo] = field(default_factory=dict)  # var_name -> ModuleVarInfo
    records: dict[str, RecordInfo] = field(default_factory=dict)  # type_name -> RecordInfo (exported types)
    protocols: dict[str, ProtocolInfo] = field(default_factory=dict)  # protocol_name -> ProtocolInfo
    type_aliases: dict[str, 'TpyType'] = field(default_factory=dict)  # alias_name -> resolved type
    enums: dict[str, 'EnumType'] = field(default_factory=dict)  # enum_name -> EnumType

    def has_export(self, name: str) -> bool:
        """Check if a name is exported by this module."""
        return (name in self.functions or name in self.records or
                name in self.protocols or name in self.enums or
                name in self.type_aliases or name in self.variables)


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self):
        self.records: dict[str, RecordInfo] = {}
        self._qname_index: dict[str, RecordInfo] = {}  # qualified name -> RecordInfo
        self.functions: dict[str, list[FunctionInfo]] = {}  # User-defined functions (single or @overload group)
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
        key = name or info.name
        self.records[key] = info
        if info.builtin_type_key:
            self._qname_index[info.builtin_type_key] = info

    def register_builtin_record(self, qname: str, info: RecordInfo) -> None:
        """Register a builtin type's RecordInfo by its qualified name."""
        self._qname_index[qname] = info

    def get_builtin_type_key(self, name: str) -> str | None:
        """Get the @builtin_type key for a locally-registered record, if any."""
        record = self.records.get(name)
        return record.builtin_type_key if record else None

    def get_builtin_decorator_key(self, name: str) -> str | None:
        """Get the @builtin_decorator key for a locally-registered function, if any."""
        funcs = self.functions.get(name)
        if funcs and len(funcs) == 1 and funcs[0].builtin_decorator_key:
            return funcs[0].builtin_decorator_key
        return None

    def register_function(self, info: FunctionInfo, name: str | None = None) -> None:
        """Register a single function (wraps in a list)."""
        self.functions[name or info.name] = [info]

    def register_function_group(self, name: str, infos: list[FunctionInfo]) -> None:
        """Register a function group (single or @overload)."""
        self.functions[name] = infos

    def get_function(self, name: str) -> list[FunctionInfo] | None:
        """Get function(s) by name, or None if not found."""
        return self.functions.get(name)

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

    def find_module_record(self, module_name: str, name: str) -> Optional[RecordInfo]:
        """Find a record in a specific module."""
        mod = self.modules.get(module_name)
        if mod and name in mod.records:
            return mod.records[name]
        return None

    def get_all_fields(self, record: RecordInfo) -> list[FieldInfo]:
        """Get all fields for a record including inherited, in parent-first order."""
        if record.parent is None or not isinstance(record.parent, NamedType):
            return list(record.fields)
        parent = self.get_record(record.parent.name)
        if parent is None:
            return list(record.fields)
        return self.get_all_fields(parent) + list(record.fields)

    def find_record_by_qname(self, qname: str) -> Optional[RecordInfo]:
        """Find a record by qualified name (e.g. 'builtins.Exception')."""
        # Check @builtin_type index first
        result = self._qname_index.get(qname)
        if result is not None:
            return result
        # Fall back to module lookup
        if "." in qname:
            module_name, bare_name = qname.rsplit(".", 1)
            return self.find_module_record(module_name, bare_name)
        return self.find_record(qname)

    def get_builtin_record(self, qname: str) -> Optional[RecordInfo]:
        """Get a builtin record by qualified name."""
        return self._qname_index.get(qname)

    def get_native_builtin_records(self) -> list[RecordInfo]:
        """Return native records that need C++ name registration.

        Used by codegen to register C++ name mappings for types that don't
        have dedicated type classes (e.g. TextIO -> tpy::TextFile,
        ValueError -> tpy::ValueError).
        """
        seen: set[str] = set()
        result: list[RecordInfo] = []
        for r in self._qname_index.values():
            if r.is_native and r.native_name and not r.type_factory:
                result.append(r)
                seen.add(r.name)
        for mod in self.modules.values():
            for r in mod.records.values():
                if r.name not in seen and r.is_native and r.native_name and not r.type_factory:
                    result.append(r)
                    seen.add(r.name)
        return result

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

    def is_subclass_of_record(self, child: RecordInfo, parent: RecordInfo) -> bool:
        """Check if child record inherits from parent (by record identity)."""
        current = child
        visited: set[str] = set()
        while current and current.parent and isinstance(current.parent, NamedType):
            pname = current.parent.name
            if pname in visited:
                break
            visited.add(pname)
            current = self.find_record(pname)
            if current is parent:
                return True
        return False

    def get_method_overloads_with_parents(
        self, record: RecordInfo, method_name: str,
    ) -> list['FunctionInfo']:
        """Look up method overloads on a record, walking the parent chain.

        Returns the first match found (own methods take precedence over inherited).
        Does NOT apply type substitution for generic parents -- callers that need
        substitution should use ProtocolChecker.lookup_record_method_overloads.
        """
        overloads = record.get_method_overloads(method_name)
        if overloads:
            return overloads
        if record.parent:
            parent_info = self.get_record_for_type(record.parent)
            if parent_info:
                return self.get_method_overloads_with_parents(parent_info, method_name)
        return []

    def get_record_for_type(self, tpy_type: 'TpyType') -> Optional[RecordInfo]:
        """Unified lookup for any type's RecordInfo.

        Tries local records by short name first, then _qname_index by
        qualified name. This handles both user records and builtin types
        whose NamedType may or may not have _module_qname set.
        """
        if isinstance(tpy_type, NamedType) and tpy_type.is_record:
            result = self.records.get(tpy_type.name)
            if result is not None:
                return result
        qname = tpy_type.qualified_name()
        if qname:
            return self.get_builtin_record(qname)
        return None

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
        # Check type factory mapping for builtin types defined in .py stubs
        from tpyc.modules import get_type_factory
        for module_name in ("builtins", "tpy"):
            if get_type_factory(f"{module_name}.{name}") is not None:
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
