"""
TurboPython Type System

Defines the core types available in TurboPython:
- Int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- Ptr[readonly[T]]: Read-only pointer (maps to const T*)
- Array[T, N], Span[T], list[T], dict[K, V]: Container types
- User-defined records (classes)
- NominalType: User-defined records/protocols and module-defined generics
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TYPE_CHECKING

from .module_names import public_module_name

if TYPE_CHECKING:
    from .parse.nodes import TpyArrayLiteral, TpyListRepeat, TpyListComprehension, TpyCall, TpyDictLiteral, TypeRefNode


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
# Used by NominalType.to_cpp() so composite types like Ptr[Rect] resolve correctly.
# NOTE: Global mutable state -- safe because the compilation pipeline is sequential
# (each CodeGenerator.generate() call clears and repopulates before use).
# Would need to move into CodeGenContext if codegen ever runs concurrently.
_native_cpp_names: dict[str, str] = {}
_union_alias_names: dict[tuple['TpyType', ...], str] = {}
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


# `public_module_name` lives in `tpyc/module_names.py` so parser can
# import it without dragging in typesys.


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


def impl_proto_matches_name(impl_proto: 'NominalType', protocol_name: str,
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
    """Clear per-module codegen state (called before each module's codegen)."""
    _native_cpp_names.clear()
    _union_alias_names.clear()


def clear_all_compilation_state() -> None:
    """Full reset for a new compilation (called once per tpyc invocation)."""
    from tpyc.type_def_registry import clear_dynamic_type_defs
    _native_cpp_names.clear()
    _union_alias_names.clear()
    _return_exception_names.clear()
    _return_exception_names.update(_BUILTIN_RETURN_EXCEPTIONS)
    _protocol_modules.clear()
    clear_dynamic_type_defs()


@dataclass(frozen=True)
class TpyType:
    """Base class for all TurboPython types."""

    # Every TpyType carries a tuple of type arguments (possibly empty).
    # NominalType overrides with an instance field; primitives and most
    # structurals inherit the empty default. No annotation -- dataclass
    # subclasses would otherwise pick it up as an inherited field.
    type_args = ()

    def to_cpp(self) -> str:
        """Return the C++ representation of this type.

        If the type has a registered cpp_formatter in the TypeDef registry
        (primitives, containers), use it. Otherwise subclasses override.
        """
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.cpp_formatter is not None:
            return td.cpp_formatter(self.type_args)
        if td is not None and td.is_compile_time_only:
            raise TypeError(f"{type(self).__name__} is compile-time only and has no C++ representation")
        raise NotImplementedError

    def is_pointer(self) -> bool:
        """Return True if this is a pointer type."""
        return False

    def qualified_name(self) -> Optional[str]:
        """Return the fully qualified type name for module lookup, or None if not a module type."""
        return None

    def is_compile_time_only(self) -> bool:
        """Return True if this type exists only at compile time (no C++ representation)."""
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            return td.is_compile_time_only
        return False

    def is_value_type(self) -> bool:
        """Return True if this is a value type (copy semantics).

        Value types include primitive types like Int32, BigInt, Bool, Char, str.
        These types should be copied when accessed from containers.

        Object types (RecordType, etc.) return False and should
        use reference semantics when accessed from containers.
        """
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            return td.is_value_type
        return False

    def is_trivially_destructible(self) -> bool:
        """Return True if the C++ type has a trivial destructor.

        Approximation: value types without heap storage. Correct for
        primitives, views, pointers, enums, tuples of trivial types.
        Conservative for non-value types (always returns False even if
        the C++ type is actually trivially destructible, e.g. Span).
        """
        return self.is_value_type() and not self.is_expensive_copy()

    def is_expensive_copy(self) -> bool:
        """Return True if copying this value type involves heap allocation."""
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            return td.is_expensive_copy
        return False

    def subscript_borrows(self) -> bool:
        """Return True if a subscript result can borrow from this container's element
        storage (i.e. the result is a view into the container, not an owned copy).
        Source-mutation tracking at the call site ensures the borrow stays valid.
        User-defined types can opt in once borrow-source annotation (6.7) is implemented."""
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            return td.subscript_borrows
        return False

    def is_send(self) -> bool:
        """Return True if this type is safe to transfer across threads.

        Default: value types are Send (copied, no aliasing). Override for
        pointer-like types (Ptr, Span) and containers (list, dict).
        """
        from tpyc.type_def_registry import type_def_of, resolve_send_sync
        td = type_def_of(self)
        if td is not None:
            resolved = resolve_send_sync(td.is_send, self.type_args)
            if resolved is not None:
                return resolved
        return self.is_value_type()

    def is_sync(self) -> bool:
        """Return True if this type is safe to share references across threads.

        Default: value types are Sync (no mutable shared state). Override for
        mutable containers (list, dict) and pointer types.
        """
        from tpyc.type_def_registry import type_def_of, resolve_send_sync
        td = type_def_of(self)
        if td is not None:
            resolved = resolve_send_sync(td.is_sync, self.type_args)
            if resolved is not None:
                return resolved
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

        Uses the TypeDef's param_cpp_formatter when registered (handles
        per-primitive overrides like str -> std::string_view). Otherwise
        defaults: value types = to_cpp(), non-value = to_cpp() + "&".
        """
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.param_cpp_formatter is not None:
            return td.param_cpp_formatter(self.type_args)
        if self.is_value_type():
            return self.to_cpp()
        return f"{self.to_cpp()}&"

    def to_cpp_param(self, name: str) -> str:
        """Return the C++ parameter declaration for this type.

        Value types (primitives, views) are passed by value: T name
        Object types (containers, records) are passed by mutable reference: T& name
        """
        return f"{self.to_cpp_param_type()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        """Return the C++ const parameter declaration for this type.

        Value types are passed by value: T name
        Object types are passed by const reference: const T& name
        Use for constructor params and other contexts where mutation is not needed.
        """
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.param_cpp_formatter is not None:
            return f"{td.param_cpp_formatter(self.type_args)} {name}"
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
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            return td.param_needs_copy_for_reassign
        return False

    def get_element_type(self) -> Optional['TpyType']:
        """Return the element type for container types, or None for non-containers."""
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.element_of is not None:
            return td.element_of(self.type_args)
        return None

    def needs_explicit_element_target(self) -> bool:
        """Return True if array literals need explicit element type targeting.

        Array and Span need explicit conversions in their initializer lists.
        Dynamic containers (list, etc.) handle implicit conversions.
        """
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            return td.needs_explicit_element_target
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
        mapped = tuple(fn(t) for t in inner)
        # Identity check: NominalType has compare=False fields (_module_qname,
        # is_dynamic_protocol) that == would miss. Use `is` to be safe.
        if all(new is old for new, old in zip(mapped, inner)):
            return self
        return self.with_inner_types(mapped)


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

    def get_element_type(self) -> Optional['TpyType']:
        # LiteralType is a value-set refinement, not a container -- iteration
        # isn't meaningful for it. Preserve the historical "None" answer
        # regardless of what base_type would say.
        return None

    def is_str_base(self) -> bool:
        """True when this Literal is over string values."""
        from .type_def_registry import is_str_type
        return is_str_type(self.base_type)

    def is_int_base(self) -> bool:
        """True when this Literal is over integer values.

        base_type is always a resolved concrete int type (fixed-width int or
        BigInt), never IntLiteralType -- so is_integer_type suffices.
        """
        return is_integer_type(self.base_type)

    def is_bool_base(self) -> bool:
        """True when this Literal is over bool values."""
        from .type_def_registry import is_bool_type
        return is_bool_type(self.base_type)

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
    bound: Optional['NominalType'] = field(default=None, compare=False, hash=False)
    kind: TypeParamKind = TypeParamKind.TYPE

    def to_cpp(self) -> str:
        return self.name  # Template parameter name (works for both TYPE and INT)

    def __str__(self) -> str:
        return self.name

    def is_value_type(self) -> bool:
        if self.kind == TypeParamKind.INT:
            # INT type params are std::size_t values
            return True
        if self.bound is not None and isinstance(self.bound, NominalType) and self.bound.qualified_name() == "tpy.ValueType":
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
class NominalType(TpyType):
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
    # Nominal identity = qualified name + type args.  Included in
    # equality and hash so `pkg_a.Foo` vs `pkg_b.Foo` (or `Foo` bare
    # vs `mod.Foo`) never collapse via set/dict dedup.  Do NOT use
    # `==` to bridge a parser placeholder with its resolved
    # counterpart -- call `same_nominal_symbol_loose(a, b)` for that
    # axis instead (see module-level helper below).
    _module_qname: str | None = field(default=None)
    is_dynamic_protocol: bool = field(default=False, compare=False, hash=False)

    @property
    def is_record(self) -> bool:
        """Return True if this is a record type (not a protocol)."""
        return not self.is_protocol

    @property
    def is_user_record(self) -> bool:
        """Return True if this is a user-defined record (not a builtin stub,
        not a protocol, not an unresolved parser placeholder).

        Consults the TypeDef registry (populated by sema's register_record for
        every user-declared class); placeholders with no `_module_qname` yield
        no registry entry and return False. The previous definition tested
        `not _module_qname` directly -- the Post-Phase-D invariant #1 anti-
        pattern of treating missing qname as a semantic shortcut.
        """
        if self.is_protocol:
            return False
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        return td is not None and td.record is not None and td.record.builtin_type_key is None

    def with_protocol_flag(self, is_protocol: bool) -> 'NominalType':
        """Return a copy with is_protocol set."""
        if self.is_protocol == is_protocol:
            return self
        return NominalType(self.name, self.type_args, is_protocol, self._module_qname,
                         self.is_dynamic_protocol)

    def to_cpp_base_name(self) -> str:
        """Return the C++ name without type arguments."""
        return _native_cpp_names.get(self.name, self.name)

    def to_cpp(self) -> str:
        if self.is_protocol and not self.is_dynamic_protocol:
            # Structural protocol: template parameter placeholder
            return "T"
        # Per-qname cpp formatter (registry overrides the default {name}<{args}>
        # rendering for builtins whose C++ name diverges from the Python qname,
        # e.g. dict_keys -> ::tpy::dict_keys_view).
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.cpp_formatter is not None:
            return td.cpp_formatter(self.type_args)
        if td is not None and td.is_compile_time_only:
            raise TypeError(f"{self.name} is compile-time only and has no C++ representation")
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
        # Builtin qnames whose TypeDef declares value-ness (Span, dict views,
        # iterator adapters, primitives, ...) answer directly.
        # Record-category TypeDefs fall through to the RecordInfo.is_value_type
        # flag, which sema sets when the record implements the ValueType
        # marker protocol.
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None:
            if td.is_value_type:
                return True
            if td.record is not None and td.record.is_value_type:
                return True
        return False

    def is_send(self) -> bool:
        # TypeDef override (builtin qname behavior) takes precedence over
        # record-level registration -- e.g. list's native stub has no
        # disqualifying fields and would be registered as Send, but the
        # TypeDef says list[T] is Send only when T is.
        from tpyc.type_def_registry import type_def_of, resolve_send_sync
        td = type_def_of(self)
        if td is not None:
            resolved = resolve_send_sync(td.is_send, self.type_args)
            if resolved is not None:
                return resolved
            if td.record is not None and td.record.is_send:
                return True
        return self.is_value_type()

    def is_sync(self) -> bool:
        from tpyc.type_def_registry import type_def_of, resolve_send_sync
        td = type_def_of(self)
        if td is not None:
            resolved = resolve_send_sync(td.is_sync, self.type_args)
            if resolved is not None:
                return resolved
            if td.record is not None and td.record.is_sync:
                return True
        return self.is_value_type()

    def get_element_type(self) -> Optional['TpyType']:
        # Per-qname override (e.g. SpanIter[readonly[T]] iterates T, not
        # readonly[T] -- the readonly wrapper is stripped from the element).
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.element_of is not None:
            return td.element_of(self.type_args)
        # Container categories default to "first type_arg is the element".
        # Pre-migration this branch gated on `self._module_qname`; that was
        # the Post-Phase-D invariant #1 anti-pattern (qname-as-shortcut).
        # Now that user records carry _module_qname too, dispatch on category
        # so Tagged[T] (RECORD) doesn't claim its first type_arg as element.
        if td is not None and td.category in _ELEMENT_FROM_FIRST_ARG_CATEGORIES:
            for arg in self.type_args:
                if isinstance(arg, TpyType):
                    return arg
        return None

    def inner_types(self) -> tuple['TpyType', ...]:
        # Only return actual types; skip integer values and integer-kind
        # TypeParamRefs (e.g. N in Array[T, N: int]) -- those are not
        # traversable element types.
        return tuple(
            t for t in self.type_args
            if isinstance(t, TpyType)
            and not (isinstance(t, TypeParamRef) and t.kind == TypeParamKind.INT)
        )

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        # Reconstruct type_args preserving non-traversable positions (ints
        # and integer-kind TypeParamRefs -- see inner_types).
        new_args: list[TpyType | int] = []
        type_iter = iter(types)
        for arg in self.type_args:
            if isinstance(arg, TpyType) and not (
                    isinstance(arg, TypeParamRef) and arg.kind == TypeParamKind.INT):
                new_args.append(next(type_iter))
            else:
                new_args.append(arg)
        return NominalType(self.name, tuple(new_args), self.is_protocol, self._module_qname,
                         self.is_dynamic_protocol)


def same_nominal_symbol_loose(a: 'TpyType', b: 'TpyType') -> bool:
    """Transition-era equivalence for NominalTypes where one side may
    be a parser placeholder without `_module_qname` set.

    Returns True when both are NominalTypes with matching `name`,
    `type_args`, and `is_protocol`, and their qnames are either equal
    or at least one is `None`.  DO NOT use this as a drop-in for `==`
    -- strict `__eq__` remains the correct comparison for resolved
    types in sets/dicts/unions.  This helper exists only for the
    narrow set of sites that compare across the parse/resolve
    boundary -- currently `TypeRegistry.is_subclass_of`'s parent-
    chain walk, where a recorded parent reference (minted pre-qname)
    may need to match a qname-bearing argument.  Keep the call-site
    list short; each addition should be justified at review time.
    """
    if not (isinstance(a, NominalType) and isinstance(b, NominalType)):
        return False
    if a.name != b.name or a.type_args != b.type_args or a.is_protocol != b.is_protocol:
        return False
    if a._module_qname is None or b._module_qname is None:
        return True
    return a._module_qname == b._module_qname


@dataclass(frozen=True)
class SelfType(TpyType):
    """Self type for method signatures.

    In protocol method signatures, Self represents the implementing type.
    When checking if Int32 conforms to a protocol with Self, Self is
    substituted with Int32.

    In record method signatures, Self represents the record's own type
    (e.g. Self in class Foo -> NominalType("Foo")). Substituted at
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


