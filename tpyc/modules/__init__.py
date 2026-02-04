"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tpyc.typesys import TpyType

from tpyc.typesys import TypeParamRef, ProtocolType, PtrType, ConstPtrType, ProtocolInfo, MethodSignature, TypeParamKind


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: "TpyType"  # TpyType, use TypeParamRef("T") for type params


@dataclass
class MethodDef:
    """Definition of a function overload or type method."""
    params: list[ParamDef]
    returns: "TpyType"  # TpyType, use TypeParamRef("T") for type params
    cpp: str  # Template: "{0}" for args, "{self}" for receiver


@dataclass
class BuiltinFunctionDef:
    """Definition of a built-in function with its overloads."""
    name: str
    overloads: list[MethodDef]
    special_handling: bool = False  # True if sema/codegen handle this specially (skip overload matching)


@dataclass
class BuiltinTypeDef:
    """Definition of a built-in type with its methods."""
    type_obj: "TpyType | None"  # The type object (e.g., INT32), None for parameterized types
    cpp_type: str
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)
    constructors: list[MethodDef] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)  # ["T"], ["T", "N"], etc.
    param_kinds: list[TypeParamKind] = field(default_factory=list)  # Kind of each type param
    type_factory: "Callable[..., TpyType] | None" = None  # Factory to create TpyType from params
    extends: list[str] = field(default_factory=list)  # Protocols: ["NativeIterable[T]"]


@dataclass
class ProtocolDef:
    """Definition of a protocol (structural type) with required methods.

    For generic protocols like Sequence[T]:
    - type_params stores the type parameter names (e.g., ["T"])
    - methods use TypeParamRef("T") for type parameter references
    """
    name: str
    methods: dict[str, MethodDef]  # method_name -> signature
    cpp_concept: str  # C++ concept name (e.g., "tpy::Sized")
    type_params: list[str] = field(default_factory=list)


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

    def function(self, name: str, overloads: list[MethodDef], special_handling: bool = False):
        """Register a built-in function.

        Args:
            name: Function name
            overloads: List of overload signatures
            special_handling: If True, sema/codegen handle this specially (skip overload matching)
        """
        self.functions[name] = BuiltinFunctionDef(name=name, overloads=overloads, special_handling=special_handling)

    def protocol(self, name: str, methods: dict[str, MethodDef], cpp_concept: str,
                 type_params: list[str] | None = None):
        """Register a protocol definition."""
        self.protocols[name] = ProtocolDef(
            name=name,
            methods=methods,
            cpp_concept=cpp_concept,
            type_params=type_params or []
        )

    def variable(self, name: str, var_type: "TpyType", cpp_expr: str):
        """Register a module-level variable."""
        self.variables[name] = ModuleVarDef(name=name, type=var_type, cpp_expr=cpp_expr)

    def register_type(self, type_obj: "TpyType", cpp_type: str,
                      methods: dict[str, list[MethodDef]] | None = None,
                      constructors: list[MethodDef] | None = None,
                      extends: list[str] | None = None):
        """Register a built-in type using its type object. Preferred for non-parameterized types."""
        qname = type_obj.qualified_name()
        assert qname is not None, f"Type {type_obj} has no qualified_name"
        self.types[qname] = BuiltinTypeDef(
            type_obj=type_obj,
            cpp_type=cpp_type,
            methods=methods or {},
            constructors=constructors or [],
            extends=extends or [],
        )

    def type(self, name: str, cpp_type: str,
             methods: dict[str, list[MethodDef]] | None = None,
             constructors: list[MethodDef] | None = None,
             type_params: list[str] | None = None,
             param_kinds: list[TypeParamKind] | None = None,
             type_factory: "Callable[..., TpyType] | None" = None,
             extends: list[str] | None = None):
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
            constructors=constructors or [],
            type_params=type_params,
            param_kinds=param_kinds,
            type_factory=type_factory,
            extends=extends or [],
        )


# Import module definitions
from tpyc.modules import builtins as _builtins_mod
from tpyc.modules import tpy as _tpy_mod
from tpyc.modules import math as _math_mod
from tpyc.modules import time as _time_mod
from tpyc.modules import sys as _sys_mod
from tpyc.modules import typing as _typing_mod

# Map module name -> factory function
_MODULE_FACTORIES: dict[str, Callable[[], BuiltinModule]] = {
    _builtins_mod.NAME: _builtins_mod.init_module,
    _tpy_mod.NAME: _tpy_mod.init_module,
    _math_mod.NAME: _math_mod.init_module,
    _time_mod.NAME: _time_mod.init_module,
    _sys_mod.NAME: _sys_mod.init_module,
    _typing_mod.NAME: _typing_mod.init_module,
}

