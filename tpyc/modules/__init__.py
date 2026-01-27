"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tpyc.typesys import TpyType


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: TpyType


@dataclass
class MethodDef:
    """Definition of a function overload or type method."""
    params: list[ParamDef]
    returns: TpyType
    cpp: str  # Template: "{0}" for args, "{self}" for receiver


@dataclass
class BuiltinFunctionDef:
    """Definition of a built-in function with its overloads."""
    name: str
    overloads: list[MethodDef]


@dataclass
class BuiltinTypeDef:
    """Definition of a built-in type with its methods."""
    qualified_name: str
    cpp_type: str
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)


class BuiltinModule:
    """A module containing built-in functions and types."""

    def __init__(self, name: str):
        self.name = name
        self.functions: dict[str, BuiltinFunctionDef] = {}
        self.types: dict[str, BuiltinTypeDef] = {}  # keyed by qualified name

    def function(self, name: str, overloads: list[MethodDef]):
        """Register a built-in function."""
        self.functions[name] = BuiltinFunctionDef(name=name, overloads=overloads)

    def type(self, name: str, cpp_type: str, methods: dict[str, list[MethodDef]] | None = None):
        """Register a built-in type. Stored with qualified name (module.name)."""
        qualified_name = f"{self.name}.{name}"
        self.types[qualified_name] = BuiltinTypeDef(
            qualified_name=qualified_name,
            cpp_type=cpp_type,
            methods=methods or {},
        )


# Module instances (lazy-loaded)
_builtins: BuiltinModule | None = None
_tpy: BuiltinModule | None = None


def get_builtins() -> BuiltinModule:
    """Get the builtins module, loading it on first access."""
    global _builtins
    if _builtins is None:
        from tpyc.modules import builtins as builtins_module
        _builtins = builtins_module.module
    assert _builtins is not None
    return _builtins


def get_tpy() -> BuiltinModule:
    """Get the tpy module, loading it on first access."""
    global _tpy
    if _tpy is None:
        from tpyc.modules import tpy as tpy_module
        _tpy = tpy_module.module
    assert _tpy is not None
    return _tpy


def _all_modules() -> list[BuiltinModule]:
    """Get all loaded modules."""
    return [get_builtins(), get_tpy()]


def lookup_function(name: str) -> BuiltinFunctionDef | None:
    """Lookup a function by name in the built-in modules."""
    for module in _all_modules():
        if fn := module.functions.get(name):
            return fn
    return None


def lookup_type(qualified_name: str) -> BuiltinTypeDef | None:
    """Lookup a type by qualified name (e.g., 'tpy.Array', 'builtins.list')."""
    for module in _all_modules():
        if typ := module.types.get(qualified_name):
            return typ
    return None


def lookup_type_method(qualified_type_name: str, method_name: str) -> list[MethodDef] | None:
    """Lookup a method on a built-in type by qualified type name."""
    if typ := lookup_type(qualified_type_name):
        return typ.methods.get(method_name)
    return None
