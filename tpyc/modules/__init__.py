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

from tpyc.typesys import TypeParamRef, NamedType, PtrType, ProtocolInfo, MethodSignature, TypeParamKind, ParamInfo, TupleType, impl_proto_matches_name, get_protocol_qname


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: "TpyType"  # TpyType, use TypeParamRef("T") for type params
    requires_lvalue: bool = False
    requires_mutable: bool = False


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
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "DEFAULT_INT"}
    type_param_bounds: dict[str, NamedType] = field(default_factory=dict)  # e.g. {"T": Default}


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
            type_param_defaults: Default values for type params (e.g., {"T": "DEFAULT_INT"})
            type_param_bounds: Bounds for type params (e.g., {"T": Default})
        """
        self.functions[name] = BuiltinFunctionDef(
            name=name, overloads=overloads, special_handling=special_handling,
            type_params=type_params or [],
            type_param_defaults=type_param_defaults or {},
            type_param_bounds=type_param_bounds or {},
        )

    def protocol(self, name: str, methods: dict[str, MethodDef], cpp_concept: str,
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
                      constructors: list[MethodDef] | None = None,
                      extends: list[str] | None = None,
                      is_nocopy: bool = False):
        """Register a built-in type using its type object. Preferred for non-parameterized types."""
        qname = type_obj.qualified_name()
        assert qname is not None, f"Type {type_obj} has no qualified_name"
        self.types[qname] = BuiltinTypeDef(
            type_obj=type_obj,
            cpp_type=cpp_type,
            methods=methods or {},
            constructors=constructors or [],
            extends=extends or [],
            is_nocopy=is_nocopy,
        )

    def type(self, name: str, cpp_type: str,
             methods: dict[str, list[MethodDef]] | None = None,
             constructors: list[MethodDef] | None = None,
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
            constructors=constructors or [],
            type_params=type_params,
            param_kinds=param_kinds,
            type_factory=type_factory,
            extends=extends or [],
            is_nocopy=is_nocopy,
        )


# Import module definitions
from tpyc.modules import builtins as _builtins_mod
from tpyc.modules import tpy as _tpy_mod
from tpyc.modules import typing as _typing_mod
from tpyc.modules import extern as _extern_mod
from tpyc.modules import enum as _enum_mod
from tpyc.modules import dataclasses as _dataclasses_mod

# Map module name -> factory function
_MODULE_FACTORIES: dict[str, Callable[[], BuiltinModule]] = {
    _builtins_mod.NAME: _builtins_mod.init_module,
    _tpy_mod.NAME: _tpy_mod.init_module,
    _typing_mod.NAME: _typing_mod.init_module,
    _extern_mod.NAME: _extern_mod.init_module,
    _enum_mod.NAME: _enum_mod.init_module,
    _dataclasses_mod.NAME: _dataclasses_mod.init_module,
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


def get_builtin_module_names() -> set[str]:
    """Get the names of all builtin modules.

    Used by the parser to distinguish user module imports from builtin imports.
    """
    return set(_MODULE_FACTORIES.keys())


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


def get_all_protocols() -> list[tuple[ProtocolDef, str]]:
    """Return all registered builtin protocols with their module names."""
    result: list[tuple[ProtocolDef, str]] = []
    for module in _all_modules():
        for pdef in module.protocols.values():
            result.append((pdef, module.name))
    # Also include typing module protocols
    typing_mod = get_typing()
    for pdef in typing_mod.protocols.values():
        result.append((pdef, typing_mod.name))
    return result


def protocol_def_to_info(pdef: ProtocolDef, module_name: str = "") -> ProtocolInfo:
    """Convert builtin ProtocolDef to unified ProtocolInfo."""
    methods = []
    for name, method_def in pdef.methods.items():
        params = [ParamInfo(p.name, p.type) for p in method_def.params]
        method_readonly = method_def.is_readonly or pdef.is_readonly
        methods.append(MethodSignature(
            name=name, params=params, return_type=method_def.returns,
            is_readonly=method_readonly,
            cpp_template=method_def.cpp,
        ))

    return ProtocolInfo(
        name=pdef.name,
        methods=methods,
        fields=[],
        type_params=pdef.type_params,
        cpp_concept=pdef.cpp_concept,
        is_marker=len(pdef.methods) == 0,
        is_readonly=pdef.is_readonly,
        module=module_name,
    )


def get_all_protocols_for_module(module_name: str) -> list["ProtocolInfo"]:
    """Return all builtin protocols defined by a specific module."""
    module = get_module(module_name)
    if module is None:
        return []
    return [protocol_def_to_info(pdef, module_name) for pdef in module.protocols.values()]


def builtin_type_to_record_info(qname: str, type_def: BuiltinTypeDef) -> "RecordInfo":
    """Convert BuiltinTypeDef to RecordInfo for unified lookup.

    This creates a RecordInfo that represents the builtin type's interface
    (methods with cpp_template) while preserving type parameter information.
    """
    from tpyc.typesys import RecordInfo, FunctionInfo, IMPLICIT_READONLY_METHODS

    methods = {}
    for method_name, overloads in type_def.methods.items():
        resolved_readonly = method_name in IMPLICIT_READONLY_METHODS
        # Store all overloads as list of FunctionInfo
        methods[method_name] = [
            FunctionInfo(
                name=method_name,
                params=[ParamInfo(p.name, p.type, p.requires_lvalue, p.requires_mutable) for p in method.params],
                return_type=method.returns,
                is_method=True,
                is_staticmethod=method.is_static,
                is_noalloc=method.is_noalloc,
                is_readonly=method.is_readonly or resolved_readonly,
                is_pure=method.is_pure,
                cpp_template=method.cpp,
                type_params=method.type_params,
                type_param_bounds=method.type_param_bounds,
            )
            for method in overloads
        ]

    # Convert constructors -- builtin type constructors are always readonly
    constructors = []
    for ctor in type_def.constructors:
        constructors.append(FunctionInfo(
            name="__init__",
            params=[ParamInfo(p.name, p.type, p.requires_lvalue, p.requires_mutable) for p in ctor.params],
            return_type=ctor.returns,
            is_noalloc=ctor.is_noalloc,
            is_readonly=ctor.is_readonly,
            is_pure=ctor.is_pure,
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
        type_param_kinds=type_def.param_kinds,
        extends_protocols=type_def.extends,
        cpp_type=type_def.cpp_type,
        is_nocopy=type_def.is_nocopy,
    )


def builtin_function_to_info(fn_def: BuiltinFunctionDef, module_name: str = "") -> list["FunctionInfo"]:
    """Convert BuiltinFunctionDef to list of FunctionInfo (one per overload).

    Used to register builtin functions in the unified TypeRegistry.
    """
    from tpyc.typesys import FunctionInfo

    qname = f"{module_name}.{fn_def.name}" if module_name else ""
    result = []
    for overload in fn_def.overloads:
        result.append(FunctionInfo(
            name=fn_def.name,
            params=[ParamInfo(p.name, p.type, p.requires_lvalue, p.requires_mutable) for p in overload.params],
            return_type=overload.returns,
            is_noalloc=overload.is_noalloc,
            is_readonly=overload.is_readonly,
            is_pure=overload.is_pure,
            cpp_template=overload.cpp,
            is_builtin_function=True,
            special_handling=fn_def.special_handling,
            type_params=fn_def.type_params,
            type_param_defaults=fn_def.type_param_defaults,
            type_param_bounds=fn_def.type_param_bounds,
            qualified_name=qname,
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
        functions[name] = builtin_function_to_info(fn_def, module.name)

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
        is_builtin=True,
        functions=functions,
        variables=variables,
        records={},
        protocols={},
    )


def _resolve_concrete_type_name(name: str) -> "TpyType | None":
    """Resolve a concrete type name (like 'Char', 'Int32') to its TpyType singleton.

    Used for resolving extends declarations like extends=["NativeIterable[Char]"].
    """
    from tpyc.typesys import (
        CHAR, BOOL, STR, VOID, BIGINT, FLOAT, FLOAT32, SLICE,
        INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    )

    # Map of simple type names to their singleton instances
    type_map = {
        "Char": CHAR,
        "Int8": INT8, "Int16": INT16, "Int32": INT32, "Int64": INT64,
        "UInt8": UINT8, "UInt16": UINT16, "UInt32": UINT32, "UInt64": UINT64,
        "Float32": FLOAT32, "Float64": FLOAT,
        "str": STR,
        "int": BIGINT,
        "float": FLOAT,
        "slice": SLICE,
        "None": VOID,
    }
    return type_map.get(name)


def _resolve_extends_type_arg(type_str: str, type_params: dict[str, "TpyType"]) -> "TpyType | None":
    """Resolve an extends type arg string to a TpyType.

    Handles:
    - Simple type param refs: "K", "V", "T"
    - Concrete type names: "Char", "Int32"
    - Tuple types: "tuple[K, V]"
    """
    # Handle tuple[...] pattern
    tuple_match = re.match(r"tuple\[(.+)\]$", type_str)
    if tuple_match:
        inner = tuple_match.group(1)
        parts = [p.strip() for p in inner.split(",")]
        resolved = []
        for part in parts:
            if part in type_params:
                resolved.append(type_params[part])
            else:
                concrete = _resolve_concrete_type_name(part)
                if concrete is None:
                    return None
                resolved.append(concrete)
        return TupleType(tuple(resolved))

    # Simple name: type param or concrete type
    if type_str in type_params:
        return type_params[type_str]
    return _resolve_concrete_type_name(type_str)


def lookup_type(type_or_name: "TpyType | str") -> BuiltinTypeDef | None:
    """Lookup a type by type object or qualified name string."""
    if isinstance(type_or_name, str):
        qname = type_or_name
    else:
        qname = type_or_name.qualified_name()
        if qname is None:
            return None
    for module_name in _MODULE_FACTORIES:
        module = get_module(module_name)
        if module and (typ := module.types.get(qname)):
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
    Returns first match across default modules (builtins + tpy) only.
    Types from other modules (tpy.mem, tpy.unsafe, etc.) require explicit import
    and are resolved via lookup_generic_type_in_module() instead.
    """
    for module in _all_modules():
        qualified = f"{module.name}.{name}"
        if typ := module.types.get(qualified):
            if typ.type_params and typ.type_factory:
                return GenericTypeLookup(typ, qualified)
    return None


