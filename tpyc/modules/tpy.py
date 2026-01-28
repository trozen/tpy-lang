"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, StaticList, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import INT32, BIGINT

module = BuiltinModule("tpy")

# Int32: 32-bit signed integer with checked arithmetic
module.type("Int32", cpp_type="int32_t", methods={
    # Conversion to BigInt (for promotion)
    "__int__": [MethodDef(params=[], returns=BIGINT, cpp="tpy::BigInt({self})")],

    # Binary arithmetic operators (Int32 only - mixed operations promote via __int__)
    "__add__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_add({self}, {0})")],
    "__sub__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_sub({self}, {0})")],
    "__mul__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_mul({self}, {0})")],
    "__floordiv__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_div({self}, {0})")],
    "__mod__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_mod({self}, {0})")],
    "__pow__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_pow({self}, {0})")],

    # Shift operators
    "__lshift__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_lshift({self}, {0})")],
    "__rshift__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_rshift({self}, {0})")],

    # Bitwise operators
    "__and__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="({self}) & ({0})")],
    "__or__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="({self}) | ({0})")],
    "__xor__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="({self}) ^ ({0})")],

    # Unary operators
    "__neg__": [MethodDef(params=[], returns=INT32, cpp="tpy::int32_neg({self})")],
    "__invert__": [MethodDef(params=[], returns=INT32, cpp="~({self})")],

    # Reverse operators (for IntLiteral + Int32)
    "__radd__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_add({0}, {self})")],
    "__rsub__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_sub({0}, {self})")],
    "__rmul__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_mul({0}, {self})")],
    "__rfloordiv__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_div({0}, {self})")],
    "__rmod__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_mod({0}, {self})")],
    "__rpow__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_pow({0}, {self})")],
    "__rlshift__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_lshift({0}, {self})")],
    "__rrshift__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="tpy::int32_rshift({0}, {self})")],
    "__rand__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="({0}) & ({self})")],
    "__ror__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="({0}) | ({self})")],
    "__rxor__": [MethodDef(params=[ParamDef("other", INT32)], returns=INT32, cpp="({0}) ^ ({self})")],
})

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
