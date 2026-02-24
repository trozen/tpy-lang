"""
TurboPython external linkage declarations (tpy.extern module).

Native global variable declarations for C and C++ interop.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import STR, VOID

NAME = "tpy.extern"


def init_module() -> BuiltinModule:
    module = BuiltinModule(NAME)

    # native_c_global() / native_global() / native_c_global_array()
    module.function("native_c_global", overloads=[
        MethodDef(params=[ParamDef("name", STR)], returns=VOID, cpp=""),
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    module.function("native_global", overloads=[
        MethodDef(params=[ParamDef("name", STR)], returns=VOID, cpp=""),
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    module.function("native_c_global_array", overloads=[
        MethodDef(params=[ParamDef("name", STR)], returns=VOID, cpp=""),
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    # Linkage decorators (validated by parser, not called at runtime)
    module.function("native", overloads=[
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    module.function("native_c", overloads=[
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    module.function("extern_c", overloads=[
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    return module
