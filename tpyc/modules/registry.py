"""Module registry: builtin module name set and type object lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tpyc.typesys import TpyType

# Builtin module names -- used by the compiler to distinguish builtin
# imports from user imports. These modules are always compiled from
# .py stubs under lib/tpy/ (see Compiler._IMPLICIT_STDLIB).
_BUILTIN_MODULE_NAMES = {"builtins", "tpy"}


def get_builtin_module_names() -> set[str]:
    """Get the names of all builtin modules.

    Used by the compiler to distinguish user module imports from builtin imports.
    """
    return _BUILTIN_MODULE_NAMES


def get_builtin_type_obj(qname: str) -> "TpyType | None":
    """Get the type object for a builtin type by qualified name (e.g. 'tpy.Float32').

    Uses type factories from .py-defined builtin types.
    """
    from tpyc.modules.type_resolution import _get_type_factories

    entry = _get_type_factories().get(qname)
    if entry and not entry[0]:  # no type params -> non-generic singleton
        return entry[1]()
    return None
