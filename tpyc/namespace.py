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
    from .typesys import TpyType, FunctionInfo, RecordInfo, EnumType


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
    enum_type: Optional[EnumType] = None      # For ENUM
    import_source: Optional[tuple[str, str]] = None  # For IMPORTED_NAME: (module, original_name)


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

    def bind_function(self, info: FunctionInfo) -> None:
        """Convenience method to bind a single user-defined function."""
        self.bind(NameBinding(kind=BindingKind.FUNCTION, name=info.name, func_infos=[info]))

    def bind_record(self, info: RecordInfo) -> None:
        """Convenience method to bind a user-defined record."""
        self.bind(NameBinding(kind=BindingKind.RECORD, name=info.name, record_info=info))

    def bind_enum(self, enum_type: EnumType, name: str | None = None) -> None:
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
