"""
TurboPython Namespace System

Unified name resolution for all binding types:
- Variables (local and global)
- User-defined functions
- User-defined records (classes)
- Imported modules
- Imported names (from X import Y)
- Builtins (__name__, etc.)

The namespace forms a chain: local_ns -> global_ns -> builtins_ns
Lookup traverses the chain, so inner bindings automatically shadow outer ones.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .typesys import TpyType, FunctionInfo, RecordInfo, NominalType


class BindingKind(Enum):
    """The kind of name binding."""
    VARIABLE = auto()       # Local or global variable
    FUNCTION = auto()       # User-defined function
    RECORD = auto()         # User-defined record (class)
    ENUM = auto()           # User-defined enum type
    MODULE = auto()         # import X
    IMPORTED_NAME = auto()  # from X import Y
    BUILTIN = auto()        # Built-in names like __name__


@dataclass
class NameBinding:
    """A binding of a name to some entity."""
    kind: BindingKind
    name: str
    type: Optional[TpyType] = None           # For VARIABLE/BUILTIN
    func_infos: Optional[list[FunctionInfo]] = None  # For FUNCTION (single or @overload group)
    record_info: Optional[RecordInfo] = None  # For RECORD
    enum_type: Optional[NominalType] = None   # For ENUM (NominalType with TypeDef.enum)
    import_source: Optional[tuple[str, str]] = None  # For IMPORTED_NAME: (module, original_name)
    # RECORD only: sema introduced this binding under a name that is not the
    # record's own and has no C++ spelling of its own (`cls` in a
    # @classmethod), so consumers must spell the record, never the binding.
    is_sema_alias: bool = False
    # Registered for name resolution only, so frame collection skips it. Set
    # where the enclosing construct provides the resumable-frame storage
    # itself: a `with` item slot, or match's per-arm state. That coverage is
    # narrower than the constructs are -- a target the construct does not
    # cover has no slot at all today (see BUGS.md) -- so this flag preserves
    # the pre-existing frame layout rather than asserting one.
    frame_exempt: bool = False


class Namespace:
    """A namespace containing name bindings with optional parent chain.

    Namespaces form a chain for nested scopes:
    - builtins_ns (root, no parent)
    - global_ns (parent=builtins_ns)
    - local_ns (parent=global_ns, per function/method)

    Lookup traverses the chain from inner to outer, so inner bindings
    automatically shadow outer ones.
    """

    def __init__(self, parent: Optional[Namespace] = None):
        self.parent = parent
        self._bindings: dict[str, NameBinding] = {}

    def bind(self, binding: NameBinding) -> None:
        """Add a binding to this namespace."""
        self._bindings[binding.name] = binding

    def bind_variable(self, name: str, typ: TpyType) -> None:
        """Convenience method to bind a variable."""
        self.bind(NameBinding(kind=BindingKind.VARIABLE, name=name, type=typ))

    def bind_capture(self, name: str, typ: TpyType, *,
                     frame_exempt: bool) -> None:
        """Bind an `as`-capture (except / with / match target) as a variable.

        Never downgrades frame residency: when the name is already a
        frame-resident local, its assignments write to that slot, so a capture
        reusing the name must not strip it.
        """
        prior = self._bindings.get(name)
        if (prior is not None and prior.kind == BindingKind.VARIABLE
                and not prior.frame_exempt):
            frame_exempt = False
        self.bind(NameBinding(kind=BindingKind.VARIABLE, name=name, type=typ,
                              frame_exempt=frame_exempt))

    def bind_function(self, info: FunctionInfo) -> None:
        """Convenience method to bind a single user-defined function."""
        self.bind(NameBinding(kind=BindingKind.FUNCTION, name=info.name, func_infos=[info]))

    def bind_record(self, info: RecordInfo) -> None:
        """Convenience method to bind a user-defined record."""
        self.bind(NameBinding(kind=BindingKind.RECORD, name=info.name, record_info=info))

    def bind_enum(self, enum_type: NominalType, name: str | None = None) -> None:
        """Convenience method to bind a user-defined enum."""
        self.bind(NameBinding(kind=BindingKind.ENUM, name=name or enum_type.name, enum_type=enum_type))

    def bind_module(self, module_name: str, alias: str | None = None) -> None:
        """Convenience method to bind an imported module.

        Args:
            module_name: The canonical module name (e.g., "mypackage.utils").
            alias: Optional local name (e.g., "utils"). If None, uses
                   module_name as the local name.
        """
        local_name = alias if alias else module_name
        self.bind(NameBinding(
            kind=BindingKind.MODULE,
            name=local_name,
            import_source=(module_name, local_name) if local_name != module_name else None
        ))

    def bind_imported_name(self, name: str, module: str, original_name: str) -> None:
        """Convenience method to bind an imported name (from X import Y)."""
        self.bind(NameBinding(
            kind=BindingKind.IMPORTED_NAME,
            name=name,
            import_source=(module, original_name)
        ))

    def bind_builtin(self, name: str, typ: TpyType) -> None:
        """Convenience method to bind a builtin name."""
        self.bind(NameBinding(kind=BindingKind.BUILTIN, name=name, type=typ))

    def unbind(self, name: str) -> None:
        """Remove a binding from this namespace, if present.

        Only the local level -- an outer binding of the same name becomes
        visible again, which is what a scoped capture (`except ... as`)
        going out of scope means.
        """
        self._bindings.pop(name, None)

    def lookup(self, name: str) -> Optional[NameBinding]:
        """Look up a name in this namespace and its parents.

        Returns the binding if found, None otherwise.
        Inner bindings automatically shadow outer ones.
        """
        if name in self._bindings:
            return self._bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def own_variables(self) -> dict[str, TpyType]:
        """The VARIABLE bindings of this level only, without parents."""
        return {name: b.type for name, b in self._bindings.items()
                if b.kind == BindingKind.VARIABLE and b.type is not None}

    def lookup_local(self, name: str) -> Optional[NameBinding]:
        """Look up a name only in this namespace (not parents).

        Useful for checking if a name is defined in the current scope
        without traversing the chain.
        """
        return self._bindings.get(name)

    def update_variable_type(self, name: str, typ: TpyType) -> bool:
        """Update the type of an existing variable binding.

        Returns True if the binding was found and updated, False otherwise.
        Only searches the local namespace (not parents).
        """
        binding = self._bindings.get(name)
        if binding and binding.kind == BindingKind.VARIABLE:
            binding.type = typ
            return True
        return False

    def update_variable_type_recursive(self, name: str, typ: TpyType) -> bool:
        """Update an existing variable binding at the level where it lives.

        Walks up the parent chain; rewrites the first VARIABLE binding found
        and returns True. Returns False if the name is not bound or the
        binding is non-variable.
        """
        ns: Optional[Namespace] = self
        while ns is not None:
            binding = ns._bindings.get(name)
            if binding is not None:
                if binding.kind == BindingKind.VARIABLE:
                    binding.type = typ
                    return True
                return False
            ns = ns.parent
        return False

    def all_bindings(self) -> dict[str, NameBinding]:
        """Return all bindings in this namespace (not including parents)."""
        return dict(self._bindings)

    def __contains__(self, name: str) -> bool:
        """Check if a name is bound in this namespace (not including parents)."""
        return name in self._bindings

    def __repr__(self) -> str:
        names = list(self._bindings.keys())
        parent_str = f" -> {repr(self.parent)}" if self.parent else ""
        return f"Namespace({names}{parent_str})"
