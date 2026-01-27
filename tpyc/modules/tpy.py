"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, StaticList, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import INT32

module = BuiltinModule("tpy")

module.type("Array", cpp_type="std::array<{T}, {N}>", methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="static_cast<int32_t>({self}.size())",
    )],
})

module.type("Span", cpp_type="std::span<const {T}>", methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="static_cast<int32_t>({self}.size())",
    )],
})

module.type("StaticList", cpp_type="StaticList<{T}, {N}>", methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="{self}.size()",
    )],
})