# --- Type-kind predicate helpers (None-safe) ---
# Category + qname predicates go through the TypeDef registry so they keep
# working after primitive subclasses are replaced with NominalType instances.
# Structural types (None, Void, Callable, Union, Optional) use isinstance on
# the dedicated subclass.
#
# PERF TODO: the compound predicates below (is_integer_type, is_numeric_type,
# is_primitive_type, is_any_str_type, is_constexpr_eligible, ...) each chain
# 2-4 type_def_of dict lookups per call. The previous frozenset-tag form was
# one attribute read + one set lookup. Mitigation when profiling warrants:
# cache TypeDef.category on NominalType at construction (single attribute
# access), restoring the O(1) lookup cost. Orthogonal: route the coercions.py
# linear scan through a qname-indexed hash table (see resolve_coercion).


def is_any_str_type(typ: 'TpyType') -> bool:
    """Check if a type is any string type (str, String, StrView, PendingStr, Literal[str]).

    Includes LiteralType with str base (the Literal["r", "w"] annotation type).
    Single-value LiteralType instances from enrichment never reach storage/codegen.
    Excludes FStr (compile-time only, shares TypeCategory.STR with the runtime
    str family in the registry).
    """
    from .type_def_registry import is_str_category, is_fstr_type
    if is_fstr_type(typ):
        return False
    if is_str_category(typ) or isinstance(typ, PendingStrType):
        return True
    # Cold path: Literal[str] annotations.
    return isinstance(typ, LiteralType) and typ.is_str_base()