def lookup_generic_type_in_module(name: str, module_name: str) -> GenericTypeLookup | None:
    """Lookup a parameterized type by simple name within a specific module.

    Used when resolving imported names (e.g., 'from tpy.mem import UninitArrayStorage').
    """
    module = get_module(module_name)
    if module is None:
        return None
    qualified = f"{module.name}.{name}"
    if typ := module.types.get(qualified):
        if typ.type_params and typ.type_factory:
            return GenericTypeLookup(typ, qualified)
    return None


def extract_type_params(tpy_type: "TpyType") -> dict[str, "TpyType"]:
    """Extract type parameters from a concrete type instance.

    For list[Int32], returns {"T": Int32}.
    For dict[str, Int32], returns {"K": str, "V": Int32}.
    For Container[Point, 10], returns {"T": Point}.
    For Ptr[Point], returns {"T": Point}.

    Note: Only type parameters that are themselves types are extracted.
    Integer parameters like N in Container[T, N] are not included.
    """
    from tpyc.typesys import PtrType, DictType, DictKeysViewType, DictValuesViewType, DictItemsViewType
    if isinstance(tpy_type, (DictType, DictKeysViewType, DictValuesViewType, DictItemsViewType)):
        return {"K": tpy_type.key_type, "V": tpy_type.value_type}
    # Pointer types store their type param as pointee (not via get_element_type,
    # since pointers are not containers). Use inner_pointee so that Ptr[readonly[T]]
    # returns {"T": T_element} not {"T": readonly[T_element]}.
    # Tspan = full pointee (preserves readonly) for Ptr.span() return type.
    if isinstance(tpy_type, PtrType):
        return {"T": tpy_type.inner_pointee, "Tspan": tpy_type.pointee}
    if (elem_type := tpy_type.get_element_type()) is not None:
        return {"T": elem_type}
    return {}


