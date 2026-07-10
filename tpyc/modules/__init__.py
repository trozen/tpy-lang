"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

# Re-exports resolve lazily (PEP 562): the submodules pull in typesys and
# the full registry, and the build cache's warm path imports
# `tpyc.modules.resolver` (which triggers this __init__) before deciding
# whether the compiler is needed at all.
# (Generic type factories and lookups live in `tpyc.type_def_registry`;
# import from there directly.)

_LAZY_EXPORTS = {
    # --- Data classes, constants ---
    "ParamDef": "defs", "MethodDef": "defs",
    "DUNDER_CPP_TEMPLATES": "defs", "get_dunder_cpp_template": "defs",
    "BINOP_TO_METHOD": "defs", "BINOP_TO_RMETHOD": "defs",
    "AUGOP_TO_IMETHOD": "defs", "UNARYOP_TO_METHOD": "defs",
    # --- Module registry ---
    "get_builtin_module_names": "registry", "get_builtin_type_obj": "registry",
    # --- Method resolution, iteration helpers ---
    "_resolve_concrete_type_name": "type_resolution",
    "_resolve_extends_type_arg": "type_resolution",
    "extract_type_params": "type_resolution", "resolve_method": "type_resolution",
    "ITERABLE_PROTOCOL_QNAMES": "type_resolution",
    "is_native_iterable": "type_resolution",
    "get_extends_protocol_type_arg": "type_resolution",
    "get_error_return_next_element_type": "type_resolution",
    "get_iter_element_type": "type_resolution",
    "get_iterable_element_type": "type_resolution",
    "get_span_element_type": "type_resolution",
    "get_span_return_type": "type_resolution",
}


def __getattr__(name: str):
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(f".{submodule}", __name__), name)
    globals()[name] = value
    return value
