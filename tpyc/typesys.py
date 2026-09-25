"""
TurboPython Type System

Defines the core types available in TurboPython:
- int32: 32-bit integer (maps to int32_t)
- Ptr[T]: Mutable pointer (maps to T*)
- Ptr[readonly[T]]: Read-only pointer (maps to const T*)
- Array[T, N], Span[T], list[T], dict[K, V]: Container types
- User-defined records (classes)
- NominalType: User-defined records/protocols and module-defined generics
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field, replace as dc_replace
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence, TYPE_CHECKING

from .module_names import public_module_name
from .compilation_context import get_current_compiler, require_current_compiler

if TYPE_CHECKING:
    from .parse.nodes import TpyArrayLiteral, TpyListRepeat, TpyListComprehension, TpyCall, TpyDictLiteral, TpyFunction, TypeRefNode


# Toggled (default off) only by qualified_type_str, so diagnostics can render
# module-qualified nominals without changing ordinary str(type).
_qualify_nominals_in_str = False

# Toggled (default off) by codegen only while emitting a record whose member
# name shadows a same-named local type. While set, a local record/enum leaf
# renders as its fully-qualified C++ spelling (`::tpyapp::mod::Name`) so the
# member can't shadow the type reference. Off everywhere else -> bare name.
_qualify_shadowed_nominals = False


@contextmanager
def qualify_shadowed_nominals():
    """Render local nominal leaves fully-qualified for the duration -- used
    around the C++ emission of a record whose member shadows a same-named type."""
    global _qualify_shadowed_nominals
    prev = _qualify_shadowed_nominals
    _qualify_shadowed_nominals = True
    try:
        yield
    finally:
        _qualify_shadowed_nominals = prev


def qualified_type_str(t: 'TpyType') -> str:
    """Render `t` like `str(t)` but with cross-module nominal leaves qualified
    by their defining module (`red.Tag`, `list[red.Tag]`)."""
    global _qualify_nominals_in_str
    prev = _qualify_nominals_in_str
    _qualify_nominals_in_str = True
    try:
        return str(t)
    finally:
        _qualify_nominals_in_str = prev


def disambiguated_pair(expected: 'TpyType', actual: 'TpyType') -> tuple[str, str]:
    """Render `(expected, actual)` for an `expected X, got Y` diagnostic.

    When the two render to the same string but are distinct cross-module types
    (`Tag` vs `Tag`, `list[Tag]` vs `list[Tag]`), qualify both so the (correct)
    rejection is legible (`red.Tag` / `blue.Tag`); otherwise leave them plain.
    """
    e, a = str(expected), str(actual)
    if e != a:
        return e, a
    eq, aq = qualified_type_str(expected), qualified_type_str(actual)
    if eq != aq:
        return eq, aq
    return e, a


class TypeParamKind(Enum):
    """Kind of type parameter in a generic type."""
    TYPE = "type"  # A type parameter like T
    INT = "int"    # An integer literal like N


def bare_name(name: str) -> str:
    """Strip the dotted prefix from a (possibly nested) qualified name.

    `rsplit` returns the original string when the separator is absent, so
    this is safe for both `Foo` and `Outer.Inner` shapes.
    """
    return name.rsplit(".", 1)[-1]


def qualify_exception_name(
    name: str, registry: 'TypeRegistry', current_module: str | None = None,
) -> str:
    """Qualify a bare exception name with its defining module.

    Returns 'module.Name' for cross-module error_return matching.
    Prefers the builtins module (the public namespace) over internal
    submodules that happen to be compiled first.

    `current_module` (when provided) is excluded from the registry-
    modules iteration -- a module never finds its *own* ModuleInfo
    entry in the workspace-shared dict during its own analyze. This
    preserves the legacy per-analyzer invariant under the shared
    `registry.modules` from Phase 1.

    Resolution order:

    1. Already-qualified names pass through unchanged.
    2. If the analyzer's local registry has a record entry for `name`
       with a known defining module, use that. This guarantees that
       sub-phase 3 (decl) and sub-phase 4 (body) agree on the
       qualified shape regardless of which peer modules have been
       registered into the shared dict in between -- exact-string
       `func.error_return == qualified_exc` comparisons in
       statements.py rely on both sides producing identical strings.
       For locally-defined exceptions, `RecordInfo.module` is set in
       sub-phase 2 (`register_records_and_protocols`) so it's stable
       by the time exception qualification runs.
    3. Fall back to the public builtins module when applicable.
    4. Finally, scan the workspace-shared modules dict (excluding self).
    """
    if '.' in name:
        return name
    # Local-record fast path: when the analyzer's per-module registry has
    # a record entry for `name`, that record carries the *defining*
    # module via `RecordInfo.module` (set in sub-phase 2). Returning
    # this directly:
    #   1. Stabilizes the qname across decl/body sub-phases regardless
    #      of which peer modules' decl passes have populated the
    #      workspace-shared `registry.modules` dict in between.
    #   2. Honors the design's "defining qname" rendering policy: a
    #      package init that re-exports `JsonError` from
    #      `tplib.json.parser` resolves to the parser-defining qname,
    #      not the surface re-export. Codegen then matches the
    #      current module and emits the bare local name.
    local_record = registry.get_record(name)
    if local_record is not None and local_record.module:
        return f"{local_record.module}.{name}"
    # Prefer builtins -- it's the public namespace for built-in exceptions.
    # Without this, iteration order would find them in tpy._builtins._exceptions
    # (compiled first) instead of the public builtins module.
    builtins_mod = registry.get_module("builtins")
    if builtins_mod and name in builtins_mod.records:
        if local_record is None or local_record is builtins_mod.records[name]:
            return f"builtins.{name}"
        # User shadowed the builtin -- fall through to local resolution
    # Find the defining module. Skip modules whose record is shadowed
    # by a local definition.
    for mod_name, mod_info in registry.modules.items():
        if mod_name == current_module:
            continue
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
    Aliased imports (`from X import error as MyErr`) resolve to the
    underlying record's canonical name before cross-module qualification.
    """
    from tpyc.codegen_cpp.context import qualified_cpp_name
    bare = bare_name(name)
    record = registry.find_record(bare)
    if record and record.native_name:
        return record.native_name
    # Aliased import: registry holds the record under the alias, but its
    # canonical record.name + defining_module are the original. Use the
    # canonical pair to qualify when the source module differs.
    if record is not None and record.name != bare and current_module is not None:
        qual = registry.record_qualification(record, current_module)
        if qual is not None:
            source_module, original_name = qual
            return qualified_cpp_name(source_module, original_name)
        return record.name
    if "." in name:
        module_path, short = name.rsplit(".", 1)
        if module_path == current_module:
            return short
        return qualified_cpp_name(module_path, short)
    return name


# Per-compilation maps for type-name resolution
# (`Compiler.native_cpp_names` / `union_alias_names` / `protocol_modules` /
# `recursive_alias_cpp_names`) live on the active `Compiler` (see
# `tpyc/compilation_context.py`).
# Readers below use `get_current_compiler()` with an empty-default
# fallback so the type system stays usable from contexts without an
# active compiler (e.g. REPL display, isolated unit tests).

_EMPTY_NATIVE_CPP_NAMES: dict[str, str] = {}


def _native_cpp_names_view() -> dict[str, str]:
    """Read-side accessor for the active compiler's native_cpp_names dict.

    Returns an empty dict when no compilation is active so that
    `NominalType.to_cpp()` outside a compile context still works (no
    overrides => bare `self.name`), matching the previous module-global
    empty-default behavior.
    """
    compiler = get_current_compiler()
    if compiler is None:
        return _EMPTY_NATIVE_CPP_NAMES
    return compiler.native_cpp_names


def _local_qualified_cpp_names_view() -> dict[str, str]:
    """Read-side accessor for the active compiler's local-record qualified
    spellings (qname -> `::tpyapp::mod::Name`), consulted only under
    `_qualify_shadowed_nominals`."""
    compiler = get_current_compiler()
    if compiler is None:
        return _EMPTY_NATIVE_CPP_NAMES
    return compiler.local_qualified_cpp_names


def shadowed_local_cpp_name(module_qname: 'str | None') -> 'str | None':
    """Qualified C++ spelling for a local record/enum while shadow-qualification
    is active, else None. For codegen sites that render a callee from a bare
    name rather than through `NominalType.to_cpp()` (e.g. a local ctor call)."""
    if not _qualify_shadowed_nominals or module_qname is None:
        return None
    return _local_qualified_cpp_names_view().get(module_qname)


def ensure_qualified(name: str) -> str:
    """Ensure a global C++ name is fully qualified (prefixed with ::).

    Names already starting with :: are returned as-is.
    Single-name and namespaced names both get :: prepended (e.g.
    "BuildOpts" -> "::BuildOpts", "tpy::Foo" -> "::tpy::Foo"). Forcing the
    leading :: avoids ambiguity when generated code is emitted inside
    `namespace tpyapp::<module>` and a same-named symbol could exist there.
    """
    if name.startswith("::"):
        return name
    return f"::{name}"


def register_native_cpp_name(py_name: str, cpp_name: str) -> None:
    """Register a mapping from a Python class name to its native C++ name."""
    require_current_compiler().native_cpp_names[py_name] = ensure_qualified(cpp_name)


def _recursive_alias_cpp_names_view() -> dict[str, str]:
    """Read-side accessor for the active compiler's qname-keyed render map for
    generic recursive alias wrappers (see Compiler.recursive_alias_cpp_names).
    Empty when no compilation is active."""
    compiler = get_current_compiler()
    if compiler is None:
        return _EMPTY_NATIVE_CPP_NAMES
    return compiler.recursive_alias_cpp_names


def register_recursive_alias_cpp_name(qname: str, cpp_name: str) -> None:
    """Map a generic recursive alias's canonical qname to its qualified C++
    wrapper name (collision-proof across same-short-named aliases)."""
    require_current_compiler().recursive_alias_cpp_names[qname] = ensure_qualified(cpp_name)


def register_union_alias(members: tuple['TpyType', ...], alias_name: str) -> None:
    """Register a union type -> alias name mapping for codegen."""
    require_current_compiler().union_alias_names[members] = alias_name


# Builtins that are always ReturnException. Pre-seeded into each
# Compiler.return_exception_names because _funcs.py (which uses
# @error_return(StopIteration)) may be compiled before _exceptions.py
# registers StopIteration as ReturnException.
# Stored as bare names -- is_return_exception strips module prefixes.
BUILTIN_RETURN_EXCEPTIONS: frozenset[str] = frozenset({"StopIteration"})


def register_return_exception(name: str) -> None:
    """Register an exception type as ReturnException (return-only, used with @error_return)."""
    require_current_compiler().return_exception_names.add(name)


def error_return_matches(a: str | None, b: str | None) -> bool:
    """Check if two error_return type names refer to the same exception type.

    Both arguments are `qualify_exception_name` outputs, i.e. canonicalized
    to the *defining*-module qname, so a re-exported type resolves to the same
    qname through every import path and the full-string compare still matches
    it. Comparing bare names instead would conflate two genuinely distinct
    exceptions that share a short name across modules (each module's own
    `AppError`, `re.error`, ...).
    """
    if a is None or b is None:
        return a is b
    return a == b


def is_return_exception(name: str) -> bool:
    """Check if an exception type name is registered as ReturnException.

    Accepts both bare ('JsonError') and module-qualified ('tplib.json.JsonError')
    names -- extracts the bare name for matching since a ReturnException type
    is ReturnException regardless of which module references it.
    """
    compiler = get_current_compiler()
    bare = bare_name(name)
    if compiler is None:
        return bare in BUILTIN_RETURN_EXCEPTIONS
    return bare in compiler.return_exception_names


def is_exception_type(name: str, registry: 'TypeRegistry') -> bool:
    """Check if a record type inherits from Exception or BaseException.

    Accepts both bare ('ValueError') and module-qualified ('re.error') names.
    Dotted names route through find_record_by_qname so a same-bare-named local
    class doesn't shadow the qualified target.
    """
    from tpyc import qnames
    if "." in name:
        child = registry.find_record_by_qname(name)
    else:
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
    proto_modules = require_current_compiler().protocol_modules
    if protocol_name not in proto_modules:
        proto_modules[protocol_name] = public_module_name(module_name)


def get_protocol_qname(protocol_name: str) -> str | None:
    """Get the qualified name for a protocol from the global registry."""
    compiler = get_current_compiler()
    if compiler is None:
        return None
    mod = compiler.protocol_modules.get(protocol_name)
    if mod:
        return f"{mod}.{protocol_name}"
    return None


def impl_proto_matches_name(impl_proto: 'NominalType', protocol_name: str,
                            target_qname: str | None = None) -> bool:
    """Check if an implemented protocol matches a target by qualified name.

    Uses qualified_name() on impl_proto and `Compiler.protocol_modules`
    to avoid false matches with user protocols that shadow builtin names.
    Falls back to short name if qualified names aren't available.

    Args:
        target_qname: Pre-computed qualified name for the target protocol.
            If None, computed from protocol_name via `Compiler.protocol_modules`.
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
    compiler = get_current_compiler()
    if compiler is not None:
        compiler.native_cpp_names.clear()
        compiler.union_alias_names.clear()
        # Presence in `recursive_alias_cpp_names` is itself the per-module
        # signal: only *imported* aliases are registered, so the defining
        # module's own alias must be absent to render bare. Qname keying makes
        # entries collision-proof, not module-independent -- a stale entry from
        # an importer's pass would qualify the definer against itself.
        compiler.recursive_alias_cpp_names.clear()


def clear_all_compilation_state() -> None:
    """Full reset for a new compilation (called once per tpyc invocation)."""
    from tpyc.type_def_registry import clear_dynamic_type_defs
    _evaluating_send.clear()
    _evaluating_sync.clear()
    _evaluating_movable.clear()
    _evaluating_alias_value.clear()
    clear_dynamic_type_defs()


class ValueForm(Enum):
    """A type's C++ value category -- the storage/borrow form it lowers to.

    A general per-type property (not tuple-specific): the same categories drive
    standalone variable/param/return lowering and tuple-element lowering. The
    C++ *consequence* is context-dependent (a standalone `BORROW_REF` is a `T&`
    reference; a tuple-member `BORROW_REF` must be a pointer because a reference
    can't be a `std::tuple` member), but the category is the same.

    PTR_OPTIONAL (nullable pointer) and BORROW_REF (non-null borrow) stay
    distinct even though both can spell `T*`: they differ in nullability and
    access semantics, so collapsing them would risk wrong null / subscript /
    comparison handling.
    """
    VALUE = "value"            # value type: copied, rendered `T` everywhere.
    OWN = "own"                # Own[T]: moved by value.
    TYPE_PARAM = "type_param"  # generic `T`: val_or_ref proxies.
    PTR_OPTIONAL = "ptr_opt"   # pointer-repr Optional[non-value] (or forced
                               #   pointer-repr): storage `std::optional<T>`,
                               #   borrow `T*` (nullable).
    BORROW_REF = "borrow_ref"  # plain non-value (record/list/dict/set/recursive-
                               #   union wrapper): storage `T`, borrow `T&`.


@dataclass(frozen=True)
class TpyType:
    """Base class for all TurboPython types."""

    # Every TpyType carries a tuple of type arguments (possibly empty).
    # NominalType overrides with an instance field; primitives and most
    # structurals inherit the empty default. No annotation -- dataclass
    # subclasses would otherwise pick it up as an inherited field.
    type_args = ()

    def value_form(self) -> 'ValueForm':
        """This type's C++ value category (see `ValueForm`). Base: value types
        are VALUE, every other (non-value) type is a borrowed reference."""
        return ValueForm.VALUE if self.is_value_type() else ValueForm.BORROW_REF

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

    def wrapper_info(self) -> Optional["RecursiveUnionInfo"]:
        """Return the wrapper-struct identity if this type is a recursive
        union alias body, else None. Overridden by `UnionType`. Defaulted
        here on the base so call sites can use it without a type guard
        (`typ.wrapper_info()` works for any TpyType)."""
        return None

    def needs_wrapper(self) -> bool:
        """True if this type's C++ form requires a wrapper struct
        (recursive union alias). Defaulted on the base; overridden by
        `UnionType`."""
        return False

    def _nominal_td(self) -> Optional["TypeDef"]:
        """Return TypeDef for NominalType instances only.

        Pending* types and LiteralType delegate `qualified_name()` to
        their resolved form but should not inherit behavior from the
        registry -- their class overrides (or base-class defaults) are
        the source of truth. Structural wrappers with registered
        TypeDefs (currently just `tpy.Ptr`) have their own method
        overrides, so falling through to defaults here is also correct.
        """
        from tpyc.type_def_registry import type_def_of
        if not isinstance(self, NominalType):
            return None
        return type_def_of(self)

    def is_compile_time_only(self) -> bool:
        """Return True if this type exists only at compile time (no C++ representation)."""
        td = self._nominal_td()
        if td is not None:
            return td.is_compile_time_only
        return False

    def is_value_type(self) -> bool:
        """Return True if this is a value type (copy semantics).

        Value types include primitive types like int32, BigInt, Bool, char, str.
        These types should be copied when accessed from containers.

        Object types (RecordType, etc.) return False and should
        use reference semantics when accessed from containers.
        """
        td = self._nominal_td()
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
        td = self._nominal_td()
        if td is not None:
            return td.is_expensive_copy
        return False

    def subscript_borrows(self) -> bool:
        """Return True if a subscript result can borrow from this container's element
        storage (i.e. the result is a view into the container, not an owned copy).
        Source-mutation tracking at the call site ensures the borrow stays valid.
        User-defined types can opt in once borrow-source annotation (6.7) is implemented."""
        td = self._nominal_td()
        if td is not None:
            return td.subscript_borrows
        return False

    def is_send(self) -> bool:
        """Return True if this type is safe to transfer across threads.

        Default: value types are Send (copied, no aliasing). Override for
        pointer-like types (Ptr, Span) and containers (list, dict).
        """
        from tpyc.type_def_registry import resolve_send_sync
        td = self._nominal_td()
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
        from tpyc.type_def_registry import resolve_send_sync
        td = self._nominal_td()
        if td is not None:
            resolved = resolve_send_sync(td.is_sync, self.type_args)
            if resolved is not None:
                return resolved
        return self.is_value_type()

    def is_movable(self) -> bool:
        """Return True if this type can be relocated (its C++ move ctor is
        available). Default True: almost everything moves -- primitives and
        views by memcpy, containers/Box/Rc by pointer-steal, records member-
        wise. Non-movable only for owner-managed inline storage of a non-
        trivially-relocatable element (UninitArrayStorage) and records that
        transitively own one without __move__ / are @nomove. Composite types
        (Own/Optional/tuple/union/readonly) override to delegate to members."""
        return True

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
        td = self._nominal_td()
        if td is not None and td.param_cpp_formatter is not None:
            return td.param_cpp_formatter(self.type_args)
        if self.is_value_type():
            return self.to_cpp()
        return f"{self.to_cpp()}&"

    def to_cpp_param(self, name: str) -> str:
        """Return the C++ mutable-context parameter declaration for this type.

        Value types (primitives, views): T name (no &).
        Object types (containers, records): T& name.
        Types with param_mut_cpp_formatter (e.g. bytearray): use the
        registered mutable form (`std::vector<uint8_t>&`) -- their
        param_cpp_formatter encodes the const form for non-mutating use.

        Caller (gen_params) selects between this and to_cpp_const_param
        based on whether the param is in the function's mutated set.
        """
        td = self._nominal_td()
        if td is not None and td.param_mut_cpp_formatter is not None:
            return f"{td.param_mut_cpp_formatter(self.type_args)} {name}"
        return f"{self.to_cpp_param_type()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        """Return the C++ const parameter declaration for this type.

        Value types are passed by value: T name
        Object types are passed by const reference: const T& name
        Use for constructor params and other contexts where mutation is not needed.
        """
        td = self._nominal_td()
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
        td = self._nominal_td()
        if td is not None:
            return td.param_needs_copy_for_reassign
        return False

    def get_element_type(self) -> Optional['TpyType']:
        """Return the element type for container types, or None for non-containers."""
        td = self._nominal_td()
        if td is not None and td.element_of is not None:
            return td.element_of(self.type_args)
        return None

    def needs_explicit_element_target(self) -> bool:
        """Return True if array literals need explicit element type targeting.

        Array and Span need explicit conversions in their initializer lists.
        Dynamic containers (list, etc.) handle implicit conversions.
        """
        td = self._nominal_td()
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
    """Unresolved integer literal - can coerce to int32 or BigInt.

    This type represents integer literals before they're resolved to a
    concrete type. It coerces to int32 or BigInt based on context:
    - int32 + IntLiteral -> int32
    - BigInt + IntLiteral -> BigInt
    - IntLiteral + IntLiteral -> configured default (int32 by default)

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


class LiteralTag(Enum):
    """Type-tag for a `Literal[...]` member. Disambiguates True from 1 and
    similar bool-vs-int collisions that compare equal in Python."""
    STR = "str"
    INT = "int"
    BOOL = "bool"


@dataclass(frozen=True)
class LiteralValue:
    """A typed literal value. Distinguishes True from 1 via tag."""
    tag: LiteralTag
    value: str | int | bool

    def __str__(self) -> str:
        if self.tag is LiteralTag.STR:
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

    def to_cpp_return(self) -> str:
        """Return form for `Literal[...]` returns.

        For str-base Literal: every value is a string literal (static storage),
        so emit `std::string_view` -- no heap alloc, no dangling risk. For
        value-type bases (int, bool) the base's own return form is already
        right (returned by value).
        """
        if self.is_str_base():
            return self.base_type.to_cpp_param_type()  # std::string_view
        return self.base_type.to_cpp_return()

    def to_cpp_return_const(self) -> str:
        if self.is_str_base():
            return self.base_type.to_cpp_param_type()
        return self.base_type.to_cpp_return_const()

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

    def contains(self, tag: LiteralTag, value: str | int | bool) -> bool:
        """Check if a tagged value is in this Literal's value set."""
        return LiteralValue(tag, value) in self.values


@dataclass(frozen=True)
class FloatLiteralType(TpyType):
    """Unresolved float literal - adapts to float32 or float64 based on context.

    Like IntLiteralType, this represents a float literal (2.0, 1.5) before context
    determines whether it's float64 or float32. Default is float64.

    - float32 * FloatLiteral -> float32 (literal adapts to context)
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
    - Can be used as values in expressions (e.g., int32(N))

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

    def value_form(self) -> 'ValueForm':
        # A value-bounded / INT type param is a value; otherwise the generic
        # proxy category (val_or_ref_t<T>), neither a plain value nor a ref.
        return ValueForm.VALUE if self.is_value_type() else ValueForm.TYPE_PARAM

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
        # Not `const T&`: at a type whose parameter form is a distinct view
        # (`str`, `bytes`) that slot cannot bind the caller's read, and inside a
        # generic body there is nothing to materialize because `T` is not fixed.
        # `readonly_form_t` const-qualifies a mutable reference and passes every
        # other form through, so a reference instantiation keeps `const T&`.
        return f"::tpy::readonly_form_t<{self.to_cpp()}> {name}"

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


def is_native_template(td: Optional["TypeDef"]) -> bool:
    """Whether the C++ template behind this TypeDef is hand-written native
    code (an `@native` stub) rather than a struct TPy emits."""
    return td is not None and td.record is not None and td.record.is_native


def spells_readonly_arg_const(td: Optional["TypeDef"]) -> bool:
    """Whether this template spells a `readonly[T]` argument `const T`: only a
    declared borrowing view does. A borrow handle over readonly elements is a
    const handle (the runtime views specialize on it); a storage template owns
    its element, and C++ containers and allocators reject a const one."""
    from tpyc.type_def_registry import declares_borrowing_view
    return declares_borrowing_view(td)


def template_arg_cpp(arg: object, const_readonly: bool,
                     render: Callable[['TpyType'], str]) -> str:
    """One C++ template argument; `const_readonly` is
    `spells_readonly_arg_const` of the template."""
    if not isinstance(arg, TpyType):
        return str(arg)
    if const_readonly and isinstance(arg, ReadonlyType):
        return f"const {render(arg.wrapped)}"
    return render(arg)


def varargs_elem_cpp(elem: 'TpyType', render: Callable[['TpyType'], str]) -> str:
    """The element argument of a `*args` pack's `varargs<...>`. The parameter
    and the call-site pack both spell it here: a const mismatch between the two
    is a C++ conversion error."""
    from tpyc.type_def_registry import get_type_def
    return template_arg_cpp(
        elem, spells_readonly_arg_const(get_type_def("tpy.varargs")), render)


@dataclass(frozen=True)
class NominalType(TpyType):
    """A named type: user-defined record/protocol, or module-defined generic.

    During parsing, is_protocol defaults to False (unknown).
    After registration in sema, is_protocol is set correctly.

    For generic types like Stack[T] or Sequence[T]:
    - type_args stores the concrete type arguments (e.g., (int32,) for Stack[int32])

    For generic records with integer type parameters like Matrix[T, N: int]:
    - type_args can contain both TpyType and int values (e.g., (int32, 8))

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

    def _cpp_base_name(self) -> str:
        """C++ base name (no type args), preferring a qname-keyed mapping.

        `native_cpp_names` is also keyed by short name, which collides for
        two records sharing a short name imported from different modules;
        the canonical qname disambiguates them (codegen registers both keys).
        """
        # Shadow-qualification: while emitting a record whose member name
        # shadows a same-named local type, a local record/enum must render
        # fully-qualified so the member can't hijack the type reference.
        if _qualify_shadowed_nominals and self._module_qname is not None:
            shadowed = _local_qualified_cpp_names_view().get(self._module_qname)
            if shadowed is not None:
                return shadowed
        view = _native_cpp_names_view()
        if self._module_qname is not None:
            qualified = view.get(self._module_qname)
            if qualified is not None:
                return qualified
        registered = view.get(self.name)
        if registered is not None:
            return registered
        # native_cpp_names skips factory-backed builtins and is filled only at
        # codegen, and a dict view is rendered outside codegen too: read its stub.
        td = self._nominal_td()
        if is_native_template(td) and td.record.native_name:
            return td.record.native_name
        return self.name

    def to_cpp_base_name(self) -> str:
        """Return the C++ name without type arguments."""
        return self._cpp_base_name()

    def to_cpp(self) -> str:
        if self.is_protocol and not self.is_dynamic_protocol:
            # Structural protocol: template parameter placeholder
            return "T"
        # Per-qname cpp formatter (registry overrides the default {name}<{args}>
        # rendering for builtins whose C++ spelling is more than the stub's
        # `@native` name plus its arguments).
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        if td is not None and td.cpp_formatter is not None:
            return td.cpp_formatter(self.type_args)
        if td is not None and td.is_compile_time_only:
            raise TypeError(f"{self.name} is compile-time only and has no C++ representation")
        # Check for native C++ name mapping. `Compiler.native_cpp_names`
        # is populated during codegen setup -- includes @native records,
        # imported records, AND @builtin_type-with-body records
        # (registered via the `record_info.builtin_type_key` loop in
        # codegen_cpp/generator.py).
        cpp_name = self._cpp_base_name()
        if self.type_args:
            const_readonly = spells_readonly_arg_const(td)
            args = ", ".join(
                template_arg_cpp(t, const_readonly, lambda a: a.to_cpp())
                for t in self.type_args
            )
            return f"{cpp_name}<{args}>"
        return cpp_name

    def __str__(self) -> str:
        name = self.name
        # Builtins (`list`, `dict`, ...) never collide cross-module, so only
        # qualify user records -- qualifying `list` to `builtins.list` is noise.
        if _qualify_nominals_in_str and self.is_user_record:
            name = self.qualified_name() or self.name
        if self.type_args:
            args = ", ".join(
                str(t) if isinstance(t, TpyType) else str(t)
                for t in self.type_args
            )
            return f"{name}[{args}]"
        return name

    def qualified_name(self) -> Optional[str]:
        if self._module_qname:
            return self._module_qname
        if self.is_protocol:
            compiler = get_current_compiler()
            if compiler is not None:
                mod = compiler.protocol_modules.get(self.name)
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
        if td is None:
            return self.is_value_type()
        resolved = resolve_send_sync(td.is_send, self.type_args)
        if resolved is not None:
            return resolved
        rec = td.record
        if rec is None:
            return self.is_value_type()
        if rec.send_override is not None:
            return rec.send_override
        if (rec.send_override_when is not None
                and _conditional_override_holds(rec.send_override_when, self.type_args)):
            return True
        if not rec.type_params:
            return rec.is_send
        # Generic record: re-walk under use-site type_args. The cycle
        # guard returns True on re-entry (greatest fixed point) so
        # self-referential generics like `Tree[T]: children: list[Tree[T]]`
        # terminate.
        if self in _evaluating_send:
            return True
        _evaluating_send.add(self)
        try:
            for sub in _fields_and_parents_under_args(rec, self.type_args):
                if not (sub.is_send() or contains_type_param(sub)):
                    return False
            return True
        finally:
            _evaluating_send.discard(self)

    def is_sync(self) -> bool:
        from tpyc.type_def_registry import type_def_of, resolve_send_sync
        td = type_def_of(self)
        if td is None:
            return self.is_value_type()
        resolved = resolve_send_sync(td.is_sync, self.type_args)
        if resolved is not None:
            return resolved
        rec = td.record
        if rec is None:
            return self.is_value_type()
        if rec.sync_override is not None:
            return rec.sync_override
        if (rec.sync_override_when is not None
                and _conditional_override_holds(rec.sync_override_when, self.type_args)):
            return True
        if not rec.type_params:
            return rec.is_sync
        if self in _evaluating_sync:
            return True
        _evaluating_sync.add(self)
        try:
            for sub in _fields_and_parents_under_args(rec, self.type_args):
                if not (sub.is_sync() or contains_type_param(sub)):
                    return False
            return True
        finally:
            _evaluating_sync.discard(self)

    def is_movable(self) -> bool:
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(self)
        rec = td.record if td is not None else None
        if rec is None:
            return True
        # @nomove (explicit) wins; __move__ supplies a relocating move so the
        # owner is movable regardless of its members (mirrors the __copy__
        # escape from nocopy propagation).
        if rec.move_override is not None:
            return rec.move_override
        if rec.has_move:
            return True
        if not rec.type_params:
            return rec.is_movable
        # Generic record: re-walk under use-site type_args. Cycle guard
        # returns True on re-entry (movable unless proven otherwise).
        if self in _evaluating_movable:
            return True
        _evaluating_movable.add(self)
        try:
            for sub in _fields_and_parents_under_args(rec, self.type_args):
                if not (sub.is_movable() or contains_type_kind_param(sub)):
                    return False
            return True
        finally:
            _evaluating_movable.discard(self)

    def get_element_type(self) -> Optional['TpyType']:
        # Per-qname override (e.g. Span/SpanIter reshape a readonly[T] element:
        # strip for a value element, keep for a reference element).
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
    boundary, where one side may be a placeholder minted pre-qname
    that must still match a qname-bearing counterpart:
    `TypeRegistry.is_subclass_of`'s parent-chain walk (a recorded
    parent reference vs a qname-bearing argument), the
    `record_to_ptr` / `record_to_const_ptr` coercions (the `&value`
    pointee may be a parser placeholder), and the match value-pattern
    enum-identity check in `sema/match.py::_validate_value_pattern`
    (where both sides are always qname-bearing, so the loose fallback
    is inert -- it reuses this helper for the name/type_args/is_protocol
    equality rather than for placeholder tolerance).  Keep the call-site
    list short; each addition should be justified at review time.
    """
    if not (isinstance(a, NominalType) and isinstance(b, NominalType)):
        return False
    if a.name != b.name or a.type_args != b.type_args or a.is_protocol != b.is_protocol:
        return False
    if a._module_qname is None or b._module_qname is None:
        return True
    return a._module_qname == b._module_qname


def same_base_type(a: 'TpyType', b: 'TpyType') -> bool:
    """Type equivalence for inheritance graph walks.

    NominalTypes go through `same_nominal_symbol_loose` so pre-resolve parent
    placeholders still match their resolved counterparts; everything else
    uses Python equality.
    """
    if isinstance(a, NominalType) and isinstance(b, NominalType):
        return same_nominal_symbol_loose(a, b)
    return a == b


def substitute_type_params_structural(
    typ: TpyType, subst: dict[str, TpyType],
) -> TpyType:
    """Replace TypeParamRef nodes in `typ` with values from `subst`.

    Structural walk: handles only TPy-type references; INT-kind
    TypeParamRefs and Array's int slot pass through unchanged. The sema
    layer has a more capable `TypeOps.substitute_type_params` in
    type_ops.py that handles representation choices (e.g. Optional
    pointer repr); this one is for typesys-internal callers that only
    need structural replacement.
    """
    if isinstance(typ, TypeParamRef):
        return subst.get(typ.name, typ)
    # NominalType.type_args can hold raw ints (Array[T, N]); map_inner_types
    # only walks TpyType entries, so mixed args need direct handling to
    # preserve the int slots.
    if type(typ) is NominalType and typ.type_args:
        new_args: list = []
        changed = False
        for arg in typ.type_args:
            if isinstance(arg, TypeParamRef) and arg.name in subst:
                new_args.append(subst[arg.name])
                changed = True
            elif hasattr(arg, "map_inner_types"):
                new = substitute_type_params_structural(arg, subst)
                new_args.append(new)
                if new is not arg:
                    changed = True
            else:
                new_args.append(arg)
        if changed:
            return NominalType(
                typ.name, tuple(new_args), typ.is_protocol,
                typ._module_qname, typ.is_dynamic_protocol,
            )
        return typ
    return typ.map_inner_types(
        lambda t: substitute_type_params_structural(t, subst))


def substitute_type_params_simple(
    typ: TpyType, subst: dict[str, TpyType],
) -> TpyType:
    """Replace TypeParamRef nodes via `map_inner_types` -- the codegen-side
    substitution. `TypeResolver.substitute_type_params` delegates here and
    THIR lowering resolves call-site param slots through the same function,
    so the two emit paths cannot drift. Unlike the structural variant above
    it does not special-case Array's raw-int `type_args` slot."""
    if isinstance(typ, TypeParamRef):
        return subst.get(typ.name, typ)
    return typ.map_inner_types(
        lambda t: substitute_type_params_simple(t, subst))


