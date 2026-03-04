"""
TurboPython built-in functions (Python builtins).

Defines functions like chr, print, len, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, UINT64, BIGINT, FLOAT, CHAR, STR, STRING, STRVIEW, VOID, BOOL, RANGE, RangeType, ListType,
    DictType, DictKeysViewType, DictValuesViewType, DictItemsViewType,
    NamedType, TypeParamRef, OwnType, OptionalType, TupleType, ALL_FIXED_INTS, FixedIntType,
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

# Methods that mutate the list (used by sema to track list literal mutation)
LIST_MUTATION_METHODS = frozenset({
    "append", "pop", "insert", "remove", "clear", "extend", "reverse",
    "__setitem__", "__delitem__",
})

# Reserved for future dict literal type deduction (cf. LIST_MUTATION_METHODS)
DICT_MUTATION_METHODS = frozenset({
    "__setitem__", "__delitem__", "pop", "clear", "update", "setdefault",
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

    module.function("repr", overloads=[
        MethodDef(
            params=[ParamDef("x", REPRESENTABLE)],
            returns=STR,
            cpp="std::string(tpy::__repr__({0}))",
            is_readonly=True,
        ),
    ])

    module.function("hash", overloads=[
        MethodDef(
            params=[ParamDef("x", HASHABLE)],
            returns=UINT64,
            cpp="tpy::__hash__({0})",
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

    module.function("ord", overloads=[
        MethodDef(
            params=[ParamDef("c", CHAR)],
            returns=INT32,
            cpp="static_cast<int32_t>(static_cast<unsigned char>({0}))",
            is_readonly=True,
        ),
    ])

    pow_overloads = [
        MethodDef(params=[ParamDef("x", BIGINT), ParamDef("y", BIGINT)],
                  returns=BIGINT, cpp="({0}).pow({1})", is_readonly=True),
        MethodDef(params=[ParamDef("x", FLOAT), ParamDef("y", FLOAT)],
                  returns=FLOAT, cpp="std::pow({0}, {1})", is_readonly=True),
    ]
    for t in ALL_FIXED_INTS:
        cpp_t = t.to_cpp()
        pow_overloads.append(MethodDef(
            params=[ParamDef("x", t), ParamDef("y", t)],
            returns=t, cpp=f"tpy::pow_check<{cpp_t}>({{0}}, {{1}})", is_readonly=True,
        ))
    module.function("pow", overloads=pow_overloads)

    # round(): generic round[T](float)->T with default T=default_int_type,
    # identity for integer types, and ndigits variants
    round_overloads = [
        # float -> T (generic, defaults to default_int_type)
        MethodDef(
            params=[ParamDef("x", FLOAT)],
            returns=T, cpp="tpy::round_to<{T}>({0})", is_readonly=True,
        ),
        # float, ndigits -> float
        MethodDef(
            params=[ParamDef("x", FLOAT), ParamDef("ndigits", INT32)],
            returns=FLOAT, cpp="tpy::round_float({0}, {1})", is_readonly=True,
        ),
    ]
    for t in ALL_FIXED_INTS:
        cpp_t = t.to_cpp()
        round_overloads.append(MethodDef(
            params=[ParamDef("x", t)], returns=t, cpp="({0})", is_readonly=True))
        round_overloads.append(MethodDef(
            params=[ParamDef("x", t), ParamDef("ndigits", INT32)],
            returns=t, cpp=f"tpy::round_fixed<{cpp_t}>({{0}}, {{1}})", is_readonly=True))
    round_overloads.append(MethodDef(
        params=[ParamDef("x", BIGINT)], returns=BIGINT, cpp="({0})", is_readonly=True))
    round_overloads.append(MethodDef(
        params=[ParamDef("x", BIGINT), ParamDef("ndigits", INT32)],
        returns=BIGINT, cpp="tpy::round_bigint({0}, {1})", is_readonly=True))
    module.function("round", overloads=round_overloads,
                    type_params=["T"], type_param_defaults={"T": "DEFAULT_INT"})

    # divmod(a, b) -> tuple[T, T]
    divmod_overloads = [
        MethodDef(params=[ParamDef("a", BIGINT), ParamDef("b", BIGINT)],
                  returns=TupleType((BIGINT, BIGINT)),
                  cpp="tpy::divmod_bigint({0}, {1})", is_readonly=True),
        MethodDef(params=[ParamDef("a", FLOAT), ParamDef("b", FLOAT)],
                  returns=TupleType((FLOAT, FLOAT)),
                  cpp="tpy::divmod_float({0}, {1})", is_readonly=True),
    ]
    for t in ALL_FIXED_INTS:
        cpp_t = t.to_cpp()
        divmod_overloads.append(MethodDef(
            params=[ParamDef("a", t), ParamDef("b", t)],
            returns=TupleType((t, t)),
            cpp=f"tpy::divmod_fixed<{cpp_t}>({{0}}, {{1}})", is_readonly=True,
        ))
    module.function("divmod", overloads=divmod_overloads)

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
        "unchecked_get": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
            is_readonly=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="tpy::__getitem__({self}, {0})",
            is_readonly=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="tpy::__setitem__({self}, {0}, {1})",
        )],
        "__delitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=VOID,
            cpp="tpy::__delitem__({self}, {0})",
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
        "__add__": [MethodDef(
            params=[ParamDef("other", ListType(T))],
            returns=ListType(T),
            cpp="tpy::list_concat({self}, {0})",
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

    # dict[K, V]: Ordered hash map backed by tpy::ordered_map<K, V>
    K = TypeParamRef("K")
    V = TypeParamRef("V")
    module.type("dict", cpp_type="tpy::ordered_map<{K}, {V}>",
                type_params=["K", "V"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.TYPE],
                type_factory=lambda k, v: DictType(k, v),
                extends=["NativeIterable[K]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="{self}.size()",
            is_readonly=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("key", K)],
            returns=V,
            cpp="tpy::__getitem__({self}, {0})",
            is_readonly=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("key", K), ParamDef("value", OwnType(V))],
            returns=VOID,
            cpp="tpy::__setitem__({self}, {0}, {1})",
        )],
        "__delitem__": [MethodDef(
            params=[ParamDef("key", K)],
            returns=VOID,
            cpp="tpy::__delitem__({self}, {0})",
        )],
        "get": [
            MethodDef(
                params=[ParamDef("key", K)],
                returns=OptionalType(V),
                cpp="tpy::dict_get({self}, {0})",
                is_readonly=True,
            ),
            MethodDef(
                params=[ParamDef("key", K), ParamDef("default", V)],
                returns=V,
                cpp="tpy::dict_get_default({self}, {0}, {1})",
                is_readonly=True,
            ),
        ],
        "pop": [
            MethodDef(
                params=[ParamDef("key", K)],
                returns=V,
                cpp="tpy::dict_pop({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("key", K), ParamDef("default", V)],
                returns=V,
                cpp="tpy::dict_pop_default({self}, {0}, {1})",
            ),
        ],
        "clear": [MethodDef(
            params=[],
            returns=VOID,
            cpp="{self}.clear()",
        )],
        "update": [MethodDef(
            params=[ParamDef("other", DictType(K, V))],
            returns=VOID,
            cpp="tpy::dict_update({self}, {0})",
        )],
        "setdefault": [MethodDef(
            params=[ParamDef("key", K), ParamDef("default", OwnType(V))],
            returns=V,
            cpp="tpy::dict_setdefault({self}, {0}, {1})",
        )],
        "keys": [MethodDef(
            params=[],
            returns=DictKeysViewType(K, V),
            cpp="tpy::dict_keys({self})",
            is_readonly=True,
        )],
        "values": [MethodDef(
            params=[],
            returns=DictValuesViewType(K, V),
            cpp="tpy::dict_values({self})",
            is_readonly=True,
        )],
        "items": [MethodDef(
            params=[],
            returns=DictItemsViewType(K, V),
            cpp="tpy::dict_items({self})",
            is_readonly=True,
        )],
    }, constructors=[
        # dict(iterable) - create dict from iterable of tuple[K, V]
        MethodDef(
            params=[ParamDef("x", NamedType("NativeIterable", (TupleType((K, V)),), is_protocol=True))],
            returns=V,  # Placeholder - sema infers actual dict[K,V] from argument
            cpp="tpy::dict_from_pairs<{K}, {V}>({0})",
            is_readonly=True,
        ),
        # dict(iterator) - create dict from OptIterator of tuple[K, V]
        MethodDef(
            params=[ParamDef("x", NamedType("OptIterator", (TupleType((K, V)),), is_protocol=True))],
            returns=V,  # Placeholder - sema infers actual dict[K,V] from argument
            cpp="tpy::dict_collect_pairs<{K}, {V}>({0})",
            is_readonly=True,
        ),
    ])

    # dict_keys[K, V]: Keys view backed by tpy::dict_keys_view<K, V>
    module.type("dict_keys", cpp_type="tpy::dict_keys_view<{K}, {V}>",
                type_params=["K", "V"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.TYPE],
                type_factory=lambda k, v: DictKeysViewType(k, v),
                extends=["NativeIterable[K]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="tpy::__len__({self})",
            is_readonly=True,
        )],
    })

    # dict_values[K, V]: Values view backed by tpy::dict_values_view<K, V>
    module.type("dict_values", cpp_type="tpy::dict_values_view<{K}, {V}>",
                type_params=["K", "V"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.TYPE],
                type_factory=lambda k, v: DictValuesViewType(k, v),
                extends=["NativeIterable[V]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="tpy::__len__({self})",
            is_readonly=True,
        )],
    })

    # dict_items[K, V]: Items view backed by tpy::dict_items_view<K, V>
    module.type("dict_items", cpp_type="tpy::dict_items_view<{K}, {V}>",
                type_params=["K", "V"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.TYPE],
                type_factory=lambda k, v: DictItemsViewType(k, v),
                extends=["NativeIterable[tuple[K, V]]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="tpy::__len__({self})",
            is_readonly=True,
        )],
    })

    module.register_type(STR, cpp_type="std::string",
        extends=["NativeIterable[Char]"],
        constructors=[
            MethodDef(params=[], returns=STR, cpp='std::string()'),
            MethodDef(params=[ParamDef("x", STR)], returns=STR, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STR, cpp="std::string(tpy::bool_to_str({0}))"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STR, cpp="std::string(tpy::char_to_str({0}))"),
            *[MethodDef(params=[ParamDef("x", t)], returns=STR,
                       cpp=f"tpy::fixed_to_str<{t.to_cpp()}>({{0}})")
              for t in ALL_FIXED_INTS],
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STR, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STR, cpp="tpy::float_to_str({0})"),
            MethodDef(params=[ParamDef("x", STRINGABLE)], returns=STR,
                      cpp="std::string(tpy::__str__({0}))", is_readonly=True),
            MethodDef(params=[ParamDef("x", REPRESENTABLE)], returns=STR,
                      cpp="std::string(tpy::__repr__({0}))", is_readonly=True),
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
            cpp="tpy::__getitem__({self}, {0})",
            is_readonly=True,
        )],
        "__add__": [
            MethodDef(
                params=[ParamDef("other", STR)],
                returns=STRING,
                cpp="tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRING)],
                returns=STRING,
                cpp="tpy::str_concat({self}, {0})",
            ),
            MethodDef(
                params=[ParamDef("other", STRVIEW)],
                returns=STRING,
                cpp="tpy::str_concat({self}, {0})",
            ),
        ],
        "split": [
            MethodDef(
                params=[],
                returns=ListType(STR),
                cpp="tpy::str_split_whitespace({self})",
                is_readonly=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR)],
                returns=ListType(STR),
                cpp="tpy::str_split({self}, {0})",
                is_readonly=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR), ParamDef("maxsplit", INT32)],
                returns=ListType(STR),
                cpp="tpy::str_split({self}, {0}, {1})",
                is_readonly=True,
            ),
        ],
        "join": [MethodDef(
            params=[ParamDef("items", NamedType("NativeIterable", (STR,), is_protocol=True))],
            returns=STR,
            cpp="tpy::str_join({self}, {0})",
            is_readonly=True,
        )],
        "strip": [MethodDef(
            params=[], returns=STRVIEW, cpp="tpy::str_strip({self})", is_readonly=True,
        )],
        "lstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="tpy::str_lstrip({self})", is_readonly=True,
        )],
        "rstrip": [MethodDef(
            params=[], returns=STRVIEW, cpp="tpy::str_rstrip({self})", is_readonly=True,
        )],
        "replace": [MethodDef(
            params=[ParamDef("old", STR), ParamDef("new", STR)],
            returns=STR, cpp="tpy::str_replace({self}, {0}, {1})", is_readonly=True,
        )],
        "find": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="tpy::str_find({self}, {0})", is_readonly=True,
        )],
        "rfind": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="tpy::str_rfind({self}, {0})", is_readonly=True,
        )],
        "index": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="tpy::str_index({self}, {0})", is_readonly=True,
        )],
        "startswith": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=BOOL, cpp="tpy::str_startswith({self}, {0})", is_readonly=True,
        )],
        "endswith": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=BOOL, cpp="tpy::str_endswith({self}, {0})", is_readonly=True,
        )],
        "upper": [MethodDef(
            params=[], returns=STR, cpp="tpy::str_upper({self})", is_readonly=True,
        )],
        "lower": [MethodDef(
            params=[], returns=STR, cpp="tpy::str_lower({self})", is_readonly=True,
        )],
        "count": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="tpy::str_count({self}, {0})", is_readonly=True,
        )],
        "isdigit": [MethodDef(
            params=[], returns=BOOL, cpp="tpy::str_isdigit({self})", is_readonly=True,
        )],
        "isalpha": [MethodDef(
            params=[], returns=BOOL, cpp="tpy::str_isalpha({self})", is_readonly=True,
        )],
        "isalnum": [MethodDef(
            params=[], returns=BOOL, cpp="tpy::str_isalnum({self})", is_readonly=True,
        )],
        "isspace": [MethodDef(
            params=[], returns=BOOL, cpp="tpy::str_isspace({self})", is_readonly=True,
        )],
        "isupper": [MethodDef(
            params=[], returns=BOOL, cpp="tpy::str_isupper({self})", is_readonly=True,
        )],
        "islower": [MethodDef(
            params=[], returns=BOOL, cpp="tpy::str_islower({self})", is_readonly=True,
        )],
        "capitalize": [MethodDef(
            params=[], returns=STR, cpp="tpy::str_capitalize({self})", is_readonly=True,
        )],
        "title": [MethodDef(
            params=[], returns=STR, cpp="tpy::str_title({self})", is_readonly=True,
        )],
        "swapcase": [MethodDef(
            params=[], returns=STR, cpp="tpy::str_swapcase({self})", is_readonly=True,
        )],
        "removeprefix": [MethodDef(
            params=[ParamDef("prefix", STR)],
            returns=STRVIEW, cpp="tpy::str_removeprefix({self}, {0})", is_readonly=True,
        )],
        "removesuffix": [MethodDef(
            params=[ParamDef("suffix", STR)],
            returns=STRVIEW, cpp="tpy::str_removesuffix({self}, {0})", is_readonly=True,
        )],
        "rindex": [MethodDef(
            params=[ParamDef("sub", STR)],
            returns=INT32, cpp="tpy::str_rindex({self}, {0})", is_readonly=True,
        )],
        "splitlines": [MethodDef(
            params=[], returns=ListType(STR),
            cpp="tpy::str_splitlines({self})", is_readonly=True,
        )],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="tpy::__hash__({self})", is_readonly=True)],
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
            "__truediv__": ("tpy::truediv(static_cast<double>({self}), static_cast<double>({0}))", FLOAT),
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
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="tpy::__hash__({self})", is_readonly=True)],
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
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="tpy::truediv({self}, {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="tpy::truediv({self}, static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="tpy::truediv({self}, static_cast<double>({0}))"),
        ],
        "__floordiv__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="tpy::floordiv({self}, {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="tpy::floordiv({self}, static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="tpy::floordiv({self}, static_cast<double>({0}))"),
        ],
        "__mod__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="tpy::fmod({self}, {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="tpy::fmod({self}, static_cast<double>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="tpy::fmod({self}, static_cast<double>({0}))"),
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
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="tpy::truediv({0}, {self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="tpy::truediv(static_cast<double>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="tpy::truediv(static_cast<double>({0}), {self})"),
        ],
        "__rfloordiv__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="tpy::floordiv({0}, {self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="tpy::floordiv(static_cast<double>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="tpy::floordiv(static_cast<double>({0}), {self})"),
        ],
        "__rmod__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="tpy::fmod({0}, {self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="tpy::fmod(static_cast<double>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="tpy::fmod(static_cast<double>({0}), {self})"),
        ],
        "__rpow__": [
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::pow({0}, {self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT, cpp="std::pow(static_cast<double>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT, cpp="std::pow(static_cast<double>({0}), {self})"),
        ],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="tpy::__hash__({self})", is_readonly=True)],
    }, extends=["Comparable"])

    # bool: Boolean type
    module.register_type(BOOL, cpp_type="bool", constructors=[
        MethodDef(params=[], returns=BOOL, cpp="false"),
        MethodDef(params=[ParamDef("x", BOOL)], returns=BOOL, cpp="{0}"),
        MethodDef(params=[ParamDef("x", INT32)], returns=BOOL, cpp="({0} != 0)"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=BOOL, cpp="({0} != 0)"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=BOOL, cpp="({0} != 0.0)"),
        MethodDef(params=[ParamDef("x", STR)], returns=BOOL, cpp="(std::string_view({0}).size() != 0)"),
        MethodDef(params=[ParamDef("x", TRUTHY)], returns=BOOL, cpp="tpy::__bool__({0})", is_readonly=True),
    ], methods={
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="tpy::__hash__({self})", is_readonly=True)],
    })

    # Char: Single character type
    module.register_type(CHAR, cpp_type="char", constructors=[
        MethodDef(params=[], returns=CHAR, cpp="'\\0'"),
        MethodDef(params=[ParamDef("x", INT32)], returns=CHAR, cpp="static_cast<char>({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=CHAR, cpp="static_cast<char>(({0}).to_fixed_check<int32_t>())"),
    ], methods={
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="tpy::__hash__({self})", is_readonly=True)],
    })

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

    # isinstance() - type checking for union type narrowing
    # Special handling in sema (validates union member, sets isinstance_var/isinstance_type)
    # and codegen (emits std::holds_alternative)
    module.function("isinstance", overloads=[], special_handling=True)

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

    # iter(x) -- calls x.__iter__(), returns Iterator[T]
    module.function("iter", overloads=[
        MethodDef(
            params=[ParamDef("x", NamedType("Iterable", (T,), is_protocol=True))],
            returns=NamedType("Iterator", (T,), is_protocol=True),
            cpp="{0}.__iter__()",
            is_readonly=True,
        ),
    ], type_params=["T"])

    return module