def _resolve_type_or_param(t: "TpyType", type_params: dict[str, "TpyType"]) -> "TpyType":
    """Resolve TypeParamRef instances to concrete types.

    Handles:
    - TypeParamRef("T") -> type_params["T"]
    - SelfType -> type_params["Self"] if available, else SELF
    - NamedType (protocol) with TypeParamRef args -> resolved NamedType
    - PtrType with TypeParamRef pointee -> resolved pointer type
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

    # Handle NamedType (protocol) with TypeParamRef in type_args
    if isinstance(t, NamedType) and t.is_protocol and t.type_args:
        resolved_args = tuple(
            _resolve_type_or_param(arg, type_params) for arg in t.type_args
        )
        return NamedType(t.name, resolved_args, is_protocol=True)

    # Handle PtrType with TypeParamRef pointee
    if isinstance(t, PtrType):
        resolved_pointee = _resolve_type_or_param(t.pointee, type_params)
        return PtrType(resolved_pointee, is_readonly=t.is_readonly)

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
        ParamDef(name=p.name, type=_resolve_type_or_param(p.type, type_params),
                 requires_lvalue=p.requires_lvalue, requires_mutable=p.requires_mutable)
        for p in method.params
    ]
    resolved_returns = _resolve_type_or_param(method.returns, type_params)
    return MethodDef(
        params=resolved_params,
        returns=resolved_returns,
        cpp=method.cpp,
        is_noalloc=method.is_noalloc,
        is_readonly=method.is_readonly,
        is_pure=method.is_pure,
        type_params=method.type_params,
        type_param_bounds=method.type_param_bounds,
    )


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


def is_native_iterable(tpy_type: "TpyType", registry: "TypeRegistry") -> bool:
    """Check if type extends NativeIterable (uses range-based for in C++)."""
    record = registry.get_record_for_type(tpy_type)
    if record is None:
        return False
    # Builtin types: check extends_protocols strings
    if record.extends_protocols:
        if any(ext.startswith("NativeIterable") for ext in record.extends_protocols):
            return True
    # User records: check implemented_protocols (includes auto-derived from __span__)
    for proto in record.implemented_protocols:
        if proto.name == "NativeIterable":
            return True
    return False


def get_extends_protocol_type_arg(
    tpy_type: "TpyType", protocol_name: str,
    registry: "TypeRegistry | None" = None,
) -> "TpyType | None":
    """Extract the first type arg from a type's extends declaration for a protocol.

    For example, Ptr[Int32] extends Deref[T] with T=Int32, so
    get_extends_protocol_type_arg(Ptr[Int32], "Deref") returns Int32.

    Checks builtin type extends strings first, then user record
    implemented_protocols if a registry is provided.
    """
    type_def = lookup_type(tpy_type)
    if type_def is not None:
        type_params = extract_type_params(tpy_type)
        for ext in type_def.extends:
            match = re.match(r"(\w+)\[(.+)\]", ext)
            if match and match.group(1) == protocol_name:
                return _resolve_extends_type_arg(match.group(2), type_params)

    if registry is not None:
        record_info = registry.get_record_for_type(tpy_type)
        if record_info is not None:
            target_qname = get_protocol_qname(protocol_name)
            for impl_proto in record_info.implemented_protocols:
                if impl_proto_matches_name(impl_proto, protocol_name, target_qname) and impl_proto.type_args:
                    return impl_proto.type_args[0]

    return None


def get_error_return_next_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __next__() with @error_return(StopIteration), return element type T."""
    from tpyc.typesys import NamedType, OwnType, TypeParamRef

    if not isinstance(tpy_type, NamedType) or not tpy_type.is_user_record:
        return None
    return _find_error_return_next_element(tpy_type.name, tpy_type.type_args, registry)


