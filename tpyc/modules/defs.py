"""
Built-in module data classes, BuiltinModule container, and constant tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tpyc.typesys import TpyType

from tpyc.typesys import TypeParamRef, NamedType, PtrType, TypeParamKind


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: "TpyType"  # TpyType, use TypeParamRef("T") for type params
    requires_mutable_lvalue: bool = False


@dataclass
class MethodDef:
    """Definition of a function overload or type method."""
    params: list[ParamDef]
    returns: "TpyType"  # TpyType, use TypeParamRef("T") for type params
    cpp: str  # Template: "{0}" for args, "{self}" for receiver
    is_noalloc: bool = False
    is_readonly: bool = False
    is_pure: bool = False
    is_static: bool = False
    # Per-method type params and bounds (for constraining class-level type params)
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, "NamedType"] = field(default_factory=dict)


@dataclass
class BuiltinFunctionDef:
    """Definition of a built-in function with its overloads."""
    name: str
    overloads: list[MethodDef]
    special_handling: bool = False  # True if sema/codegen handle this specially (skip overload matching)
    type_params: list[str] = field(default_factory=list)  # Generic type params (e.g., ["T"])
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "tpy.extern.DefaultInt"}
    type_param_bounds: dict[str, NamedType] = field(default_factory=dict)  # e.g. {"T": Default}


@dataclass
class BuiltinTypeDef:
    """Definition of a built-in type with its methods."""
    type_obj: "TpyType | None"  # The type object (e.g., INT32), None for parameterized types
    cpp_type: str
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)
    type_params: list[str] = field(default_factory=list)  # ["T"], ["T", "N"], etc.
    param_kinds: list[TypeParamKind] = field(default_factory=list)  # Kind of each type param
    type_factory: "Callable[..., TpyType] | None" = None  # Factory to create TpyType from params
    extends: list[str] = field(default_factory=list)  # Protocols: ["NativeIterable[T]"]
    is_nocopy: bool = False  # True for move-only types (copy deleted)


@dataclass
class ProtocolDef:
    """Definition of a protocol (structural type) with required methods.

    For generic protocols like Sequence[T]:
    - type_params stores the type parameter names (e.g., ["T"])
    - methods use TypeParamRef("T") for type parameter references
    """
    name: str
    methods: dict[str, MethodDef]  # method_name -> signature
    cpp_concept: str  # C++ concept name (e.g., "::tpy::Sized")
    type_params: list[str] = field(default_factory=list)
    is_readonly: bool = False  # All methods are read-only (safe for readonly[T] args)


@dataclass
class ModuleVarDef:
    """Definition of a module-level variable."""
    name: str
    type: "TpyType"
    cpp_expr: str  # C++ expression to access the variable


class BuiltinModule:
    """A module containing built-in functions and types."""

    def __init__(self, name: str):
        self.name = name
        self.functions: dict[str, BuiltinFunctionDef] = {}
        self.types: dict[str, BuiltinTypeDef] = {}  # keyed by qualified name
        self.protocols: dict[str, ProtocolDef] = {}  # protocol_name -> ProtocolDef
        self.variables: dict[str, ModuleVarDef] = {}  # var_name -> ModuleVarDef

    def function(self, name: str, overloads: list[MethodDef], special_handling: bool = False,
                 type_params: list[str] | None = None,
                 type_param_defaults: dict[str, str] | None = None,
                 type_param_bounds: dict[str, NamedType] | None = None):
        """Register a built-in function.

        Args:
            name: Function name
            overloads: List of overload signatures
            special_handling: If True, sema/codegen handle this specially (skip overload matching)
            type_params: Generic type parameter names (e.g., ["T"])
            type_param_defaults: Default values for type params (e.g., {"T": "DefaultInt"})
            type_param_bounds: Bounds for type params (e.g., {"T": Default})
        """
        self.functions[name] = BuiltinFunctionDef(
            name=name, overloads=overloads, special_handling=special_handling,
            type_params=type_params or [],
            type_param_defaults=type_param_defaults or {},
            type_param_bounds=type_param_bounds or {},
        )

    def protocol(self, name: str, methods: dict[str, MethodDef], cpp_concept: str = "",
                 type_params: list[str] | None = None, is_readonly: bool = False):
        """Register a protocol definition."""
        self.protocols[name] = ProtocolDef(
            name=name,
            methods=methods,
            cpp_concept=cpp_concept,
            type_params=type_params or [],
            is_readonly=is_readonly,
        )

    def variable(self, name: str, var_type: "TpyType", cpp_expr: str):
        """Register a module-level variable."""
        self.variables[name] = ModuleVarDef(name=name, type=var_type, cpp_expr=cpp_expr)

    def register_type(self, type_obj: "TpyType", cpp_type: str,
                      methods: dict[str, list[MethodDef]] | None = None,
                      extends: list[str] | None = None,
                      is_nocopy: bool = False):
        """Register a built-in type using its type object. Preferred for non-parameterized types."""
        qname = type_obj.qualified_name()
        assert qname is not None, f"Type {type_obj} has no qualified_name"
        self.types[qname] = BuiltinTypeDef(
            type_obj=type_obj,
            cpp_type=cpp_type,
            methods=methods or {},
            extends=extends or [],
            is_nocopy=is_nocopy,
        )

    def type(self, name: str, cpp_type: str,
             methods: dict[str, list[MethodDef]] | None = None,
             type_params: list[str] | None = None,
             param_kinds: list[TypeParamKind] | None = None,
             type_factory: "Callable[..., TpyType] | None" = None,
             extends: list[str] | None = None,
             is_nocopy: bool = False,
             ):
        """Register a built-in type by name. Use for parameterized types (list, Array, etc.)."""
        type_params = type_params or []
        param_kinds = param_kinds or []

        # Validate parameterized type configuration
        if type_params:
            if not param_kinds:
                raise ValueError(f"Type '{name}' has type_params but no param_kinds")
            if not type_factory:
                raise ValueError(f"Type '{name}' has type_params but no type_factory")
            if len(param_kinds) != len(type_params):
                raise ValueError(
                    f"Type '{name}': param_kinds length ({len(param_kinds)}) "
                    f"!= type_params length ({len(type_params)})"
                )
        else:
            if param_kinds:
                raise ValueError(f"Type '{name}' has param_kinds but no type_params")
            if type_factory:
                raise ValueError(f"Type '{name}' has type_factory but no type_params")

        qualified_name = f"{self.name}.{name}"
        self.types[qualified_name] = BuiltinTypeDef(
            type_obj=None,  # Parameterized type, no single instance
            cpp_type=cpp_type,
            methods=methods or {},
            type_params=type_params,
            param_kinds=param_kinds,
            type_factory=type_factory,
            extends=extends or [],
            is_nocopy=is_nocopy,
        )


@dataclass
class GenericTypeLookup:
    """Result of looking up a generic type by name."""
    type_def: BuiltinTypeDef | None
    qualified_name: str


# C++ expression templates for Python dunder methods.
# Used when .py-defined protocols don't carry cpp templates.
DUNDER_CPP_TEMPLATES: dict[str, str] = {
    "__len__": "::tpy::__len__({self})",
    "__getitem__": "::tpy::__getitem__({self}, {0})",
    "__setitem__": "::tpy::__setitem__({self}, {0}, {1})",
    "__iter__": "::tpy::__iter__({self})",
    "__next__": "{self}.__next__()",
    "__contains__": "::tpy::__contains__({self}, {0})",
    "__delitem__": "::tpy::__delitem__({self}, {0})",
    "__bool__": "::tpy::__bool__({self})",
    "__str__": "::tpy::__str__({self})",
    "__repr__": "::tpy::__repr__({self})",
    "__hash__": "::tpy::__hash__({self})",
    "__deref__": "::tpy::deref_check({self})",
    "__span__": "::tpy::as_span({self})",
}


def get_dunder_cpp_template(method_name: str) -> str | None:
    """Get the C++ expression template for a dunder method, or None."""
    return DUNDER_CPP_TEMPLATES.get(method_name)


# Operator to method name mappings
BINOP_TO_METHOD = {
    "+": "__add__", "-": "__sub__", "*": "__mul__",
    "div": "__truediv__", "//": "__floordiv__", "%": "__mod__", "**": "__pow__",
    "<<": "__lshift__", ">>": "__rshift__",
    "&": "__and__", "|": "__or__", "^": "__xor__",
}

BINOP_TO_RMETHOD = {
    "+": "__radd__", "-": "__rsub__", "*": "__rmul__",
    "div": "__rtruediv__", "//": "__rfloordiv__", "%": "__rmod__", "**": "__rpow__",
    "<<": "__rlshift__", ">>": "__rrshift__",
    "&": "__rand__", "|": "__ror__", "^": "__rxor__",
}

AUGOP_TO_IMETHOD = {
    "+": "__iadd__", "-": "__isub__", "*": "__imul__",
    "div": "__itruediv__", "//": "__ifloordiv__", "%": "__imod__",
    "<<": "__ilshift__", ">>": "__irshift__",
    "|": "__ior__", "&": "__iand__", "^": "__ixor__",
}

UNARYOP_TO_METHOD = {
    "+": "__pos__",
    "-": "__neg__",
    "~": "__invert__",
}