def expand_fi_template(fi: 'FunctionInfo',
                       type_args: 'tuple[TpyType, ...] | None') -> str:
    """A `cpp_template`'s named-placeholder substitution -- the `{T}` slots
    (each `to_cpp_stored()`) and the `{cpp}` return-type spelling
    (substituted, `to_cpp()`) -- WITHOUT the positional expansion: the
    caller expands `{0}, {1}, ...` over the args (`expand_cpp_template` /
    THIR's cpp_template emit arm). The ONE substitution rule shared by
    `gen_call_from_fi` and the THIR lowering mirror, so the two emit paths
    cannot drift."""
    template = fi.cpp_template
    effective_subst = None
    if type_args and fi.type_params:
        effective_subst = dict(zip(fi.type_params, type_args))
        for name, typ in effective_subst.items():
            placeholder = f"{{{name}}}"
            if placeholder in template and hasattr(typ, "to_cpp"):
                template = template.replace(placeholder, typ.to_cpp_stored())
    if "{cpp}" in template and fi.return_type is not None:
        ret_type = fi.return_type
        if effective_subst:
            ret_type = substitute_type_params_simple(ret_type, effective_subst)
        template = template.replace("{cpp}", ret_type.to_cpp())
    return template


# Re-entrancy guards for NominalType.is_send / is_sync. Generic records
# can recurse into themselves through container fields (e.g.
# `class Tree[T]: children: list[Tree[T]]`). Re-entry returns True --
# the greatest-fixed-point answer for a recursive type's Send/Sync
# property: assume the recursive position is Send/Sync, then validate
# the rest of the walk. Cleared per compilation via
# `clear_all_compilation_state`.
_evaluating_send: set['NominalType'] = set()
_evaluating_sync: set['NominalType'] = set()
_evaluating_movable: set['NominalType'] = set()


def _fields_and_parents_under_args(
    record: 'RecordInfo', type_args: tuple,
) -> Iterator[TpyType]:
    """Yield each field type and each parent type from `record` with
    `record.type_params -> type_args` substituted.

    Callers apply a Send/Sync predicate per yielded type. The
    `contains_type_param` allowance at the call site keeps unresolved
    type parameters conservative -- needed both when the query happens
    during another generic's registration (so `type_args` is itself
    `(TypeParamRef(...),)`) and when use-site `type_args` leaves some
    parameters unbound (sema should reject these but this stays robust).
    """
    subst: dict[str, TpyType] = {}
    if record.type_params and type_args:
        for name, arg in zip(record.type_params, type_args):
            if isinstance(arg, TpyType):
                subst[name] = arg
    for f in record.fields:
        yield substitute_type_params_structural(f.type, subst) if subst else f.type
    for p in record.parents:
        yield substitute_type_params_structural(p, subst) if subst else p


def _conditional_override_holds(required_traits: tuple[str, ...], type_args: tuple) -> bool:
    """Back `@unsafe_send`/`@unsafe_sync` with if_params_* kwargs: True iff every (resolved)
    type arg satisfies every listed marker trait (`tpy.Send` / `tpy.Sync`).

    An arg that still contains a type parameter is treated as satisfying, so a
    generic use (`Arc[T]` inside another generic) defers to the concrete
    instantiation -- mirroring `_fields_and_parents_under_args`'s allowance.
    An empty `type_args` (the bare, uninstantiated record) does not grant the
    override; the caller falls through to the structural answer.
    """
    from tpyc import qnames
    if not type_args:
        return False
    for ta in type_args:
        if not isinstance(ta, TpyType) or contains_type_param(ta):
            continue
        for trait in required_traits:
            if trait == qnames.SEND and not ta.is_send():
                return False
            if trait == qnames.SYNC and not ta.is_sync():
                return False
    return True


class C3LinearizationError(Exception):
    """Raised when C3 merge fails (no valid linearization)."""


def c3_linearize(bases_with_mros: list[tuple['TpyType', list['TpyType']]]) -> list['TpyType']:
    """Compute C3 linearization of a class's ancestors (self excluded).

    Per C3: merge of [L(B1), ..., L(Bn), [B1, ..., Bn]] where L(Bi) = Bi followed
    by Bi's own linearized ancestors. Raises C3LinearizationError if no valid
    linearization exists.
    """
    if not bases_with_mros:
        return []
    # Per C3: merge of [L(B1), ..., L(Bn), [B1, ..., Bn]], where L(Bi) includes Bi.
    lists: list[list[TpyType]] = []
    for base, base_mro in bases_with_mros:
        lists.append([base] + list(base_mro))
    lists.append([b for b, _ in bases_with_mros])

    result: list[TpyType] = []
    # Filter out any empty lists that may have been passed in.
    lists = [lst for lst in lists if lst]
    while lists:
        head = None
        for lst in lists:
            candidate = lst[0]
            # A valid head appears only at positions[0] across all lists -- not in any tail.
            in_tail = any(
                any(same_base_type(x, candidate) for x in other[1:])
                for other in lists
            )
            if not in_tail:
                head = candidate
                break
        if head is None:
            raise C3LinearizationError(
                "C3 linearization failed: inconsistent base-class ordering across parents"
            )
        result.append(head)
        new_lists: list[list[TpyType]] = []
        for lst in lists:
            if lst and same_base_type(lst[0], head):
                rest = lst[1:]
                if rest:
                    new_lists.append(rest)
            else:
                new_lists.append(lst)
        lists = new_lists
    return result


@dataclass(frozen=True)
class SelfType(TpyType):
    """Self type for method signatures.

    In protocol method signatures, Self represents the implementing type.
    When checking if int32 conforms to a protocol with Self, Self is
    substituted with int32.

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
        # Ptr[None] -> void* (and Ptr[readonly[None]] -> const void*).
        # `void*` is the canonical opaque-pointer idiom in C/C++ and is
        # what user @native bindings to C structs expect; std::monostate*
        # would force per-cast bridges at every interop boundary.
        if is_void_like_type(self.inner_pointee):
            return "const void*" if self.is_readonly else "void*"
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


def is_dyn_protocol(typ: 'TpyType') -> bool:
    """Check if a type is a @dynamic protocol -- an abstract base in C++.

    Such types have no value-by-value storage (size unknown, abstract methods);
    Own[T] for these lowers to std::unique_ptr<T>, Ptr[T] stays T*.
    """
    return isinstance(typ, NominalType) and typ.is_dynamic_protocol


@dataclass(frozen=True)
class OwnType(TpyType):
    """Owned type - passed/returned by value (ownership transfer).

    Own[T] wraps a type to indicate ownership transfer - the value is
    moved/copied, not referenced. Used for function parameters and returns.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        # Own[P] for abstract @dynamic P -- heap-owned via std::unique_ptr<P>
        # since the abstract base has no sizeof.
        if is_dyn_protocol(self.wrapped):
            return f"std::unique_ptr<{self.wrapped.to_cpp()}>"
        return self.wrapped.to_cpp()

    def is_value_type(self) -> bool:
        # Own[T] uses T&& at param boundaries; treat as value type for other purposes
        return True

    def value_form(self) -> 'ValueForm':
        return ValueForm.OWN

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        # Single-owner move slot; sharing readonly aliases of an Own[T]
        # slot is a category error regardless of T.
        return False

    def is_movable(self) -> bool:
        return self.wrapped.is_movable()

    def to_cpp_param_type(self) -> str:
        if is_dyn_protocol(self.wrapped):
            return f"std::unique_ptr<{self.wrapped.to_cpp()}>"
        if self.wrapped.is_value_type():
            return self.wrapped.to_cpp()
        cpp_type = self.wrapped.to_cpp()
        if isinstance(self.wrapped, TypeParamRef):
            # own_param_t resolves to unique_ptr<T> for abstract @dynamic T,
            # T&& otherwise -- lazy per-T dispatch in a template context.
            # Also prevents forwarding-ref deduction (non-deduced context).
            return f"::tpy::own_param_t<{cpp_type}>"
        return f"{cpp_type}&&"

    def to_cpp_param(self, name: str) -> str:
        if is_dyn_protocol(self.wrapped):
            return f"std::unique_ptr<{self.wrapped.to_cpp()}> {name}"
        if self.wrapped.is_value_type():
            return f"{self.wrapped.to_cpp()} {name}"
        cpp_type = self.wrapped.to_cpp()
        if isinstance(self.wrapped, TypeParamRef):
            return f"::tpy::own_param_t<{cpp_type}> {name}"
        return f"{cpp_type}&& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        return self.to_cpp_param(name)

    def to_cpp_return(self) -> str:
        # Lazy dispatch via own_return_t<T>: T for concrete, unique_ptr<T>
        # for abstract @dynamic. Direct `T` would be ill-formed when T
        # monomorphizes to an abstract base.
        if isinstance(self.wrapped, TypeParamRef):
            return f"::tpy::own_return_t<{self.wrapped.to_cpp()}>"
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

    def needs_wrapper(self) -> bool:
        # readonly is a const qualifier, not a shape change -- the C++
        # form is the wrapped type. Consumers asking "does this type
        # have a wrapper struct?" must see through the qualifier.
        return self.wrapped.needs_wrapper()

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        # readonly restricts this handle only -- the object may be mutated
        # through other non-readonly aliases, so no Send->Sync lift.
        return self.wrapped.is_sync()

    def is_movable(self) -> bool:
        return self.wrapped.is_movable()

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


def peel_value_readonly(typ: 'TpyType') -> 'TpyType':
    """Strip `readonly` off a VALUE type at a position that binds a copy (a
    parameter, a loop var, a comprehension element): the copy is the
    receiver's own, so the marker says nothing about it. A non-value type
    keeps the marker (the binding borrows, and the marker is what makes it
    const), and so does a value tuple with borrow-form elements: the tuple
    copies but its elements alias the source objects, so its readonly is the
    const protecting them. `has_ref_elements` covers every borrowed element
    kind (records, pointer-variant unions, recursive-union wrappers)."""
    if isinstance(typ, ReadonlyType) and typ.wrapped.is_value_type():
        inner = typ.wrapped
        if isinstance(inner, TupleType) and inner.has_ref_elements():
            return typ
        return inner
    return typ


def is_readonly_ref_param(typ: 'TpyType | None') -> bool:
    """An explicit readonly[T] param whose underlying T passes by C++
    reference (const T&). The wrapper defeats is_ref_param() by design (a
    const ref binds an rvalue inline in a sync call, no temp needed), so
    the frame-capture-aware call sites -- which must materialize a named
    scope-local for a borrowing generator/coro factory -- test this
    composite separately. One definition, shared by the AST arg arms and
    the THIR temp rows, so the mirror pair cannot drift."""
    return (isinstance(typ, ReadonlyType)
            and unwrap_readonly(typ).is_ref_param())


def unwrap_own(typ: 'TpyType') -> 'TpyType':
    """Strip OwnType wrapper if present, returning the inner type."""
    if isinstance(typ, OwnType):
        return typ.wrapped
    return typ


def type_contains_own(typ: 'TpyType', _under_optional: bool = False,
                      allow_optional_own: bool = True) -> bool:
    """True if a *redundant* `OwnType` appears anywhere in the type tree.

    `Own[T]` only ever selects an owned C++ shape. A storage position (local
    variable or field) owns its value inline regardless, so an `Own` there is
    redundant -- EXCEPT, for a LOCAL, `Optional[Own[T]]`: a local `Optional[T]`
    defaults to a BORROW (`T*`), so the `Own` directly under the `Optional` is
    the load-bearing selector for an OWNED nullable (`std::optional<T>`). A
    FIELD `Optional[T]` is `std::optional<T>` regardless, so the exemption does
    not apply there -- callers validating a field pass `allow_optional_own=False`
    to reject `Optional[Own[T]]` too. The exemption is for the direct
    `Optional`->`Own` shape only -- `Own` nested under a tuple/union/nominal
    (`tuple[..., Own[T]] | None`) collapses anyway, so `_under_optional` does
    not propagate into those branches. `allow_optional_own`, by contrast, IS
    threaded into them so a nested `Optional`->`Own` (e.g. `tuple[Optional[
    Own[T]]]`) still consults it -- the threading is load-bearing, not dead.
    """
    if isinstance(typ, OwnType):
        return not (_under_optional and allow_optional_own)
    if isinstance(typ, OptionalType):
        return type_contains_own(typ.inner, _under_optional=True,
                                 allow_optional_own=allow_optional_own)
    if isinstance(typ, (ReadonlyType, AutoReadonlyType, RefType)):
        return type_contains_own(typ.wrapped, _under_optional, allow_optional_own)
    if isinstance(typ, UnionType):
        return any(type_contains_own(m, allow_optional_own=allow_optional_own)
                   for m in typ.members)
    if isinstance(typ, TupleType):
        return any(type_contains_own(e, allow_optional_own=allow_optional_own)
                   for e in typ.element_types)
    if isinstance(typ, NominalType):
        return any(type_contains_own(a, allow_optional_own=allow_optional_own)
                   for a in typ.type_args)
    return False


def coro_struct_owner(owning_type_qname: 'str | None',
                      receiver: 'NominalType',
                      receiver_record: 'RecordInfo | None' = None) -> 'NominalType':
    """Owner NominalType for naming a method's coro / resumable-frame struct.

    The struct is emitted once, for the method's DEFINING record. When the
    method is inherited (its `owning_type_qname` differs from the receiver's
    qname), re-base onto the defining record so call/await sites name
    `__coro_<Base>_<method>` (which exists) rather than
    `__coro_<Subclass>_<method>` (which is never emitted). For an own method
    the receiver is returned unchanged, preserving class-level type_args.

    A generic base needs its concrete type args too (`__coro_Box_fetch<int32_t>`,
    not a bare `__coro_Box_fetch`). The receiver's MRO carries that binding --
    `IntBox(Box[int32])` records `Box[int32]` in `mro_ancestors` -- so when a
    `receiver_record` is supplied, re-base onto the matching bound ancestor.
    Without it (or for a non-generic base, where the ancestor has no type args)
    fall back to the bare base name.
    """
    if owning_type_qname is None or owning_type_qname == receiver.qualified_name():
        return receiver
    if receiver_record is not None:
        for anc in receiver_record.mro_ancestors:
            if (isinstance(anc, NominalType)
                    and anc.qualified_name() == owning_type_qname):
                return anc
    base_name = owning_type_qname.rsplit(".", 1)[-1]
    return NominalType(base_name, _module_qname=owning_type_qname)


def strip_own_type_args(typ: 'NominalType') -> 'NominalType':
    """Strip a top-level Own[...] from each of a nominal type's args
    (`Iterable[Own[X]]` -> `Iterable[X]`). An Own-wrapped type arg signals
    copy/ownership semantics for the copy-semantics conformance check, not a
    distinct element type, so both sides are stripped before structural match."""
    return typ.with_inner_types(
        tuple(unwrap_own(a) for a in typ.inner_types()))


def yield_uses_borrow_slot(elem_type: 'TpyType') -> bool:
    """Whether a generator / genexpr yield of this element uses the plain
    `val_or_ref<T>` borrow slot -- i.e. a bare reference type (record / class)
    handed out by reference, zero-copy, instead of a copy (`Iterator[T]` for a
    non-value `T`).

    A `readonly` element is INCLUDED, spelling `val_or_ref<const T>` -- the
    const rides inside the borrow slot exactly as it does at the sibling
    `error_return_uses_borrow_slot`'s `std::expected` payload
    (`codegen_cpp/functions.py`). Excluding it made `Iterator[readonly[T]]`
    a VALUE slot, which copies the element out of the source on every pull
    (a whole `std::vector` for a container element) where the mutable twin
    hands out a pointer -- a silent copy, and a divergence from CPython,
    which aliases.

    An unsubstituted `TypeParamRef` is INCLUDED, spelling `val_or_ref<T>`:
    `val_or_ref` stores a value `T` by value and a reference `T` by pointer,
    so the element's value-vs-reference shape is settled at INSTANTIATION --
    the generic frame then renders exactly what its monomorphic twin does.
    A bare `T` slot assumed VALUE and copied the element out of the source,
    losing a consumer's mutation where `Iterator[Point]` lends it. The
    value-bounded (`T: ValueType`) and INT type params never reach here:
    `is_value_type()` answers True for them above. Admission is not the whole
    answer for an open `T` -- whether a given generator's yields may be lent
    at all is the per-generator provenance verdict
    (`TpyFunction.generic_yield_borrows`); this predicate only says the slot
    CAN carry a borrow.

    Excluded (each keeps its existing slot): value-type elements (copied);
    `Own` (owned value slot, moved out); tuples (own borrow form via
    `to_cpp_return`); `Optional` / `Union` (pointer / storage-form machinery
    -- `optional_to_ptr`, pointer variants).
    """
    if elem_type.is_value_type():
        return False
    if isinstance(unwrap_readonly(unwrap_ref_type(elem_type)), OwnType):
        return False
    bare = unwrap_ref_type(unwrap_readonly(elem_type))
    return not isinstance(bare, (TupleType, OptionalType, UnionType))


def yield_always_borrows(elem_type: 'TpyType') -> bool:
    """Whether the borrow slot `yield_uses_borrow_slot` admits hands out a
    borrow at EVERY instantiation -- the same question minus the open type
    param, whose `val_or_ref<T>` holds a pointer at a reference `T` and a
    plain value at a value `T`.

    Sema rules that must answer at DEFINITION time read this one: a generic
    body is analyzed once, before any instantiation, so a rule that treats
    the open `T` as a borrow rejects the value instantiations too (a
    `for x in it: yield x` relay of an `Iterator[T]` param is the shape that
    showed it). The slot spelling has no such problem -- C++ resolves it per
    instantiation -- so it reads the predicate above instead.
    """
    return (yield_uses_borrow_slot(elem_type)
            and not isinstance(unwrap_ref_type(unwrap_readonly(elem_type)),
                               TypeParamRef))


def yield_slot_borrows(elem_type: 'TpyType', generic_borrows: bool) -> bool:
    """Whether a generator's yield slot for `elem_type` hands out a borrow.

    `yield_uses_borrow_slot` says the slot CAN carry one; an OPEN `T` also
    needs the producer's per-generator provenance verdict
    (`TpyFunction.generic_yield_borrows`), since one slot type serves the
    whole frame. The open-`T` shape test lives here alone, so a consumer
    re-deriving it cannot drift from the spelling `yield_borrow_slot_cpp`
    picks for the same element.
    """
    if not yield_uses_borrow_slot(elem_type):
        return False
    return yield_always_borrows(elem_type) or generic_borrows


