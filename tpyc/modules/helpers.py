"""
Helper utilities for defining builtin module types.

Separated from __init__.py to avoid circular imports during module initialization.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tpyc.typesys import TpyType

from tpyc.modules import MethodDef, ParamDef


def make_binop_methods(
    ops: dict[str, tuple[str, "TpyType"]],
    self_type: "TpyType",
    param_name: str = "other",
    include_reverse: bool = True,
) -> dict[str, list[MethodDef]]:
    """Generate binary operator method definitions for a numeric type.

    Args:
        ops: Mapping from dunder name to (cpp_template, return_type).
             The cpp_template uses {self} and {0} placeholders.
             Only forward operators (e.g., "__add__", not "__radd__").
        self_type: The type of the parameter (e.g., BIGINT, INT32).
        param_name: Name for the operator parameter.
        include_reverse: If True, also generates __radd__ etc. by swapping
                         {self} and {0} in the template.

    Returns:
        Dict of method_name -> [MethodDef] suitable for type registration.
    """
    methods: dict[str, list[MethodDef]] = {}
    for dunder, (cpp, ret_type) in ops.items():
        methods[dunder] = [MethodDef(
            params=[ParamDef(param_name, self_type)],
            returns=ret_type,
            cpp=cpp,
        )]
        if include_reverse and not dunder.startswith("__r"):
            # Generate reverse by swapping {self} and {0}
            reverse_cpp = cpp.replace("{self}", "{__tmp__}").replace("{0}", "{self}").replace("{__tmp__}", "{0}")
            rname = dunder.replace("__", "__r", 1)
            methods[rname] = [MethodDef(
                params=[ParamDef(param_name, self_type)],
                returns=ret_type,
                cpp=reverse_cpp,
            )]
    return methods
