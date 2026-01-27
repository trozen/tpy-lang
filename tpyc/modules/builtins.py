"""
TurboPython built-in functions (Python builtins).

Defines functions like chr, print, len, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import INT32, BIGINT, CHAR

module = BuiltinModule("builtins")

module.function("chr", overloads=[
    MethodDef(
        params=[ParamDef("i", INT32)],
        returns=CHAR,
        cpp="static_cast<char>({0})",
    ),
    MethodDef(
        params=[ParamDef("i", BIGINT)],
        returns=CHAR,
        cpp="static_cast<char>(({0}).to_int32())",
    ),
])

module.type("list", cpp_type="std::vector<{T}>", methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="static_cast<int32_t>({self}.size())",
    )],
})

module.type("str", cpp_type="std::string_view", methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="static_cast<int32_t>({self}.size())",
    )],
})