def is_integer_type(t: 'TpyType | None') -> bool:
    """True for concrete integer types (FixedInt, BigInt). Excludes literals and bool."""
    if t is None:
        return False
    from .type_def_registry import is_fixed_int_type, is_big_int_type
    return is_fixed_int_type(t) or is_big_int_type(t)


def is_any_int_type(t: 'TpyType | None') -> bool:
    """True for any integer-family type including IntLiteralType (unresolved literals)."""
    return is_integer_type(t) or isinstance(t, IntLiteralType)


def is_float_type(t: 'TpyType | None') -> bool:
    """True for concrete float types (float, Float32). Excludes FloatLiteralType."""
    if t is None:
        return False
    from .type_def_registry import is_float_category
    return is_float_category(t)


def is_any_float_type(t: 'TpyType | None') -> bool:
    """True for any float-family type including FloatLiteralType (unresolved literals)."""
    return is_float_type(t) or isinstance(t, FloatLiteralType)


def is_numeric_type(t: 'TpyType | None') -> bool:
    """True for types that participate in arithmetic: integers + floats + bool.
    Follows Python/C++ convention where bool is an integral numeric. Excludes literals."""
    if t is None:
        return False
    from .type_def_registry import is_bool_type
    return is_integer_type(t) or is_float_type(t) or is_bool_type(t)


def is_primitive_type(t: 'TpyType | None') -> bool:
    """True for fundamental C++ scalar types: Bool, Char, FixedInt, Float, Float32.
    Register-sized, trivially copyable, no heap. Excludes BigInt (heap) and StrView (internal pointer)."""
    if t is None:
        return False
    from .type_def_registry import is_fixed_int_type, is_bool_type, is_char_type
    return is_fixed_int_type(t) or is_float_type(t) or is_bool_type(t) or is_char_type(t)


def is_void_like_type(t: 'TpyType | None') -> bool:
    """True for NoneType or VoidType (Python None and C++ void return)."""
    return isinstance(t, (NoneType, VoidType))


def is_callable_type(t: 'TpyType | None') -> bool:
    """True for callable function-type aliases (CallableType)."""
    return isinstance(t, CallableType)


def is_union_or_optional_type(t: 'TpyType | None') -> bool:
    """True for UnionType or OptionalType (both carry a 'maybe-None' shape)."""
    return isinstance(t, (UnionType, OptionalType))


def is_any_bytes_type(typ: 'TpyType') -> bool:
    """Check if a type is any bytes type (bytes, bytearray, BytesView, PendingBytes)."""
    from .type_def_registry import is_bytes_category
    return is_bytes_category(typ) or isinstance(typ, PendingBytesType)


def is_constexpr_eligible(typ: 'TpyType') -> bool:
    """Check if a type can use C++ constexpr (vs runtime const).

    Constexpr-eligible: fixed-width integers, float, bool, char, str, StrView.
    Non-constexpr (needs const): BigInt (non-trivial constructor), String (std::string).
    Note: str uses std::string_view for Final[str] (overridden in codegen).
    """
    from .type_def_registry import is_str_type, is_str_view_type
    return is_primitive_type(typ) or is_str_type(typ) or is_str_view_type(typ)


def final_type_str_to_strview(t: 'TpyType') -> 'TpyType':
    """Optimize str to StrView in Final type annotations.

    String literals have static lifetime, so Final globals can use
    string_view instead of std::string. Applies to scalar str and
    recurses into tuple element types.
    """
    from .type_def_registry import is_str_type
    if is_str_type(t):
        return STRVIEW
    if isinstance(t, TupleType):
        new_elems = tuple(final_type_str_to_strview(et) for et in t.element_types)
        if new_elems == t.element_types:
            return t
        return TupleType(new_elems)
    return t


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