# Cache for loaded modules
_module_cache: dict[str, BuiltinModule] = {}


def get_module(name: str) -> BuiltinModule | None:
    """Get a module by name, lazy-loading on first access."""
    if name in _module_cache:
        return _module_cache[name]

    factory = _MODULE_FACTORIES.get(name)
    if factory is None:
        return None

    _module_cache[name] = factory()
    return _module_cache[name]


def get_builtins() -> BuiltinModule:
    """Get the builtins module."""
    result = get_module("builtins")
    assert result is not None
    return result


def get_tpy() -> BuiltinModule:
    """Get the tpy module."""
    result = get_module("tpy")
    assert result is not None
    return result


def get_typing() -> BuiltinModule:
    """Get the typing module."""
    result = get_module("typing")
    assert result is not None
    return result


def _all_modules() -> list[BuiltinModule]:
    """Get all loaded modules for default lookups (builtins + tpy only).

    Note: time module is excluded - it requires explicit import.
    """
    return [get_builtins(), get_tpy()]


def get_all_modules() -> list[BuiltinModule]:
    """Get all available modules."""
    return [get_module(name) for name in _MODULE_FACTORIES if (m := get_module(name)) is not None]


def get_importable_modules() -> list[BuiltinModule]:
    """Get modules that require explicit import (excludes builtins)."""
    return [m for m in get_all_modules() if m.name != "builtins"]


def lookup_protocol(name: str) -> ProtocolDef | None:
    """Lookup a protocol by name across all modules.

    Used by the parser to check protocol type annotations before semantic analysis.
    """
    # Check default modules first
    for module in _all_modules():
        if proto := module.protocols.get(name):
            return proto
    # Also check typing module for protocols like Sized
    typing_mod = get_typing()
    if proto := typing_mod.protocols.get(name):
        return proto
    return None


def get_all_protocols() -> list[ProtocolDef]:
    """Return all registered builtin protocols across all modules."""
    result = []
    for module in _all_modules():
        result.extend(module.protocols.values())
    # Also include typing module protocols
    typing_mod = get_typing()
    result.extend(typing_mod.protocols.values())
    return result


def protocol_def_to_info(pdef: ProtocolDef) -> ProtocolInfo:
    """Convert builtin ProtocolDef to unified ProtocolInfo."""
    methods = []
    for name, method_def in pdef.methods.items():
        params = [(p.name, p.type) for p in method_def.params]
        methods.append(MethodSignature(name=name, params=params, return_type=method_def.returns))

    return ProtocolInfo(
        name=pdef.name,
        methods=methods,
        fields=[],
        type_params=pdef.type_params,
        cpp_concept=pdef.cpp_concept,
        is_marker=len(pdef.methods) == 0
    )


def builtin_type_to_record_info(qname: str, type_def: BuiltinTypeDef) -> "RecordInfo":
    """Convert BuiltinTypeDef to RecordInfo for unified lookup.

    This creates a RecordInfo that represents the builtin type's interface
    (methods with cpp_template) while preserving type parameter information.
    """
    from tpyc.typesys import RecordInfo, FunctionInfo

    methods = {}
    for method_name, overloads in type_def.methods.items():
        # Store all overloads as list of FunctionInfo
        methods[method_name] = [
            FunctionInfo(
                name=method_name,
                params=[(p.name, p.type) for p in method.params],
                return_type=method.returns,
                is_method=True,
                cpp_template=method.cpp,
            )
            for method in overloads
        ]

    # Convert constructors
    constructors = []
    for ctor in type_def.constructors:
        constructors.append(FunctionInfo(
            name="__init__",
            params=[(p.name, p.type) for p in ctor.params],
            return_type=ctor.returns,
            cpp_template=ctor.cpp,
        ))

    # Extract simple name from qualified name
    simple_name = qname.split(".")[-1]

    return RecordInfo(
        name=simple_name,
        fields=[],
        methods=methods,
        constructors=constructors,
        type_params=type_def.type_params,
        extends_protocols=type_def.extends,
        cpp_type=type_def.cpp_type,
    )


def builtin_function_to_info(fn_def: BuiltinFunctionDef) -> list["FunctionInfo"]:
    """Convert BuiltinFunctionDef to list of FunctionInfo (one per overload).

    Used to register builtin functions in the unified TypeRegistry.
    """
    from tpyc.typesys import FunctionInfo

    result = []
    for overload in fn_def.overloads:
        result.append(FunctionInfo(
            name=fn_def.name,
            params=[(p.name, p.type) for p in overload.params],
            return_type=overload.returns,
            cpp_template=overload.cpp,
            is_builtin_function=True,
            special_handling=fn_def.special_handling,
        ))
    return result


