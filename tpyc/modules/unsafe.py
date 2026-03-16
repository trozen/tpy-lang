"""
TurboPython unsafe pointer operations -- builtin supplements.

Most tpy.unsafe functions are defined in lib/tpy/tpy/unsafe.py.
This file provides functions that need INT type params on function
signatures (not yet supported in .py source).
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import (
    STR, CHAR, ArrayType, ListType, TypeParamRef, PtrType,
)

T = TypeParamRef("T")

NAME = "tpy.unsafe"


def init_module() -> BuiltinModule:
    module = BuiltinModule(NAME)

    # unsafe_ptr: all overloads here (Array needs INT type param N)
    module.function("unsafe_ptr", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("s", STR)],
            returns=PtrType(CHAR, is_readonly=True),
            cpp="{0}.data()",
        ),
        MethodDef(
            params=[ParamDef("a", ArrayType(T, TypeParamRef("N")))],
            returns=PtrType(T),
            cpp="{0}.data()",
        ),
        MethodDef(
            params=[ParamDef("l", ListType(T))],
            returns=PtrType(T),
            cpp="{0}.data()",
        ),
    ])

    return module