def yield_borrow_slot_cpp(elem_type: 'TpyType', cpp_elem: str) -> str:
    """The `val_or_ref<...>` spelling of a slot `yield_uses_borrow_slot`
    admits. ONE site, so the const half of the answer cannot drift from the
    predicate that decides the slot is a borrow at all: a `readonly` element
    stores a `const T*` -- the spelling `ReadonlyType.to_cpp_stored` already
    uses for the same "a slot that cannot hold a reference" question.

    An OPEN type param takes the idempotent `yield_slot_t<T>` instead: a
    generic callee's type argument is substituted with the slot form already
    when sema infers it as a borrow (`Ref[X]` renders `val_or_ref<X>`), so a
    bare wrap would nest at exactly those instantiations."""
    is_const = isinstance(unwrap_ref_type(elem_type), ReadonlyType)
    if isinstance(unwrap_ref_type(unwrap_readonly(elem_type)), TypeParamRef):
        inner = f"const {cpp_elem}" if is_const else cpp_elem
        return f"::tpy::yield_slot_t<{inner}>"
    if is_const:
        return f"::tpy::val_or_ref<const {cpp_elem}>"
    return f"::tpy::val_or_ref<{cpp_elem}>"


def error_return_uses_borrow_slot(return_type: 'TpyType') -> bool:
    """Whether an @error_return function's compiled return stores its success
    value through `val_or_ref<T>` -- std::expected cannot hold a reference, so
    a borrow-form (non-value, non-void) return is wrapped. Own[T] is exempt
    (is_value_type True: the success value is moved out by value). ONE answer
    for both the signature render (codegen functions) and any consumer of the
    compiled return (the CPython tp_iternext glue), so a wrap without the
    matching unwrap can't reappear. Deliberately bare, unlike the sibling
    yield_uses_borrow_slot above: its Optional/Union/tuple exclusions serve
    the yield slot's distinct storage forms, while this mirrors the expected
    wrap exactly (those shapes DO wrap here; the boundary consumer never
    sees them -- is_function_boundary_marshallable rejects them upstream)."""
    return (not return_type.is_value_type()
            and not isinstance(return_type, VoidType))


def unwrap_qualifiers(typ: 'TpyType') -> 'TpyType':
    """Strip Send/Sync markers, ReadonlyType, OwnType, and RefType wrappers."""
    typ = unwrap_send_sync(typ)
    if isinstance(typ, RefType):
        typ = typ.wrapped
    if isinstance(typ, ReadonlyType):
        typ = typ.wrapped
    if isinstance(typ, OwnType):
        typ = typ.wrapped
    return typ


class MarkerAssertionError(Exception):
    """A Send[T] / Sync[T] static assertion failed at canonicalization time.

    Callers holding a source location convert it to ParseError/SemanticError.
    """


@dataclass(frozen=True)
class _MarkerType(TpyType):
    """Base for the Send[T] / Sync[T] marker wrappers.

    Sema-only: the C++ representation is identical to the wrapped type, so
    every emission method delegates. A marker persists after resolution only
    when the trait is a per-value property the type can't answer statically
    (erased Callable / @dynamic protocols, unsubstituted type params); for
    everything else make_send_marker / make_sync_marker check the assertion
    and erase the wrapper at resolve time.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        return self.wrapped.to_cpp()

    def is_value_type(self) -> bool:
        return self.wrapped.is_value_type()

    def needs_wrapper(self) -> bool:
        return self.wrapped.needs_wrapper()

    def to_cpp_param_type(self) -> str:
        return self.wrapped.to_cpp_param_type()

    def to_cpp_param(self, name: str) -> str:
        return self.wrapped.to_cpp_param(name)

    def to_cpp_const_param(self, name: str) -> str:
        return self.wrapped.to_cpp_const_param(name)

    def to_cpp_return(self) -> str:
        return self.wrapped.to_cpp_return()

    def to_cpp_return_const(self) -> str:
        return self.wrapped.to_cpp_return_const()

    def to_cpp_stored(self) -> str:
        return self.wrapped.to_cpp_stored()

    def is_ref_param(self) -> bool:
        return self.wrapped.is_ref_param()

    def param_needs_copy_for_reassign(self) -> bool:
        return self.wrapped.param_needs_copy_for_reassign()

    def get_element_type(self) -> Optional['TpyType']:
        return self.wrapped.get_element_type()

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)


@dataclass(frozen=True)
class SendType(_MarkerType):
    """Send[T]: asserts values entering this slot are Send."""

    def is_send(self) -> bool:
        return True

    def is_sync(self) -> bool:
        return self.wrapped.is_sync()

    def __str__(self) -> str:
        return f"Send[{self.wrapped}]"

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        # Generic substitution may replace a wrapped TypeParamRef with a
        # concrete type; re-canonicalize so non-erased Send types erase the
        # wrapper. Lenient: with no diagnostic channel here, a failed
        # assertion keeps the wrapper, and conversion-site checks reject
        # every incoming value instead.
        return make_send_marker(types[0], lenient=True)


@dataclass(frozen=True)
class SyncType(_MarkerType):
    """Sync[T]: asserts values entering this slot are Sync."""

    def is_send(self) -> bool:
        return self.wrapped.is_send()

    def is_sync(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"Sync[{self.wrapped}]"

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return make_sync_marker(types[0], lenient=True)


def unwrap_send_sync(typ: 'TpyType') -> 'TpyType':
    """Strip Send[T] / Sync[T] marker wrappers (canonically outermost)."""
    while isinstance(typ, _MarkerType):
        typ = typ.wrapped
    return typ


def _marker_persists(t: 'TpyType') -> bool:
    """Whether Send/Sync-ness of `t` is a per-value property (erased impl
    or unsubstituted type param) rather than a static type-level fact."""
    base = t
    while isinstance(base, (ReadonlyType, OwnType, RefType, _MarkerType)):
        base = base.wrapped
    if isinstance(base, CallableType) and not base.is_template:
        return True
    if is_dyn_protocol(base):
        return True
    return isinstance(base, TypeParamRef)


def _make_marker(inner: 'TpyType', trait: str, *, lenient: bool) -> 'TpyType':
    """Shared canonicalization for Send[inner] / Sync[inner].

    - distributes over unions and optionals (None stays unwrapped)
    - idempotent; both markers stack with Send canonically outermost
    - persistent for erased/type-param inner (assertion fires per value at
      conversion sites)
    - otherwise a static assertion: returns inner when the trait holds,
      raises MarkerAssertionError when not (lenient callers keep the
      wrapper instead, so conversion sites reject every incoming value)
    """
    is_send_marker = trait == "Send"
    if isinstance(inner, OptionalType):
        return inner.with_inner(_make_marker(inner.inner, trait, lenient=lenient))
    if isinstance(inner, UnionType):
        return make_union(*(
            m if isinstance(m, VoidType) else _make_marker(m, trait, lenient=lenient)
            for m in inner.members
        ))
    if isinstance(inner, CallableType) and inner.is_template:
        raise MarkerAssertionError(
            f"{trait}[Fn[...]] is not supported -- Fn lowers to a template "
            f"parameter; constrain it with a generic bound instead "
            f"(e.g. `def f[F: {trait}](fn: F)`)"
        )
    if is_send_marker:
        if isinstance(inner, SendType):
            return inner
        if isinstance(inner, SyncType):
            return SendType(inner)
    else:
        if isinstance(inner, SyncType):
            return inner
        if isinstance(inner, SendType):
            # Canonical order: Send outermost (Sync[Send[T]] -> Send[Sync[T]])
            return SendType(_make_marker(inner.wrapped, "Sync", lenient=lenient))
    if _marker_persists(inner):
        return SendType(inner) if is_send_marker else SyncType(inner)
    holds = inner.is_send() if is_send_marker else inner.is_sync()
    if holds:
        return inner
    if lenient:
        return SendType(inner) if is_send_marker else SyncType(inner)
    raise MarkerAssertionError(
        f"`{trait}[{inner}]`: '{inner}' is not {trait}"
    )


@dataclass(frozen=True)
class FrameSlot:
    """One captured-state slot of a FrameType.

    `send`/`sync` are the slot's storage-shape-aware traits -- a borrow-form
    slot (T&, raw pointer, view of caller storage) is non-Send regardless of
    T; an owned slot follows T's own traits. `type` is kept so diagnostics
    can name the offending slot; it may be None for synthesized slots.
    """
    name: str
    send: bool
    sync: bool
    type: Optional['TpyType'] = None


@dataclass(frozen=True)
class FrameType(TpyType):
    """Captured-state structural form behind coroutines, generators,
    lambdas, and nested closures (docs/SEND_SYNC_DESIGN.md OQ3).

    Internal to sema -- never written by users and never emitted; the C++
    shape is the codegen frame struct / lambda capture. Slots reflect the
    storage form actually emitted, conservatively: any captured state that
    sema cannot classify sets `unclassified`, which forces non-Send and
    non-Sync (relaxations are individual, tested decisions).

    Sub-frames (awaited coroutines, delegated generators) are not slots
    here; they live as FunctionInfo.frame_subframes and are ANDed in by
    sema/frame_traits.py's recursive walk.
    """
    slots: tuple[FrameSlot, ...]
    kind: str  # "coroutine" | "generator" | "closure"
    unclassified: bool = False

    def is_send(self) -> bool:
        return not self.unclassified and all(s.send for s in self.slots)

    def is_sync(self) -> bool:
        return not self.unclassified and all(s.sync for s in self.slots)

    def is_value_type(self) -> bool:
        return False

    def to_cpp(self) -> str:
        raise RuntimeError("FrameType has no C++ spelling; it mirrors the "
                           "codegen-emitted frame struct")

    def __str__(self) -> str:
        return f"<{self.kind} frame: {len(self.slots)} slots>"


def make_send_marker(inner: 'TpyType', *, lenient: bool = False) -> 'TpyType':
    """Canonicalize Send[inner]; see _make_marker."""
    return _make_marker(inner, "Send", lenient=lenient)


def make_sync_marker(inner: 'TpyType', *, lenient: bool = False) -> 'TpyType':
    """Canonicalize Sync[inner]; see _make_marker."""
    return _make_marker(inner, "Sync", lenient=lenient)


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
        if isinstance(self.wrapped, TypeParamRef):
            # Not `const T&` -- see TypeParamRef.to_cpp_const_param.
            return self.wrapped.to_cpp_const_param(name)
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

    def is_movable(self) -> bool:
        return self.wrapped.is_movable()

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



class ResultPosition(Enum):
    SYNC_CALL = "sync_call"
    ASYNC_PAYLOAD = "async_payload"
    ERASED_CALLABLE = "erased_callable"


class ResultRepresentation(Enum):
    """Existing reader decisions, not ownership or contained-borrow facts.

    STORAGE only selects the async storage path or a negative sync-reference
    answer. It does not prove that the result is fresh or contains no borrows.
    """
    STORAGE = "storage"
    CPP_REFERENCE = "cpp_reference"
    ASYNC_POINTER = "async_pointer"
    ASYNC_TRAIT = "async_trait"
    ERASED_VOID = "erased_void"
    ERASED_DECLARED_TYPE = "erased_declared_type"


def returns_cpp_reference_shape(ret_type: TpyType | None) -> bool:
    """The SHAPE half of the sync call convention, callee facts aside.

    Separate from the classifier because a caller can hold only a composed
    return type -- a generic record's re-resolved signature carries the raw
    `T`, which answers False here for a substitution that is in fact a
    reference -- and must get the same answer the SYNC_CALL arm computes.
    """
    rt = unwrap_ref_type(ret_type)
    return (rt is not None and not rt.is_value_type()
            and not isinstance(rt, (TypeParamRef, OwnType, OptionalType, UnionType))
            and not is_protocol_type(rt))


def classify_result_representation(
        ret_type: TpyType | None, *, position: ResultPosition,
        fi: FunctionInfo | None = None) -> ResultRepresentation:
    """Read the current return convention at one specific consuming position.

    Normalization is position-specific: erasure renders the declared type
    verbatim, sync strips Ref only, and async also strips outer markers and
    readonly. Keep render-time type/permission handling at the consumer.
    """
    if position is ResultPosition.ERASED_CALLABLE:
        return (ResultRepresentation.ERASED_VOID if isinstance(ret_type, VoidType)
                else ResultRepresentation.ERASED_DECLARED_TYPE)
    if position is ResultPosition.SYNC_CALL:
        if fi is None or fi.is_constructor:
            return ResultRepresentation.STORAGE
        # Free native declarations do not establish the C++ reference ABI.
        if fi.is_native_import and not fi.is_method:
            return ResultRepresentation.STORAGE
        return (ResultRepresentation.CPP_REFERENCE
                if returns_cpp_reference_shape(ret_type)
                else ResultRepresentation.STORAGE)
    if position is ResultPosition.ASYNC_PAYLOAD:
        rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret_type)))
              if ret_type is not None else None)
        if rt is None or isinstance(rt, (VoidType, OwnType)):
            return ResultRepresentation.STORAGE
        if isinstance(rt, TypeParamRef):
            return ResultRepresentation.ASYNC_TRAIT
        if isinstance(rt, OptionalType):
            return (ResultRepresentation.ASYNC_POINTER if rt.uses_pointer_repr()
                    else ResultRepresentation.STORAGE)
        if (isinstance(rt, UnionType) or rt.is_value_type()
                or is_protocol_type(rt) or rt.needs_wrapper()):
            return ResultRepresentation.STORAGE
        return ResultRepresentation.ASYNC_POINTER
    raise AssertionError(f"Unknown result position: {position}")


def is_open_type_param_return(ret: 'TpyType') -> bool:
    """A declared return that is a bare, still-unresolved type parameter.

    Such a return hands back whatever the substituted type's own convention
    gives -- a reference at every reference-type instantiation, a value at the
    rest -- so every reader asking "could this return borrow" must answer yes
    for it. One home for the question so the borrow-capability readers cannot
    drift apart; the RENDER reader (`call_returns_cpp_ref`) deliberately
    answers the opposite and is not one of them.
    """
    return isinstance(unwrap_ref_type(unwrap_readonly(ret)), TypeParamRef)


def is_ref_type(t: 'TpyType') -> bool:
    """Return True if t is a RefType."""
    return isinstance(t, RefType)


def param_has_mutable_borrow_surface(t: 'TpyType') -> bool:
    """True when the param's C++ shape contains a non-const borrowed surface
    that mutation can flow through.

    Used by Phase-2 mutation propagation (`sema/calls.py`) to decide whether
    a callee param participates in call-edge recording, and by const-inference
    gating to decide whether a param is a candidate for the const spelling.

    Includes: ordinary non-value borrowed params (`T&`), pointer-repr
    `Optional[T]` (`T*`), and `tuple[...]` whose elements recursively expose
    a mutable borrow surface.

    Excludes: `ReadonlyType`, `Own[T]`, value-only tuples, and tuple slots
    containing `TypeParamRef` -- the last because mutation through a generic
    slot can't be statically determined and must default to mutable. Explicit
    `readonly[]` annotation remains the opt-in path for those.
    """
    if isinstance(t, ReadonlyType):
        return False
    if isinstance(t, OwnType):
        return False
    if isinstance(t, RefType):
        return param_has_mutable_borrow_surface(t.wrapped)
    if isinstance(t, OptionalType):
        return t.uses_pointer_repr()
    if isinstance(t, TupleType):
        for et in t.element_types:
            # Look through Ref/Readonly wrapping to see the slot's effective
            # shape -- tuple element types are often Ref[T] in non-value
            # contexts, and we need to detect generic-slot TypeParamRefs
            # regardless of that wrapping.
            et_inner = unwrap_readonly(unwrap_ref_type(et))
            if isinstance(et_inner, TypeParamRef):
                # Generic slot: can't reason about mutation statically.
                return False
            if param_has_mutable_borrow_surface(et):
                return True
            if not et.is_value_type() and not isinstance(et, OwnType):
                # Plain non-value borrow slot (T&) is mutable by default.
                return True
        return False
    # Top-level TypeParamRef: defer to is_value_type. Unbounded T behaves
    # as a borrow surface (call-edge recording must still happen so
    # return_borrows_from propagates through generic forwarders). Every
    # reference type is a mutable-borrow surface (is_ref_param is now just
    # `not is_value_type()`).
    return not t.is_value_type()


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
class InteriorMutableType(TpyType):
    """Transient field-declaration marker for `unsafe_interior_mutable[T]`.

    Marks a field as outside its owning object's readonly boundary: readonly
    does not propagate into the field's pointee and mutating *through* the
    field does not demote the receiver (reassigning the slot still does --
    that is enforced on the receiver, not the field). The escape hatch for
    refcount-style hidden bookkeeping (e.g. Rc's `_cell`); the C++ analog is
    a `mutable` member reached through a raw pointer.

    Like AutoReadonlyType/AutoOwnType this never survives to codegen: field
    registration strips it into `FieldInfo.is_interior_mutable` and stores
    the unwrapped type. A surviving node (used outside a field annotation)
    raises rather than emitting silently wrong C++.
    """
    wrapped: TpyType

    def to_cpp(self) -> str:
        raise RuntimeError(
            "unsafe_interior_mutable[...] is only valid on a class field declaration"
        )

    def is_value_type(self) -> bool:
        return self.wrapped.is_value_type()

    def __str__(self) -> str:
        return f"unsafe_interior_mutable[{self.wrapped}]"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return InteriorMutableType(types[0])


@dataclass(frozen=True)
class _TypeModifierWrapper(TpyType):
    """Shared boilerplate for thin annotation wrappers (Final[T], ClassVar[T]).

    These are stripped during sema; codegen never sees them. All trait
    queries pass through to the inner type. Subclasses override `__str__`
    and `with_inner_types` to round-trip the right wrapper class.
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

    def is_movable(self) -> bool:
        return self.wrapped.is_movable()

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.wrapped,)


@dataclass(frozen=True)
class FinalType(_TypeModifierWrapper):
    """Final[T] -- immutable binding (PEP 591). Sema tracks finality via
    `is_final` on the VarDecl node and `final_globals` in SemanticContext;
    on class-body fields it routes to `RecordInfo.class_constants`.
    """
    def __str__(self) -> str:
        return f"Final[{self.wrapped}]"

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return FinalType(types[0])


def unwrap_final(typ: 'TpyType') -> 'TpyType':
    """Strip FinalType wrapper if present, returning the inner type."""
    if isinstance(typ, FinalType):
        return typ.wrapped
    return typ


def try_unwrap_class_constant(typ: 'TpyType') -> 'tuple[TpyType, bool] | None':
    """Recognize class-body Final/ClassVar annotations and return (inner, is_final).

    Returns None when `typ` is neither a class-constant annotation. PEP 591
    treats `Final[T] = value` in a class body as implicit-ClassVar, and
    `ClassVar[Final[T]] = value` is the explicit alias.
    """
    if isinstance(typ, FinalType):
        return typ.wrapped, True
    if isinstance(typ, ClassVarType):
        wrapped = typ.wrapped
        if isinstance(wrapped, FinalType):
            return wrapped.wrapped, True
        return wrapped, False
    return None


@dataclass(frozen=True)
class ClassVarType(_TypeModifierWrapper):
    """ClassVar[T] (PEP 526) -- class-scoped storage, not an instance field.
    Only valid in class-body annotations; v1 errors with a Phase-7 hint.
    """
    def __str__(self) -> str:
        return f"ClassVar[{self.wrapped}]"

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return ClassVarType(types[0])


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
    """True for concrete float types (float, float32). Excludes FloatLiteralType."""
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
    """True for fundamental C++ scalar types: Bool, char, FixedInt, Float, float32.
    Register-sized, trivially copyable, no heap. Excludes BigInt (heap) and StrView (internal pointer)."""
    if t is None:
        return False
    from .type_def_registry import is_fixed_int_type, is_bool_type, is_char_type
    return is_fixed_int_type(t) or is_float_type(t) or is_bool_type(t) or is_char_type(t)


def is_bufferless_scalar(t: 'TpyType | None') -> bool:
    """A value no view or reference can point into: a number (literal or
    not), `bool`, `char`, enum or None."""
    if t is None:
        return False
    from .type_def_registry import is_char_type, is_enum_type
    t = unwrap_qualifiers(t)
    if isinstance(t, LiteralType):
        t = t.base_type
    return (is_numeric_type(t) or is_char_type(t) or is_enum_type(t)
            or isinstance(t, (NoneType, IntLiteralType, FloatLiteralType)))


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


def is_final_allowed_inner(typ: 'TpyType') -> bool:
    """Inner-type allow-list for Final[T] annotations -- shared between
    module-level Final globals and class-body class constants.

    Constexpr-eligible primitives + tuple. BigInt is not allowed (not
    constexpr-constructible).
    """
    from .type_def_registry import is_char_type, is_str_view_type
    return (is_numeric_type(typ) or is_char_type(typ)
            or is_str_view_type(typ) or isinstance(typ, TupleType))


def is_classvar_allowed_inner(typ: 'TpyType') -> bool:
    """Inner-type allow-list for mutable `ClassVar[T] = value`.

    Tighter than `is_final_allowed_inner` because mutation is permitted:
    StrView is excluded (a write `C.X = make_string()` would store a view
    into a temporary's storage and dangle). Tuples are admitted only when
    every element is itself ClassVar-allowed -- so `tuple[int32, StrView]`
    is rejected.
    """
    from .type_def_registry import is_char_type
    if is_numeric_type(typ) or is_char_type(typ):
        return True
    if isinstance(typ, TupleType):
        return all(is_classvar_allowed_inner(e) for e in typ.element_types)
    return False


FINAL_INNER_TYPE_ERROR = (
    "only primitive types (int, float, bool, str, StrView, char, IntN) "
    "and tuple are allowed"
)


CLASSVAR_INNER_TYPE_ERROR = (
    "only primitive types (int, float, bool, char, IntN) and tuple are "
    "allowed; StrView is rejected because mutation can store a view into "
    "a temporary -- use `Final[StrView]` for read-only string constants"
)


C_ABI_TYPE_ERROR = "is not representable in the C ABI"


def is_c_abi_allowed(typ: 'TpyType', *, is_return: bool = False) -> bool:
    """Allow-list for types tpyc renders into an `extern "C"` signature.

    The gate exists because a C caller has to be able to spell the
    declaration: anything outside this set either emits a C++ type no C
    header can express (`std::vector&`, `::tpy::BigInt`, a `T&`
    reference) or is ABI-incompatible while still compiling, which is a
    silent break rather than a build error. `None` is admitted in return
    position only -- that is `void`.
    """
    from .type_def_registry import (is_fixed_int_type, is_bool_type,
                                    is_char_type, is_float_category,
                                    is_enum_type, enum_info_of)
    t = unwrap_readonly(typ)
    if isinstance(t, OwnType) and t.wrapped.is_value_type():
        # Own selects the owned C++ form over the borrowed one; on a value
        # type every form this gate admits is the same spelling, so the
        # wrapper says nothing about the ABI -- as `readonly` does not.
        # Over a reference type Own is `T&&` (and forces the value form of an
        # Optional), which is why the unwrap stops at value types.
        t = unwrap_readonly(t.wrapped)
    if isinstance(t, PtrType):
        # Deliberately unconditional in the pointee: a pointer is an opaque
        # handle at the ABI, whatever it addresses. The C++ spelling of the
        # pointee still appears in the declaration, so a C consumer writes
        # `void*` for anything it cannot name. The array form of
        # native_global checks the pointee instead, because there the
        # pointee IS the emitted element type rather than a handle.
        return True
    if isinstance(t, OptionalType) and t.uses_pointer_repr():
        # A pointer-repr Optional IS the pointer -- the nullable-handle idiom
        # (`-> Widget | None` emits the same type as `Ptr[Widget]`), admitted
        # for the same reason and at both positions. A value inner stays out
        # (`int32 | None` is `std::optional`), and a union stays out whatever
        # its repr: that one is a variant, not a pointer.
        return True
    if (is_fixed_int_type(t) or is_bool_type(t)
            or is_char_type(t) or is_float_category(t)):
        return True
    if is_enum_type(t):
        # A @native enum names a real C enum; a TPy-declared one lowers to
        # a namespaced C++ `enum class` with no C spelling.
        info = enum_info_of(t)
        return info is not None and info.is_native
    # `-> None` resolves to VoidType; NoneType is the annotation spelling.
    return is_return and isinstance(t, (VoidType, NoneType))


def is_c_abi_element_allowed(typ: 'TpyType') -> bool:
    """Allow-list for the ELEMENT type of a C-linkage `native_global` array.

    That form emits `extern "C" <element> <name>[];`, so the element type is
    NAMED rather than passed. A `@native(binding="C")` struct is admitted
    here for the reason a `@native` enum is admitted anywhere -- the author's
    own C header owns the spelling -- while the by-value gate keeps rejecting
    it, because a record in a signature emits a C++ reference.
    """
    from .type_def_registry import record_info_of
    t = unwrap_readonly(typ)
    info = record_info_of(t)
    if info is not None and info.is_native_c:
        return True
    return is_c_abi_allowed(t)


def c_abi_type_hint(typ: 'TpyType', *, is_element: bool = False) -> str:
    """Remedy clause naming the manual spelling for a rejected type.

    Generic advice ("use Ptr[T]") is useless for the two families users
    actually hit, so int and the str/buffer families get their own.
    `is_element` is the array-global element position, where the remedy for
    a record is to bind it to the C declaration rather than to add a
    pointer the annotation already has.
    """
    from .type_def_registry import is_big_int_type, is_enum_type
    t = unwrap_readonly(typ)
    if is_big_int_type(t):
        return "use a fixed-width integer type (int32, int64, ...)"
    if is_any_str_type(t):
        return ("use Ptr[readonly[uint8]] and convert with "
                "tpy.unsafe.unsafe_str_from_cstr() / unsafe_cstr()")
    if is_byte_buffer_c_abi(t):
        return "use Ptr[readonly[uint8]] plus an explicit length parameter"
    if is_sequence_c_abi(t):
        return "use Ptr[T] plus an explicit length parameter"
    if is_enum_type(t):
        return "declare the enum @native so it names a real C enum"
    if isinstance(t, NominalType) and t.is_user_record:
        if is_element:
            return ('declare the class @native(binding="C") so it names a '
                    'struct the C side already declares')
        # Steering a by-value C struct at Ptr[S] would be actively wrong:
        # extern "C" does not mangle, so the call links and the callee reads
        # a struct where a pointer was passed.
        return ("pass it as Ptr[T] -- a struct crosses a C boundary by "
                "pointer, and a by-value struct parameter cannot be "
                "expressed there")
    return "pass it as Ptr[T]"