def _find_error_return_next_element(
    record_name: str, type_args: "list[TpyType] | None", registry: "TypeRegistry",
) -> "TpyType | None":
    """Walk a record's method table (and parent chain) looking for __next__() with error_return."""
    from tpyc.typesys import NamedType, OwnType, TypeParamRef

    record = registry.find_record(record_name)
    if record is None:
        return None

    type_subst: dict[str, "TpyType"] = {}
    if record.type_params and type_args:
        type_subst = dict(zip(record.type_params, type_args))

    for method in record.get_method_overloads("__next__"):
        if method.error_return_type == "StopIteration" and len(method.params) == 0:
            inner = method.return_type
            if isinstance(inner, OwnType):
                inner = inner.wrapped
            if isinstance(inner, TypeParamRef) and inner.name in type_subst:
                return type_subst[inner.name]
            return inner

    # Walk parent chain
    if record.parent and isinstance(record.parent, NamedType) and record.parent.is_user_record:
        parent_args = list(record.parent.type_args) if record.parent.type_args else None
        if type_subst and parent_args:
            parent_args = [
                type_subst[a.name] if isinstance(a, TypeParamRef) and a.name in type_subst else a
                for a in parent_args
            ]
        return _find_error_return_next_element(record.parent.name, parent_args, registry)

    return None