def attach_type_param_bounds(t: TpyType, bounds: dict[str, 'NominalType']) -> TpyType:
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
            if t.bound is None or (isinstance(t.bound, NominalType) and not t.bound.is_protocol
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
        from .type_def_registry import is_str_type
        if is_str_type(self.inner):
            return "std::optional<std::string_view>"
        return self.to_cpp()

    def to_cpp_param(self, name: str) -> str:
        from .type_def_registry import is_str_type
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}* {name}"
        if is_str_type(self.inner):
            return f"std::optional<std::string_view> {name}"
        return f"{self.to_cpp()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        from .type_def_registry import is_str_type
        if self.uses_pointer_repr():
            return f"const {self.inner.to_cpp()}* {name}"
        if is_str_type(self.inner):
            return f"std::optional<std::string_view> {name}"
        return f"{self.to_cpp()} {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        from .type_def_registry import is_str_type
        return is_str_type(self.inner)

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
    NoneType is always first if present (maps to std::monostate).
    """
    members: tuple[TpyType, ...]

    def to_cpp(self) -> str:
        alias = _union_alias_names.get(self.members)
        if alias is not None:
            return alias
        cpp_members = [
            "std::monostate" if is_void_like_type(m) else m.to_cpp()
            for m in self.members
        ]
        return f"std::variant<{', '.join(cpp_members)}>"

    def has_none_member(self) -> bool:
        return any(is_void_like_type(m) for m in self.members)

    def is_value_type(self) -> bool:
        # Note: recursive union aliases (type Tree = int | list[Tree]) have
        # non-value members but their C++ wrapper struct IS a value type.
        # Codegen handles this via ctx.is_recursive_union() overrides.
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
            "std::monostate" if is_void_like_type(m)
            else f"{m.to_cpp()}*"
            for m in self.members
        ]
        return f"std::variant<{', '.join(cpp_members)}>"

    def to_cpp_const_ptr_variant(self) -> str:
        """Return the const pointer-variant type: std::variant<const Dog*, const Cat*>."""
        cpp_members = [
            "std::monostate" if is_void_like_type(m)
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
            "None" if is_void_like_type(m) else str(m)
            for m in self.members
        ]
        return " | ".join(parts)

    def inner_types(self) -> tuple['TpyType', ...]:
        return self.members

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return make_union(*types)


def _contains_self_reference(typ: 'TpyType', name: str) -> bool:
    """Check if a type tree contains a NominalType self-reference to the given name.

    Walks an alias body where self-references are bare parser placeholders
    (no _module_qname). Matches on name + non-protocol shape; excludes
    already-resolved types which carry a qname.
    """
    if (isinstance(typ, NominalType) and typ.name == name
            and not typ.is_protocol and not typ._module_qname):
        return True
    return any(_contains_self_reference(inner, name) for inner in typ.inner_types())


def validate_recursive_union_paths(
    alias_name: str, members: tuple['TpyType', ...],
) -> str | None:
    """Validate that all self-references go through indirecting containers.

    Returns an error message if validation fails, None if OK.
    Indirecting types: list, dict, set, Optional, Ptr, Own, Box.
    Fixed-size types (tuple, Array) do NOT provide indirection.
    """

    def _check(typ: 'TpyType', inside_indirection: bool) -> str | None:
        if isinstance(typ, NominalType) and typ.name == alias_name and not typ.is_protocol:
            if not inside_indirection:
                return (
                    f"direct recursion in type alias '{alias_name}' -- "
                    f"every recursive path must go through a container "
                    f"(list, dict, set, Optional, Box, Ptr)"
                )
            return None

        # These types are defined later in this file, so use lazy string checks
        # for types that are NominalType subclasses. The isinstance checks work
        # because Python resolves the class at call time, not definition time.
        type_name = type(typ).__name__
        is_indirecting = type_name in ('OptionalType', 'PtrType')
        # set / dict / Box are plain NominalTypes, detected by name
        if isinstance(typ, NominalType) and typ.name in ("Box", "set", "dict", "list"):
            is_indirecting = True

        new_indirection = inside_indirection or is_indirecting
        for inner in typ.inner_types():
            err = _check(inner, new_indirection)
            if err is not None:
                return err
        return None

    for m in members:
        err = _check(m, False)
        if err is not None:
            return err
    return None


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
        args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
        return f"std::tuple<{args}>"

    def to_cpp_return_const(self) -> str:
        args = ", ".join(self._element_to_cpp_param(t, const=True) for t in self.element_types)
        return f"std::tuple<{args}>"

    def has_ref_elements(self) -> bool:
        return any(
            not et.is_value_type() and not isinstance(et, (OwnType, TypeParamRef))
            for et in self.element_types
        )

    def _element_to_cpp_param(self, t: 'TpyType', const: bool) -> str:
        """C++ type for a tuple element in param context.

        For Optional elements, uses to_cpp() (std::optional<T>) instead of
        to_cpp_return (T*) so the param type matches the field storage type.
        Tuples are passed as const&, so pointer repr inside makes no sense.
        """
        unwrapped = t.wrapped if isinstance(t, RefType) else t
        if isinstance(unwrapped, OptionalType) and unwrapped.uses_pointer_repr():
            # Use std::optional<T> (field repr) not T* (param/return repr)
            ref_prefix = "const " if const and isinstance(t, RefType) else ""
            suffix = "&" if isinstance(t, RefType) else ""
            return f"{ref_prefix}{unwrapped.to_cpp()}{suffix}"
        return t.to_cpp_return_const() if const else t.to_cpp_return()

    def to_cpp_param_type(self) -> str:
        args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
        return f"const std::tuple<{args}>&"

    def to_cpp_param(self, name: str) -> str:
        args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
        return f"const std::tuple<{args}>& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        args = ", ".join(self._element_to_cpp_param(t, const=True) for t in self.element_types)
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
    FloatLiteralType always resolves to FLOAT (float64).
    Handles TupleType at arbitrary nesting depth.
    """
    def _resolve(t: TpyType) -> TpyType:
        if isinstance(t, IntLiteralType):
            return resolver(t) if callable(resolver) else resolver
        if isinstance(t, FloatLiteralType):
            return FLOAT
        if isinstance(t, TupleType):
            return t.map_inner_types(_resolve)
        from tpyc.type_def_registry import is_array as _is_array, is_span as _is_span, is_list as _is_list_ctr
        if _is_array(t) and isinstance(t.type_args[0], (IntLiteralType, FloatLiteralType)):
            elem = _resolve(t.type_args[0])
            return make_array(elem, t.type_args[1])
        from tpyc.type_def_registry import is_list as _is_list
        if _is_list(t) and isinstance(t.type_args[0], (IntLiteralType, FloatLiteralType)):
            elem = _resolve(t.type_args[0])
            return make_list(elem)
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
                if is_void_like_type(m):
                    has_none = True
                else:
                    flat.append(m)
        elif isinstance(t, OptionalType):
            has_none = True
            flat.append(t.inner)
        elif is_void_like_type(t):
            has_none = True
        else:
            flat.append(t)

    # Deduplicate preserving order.  `NominalType` equality includes
    # `_module_qname`, so two records with the same short name from
    # different modules (e.g. `pkg_a.Foo` vs `pkg_b.Foo`) are distinct
    # and survive the set-dedup.
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
    non_none = [m for m in union.members if not is_void_like_type(m)]
    return make_union(*non_none), NoneType()


def make_array(element_type: 'TpyType', size: 'int | TypeParamRef') -> 'NominalType':
    """Factory for Array[T, N]. Plain NominalType with qname tpy.Array;
    behavior (is_send/is_sync propagate from element, subscript_borrows,
    needs_explicit_element_target, cpp_formatter -> std::array<T, N>)
    comes from the TypeDef registry."""
    return NominalType(name="Array", type_args=(element_type, size),
                       _module_qname="tpy.Array")


