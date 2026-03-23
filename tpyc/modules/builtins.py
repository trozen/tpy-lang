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

    module.register_type(STR, cpp_type="std::string",
        extends=["NativeIterable[Char]", "Iterable[Char]", "Equatable"],
        methods={
        "__init__": [
            MethodDef(params=[], returns=STR, cpp='std::string()'),
            MethodDef(params=[ParamDef("x", STR)], returns=STR, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STR, cpp="std::string(::tpy::bool_to_str({0}))"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STR, cpp="std::string(::tpy::char_to_str({0}))"),
            *[MethodDef(params=[ParamDef("x", t)], returns=STR,
                       cpp=f"::tpy::fixed_to_str<{t.to_cpp()}>({{0}})")
              for t in ALL_FIXED_INTS],
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STR, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STR, cpp="::tpy::float_to_str({0})"),
            MethodDef(params=[ParamDef("x", FLOAT32)], returns=STR, cpp="::tpy::float_to_str(static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("x", STRINGABLE)], returns=STR,
                      cpp="std::string(::tpy::__str__({0}))", is_readonly=True, is_pure=True),
            MethodDef(params=[ParamDef("x", REPRESENTABLE)], returns=STR,
                      cpp="std::string(::tpy::__repr__({0}))", is_readonly=True, is_pure=True),
        ],
        "__iter__": [MethodDef(
            params=[],
            returns=NamedType("Iterator", (CHAR,), is_protocol=True),
            cpp="::tpy::__iter__({self})",
            is_readonly=True, is_pure=True,
        )],
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True, is_pure=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=CHAR,
            cpp="::tpy::__getitem__({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "__add__": [
            MethodDef(
                params=[ParamDef("other", STR)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRING)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRVIEW)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", CHAR)],
                returns=STRING,
                cpp="::tpy::str_concat({self}, ::tpy::char_to_str({0}))",
            ),
        ],
        "__mul__": [MethodDef(
            params=[ParamDef("n", INT32)],
            returns=STR, cpp="::tpy::str_repeat({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "__rmul__": [MethodDef(
            params=[ParamDef("n", INT32)],
            returns=STR, cpp="::tpy::str_repeat({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "split": [
            MethodDef(
                params=[],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split_whitespace({self})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR)],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split({self}, {0})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR), ParamDef("maxsplit", INT32)],
                returns=OwnType(ListType(STR)),
                cpp="::tpy::str_split({self}, {0}, {1})",
                is_readonly=True, is_pure=True,
            ),
        ],
        "join": [MethodDef(
            params=[ParamDef("items", NamedType("Iterable", (STR,), is_protocol=True))],
            returns=STR,
            cpp="::tpy::str_join({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "strip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_strip({self})", is_readonly=True, is_pure=True,
        )],
        "lstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_lstrip({self})", is_readonly=True, is_pure=True,
        )],
        "rstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="::tpy::str_rstrip({self})", is_readonly=True, is_pure=True,
        )],
        "replace": [MethodDef(
            params=[ParamDef("old", STR), ParamDef("new", STR)],
            returns=STR, cpp="::tpy::str_replace({self}, {0}, {1})", is_readonly=True, is_pure=True,
        )],
        "find": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_find({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "rfind": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_rfind({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "index": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_index({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "startswith": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=BOOL, cpp="::tpy::str_startswith({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "endswith": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=BOOL, cpp="::tpy::str_endswith({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "upper": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_upper({self})", is_readonly=True, is_pure=True,
        )],
        "lower": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_lower({self})", is_readonly=True, is_pure=True,
        )],
        "count": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_count({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "isdigit": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isdigit({self})", is_readonly=True, is_pure=True,
        )],
        "isalpha": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isalpha({self})", is_readonly=True, is_pure=True,
        )],
        "isalnum": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isalnum({self})", is_readonly=True, is_pure=True,
        )],
        "isspace": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isspace({self})", is_readonly=True, is_pure=True,
        )],
        "isupper": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_isupper({self})", is_readonly=True, is_pure=True,
        )],
        "islower": [MethodDef(
            params=[], returns=BOOL, cpp="::tpy::str_islower({self})", is_readonly=True, is_pure=True,
        )],
        "capitalize": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_capitalize({self})", is_readonly=True, is_pure=True,
        )],
        "title": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_title({self})", is_readonly=True, is_pure=True,
        )],
        "swapcase": [MethodDef(
            params=[], returns=STR, cpp="::tpy::str_swapcase({self})", is_readonly=True, is_pure=True,
        )],
        "removeprefix": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=STRVIEW, cpp="::tpy::str_removeprefix({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "removesuffix": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=STRVIEW, cpp="::tpy::str_removesuffix({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "rindex": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="::tpy::str_rindex({self}, {0})", is_readonly=True, is_pure=True,
        )],
        "splitlines": [MethodDef(
            params=[], returns=OwnType(ListType(STR)),
            cpp="::tpy::str_splitlines({self})", is_readonly=True, is_pure=True,
        )],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)],
    })

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