def is_byte_buffer_c_abi(t: 'TpyType') -> bool:
    """Byte buffers -- their C spelling is `const uint8_t*` plus a length."""
    from .type_def_registry import is_bytes_type, is_bytearray_type
    return is_bytes_type(t) or is_bytearray_type(t)


def is_sequence_c_abi(t: 'TpyType') -> bool:
    """Element sequences -- `T*` plus a length on the C side."""
    from .type_def_registry import is_list, is_span, is_array
    return is_list(t) or is_span(t) or is_array(t)


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


def is_own_pointer_repr_optional(t: 'TpyType') -> bool:
    """True for `Own[OptionalType[T_ref]]` where the inner Optional uses
    pointer representation (`T*` at borrow boundaries, `optional<T>` at
    storage). Used by codegen sites that need to bridge between the
    storage-form ABI of an Own-Optional param and the pointer-form local
    consumers expect. Peels `ReadonlyType` first.
    """
    inner = unwrap_readonly(t)
    return (isinstance(inner, OwnType)
            and isinstance(inner.wrapped, OptionalType)
            and inner.wrapped.uses_pointer_repr())


def own_tuple_target(expected: 'TpyType') -> 'TupleType | None':
    """Return the elem-capture-shaped TupleType for a tuple-target type.

    Both `tuple[Own[T], ...]` and `Own[tuple[T, ...]]` imply per-element
    move semantics: the value tuple owns its elements, so non-value
    elements need ownership transfer at construction. For `Own[Tuple]`
    we synthesize an Own-wrapped inner tuple so the per-element `Own[T]`
    handling (sema's `check_tuple_literal_members` and
    `_annotate_tuple_elem_capture`) covers both shapes uniformly.
    """
    if isinstance(expected, TupleType):
        return expected
    if isinstance(expected, OwnType) and isinstance(expected.wrapped, TupleType):
        inner = expected.wrapped
        if all(et.is_value_type() or isinstance(et, OwnType)
               for et in inner.element_types):
            return inner
        return TupleType(tuple(
            et if (et.is_value_type() or isinstance(et, OwnType)) else OwnType(et)
            for et in inner.element_types
        ))
    return None


def global_binds_by_reference(t: 'TpyType | None') -> bool:
    """A module global whose binding is a POINTER SLOT (a reference type) or
    a tuple of pointer slots (`TupleType.takes_borrow_slot_as_global`): bound
    once at module init, so rebinding it from a function body is refused --
    a slot aimed at a function's storage would dangle. The tuple form has to
    be named here because `is_value_type()` alone calls it a value."""
    if t is None:
        return False
    t = unwrap_readonly(t)
    if isinstance(t, TupleType):
        return t.takes_borrow_slot_as_global()
    return not t.is_value_type()


def collapse_tuple_own_elements(var_type: 'TpyType') -> 'TpyType':
    """Collapse per-element `Own[T]` -> `T` in a tuple type.

    A reassigned tuple local takes one borrow-form C++ shape
    (`std::tuple<..., T*>`) across all bindings so an alias rebind aliases
    the source like CPython; a per-element `Own[T]` slot would otherwise
    keep it on owning storage form and copy the alias. Collapsing to the
    unified borrow type lets sema and codegen agree on the borrow-slot path.

    A MIXED tuple (`tuple[Own[A], B]`) is left alone: it HAS no unified form
    to collapse onto. Its owned element must stay by value (a pointer would
    have nothing to point at but a materialized slot, which then copies the
    borrowed element), so its one shape is the mixed render
    `std::tuple<A, B*>` -- which is what the retained `Own` markers spell.
    """
    inner = unwrap_readonly(var_type)
    if isinstance(inner, OptionalType):
        collapsed_inner = collapse_tuple_own_elements(inner.inner)
        if collapsed_inner is inner.inner:
            return var_type
        rewrapped = inner.with_inner(collapsed_inner)
        return ReadonlyType(rewrapped) if isinstance(var_type, ReadonlyType) else rewrapped
    if not isinstance(inner, TupleType):
        return var_type
    if not any(isinstance(et, OwnType) for et in inner.element_types):
        return var_type
    if inner.is_mixed_own():
        return var_type
    collapsed = TupleType(tuple(
        et.wrapped if isinstance(et, OwnType) else et
        for et in inner.element_types))
    return ReadonlyType(collapsed) if isinstance(var_type, ReadonlyType) else collapsed


def owned_tuple_storage_type(typ: 'TpyType') -> 'TpyType':
    """The fully-owned storage form of a tuple type: every element held by
    value, with both the ownership marker (`Own[T]`) and the borrow marker
    (`Ref[T]`) removed.

    Stronger than `collapse_tuple_own_elements`, which only drops `Own` and so
    can leave a `Ref` element behind -- a type that still describes a borrow and
    will not match an owning slot. This is the form `copy()` produces, and the
    only tuple form an owning slot can hold without a further lift.
    """
    inner = unwrap_readonly(typ)
    if not isinstance(inner, TupleType):
        return typ
    owned = TupleType(tuple(
        unwrap_ref_type(unwrap_own(et)) for et in inner.element_types))
    if owned == inner:
        return typ
    return ReadonlyType(owned) if isinstance(typ, ReadonlyType) else owned


@dataclass(frozen=True)
class NoneType(TpyType):
    """Type of the `None` literal at value-bearing positions (generic
    type args, parameter slots). Distinct from `VoidType` which lowers
    to C++ `void` at function-return shape. The parser routes `None`
    annotations to one or the other based on syntactic position (see
    `parse/type_resolver.py`'s `is_type_arg` plumbing).
    """

    def to_cpp(self) -> str:
        # std::monostate is the canonical "unit type" in C++ -- already
        # used by the runtime for variant None-members and printing. A
        # `Foo[None]` lowering to `Foo<std::monostate>` is uniform with
        # how nullable-union None members are spelled.
        return "std::monostate"

    def __str__(self) -> str:
        return "None"

    def is_value_type(self) -> bool:
        return True


@dataclass(frozen=True)
class AnyType(TpyType):
    """`typing.Any`: type-erased value cell holding any copyable concrete type.

    Backed at runtime by `tpy::Any` (std::any + per-type ops table). See
    `docs/ANY_TYPE_DESIGN.md` for the full design and composition rules.
    """

    def to_cpp(self) -> str:
        return "::tpy::Any"

    def __str__(self) -> str:
        return "Any"

    def is_value_type(self) -> bool:
        # Owns its contents and copies them on assignment (via std::any's
        # internal manager). Move semantics auto-std::move at last use.
        return True

    def is_expensive_copy(self) -> bool:
        return True


def contains_type_kind_param(t: TpyType) -> bool:
    """Like contains_type_param but counts only TYPE-kind params, not INT
    (const) params. Movability never depends on a const N -- only on element
    types -- so a field like `UninitArrayStorage[Item, N]` (element resolved,
    N still a param) is movability-determinable and must NOT be skipped."""
    if isinstance(t, TypeParamRef):
        return t.kind != TypeParamKind.INT
    if isinstance(t, NominalType) and t.type_args:
        return any(contains_type_kind_param(a) for a in t.type_args
                   if isinstance(a, TpyType))
    return any(contains_type_kind_param(inner) for inner in t.inner_types())


def contains_type_param(
    t: TpyType, names: Optional[set[str]] = None,
) -> bool:
    """Return True if the type contains any TypeParamRef (recursively).

    If `names` is given, only TypeParamRefs whose name is in `names` count.
    NominalType is walked via `type_args` directly (not `inner_types()`)
    so INT-kind TypeParamRefs in `Array[T, N: int]` count.
    """
    if isinstance(t, TypeParamRef):
        return names is None or t.name in names
    if isinstance(t, NominalType) and t.type_args:
        return any(
            contains_type_param(a, names)
            for a in t.type_args if isinstance(a, TpyType)
        )
    return any(contains_type_param(inner, names) for inner in t.inner_types())


