"""
TurboPython built-in functions (Python builtins).

Defines functions like chr, print, len, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import INT32, BIGINT, FLOAT, CHAR, STR, VOID, BOOL, RANGE, RangeType, ListType, NamedType, TypeParamRef, OwnType

# Shorthand for type parameter T
T = TypeParamRef("T")

# Sized protocol type for len() parameter
SIZED = NamedType("Sized", is_protocol=True)

NAME = "builtins"

# Methods that mutate the list (used by sema to track list literal mutation)
LIST_MUTATION_METHODS = frozenset({
    "append", "pop", "insert", "remove", "clear", "extend", "reverse", "__setitem__",
})


def init_module() -> BuiltinModule:
    """Initialize and return the builtins module."""
    module = BuiltinModule(NAME)

    module.function("len", overloads=[
        MethodDef(
            params=[ParamDef("x", SIZED)],
            returns=INT32,
            cpp="tpy::__len__({0})",
        ),
    ])

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

    module.function("abs", overloads=[
        MethodDef(
            params=[ParamDef("x", INT32)],
            returns=INT32,
            cpp="std::abs({0})",
        ),
        MethodDef(
            params=[ParamDef("x", BIGINT)],
            returns=BIGINT,
            cpp="tpy::BigInt::abs({0})",
        ),
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::fabs({0})",
        ),
    ])

    module.function("min", overloads=[
        MethodDef(
            params=[ParamDef("a", INT32), ParamDef("b", INT32)],
            returns=INT32,
            cpp="std::min({0}, {1})",
        ),
        MethodDef(
            params=[ParamDef("a", BIGINT), ParamDef("b", BIGINT)],
            returns=BIGINT,
            cpp="(({0}) < ({1}) ? ({0}) : ({1}))",
        ),
        MethodDef(
            params=[ParamDef("a", FLOAT), ParamDef("b", FLOAT)],
            returns=FLOAT,
            cpp="std::fmin({0}, {1})",
        ),
    ])

    module.function("max", overloads=[
        MethodDef(
            params=[ParamDef("a", INT32), ParamDef("b", INT32)],
            returns=INT32,
            cpp="std::max({0}, {1})",
        ),
        MethodDef(
            params=[ParamDef("a", BIGINT), ParamDef("b", BIGINT)],
            returns=BIGINT,
            cpp="(({0}) > ({1}) ? ({0}) : ({1}))",
        ),
        MethodDef(
            params=[ParamDef("a", FLOAT), ParamDef("b", FLOAT)],
            returns=FLOAT,
            cpp="std::fmax({0}, {1})",
        ),
    ])

    # list[T]: Dynamic list backed by std::vector<T>
    # Methods use TypeParamRef("T") which gets resolved to element_type at lookup time
    module.type("list", cpp_type="std::vector<{T}>", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: ListType(t),
                extends=["NativeIterable[T]", "NativeContiguous[T]", "NativeRangeConstructible[T]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
        )],
        "append": [MethodDef(
            params=[ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="{self}.push_back({0})",
        )],
        "pop": [
            MethodDef(
                params=[],
                returns=T,
                cpp="tpy::pop_back({self})",
            ),
            MethodDef(
                params=[ParamDef("index", INT32)],
                returns=T,
                cpp="tpy::list_pop_at({self}, {0})",
            ),
        ],
        "clear": [MethodDef(
            params=[],
            returns=VOID,
            cpp="{self}.clear()",
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="tpy::get_item({self}, {0})",
            is_readonly=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="tpy::set_item({self}, {0}, {1})",
        )],
        "insert": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="tpy::list_insert({self}, {0}, {1})",
        )],
        "remove": [MethodDef(
            params=[ParamDef("value", T)],
            returns=VOID,
            cpp="tpy::list_remove({self}, {0})",
        )],
        "extend": [MethodDef(
            params=[ParamDef("other", NamedType("NativeIterable", (T,), is_protocol=True))],
            returns=VOID,
            cpp="tpy::list_extend({self}, {0})",
        )],
        "index": [MethodDef(
            params=[ParamDef("value", T)],
            returns=INT32,
            cpp="tpy::list_index({self}, {0})",
        )],
        "count": [MethodDef(
            params=[ParamDef("value", T)],
            returns=INT32,
            cpp="tpy::list_count({self}, {0})",
        )],
        "reverse": [MethodDef(
            params=[],
            returns=VOID,
            cpp="tpy::list_reverse({self})",
        )],
        "copy": [MethodDef(
            params=[],
            returns=ListType(T),
            cpp="tpy::list_copy({self})",
        )],
    }, constructors=[
        # list(iterable) - create list from any iterable, inferring element type
        MethodDef(
            params=[ParamDef("x", NamedType("NativeIterable", (T,), is_protocol=True))],
            returns=T,  # Placeholder - sema infers actual list[T] from argument
            cpp="std::vector<{T}>({0}.begin(), {0}.end())",
        ),
    ])

    module.register_type(STR, cpp_type="std::string_view",
        extends=["NativeIterable[Char]"],
        constructors=[
            MethodDef(params=[], returns=STR, cpp='""'),
            MethodDef(params=[ParamDef("x", STR)], returns=STR, cpp="{0}"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STR, cpp="tpy::bool_to_str({0})"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STR, cpp="tpy::char_to_str({0})"),
            MethodDef(params=[ParamDef("x", INT32)], returns=STR, cpp="tpy::int32_to_str({0})"),
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STR, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STR, cpp="tpy::float_to_str({0})"),
        ],
        methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=CHAR,
            cpp="tpy::get_char({self}, {0})",
        )],
    })

    # int: arbitrary precision integer (BigInt)
    # Uses C++ operator overloads defined in tpy::BigInt
    module.register_type(BIGINT, cpp_type="tpy::BigInt", constructors=[
        MethodDef(params=[], returns=BIGINT, cpp="tpy::BigInt(0)"),
        MethodDef(params=[ParamDef("x", INT32)], returns=BIGINT, cpp="tpy::BigInt({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=BIGINT, cpp="tpy::BigInt({0})"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=BIGINT, cpp="tpy::BigInt::from_float({0})"),
        MethodDef(params=[ParamDef("x", STR)], returns=BIGINT, cpp="tpy::BigInt::from_str({0})"),
    ], methods={
        **make_binop_methods({
            "__add__": ("({self}) + ({0})", BIGINT),
            "__sub__": ("({self}) - ({0})", BIGINT),
            "__mul__": ("({self}) * ({0})", BIGINT),
            "__truediv__": ("static_cast<double>({self}) / static_cast<double>({0})", FLOAT),
            "__floordiv__": ("({self}) / ({0})", BIGINT),
            "__mod__": ("({self}) % ({0})", BIGINT),
            "__pow__": ("({self}).pow({0})", BIGINT),
            "__lshift__": ("({self}) << ({0})", BIGINT),
            "__rshift__": ("({self}) >> ({0})", BIGINT),
            "__and__": ("({self}) & ({0})", BIGINT),
            "__or__": ("({self}) | ({0})", BIGINT),
            "__xor__": ("({self}) ^ ({0})", BIGINT),
        }, self_type=BIGINT),
        "__neg__": [MethodDef(params=[], returns=BIGINT, cpp="-({self})")],
        "__invert__": [MethodDef(params=[], returns=BIGINT, cpp="~({self})")],
    }, extends=["Comparable"])

    # float: 64-bit IEEE 754 double precision floating point
    module.register_type(FLOAT, cpp_type="double", constructors=[
        MethodDef(params=[], returns=FLOAT, cpp="0.0"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", INT32)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=FLOAT, cpp="static_cast<double>({0})"),
    ], methods={
        # Binary arithmetic operators (float, float)
        "__add__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({self}) + ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="({self}) + static_cast<double>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="({self}) + static_cast<double>({0})"),
        ],
        "__sub__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({self}) - ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="({self}) - static_cast<double>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="({self}) - static_cast<double>({0})"),
        ],
        "__mul__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({self}) * ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="({self}) * static_cast<double>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="({self}) * static_cast<double>({0})"),
        ],
        "__truediv__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({self}) / ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="({self}) / static_cast<double>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="({self}) / static_cast<double>({0})"),
        ],
        "__floordiv__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::floor(({self}) / ({0}))"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::floor(({self}) / static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::floor(({self}) / static_cast<double>({0}))"),
        ],
        "__mod__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::fmod({self}, {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::fmod({self}, static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::fmod({self}, static_cast<double>({0}))"),
        ],
        "__pow__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::pow({self}, {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::pow({self}, static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::pow({self}, static_cast<double>({0}))"),
        ],

        # Unary operators
        "__neg__": [MethodDef(params=[], returns=FLOAT, cpp="-({self})")],

        # Reverse operators (for int + float -> float)
        "__radd__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) + ({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="static_cast<double>({0}) + ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="static_cast<double>({0}) + ({self})"),
        ],
        "__rsub__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) - ({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="static_cast<double>({0}) - ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="static_cast<double>({0}) - ({self})"),
        ],
        "__rmul__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) * ({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="static_cast<double>({0}) * ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="static_cast<double>({0}) * ({self})"),
        ],
        "__rtruediv__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) / ({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="static_cast<double>({0}) / ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="static_cast<double>({0}) / ({self})"),
        ],
        "__rfloordiv__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::floor(({0}) / ({self}))"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::floor(static_cast<double>({0}) / ({self}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::floor(static_cast<double>({0}) / ({self}))"),
        ],
        "__rmod__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::fmod({0}, {self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::fmod(static_cast<double>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::fmod(static_cast<double>({0}), {self})"),
        ],
        "__rpow__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::pow({0}, {self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::pow(static_cast<double>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::pow(static_cast<double>({0}), {self})"),
        ],
    }, extends=["Comparable"])

    # bool: Boolean type
    module.register_type(BOOL, cpp_type="bool", constructors=[
        MethodDef(params=[], returns=BOOL, cpp="false"),
        MethodDef(params=[ParamDef("x", BOOL)], returns=BOOL, cpp="{0}"),
        MethodDef(params=[ParamDef("x", INT32)], returns=BOOL, cpp="({0} != 0)"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=BOOL, cpp="({0} != 0)"),
    ], methods={})

    # Char: Single character type
    module.register_type(CHAR, cpp_type="char", constructors=[
        MethodDef(params=[], returns=CHAR, cpp="'\\0'"),
        MethodDef(params=[ParamDef("x", INT32)], returns=CHAR, cpp="static_cast<char>({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=CHAR, cpp="static_cast<char>(({0}).to_int32())"),
    ], methods={})

    # None: Void type (used for function returns)
    module.register_type(VOID, cpp_type="void", methods={})

    # Range: lazy iterator returned by range()
    RANGE_BIGINT = RangeType(BIGINT)

    module.register_type(RANGE, cpp_type="tpy::Range<int32_t>",
        extends=["NativeIterator[Int32]"],
        methods={},
    )
    module.register_type(RANGE_BIGINT, cpp_type="tpy::Range<tpy::BigInt>",
        extends=["NativeIterator[int]"],
        methods={},
    )

    # print() - variadic print function
    # Signature: print(*args) -> None
    # Special handling in sema/ and codegen_cpp/ because:
    # - Variadic: accepts any number of arguments
    # - Polymorphic: accepts any printable type (primitives, records, containers)
    module.function("print", overloads=[], special_handling=True)

    # range() - range iterator for for loops
    module.function("range", overloads=[
        # Int32 overloads
        MethodDef(params=[ParamDef("stop", INT32)], returns=RANGE, cpp="tpy::Range<int32_t>({0})"),
        MethodDef(params=[ParamDef("start", INT32), ParamDef("stop", INT32)], returns=RANGE, cpp="tpy::Range<int32_t>({0}, {1})"),
        MethodDef(params=[ParamDef("start", INT32), ParamDef("stop", INT32), ParamDef("step", INT32)], returns=RANGE, cpp="tpy::Range<int32_t>({0}, {1}, {2})"),
        # BigInt overloads
        MethodDef(params=[ParamDef("stop", BIGINT)], returns=RANGE_BIGINT, cpp="tpy::Range<tpy::BigInt>({0})"),
        MethodDef(params=[ParamDef("start", BIGINT), ParamDef("stop", BIGINT)], returns=RANGE_BIGINT, cpp="tpy::Range<tpy::BigInt>({0}, {1})"),
        MethodDef(params=[ParamDef("start", BIGINT), ParamDef("stop", BIGINT), ParamDef("step", BIGINT)], returns=RANGE_BIGINT, cpp="tpy::Range<tpy::BigInt>({0}, {1}, {2})"),
    ])

    return module