def make_span(element_type: 'TpyType', is_readonly: bool = False) -> 'NominalType':
    """Factory for Span[T] / Span[readonly[T]]. Plain NominalType with qname
    tpy.Span; behavior (is_value_type=True, is_send=False, is_sync depends on
    readonly, cpp_formatter handling const) comes from the TypeDef registry."""
    if is_readonly and not isinstance(element_type, ReadonlyType):
        element_type = ReadonlyType(element_type)
    return NominalType(name="Span", type_args=(element_type,),
                       _module_qname="tpy.Span")


def span_is_readonly(t: 'TpyType') -> bool:
    """True if t is a Span whose element is wrapped in ReadonlyType."""
    from tpyc.type_def_registry import is_span
    return is_span(t) and isinstance(t.type_args[0], ReadonlyType)


def span_inner_element(t: 'TpyType') -> 'TpyType':
    """Unwrapped element type of a Span (strips ReadonlyType). Caller must
    have confirmed `is_span(t)`."""
    return unwrap_readonly(t.type_args[0])


def span_as_const(t: 'TpyType') -> 'NominalType':
    """Return a const variant (Span[readonly[T]]) of a Span. No-op if already readonly."""
    if span_is_readonly(t):
        return t
    return make_span(ReadonlyType(t.type_args[0]))


def span_as_mutable(t: 'TpyType') -> 'NominalType':
    """Return a mutable (Span[T]) variant. No-op if already mutable."""
    if not span_is_readonly(t):
        return t
    return make_span(span_inner_element(t))


def make_span_iter(element_type: 'TpyType') -> 'NominalType':
    """Factory for SpanIter[T]. Produces a plain NominalType with qname
    `tpy.SpanIter`; behavior (is_value_type, is_send=False, is_sync=False,
    const-aware cpp_formatter) comes from the TypeDef registry."""
    return NominalType(name="SpanIter", type_args=(element_type,),
                       _module_qname="tpy.SpanIter")


def make_copy_iter(element_type: 'TpyType') -> 'NominalType':
    return NominalType(name="CopyIter", type_args=(element_type,),
                       _module_qname="tpy.CopyIter")


def make_own_iter(element_type: 'TpyType') -> 'NominalType':
    return NominalType(name="OwnIter", type_args=(element_type,),
                       _module_qname="tpy.OwnIter")


def make_range(element_type: 'TpyType') -> 'NominalType':
    """Factory for Range[T]. Plain NominalType with qname builtins.Range;
    behavior (is_value_type=True, cpp_formatter -> ::tpy::Range<T>) comes
    from the TypeDef registry."""
    return NominalType(name="Range", type_args=(element_type,),
                       _module_qname="builtins.Range")


def is_readonly_span(typ: 'TpyType') -> bool:
    """Check if a type is a read-only span (Span[readonly[T]])."""
    return span_is_readonly(typ)


def make_list(element_type: 'TpyType') -> 'NominalType':
    """Factory for list[T]. Plain NominalType with qname builtins.list;
    behavior (is_send from element, is_sync=False, subscript_borrows=True,
    cpp_formatter -> std::vector<T>) comes from the TypeDef registry."""
    return NominalType(name="list", type_args=(element_type,),
                       _module_qname="builtins.list")


def make_dict(key_type: 'TpyType', value_type: 'TpyType') -> 'NominalType':
    """Factory for dict[K, V]. Plain NominalType with qname builtins.dict;
    behavior (is_send, is_sync=False, subscript_borrows=True, element_of=V,
    cpp_formatter -> ::tpy::ordered_map<K, V>) comes from the registry."""
    return NominalType(name="dict", type_args=(key_type, value_type),
                       _module_qname="builtins.dict")


def make_set(element_type: 'TpyType') -> 'NominalType':
    """Factory for set[T]. Produces a plain NominalType with qname
    `builtins.set`; behavior (is_send=element.is_send(), is_sync=False,
    cpp_formatter -> ::tpy::ordered_set<T>) comes from the TypeDef registry."""
    return NominalType(name="set", type_args=(element_type,),
                       _module_qname="builtins.set")


def make_dict_keys_view(key_type: 'TpyType', value_type: 'TpyType') -> 'NominalType':
    """Factory for `dict.keys()` view type. Produces a plain NominalType
    with qname `builtins.dict_keys`; behavior comes from the TypeDef
    registry (is_value_type=True, is_send/is_sync=False, cpp_formatter
    -> ::tpy::dict_keys_view<K, V>)."""
    return NominalType(name="dict_keys", type_args=(key_type, value_type),
                       _module_qname="builtins.dict_keys")


def make_dict_values_view(key_type: 'TpyType', value_type: 'TpyType') -> 'NominalType':
    return NominalType(name="dict_values", type_args=(key_type, value_type),
                       _module_qname="builtins.dict_values")


def make_dict_items_view(key_type: 'TpyType', value_type: 'TpyType') -> 'NominalType':
    return NominalType(name="dict_items", type_args=(key_type, value_type),
                       _module_qname="builtins.dict_items")


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

    def to_cpp(self) -> str:
        return "auto"

    def __str__(self) -> str:
        return f"<genexpr>[{self.element_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


@dataclass(frozen=True)
class CallableType(TpyType):
    """Callable[[ParamType, ...], ReturnType] or Fn[[ParamType, ...], ReturnType].

    Two modes distinguished by `is_template`:
    - is_template=False (Callable): type-erased via std::function; valid in
      all positions (params, fields, returns, containers, locals).
    - is_template=True (Fn): zero-cost template parameter with a requires
      clause. Valid only in function parameter position; to_cpp is an error
      because Fn params are rendered as template parameters, not concrete
      types.
    """
    param_types: tuple[TpyType, ...]
    return_type: TpyType
    is_template: bool = False

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
        if self.is_template:
            raise RuntimeError(
                "CallableType(is_template=True).to_cpp() should not be called "
                "directly; Fn params use template codegen"
            )
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
        return CallableType(types[:-1], types[-1], self.is_template)

    def __str__(self) -> str:
        params = ", ".join(str(t) for t in self.param_types)
        name = "Fn" if self.is_template else "Callable"
        return f"{name}[[{params}], {self.return_type}]"

    def qualified_name(self) -> Optional[str]:
        return None


def make_fn_type(param_types: tuple['TpyType', ...], return_type: 'TpyType') -> CallableType:
    """Factory for Fn[[...], R] -- callable template (zero-cost parameter position)."""
    return CallableType(param_types, return_type, is_template=True)


def is_fn_type(t: 'TpyType') -> bool:
    """True if t is a template-mode CallableType (Fn[[...], R])."""
    return isinstance(t, CallableType) and t.is_template


def contains_fn_type(typ: TpyType) -> bool:
    """Check if a type contains a template-mode Callable (Fn) anywhere in its structure."""
    if isinstance(typ, CallableType) and typ.is_template:
        return True
    return any(contains_fn_type(inner) for inner in typ.inner_types())


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
    promote_param_match: Callable[['TpyType'], bool]  # predicate: matches String/ByteArray
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
INT8 = NominalType("Int8", (), _module_qname="tpy.Int8")
INT16 = NominalType("Int16", (), _module_qname="tpy.Int16")
INT32 = NominalType("Int32", (), _module_qname="tpy.Int32")
INT64 = NominalType("Int64", (), _module_qname="tpy.Int64")
UINT8 = NominalType("UInt8", (), _module_qname="tpy.UInt8")
UINT16 = NominalType("UInt16", (), _module_qname="tpy.UInt16")
UINT32 = NominalType("UInt32", (), _module_qname="tpy.UInt32")
UINT64 = NominalType("UInt64", (), _module_qname="tpy.UInt64")
ALL_FIXED_INTS = [INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64]