def type_param_names(t: TpyType) -> frozenset[str]:
    """The names of every TypeParamRef in the type (recursively), walked the
    way `contains_type_param` walks."""
    if isinstance(t, TypeParamRef):
        return frozenset({t.name})
    if isinstance(t, NominalType) and t.type_args:
        inner = [a for a in t.type_args if isinstance(a, TpyType)]
    else:
        inner = list(t.inner_types())
    return frozenset().union(*(type_param_names(i) for i in inner))


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

    Pointer-inner collapse
    ----------------------
    `OptionalType(PtrType(T))` collapses to `PtrType(T)` at construction time
    (and `OptionalType(ReadonlyType(PtrType(T)))` to `ReadonlyType(PtrType(T))`).
    Both spellings already lower to `T*` / `const T*` in C++, both are nullable,
    and `is None` narrowing handles `Ptr[T]` directly (`sema/narrowing.py`'s
    `condition_ptr_null_facts`). Representing them as two distinct TPy types
    forced every boundary (assignment, call, generic substitution, container
    element) to bridge the duplication with its own coercion rule. The
    collapse makes `Ptr[T] | None` unrepresentable: there is one type for one
    C++ shape.

    Consequence: `OptionalType(...)` may return a `PtrType` instead of an
    `OptionalType`. Any post-construction code that dot-accesses `.inner`,
    `.force_pointer_repr`, or calls `.uses_pointer_repr()` on the result
    without an `isinstance(t, OptionalType)` guard will crash or misbehave
    when the collapse fires. This includes callers in sema substitution,
    codegen helpers, and tests.

    force_pointer_repr invariants
    -----------------------------
    The flag locks codegen to T* even when the concrete inner is a value type.
    It is set by `sema/type_ops.py::substitute_type_params` when a generic
    template committed to T* ABI (unbounded TypeParamRef param/return) is
    instantiated with a value-typed concrete T (e.g. Container[int32].get()
    must return int32_t*, not std::optional<int32_t>, to match the emitted
    C++ template's signature).

    The flag is load-bearing beyond the call site: once set on a resolved
    return type, it flows through sema into the receiver variable's type,
    and the var-decl lowering reads uses_pointer_repr() to pick T* storage
    and register the name in pointer_locals. Silently
    dropping the flag during any structural transform miscompiles any
    `v = container.get()` pattern.

    When the inner is already pointer-shaped (PtrType or ReadonlyType(PtrType)),
    the collapse rule takes precedence: the returned PtrType is intrinsically
    T*-shaped, so `force_pointer_repr` is redundant and silently dropped
    (calling `OptionalType(PtrType(T), force_pointer_repr=True)` returns
    `PtrType(T)` with no record of the flag).

    Three rules for constructing OptionalType (when the inner is not pointer-
    shaped, otherwise see the collapse rule above):

    1. Fresh construction (no pre-existing Optional on the input side):
       plain `OptionalType(inner)`. Flag defaults to False.
    2. Transforming an existing Optional (resolve, substitute, rewrap inner):
       use `self.with_inner(new_inner)` -- preserves the flag automatically.
    3. Deliberately crossing an ABI boundary (builtin generic whose hand-written
       C++ returns std::optional despite a T*-committed template signature):
       call the module-level `strip_template_repr(t)`. The name flags intent.

    Removing the flag outright is not a small refactor: it would require a
    structural replacement for the provenance it tracks (e.g. two classes,
    TPy-level monomorphization, or conditional-ABI templates). Deferred to
    the THIR/MIR migration.
    """
    inner: TpyType
    # Excluded from eq/hash: representation context, not semantic identity --
    # it pins uses_pointer_repr() to True over a value inner without splitting
    # one C++ shape into two distinct TPy types. See the class docstring's
    # force_pointer_repr invariants.
    force_pointer_repr: bool = field(default=False, compare=False, hash=False)

    def __new__(cls, inner: 'TpyType | None' = None, force_pointer_repr: bool = False):
        # Collapse: Ptr[T] is already T* and nullable, so an extra Optional
        # wrapper would yield std::optional<T*> (redundant) and split one C++
        # shape into two distinct TPy types. See class docstring for context.
        # `inner=None` default supports copy.deepcopy / pickle reconstruction
        # via __newobj__(cls) -- the state is populated separately afterward.
        if inner is not None:
            if isinstance(inner, PtrType):
                return inner
            if isinstance(inner, ReadonlyType) and isinstance(inner.wrapped, PtrType):
                return inner
        return super().__new__(cls)

    def to_cpp(self) -> str:
        return f"std::optional<{self.inner.to_cpp()}>"

    def is_value_type(self) -> bool:
        return self.inner.is_value_type()

    def is_send(self) -> bool:
        return self.inner.is_send()

    def is_sync(self) -> bool:
        return self.inner.is_sync()

    def is_movable(self) -> bool:
        # T* (pointer repr) always moves; std::optional<T> moves iff T does.
        return True if self.uses_pointer_repr() else self.inner.is_movable()

    def uses_pointer_repr(self) -> bool:
        """Whether this Optional uses T* (pointer) repr instead of std::optional<T>.

        True when inner is not a value type: records, unbounded TypeParamRef.
        False for value types (int32, bool) and ValueType-bounded TypeParamRef.
        force_pointer_repr overrides: set during generic substitution when the
        template used T* but the concrete inner is a value type (e.g. Container[int32]
        where the template committed to T* for all instantiations).
        """
        if self.force_pointer_repr:
            return True
        return not self.inner.is_value_type()

    def uses_generic_param_trait(self) -> bool:
        """Whether the PARAMETER form of this Optional is left to the runtime
        trait (`opt_param_t<T>`) instead of being spelled here.

        True only over an OPEN type param: the template is emitted once, so the
        form has to be decided at instantiation -- `std::optional<T>` for a
        value T (what the monomorphic twin takes) and `T*` for a reference one.
        A concrete or ValueType-bounded inner already knows its form.

        Parameters only. The return position stays committed to `T*` (see
        force_pointer_repr): an optional return cannot alias a field.
        """
        return (isinstance(self.inner, TypeParamRef)
                and self.inner.kind == TypeParamKind.TYPE
                and not self.inner.is_value_type())

    def wraps_pointer_repr_tuple(self) -> bool:
        """True when this Optional wraps a tuple that has a pointer-repr element.

        `tuple[..., Box] | None` is a value type (tuples always are), so
        `uses_pointer_repr()` is False and it stays `std::optional<...>`. But the
        inner tuple has a borrow form (`std::tuple<..., T*>`) distinct from its
        storage form, so a nullable local of this type takes the borrow-form
        inner shape (`std::optional<std::tuple<..., T*>>`) and aliases reference
        elements on rebind rather than copying them.
        """
        return isinstance(self.inner, TupleType) and self.inner.has_pointer_repr_element()

    def value_form(self) -> 'ValueForm':
        # Keyed on the C++ shape (uses_pointer_repr), NOT is_value_type: a
        # force_pointer_repr Optional over a value inner is value-semantics but
        # lowers to T* / std::optional<T>, so it is PTR_OPTIONAL. A non-pointer
        # (value-inner) Optional is a plain value.
        return ValueForm.PTR_OPTIONAL if self.uses_pointer_repr() else ValueForm.VALUE

    def to_cpp_return(self) -> str:
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}*"
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        if self.uses_pointer_repr():
            return f"const {self.inner.to_cpp()}*"
        return self.to_cpp()

    def to_cpp_param_type(self) -> str:
        if self.uses_generic_param_trait():
            return f"::tpy::opt_param_t<{self.inner.to_cpp()}>"
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}*"
        fam = view_family_for_type(self.inner)
        if fam is not None:
            return f"std::optional<{fam.view_type.to_cpp()}>"
        return self.to_cpp()

    def to_cpp_param(self, name: str) -> str:
        if self.uses_generic_param_trait():
            return f"::tpy::opt_param_t<{self.inner.to_cpp()}> {name}"
        if self.uses_pointer_repr():
            return f"{self.inner.to_cpp()}* {name}"
        fam = view_family_for_type(self.inner)
        if fam is not None:
            return f"std::optional<{fam.view_type.to_cpp()}> {name}"
        return f"{self.to_cpp()} {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.uses_generic_param_trait():
            return f"::tpy::opt_cparam_t<{self.inner.to_cpp()}> {name}"
        if self.uses_pointer_repr():
            return f"const {self.inner.to_cpp()}* {name}"
        fam = view_family_for_type(self.inner)
        if fam is not None:
            return f"std::optional<{fam.view_type.to_cpp()}> {name}"
        return f"{self.to_cpp()} {name}"

    def param_needs_copy_for_reassign(self) -> bool:
        # A borrow-form view param (str -> string_view, bytes -> span) aliases
        # caller storage; reassigning it in the body needs an owned copy.
        return view_family_for_type(self.inner) is not None

    def is_ref_param(self) -> bool:
        # Optional params are T* (pointer), not T& (reference)
        return False

    def get_element_type(self) -> Optional['TpyType']:
        return self.inner.get_element_type()

    def __str__(self) -> str:
        return f"{self.inner} | None"

    def inner_types(self) -> tuple['TpyType', ...]:
        return (self.inner,)

    def with_inner(self, new_inner: 'TpyType') -> 'TpyType':
        """Return a new OptionalType with the same repr commitment and a different inner.

        Use this for any structural transform (resolve, substitute, rewrap) of an
        existing Optional. Preserves force_pointer_repr mechanically so callers
        cannot accidentally drop it.

        Returns `TpyType` (not `OptionalType`) because the pointer-inner collapse
        in `OptionalType.__new__` may return a `PtrType` when `new_inner` is
        pointer-shaped. Callers that need to act on `OptionalType` specifically
        must isinstance-check.
        """
        return OptionalType(new_inner, force_pointer_repr=self.force_pointer_repr)

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return self.with_inner(types[0])


def strip_template_repr(t: TpyType) -> TpyType:
    """Drop force_pointer_repr -- callee's C++ actually uses value repr despite
    the template's T*-committed signature.

    Used only at known ABI boundaries (builtin generic functions like `iter()`
    whose hand-written C++ returns std::optional<T> rather than T*).
    """
    if isinstance(t, OptionalType) and t.force_pointer_repr:
        return OptionalType(t.inner)
    return t


@dataclass(frozen=True)
class RecursiveUnionInfo:
    """Identity of a recursive union alias.

    Recursive aliases (`type Tree = int | list[Tree]`) cannot be plain
    `using` aliases in C++ because the alias name must already be a
    complete type when its own body is parsed. Codegen emits a wrapper
    struct instead, and this record carries the wrapper identity:
    the alias short name, the canonical member tuple, and the defining
    module's name.
    """
    name: str  # alias short name (e.g. "JsonValue")
    full_members: tuple[TpyType, ...]  # canonical wrapper-struct variant ordering
    origin: str  # defining module name


@dataclass(frozen=True)
class AliasRef(TpyType):
    """Explicit forward reference to a type alias.

    Used by the parser for self-references inside a recursive alias body
    (`type Tree = int | list[Tree]` -- the inner `Tree` becomes an
    `AliasRef("Tree", module=...)` because the alias isn't yet in the
    registry when its own body is parsed). Replacing the prior
    "bare NominalType" placeholder makes downstream placeholder
    detection a type-level `isinstance` check instead of a
    name-set membership check against `recursive_union_names`.

    Resolution: callers that need the underlying type use
    `registry.resolve_alias_ref(ref)` -- the single module-aware entry
    point (a bare `get_type_alias(name)` is caller-local and misses a
    cross-module alias used without importing it). The C++ rendering is
    the alias name -- recursive aliases emit a wrapper struct named
    identically.

    `args` carries the type arguments of a *generic* recursive alias
    self-reference (`type Tree[T] = T | list[Tree[T]]` -- the inner
    `Tree[T]`). It is unresolved forward material only: after alias
    registration the finalize step rewrites resolvable `AliasRef(name,
    args)` nodes into `RecursiveAliasInstanceType`, so the semantic
    carrier for a value is never a half-resolved `AliasRef`. Empty for
    non-generic self-references.
    """
    name: str  # alias short name (e.g. "Tree")
    module: Optional[str] = None  # defining module; None for current-module local
    args: tuple['TpyType', ...] = ()  # generic self-ref type args; () for non-generic

    def to_cpp(self) -> str:
        # Recursive-alias wrapper struct shares the alias's short name.
        # Cross-module references go through the per-compilation
        # native_cpp_names map (populated for imports during codegen),
        # which already routes the name to the qualified C++ form.
        base = _native_cpp_names_view().get(self.name, self.name)
        if self.args:
            rendered = ", ".join(a.to_cpp() for a in self.args)
            return f"{base}<{rendered}>"
        return base

    def inner_types(self) -> tuple['TpyType', ...]:
        return self.args

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return AliasRef(self.name, self.module, types)

    # Conservative defaults matching the prior bare-NominalType
    # placeholder behavior. The wrapper struct itself is value-type,
    # but reporting False here keeps `UnionType.is_value_type()` /
    # `uses_pointer_repr()` aggregating exactly as before, so codegen
    # branching (and the `needs_wrapper()` override that already
    # special-cases recursive unions) is unchanged.
    def is_value_type(self) -> bool:
        return False

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return False

    def __str__(self) -> str:
        if self.args:
            return f"{self.name}[{', '.join(str(a) for a in self.args)}]"
        return self.name


# Re-entrancy guard for RecursiveAliasInstanceType.is_value_type. A generic
# recursive alias's body contains a self-reference instance, so naive
# delegation to the substituted union's is_value_type would recurse forever.
# Returning False at the re-entry point mirrors AliasRef.is_value_type (the
# non-generic placeholder's conservative answer) and matches how the
# non-generic UnionType wrapper aggregates -- codegen treats the wrapper as a
# value via needs_wrapper() regardless. Cleared per compilation via
# clear_all_compilation_state.
_evaluating_alias_value: set[tuple] = set()


@dataclass(frozen=True)
class RecursiveAliasInstanceType(TpyType):
    """A use of a generic recursive type alias at a concrete instantiation
    (`Tree[int32]`, or `Tree[T]` inside a generic function/alias body).

    The semantic carrier for a generic recursive alias value. Unlike a
    non-generic recursive alias -- whose use site is an eagerly-expanded
    `UnionType` recognized via `union_wrapper_index` -- a generic alias has
    one C++ template per name and unboundedly many instantiations, so the
    use site is this dedicated nominal-but-union-like type that renders
    `Tree<int32_t>`.

    Identity is `(qname, type_args)`: `qname` is the alias's fully-qualified
    name (defining module + short name), collision-proof across modules that
    define same-short-named aliases. It is also the C++ render key:
    `to_cpp()` looks `qname` up in the per-compilation
    `recursive_alias_cpp_names` map (qualified for imported aliases, absent ->
    bare short for same-module ones). `name` (the local render short, used by
    `_short()` / `__str__`) and `alias_info` (the semantic payload -- body +
    type_params, used to expand alternatives / value-ness) are carried but
    excluded from eq/hash.

    Minted only by sema's finalize pass from the parser's `AliasRef`
    placeholders -- parse-resolve runs against a separate registry and lacks
    the sema `alias_info`. See docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md.
    """
    qname: str
    type_args: tuple['TpyType', ...] = ()
    name: str = field(default="", compare=False, hash=False)
    alias_info: 'TypeAliasInfo' = field(
        default=None, compare=False, hash=False, repr=False)

    def _short(self) -> str:
        return self.name or self.qname.rsplit('.', 1)[-1]

    def _origin(self) -> str:
        return self.qname.rsplit('.', 1)[0] if '.' in self.qname else self.qname

    def substituted_body(self) -> 'TpyType':
        """The alias body with this instance's type args substituted for the
        alias's declared type params. The self-reference inside stays a
        RecursiveAliasInstanceType (substitution only touches TypeParamRefs)."""
        info = self.alias_info
        if info is None:
            return self
        subst = {
            p: a for p, a in zip(info.type_params, self.type_args)
            if isinstance(a, TpyType)
        }
        return substitute_type_params_structural(info.body, subst)

    def alternatives(self) -> tuple['TpyType', ...]:
        """The variant alternatives behind the wrapper, in the wrapper
        struct's std::variant ordering.

        Substitutes each *original* body member positionally rather than
        substituting the whole union -- the latter re-canonicalizes (sorts,
        collapses `X | None` to Optional), which would diverge from the
        template's fixed `std::variant<member0, member1, ...>` ordering."""
        info = self.alias_info
        if info is None or not isinstance(info.body, UnionType):
            return (self.substituted_body(),)
        subst = {
            p: a for p, a in zip(info.type_params, self.type_args)
            if isinstance(a, TpyType)
        }
        return tuple(
            substitute_type_params_structural(m, subst)
            for m in info.body.members
        )

    def wrapper_info(self) -> 'RecursiveUnionInfo | None':
        return RecursiveUnionInfo(
            name=self._short(), full_members=self.alternatives(),
            origin=self._origin(),
        )

    def needs_wrapper(self) -> bool:
        return True

    def to_cpp(self) -> str:
        # Identity-keyed (qname) lookup is collision-proof: a same-module alias
        # is absent from the map and renders bare via _short(); an imported one
        # renders the defining module's qualified wrapper name. Mixing the two
        # in one module (local `Tree` + imported `other.Tree`) stays correct
        # because the key is the qname, not the short name.
        base = _recursive_alias_cpp_names_view().get(self.qname) or self._short()
        if self.type_args:
            rendered = ", ".join(
                a.to_cpp() if isinstance(a, TpyType) else str(a)
                for a in self.type_args
            )
            return f"{base}<{rendered}>"
        return base

    def inner_types(self) -> tuple['TpyType', ...]:
        return tuple(a for a in self.type_args if isinstance(a, TpyType))

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        return RecursiveAliasInstanceType(
            self.qname, types, self.name, self.alias_info)

    def is_value_type(self) -> bool:
        key = (self.qname, self.type_args)
        if key in _evaluating_alias_value:
            return False
        _evaluating_alias_value.add(key)
        try:
            return self.substituted_body().is_value_type()
        finally:
            _evaluating_alias_value.discard(key)

    def is_send(self) -> bool:
        return False

    def is_sync(self) -> bool:
        return False

    def __str__(self) -> str:
        if self.type_args:
            return f"{self._short()}[{', '.join(str(a) for a in self.type_args)}]"
        return self._short()


def recursive_union_alternatives(typ: 'TpyType') -> 'tuple[TpyType, ...] | None':
    """The variant alternatives behind a recursive-union wrapper, in the
    wrapper struct's std::variant ordering, or None if `typ` is not a
    recursive-union wrapper."""
    if isinstance(typ, RecursiveAliasInstanceType):
        return typ.alternatives()
    if isinstance(typ, UnionType):
        info = typ.wrapper_info()
        if info is not None:
            return info.full_members
    return None


@dataclass(frozen=True)
class UnionType(TpyType):
    """Union of multiple types: A | B | C -> std::variant<A, B, C>.

    Members are stored in canonical sorted order for deterministic eq/hash.
    NoneType is always first if present (maps to std::monostate).
    """
    members: tuple[TpyType, ...]

    def wrapper_info(self) -> 'RecursiveUnionInfo | None':
        """Return the wrapper-struct identity if this union needs a C++
        wrapper (recursive alias), else None. Reads the active compiler's
        canonical `union_wrapper_index`; returns None outside a
        compilation context. Looks up by full members and, when None is
        a direct member, by the non-None subset so a narrowed-by-None
        type still resolves to the same wrapper."""
        compiler = get_current_compiler()
        if compiler is None:
            return None
        index = compiler.union_wrapper_index
        if not index:
            return None
        info = index.get(self.members)
        if info is not None:
            return info
        if self.has_none_member():
            non_none = tuple(m for m in self.members if not is_void_like_type(m))
            return index.get(non_none)
        return None

    def needs_wrapper(self) -> bool:
        """True if this union requires a C++ wrapper struct (recursive alias)."""
        return self.wrapper_info() is not None

    def to_cpp(self) -> str:
        compiler = get_current_compiler()
        if compiler is not None:
            alias = compiler.union_alias_names.get(self.members)
            if alias is not None:
                return alias
        cpp_members = [
            "std::monostate" if is_void_like_type(m) else m.to_cpp()
            for m in self.members
        ]
        # Every union spells the TPy type that owns Python's comparison
        # rule -- the bare variant compares the alternative INDEX first,
        # which is wrong for a reference union's container element just as
        # it is for a value union's. One head at every position: the
        # ALTERNATIVES say whether this is the owning or the borrowing form,
        # and a union whose own members are all `Ptr[T]` needs no carve-out
        # because its alternatives already say "borrowed".
        return f"::tpy::Union<{', '.join(cpp_members)}>"

    def has_none_member(self) -> bool:
        return any(is_void_like_type(m) for m in self.members)

    def is_value_type(self) -> bool:
        # Note: recursive union aliases (type Tree = int | list[Tree]) have
        # non-value members but their C++ wrapper struct IS a value type.
        # Codegen handles this via UnionType.needs_wrapper() overrides.
        return all(m.is_value_type() for m in self.members)

    def is_send(self) -> bool:
        return all(m.is_send() for m in self.members)

    def is_sync(self) -> bool:
        return all(m.is_sync() for m in self.members)

    def is_movable(self) -> bool:
        # Pointer-variant members are pointers (always movable); a value
        # variant<...> moves iff every member moves.
        return True if self.uses_pointer_repr() else all(m.is_movable() for m in self.members)

    def uses_pointer_repr(self) -> bool:
        """Whether this union uses pointer-variant repr for params/returns/locals.

        True when any non-None member is not a value type (e.g. Dog | Cat with records).
        Pointer variants use ::tpy::Union<Dog*, Cat*> instead of the
        storage form ::tpy::Union<Dog, Cat>.
        """
        return not self.is_value_type()

    def to_cpp_ptr_variant(self) -> str:
        """Return the borrow-form type: ::tpy::Union<Dog*, Cat*>.

        The TPy type that owns Python's comparison rule AT A BORROW (through
        the pointee, identity when the pointee defines no `__eq__`), and that
        carries the borrow/storage verdict itself rather than leaving the
        runtime to infer it from the alternative shapes.

        Monostate members (None) stay as std::monostate.
        Does not use type aliases (aliases are for value variants only).
        """
        cpp_members = [
            "std::monostate" if is_void_like_type(m)
            else f"{m.to_cpp()}*"
            for m in self.members
        ]
        return f"::tpy::Union<{', '.join(cpp_members)}>"

    def to_cpp_const_ptr_variant(self) -> str:
        """Return the read-borrow form: ::tpy::Union<const Dog*, const Cat*>."""
        cpp_members = [
            "std::monostate" if is_void_like_type(m)
            else f"const {m.to_cpp()}*"
            for m in self.members
        ]
        return f"::tpy::Union<{', '.join(cpp_members)}>"

    def to_cpp_param_type(self) -> str:
        # A recursive-alias wrapper is a single nominal struct, not a
        # pointer-variant; it follows the reference-type param convention
        # (mutable `X&`, const `const X&` via to_cpp_const_param), checked
        # before uses_pointer_repr so a wrapper never renders as a ptr-variant.
        if self.needs_wrapper():
            return f"{self.to_cpp()}&"
        if self.uses_pointer_repr():
            return self.to_cpp_ptr_variant()
        return f"const {self.to_cpp()}&"

    def to_cpp_param(self, name: str) -> str:
        if self.needs_wrapper():
            return f"{self.to_cpp()}& {name}"
        if self.uses_pointer_repr():
            return f"{self.to_cpp_ptr_variant()} {name}"
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_const_param(self, name: str) -> str:
        if self.needs_wrapper():
            return f"const {self.to_cpp()}& {name}"
        if self.uses_pointer_repr():
            # DEEP const: the const belongs on the pointees. On the by-value
            # borrow union itself it would freeze the union and nothing it
            # points at, and a const-bound source could not reach the slot at
            # all.
            return f"{self.to_cpp_const_ptr_variant()} {name}"
        return f"const {self.to_cpp()}& {name}"

    def to_cpp_return(self) -> str:
        # A recursive-alias wrapper is a single nominal struct (not a
        # pointer-variant); it follows the reference-type return convention
        # (`X&`), with Own[X] required for fresh values.
        if self.needs_wrapper():
            return f"{self.to_cpp()}&"
        if self.uses_pointer_repr():
            return self.to_cpp_ptr_variant()
        return self.to_cpp()

    def to_cpp_return_const(self) -> str:
        if self.needs_wrapper():
            return f"const {self.to_cpp()}&"
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
        # Prefer the user-spelled alias name when this union body matches
        # a registered type alias (`Shape = Circle | Rect` -> "Shape"
        # instead of "Circle | Rect"). Falls back to the expanded form
        # outside a compilation context or for unnamed unions.
        compiler = get_current_compiler()
        if compiler is not None:
            display = compiler.union_display_names.get(self.members)
            if display is not None:
                return display
        return self.expanded_str()

    def expanded_str(self) -> str:
        """Always render as `A | B | ...`, ignoring any registered alias name.

        For diagnostics that suggest the structural form as an alternative
        ("Use `A | B` directly") -- using `str()` there would print the
        rejected alias name, contradicting the suggestion.
        """
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
    """Check if a type tree contains an AliasRef self-reference to the given name.

    Walks an alias body where self-references are emitted by the parser
    as `AliasRef` placeholders. Plain NominalTypes are never confused
    with the placeholder.
    """
    if isinstance(typ, AliasRef) and typ.name == name:
        return True
    return any(_contains_self_reference(inner, name) for inner in typ.inner_types())


# validate_recursive_union_paths now lives in tpyc.cycle_detection so it can
# share _is_indirecting_type with the wider cycle walker without an inverted
# typesys -> cycle_detection lazy-import.


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

    def is_movable(self) -> bool:
        return all(t.is_movable() for t in self.element_types)

    def is_expensive_copy(self) -> bool:
        return any(t.is_expensive_copy() for t in self.element_types)

    def to_cpp_return(self) -> str:
        args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
        return f"std::tuple<{args}>"

    def to_cpp_return_const(self) -> str:
        args = ", ".join(self._element_to_cpp_param(t, const=True) for t in self.element_types)
        return f"std::tuple<{args}>"

    def element_forms(self) -> tuple['ValueForm', ...]:
        """Per-element C++ value-category classification (see `ValueForm`)."""
        return tuple(e.value_form() for e in self.element_types)

    def has_ref_elements(self) -> bool:
        # A non-value element that is not moved-by-value (OWN) nor a generic
        # proxy (TYPE_PARAM) -- i.e. a borrowed reference or pointer-repr slot.
        return any(
            not e.is_value_type()
            and e.value_form() not in (ValueForm.OWN, ValueForm.TYPE_PARAM)
            for e in self.element_types
        )

    def has_own_element(self) -> bool:
        # An OWN element is stored by value in storage form (e.g. `socket` for
        # `Own[socket]`), so a frame field of this tuple owns that storage and
        # may be non-default-constructible -- it needs frame_slot wrapping, not
        # a raw field, even though `is_value_type()` reports the tuple True.
        return any(e.value_form() is ValueForm.OWN for e in self.element_types)

    def has_nested_element(self, pred: 'Callable[[TpyType], bool]') -> bool:
        """Depth-first over the element tree: True if `pred` holds at any
        element position, here or inside a nested value tuple.

        The walker owns the DESCENT -- an element reaches a nested tuple
        through the readonly / ref / Own wrappers it may carry -- and `pred`
        owns the element test. They are separate because the two questions
        asked of an element differ: ownership is about the payload under an
        element's wrappers, while the borrow form is about the element as
        written (peeling `Own[Box]` to `Box` would answer a different
        question)."""
        for e in self.element_types:
            if pred(e):
                return True
            inner = unwrap_own(unwrap_readonly(unwrap_ref_type(e)))
            if isinstance(inner, TupleType) and inner.has_nested_element(pred):
                return True
        return False

    def has_nested_own_element(self) -> bool:
        """True if an element AT ANY DEPTH through nested value tuples owns
        its payload -- `Own[T]`, and `Own[T] | None` alike.

        The ownership-transfer question, not the frame-slot form question:
        a sink that owns this tuple consumes the source's owned members
        wherever they sit in the element tree. `has_own_element` stays
        direct-only because a frame field's own slot wrapping is decided by
        the outer tuple's own elements."""
        return self.has_nested_element(
            lambda e: unwrap_optional_own(
                unwrap_readonly(unwrap_ref_type(e))) is not None)

    def is_owned_movable(self) -> bool:
        # A "purely owned" tuple: at least one Own element and NO bare borrow
        # element (every non-value element is Own). Such a tuple has a single
        # storage form with no slot aliasing the caller, so it can be owned and
        # moved as a unit -- the ownership-transfer `std::tuple<...>&&` param
        # ABI and whole-tuple move-out apply. A mixed owned+borrow tuple
        # (`tuple[Own[A], A]`) is excluded: its borrow slot aliases the caller,
        # so the whole-tuple move model does not fit (it stays a const& borrow).
        return self.has_own_element() and not self.has_ref_elements()

    def takes_borrow_slot_as_global(self) -> bool:
        """A module global of this tuple type is a tuple of POINTER SLOTS
        (`std::tuple<T*, ...>`, bound once at module init, never rebound
        from a function body) -- the tuple of the slot every reference-typed
        global is, so an element aliases the object it was given as the
        scalar global does. The tuple as a whole answers `is_value_type()`,
        which is why the global classification cannot ask that. An owned
        element (sema marks a fresh literal element or an owning call's
        element `Own` on the binding) keeps the storage form: the owned
        half needs static backing."""
        return (self.has_pointer_repr_element() and not self.has_own_element()
                and not self.needs_wrapper())

    def is_mixed_own(self) -> bool:
        # The complement of is_owned_movable() among Own-carrying tuples: an
        # owned element AND a borrow element, so the tuple has no single form --
        # the owned half wants storage, the borrowed half a pointer. Every
        # owning sink must therefore materialize the borrowed half rather than
        # take the value as-is, which is what makes "is this tuple owned
        # storage?" answerable only by is_owned_movable(), never by
        # has_own_element().
        return self.has_own_element() and self.has_ref_elements()

    def has_pointer_repr_optional_element(self) -> bool:
        """True if any element is a pointer-repr OptionalType.

        Marks tuples that have distinct borrow form (`std::tuple<T*, ...>`)
        and storage form (`std::tuple<std::optional<T>, ...>`); element-wise
        conversion at boundaries between the two reps goes through
        `tpy::tuple_to_pointer` / `tpy::tuple_to_storage`.
        """
        return any(f is ValueForm.PTR_OPTIONAL for f in self.element_forms())

    @staticmethod
    def _element_is_pointer_repr(e: 'TpyType') -> bool:
        """Whether element `e`'s borrow form is a bare pointer `T*` -- a
        pointer-repr Optional (nullable) or a plain non-value reference
        (record / list / dict / set; non-null).

        Excludes UnionType elements: a pointer-variant union (`Dog | Cat`)
        borrows as `::tpy::Union<A*, B*>` and a recursive-union wrapper as
        `X&` -- neither is a bare `T*`, and both keep their own borrow form and
        conversion path rather than routing through tuple_to_pointer /
        tuple_to_storage."""
        form = e.value_form()
        if form is ValueForm.PTR_OPTIONAL:
            return True
        if form is ValueForm.BORROW_REF:
            peeled = unwrap_readonly(unwrap_ref_type(e))
            # TypeParamRef keeps its `val_or_ref_t<T>` proxy (resolved at C++
            # instantiation); a recursive-union wrapper keeps its nominal `X&`
            # form; a (pointer-variant) union keeps `::tpy::Union<A*,B*>`.
            if isinstance(peeled, (UnionType, TypeParamRef)):
                return False
            if peeled.needs_wrapper():
                return False
            return True
        return False

    def has_pointer_repr_element(self) -> bool:
        """True if any element's borrow form is a bare pointer `T*`.

        Generalizes `has_pointer_repr_optional_element` to also cover plain
        non-value (BORROW_REF) elements: both share one borrow representation
        (`std::tuple<T*, ...>`) distinct from storage form, with element-wise
        conversion at boundaries through `tpy::tuple_to_pointer` (storage ->
        borrow) and `tpy::tuple_to_storage`
        (borrow -> storage). Drives the storage<->pointer wrap sites."""
        return any(self._element_is_pointer_repr(e) for e in self.element_types)

    def has_nested_pointer_repr_element(self) -> bool:
        """True if any element AT ANY DEPTH through nested value tuples is a
        bare-pointer borrow.

        The aliasing question, not the form question: storing this tuple into
        owned storage copies a borrowed reference somewhere in its element
        tree, so the copy diagnostics need this depth. `has_pointer_repr_element`
        deliberately stays direct-only because the outer tuple genuinely has no
        pointer slot of its own when the reference sits a level down -- every
        codegen form site must keep asking that shallower question."""
        return self.has_nested_element(self._element_is_pointer_repr)

    def _element_to_cpp_param(self, t: 'TpyType', const: bool) -> str:
        """C++ type for a tuple element in param/return (borrow) context.

        A non-value element borrows as a bare pointer `T*` / `const T*` (a
        reference can't be a `std::tuple` member, and pointer form is both
        constructible and aliasing). A pointer-variant union borrows as the
        const pointer variant `::tpy::Union<const A*, B const*>`: std::variant
        has no mutable->const converting ctor, so this form must match the
        call-site slot exactly (const is the read-borrow form). OptionalType of
        a non-value inner already lowers to `T*` via its own `to_cpp_return`.
        The `std::optional<T>` / value-`T` storage forms are reserved for
        field/storage contexts and reached via to_cpp() / to_cpp_stored()."""
        if self._element_is_pointer_repr(t) and t.value_form() is ValueForm.BORROW_REF:
            peeled = unwrap_ref_type(t)
            is_const = const or isinstance(peeled, ReadonlyType)
            base = unwrap_readonly(peeled).to_cpp()
            return f"const {base}*" if is_const else f"{base}*"
        # Generic element: val_or_ptr_t<T> (pointer sibling of val_or_ref_t) so
        # the instantiated slot matches the concrete borrow form (`T*` for
        # non-value T, `T` for value T). Plain val_or_ref_t would instantiate
        # to `T&`, which neither a std::tuple member nor a concrete pointer-form
        # caller can satisfy.
        core = unwrap_readonly(unwrap_ref_type(t))
        if (isinstance(core, TypeParamRef)
                and t.value_form() in (ValueForm.TYPE_PARAM, ValueForm.BORROW_REF)):
            peeled = unwrap_ref_type(t)
            is_const = const or isinstance(peeled, ReadonlyType)
            trait = "::tpy::val_or_cptr_t" if is_const else "::tpy::val_or_ptr_t"
            return f"{trait}<{core.name}>"
        # A pointer-variant union element always borrows as the const pointer
        # variant: std::variant has no mutable->const converting ctor, so the
        # call-site slot (which uses to_const_ptr_variant) and this param/return
        # form must agree on const pointees regardless of the surrounding const.
        if isinstance(core, UnionType) and core.uses_pointer_repr():
            return t.to_cpp_return_const()
        return t.to_cpp_return_const() if const else t.to_cpp_return()

    def to_cpp_param_type(self) -> str:
        if self.is_owned_movable():
            # Purely-owned tuple: ownership-transfer param, rvalue ref so the
            # callee can move the owned elements out -- mirrors Own[T] -> T&&.
            args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
            return f"std::tuple<{args}>&&"
        args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
        return f"const std::tuple<{args}>&"

    def to_cpp_param(self, name: str) -> str:
        if self.is_owned_movable():
            args = ", ".join(self._element_to_cpp_param(t, const=False) for t in self.element_types)
            return f"std::tuple<{args}>&& {name}"
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
    """
    def _resolve(t: TpyType) -> TpyType:
        if isinstance(t, IntLiteralType):
            return resolver(t) if callable(resolver) else resolver
        if isinstance(t, FloatLiteralType):
            return FLOAT
        return t.map_inner_types(_resolve)
    return _resolve(typ)


def make_union(*types: TpyType) -> TpyType:
    """Normalize a sequence of types into a canonical union form.

    - Flattens nested UnionType/OptionalType members
    - Deduplicates by structural equality
    - Single type collapses to itself
    - Single type + None -> OptionalType(T) (and OptionalType.__new__ may
      further collapse to PtrType when T is pointer-shaped; see that class
      for the pointer-inner collapse rule)
    - Multiple types +/- None -> UnionType(sorted..., [NoneType])
    """
    # Flatten nested unions and optionals
    flat: list[TpyType] = []
    has_none = False
    # Inners flattened out of a force_pointer_repr Optional: if such an inner
    # survives as the sole member, the rebuilt Optional must re-commit to T*
    # repr. Dropping it re-types a T*-ABI generic Optional (or a branch/None
    # join over one) as std::optional<T> with no bridge -- uncompilable.
    force_ptr_inners: set[TpyType] = set()
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
            if t.force_pointer_repr:
                force_ptr_inners.add(t.inner)
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

    # Sort for canonical order. `str(t)` renders a NominalType's short name
    # only, so two same-short-name records / alias-instances from different
    # modules would tie and fall back to insertion order -- producing two
    # unequal, layout-incompatible variants for the same semantic union across
    # module boundaries. Break ties on the qualified name when available.
    deduped.sort(key=lambda t: (str(t), t.qualified_name() or ""))

    if len(deduped) == 0:
        # Only None members -- caller should handle this
        return VoidType()
    elif len(deduped) == 1 and not has_none:
        return deduped[0]
    elif len(deduped) == 1 and has_none:
        return OptionalType(deduped[0],
                            force_pointer_repr=deduped[0] in force_ptr_inners)
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


def view_is_inherently_const(t: 'TpyType') -> bool:
    """Distinguish views that auto-const inference can safely return.

    Mutable Span[T] / SpanIter[T] would silently degrade to their readonly
    counterpart under const inference -- a real return-type change behind
    the user's back. StrView, BytesView, Span[readonly[T]], and
    SpanIter[readonly[T]] are inherently const, so const-ifying their
    enclosing method is a no-op on the declared return type; so is an
    `Iterator` handle over a VALUE element (`Iterator[str]`), whose steps
    are copies whatever the receiver's const-ness.
    """
    from tpyc.type_def_registry import is_str_view_type, is_bytes_view_type, is_span_iter
    if is_str_view_type(t) or is_bytes_view_type(t):
        return True
    if span_is_readonly(t):
        return True
    if is_span_iter(t):
        return isinstance(t.type_args[0], ReadonlyType)
    if (isinstance(t, NominalType) and t.is_protocol
            and t.qualified_name() == "typing.Iterator" and t.type_args):
        # A value element is a copy per step -- unless the value is or
        # carries a mutable alias (a `Span[T]`, a `Ptr[T]`, a tuple holding
        # one), which const would turn into its readonly twin like the
        # SpanIter arm above.
        elem = t.type_args[0]
        return isinstance(elem, TpyType) and _value_carries_no_mutable_alias(elem)
    return False


def _value_carries_no_mutable_alias(t: 'TpyType') -> bool:
    """A VALUE type whose copy aliases nothing mutable: not a pointer, not a
    non-readonly borrowing view, and no tuple element that is one."""
    from tpyc.type_def_registry import is_borrowing_view_type
    if not t.is_value_type() or t.is_pointer():
        return False
    if is_borrowing_view_type(t) and not view_is_inherently_const(t):
        return False
    if isinstance(t, TupleType):
        return all(_value_carries_no_mutable_alias(e) for e in t.element_types)
    return True


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


def make_varargs(element_type: 'TpyType', is_readonly: bool = False) -> 'NominalType':
    """Factory for the *args body view varargs[T] / varargs[readonly[T]].
    Plain NominalType with qname `tpy.varargs`; behavior comes from the
    TypeDef registry. Distinct from Span so it never coerces to std::span."""
    if is_readonly and not isinstance(element_type, ReadonlyType):
        element_type = ReadonlyType(element_type)
    return NominalType(name="varargs", type_args=(element_type,),
                       _module_qname="tpy.varargs")


def varargs_is_readonly(t: 'TpyType') -> bool:
    """True if t is a varargs whose element is wrapped in ReadonlyType."""
    from tpyc.type_def_registry import is_varargs
    return is_varargs(t) and isinstance(t.type_args[0], ReadonlyType)


def varargs_as_const(t: 'TpyType') -> 'NominalType':
    """Const variant (varargs[readonly[T]]) of a varargs. No-op if already
    readonly. Parallels span_as_const but preserves the varargs qname so a
    readonly-inferred *args slot does not silently become a Span."""
    if varargs_is_readonly(t):
        return t
    return make_varargs(ReadonlyType(t.type_args[0]))


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


def make_awaitable(awaited_type: 'TpyType') -> 'NominalType':
    """Factory for the structural protocol Awaitable[T] (tpy.coro.Awaitable).
    Used at sites that need the bare `__poll__`-shaped protocol -- e.g.
    `__anext__` / `__aenter__` / `__aexit__` return-type unwrapping where
    user types might legitimately implement just `__poll__`. The result
    type of an `async def` call is `Cancellable[T]` instead (see
    `make_cancellable`), which is a structural extension of Awaitable."""
    from tpyc import qnames
    return NominalType(name="Awaitable", type_args=(awaited_type,),
                       is_protocol=True,
                       _module_qname=qnames.AWAITABLE)


def make_cancellable(awaited_type: 'TpyType') -> 'NominalType':
    """Factory for the @dynamic protocol Cancellable[T] (tpy.coro.Cancellable).
    Used as the sema-visible return type of `async def f() -> T` so that
    callers expecting `Own[Cancellable[T]]` (e.g. `create_task`,
    `wait_for`) can match the call result; codegen still emits a concrete
    `__coro_<funcname>` struct that auto-implements `cancel()` (mirrors
    generator `Iterator[T]` return + concrete `__gen_<funcname>` factory).
    Cancellable structurally extends Awaitable via its `__poll__` member,
    so `await coro` and other Awaitable-consumer sites continue to work."""
    from tpyc import qnames
    # Own is a return-ABI decoration, not part of what type the awaited
    # result is: `-> Own[C]` and `-> C` both await to a C. Normalizing it
    # out here lets an Own-returning coro match `Own[Cancellable[T]]`
    # consumers (run / create_task / wait_for) at T=C; the borrow-vs-own
    # distinction lives on `FunctionInfo.async_inner_return`.
    if isinstance(awaited_type, OwnType):
        awaited_type = awaited_type.wrapped
    # is_dynamic_protocol matches the lib declaration (tpy.coro.Cancellable
    # is @dynamic); without it is_dyn_protocol() disagrees with the
    # registry's protocol_info and Own[Cancellable[T]] renders as the
    # structural-protocol placeholder instead of unique_ptr<P>.
    return NominalType(name="Cancellable", type_args=(awaited_type,),
                       is_protocol=True,
                       is_dynamic_protocol=True,
                       _module_qname=qnames.CANCELLABLE)


@dataclass(frozen=True)
class ConcreteFrameType(NominalType):
    """A generator or coroutine object whose concrete frame struct is
    statically known (the result of binding a direct generator /
    async-def call).

    A subtype of the protocol the frame implements (`Iterator[T]` /
    `Cancellable[T]`), so every conformance, compat and diagnostic path
    treats it as that protocol; the concreteness is a REPRESENTATION fact:
    the value is one frame object, a non-copyable reference type (a
    generator object is aliased the way a record is; a coroutine handle
    still moves on a second binding). Never printed to users (renders as the
    protocol via the inherited __str__); has no self-contained C++
    spelling -- codegen names the frame struct.
    """
    # Identity fields participate in equality: two DIFFERENT functions'
    # frames must compare unequal (a branch-join merge collapsing them
    # would bind one frame type's storage to the other's object).
    frame_func_name: str = ""
    # Methods: the receiver's owner NominalType (carries the class-level
    # type args the struct name needs). None for free functions.
    frame_owner: 'NominalType | None' = None
    frame_inferred_type_args: 'tuple[TpyType, ...] | None' = None
    # Defining module when it differs from the binding module (qualifies
    # the struct name with the callee's C++ namespace).
    frame_module_qual: 'str | None' = None

    def is_value_type(self) -> bool:
        return False

    def to_cpp(self) -> str:
        raise RuntimeError(
            f"{type(self).__name__} has no self-contained C++ spelling; "
            f"render via codegen's type_to_cpp (frame struct naming)")

    def with_inner_types(self, types: tuple['TpyType', ...]) -> 'TpyType':
        # NominalType's override reconstructs a plain NominalType, which
        # would silently strip the frame identity if a generic-substitution
        # walk ever traverses one of these. Preserve every field.
        base = super().with_inner_types(types)
        return dc_replace(self, type_args=base.type_args)


@dataclass(frozen=True)
class ConcreteCoroType(ConcreteFrameType):
    """A coroutine handle with a known frame struct: erased to
    unique_ptr<Cancellable<T>> only at typed boundaries (create_task/run
    args, Own[Cancellable[T]] params/returns)."""
    # Awaiting this handle yields a borrow (pointer Poll payload aliasing
    # caller-durable storage), per the callee's declared-return form. A
    # borrow-result handle is direct-await-only: it must never erase to
    # Own[Cancellable[T]] (task results are owned slots).
    result_is_borrow: bool = False


@dataclass(frozen=True)
class ConcreteGenType(ConcreteFrameType):
    """A generator object with a known frame struct (`g = gen()`)."""


def make_concrete_coro(awaited_type: 'TpyType', func_name: str,
                       owner: 'NominalType | None' = None,
                       inferred_type_args: 'tuple[TpyType, ...] | None' = None,
                       module_qual: 'str | None' = None,
                       result_is_borrow: bool = False) -> 'ConcreteCoroType':
    from tpyc import qnames
    return ConcreteCoroType(
        name="Cancellable", type_args=(awaited_type,),
        is_protocol=True, is_dynamic_protocol=True,
        _module_qname=qnames.CANCELLABLE,
        frame_func_name=func_name, frame_owner=owner,
        frame_inferred_type_args=inferred_type_args,
        frame_module_qual=module_qual,
        result_is_borrow=result_is_borrow)


def make_concrete_gen(iterator: 'NominalType', func_name: str,
                      owner: 'NominalType | None' = None,
                      inferred_type_args: 'tuple[TpyType, ...] | None' = None,
                      module_qual: 'str | None' = None) -> 'ConcreteGenType':
    """The concrete frame of a generator call typed `iterator`
    (`typing.Iterator[T]`)."""
    return ConcreteGenType(
        name=iterator.name, type_args=iterator.type_args,
        is_protocol=iterator.is_protocol,
        _module_qname=iterator._module_qname,
        is_dynamic_protocol=iterator.is_dynamic_protocol,
        frame_func_name=func_name, frame_owner=owner,
        frame_inferred_type_args=inferred_type_args,
        frame_module_qual=module_qual)


# Singleton for the non-generic Waker type. Registered as a value-type
# nominal so user code can declare `def poll(self, w: Waker) -> Poll[T]`.
WAKER = NominalType(name="Waker", type_args=(),
                    _module_qname="tpy.coro.Waker")


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
    registry (is_value_type=True, is_send/is_sync=False) and the stub
    (`@native("tpy::dict_keys_view")`)."""
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
    The C++ type is the frame of the generator function sema builds for the
    expression (auto-deduced where it is bound).
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
        """C++ type for a parameter in a std::function / Fn requires signature.

        Uses to_cpp_param_type() for types that override it (str -> string_view,
        BigInt -> const BigInt&). Non-value params spell MUTABLE ref unless
        wrapped readonly[...]: a callable contract says nothing about mutation,
        so the callback must be allowed to mutate (CPython semantics), and the
        permissive C++ direction is mutable -- a const-ref-taking callee
        converts into a mutable-ref std::function slot, not vice versa.
        readonly[T] params keep const ref (the explicit non-mutating contract).
        """
        if isinstance(t, ReadonlyType):
            cpp = t.wrapped.to_cpp_param_type()
            if not t.wrapped.is_value_type() and not cpp.startswith("const "):
                return f"const {t.wrapped.to_cpp()}&"
            return cpp
        cpp = t.to_cpp_param_type()
        if not t.is_value_type() and cpp.startswith("const "):
            return f"{t.to_cpp()}&"
        return cpp

    def _std_function_sig(self) -> str:
        representation = classify_result_representation(
            self.return_type, position=ResultPosition.ERASED_CALLABLE)
        ret = ("void" if representation is ResultRepresentation.ERASED_VOID
               else self.return_type.to_cpp())
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

    def is_send(self) -> bool:
        # The erased closure may capture non-Send state; opting in is via
        # the Send[Callable[...]] wrapper checked at the construction site.
        return False

    def is_sync(self) -> bool:
        return False

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


# Builtin containers whose concrete type can still wrap a pending leaf in a type
# arg (a non-empty `dict`/`list`/`set` literal is concrete but its element/value
# may stay pending). `unify_literal_types` recurses these; nothing else.
_PENDING_WRAPPER_QNAMES = frozenset({"builtins.list", "builtins.dict", "builtins.set"})


def contains_pending_leaf(typ: 'TpyType') -> bool:
    """True if typ is, or nests, a Pending* container type.

    Type-structural, so it lives with the types: the deferred-resolution
    machinery (cached snapshots, the peer-unify traversal below) uses it to tell
    a fully-concrete type from one that still has a pending leaf to resolve."""
    if isinstance(typ, (PendingListType, PendingDictType, PendingSetType)):
        return True
    return any(contains_pending_leaf(t) for t in typ.inner_types())


def pending_containers_match(
    a: 'TpyType', b: 'TpyType',
    elem_compatible: 'Callable[[TpyType, TpyType], bool]',
) -> bool:
    """Whether two PENDING dict/set containers are the same kind with
    pairwise-compatible key/value/element types, without forcing the deferred
    resolution. The element rule is the caller's, so the peer-unify and
    assignability paths share one container skeleton rather than each rejecting
    structurally-equal pendings.

    Pending LISTS are handled directly in `unify_literal_types` instead: a list
    pair is size-agnostic (jagged peers share `list[T]`), and the Array-vs-list
    decision is left to the demotion hook -- a concern this pure predicate has no
    business encoding.
    """
    if isinstance(a, PendingDictType) and isinstance(b, PendingDictType):
        return (elem_compatible(a.key_type, b.key_type)
                and elem_compatible(a.value_type, b.value_type))
    if isinstance(a, PendingSetType) and isinstance(b, PendingSetType):
        return elem_compatible(a.element_type, b.element_type)
    return False


def unify_literal_types(
    a: 'TpyType', b: 'TpyType',
    on_pending_pair: 'Callable[[TpyType, TpyType], None] | None' = None,
) -> 'TpyType | None':
    """Unify two literal/pending element types -- the single source of truth for
    "are these two element types peer-compatible". IntLiteralType / FloatLiteralType
    count as compatible with each other and with their concrete equivalents;
    TupleType and pending containers recurse. Returns the unified type or None.

    Used by both the peer-unification path (list/dict/set literal element merge)
    and the assignability path (`check_type_compatible`'s expected-Pending branch,
    via `... is not None`), so the two cannot drift in what they accept.

    `on_pending_pair`, when given, fires for every matched pending-container pair
    on this one traversal (inner pairs before the pair enclosing them), letting a
    caller apply a side effect -- e.g. demoting a jagged pending list to vector --
    wherever a pending pair is reachable, including inside tuples and dict values.
    This keeps the side-effecting and pure paths from drifting on which shapes
    they reach. The function stays pure when the hook is None (the default).
    """
    if a == b:
        return a
    if isinstance(a, IntLiteralType) and isinstance(b, IntLiteralType):
        return a
    if isinstance(a, IntLiteralType) and is_integer_type(b):
        return b
    if isinstance(b, IntLiteralType) and is_integer_type(a):
        return a
    if isinstance(a, FloatLiteralType) and isinstance(b, FloatLiteralType):
        return a
    if isinstance(a, FloatLiteralType) and is_float_type(b):
        return b
    if isinstance(b, FloatLiteralType) and is_float_type(a):
        return a
    if isinstance(a, TupleType) and isinstance(b, TupleType):
        if len(a.element_types) != len(b.element_types):
            return None
        unified: list[TpyType] = []
        for ea, eb in zip(a.element_types, b.element_types):
            u = unify_literal_types(ea, eb, on_pending_pair)
            if u is None:
                return None
            unified.append(u)
        return TupleType(tuple(unified))
    # Pending lists are size-agnostic: element-compatible peers always unify,
    # since jagged ones share `list[T]`. The hook converges them on this pass
    # (same size -> link/Array, different size -> demote to vector); size is not
    # a compatibility gate here.
    if isinstance(a, PendingListType) and isinstance(b, PendingListType):
        if unify_literal_types(a.element_type, b.element_type, on_pending_pair) is None:
            return None
        if on_pending_pair is not None:
            on_pending_pair(a, b)
        return a
    if pending_containers_match(
            a, b, lambda x, y: unify_literal_types(x, y, on_pending_pair) is not None):
        if on_pending_pair is not None:
            on_pending_pair(a, b)
        return a
    # A concrete builtin container can still wrap a pending leaf (a non-empty
    # dict literal `{1: [2, 3]}` is a concrete dict[int32, PendingList...]).
    # Recurse its type args so a pending list nested under it is reached and
    # converged on the same pass. Gated on an actual pending leaf, so fully
    # concrete generics keep their exact a==b / None behaviour above; and
    # restricted to builtin containers so a same-named user record/protocol that
    # somehow carried a pending leaf can't be silently treated as unifiable.
    if (isinstance(a, NominalType) and isinstance(b, NominalType)
            and a.qualified_name() in _PENDING_WRAPPER_QNAMES
            and a.qualified_name() == b.qualified_name()
            and (contains_pending_leaf(a) or contains_pending_leaf(b))):
        ia, ib = a.inner_types(), b.inner_types()
        if len(ia) != len(ib):
            return None
        for ea, eb in zip(ia, ib):
            if unify_literal_types(ea, eb, on_pending_pair) is None:
                return None
        return a
    return None


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
    is_any_member: Callable[['TpyType'], bool]  # predicate: matches any str/bytes-category type
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
    # Owned-storage roots this view borrows from (a compound ternary/and-or
    # source borrows every root reachable through its arms). Mutating any one
    # demotes the view to owned.
    source_storages: list[str] = field(default_factory=list)
    source_mutated: bool = False
    # A view-safe source whose storage is not static (param, container
    # element, tuple-unpack temp, ...). Irrelevant in a sync body -- the
    # source outlives the function-scoped view -- but in a resumable frame
    # the binding outlives case-block temps and suspensions, so resolution
    # promotes the local to owned storage. Literal / Final sources have
    # static storage and never set this.
    frame_unsafe_source: bool = False
    # What every binding of this view reads from, for the hoist rule: the
    # storage roots it views, resolved at the binding (a view name is
    # followed through its own entries), or unknown -- no binding recorded,
    # or a source with no root the rule can place -- which owns once hoisted.
    hoist_roots: set[str] = field(default_factory=set)
    hoist_unknown: bool = False
    resolved_type: Optional[TpyType] = None


# Singleton instances for built-in types
INT8 = NominalType("int8", (), _module_qname="tpy.int8")
INT16 = NominalType("int16", (), _module_qname="tpy.int16")
INT32 = NominalType("int32", (), _module_qname="tpy.int32")
INT64 = NominalType("int64", (), _module_qname="tpy.int64")
UINT8 = NominalType("uint8", (), _module_qname="tpy.uint8")
UINT16 = NominalType("uint16", (), _module_qname="tpy.uint16")
UINT32 = NominalType("uint32", (), _module_qname="tpy.uint32")
UINT64 = NominalType("uint64", (), _module_qname="tpy.uint64")
ALL_FIXED_INTS = [INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64]

VOID = VoidType()
STR = NominalType("str", (), _module_qname="builtins.str")
STRING = NominalType("String", (), _module_qname="tpy.String")
STRVIEW = NominalType("StrView", (), _module_qname="tpy.StrView")
FSTR = NominalType("FStr", (), _module_qname="tpy.FStr")
CHAR = NominalType("char", (), _module_qname="tpy.char")
BYTES = NominalType("bytes", (), _module_qname="builtins.bytes")
BYTEARRAY = NominalType("bytearray", (), _module_qname="builtins.bytearray")
BYTESVIEW = NominalType("BytesView", (), _module_qname="tpy.BytesView")
BOOL = NominalType("bool", (), _module_qname="builtins.bool")
FLOAT = NominalType("float", (), _module_qname="builtins.float")
FLOAT32 = NominalType("float32", (), _module_qname="tpy.float32")

BIGINT = NominalType("int", (), _module_qname="builtins.int")
NONE = NoneType()
ANY = AnyType()
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
from .type_def_registry import is_string_type as _is_string_type, is_bytearray_type as _is_bytearray_type, enum_info_of as _enum_info_of
STR_FAMILY = ViewTypeFamily(
    owned_type=STR, view_type=STRVIEW, promote_param_match=_is_string_type,
    is_any_member=is_any_str_type,
    pending_type_class=PendingStrType, element_type=CHAR,
    display_name="str", qualified="builtins.str",
)
BYTES_FAMILY = ViewTypeFamily(
    owned_type=BYTES, view_type=BYTESVIEW, promote_param_match=_is_bytearray_type,
    is_any_member=is_any_bytes_type,
    pending_type_class=PendingBytesType, element_type=UINT8,
    display_name="bytes", qualified="builtins.bytes",
)
VIEW_TYPE_FAMILIES = (STR_FAMILY, BYTES_FAMILY)

# Family lookup tables. Owned types share the NominalType class, so dispatch
# on qname; pending types each have their own class.
_VIEW_OWNED_QNAME_TO_FAMILY: dict[str, ViewTypeFamily] = {
    _f.owned_type.qualified_name(): _f for _f in VIEW_TYPE_FAMILIES
}
_VIEW_PENDING_CLASS_TO_FAMILY: dict[type, ViewTypeFamily] = {
    _f.pending_type_class: _f for _f in VIEW_TYPE_FAMILIES
}


def view_family_for_type(var_type: 'TpyType') -> Optional[ViewTypeFamily]:
    """Return the ViewTypeFamily for a str/bytes/pending-view type, or None.

    `LiteralType` over a str/bytes base also resolves to its base's family --
    Literal-annotated locals get the same view-storage inference as plain
    str/bytes locals, while the LiteralType annotation is preserved for
    overload dispatch / narrowing / out-of-set rejection.
    """
    family = _VIEW_PENDING_CLASS_TO_FAMILY.get(type(var_type))
    if family is not None:
        return family
    if isinstance(var_type, LiteralType):
        return view_family_for_type(var_type.base_type)
    qn = var_type.qualified_name() if isinstance(var_type, NominalType) else None
    return _VIEW_OWNED_QNAME_TO_FAMILY.get(qn) if qn else None


# int32 range limits (for runtime-constant checks). Use int_traits_of(t) for
# other widths.
INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1


def is_protocol_type(typ: TpyType) -> bool:
    """Check if a type is a protocol type."""
    return isinstance(typ, NominalType) and typ.is_protocol


def is_ptr_variant_union(t: TpyType) -> bool:
    """A non-value union lowered to `::tpy::Union<A*, B*>` (pointer variant).

    Pure type query -- `CodeGenContext.is_ptr_variant_union`, the binding
    classifier and the value-category rules all read this one definition. A
    recursive-union alias uses a wrapper struct, which is a value type, so it
    is not a pointer variant.
    """
    return (isinstance(t, UnionType) and t.uses_pointer_repr()
            and not t.needs_wrapper())


def property_getter_returns_storage_ref(rt: 'TpyType | None') -> bool:
    """A property GETTER's return convention differs from a plain method's for
    the two pointer-repr shapes: the getter hands back the FIELD'S STORAGE by
    reference (`std::optional<T>&`, `::tpy::Union<A, B>&`) where a method
    returns the borrow form by value (`T*`, `::tpy::Union<A*, B*>`).

    The emitter that spells the return type and the value-category rule that
    calls such a getter an lvalue must agree on WHICH shapes those are, or a
    reference return reads as a temporary (or the reverse) at every hop off a
    property.
    """
    rt = unwrap_ref_type(rt)
    return ((isinstance(rt, OptionalType) and rt.uses_pointer_repr())
            or (rt is not None and is_ptr_variant_union(rt)))


def is_polymorphic_class_type(typ: TpyType, registry: 'TypeRegistry') -> bool:
    """True if `typ` is a concrete class that inherits a @dynamic protocol
    (directly or transitively through any ancestor class).

    Such classes have a real C++ vtable, support dynamic_cast, and require
    care at value-conversion boundaries to avoid slicing. Result is cached
    on RecordInfo (the answer is stable post-Phase-1 sema; MRO and
    implemented_protocols don't change after registration).
    """
    if not isinstance(typ, NominalType) or typ.is_protocol:
        return False
    record_info = registry.get_record(typ.name)
    if record_info is None:
        return False
    cached = record_info._is_polymorphic_class
    if cached is not None:
        return cached
    answer = any(registry.iter_dynamic_protocols(record_info))
    record_info._is_polymorphic_class = answer
    return answer


def is_dynamic_dispatch_inner(typ: TpyType, registry: 'TypeRegistry') -> bool:
    """True if values of `typ` carry a @dynamic-rooted C++ vtable reachable
    through a pointer/reference, so dynamic_cast to a descendant is sound:
    either a polymorphic class (`is_polymorphic_class_type`) or a direct
    @dynamic protocol. The two share one fact -- a pointer/reference to such
    a value is the input to isinstance's dynamic_cast -- so callers treat
    them uniformly.

    Behind such a pointer/reference the runtime object is either an
    inheritance conformer (a real IS-A `dynamic_cast` target) or, for a
    structural conformer, an owned `Adapter<P,U>` / `RefAdapter<P,U>` reached
    via `tpy::dyn_adapter_cast` -- both vtable-carrying, so the descendant cast
    is sound either way."""
    if is_polymorphic_class_type(typ, registry):
        return True
    if isinstance(typ, NominalType) and typ.is_protocol:
        from .type_def_registry import protocol_info_of
        pi = protocol_info_of(typ)
        return pi is not None and pi.is_dynamic
    return False


def polymorphic_source_inner(
    declared: 'TpyType | None', registry: 'TypeRegistry'
) -> 'NominalType | None':
    """If `declared` is a dynamic-dispatch source -- a bare dispatch inner
    (`T&`), `Optional[inner]` (pointer-repr `T*`), or `Ptr[inner]` (`T*`),
    where `inner` is a polymorphic class or direct @dynamic protocol
    (`is_dynamic_dispatch_inner`) -- return that inner. Returns None
    otherwise. Centralizes the predicate used by the isinstance narrowing
    fact filter (sema) and the cast-and-cache extraction (codegen)."""
    if declared is None:
        return None
    unwrapped = unwrap_readonly(declared)
    if (isinstance(unwrapped, OptionalType)
            and unwrapped.uses_pointer_repr()
            and isinstance(unwrapped.inner, NominalType)
            and is_dynamic_dispatch_inner(unwrapped.inner, registry)):
        return unwrapped.inner
    if isinstance(unwrapped, PtrType):
        pointee = unwrapped.inner_pointee
        if (isinstance(pointee, NominalType)
                and is_dynamic_dispatch_inner(pointee, registry)):
            return pointee
    if (isinstance(unwrapped, NominalType)
            and is_dynamic_dispatch_inner(unwrapped, registry)):
        return unwrapped
    return None


def deref_dispatch_inner(
    typ: TpyType, type_ops: 'TypeOperations', registry: 'TypeRegistry',
) -> 'tuple[NominalType, int] | None':
    """If `typ` is an owning wrapper whose reference-returning __deref__ peels
    to a @dynamic-dispatch inner (polymorphic class or direct @dynamic
    protocol), return (inner, deref_depth). The deref payload pointer
    `&(<v>.__deref__()...)` is then the same dynamic_cast input the
    bare/Ptr/Optional sources expose directly, so isinstance / match dispatch
    the same way -- on the deref view rather than on the wrapper.

    Bare/Ptr/Optional sources are caught earlier via polymorphic_source_inner;
    this fires only for wrappers (Box/Rc). A wrapper without a __deref__ (e.g.
    Weak) has no deref view and yields None -- it falls through to the
    static-fold path. `type_ops` is the analyzer's TypeOperations (it owns
    `get_deref_target_type`); passed in to keep typesys free of a sema import."""
    current = unwrap_readonly(typ)
    for depth in range(1, 9):
        target = type_ops.get_deref_target_type(current)
        if target is None:
            return None
        inner = unwrap_readonly(target)
        if (isinstance(inner, NominalType)
                and is_dynamic_dispatch_inner(inner, registry)):
            return inner, depth
        current = inner
    return None


def is_polymorphic_subclass_fact(
    var_decl: 'TpyType | None', narrowed: 'TpyType', registry: 'TypeRegistry'
) -> bool:
    """True when `narrowed` is a strict polymorphic subclass narrowing of
    `var_decl`. Composed predicate: `var_decl` is a polymorphic-class source
    (via `polymorphic_source_inner`), `narrowed` is a `NominalType`, and the
    narrowed class is distinct from the declared root (identity narrowings
    like `is not None` are gated out). Centralizes the check used at the
    isinstance fact filter (sema), the if-init cast pre-bind (codegen),
    the post-guard cast-and-cache (codegen), and the early-return
    post-guard fact filter (codegen)."""
    if not isinstance(narrowed, NominalType):
        return False
    source_inner = polymorphic_source_inner(var_decl, registry)
    return source_inner is not None and source_inner != narrowed


def polymorphic_source_is_pointer(declared: 'TpyType | None') -> bool:
    """True if a polymorphic-class source is pointer-shaped at the C++ level
    (`Optional[Polymorphic]` lowered to `T*`), False for bare polymorphic
    sources lowered to `T&`. Callers should first confirm the source IS
    polymorphic via `polymorphic_source_inner`. Drives the `&var` vs `var`
    cast-input choice in dynamic_cast emission."""
    if declared is None:
        return False
    unwrapped = unwrap_readonly(declared)
    if isinstance(unwrapped, PtrType):
        return True
    return (isinstance(unwrapped, OptionalType)
            and unwrapped.uses_pointer_repr())


def polymorphic_subclass_into_optional(
    target_type: TpyType, init_type: TpyType | None, registry: 'TypeRegistry'
) -> 'NominalType | None':
    """If `target_type` is `Optional[Polymorphic]` and `init_type` is a
    polymorphic strict subclass of its inner, return `init_type` (the
    rvalue's actual class). Otherwise return None.

    Centralizes the slicing-risk predicate for Optional[Polymorphic] slots
    (parameter-passing temp materialization, local init slot, local rebind
    slot). The caller decides how to react: argument-passing widens the
    temp to the returned type; local init does the same; local rebind
    raises because the shared rebind slot can't be retyped per rvalue.

    The inner may be a concrete polymorphic class (`Optional[Animal]`) or a
    direct @dynamic protocol (`Optional[Pet]`) -- both lower to a pointer-repr
    borrow that an init-only local can back with a widened slot, and both face
    the same shared-rebind-slot retyping problem.
    """
    if not (isinstance(target_type, OptionalType)
            and isinstance(init_type, NominalType)
            and is_polymorphic_class_type(init_type, registry)
            and init_type != target_type.inner):
        return None
    inner = target_type.inner
    if (is_polymorphic_class_type(inner, registry)
            or is_dynamic_dispatch_inner(inner, registry)):
        return init_type
    return None


def none_default_cpp_spelling(ptype: TpyType) -> str:
    """How a `None` default/argument spells in C++ for a parameter of `ptype`.

    THE single answer, so a consumer that only needs "is it the bare `nullptr`?"
    (a target-less render can reproduce that and nothing else) asks the same
    function that emits it. `Own[T]` forces value form regardless of the inner
    repr, and a None-including union keeps `std::monostate` first in BOTH reprs,
    so `{}` default-constructs to None either way.
    """
    inner = ptype.wrapped if isinstance(ptype, OwnType) else ptype
    if isinstance(inner, OptionalType):
        if isinstance(ptype, OwnType) or not inner.uses_pointer_repr():
            return "std::nullopt"
    if isinstance(inner, UnionType) and not is_protocol_union(inner):
        return "{}"
    return "nullptr"


def default_needs_call_site_fill(ptype: TpyType) -> bool:
    """Whether a default on this parameter must be filled at the CALL site
    rather than emitted as a C++ default argument.

    A value-form `std::optional<Rec>` / `std::variant<..., Rec, ...>` default
    has its conversion to the parameter type checked AT THE DECLARATION, and
    free functions are declared ahead of every record definition -- so the
    class template instantiates over an incomplete type and the build dies
    inside <type_traits>. Pointer-form (`Rec*`) members and scalars are
    complete at that point and keep the C++ default -- except under `Own[T]`,
    which renders the value form whatever the inner repr says, so the
    pointer-form escape does not apply to it.
    """
    def _is_record(t: TpyType) -> bool:
        # Only NominalType carries the flag; a NoneType/monostate arm does not.
        return isinstance(t, NominalType) and t.is_user_record

    own = isinstance(ptype, OwnType)
    inner = ptype.wrapped if own else ptype
    if isinstance(inner, OptionalType):
        return (own or not inner.uses_pointer_repr()) and _is_record(inner.inner)
    if isinstance(inner, UnionType):
        if is_protocol_union(inner) or (inner.uses_pointer_repr() and not own):
            return False
        return any(_is_record(m) for m in inner.members)
    return False


# (param_type, default_expr); the expr is a parser TpyExpr, spelled Any here for
# the same circular-import reason as ParamInfo.default_expr.
DefaultSlot = tuple[TpyType, Any]


def default_emittable_at(slots: Sequence[DefaultSlot], i: int,
                         is_member: bool = False) -> bool:
    """Whether parameter `i`'s default can be a C++ default argument.

    `slots`: (param_type, default_expr) in signature order. THE rule -- codegen
    emits from it and the call-site fill is its complement, so the two cannot
    define "needs filling" differently.

    C++ requires defaults to form a trailing suffix, which Python does not. A
    default whose type would instantiate a class template over a user record
    additionally has no spelling where a FREE function is declared, since that
    block precedes every record definition; inside a record the alternatives
    are already defined, so a member keeps such a default (a member whose union
    forward-references a LATER record is the residual, tracked in BUGS.md).
    """
    if slots[i][1] is None:
        return False
    if not is_member and any(d is not None and default_needs_call_site_fill(t)
                             for t, d in slots):
        return False
    return all(slots[j][1] is not None for j in range(i + 1, len(slots)))


def any_default_suppressed(slots: Iterable[DefaultSlot],
                           is_member: bool = False) -> bool:
    """True when some default is not emittable, so every call must supply the
    omitted arguments explicitly. The exact complement of `default_emittable_at`."""
    slots = list(slots)
    return any(d is not None and not default_emittable_at(slots, i, is_member)
               for i, (_t, d) in enumerate(slots))


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


def type_value_init_indeterminate(typ: TpyType) -> bool:
    """Whether C++ default-initialization of a field of this type leaves it
    indeterminate.

    Raw pointers (`Ptr[T]`) and primitive scalars are the hazard: a
    non-trivial class `T() = default;` default-initializes each member, and
    primitives get no zeroing. Strings, containers, Optional, Span, and
    tuples of safe-defaults all yield defined states (their type has a
    well-behaved default ctor). User records propagate via their own
    `del_suppresses_default_ctor` rule -- this predicate treats them as
    defined here.
    """
    from .type_def_registry import is_array, is_enum_type
    if isinstance(typ, ReadonlyType):
        return type_value_init_indeterminate(typ.wrapped)
    if isinstance(typ, OwnType):
        return type_value_init_indeterminate(typ.wrapped)
    if isinstance(typ, OptionalType):
        return False
    if isinstance(typ, PtrType):
        return True
    if isinstance(typ, TupleType):
        return any(type_value_init_indeterminate(e) for e in typ.element_types)
    if is_array(typ):
        elem = typ.get_element_type()
        return elem is None or type_value_init_indeterminate(elem)
    if is_enum_type(typ):
        return False
    if is_primitive_type(typ):
        return True
    return False


def del_suppresses_default_ctor(record_info: 'RecordInfo') -> bool:
    """Whether this record has no usable zero-arg constructor.

    Three sources of suppression, all rooted in "~T() would read
    indeterminate state if T() were callable":

      - `@nocopy + __del__`: author intent -- no safe default state
        (Box, Rc, Weak).
      - `__del__` with `__init__` that has any required parameter: the
        zero-arg overload is suppressed; callers must provide arguments.
      - `__del__` with no `__init__` and at least one own field whose
        value-initialization would leave it indeterminate (raw `Ptr[T]`,
        primitive scalar without an in-class initializer).

    Empty-fields `__del__`-only records (the abstract-base pattern) stay
    default-constructible so derived classes can value-init the base
    subobject. Codegen and sema both consult this predicate so the C++
    build never silently deletes a constructor that sema reported as
    available.
    """
    if record_info.is_nocopy and record_info.has_del:
        return True
    if not record_info.has_del:
        return False
    if record_info.has_init:
        return any(default is None for _, _, default in record_info.init_params)
    return any(
        fld.default_value is None and not fld.is_factory_default
        and type_value_init_indeterminate(fld.type)
        for fld in record_info.fields
    )


@dataclass
class FieldInfo:
    """Information about a record field."""
    name: str
    type: TpyType
    default_value: Optional[str] = None
    default_expr: Optional[Any] = None  # TpyExpr from parser (avoid circular import)
    is_factory_default: bool = False  # True for field(default_factory=...)
    loc: Optional[Any] = None  # SourceLocation from parse.py (avoid circular import)
    native_name: Optional[str] = None  # C++ member name override from native_field(...)
    # `unsafe_interior_mutable[T]` marker, stripped from `type` at registration: mutations
    # reached *through* this field don't count against the owner's readonly-ness
    # and readonly does not propagate into the field. See InteriorMutableType.
    is_interior_mutable: bool = False


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
    inherits_init_from: 'Optional[NominalType]' = None  # Set when init_params were copied from an MRO ancestor; drives `using Base::Base;` codegen.
    init_params: list[tuple[str, TpyType, Optional[str]]] = field(default_factory=list)  # (name, type, default)
    methods: dict[str, list['FunctionInfo']] = field(default_factory=dict)  # method_name -> list of overloads
    properties: dict[str, 'PropertyInfo'] = field(default_factory=dict)  # property_name -> PropertyInfo
    class_constants: dict[str, FieldInfo] = field(default_factory=dict)  # class-scoped Final/ClassVar storage; partitioned out of `fields` during sema registration
    class_constants_finality: dict[str, bool] = field(default_factory=dict)  # per-entry: True for Final (read-only, static constexpr), False for mutable ClassVar (static inline)
    type_params: list[str] = field(default_factory=list)  # ["T", "U"] for class Stack[T, U]
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)  # [TYPE, INT] for class Matrix[T, N: int]
    type_param_bounds: dict[str, 'NominalType'] = field(default_factory=dict)  # {"T": Comparable} (must be protocols)
    type_factory: "Optional[Callable[..., TpyType]]" = None  # Factory to create concrete type from params
    parents: list['TpyType'] = field(default_factory=list)  # Direct base classes in source order (equals MRO tail order, enforced by _check_multi_base_order).
    mro_ancestors: list['TpyType'] = field(default_factory=list)  # C3 linearization of ancestors (self excluded), populated by validate_record_inheritance
    # True if a C++ member (own or MRO-inherited method/field/property/class-
    # constant) shares a name with a same-module record/enum/@dynamic-protocol,
    # so the member shadows that type in record scope -- codegen/THIR then render
    # local type references fully-qualified. Computed once by sema after
    # inheritance validation; the single source both consumers read.
    shadows_local_type: bool = False
    implemented_protocols: list['NominalType'] = field(default_factory=list)  # Explicit protocol implementations
    # Short names of every protocol this record implements directly OR
    # transitively via parent-protocol chains. Populated once by sema
    # after implemented_protocols is finalized; never mutated afterward.
    # Single source of truth for record-implements-protocol queries; see
    # tpyc.type_def_registry.is_subtype.
    transitive_supertypes: frozenset[str] = field(default_factory=frozenset)
    _is_polymorphic_class: 'Optional[bool]' = None  # Lazy cache for is_polymorphic_class_type; populated on first call
    # Phase 20: materialized at validate_record_inheritance time so both
    # sema (slicing-rule, conformance check) and codegen (auto-emit) read
    # one source of truth instead of re-walking the MRO each time.
    implements_throwable: bool = False
    inherits_base_exception: bool = False
    # A `ReturnException` class is a plain value that travels in the error slot
    # of a std::expected and is never thrown: it keeps `Exception` among its
    # Python bases (CPython requires it) but is not a Throwable, so
    # `implements_throwable` is False for it.
    is_return_exception: bool = False

    @property
    def is_native_exception_class(self) -> bool:
        """A runtime-defined exception class of either tier (`::tpy::OSError`,
        `::tpy::StopIteration`): constructed by its `@native` C++ name."""
        return self.is_native and (self.implements_throwable
                                   or self.is_return_exception)
    extends_protocols: list[str] = field(default_factory=list)  # Protocol extensions: ["NativeIterable[T]"]
    native_name: Optional[str] = None  # C++ name for @native/@native_c records (e.g., "SDL_Rect")
    is_native: bool = False       # True for @native or @native_c records
    is_native_c: bool = False     # True for @native_c specifically
    is_indirecting: bool = False  # True for @native(indirecting=True) records that own heap storage of T (cycle-breaking)
    is_borrowing_view: bool = False  # True for @native(borrowing_view=True): values are borrow handles (lifetime-checked)
    iter_yields_ref_tuple_proxies: bool = False  # True for @native(iter_yields_ref_tuple_proxies=True)
    is_nocopy: bool = False       # True for @nocopy records (copy deleted, move-only)
    match_args: tuple[str, ...] | None = None  # Positional match arg names (set by macro, mirrors __match_args__)
    is_frozen: bool = False       # True for @dataclass(frozen=True) (field mutation rejected)
    is_typed_dict: bool = False   # True for TypedDict (struct with string-literal subscript)
    is_total_false: bool = False  # True for TypedDict(total=False) -- all fields Optional, absent by default
    is_value_type: bool = False   # True for ValueType marker protocol
    is_send: bool = False         # True if record is Send (all fields Send + parent Send); derived by sema
    is_sync: bool = False         # True if record is Sync (all fields Sync + parent Sync); derived by sema
    # Decorator overrides: True from @unsafe_send/@unsafe_sync, False from
    # @nosend/@nosync, None = structural auto-derive. Consulted by the
    # generic-record per-instantiation walk in NominalType.is_send/is_sync;
    # for non-generic records registration folds them into is_send/is_sync.
    send_override: bool | None = None
    sync_override: bool | None = None
    # Conditional overrides (@unsafe_send/@unsafe_sync with if_params_*): a tuple of
    # marker-trait qnames (tpy.Send / tpy.Sync) every type param must satisfy
    # for the trait to hold at a given instantiation; None = not conditional.
    # Consulted by NominalType.is_send/is_sync between the unconditional
    # override and the structural walk.
    send_override_when: tuple[str, ...] | None = None
    sync_override_when: tuple[str, ...] | None = None
    has_del: bool = False           # True if class declares __del__ (needs drop flag)
    has_copy: bool = False          # True if class defines __copy__ (custom copy semantics)
    has_move: bool = False          # True if class defines __move__ (custom relocating move)
    is_movable: bool = True         # False if a field/parent is non-movable and no __move__; derived by sema
    move_override: bool | None = None  # False from @nomove; None = structural auto-derive
    builtin_type_key: str | None = None  # e.g. "builtins.list" -- links .py class to type_factory
    virtual_raise: bool = False  # @virtual_raise: `raise X(args)` routes through the dispatching C++ __raise__, not a fresh throw
    module: str | None = None  # Public module name (collapses private submodules via public_module_name); used for qualified_name() and codegen C++ namespace
    defining_module: str | None = None  # Raw (uncollapsed) module where the class was declared; used by re-export logic to look up the record through ModuleInfo.records
    exposed_to_host: bool = False  # True for a bare `@export` class in an ext_module: exposed as a CPython type (PyType_FromSpec). Mirrors TpyFunction.exposed_to_host.
    enum_companion_of: str | None = None  # The enum whose body's methods this record carries (`__enum_<Name>`); never bound by name, reached through EnumInfo.companion
    # Field names (declared on THIS record) that some body assigns outside an
    # `__init__` with a bare-`self` receiver -- i.e. the field can be REBOUND
    # after construction. Populated during Phase-1 body analysis at the
    # field-store check. The CPython-interop borrow-view gate admits only
    # never-rebound fields: a live view aliases the field's storage SLOT, so
    # a rebind would show through it where Python's rebind leaves the old
    # object intact.
    fields_rebound_outside_init: set[str] = field(default_factory=set)

    @property
    def is_keyword_stub(self) -> bool:
        """True for @builtin_type stubs with no methods or fields.

        These exist only for parser/import resolution (e.g. typing.Protocol,
        typing.overload) and should not generate C++ code.
        """
        return self.builtin_type_key is not None and not self.methods and not self.fields

    @property
    def display_name(self) -> str:
        """The name a diagnostic spells: an enum's companion is the enum the
        user wrote, never its internal record name."""
        return self.enum_companion_of or self.name

    @property
    def materializes_defaults(self) -> bool:
        """True if a ctor default here has no C++ default-argument spelling, so
        every construction must fill its omitted arguments explicitly. The
        `FunctionInfo` twin, over `init_params`' (name, type, default) shape."""
        # A ctor is a member: its record's alternatives are defined by the
        # time the in-class declaration is read.
        return any_default_suppressed(((t, d) for _n, t, d in self.init_params),
                                      is_member=True)

    def is_final_class_constant(self, name: str) -> bool:
        """True if `name` is declared on this record as a Final class
        constant (read-only `static constexpr`); False for mutable
        ClassVar (`static inline`). Defaults to True for missing keys --
        treating absent metadata as the safer read-only interpretation
        prevents accidental mutation if the finality dict drifts from
        `class_constants`.
        """
        return self.class_constants_finality.get(name, True)

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
    is_variadic: bool = False  # True for *args param (type Span[T], or Span[readonly[T]] for readonly *args)
    positional_only: bool = False  # True for params before a '/' separator
    is_kwargs: bool = False  # True for the **kwargs param (type Unpack[TypedDict])

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
    # The caller param a receiver rooted at `self` is (`self.m()`,
    # `self.f.m()`, `super().m()`): -1 for a method's own receiver (folded
    # into self_mutated), the param's index where `self` is an ordinary param
    # (a generator expression's capture); None when the receiver is not
    # rooted at `self`.
    receiver_idx: int | None = None
    # Callee params bound through a call that LENDS the caller's storage (a
    # combinator, a borrow-returning call): only the callee's element
    # mutation of such a param reaches the caller's storage.
    lent: frozenset[int] = frozenset()


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
    # `is_readonly` was INFERRED from the body (the receiver is never
    # mutated), not declared: only the receiver's const-ness is proven.
    readonly_inferred: bool = False
    is_pure: bool = False
    is_inline: bool = False
    is_consuming: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    # A @classmethod is also is_staticmethod; this distinguishes it (diagnostics
    # and the inherited-receiver check, which `cls`'s static binding requires).
    is_classmethod: bool = False
    is_async: bool = False  # `async def` -- factory returns a coroutine struct
    is_generator: bool = False  # `yield` body -- factory returns an iterator/frame that borrows the receiver + args
    # async def only: the raw declared return (`C`, `Own[C]`, `T`, ...)
    # BEFORE the Cancellable[T] wrap. `return_type` carries the wrapped,
    # Own-normalized call-site type, which cannot distinguish a borrow
    # contract (`-> C`) from an ownership one (`-> Own[C]`); the
    # async-return-form classification (value_category.async_return_form)
    # reads this field.
    async_inner_return: Optional[TpyType] = None
    is_property_getter: bool = False
    is_property_setter: bool = False
    property_name: Optional[str] = None  # for setter: which property it belongs to
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: Optional[str] = None
    native_function: bool = False  # @native("func", function=True) -> generates func(self, args)
    native_preserves_refs: bool = False  # non-readonly but doesn't invalidate iterators/refs
    copy_returns_warn: bool = False  # Own[V] accessor copies where CPython aliases -> warn at call sites
    # `__enter__` only (computed at registration): can what this returns root
    # at `self`? False means it lends storage that is NOT the receiver's, so a
    # `with` target aliasing it does not force the manager to stay alive.
    returns_self_borrow: bool = True
    # @native(cpp_return_type=T): C++ side returns a wider/different type
    # than the declared TPy return. Codegen wraps the call in
    # static_cast<DECLARED_TPY_RETURN>(...) so -Wsign-conversion /
    # -Wconversion don't fire at the use site. Carries the user-supplied
    # type-name string; today consumed only as a marker.
    native_cpp_return_type: Optional[str] = None
    type_params: list[str] = field(default_factory=list)
    # Protocol bound (capability) OR a class / type-param subtype bound (`U: Animal`, `U: T`).
    type_param_bounds: dict[str, 'TpyType'] = field(default_factory=dict)
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "tpy.extern.DefaultInt"}
    cpp_template: Optional[str] = None  # For builtins: "{self}.push_back({0})"
    value_ptr_coercion: bool = False  # @value_ptr_coercion: Ptr[T] params accept T values
    is_builtin_function: bool = False  # True for global builtins (len, chr, etc.)
    is_constructor: bool = False  # True for synthetic record-constructor FunctionInfo
    # (return_type is the record itself, the call is an rvalue; distinguishes
    # from a regular function declared to return that type which would emit T&).
    # Only a unique ordinary source declaration; overloads/redefinitions stay absent.
    declaration: TpyFunction | None = field(default=None, repr=False, compare=False)
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
    # Param indices whose address escapes into a mutable Ptr[T] field via
    # `self.FIELD = PARAM`. Used to suppress the `const T&` perf-default param
    # emission that would otherwise trigger `const T* -> T*` in the member
    # initializer list. Populated during body analysis; never propagated
    # transitively (escape is a per-body structural fact).
    addr_escapes_params: frozenset[int] = frozenset()
    structural_mutated_params: Optional[frozenset[int]] = None
    # Like mutated_params, but only structural mutations (append/insert/clear/del/etc.) that
    # invalidate element references. Excludes element-ref taking (a=items[0]) and field writes.
    # None = not yet analyzed; frozenset() = no structural mutation.
    elem_mutated_params: Optional[frozenset[int]] = None
    # Like mutated_params, but only mutations THROUGH what the param lends --
    # a loop variable over it, an element or field borrow off it -- not of the
    # param itself (an advance of an iterator, an append). An argument bound
    # through a call that lends its result (`g(zip(xs, ys))`) carries only
    # this: advancing the combinator mutates nothing the caller passed.
    # Subset of mutated_params. None = not yet analyzed.
    return_borrows_from: Optional[frozenset[int]] = None
    # Param indices whose storage the return value borrows from (8b).
    # -1 = self (methods only); 0, 1, ... = regular params.
    # None = not yet analyzed; frozenset() = no borrow (value/local return).
    held_whole_params: frozenset[int] = frozenset()
    # The part of return_borrows_from the result holds only as a reference to
    # the whole object: it neither iterates that storage nor hands out a
    # reference into it, so growing it leaves the result valid (a genexpr's
    # captures, read afresh at each pull).
    # Phase 1 local facts (set during sema, consumed by Phase 2 propagation)
    direct_mutated_params: Optional[frozenset[int]] = None
    direct_structural_mutated_params: Optional[frozenset[int]] = None
    direct_elem_mutated_params: Optional[frozenset[int]] = None
    call_edges: Optional[list['MutationCallEdge']] = None
    # Method type-params whose `U: T` bound was used representationally in the
    # body (e.g. `Ptr[U] -> Ptr[T]` coercion). Codegen reads this at call sites
    # to decide adapter materialization for structural conformers.
    representational_type_params: frozenset[str] = frozenset()
    # Owning-slot copies in this body whose payload still names a type param,
    # and the generic callees this body instantiates with such a payload. An
    # instantiation discharges the first and composes through the second
    # (sema/own_copy.py).
    own_copy_obligations: tuple = ()
    own_copy_forwards: tuple = ()
    # Self-mutation inference (Phase 1 + Phase 2, methods only)
    # None = not yet analyzed; True/False = Phase 1 direct fact; finalized by Phase 2.
    direct_self_mutated: Optional[bool] = None
    self_mutated: bool = True  # conservative default until Phase 2 resolves
    # Back-pointer to the canonical registry FunctionInfo. Set on ephemeral
    # copies produced by type-param substitution / cpp_template fill-in so
    # mutation propagation reads facts from the canonical (which evolves
    # through Phase 1 + Phase 2) rather than from a stale snapshot.
    # compare/repr=False to keep dataclass equality and reprs unaffected.
    canonical_fi: Optional['FunctionInfo'] = field(default=None, compare=False, repr=False)
    # Auto-const inference must skip the mutable clone of an @auto_readonly
    # pair: flipping it to is_readonly=True would merge it with the const
    # sibling and break overload resolution.
    is_auto_readonly_mutable_clone: bool = False
    # Synthesized for a call through a callable VALUE (an Fn/Callable-typed
    # param, local or field). The signature comes from the Fn type, not from
    # a declaration the compiler has checked, so nothing about the callee's
    # conventions may be read off it -- the body that runs is a lambda or any
    # other conforming callable. Readers that would otherwise trust the
    # declared return (does it borrow? is it consuming?) must treat it as
    # opaque.
    is_callable_value: bool = False
    kwarg_name: Optional[str] = None  # name of **kwargs param (TypedDict type)
    # FStr inlining: body expression to inline at call sites.
    # Set during method body analysis for methods with FStr params.
    inline_body: Optional[Any] = None  # TpyExpr: body expression for @inline functions
    # Defining module name. Set when this FunctionInfo is registered
    # during the current module's signature finalization sub-phase,
    # so cross-module mutation propagation can tell apart "I own
    # this fi" from "I'm looking at a peer's signature". None on
    # opaque FIs (builtin / native / @builtin_function stubs and
    # ad-hoc FunctionInfos minted in sema for virtual calls / partial
    # application) -- readers must tolerate None as "unknown owner".
    originating_module: str | None = None
    # Codegen ABI facts: param indices whose emitted spelling and inner
    # surface should be const, computed by `param_const.populate_const_borrow_params`
    # AFTER Phase-2 mutation propagation finalizes mutated_params /
    # addr_escapes_params. None = not yet computed (consumers fall back to
    # mutable spellings). Distinguished from sema facts (mutated_params,
    # return_borrows_from): these never feed back into mutation analysis.
    # Populated on the RAW fi only; the public accessor below forwards
    # through `root`, so a substituted fi transparently reads the raw
    # fi's verdict -- writers go through `set_const_borrow_verdict`.
    # ONE set: the signature spelling and the inner surface answer together
    # for every type (`decide_param_const` returns both bools equal), so a
    # second set could only ever disagree by accident.
    _const_borrow_params: Optional[frozenset[int]] = None
    # Send/Sync frame facts (docs/SEND_SYNC_DESIGN.md OQ3). frame_type is the
    # memoized own-slot classification, computed lazily by sema/frame_traits.py
    # from the raw materials below (lazy because awaited sub-frames may
    # belong to functions whose bodies are analyzed later).
    # - frame_locals: hoisted locals (mirror of the AST generator_locals)
    # - frame_loop_var_names: locals that codegen may lower to raw-pointer
    #   slots -- classified conservatively as non-Send
    # - frame_subframes: FunctionInfos of awaited/delegated coroutines;
    #   a None entry is an unclassifiable await (forces non-Send)
    # - frame_captures: (name, type, by_ref) capture list for nested defs
    frame_type: Optional['FrameType'] = None
    frame_locals: Optional[list[tuple[str, 'TpyType']]] = None
    frame_loop_var_names: frozenset = frozenset()
    frame_subframes: Optional[list] = None
    frame_captures: Optional[list[tuple[str, 'TpyType', bool]]] = None
    # Decorator overrides for the frame answer: True from @unsafe_send /
    # @unsafe_sync, False from @nosend/@nosync, None = structural
    send_override: bool | None = None
    sync_override: bool | None = None

    @property
    def root(self) -> 'FunctionInfo':
        return self.canonical_fi or self

    @property
    def const_borrow_params(self) -> Optional[frozenset[int]]:
        return self.root._const_borrow_params

    def set_const_borrow_verdict(self, sig: frozenset[int]) -> None:
        """Store the per-param const ABI verdict on THIS fi (the raw fi --
        `populate_const_borrow_params` runs before any substitution)."""
        self._const_borrow_params = sig

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

    @property
    def borrows_receiver_via_auto_readonly(self) -> bool:
        """Mutable clone of an @auto_readonly accessor whose result borrows the
        receiver (Box.get / Rc.get / Deref). Such a call does not mutate its
        receiver; only a mutation through the borrowed result does. Used for
        mutation rooting and to keep the receiver const for read-only use.
        A value-returning clone (no -1 in return_borrows_from) returns a copy
        and is excluded.
        """
        return (self.is_auto_readonly_mutable_clone
                and -1 in recorded_return_borrow_sources(self))

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
    def materializes_defaults(self) -> bool:
        """True if a default here has no C++ default-argument spelling, so
        every call must fill its omitted arguments explicitly. Reads the same
        rule codegen's `default_emittable` does, so the two cannot disagree
        about which slots the caller owes.
        """
        return any_default_suppressed(
            ((p.type, p.default_expr) for p in self.params),
            is_member=self.is_method)

    @property
    def min_args(self) -> int:
        """Minimum number of required positional arguments (excludes keyword-only, variadic, **kwargs)."""
        return sum(1 for p in self.params
                   if not p.has_default and not p.keyword_only and not p.is_variadic
                   and not p.is_kwargs)

    @property
    def max_args(self) -> int:
        """Maximum number of total arguments (all params). Unlimited if variadic.

        Includes **kwargs param since call-site packing fills it as a positional arg.
        """
        if self.has_variadic:
            return 2**31
        return len(self.params)


