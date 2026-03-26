"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

# --- Data classes, BuiltinModule, constants ---
from tpyc.modules.defs import (  # noqa: F401
    ParamDef, MethodDef, BuiltinFunctionDef, BuiltinTypeDef, ProtocolDef,
    ModuleVarDef, BuiltinModule, GenericTypeLookup,
    DUNDER_CPP_TEMPLATES, get_dunder_cpp_template,
    BINOP_TO_METHOD, BINOP_TO_RMETHOD, AUGOP_TO_IMETHOD, UNARYOP_TO_METHOD,
    TypeParamKind,
)

# --- Module registry, protocol lookup, converters ---
from tpyc.modules.registry import (  # noqa: F401
    get_module, get_builtin_module_names, get_builtin_type_obj,
    get_builtins, get_tpy, get_all_modules, get_importable_modules,
    lookup_protocol, get_all_protocols, protocol_def_to_info,
    get_all_protocols_for_module,
    builtin_type_to_record_info, builtin_function_to_info, builtin_module_to_info,
)

# --- Type factories, generic lookup, iteration helpers ---
from tpyc.modules.type_resolution import (  # noqa: F401
    _resolve_concrete_type_name, _resolve_extends_type_arg,
    get_type_factory, get_type_factory_names,
    lookup_generic_type, lookup_generic_type_in_module,
    extract_type_params, resolve_method,
    is_native_iterable, get_extends_protocol_type_arg,
    get_error_return_next_element_type, IterInfo,
    get_iter_info, get_iter_element_type,
    get_span_element_type, get_span_return_type,
)
