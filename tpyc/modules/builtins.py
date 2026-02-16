"""
TurboPython built-in functions (Python builtins).

Defines functions like chr, print, len, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, BIGINT, FLOAT, CHAR, STR, VOID, BOOL, RANGE, RangeType, ListType,
    NamedType, TypeParamRef, OwnType, ALL_FIXED_INTS, FixedIntType,
)

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
            is_readonly=True,
        ),
    ])

    module.function("chr", overloads=[
        MethodDef(
            params=[ParamDef("i", INT32)],
            returns=CHAR,
            cpp="static_cast<char>({0})",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("i", BIGINT)],
            returns=CHAR,
            cpp="static_cast<char>(({0}).to_fixed_check<int32_t>())",
            is_readonly=True,
        ),
    ])

    module.function("abs", overloads=[
        MethodDef(
            params=[ParamDef("x", INT32)],
            returns=INT32,
            cpp="std::abs({0})",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("x", BIGINT)],
            returns=BIGINT,
            cpp="tpy::BigInt::abs({0})",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=FLOAT,
            cpp="std::fabs({0})",
            is_readonly=True,
        ),
    ])

    module.function("min", overloads=[
        MethodDef(
            params=[ParamDef("a", INT32), ParamDef("b", INT32)],
            returns=INT32,
            cpp="std::min({0}, {1})",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("a", BIGINT), ParamDef("b", BIGINT)],
            returns=BIGINT,
            cpp="(({0}) < ({1}) ? ({0}) : ({1}))",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("a", FLOAT), ParamDef("b", FLOAT)],
            returns=FLOAT,
            cpp="std::fmin({0}, {1})",
            is_readonly=True,
        ),
    ])

    module.function("max", overloads=[
        MethodDef(
            params=[ParamDef("a", INT32), ParamDef("b", INT32)],
            returns=INT32,
            cpp="std::max({0}, {1})",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("a", BIGINT), ParamDef("b", BIGINT)],
            returns=BIGINT,
            cpp="(({0}) > ({1}) ? ({0}) : ({1}))",
            is_readonly=True,
        ),
        MethodDef(
            params=[ParamDef("a", FLOAT), ParamDef("b", FLOAT)],
            returns=FLOAT,
            cpp="std::fmax({0}, {1})",
            is_readonly=True,
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
            is_readonly=True,
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
            is_readonly=True,
        )],
        "count": [MethodDef(
            params=[ParamDef("value", T)],
            returns=INT32,
            cpp="tpy::list_count({self}, {0})",
            is_readonly=True,
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
            is_readonly=True,
        )],
    }, constructors=[
        # list(iterable) - create list from any iterable, inferring element type
        MethodDef(
            params=[ParamDef("x", NamedType("NativeIterable", (T,), is_protocol=True))],
            returns=T,  # Placeholder - sema infers actual list[T] from argument
            cpp="tpy::from_range<std::vector<{T}>>({0})",
            is_readonly=True,
        ),
        # list(iterator) - create list from OptIterator (user-defined iterators)
        MethodDef(
            params=[ParamDef("x", NamedType("OptIterator", (T,), is_protocol=True))],
            returns=T,
            cpp="tpy::collect<std::vector<{T}>>({0})",
            is_readonly=True,
        ),
    ])

    module.register_type(STR, cpp_type="std::string_view",
        extends=["NativeIterable[Char]"],
        constructors=[
            MethodDef(params=[], returns=STR, cpp='""'),
            MethodDef(params=[ParamDef("x", STR)], returns=STR, cpp="{0}"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STR, cpp="tpy::bool_to_str({0})"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STR, cpp="tpy::char_to_str({0})"),
            *[MethodDef(params=[ParamDef("x", t)], returns=STR,
                       cpp=f"tpy::fixed_to_str<{t.to_cpp()}>({{0}})")
              for t in ALL_FIXED_INTS],
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STR, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STR, cpp="tpy::float_to_str({0})"),
        ],
        methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=CHAR,
            cpp="tpy::get_char({self}, {0})",
            is_readonly=True,
        )],
    })

    # int: arbitrary precision integer (BigInt)
    # Uses C++ operator overloads defined in tpy::BigInt
    module.register_type(BIGINT, cpp_type="tpy::BigInt", constructors=[
        MethodDef(params=[], returns=BIGINT, cpp="tpy::BigInt(0)"),
        *[MethodDef(params=[ParamDef("x", t)], returns=BIGINT,
                    cpp=f"tpy::BigInt(static_cast<{'int64_t' if t.signed else 'uint64_t'}>({{0}}))")
          for t in ALL_FIXED_INTS],
        MethodDef(params=[ParamDef("x", BIGINT)], returns=BIGINT, cpp="tpy::BigInt({0})"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=BIGINT, cpp="tpy::BigInt::from_float({0})"),
        MethodDef(params=[ParamDef("x", STR)], returns=BIGINT, cpp="tpy::BigInt::from_str({0})"),
        MethodDef(params=[ParamDef("x", BOOL)], returns=BIGINT, cpp="tpy::BigInt(static_cast<int32_t>({0}))"),
        MethodDef(params=[ParamDef("x", CHAR)], returns=BIGINT, cpp="tpy::BigInt(static_cast<int32_t>({0}))"),
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
        MethodDef(params=[ParamDef("x", BOOL)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", STR)], returns=FLOAT, cpp="tpy::float_from_str({0})"),
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
        MethodDef(params=[ParamDef("x", FLOAT)], returns=BOOL, cpp="({0} != 0.0)"),
        MethodDef(params=[ParamDef("x", STR)], returns=BOOL, cpp="(std::string_view({0}).size() != 0)"),
    ], methods={})

    # Char: Single character type
    module.register_type(CHAR, cpp_type="char", constructors=[
        MethodDef(params=[], returns=CHAR, cpp="'\\0'"),
        MethodDef(params=[ParamDef("x", INT32)], returns=CHAR, cpp="static_cast<char>({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=CHAR, cpp="static_cast<char>(({0}).to_fixed_check<int32_t>())"),
    ], methods={})

    # None: Void type (used for function returns)
    module.register_type(VOID, cpp_type="void", methods={})

    # Range: lazy iterator returned by range()
    RANGE_BIGINT = RangeType(BIGINT)

    module.type("Range", cpp_type="tpy::Range<{T}>", type_params=["T"],
        param_kinds=[TypeParamKind.TYPE],
        type_factory=lambda t: RangeType(t),
        extends=["NativeIterable[T]"],
        methods={},
    )

    # print() - variadic print function
    # Signature: print(*args) -> None
    # Special handling in sema/ and codegen_cpp/ because:
    # - Variadic: accepts any number of arguments
    # - Polymorphic: accepts any printable type (primitives, records, containers)
    module.function("print", overloads=[], special_handling=True)

    # range() - range iterator for for loops
    range_overloads = []
    for fixed_type in ALL_FIXED_INTS:
        cpp_t = fixed_type.to_cpp()
        rt = RangeType(fixed_type)
        range_overloads += [
            MethodDef(params=[ParamDef("stop", fixed_type)], returns=rt, cpp=f"tpy::Range<{cpp_t}>({{0}})", is_readonly=True),
            MethodDef(params=[ParamDef("start", fixed_type), ParamDef("stop", fixed_type)], returns=rt, cpp=f"tpy::Range<{cpp_t}>({{0}}, {{1}})", is_readonly=True),
            MethodDef(params=[ParamDef("start", fixed_type), ParamDef("stop", fixed_type), ParamDef("step", fixed_type)], returns=rt, cpp=f"tpy::Range<{cpp_t}>({{0}}, {{1}}, {{2}})", is_readonly=True),
        ]
    range_overloads += [
        MethodDef(params=[ParamDef("stop", BIGINT)], returns=RANGE_BIGINT, cpp="tpy::Range<tpy::BigInt>({0})", is_readonly=True),
        MethodDef(params=[ParamDef("start", BIGINT), ParamDef("stop", BIGINT)], returns=RANGE_BIGINT, cpp="tpy::Range<tpy::BigInt>({0}, {1})", is_readonly=True),
        MethodDef(params=[ParamDef("start", BIGINT), ParamDef("stop", BIGINT), ParamDef("step", BIGINT)], returns=RANGE_BIGINT, cpp="tpy::Range<tpy::BigInt>({0}, {1}, {2})", is_readonly=True),
    ]
    module.function("range", overloads=range_overloads)

    return module