def is_bodyless_binding(fn) -> bool:
    """A callable with NO body or MIL emit at all, so nothing is lowered
    for it -- a `FunctionInfo` or the `TpyFunction` it was registered from.

    A call-site dispatch to a runtime symbol / template -- method-style
    `@native("push_back")` (native_name), `@cpp_template(...)`, free
    `@native(function=True)` (native_function) -- or any `...` stub (is_stub
    covers declaration-only stubs like `cast`, native-class method stubs, and
    bare-`@native` methods whose native_name stays None). Covers the whole
    builtin-type method/ctor surface (str / int / list / dict / ...); a BODIED
    method on a builtin receiver still counts (a real deferred surface).

    Such a binding has no body sema could have analyzed, so no per-parameter
    mutation fact exists for it either: what it declares (`@readonly`) and
    what its signature says are all there is.
    """
    if fn.is_export:
        # An `@export(binding="C")` function carries a `native_name` for the
        # exported C symbol but has a real body the function driver emits.
        return False
    return (fn.native_function or fn.native_name is not None
            or fn.cpp_template is not None or fn.is_stub)


def recorded_return_borrow_sources(fi: FunctionInfo) -> frozenset[int]:
    """Recorded source indices; missing facts are not proof of an owning result.

    Read off the ROOT fi: a call site's `resolved_function_info` can be a
    specialization synthesized before the callee's body facts landed, so the
    copy still carries None (or a stale set) where the fact is now known --
    two readers of the same call site would otherwise disagree. Every
    PROVENANCE reader of the fact goes through here; a writer stamps the raw
    fi. The readiness gate in `_register_call_result_borrow` is the one reader
    that stays on the raw field on purpose: it pairs a None against
    `ctx.pending_borrow_fact_fis`, which holds the registry FIs, so the
    membership test and the None it qualifies must be about the same fi the
    call site resolved to. That leaves a synthesized fi's None unqualified
    (BUGS.md#pending-generic-receiver-call-borrow-unregistered).
    """
    return fi.root.return_borrows_from or frozenset()