VOID = VoidType()
STR = NominalType("str", (), _module_qname="builtins.str")
STRING = NominalType("String", (), _module_qname="tpy.String")
STRVIEW = NominalType("StrView", (), _module_qname="tpy.StrView")
FSTR = NominalType("FStr", (), _module_qname="tpy.FStr")
CHAR = NominalType("Char", (), _module_qname="tpy.Char")
BYTES = NominalType("bytes", (), _module_qname="builtins.bytes")
BYTEARRAY = NominalType("bytearray", (), _module_qname="builtins.bytearray")
BYTESVIEW = NominalType("BytesView", (), _module_qname="tpy.BytesView")
BOOL = NominalType("bool", (), _module_qname="builtins.bool")
FLOAT = NominalType("float", (), _module_qname="builtins.float")
FLOAT32 = NominalType("Float32", (), _module_qname="tpy.Float32")

BIGINT = NominalType("int", (), _module_qname="builtins.int")
NONE = NoneType()
BASIC_SLICE = NominalType("basic_slice", (), _module_qname="tpy.basic_slice")
SLICE = NominalType("slice", (), _module_qname="builtins.slice")


# Categories whose "element type" is the first type argument by default
# (consulted by NominalType.get_element_type when no explicit element_of
# override is set on the TypeDef). Anything not in this set returns None --
# user records, protocols, enums, primitives, etc.
from tpyc.type_def_registry import TypeCategory as _TypeCategory
_ELEMENT_FROM_FIRST_ARG_CATEGORIES = frozenset({
    _TypeCategory.LIST,
    _TypeCategory.DICT,
    _TypeCategory.DICT_VIEW,
    _TypeCategory.SET,
    _TypeCategory.ARRAY,
    _TypeCategory.SPAN,
    _TypeCategory.ITERATOR,
    _TypeCategory.RANGE,
})

# View-type family descriptors (must follow singleton definitions)
from .type_def_registry import is_string_type as _is_string_type, is_bytearray_type as _is_bytearray_type
STR_FAMILY = ViewTypeFamily(
    owned_type=STR, view_type=STRVIEW, promote_param_match=_is_string_type,
    pending_type_class=PendingStrType, element_type=CHAR,
    display_name="str", qualified="builtins.str",
)
BYTES_FAMILY = ViewTypeFamily(
    owned_type=BYTES, view_type=BYTESVIEW, promote_param_match=_is_bytearray_type,
    pending_type_class=PendingBytesType, element_type=UINT8,
    display_name="bytes", qualified="builtins.bytes",
)
VIEW_TYPE_FAMILIES = (STR_FAMILY, BYTES_FAMILY)

# Int32 range limits (for runtime-constant checks). Use int_traits_of(t) for
# other widths.
INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1


def is_protocol_type(typ: TpyType) -> bool:
    """Check if a type is a protocol type."""
    return isinstance(typ, NominalType) and typ.is_protocol


def is_protocol_union(typ: TpyType) -> bool:
    """Check if a type is a union where all non-None members are static protocols.

    Matches UnionType with 2+ protocol members (with or without None).
    Does NOT match OptionalType(protocol) -- that has its own codegen paths.
    """
    if not isinstance(typ, UnionType):
        return False
    non_none = [m for m in typ.members if not is_void_like_type(m)]
    return len(non_none) >= 2 and all(is_protocol_type(m) for m in non_none)


def protocol_union_protocols(typ: UnionType) -> list[TpyType]:
    """Get non-None protocol members from a protocol union."""
    return [m for m in typ.members if not is_void_like_type(m)]


def protocol_union_has_none(typ: UnionType) -> bool:
    """Check if a protocol union includes None."""
    return any(is_void_like_type(m) for m in typ.members)


def container_to_str_template(typ: TpyType) -> str | None:
    """Return the C++ to_str template for a container type, or None."""
    if isinstance(typ, TupleType):
        return "::tpy::tuple_to_str({0})"
    from tpyc.type_def_registry import is_array as _is_array, is_span as _is_span, is_list as _is_list_ctr
    if _is_array(typ) or _is_list_ctr(typ) or _is_span(typ):
        return "::tpy::list_to_str({0})"
    from tpyc.type_def_registry import is_set, is_dict
    if is_dict(typ):
        return "::tpy::dict_to_str({0})"
    if is_set(typ):
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
    type_param_bounds: dict[str, 'NominalType'] = field(default_factory=dict)  # {"T": Comparable} (must be protocols)
    type_factory: "Optional[Callable[..., TpyType]]" = None  # Factory to create concrete type from params
    parent: Optional['TpyType'] = None  # Parent type (NominalType or builtin TpyType)
    implemented_protocols: list['NominalType'] = field(default_factory=list)  # Explicit protocol implementations
    extends_protocols: list[str] = field(default_factory=list)  # Protocol extensions: ["NativeIterable[T]"]
    native_name: Optional[str] = None  # C++ name for @native/@native_c records (e.g., "SDL_Rect")
    is_native: bool = False       # True for @native or @native_c records
    is_native_c: bool = False     # True for @native_c specifically
    is_nocopy: bool = False       # True for @nocopy records (copy deleted, move-only)
    match_args: tuple[str, ...] | None = None  # Positional match arg names (set by macro, mirrors __match_args__)
    is_frozen: bool = False       # True for @dataclass(frozen=True) (field mutation rejected)
    is_typed_dict: bool = False   # True for TypedDict (struct with string-literal subscript)
    is_total_false: bool = False  # True for TypedDict(total=False) -- all fields Optional, absent by default
    is_value_type: bool = False   # True for ValueType marker protocol
    is_send: bool = False         # True if record is Send (all fields Send + parent Send); derived by sema
    is_sync: bool = False         # True if record is Sync (all fields Sync + parent Sync); derived by sema
    has_del: bool = False           # True if class declares __del__ (needs drop flag)
    has_copy: bool = False          # True if class defines __copy__ (custom copy semantics)
    builtin_type_key: str | None = None  # e.g. "builtins.list" -- links .py class to type_factory
    module: str | None = None  # Public module name (collapses private submodules via public_module_name); used for qualified_name() and codegen C++ namespace
    defining_module: str | None = None  # Raw (uncollapsed) module where the class was declared; used by re-export logic to look up the record through ModuleInfo.records

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

    def qualified_name(self) -> str:
        """Stable qname for this record, usable as the key for type_def_of / TypeDef lookup."""
        if self.builtin_type_key:
            return self.builtin_type_key
        return f"{self.module or '__main__'}.{self.name}"