def builtin_module_to_info(module: BuiltinModule) -> "ModuleInfo":
    """Convert BuiltinModule to ModuleInfo for unified registry storage.

    Used to register builtin modules (math, time, sys) in the unified TypeRegistry.
    """
    from tpyc.typesys import ModuleInfo, ModuleVarInfo

    # Convert functions to FunctionInfo overloads
    functions = {}
    for name, fn_def in module.functions.items():
        functions[name] = builtin_function_to_info(fn_def)

    # Convert module variables
    variables = {}
    for var_name, var_def in module.variables.items():
        variables[var_name] = ModuleVarInfo(
            name=var_def.name,
            type=var_def.type,
            cpp_expr=var_def.cpp_expr,
        )

    return ModuleInfo(
        name=module.name,
        functions=functions,
        variables=variables,
    )


def _resolve_concrete_type_name(name: str) -> "TpyType | None":
    """Resolve a concrete type name (like 'Char', 'Int32') to its TpyType singleton.

    Used for resolving extends declarations like extends=["NativeIterable[Char]"].
    """
    from tpyc.typesys import CHAR, INT32, BOOL, STR, VOID, BIGINT, FLOAT

    # Map of simple type names to their singleton instances
    type_map = {
        "Char": CHAR,
        "Int32": INT32,
        "Bool": BOOL,
        "str": STR,
        "int": BIGINT,
        "float": FLOAT,
        "None": VOID,
    }
    return type_map.get(name)


def type_extends_protocol(tpy_type: "TpyType", protocol_name: str, protocol_type_args: list["TpyType"]) -> bool:
    """Check if a type explicitly extends a protocol via 'extends' declaration.

    For generic types like list[Int32] with extends=["NativeIterable[T]"],
    substitutes T with Int32 before comparing with the expected protocol args.

    For non-generic types like str with extends=["NativeIterable[Char]"],
    resolves the concrete type name and compares.
    """
    type_def = lookup_type(tpy_type)
    if type_def is None:
        return False

    # Get type parameters from the actual type (e.g., {"T": Int32} for list[Int32])
    type_params = extract_type_params(tpy_type)

    for ext in type_def.extends:
        # Parse "Protocol[X]" pattern where X is a type param or concrete type
        match = re.match(r"(\w+)\[(\w+)\]", ext)
        if match:
            ext_protocol = match.group(1)
            ext_type_name = match.group(2)
            if ext_protocol == protocol_name and len(protocol_type_args) == 1:
                # Resolve the type: either a type param (T) or concrete type (Char, Int32, etc.)
                if ext_type_name in type_params:
                    actual_type_arg = type_params[ext_type_name]
                else:
                    # Try to resolve as a concrete type name
                    actual_type_arg = _resolve_concrete_type_name(ext_type_name)
                    if actual_type_arg is None:
                        continue

                # Allow coercible types (e.g., IntLiteralType -> Int32)
                from tpyc.coercions import resolve_coercion, CoercionContext
                if actual_type_arg == protocol_type_args[0]:
                    return True
                if resolve_coercion(actual_type_arg, protocol_type_args[0], CoercionContext.RETURN) is not None:
                    return True
        elif ext == protocol_name and not protocol_type_args:
            # Non-generic protocol match
            return True

    return False


def type_extends_any(tpy_type: "TpyType", protocol_name: str) -> bool:
    """Check if a type extends any variant of a protocol (ignoring type args).

    For example, type_extends_any(list[Int32], "NativeIterable") returns True
    because list extends NativeIterable[T].
    """
    type_def = lookup_type(tpy_type)
    if type_def is None:
        return False

    for ext in type_def.extends:
        # Parse "Protocol[X]" or just "Protocol"
        match = re.match(r"(\w+)(?:\[\w+\])?", ext)
        if match and match.group(1) == protocol_name:
            return True

    return False


def lookup_type(type_or_name: "TpyType | str") -> BuiltinTypeDef | None:
    """Lookup a type by type object or qualified name string."""
    if isinstance(type_or_name, str):
        qname = type_or_name
    else:
        qname = type_or_name.qualified_name()
        if qname is None:
            return None
    for module in _all_modules():
        if typ := module.types.get(qname):
            return typ
    return None


