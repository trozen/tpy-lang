"""
TurboPython built-in functions (Python builtins).

Defines functions like chr, print, len, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, UINT64, BIGINT, FLOAT, FLOAT32, CHAR, STR, STRING, STRVIEW, VOID, BOOL, SLICE, ListType,
    NamedType, TypeParamRef, OwnType, ALL_FIXED_INTS, FixedIntType,
)

# Shorthand for type parameter T
T = TypeParamRef("T")

# Sized protocol type for len() parameter
SIZED = NamedType("Sized", is_protocol=True)

# Truthy protocol type for bool() parameter
TRUTHY = NamedType("Truthy", is_protocol=True)

# Stringable protocol type for str() parameter
STRINGABLE = NamedType("Stringable", is_protocol=True)

# Representable protocol type for repr() parameter
REPRESENTABLE = NamedType("Representable", is_protocol=True)

# Hashable protocol type for hash() parameter
HASHABLE = NamedType("Hashable", is_protocol=True)

NAME = "builtins"

def init_module() -> BuiltinModule:
    """Initialize and return the builtins module."""
    module = BuiltinModule(NAME)

    # round(): generic round[T](float)->T with default T=default_int_type,
    # identity for integer types, and ndigits variants
    round_overloads = [
        # float -> T (generic, defaults to default_int_type)
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=T, cpp="::tpy::round_to<{T}>({0})", is_readonly=True, is_pure=True,
        ),
        # float, ndigits -> float
        MethodDef(
            params=[ParamDef("x", FLOAT), ParamDef("ndigits", INT32)],
            returns=FLOAT, cpp="::tpy::round_float({0}, {1})", is_readonly=True, is_pure=True,
        ),
    ]
    for t in ALL_FIXED_INTS:
        cpp_t = t.to_cpp()
        round_overloads.append(MethodDef(
            params=[ParamDef("x", t)], returns=t, cpp="({0})", is_readonly=True, is_pure=True))
        round_overloads.append(MethodDef(
            params=[ParamDef("x", t), ParamDef("ndigits", INT32)],
            returns=t, cpp=f"::tpy::round_fixed<{cpp_t}>({{0}}, {{1}})", is_readonly=True, is_pure=True))
    round_overloads.append(MethodDef(
        params=[ParamDef("x", BIGINT)], returns=BIGINT, cpp="({0})", is_readonly=True, is_pure=True))
    round_overloads.append(MethodDef(
        params=[ParamDef("x", BIGINT), ParamDef("ndigits", INT32)],
        returns=BIGINT, cpp="::tpy::round_bigint({0}, {1})", is_readonly=True, is_pure=True))
    module.function("round", overloads=round_overloads,
                    type_params=["T"], type_param_defaults={"T": "DEFAULT_INT"})


    # None: Void type (used for function returns)
    module.register_type(VOID, cpp_type="void", methods={})

    # slice: built-in type for subscript ranges (start/stop are Optional[Int32])
    module.register_type(SLICE, cpp_type="::tpy::Slice", methods={})

    # print() - variadic print function
    # Signature: print(*args) -> None
    # Special handling in sema/ and codegen_cpp/ because:
    # - Variadic: accepts any number of arguments
    # - Polymorphic: accepts any printable type (primitives, records, containers)
    module.function("print", overloads=[], special_handling=True)

    # isinstance() - type checking for union type narrowing
    # Special handling in sema (validates union member, sets isinstance_var/isinstance_type)
    # and codegen (emits std::holds_alternative)
    module.function("isinstance", overloads=[], special_handling=True)

    # iter(x) -- calls x.__iter__(), returns Iterator[T]
    # Stays hardcoded: return type is a protocol (not expressible in .py)
    module.function("iter", overloads=[
        MethodDef(
            params=[ParamDef("x", NamedType("Iterable", (T,), is_protocol=True))],
            returns=NamedType("Iterator", (T,), is_protocol=True),
            cpp="::tpy::__iter__({0})",
            is_readonly=True, is_pure=True,
        ),
    ], type_params=["T"])

    return module
