"""Module registry: factory registration, caching, protocol/converter helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tpyc.typesys import TpyType, ProtocolInfo, RecordInfo, FunctionInfo, ModuleInfo

from tpyc.typesys import ParamInfo, MethodSignature
from tpyc.modules.defs import (
    ParamDef, MethodDef, BuiltinFunctionDef, BuiltinTypeDef, ProtocolDef,
    ModuleVarDef, BuiltinModule,
)

# ---------------------------------------------------------------------------
# Module factory registry
# ---------------------------------------------------------------------------

from tpyc.modules import builtins as _builtins_mod
from tpyc.modules import tpy as _tpy_mod

# Map module name -> factory function
_MODULE_FACTORIES: dict[str, Callable[[], BuiltinModule]] = {
    _builtins_mod.NAME: _builtins_mod.init_module,
    _tpy_mod.NAME: _tpy_mod.init_module,
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


def get_builtin_type_obj(qname: str) -> "TpyType | None":
    """Get the type object for a builtin type by qualified name (e.g. 'tpy.Float32').

    Checks hardcoded module types first, then type factories for types
    fully defined in .py.
    """
    from tpyc.modules.type_resolution import _get_type_factories

    module_name = qname.rsplit(".", 1)[0] if "." in qname else qname
    module = get_module(module_name)
    if module and qname in module.types:
        return module.types[qname].type_obj
    # Factory path: non-generic types fully defined in .py
    entry = _get_type_factories().get(qname)
    if entry and not entry[0]:  # no type params -> non-generic singleton
        return entry[1]()
    return None


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


def _all_modules() -> list[BuiltinModule]:
    """Get all loaded modules for default lookups (builtins + tpy only).

    Note: time module is excluded - it requires explicit import.
    """
    return [get_builtins(), get_tpy()]


def get_all_modules() -> list[BuiltinModule]:
    """Get all available modules."""
    return [m for name in _MODULE_FACTORIES if (m := get_module(name)) is not None]


def get_importable_modules() -> list[BuiltinModule]:
    """Get all modules (used by register_builtin_modules)."""
    return get_all_modules()


# ---------------------------------------------------------------------------
# Protocol lookup
# ---------------------------------------------------------------------------

def lookup_protocol(name: str) -> ProtocolDef | None:
    """Lookup a protocol by name across all modules.

    Used by the parser to check protocol type annotations before semantic analysis.
    """
    for module in _all_modules():
        if proto := module.protocols.get(name):
            return proto
    return None


def get_all_protocols() -> list[tuple[ProtocolDef, str]]:
    """Return all registered builtin protocols with their module names."""
    result: list[tuple[ProtocolDef, str]] = []
    for module in _all_modules():
        for pdef in module.protocols.values():
            result.append((pdef, module.name))
    return result


def protocol_def_to_info(pdef: ProtocolDef, module_name: str = "") -> "ProtocolInfo":
    """Convert builtin ProtocolDef to unified ProtocolInfo."""
    from tpyc.typesys import ProtocolInfo

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


# ---------------------------------------------------------------------------
# Converters: BuiltinTypeDef/FunctionDef/Module -> unified info types
# ---------------------------------------------------------------------------

def builtin_type_to_record_info(qname: str, type_def: BuiltinTypeDef) -> "RecordInfo":
    """Convert BuiltinTypeDef to RecordInfo for unified lookup.

    This creates a RecordInfo that represents the builtin type's interface
    (methods with cpp_template) while preserving type parameter information.
    """
    from tpyc.typesys import RecordInfo, FunctionInfo, IMPLICIT_READONLY_METHODS

    methods = {}
    for method_name, overloads in type_def.methods.items():
        resolved_readonly = method_name in IMPLICIT_READONLY_METHODS
        is_ctor = method_name == "__init__"
        # Store all overloads as list of FunctionInfo
        methods[method_name] = [
            FunctionInfo(
                name=method_name,
                params=[ParamInfo(p.name, p.type, requires_mutable_lvalue=p.requires_mutable_lvalue) for p in method.params],
                return_type=method.returns,
                is_method=not is_ctor,
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

    # Extract simple name from qualified name
    simple_name = qname.split(".")[-1]

    return RecordInfo(
        name=simple_name,
        fields=[],
        methods=methods,
        type_params=type_def.type_params,
        type_param_kinds=type_def.param_kinds,
        type_factory=type_def.type_factory,
        extends_protocols=type_def.extends,
        is_nocopy=type_def.is_nocopy,
        builtin_type_key=qname,
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
            params=[ParamInfo(p.name, p.type, requires_mutable_lvalue=p.requires_mutable_lvalue) for p in overload.params],
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