def held_whole_borrow_sources(fi: FunctionInfo) -> frozenset[int]:
    """The recorded sources the result keeps a whole-object reference to
    and nothing more (see `FunctionInfo.held_whole_params`); root-read like
    `recorded_return_borrow_sources`."""
    return fi.root.held_whole_params


def return_const_projected(fi: FunctionInfo) -> bool:
    """Whether the emitted signature const-projects this callee's borrowed
    return (`const T&` / `const T*`), which every binding off the call must
    mirror.

    A DECLARED `@readonly` makes the receiver and every parameter readonly, so
    whatever the return borrows is const. An INFERRED one proves the receiver
    only: a return that borrows a PARAMETER keeps the declared mutable type,
    exactly as a free function's does, and that parameter stays in the mutated
    set. Read off the root: a call site's specialization can predate the
    inference.
    """
    root = fi.root
    if not (fi.is_readonly or root.is_readonly):
        return False
    if not root.readonly_inferred:
        return True
    if root.return_type is not None and view_is_inherently_const(root.return_type):
        return True
    # No recorded source keeps the projection: an open-`T` member read records
    # none and still lends the const receiver.
    sources = recorded_return_borrow_sources(fi)
    return not sources or -1 in sources


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


# Dunders implicitly marked is_readonly=True for the *single-overload* case.
# IMPLICIT_AUTO_READONLY_METHODS members appear here too, by design: when
# their return type is value-typed they take the single-overload path (no
# cloning, marked is_readonly=True via this set); when reference-typed they
# go through the dual-overload synthesis in method_expansion and the
# `mutable_clone_ids` check at registration.py keeps this set from
# clobbering the mutable clone. Keep the two sets in sync if either is
# updated.
IMPLICIT_READONLY_METHODS = frozenset({
    "__bool__", "__len__", "__getitem__", "__contains__", "__str__", "__repr__", "__hash__", "__eq__", "__ne__",
    "__lt__", "__le__", "__gt__", "__ge__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
    "__and__", "__or__", "__xor__", "__lshift__", "__rshift__",
    "__radd__", "__rsub__", "__rmul__", "__rtruediv__", "__rfloordiv__", "__rmod__", "__rpow__",
    "__neg__", "__pos__", "__invert__",
    "__copy__", "__deref__", "__span__",
})