@dataclass
class GenericTypeLookup:
    """Result of looking up a generic type by name."""
    type_def: BuiltinTypeDef
    qualified_name: str


def lookup_generic_type(name: str) -> GenericTypeLookup | None:
    """Lookup a parameterized type by its simple name (e.g., 'list', 'Array').

    Only returns types that have type parameters and a type factory defined.
    Returns first match across modules - names must be unique to avoid ambiguity.
    """
    for module in _all_modules():
        qualified = f"{module.name}.{name}"
        if typ := module.types.get(qualified):
            if typ.type_params and typ.type_factory:
                return GenericTypeLookup(typ, qualified)
    return None


def extract_type_params(tpy_type: "TpyType") -> dict[str, "TpyType"]:
    """Extract type parameters from a concrete type instance.

    For list[Int32], returns {"T": Int32}.
    For Container[Point, 10], returns {"T": Point}.

    Note: Only type parameters that are themselves types are extracted.
    Integer parameters like N in Container[T, N] are not included.
    """
    if (elem_type := tpy_type.get_element_type()) is not None:
        return {"T": elem_type}
    return {}


def _resolve_type_or_param(t: "TpyType", type_params: dict[str, "TpyType"]) -> "TpyType":
    """Resolve TypeParamRef instances to concrete types.

    Handles:
    - TypeParamRef("T") -> type_params["T"]
    - SelfType -> type_params["Self"] if available, else SELF
    - ProtocolType with TypeParamRef args -> resolved ProtocolType
    - PtrType/ConstPtrType with TypeParamRef pointee -> resolved pointer type
    - Other TpyType -> returned as-is (uses map_inner_types for nested resolution)
    """
    from tpyc.typesys import SelfType, SELF

    # Handle SelfType
    if isinstance(t, SelfType):
        if "Self" in type_params:
            return type_params["Self"]
        return SELF

    # Handle TypeParamRef directly
    if isinstance(t, TypeParamRef):
        if t.name not in type_params:
            raise ValueError(f"Unresolved type parameter: {t.name}")
        return type_params[t.name]

    # Handle ProtocolType with TypeParamRef in type_args
    if isinstance(t, ProtocolType) and t.type_args:
        resolved_args = tuple(
            _resolve_type_or_param(arg, type_params) for arg in t.type_args
        )
        return ProtocolType(t.name, resolved_args)

    # Handle PtrType with TypeParamRef pointee
    if isinstance(t, PtrType):
        resolved_pointee = _resolve_type_or_param(t.pointee, type_params)
        return PtrType(resolved_pointee)

    # Handle ConstPtrType with TypeParamRef pointee
    if isinstance(t, ConstPtrType):
        resolved_pointee = _resolve_type_or_param(t.pointee, type_params)
        return ConstPtrType(resolved_pointee)

    # For other types, use map_inner_types for recursive substitution
    return t.map_inner_types(lambda inner: _resolve_type_or_param(inner, type_params))


def resolve_method(method: MethodDef, type_params: dict[str, "TpyType"]) -> MethodDef:
    """Resolve TypeParamRef instances in a method signature to concrete types.

    Given a method with TypeParamRef placeholders and a dict mapping
    param names to concrete types, returns a new MethodDef with all types resolved.

    Example:
        method = MethodDef(params=[ParamDef("value", TypeParamRef("T"))],
                           returns=TypeParamRef("T"), cpp=...)
        resolved = resolve_method(method, {"T": Int32})
        # resolved.params[0].type == Int32, resolved.returns == Int32
    """
    resolved_params = [
        ParamDef(name=p.name, type=_resolve_type_or_param(p.type, type_params))
        for p in method.params
    ]
    resolved_returns = _resolve_type_or_param(method.returns, type_params)
    return MethodDef(params=resolved_params, returns=resolved_returns, cpp=method.cpp)


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

UNARYOP_TO_METHOD = {
    "-": "__neg__",
    "~": "__invert__",
}


def _type_matches_param(arg_type: "TpyType", param_type: "TpyType") -> bool:
    """Check if an argument type matches a parameter type."""
    # Import here to avoid circular imports
    from tpyc.typesys import IntLiteralType, Int32Type, BigIntType

    if arg_type == param_type:
        return True
    # IntLiteralType can match Int32 or BigInt
    if isinstance(arg_type, IntLiteralType):
        return isinstance(param_type, (Int32Type, BigIntType))
    # INT TypeParamRef can match Int32 or BigInt (compile-time constant)
    if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
        return isinstance(param_type, (Int32Type, BigIntType))
    return False
