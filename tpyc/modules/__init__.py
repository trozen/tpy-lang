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
    name: str
    cpp_type: str
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)


class BuiltinModule:
    """A module containing built-in functions and types."""

    def __init__(self, name: str):
        self.name = name
        self.functions: dict[str, BuiltinFunctionDef] = {}
        self.types: dict[str, BuiltinTypeDef] = {}

    def function(self, name: str, overloads: list[MethodDef]):
        """Register a built-in function."""
        self.functions[name] = BuiltinFunctionDef(name=name, overloads=overloads)

    def type(self, name: str, cpp_type: str, methods: dict[str, list[MethodDef]] | None = None):
        """Register a built-in type."""
        self.types[name] = BuiltinTypeDef(
            name=name,
            cpp_type=cpp_type,
            methods=methods or {},
        )


# Module instances (lazy-loaded)
_builtins: BuiltinModule | None = None


def get_builtins() -> BuiltinModule:
    """Get the builtins module, loading it on first access."""
    global _builtins
    if _builtins is None:
        from tpyc.modules import builtins as builtins_module
        _builtins = builtins_module.module
    assert _builtins is not None
    return _builtins


def lookup_function(name: str) -> BuiltinFunctionDef | None:
    """Lookup a function by name in the built-in modules."""
    if fn := get_builtins().functions.get(name):
        return fn
    return None


def lookup_type(name: str) -> BuiltinTypeDef | None:
    """Lookup a type by name in the built-in modules."""
    if typ := get_builtins().types.get(name):
        return typ
    return None


def lookup_type_method(type_name: str, method_name: str) -> list[MethodDef] | None:
    """Lookup a method on a built-in type."""
    if typ := lookup_type(type_name):
        return typ.methods.get(method_name)
    return None