@dataclass
class IterInfo:
    """Result of checking __iter__() on a type."""
    element_type: "TpyType"
    iter_is_native: bool  # True if __iter__ returns a NativeIterable (can use begin/end directly)


def get_iter_info(tpy_type: "TpyType", registry: "TypeRegistry") -> "IterInfo | None":
    """If type has __iter__() returning a concrete iterator, return element type and dispatch info."""
    from tpyc.typesys import NamedType

    # Try user records (walks parent chain)
    if isinstance(tpy_type, NamedType) and tpy_type.is_user_record:
        record = registry.get_record(tpy_type.name)
        if record is not None:
            type_subst: dict[str, "TpyType"] = {}
            if record.type_params and tpy_type.type_args:
                type_subst = dict(zip(record.type_params, tpy_type.type_args))
            result = _find_record_iter_info(record, type_subst, registry)
            if result is not None:
                return result

    # Try builtin types
    type_def = lookup_type(tpy_type)
    if type_def is not None:
        type_subst_b: dict[str, "TpyType"] = {}
        if type_def.type_params and hasattr(tpy_type, 'type_args') and tpy_type.type_args:
            type_subst_b = dict(zip(type_def.type_params, tpy_type.type_args))
        iter_methods = type_def.methods.get("__iter__", [])
        result = _find_iter_method_info(iter_methods, type_subst_b, registry)
        if result is not None:
            return result

    return None


