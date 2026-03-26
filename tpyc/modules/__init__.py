"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

# --- Data classes, constants ---
from tpyc.modules.defs import (  # noqa: F401
    ParamDef, MethodDef, BuiltinTypeDef, GenericTypeLookup,
    DUNDER_CPP_TEMPLATES, get_dunder_cpp_template,
    BINOP_TO_METHOD, BINOP_TO_RMETHOD, AUGOP_TO_IMETHOD, UNARYOP_TO_METHOD,
    TypeParamKind,
)

# --- Module registry ---
from tpyc.modules.registry import (  # noqa: F401
    get_builtin_module_names, get_builtin_type_obj,
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
