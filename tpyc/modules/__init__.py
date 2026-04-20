"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

# --- Data classes, constants ---
from tpyc.modules.defs import (  # noqa: F401
    ParamDef, MethodDef,
    DUNDER_CPP_TEMPLATES, get_dunder_cpp_template,
    BINOP_TO_METHOD, BINOP_TO_RMETHOD, AUGOP_TO_IMETHOD, UNARYOP_TO_METHOD,
)

# --- Module registry ---
from tpyc.modules.registry import (  # noqa: F401
    get_builtin_module_names, get_builtin_type_obj,
)

# --- Method resolution, iteration helpers ---
# (Generic type factories and lookups live in tpyc.type_def_registry since
# Phase F.3e; import from there directly.)
from tpyc.modules.type_resolution import (  # noqa: F401
    _resolve_concrete_type_name, _resolve_extends_type_arg,
    extract_type_params, resolve_method,
    ITERABLE_PROTOCOL_QNAMES,
    is_native_iterable, get_extends_protocol_type_arg,
    get_error_return_next_element_type,
    get_iter_element_type, get_iterable_element_type,
    get_span_element_type, get_span_return_type,
)