# Reference-returning dunders that, when their return type is a reference
# type, need dual mutable/const overloads rather than a single const overload.
# method_expansion treats these as if the user wrote `@auto_readonly` and
# wraps the return with `AutoReadonlyType`, so the cloner produces both
# variants. Value-typed returns stay on the IMPLICIT_READONLY_METHODS path
# (single const overload is correct -- there's no aliasing to mutate).
#
# Without this, a class with `def __deref__(self) -> SomeRecord:` would be
# const-only and mutation through the wrapper (`box.x = v`) silently
# miscompiles as "assignment of member in read-only object". Users wanting a
# strict const-only contract can still write `@readonly` explicitly to opt
# out of the implicit dual-overload behavior.
#
# Strict subset of IMPLICIT_READONLY_METHODS; see the comment on that set.
IMPLICIT_AUTO_READONLY_METHODS = frozenset({
    "__deref__", "__getitem__", "__span__",
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

    Between parse and the post-parse `resolve_refs` pass,
    `params` and `return_type` may hold `TypeRefNode` in place of
    `TpyType`.  All readers post-pre-pass see TpyType.  `return_type`
    may also be None when no annotation was provided -- sema
    substitutes VOID during the pre-pass.

    `param_defaults`, when non-empty, holds a parallel list with one
    entry per param (None for required params, a TpyExpr for those
    with defaults). Empty list means no param carries a default --
    the common case for marker / dunder protocols. Used by protocol
    method dispatch to populate `ParamInfo.default_expr` so that
    `def f(fp: Seekable): fp.seek(0)` can drop the trailing
    `whence=0` default like a direct method call would.
    """
    name: str
    params: list[tuple[str, 'TpyType | TypeRefNode']]
    return_type: 'TpyType | TypeRefNode | None'
    is_readonly: bool = False
    readonly_opt_out: bool = False
    cpp_template: str | None = None
    param_defaults: list = field(default_factory=list)  # list[TpyExpr | None]
    num_posonly_params: int = 0  # leading params before a '/' separator (self excluded)


@dataclass
class ProtocolInfo:
    """Information about a protocol definition.

    For generic protocols like Sequence[T]:
    - type_params stores the type parameter names (e.g., ["T"])

    For builtin protocols:
    - cpp_concept stores the C++ concept name (e.g., "tpy::Sized", auto-qualified to "::tpy::Sized")
    - is_marker indicates protocols with no methods (require explicit extends)

    For protocol inheritance:
    - parent_protocols stores resolved NominalType references to parent
      protocols this protocol extends. Generic parents like `Iterable[T]`
      carry their type args in NominalType.type_args (referencing the
      child protocol's own type parameters).
    """
    name: str
    methods: list[MethodSignature]
    fields: list[tuple[str, TpyType]] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)
    parent_protocols: list['NominalType'] = field(default_factory=list)
    cpp_concept: str | None = None  # C++ concept name for builtin protocols
    is_marker: bool = False  # Marker protocols require explicit extends
    is_readonly: bool = False  # All methods are read-only (safe for readonly[T] args)
    is_dynamic: bool = False  # Supports runtime dispatch via base/adapter
    module: str = ""  # Module that defines this protocol (e.g. "typing", "tpy")
    # Short names of every protocol reachable via repeated parent_protocols
    # walks (self excluded). Populated once by sema after all protocols in
    # the module are registered; never mutated afterward. The single source
    # of truth for nominal protocol-to-protocol subtyping queries; see
    # tpyc.type_def_registry.is_subtype.
    transitive_supertypes: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ModuleVarInfo:
    """Information about a module-level variable."""
    name: str
    type: TpyType
    cpp_expr: str  # C++ expression to access the variable
    is_pointer: bool = False  # True for non-value-type module globals (stored as T* in C++)
    # For native_global variables: the absolute-qualified C++ name to emit at
    # use sites (e.g. "::engine::score" or "::DG_FrameCount"). Overrides the
    # module-namespace-based cpp_expr for cross-module references. None for
    # regular globals.
    native_cpp_name: str | None = None
    # True if the variable was declared Final[T] in its source module. The
    # FinalType wrapper is stripped during registration, so this flag is how
    # downstream modules recover "this reference is a compile-time constant".
    is_final: bool = False


@dataclass
class TypeAliasInfo:
    """Per-alias metadata for a `type X[...] = ...` declaration.

    `body` is the alias's RHS as a fully resolved TpyType (post
    `parse.resolve_refs`).  `type_params` / `type_param_kinds` describe the
    alias's generic parameters; both empty for non-generic aliases.
    `is_recursive` is True when the alias self-references through an
    indirecting container and codegen must emit a wrapper struct (see
    `recursive_union_names`).

    See `docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md` for the broader design;
    fields beyond v1's needs (bounds, defining_module) will land alongside
    the commits that consume them.
    """
    body: 'TpyType'
    type_params: list[str] = field(default_factory=list)
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)
    loc: Optional[Any] = None  # SourceLocation from parse.py (avoid circular import)
    is_recursive: bool = False


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
    type_aliases: dict[str, TypeAliasInfo] = field(default_factory=dict)  # alias_name -> resolved info
    recursive_union_names: set[str] = field(default_factory=set)  # subset of type_aliases that are recursive union aliases
    enums: dict[str, 'NominalType'] = field(default_factory=dict)  # enum_name -> NominalType (enum-kind)
    # Defining modules whose symbols this module's generated code references.
    # Computed by sema.reach_analysis. Used by codegen to propagate native-
    # module `# tpy: include(...)` directives transitively across native-to-
    # native chains (which have no .hpp to chain through).
    reached: set[str] = field(default_factory=set)
    # Per-module attribute table reference (Phase 2). Aliases the dict
    # owned by `CompiledModule.module_attributes`, so cross-module
    # sema readers can look names up via `module_info.lookup_attribute`.
    # None for builtin/synthetic ModuleInfo objects that have no
    # CompiledModule (e.g. the `sys` module, ad-hoc test fixtures).
    module_attributes: 'dict | None' = None

    def lookup_attribute(self, name: str) -> 'object | None':
        """Return the `BindingCell` for `name` or None if not bound.

        Phase 2 reader entry point. Returns the cell, not the binding,
        so cycle PLACEHOLDER cells (Phase 5) are visible to callers
        that want to follow the chain.
        """
        if self.module_attributes is None:
            return None
        return self.module_attributes.get(name)

    def has_export(self, name: str) -> bool:
        """Check if a name is exported by this module."""
        return (name in self.functions or name in self.records or
                name in self.protocols or name in self.enums or
                name in self.type_aliases or name in self.variables)

    @property
    def has_runtime_init(self) -> bool:
        # Hardcoded builtins (sys, etc.) have no .cpp; native_module files
        # compile to declarations only. Both lack a __tpy_init symbol.
        return not self.is_builtin and not self.is_native_module


class TypeRegistry:
    """Registry of all known types and symbols."""

    def __init__(self, shared_modules: 'dict[str, ModuleInfo] | None' = None):
        self.records: dict[str, RecordInfo] = {}
        # Qname -> RecordInfo indexes, split so builtin consumers
        # (`get_builtin_record` and its callers) don't accidentally
        # match user records that happen to share a qname namespace.
        self._qname_index: dict[str, RecordInfo] = {}       # builtins only
        self._user_qname_index: dict[str, RecordInfo] = {}  # user records only
        self.functions: dict[str, list[FunctionInfo]] = {}  # User-defined functions (single or @overload group)
        # Module-local short-name -> ProtocolInfo. Keyed by the name under
        # which the protocol is *visible in the current module* (its own
        # short name or an import alias). Read exclusively through
        # `scan_by_short_name`; qname-keyed reads go through
        # `type_def_registry.protocol_info_of(typ)` / `TypeDef.protocol`,
        # which is the authoritative payload store.
        self._protocols_by_local_name: dict[str, ProtocolInfo] = {}
        # `shared_modules` (when provided) is the workspace-wide
        # ModuleInfo dict owned by the Compiler. Every analyzer in the
        # same compilation aliases the same dict so qname-keyed cross-
        # module reads (`get_record_for_type`, recursive-union
        # detection, builder-trace type rendering) see every peer
        # module's records / type_aliases. Default = fresh per-registry
        # dict for the legacy per-analyzer mode.
        self.modules: dict[str, ModuleInfo] = (
            shared_modules if shared_modules is not None else {}
        )
        self.type_aliases: dict[str, TypeAliasInfo] = {}  # alias_name -> resolved info
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
        """Register a protocol's local-name binding in this analyzer's
        registry.

        The authoritative qname-keyed ProtocolInfo payload lives on the
        `TypeDef.protocol` entry attached by
        `sema.registration.register_protocol` via `attach_dynamic_type_def`;
        this registration only maintains the module-local name -> info
        table consulted by `scan_by_short_name`.

        Args:
            info: The protocol info to register.
            name: Optional name to register under (defaults to info.name).
                  Used for imported protocols that may have a local alias
                  (e.g. `from typing import Sized as MySized`).
        """
        self._protocols_by_local_name[name or info.name] = info
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
                            *, imported_from: tuple[str, str] | None = None,
                            info: 'TypeAliasInfo | None' = None) -> None:
        """Register a type alias (e.g., Shape = Circle | Rect).

        `info` carries generic-alias metadata (type_params, type_param_kinds,
        loc, is_recursive); pass None for non-generic aliases and a bare-body
        `TypeAliasInfo(body=typ)` is constructed.

        imported_from: (declaring_module, alias_name) if the alias was imported
            from another user module; None for locally-defined aliases.
        """
        self.type_aliases[name] = info if info is not None else TypeAliasInfo(body=typ)
        if imported_from is not None:
            self.imported_type_alias_info[name] = imported_from

    def get_type_alias(self, name: str) -> 'TpyType | None':
        """Get a type alias body by name, or None if not found.

        Returns the resolved body type; for generic-alias metadata (type
        params, recursion flag, loc) use `get_type_alias_info`.
        """
        entry = self.type_aliases.get(name)
        return entry.body if entry is not None else None

    def get_type_alias_info(self, name: str) -> 'TypeAliasInfo | None':
        """Get the full type alias info by name, or None if not found."""
        return self.type_aliases.get(name)

    def resolve_alias_ref(self, ref: 'AliasRef') -> 'TpyType | None':
        """Resolve a recursive-union `AliasRef`'s body by its defining module.

        Canonical entry point: every recursive-union `AliasRef` resolution
        (coercion, codegen elem-target, match dispatch, narrowing) must route
        here, not a bare caller-local `get_type_alias`, which misses a
        cross-module alias used without importing it (e.g. `json.dumps([...])`).
        Defining-module-first (the `AliasRef` carries it) is collision-proof
        across same-short-named aliases -- matching `_alias_lookup_for_finalize`.
        Returns the alias body, or None if not a known alias."""
        if ref.module is not None:
            mi = self.modules.get(ref.module)
            info = mi.type_aliases.get(ref.name) if mi is not None else None
            if info is not None:
                return info.body
        return self.get_type_alias(ref.name)

    def imported_record_qualification(
        self, name: str, current_module: str
    ) -> tuple[str, str] | None:
        """For a user-imported record: return (defining_module, canonical_name), else None.

        Short-name lookup in the current module's registry -- use this when the
        caller is iterating `registry.records` or knows the record was imported
        into the current module by name. For a lookup that also resolves records
        only reachable through an inferred cross-module type (e.g. the return
        type of `import mod; p = mod.f()`), use
        `imported_record_qualification_for_type`.

        Reads `RecordInfo.defining_module` -- the actual (uncollapsed) submodule
        where the class was declared. Both codegen (which qualifies C++ namespace
        via `_namespace_map` / `cpp_namespace` directives) and compiler.py's
        re-export logic (which looks up `ModuleInfo.records[original_name]`)
        want this form. For regular user code `defining_module == module` (the
        public name); they differ only for private submodules like
        `tpy._builtins._bytes` where `module` collapses to `tpy` / `builtins`.
        """
        return self._record_qualification(self.records.get(name), current_module)

    def imported_record_qualification_for_type(
        self, typ: 'NominalType', current_module: str
    ) -> tuple[str, str] | None:
        """Qname-aware variant of `imported_record_qualification`.

        Resolves the record via `get_record_for_type` (which consults the
        cross-module qname index), so a NominalType whose record lives in a
        module the caller only imported as `import mod` (no `from mod import X`)
        still qualifies correctly.
        """
        return self._record_qualification(self.get_record_for_type(typ), current_module)

    def record_qualification(
        self, record_info: 'RecordInfo', current_module: str
    ) -> tuple[str, str] | None:
        """RecordInfo-aware variant of `imported_record_qualification`.

        Use when the caller already holds the `RecordInfo` (e.g. resolved via
        an MRO walk, where the declaring ancestor may not be in the current
        module's short-name registry). Bypasses the registry lookup entirely.
        """
        return self._record_qualification(record_info, current_module)

    def _record_qualification(
        self, record_info: 'RecordInfo | None', current_module: str
    ) -> tuple[str, str] | None:
        if record_info is None or record_info.defining_module is None:
            return None
        source_module = record_info.defining_module
        if source_module == current_module:
            return None
        module_info = self.modules.get(source_module)
        if module_info is not None and module_info.is_builtin:
            return None
        return (source_module, record_info.name)

    def imported_protocol_qualification(
        self, name: str, current_module: str
    ) -> tuple[str, str] | None:
        """For a @dynamic protocol imported into the current module:
        return `(defining_module, canonical_name)`, else None.

        Mirrors `imported_record_qualification` for the protocol side --
        @dynamic protocols carry a runtime vtable, so cross-module
        references need the qualified C++ name (the same machinery that
        registers user records into `Compiler.native_cpp_names` per emit-module).
        Static protocols are skipped: they monomorphize at use sites and
        never appear as runtime types.
        """
        info = self._protocols_by_local_name.get(name)
        if info is None or not info.is_dynamic or not info.module:
            return None
        if info.module == current_module:
            return None
        module_info = self.modules.get(info.module)
        if module_info is not None and module_info.is_builtin:
            return None
        return (info.module, info.name)

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

    @staticmethod
    def is_struct_base(record: RecordInfo, ancestor: RecordInfo) -> bool:
        """True when `ancestor` is a base of `record`'s C++ struct. A return
        exception names the thrown `Exception` classes among its Python bases
        (CPython only raises BaseException subclasses) but its struct derives
        from the empty value base instead, so their fields, constructors and
        by-reference dispatch are not part of it."""
        return not (record.is_return_exception and ancestor.implements_throwable)

    def iter_field_ancestors(self, record: RecordInfo,
                             reverse: bool = False) -> Iterator[RecordInfo]:
        """The ancestors whose fields `record` really has (see `is_struct_base`)."""
        for anc_rec in self.iter_ancestor_records(record, reverse=reverse):
            if self.is_struct_base(record, anc_rec):
                yield anc_rec

    def get_all_fields(self, record: RecordInfo) -> list[FieldInfo]:
        """Get all fields for a record including inherited, in base-first order.

        Post-registration (mro_ancestors populated) walks the MRO so multi-base
        records contribute all parents' fields -- required by sema/match.py
        callers that bind pattern fields to ancestry slots.

        Pre-registration (during macro execution) falls back to a parent-chain
        walk: mro_ancestors is not yet computed and `parents` only holds the
        provisional single parent set in register_record. Under the v1 invariant
        this matches the old single-parent behavior exactly.
        """
        if record.mro_ancestors:
            result: list[FieldInfo] = []
            for anc_rec in self.iter_field_ancestors(record, reverse=True):
                result.extend(anc_rec.fields)
            result.extend(record.fields)
            return result
        if not record.parents or not isinstance(record.parents[0], NominalType):
            return list(record.fields)
        parent = self.get_record(record.parents[0].name)
        if parent is None:
            return list(record.fields)
        return self.get_all_fields(parent) + list(record.fields)

    def user_declared_fields(self, record: RecordInfo) -> list[FieldInfo]:
        """Fields declared on a record and its NON-native ancestor records,
        base-first. Unlike get_all_fields, this skips native-base fields -- for a
        user exception the native BaseException carries a `message` field that is
        the what()/str() source, not a data attribute, so it must not be treated
        as one. Used for the CPython-interop exception data-field crossing, which
        marshals only user-declared data fields (own + inherited from user bases).
        Post-registration only (walks mro_ancestors); every current caller
        (sema validation + codegen) runs after registration.
        """
        result: list[FieldInfo] = []
        for anc in self.iter_ancestor_records(record, reverse=True):
            if not anc.is_native:
                result.extend(anc.fields)
        result.extend(record.fields)
        return result

    def find_record_by_qname(self, qname: str) -> Optional[RecordInfo]:
        """Find a record by qualified name (e.g. 'builtins.Exception')."""
        # Check @builtin_type index first
        result = self._qname_index.get(qname)
        if result is not None:
            return result
        # User record qname index (records added via register_record /
        # register_module since Phase 1's workspace-shared modules dict
        # populates this for cross-module lookups).
        result = self._user_qname_index.get(qname)
        if result is not None:
            return result
        # Fall back to module lookup, then short-name fallback. The
        # short-name fallback covers the entry-point case: a module
        # never has its own ModuleInfo registered in `registry.modules`
        # during its own analyze, so `find_module_record("__main__", ...)`
        # would return None even though the record is in `self.records`.
        if "." in qname:
            module_name, short = qname.rsplit(".", 1)
            result = self.find_module_record(module_name, short)
            if result is not None:
                return result
            return self.records.get(short)
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

    def iter_ancestor_records(self, record: RecordInfo, *, reverse: bool = False) -> Iterator[RecordInfo]:
        """Yield each RecordInfo in record.mro_ancestors, skipping non-user-record
        ancestors and unresolved references. reverse=True walks root-first (for
        base-first field layout); default is MRO order (nearest-first)."""
        seq = reversed(record.mro_ancestors) if reverse else record.mro_ancestors
        for anc in seq:
            if isinstance(anc, NominalType):
                rec = self.get_record_for_type(anc)
                if rec is not None:
                    yield rec

    def iter_dynamic_protocols(
        self, record: RecordInfo,
    ) -> Iterator[tuple['NominalType', 'ProtocolInfo']]:
        """Yield ``(proto, proto_info)`` for every ``@dynamic`` protocol
        ``record`` or its MRO ancestor records declare in
        ``implemented_protocols``. No dedupe; consumers that care (e.g.
        ``_get_dynamic_override_info``'s first-wins map) dedupe at
        their own granularity. Diamond inheritance is rejected at sema
        so a protocol reachable via multiple MRO paths is rare.
        """
        from .type_def_registry import protocol_info_of
        for owner in (record, *self.iter_ancestor_records(record)):
            for proto in owner.implemented_protocols:
                proto_info = protocol_info_of(proto)
                if proto_info is not None and proto_info.is_dynamic:
                    yield proto, proto_info

    def find_class_constant_owner(self, record: RecordInfo, field_name: str) -> 'RecordInfo | None':
        """Return the record that declares `field_name` as a class constant,
        starting from `record` and walking its MRO ancestors. None if not
        declared on `record` or any ancestor.
        """
        if field_name in record.class_constants:
            return record
        for ancestor in self.iter_ancestor_records(record):
            if field_name in ancestor.class_constants:
                return ancestor
        return None

    def compute_mro_ancestors(self, record: RecordInfo) -> list['TpyType']:
        """Compute C3 linearization of a record's ancestors (self excluded).

        Pre-resolved parents in `record.parents` are fed to c3_linearize along
        with each parent's already-computed mro_ancestors. Parents that are
        builtin TpyTypes (non-user records) are treated as leaves -- they
        contribute themselves but no further ancestors.

        Callers are responsible for storing the result on record_info.mro_ancestors
        and for surfacing C3LinearizationError as a user-facing diagnostic.
        """
        if not record.parents:
            return []
        bases_with_mros: list[tuple[TpyType, list[TpyType]]] = []
        for p in record.parents:
            parent_info = None
            if isinstance(p, NominalType) and p.is_user_record:
                parent_info = self.get_record_for_type(p)
            if parent_info is not None:
                bases_with_mros.append((p, list(parent_info.mro_ancestors)))
            else:
                bases_with_mros.append((p, []))
        return c3_linearize(bases_with_mros)

    def detect_diamond(self, record: RecordInfo) -> Optional[tuple[str, str, str]]:
        """Return (shared_ancestor_name, first_parent_name, second_parent_name) if a
        diamond is present in record's base hierarchy, else None.

        A diamond exists when some type is reachable from more than one of record's
        direct parents. Non-virtual C++ MI would duplicate the shared subobject, so
        D22 rejects diamonds and directs users to @dynamic for runtime polymorphism.
        """
        if len(record.parents) < 2:
            return None
        ancestries: list[list[TpyType]] = []
        for p in record.parents:
            ancestry: list[TpyType] = [p]
            if isinstance(p, NominalType) and p.is_user_record:
                p_info = self.get_record_for_type(p)
                if p_info is not None:
                    ancestry.extend(p_info.mro_ancestors)
            ancestries.append(ancestry)

        def _type_label(t: TpyType) -> str:
            return t.name if isinstance(t, NominalType) else str(t)

        for i in range(len(ancestries)):
            for j in range(i + 1, len(ancestries)):
                for anc in ancestries[i]:
                    for other in ancestries[j]:
                        if same_base_type(anc, other):
                            return (
                                _type_label(anc),
                                _type_label(record.parents[i]),
                                _type_label(record.parents[j]),
                            )
        return None

    def is_subclass_of(self, child: 'TpyType', parent: 'TpyType') -> bool:
        """Check if child is a strict subclass of parent (walking the MRO).

        Compares name + type_args at each level so generic parents are
        matched correctly (e.g. IntContainer -> Container[int32]).
        Returns False for child == parent; use `is_subclass_of_or_equal`
        when same-type should count.
        """
        if not (isinstance(child, NominalType) and child.is_user_record
                and isinstance(parent, NominalType) and parent.is_user_record):
            return False
        child_info = self.get_record_for_type(child)
        if child_info is None:
            return False
        # MRO-based membership. Parent references recorded pre-qname-mint may
        # be bare; `parent` argument may be qname-bearing. `loose` matches
        # strictly when both have qnames (keeps cross-module shadowing honest)
        # and permissively when either is bare.
        for ancestor in child_info.mro_ancestors:
            if same_nominal_symbol_loose(ancestor, parent):
                return True
        return False

    def is_subclass_of_or_equal(self, child: 'TpyType', parent: 'TpyType') -> bool:
        """True if child is parent or a subclass of parent. Mirrors Python's
        `isinstance(x, type(x)) is True` and `issubclass(T, T) is True`."""
        return child == parent or self.is_subclass_of(child, parent)

    def is_subclass_of_record(self, child: RecordInfo, parent: RecordInfo) -> bool:
        """Check if child record inherits from parent (by record identity)."""
        for rec in self.iter_ancestor_records(child):
            if rec is parent:
                return True
        return False

    def get_method_overloads_with_parents(
        self, record: RecordInfo, method_name: str,
    ) -> list['FunctionInfo']:
        """Look up method overloads on a record, walking the MRO.

        Returns the first match found (own methods take precedence over inherited).
        Does NOT apply type substitution for generic parents -- callers that need
        substitution should use ProtocolChecker.lookup_record_method_overloads.
        """
        overloads = record.get_method_overloads(method_name)
        if overloads:
            return overloads
        for anc_info in self.iter_ancestor_records(record):
            overloads = anc_info.get_method_overloads(method_name)
            if overloads:
                return overloads
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

    def receiver_record(self, tpy_type: 'TpyType') -> Optional[RecordInfo]:
        """The record whose methods and properties a receiver of `tpy_type`
        resolves against: `get_record_for_type`, except that an enum answers
        its companion record. Only method / property lookup reads it -- an
        enum is never a record to the storage and lowering gates."""
        einfo = _enum_info_of(tpy_type)
        if einfo is not None:
            return einfo.companion
        return self.get_record_for_type(tpy_type)

    def scan_by_short_name(self, name: str) -> Optional[ProtocolInfo]:
        """Resolve a module-local short name (or import alias) to a
        ProtocolInfo.

        Intended for callers that only have a bare string (parent-protocol
        names in `TpyProtocol.parent_protocols`, `except`-clause exception
        types, `isinstance(x, T)` second-arg TpyName, compile-time
        protocol-name lookups in codegen). Callers that already have a
        `NominalType` should go through `protocol_info_of(typ)` instead,
        which is qname-keyed via the TypeDef registry.

        The authoritative payload store is `TypeDef.protocol`; this table
        only exists to translate names that haven't yet been resolved
        against the current module's imports.
        """
        return self._protocols_by_local_name.get(name)

    def get_enum(self, name: str) -> Optional['NominalType']:
        return self.enums.get(name)

    def find_enum_by_qname(self, qname: str) -> Optional['NominalType']:
        """Find an enum NominalType by qualified name (e.g. 'colors.Color').

        Routes through the declaring module's own `ModuleInfo.enums` dict so a
        cross-module reference resolves even when the bare-name `self.enums`
        slot holds an unrelated local enum of the same canonical name; mirrors
        `find_record_by_qname`'s module-dict + short-name fallback tail. No
        dedicated qname index is needed -- a local enum always wins its own
        bare slot, so the short-name fallback stays authoritative for
        current-module refs (whose `ModuleInfo` isn't in `self.modules` yet).
        """
        if "." in qname:
            module_name, short = qname.rsplit(".", 1)
            mod = self.modules.get(module_name)
            if mod is not None and short in mod.enums:
                return mod.enums[short]
            return self.enums.get(short)
        return self.enums.get(qname)

    def is_known_type(self, name: str) -> bool:
        """Check if a name refers to a known type."""
        if name in self._fundamental_types:
            return True
        if name in self.records or name in self._protocols_by_local_name or name in self.type_aliases or name in self.enums:
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