class FunctionLinkage(Enum):
    """Linkage mode for functions."""
    DEFAULT = "default"
    NATIVE = "native"        # C++ import (stub, no body)
    NATIVE_C = "native_c"    # C import (stub, no body)
    EXPORT_C = "export_c"    # C export (has body)


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
    keyword_only: bool = False  # True for params after * separator
    is_variadic: bool = False  # True for *args param (type is Span[readonly[T]])

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
    param_map: dict[int, int]  # callee_param_idx -> caller_param_idx; caller -1 = self passed as arg
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
    # Always carries a resolved TpyType at runtime.  The `Optional`
    # annotation is a type-checker concession for the parse -> sema
    # window where `TpyFunction.return_type` briefly holds `None`
    # (parser's "no annotation" sentinel).  All FunctionInfo
    # construction sites feed an already-resolved TpyType, so readers
    # may treat `return_type` as non-None.
    return_type: Optional[TpyType]
    is_noalloc: bool = False
    is_readonly: bool = False
    is_pure: bool = False
    is_inline: bool = False
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
    type_param_bounds: dict[str, 'NominalType'] = field(default_factory=dict)
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "tpy.extern.DefaultInt"}
    cpp_template: Optional[str] = None  # For builtins: "{self}.push_back({0})"
    value_ptr_coercion: bool = False  # @value_ptr_coercion: Ptr[T] params accept T values
    is_builtin_function: bool = False  # True for global builtins (len, chr, etc.)
    special_handling: bool = False  # True if sema/codegen handle specially
    error_return_type: Optional[str] = None  # @error_return(E) exception type name
    builtin_decorator_key: Optional[str] = None  # e.g. "tpy.readonly" -- links .py function to decorator semantics
    qualified_name: str = ""  # Full dotted path, e.g. "builtins.print", "tpy.copy", "__main__.foo"
    # For methods: qname of the enclosing record, e.g. "builtins.float".
    # Populated by record-method registration (sema/registration.py) and
    # preserved across dataclass_replace. NOT uniformly set on ad-hoc
    # FunctionInfo constructions in sema/calls.py and sema/methods.py
    # (virtual calls, partial application, callable fields, protocol method
    # bindings, etc.). Readers using this for qname-based dispatch should
    # tolerate None (treat as "unknown / no qname-based match").
    owning_type_qname: str | None = None
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
    kwarg_name: Optional[str] = None  # name of **kwargs param (TypedDict type)
    # FStr inlining: body expression to inline at call sites.
    # Set during method body analysis for methods with FStr params.
    inline_body: Optional[Any] = None  # TpyExpr: body expression for @inline functions

    @property
    def has_fstr_param(self) -> bool:
        """True if any parameter has FStr type."""
        from .type_def_registry import is_fstr_type
        return any(is_fstr_type(p.type) for p in self.params)

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
        return self.linkage == FunctionLinkage.EXPORT_C

    @property
    def is_export(self) -> bool:
        return self.linkage == FunctionLinkage.EXPORT_C

    @property
    def is_native_c(self) -> bool:
        return self.linkage == FunctionLinkage.NATIVE_C

    @property
    def is_native(self) -> bool:
        return self.linkage == FunctionLinkage.NATIVE

    @property
    def extern_name(self) -> Optional[str]:
        """Backward compat alias for native_name."""
        return self.native_name

    def is_generic(self) -> bool:
        """Return True if this is a generic function with type parameters."""
        return bool(self.type_params)

    @property
    def has_variadic(self) -> bool:
        """True if this function has a *args parameter."""
        return any(p.is_variadic for p in self.params)

    @property
    def has_keyword_only(self) -> bool:
        """True if this function has keyword-only parameters."""
        return any(p.keyword_only for p in self.params)

    @property
    def min_args(self) -> int:
        """Minimum number of required positional arguments (excludes keyword-only, variadic, **kwargs)."""
        return sum(1 for p in self.params
                   if not p.has_default and not p.keyword_only and not p.is_variadic
                   and not (self.kwarg_name and p.name == self.kwarg_name))

    @property
    def max_args(self) -> int:
        """Maximum number of total arguments (all params). Unlimited if variadic.

        Includes **kwargs param since call-site packing fills it as a positional arg.
        """
        if self.has_variadic:
            return 2**31
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
    """Method signature required by a protocol.

    Between parse and sema's `_resolve_pending_type_refs` pre-pass,
    `params` and `return_type` may hold `TypeRefNode` in place of
    `TpyType`.  All readers post-pre-pass see TpyType.  `return_type`
    may also be None when no annotation was provided -- sema
    substitutes VOID during the pre-pass.
    """
    name: str
    params: list[tuple[str, 'TpyType | TypeRefNode']]
    return_type: 'TpyType | TypeRefNode | None'
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
    includes: list[tuple[str, str | None]] = field(default_factory=list)  # # tpy: include() directives (path, platform_filter)
    functions: dict[str, list[FunctionInfo]] = field(default_factory=dict)  # func_name -> overloads
    variables: dict[str, ModuleVarInfo] = field(default_factory=dict)  # var_name -> ModuleVarInfo
    records: dict[str, RecordInfo] = field(default_factory=dict)  # type_name -> RecordInfo (exported types)
    protocols: dict[str, ProtocolInfo] = field(default_factory=dict)  # protocol_name -> ProtocolInfo
    type_aliases: dict[str, 'TpyType'] = field(default_factory=dict)  # alias_name -> resolved type
    enums: dict[str, 'NominalType'] = field(default_factory=dict)  # enum_name -> NominalType (enum-kind)

    def has_export(self, name: str) -> bool:
        """Check if a name is exported by this module."""
        return (name in self.functions or name in self.records or
                name in self.protocols or name in self.enums or
                name in self.type_aliases or name in self.variables)


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self):
        self.records: dict[str, RecordInfo] = {}
        # Qname -> RecordInfo indexes, split so builtin consumers
        # (`get_builtin_record` and its callers) don't accidentally
        # match user records that happen to share a qname namespace.
        self._qname_index: dict[str, RecordInfo] = {}       # builtins only
        self._user_qname_index: dict[str, RecordInfo] = {}  # user records only
        self.functions: dict[str, list[FunctionInfo]] = {}  # User-defined functions (single or @overload group)
        self.protocols: dict[str, ProtocolInfo] = {}
        self.modules: dict[str, ModuleInfo] = {}  # module_name -> ModuleInfo
        self.type_aliases: dict[str, 'TpyType'] = {}  # alias_name -> resolved type
        # Source tracking for imported aliases: local_name -> (declaring_module, original_name).
        # Type alias bodies (UnionType, OptionalType, ...) have no `_module_qname` of
        # their own -- an alias is a pure name-binding in the module that declared it --
        # so provenance must be stored here. Populated via register_type_alias(imported_from=...).
        self.imported_type_alias_info: dict[str, tuple[str, str]] = {}
        self.enums: dict[str, 'NominalType'] = {}  # enum_name -> NominalType (enum-kind)
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
        else:
            # Index user records by fully-qualified name so cross-module
            # lookups (`get_record_for_type`, `is_subclass_of`) can
            # disambiguate shadowed short names.  Kept separate from
            # `_qname_index` so `get_builtin_record` consumers don't
            # accidentally match user records.
            qname = info.qualified_name()
            if qname:
                self._user_qname_index[qname] = info

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

    def register_enum(self, info: 'NominalType', name: str | None = None) -> None:
        """Register an enum type.

        Args:
            info: The enum NominalType to register. Behavior payload
                  (members, underlying type, is_int_enum) lives on the
                  per-qname TypeDef.enum entry populated alongside this
                  registration in sema/registration.py.
            name: Optional name to register under (defaults to info.name).
                  Used for imported enums that may have a local alias.
        """
        self.enums[name or info.name] = info

    def register_enum_placeholder(self, name: str, module: str) -> None:
        """Register a parse-time enum placeholder by name.

        Parser calls this to mark a name as an enum so same-file type-
        ref resolution can treat it as a known enum.  Sema re-registers
        later with the fully-populated NominalType + TypeDef.enum
        payload.  Keeping typesys value construction out of the parser
        preserves parser purity.

        `module` is the public module for the declaring file
        (`"__main__"` for the entry-point module, matching sema's
        `ctx.module_name` convention).  The placeholder is minted with
        `_module_qname = f"{module}.{name}"` so parser-side and sema-
        side registrations agree on the qname, letting the resolver
        mint authoritative `_module_qname` values on the first pass.
        """
        qname = f"{module}.{name}"
        self.enums[name] = NominalType(name=name, _module_qname=qname)

    def register_type_alias(self, name: str, typ: 'TpyType',
                            *, imported_from: tuple[str, str] | None = None) -> None:
        """Register a type alias (e.g., Shape = Circle | Rect).

        imported_from: (declaring_module, alias_name) if the alias was imported
            from another user module; None for locally-defined aliases.
        """
        self.type_aliases[name] = typ
        if imported_from is not None:
            self.imported_type_alias_info[name] = imported_from

    def get_type_alias(self, name: str) -> 'TpyType | None':
        """Get a type alias by name, or None if not found."""
        return self.type_aliases.get(name)

    def imported_record_qualification(
        self, name: str, current_module: str
    ) -> tuple[str, str] | None:
        """For a user-imported record: return (defining_module, canonical_name), else None.

        Reads `RecordInfo.defining_module` -- the actual (uncollapsed) submodule
        where the class was declared. Both codegen (which qualifies C++ namespace
        via `_namespace_map` / `cpp_namespace` directives) and compiler.py's
        re-export logic (which looks up `ModuleInfo.records[original_name]`)
        want this form. For regular user code `defining_module == module` (the
        public name); they differ only for private submodules like
        `tpy._builtins._bytes` where `module` collapses to `tpy` / `builtins`.
        """
        record_info = self.records.get(name)
        if record_info is None or record_info.defining_module is None:
            return None
        source_module = record_info.defining_module
        if source_module == current_module:
            return None
        module_info = self.modules.get(source_module)
        if module_info is not None and module_info.is_builtin:
            return None
        return (source_module, record_info.name)

    def imported_enum_qualification(
        self, name: str, current_module: str
    ) -> tuple[str, str] | None:
        """For a user-imported enum: return (declaring_module, canonical_name), else None.

        Reads `EnumInfo.module_name` (attached via `attach_dynamic_type_def` in
        sema/registration.py::register_enum) and the enum NominalType's `.name`
        field (which always holds the canonical declaration name, even when
        registered under a local alias). Entry-point `__main__` enums have
        EnumInfo.module_name == None and are correctly treated as local.
        """
        enum_type = self.enums.get(name)
        if enum_type is None:
            return None
        from .type_def_registry import enum_info_of
        einfo = enum_info_of(enum_type)
        if einfo is None or einfo.module_name is None:
            return None
        if einfo.module_name == current_module:
            return None
        module_info = self.modules.get(einfo.module_name)
        if module_info is not None and module_info.is_builtin:
            return None
        return (einfo.module_name, enum_type.name)

    def register_module(self, info: ModuleInfo) -> None:
        """Register a module by name and index its records' qnames so
        cross-module `get_record_for_type` / `is_subclass_of` lookups
        find them even when the user never imported them by short name
        (short-name lookup would otherwise pick a local shadow).
        """
        self.modules[info.name] = info
        for rec in info.records.values():
            if rec.builtin_type_key:
                self._qname_index.setdefault(rec.builtin_type_key, rec)
            else:
                qname = rec.qualified_name()
                if qname:
                    self._user_qname_index.setdefault(qname, rec)

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
        if record.parent is None or not isinstance(record.parent, NominalType):
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
        if not (isinstance(child, NominalType) and child.is_user_record
                and isinstance(parent, NominalType) and parent.is_user_record):
            return False
        current_info = self.get_record_for_type(child)
        visited: set[str] = set()
        while current_info and current_info.parent and current_info.name not in visited:
            visited.add(current_info.name)
            p = current_info.parent
            # Parent references recorded pre-qname-mint may be bare; the
            # `parent` argument may be qname-bearing.  `loose` matches
            # strictly when both have qnames (keeps cross-module
            # shadowing honest) and permissively when either is bare.
            if same_nominal_symbol_loose(p, parent):
                return True
            if isinstance(p, NominalType) and p.is_user_record:
                current_info = self.get_record_for_type(p)
            else:
                break
        return False

    def is_subclass_of_record(self, child: RecordInfo, parent: RecordInfo) -> bool:
        """Check if child record inherits from parent (by record identity)."""
        current: RecordInfo | None = child
        visited: set[str] = set()
        while current and current.parent and isinstance(current.parent, NominalType):
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

        Qname-first when the NominalType carries one, so cross-module
        references never collide with a locally-shadowed short name.
        Falls back to short-name lookup if the qname misses -- bare
        placeholder NominalTypes (no qname) and records registered only
        under a short alias still resolve.
        """
        if isinstance(tpy_type, NominalType) and tpy_type.is_record:
            qname = tpy_type._module_qname
            if qname:
                result = self._user_qname_index.get(qname) or self._qname_index.get(qname)
                if result is not None:
                    return result
            return self.records.get(tpy_type.name)
        qname = tpy_type.qualified_name()
        if qname:
            return self._qname_index.get(qname)
        return None

    def get_protocol(self, name: str) -> Optional[ProtocolInfo]:
        return self.protocols.get(name)

    def get_enum(self, name: str) -> Optional['NominalType']:
        return self.enums.get(name)

    def is_known_type(self, name: str) -> bool:
        """Check if a name refers to a known type."""
        if name in self._fundamental_types:
            return True
        if name in self.records or name in self.protocols or name in self.type_aliases or name in self.enums:
            return True
        # Check the TypeDef registry for builtin types defined in .py stubs.
        # `td.type_factory is not None` matches the old factory-table membership
        # check: only factory-registered qnames count as "known" here.
        from tpyc.type_def_registry import get_type_def
        for module_name in ("builtins", "tpy"):
            td = get_type_def(f"{module_name}.{name}")
            if td is not None and td.type_factory is not None:
                return True
        return False


