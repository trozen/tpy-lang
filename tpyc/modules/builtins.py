"""
TurboPython built-in functions (Python builtins).

Defines functions like chr, print, len, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import INT32, BIGINT, CHAR, STR

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

module.register_type(STR, cpp_type="std::string_view", methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="static_cast<int32_t>({self}.size())",
    )],
})

# int: arbitrary precision integer (BigInt)
# Uses C++ operator overloads defined in tpy::BigInt
module.register_type(BIGINT, cpp_type="tpy::BigInt", methods={
    # Binary arithmetic operators
    "__add__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) + ({0})")],
    "__sub__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) - ({0})")],
    "__mul__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) * ({0})")],
    "__floordiv__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) / ({0})")],
    "__mod__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) % ({0})")],
    "__pow__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}).pow({0})")],

    # Shift operators
    "__lshift__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) << ({0})")],
    "__rshift__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) >> ({0})")],

    # Bitwise operators
    "__and__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) & ({0})")],
    "__or__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) | ({0})")],
    "__xor__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({self}) ^ ({0})")],

    # Unary operators
    "__neg__": [MethodDef(params=[], returns=BIGINT, cpp="-({self})")],
    "__invert__": [MethodDef(params=[], returns=BIGINT, cpp="~({self})")],

    # Reverse operators (same as forward since BigInt is the "widest" integer type)
    "__radd__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) + ({self})")],
    "__rsub__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) - ({self})")],
    "__rmul__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) * ({self})")],
    "__rfloordiv__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) / ({self})")],
    "__rmod__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) % ({self})")],
    "__rpow__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}).pow({self})")],
    "__rlshift__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) << ({self})")],
    "__rrshift__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) >> ({self})")],
    "__rand__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) & ({self})")],
    "__ror__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) | ({self})")],
    "__rxor__": [MethodDef(params=[ParamDef("other", BIGINT)], returns=BIGINT, cpp="({0}) ^ ({self})")],
})