def get_iter_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __iter__() returning a concrete iterator, return element type T."""
    info = get_iter_info(tpy_type, registry)
    return info.element_type if info is not None else None


def _find_record_iter_info(
    record: "RecordInfo", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
) -> "IterInfo | None":
    """Check if record (or its parents) has __iter__() returning a concrete iterator."""
    from tpyc.typesys import NamedType, TypeParamRef

    result = _find_iter_method_info(record.get_method_overloads("__iter__"), type_subst, registry)
    if result is not None:
        return result

    # Walk parent chain (recursive)
    if record.parent and isinstance(record.parent, NamedType) and record.parent.is_user_record:
        parent_info = registry.get_record(record.parent.name)
        if parent_info:
            parent_subst = dict(type_subst)
            if record.parent.type_args and parent_info.type_params:
                for param_name, arg in zip(parent_info.type_params, record.parent.type_args):
                    if isinstance(arg, TypeParamRef) and arg.name in type_subst:
                        parent_subst[param_name] = type_subst[arg.name]
                    else:
                        parent_subst[param_name] = arg
            return _find_record_iter_info(parent_info, parent_subst, registry)

    return None


def _find_iter_method_info(
    methods: "list[MethodDef | FunctionInfo]", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
) -> "IterInfo | None":
    """Check __iter__() methods for a concrete iterator return type and extract element type."""
    from tpyc.typesys import FunctionInfo, NamedType, OwnType, SpanIterType, TypeParamRef

    for method in methods:
        if len(method.params) != 0:
            continue
        if isinstance(method, FunctionInfo):
            ret = method.return_type
        else:
            ret = method.returns
        if isinstance(ret, TypeParamRef) and ret.name in type_subst:
            ret = type_subst[ret.name]
        if isinstance(ret, OwnType):
            ret = ret.wrapped

        # SpanIter[T] -- known NativeIterable with element type T
        if isinstance(ret, SpanIterType):
            elem = ret.element_type
            if isinstance(elem, TypeParamRef) and elem.name in type_subst:
                elem = type_subst[elem.name]
            return IterInfo(elem, iter_is_native=True)

        # User-defined iterator with error_return __next__
        if isinstance(ret, NamedType) and ret.is_user_record:
            iter_type_args = ret.type_args
            if iter_type_args and type_subst:
                iter_type_args = [
                    type_subst.get(a.name, a) if isinstance(a, TypeParamRef) else a
                    for a in iter_type_args
                ]
            elem = _find_error_return_next_element(ret.name, iter_type_args, registry)
            if elem is not None:
                iter_native = is_native_iterable(ret, registry)
                return IterInfo(elem, iter_is_native=iter_native)

    return None


def get_span_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __span__() -> Span[T] or Span[readonly[T]], return element type T."""
    span_type = get_span_return_type(tpy_type, registry)
    if span_type is not None:
        return span_type.element_type
    return None


def get_span_return_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "SpanType | None":
    """If type has __span__() -> Span[T] or Span[readonly[T]], return the full SpanType."""
    from tpyc.typesys import NamedType, SpanType, TypeParamRef

    if not (isinstance(tpy_type, NamedType) and tpy_type.is_user_record):
        return None
    record = registry.get_record(tpy_type.name)
    if record is None:
        return None
    type_subst: dict[str, "TpyType"] = {}
    if record.type_params and tpy_type.type_args:
        type_subst = dict(zip(record.type_params, tpy_type.type_args))
    return _find_span_method_return_type(record, type_subst, registry)


def _find_span_method_return_type(
    record: "RecordInfo", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
) -> "SpanType | None":
    """Check if record has __span__() returning Span[T]/Span[readonly[T]], and return the SpanType."""
    from tpyc.typesys import NamedType, SpanType, TypeParamRef

    for method in record.get_method_overloads("__span__"):
        if len(method.params) != 0:
            continue
        ret = method.return_type
        if isinstance(ret, TypeParamRef) and ret.name in type_subst:
            ret = type_subst[ret.name]
        if isinstance(ret, SpanType):
            # Use inner_element_type (unwrapped) for TypeParamRef resolution,
            # then reconstruct with the original is_readonly.
            elem = ret.inner_element_type
            if isinstance(elem, TypeParamRef) and elem.name in type_subst:
                elem = type_subst[elem.name]
            return SpanType(elem, is_readonly=ret.is_readonly)

    # Walk parent chain
    if record.parent and isinstance(record.parent, NamedType) and record.parent.is_user_record:
        parent_info = registry.get_record(record.parent.name)
        if parent_info:
            parent_subst = dict(type_subst)
            if record.parent.type_args and parent_info.type_params:
                for param_name, arg in zip(parent_info.type_params, record.parent.type_args):
                    if isinstance(arg, TypeParamRef) and arg.name in type_subst:
                        parent_subst[param_name] = type_subst[arg.name]
                    else:
                        parent_subst[param_name] = arg
            return _find_span_method_return_type(parent_info, parent_subst, registry)

    return None


def _type_matches_param(arg_type: "TpyType", param_type: "TpyType") -> bool:
    """Check if an argument type matches a parameter type.

    Delegates to the shared overload resolution module.
    """
    from tpyc.sema.overloads import type_matches_numeric
    return type_matches_numeric(arg_type, param_type)
