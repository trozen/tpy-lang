"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, StaticList, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, BIGINT, FLOAT, STR, STRING, STRVIEW, CHAR, VOID, BOOL, SELF,
    ALL_FIXED_INTS, FixedIntType,
    ArrayType, SpanType, ListType, TypeParamRef, PtrType, NamedType, OwnType, OptionalType,
)

# Shorthand for type parameter T
T = TypeParamRef("T")

NAME = "tpy"


def _register_fixed_int(module: BuiltinModule, typ: FixedIntType) -> None:
    """Register a fixed-width integer type with constructors, operators, and methods."""
    cpp_t = typ.to_cpp()  # e.g. "int32_t", "uint8_t"

    constructors = [
        MethodDef(params=[], returns=typ, cpp="0"),
        MethodDef(params=[ParamDef("x", typ)], returns=typ, cpp="{0}"),
    ]
    # Cross-type constructors from all other fixed-int types
    for other in ALL_FIXED_INTS:
        if other != typ:
            constructors.append(
                MethodDef(params=[ParamDef("x", other)], returns=typ,
                          cpp="tpy::int_cast_check<" + cpp_t + ">({0})")
            )
    constructors += [
        MethodDef(params=[ParamDef("x", BIGINT)], returns=typ, cpp="({0}).to_fixed_check<" + cpp_t + ">()"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=typ, cpp="tpy::from_float_check<" + cpp_t + ">({0})"),
        MethodDef(params=[ParamDef("x", STR)], returns=typ, cpp="tpy::from_str_check<" + cpp_t + ">({0})"),
        MethodDef(params=[ParamDef("x", BOOL)], returns=typ, cpp="static_cast<" + cpp_t + ">({0})"),
    ]

    # trunc: truncating (wrapping) conversion from any other fixed-int type or BigInt
    trunc_overloads = []
    for other in ALL_FIXED_INTS:
        if other != typ:
            trunc_overloads.append(
                MethodDef(params=[ParamDef("x", other)], returns=typ,
                          cpp=f"static_cast<{cpp_t}>({{0}})", is_static=True)
            )
    trunc_overloads.append(
        MethodDef(params=[ParamDef("x", BIGINT)], returns=typ,
                  cpp=f"({{0}}).to_fixed_trunc<{cpp_t}>()", is_static=True)
    )

    methods = {
        "trunc": trunc_overloads,
        "__int__": [MethodDef(params=[], returns=BIGINT, cpp="tpy::BigInt({self})")],
        **make_binop_methods({
            "__add__": (f"tpy::add_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__sub__": (f"tpy::sub_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__mul__": (f"tpy::mul_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__truediv__": (f"tpy::truediv(static_cast<double>({{self}}), static_cast<double>({{0}}))", FLOAT),
            "__floordiv__": (f"tpy::div_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__mod__": (f"tpy::mod_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__pow__": (f"tpy::pow_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__lshift__": (f"tpy::lshift_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__rshift__": (f"tpy::rshift_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__and__": (f"static_cast<{cpp_t}>({{self}} & {{0}})", typ),
            "__or__": (f"static_cast<{cpp_t}>({{self}} | {{0}})", typ),
            "__xor__": (f"static_cast<{cpp_t}>({{self}} ^ {{0}})", typ),
        }, self_type=typ),
        "__invert__": [MethodDef(params=[], returns=typ, cpp=f"static_cast<{cpp_t}>(~({{self}}))")],
    }

    # Negation only for signed types
    if typ.signed:
        methods["__neg__"] = [MethodDef(params=[], returns=typ, cpp=f"tpy::neg_check<{cpp_t}>({{self}})")]

    module.register_type(typ, cpp_type=cpp_t, constructors=constructors, methods=methods, extends=["Comparable"])


def init_module() -> BuiltinModule:
    """Initialize and return the tpy module."""
    module = BuiltinModule(NAME)

    # Register all fixed-width integer types
    for fixed_type in ALL_FIXED_INTS:
        _register_fixed_int(module, fixed_type)

    # Array[T, N]: Fixed-size array
    module.type("Array", cpp_type="std::array<{T}, {N}>", type_params=["T", "N"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.INT],
                type_factory=lambda t, n: ArrayType(t, n),
                extends=["NativeIterable[T]", "NativeContiguous[T]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True,
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
    })

    # Span[T]: Non-owning mutable view
    module.type("Span", cpp_type="std::span<{T}>", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: SpanType(t),
                extends=["NativeIterable[T]", "NativeContiguous[T]"],
                constructors=[
                    MethodDef(params=[ParamDef("ptr", PtrType(T)), ParamDef("length", INT32)],
                              returns=VOID,
                              cpp="std::span<{T}>({0}, static_cast<size_t>({1}))"),
                ],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True,
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
    })

    # ReadOnlySpan[T]: Non-owning read-only view
    module.type("ReadOnlySpan", cpp_type="std::span<const {T}>", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: SpanType(t, is_readonly=True),
                extends=["NativeIterable[T]", "NativeContiguous[T]"],
                constructors=[
                    MethodDef(params=[ParamDef("ptr", PtrType(T, is_readonly=True)), ParamDef("length", INT32)],
                              returns=VOID,
                              cpp="std::span<const {T}>({0}, static_cast<size_t>({1}))"),
                ],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True,
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
    })

    # Ptr[T]: Mutable pointer
    module.type("Ptr", cpp_type="{T}*", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: PtrType(t),
                extends=["Deref[T]"],
                constructors=[
                    MethodDef(params=[], returns=VOID, cpp="nullptr"),
                    MethodDef(params=[ParamDef("x", T, requires_mutable=True)], returns=T, cpp="&{0}"),
                ],
                methods={
                    "__deref__": [MethodDef(params=[], returns=T, cpp="tpy::deref_check({self})")],
                })

    # ReadOnlyPtr[T]: Read-only pointer
    module.type("ReadOnlyPtr", cpp_type="const {T}*", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: PtrType(t, is_readonly=True),
                extends=["Deref[T]"],
                constructors=[
                    MethodDef(params=[], returns=VOID, cpp="nullptr"),
                    MethodDef(params=[ParamDef("x", T, requires_lvalue=True)], returns=T, cpp="&{0}"),
                ],
                methods={
                    "__deref__": [MethodDef(params=[], returns=T, cpp="tpy::deref_check({self})")],
                })

    # StaticList[T, N]: Fixed-capacity container
    module.type("StaticList", cpp_type="StaticList<{T}, {N}>", type_params=["T", "N"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.INT],
                type_factory=lambda t, n: NamedType("StaticList", (t, n), _module_qname="tpy.StaticList"),
                extends=["NativeIterable[T]", "NativeContiguous[T]", "NativeRangeConstructible[T]"],
                constructors=[
                    MethodDef(params=[], returns=VOID, cpp=""),
                    MethodDef(params=[ParamDef("items", SpanType(T, is_readonly=True))], returns=VOID, cpp="{0}"),
                ],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="{self}.size()",
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
                cpp="{self}.pop_back()",
            ),
            MethodDef(
                params=[ParamDef("index", INT32)],
                returns=T,
                cpp="tpy::staticlist_pop_at({self}, {0})",
            ),
        ],
        "clear": [MethodDef(
            params=[],
            returns=VOID,
            cpp="{self}.clear()",
        )],
        "push_empty": [MethodDef(
            params=[],
            returns=PtrType(T),
            cpp="{self}.push_empty()",
        )],
        "get_mut": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=PtrType(T),
            cpp="tpy::get_mut({self}, {0})",
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
        "extend": [MethodDef(
            params=[ParamDef("other", NamedType("NativeIterable", (T,), is_protocol=True))],
            returns=VOID,
            cpp="tpy::staticlist_extend({self}, {0})",
        )],
        "insert": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="tpy::staticlist_insert({self}, {0}, {1})",
        )],
        "remove": [MethodDef(
            params=[ParamDef("value", T)],
            returns=VOID,
            cpp="tpy::staticlist_remove({self}, {0})",
        )],
        "index": [MethodDef(
            params=[ParamDef("value", T)],
            returns=INT32,
            cpp="tpy::staticlist_index({self}, {0})",
            is_readonly=True,
        )],
        "count": [MethodDef(
            params=[ParamDef("value", T)],
            returns=INT32,
            cpp="tpy::staticlist_count({self}, {0})",
            is_readonly=True,
        )],
        "reverse": [MethodDef(
            params=[],
            returns=VOID,
            cpp="tpy::staticlist_reverse({self})",
        )],
    })

    # Deref[T] protocol: types that can be dereferenced to yield T
    # Structural protocol -- any type with __deref__() -> T conforms automatically.
    # Ptr[T] and ReadOnlyPtr[T] explicitly extend this for clarity.
    module.protocol("Deref",
        type_params=["T"],
        methods={
            "__deref__": MethodDef(params=[], returns=T, cpp="{self}.__deref__()"),
        },
        cpp_concept="tpy::Deref",
    )

    # Truthy protocol: types that support bool() conversion via __bool__()
    module.protocol("Truthy",
        methods={"__bool__": MethodDef(params=[], returns=BOOL, cpp="tpy::__bool__({self})")},
        cpp_concept="tpy::Truthy",
        is_readonly=True,
    )

    # Stringable protocol: types that support str() conversion via __str__()
    module.protocol("Stringable",
        methods={"__str__": MethodDef(params=[], returns=STR, cpp="tpy::__str__({self})")},
        cpp_concept="tpy::Stringable",
        is_readonly=True,
    )

    # Representable protocol: types that support repr() conversion via __repr__()
    module.protocol("Representable",
        methods={"__repr__": MethodDef(params=[], returns=STR, cpp="tpy::__repr__({self})")},
        cpp_concept="tpy::Representable",
        is_readonly=True,
    )

    # Comparable protocol
    module.protocol("Comparable",
        methods={
            "__lt__": MethodDef(params=[ParamDef("other", SELF)], returns=BOOL, cpp="{self} < {0}"),
        },
        cpp_concept="tpy::Comparable",
    )

    # OptIterator[T] protocol
    module.protocol("OptIterator",
        type_params=["T"],
        methods={
            "__next_opt__": MethodDef(params=[], returns=OptionalType(T), cpp="{self}.__next_opt__()"),
        },
        cpp_concept="tpy::OptIterator",
    )

    # NativeIterable[T] protocol
    module.protocol("NativeIterable",
        type_params=["T"],
        methods={},
        cpp_concept="tpy::NativeIterable",
    )

    # NativeContiguous[T] protocol
    module.protocol("NativeContiguous",
        type_params=["T"],
        methods={},
        cpp_concept="tpy::NativeContiguous",
    )

    # NativeRangeConstructible[T] protocol
    module.protocol("NativeRangeConstructible",
        type_params=["T"],
        methods={},
        cpp_concept="tpy::NativeRangeConstructible",
    )

    # ValueType protocol -- marker for types with value semantics
    module.protocol("ValueType",
        type_params=[],
        methods={},
        cpp_concept="tpy::ValueType",
    )

    # Covariant[T] marker -- type parameter T is covariant, enabling
    # G[Child] -> G[Parent] coercion for @dynamic protocol hierarchies.
    module.protocol("Covariant",
        type_params=["T"],
        methods={},
        cpp_concept="tpy::Covariant",
    )

    # String: Explicit owned string type (std::string)
    module.register_type(STRING, cpp_type="std::string",
        extends=["NativeIterable[Char]"],
        constructors=[
            MethodDef(params=[], returns=STRING, cpp="std::string()"),
            MethodDef(params=[ParamDef("x", STR)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", STRING)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", STRVIEW)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STRING, cpp="std::string(tpy::bool_to_str({0}))"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STRING, cpp="std::string(tpy::char_to_str({0}))"),
            *[MethodDef(params=[ParamDef("x", t)], returns=STRING,
                       cpp=f"tpy::fixed_to_str<{t.to_cpp()}>({{0}})")
              for t in ALL_FIXED_INTS],
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STRING, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STRING, cpp="tpy::float_to_str({0})"),
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
    })

    # StrView: Explicit string view type (std::string_view)
    module.register_type(STRVIEW, cpp_type="std::string_view",
        extends=["NativeIterable[Char]"],
        constructors=[
            MethodDef(params=[], returns=STRVIEW, cpp='std::string_view()'),
            MethodDef(params=[ParamDef("x", STR)], returns=STRVIEW, cpp="std::string_view({0})"),
            MethodDef(params=[ParamDef("x", STRING)], returns=STRVIEW, cpp="std::string_view({0})"),
            MethodDef(params=[ParamDef("x", STRVIEW)], returns=STRVIEW, cpp="{0}"),
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
    })

    # copy() - explicit copy for ownership transfer
    module.function("copy", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # try_parse(EnumType, str) -> Optional[EnumType]
    module.function("try_parse", overloads=[], special_handling=True)

    return module
