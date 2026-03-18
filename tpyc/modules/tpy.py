"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, UINT64, BIGINT, FLOAT, FLOAT32, STR, STRING, STRVIEW, CHAR, VOID, BOOL, SELF,
    ALL_FIXED_INTS, FixedIntType,
    ArrayType, SpanType, SpanIterType, ListType, TypeParamRef, PtrType, NamedType, OwnType,
)

# Shorthand for type parameter T
T = TypeParamRef("T")
# Full element/pointee type (preserves readonly) for span() / __span__() return types
Tspan = TypeParamRef("Tspan")

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
                          cpp="::tpy::int_cast_check<" + cpp_t + ">({0})")
            )
    constructors += [
        MethodDef(params=[ParamDef("x", BIGINT)], returns=typ, cpp="({0}).to_fixed_check<" + cpp_t + ">()"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=typ, cpp="::tpy::from_float_check<" + cpp_t + ">({0})"),
        MethodDef(params=[ParamDef("x", FLOAT32)], returns=typ, cpp="::tpy::from_float_check<" + cpp_t + ">(static_cast<double>({0}))"),
        MethodDef(params=[ParamDef("x", STR)], returns=typ, cpp="::tpy::from_str_check<" + cpp_t + ">({0})"),
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
        "__int__": [MethodDef(params=[], returns=BIGINT, cpp="::tpy::BigInt({self})")],
        **make_binop_methods({
            "__add__": (f"::tpy::add_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__sub__": (f"::tpy::sub_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__mul__": (f"::tpy::mul_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__truediv__": (f"::tpy::truediv(static_cast<double>({{self}}), static_cast<double>({{0}}))", FLOAT),
            "__floordiv__": (f"::tpy::div_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__mod__": (f"::tpy::mod_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__pow__": (f"::tpy::pow_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__lshift__": (f"::tpy::lshift_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__rshift__": (f"::tpy::rshift_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__and__": (f"static_cast<{cpp_t}>({{self}} & {{0}})", typ),
            "__or__": (f"static_cast<{cpp_t}>({{self}} | {{0}})", typ),
            "__xor__": (f"static_cast<{cpp_t}>({{self}} ^ {{0}})", typ),
        }, self_type=typ),
        "__invert__": [MethodDef(params=[], returns=typ, cpp=f"static_cast<{cpp_t}>(~({{self}}))")],
    }

    # Unary plus (identity) for all, negation only for signed types
    methods["__pos__"] = [MethodDef(params=[], returns=typ, cpp=f"+{{self}}")]
    if typ.signed:
        methods["__neg__"] = [MethodDef(params=[], returns=typ, cpp=f"::tpy::neg_check<{cpp_t}>({{self}})")]

    methods["__hash__"] = [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)]
    methods["__lt__"] = [MethodDef(params=[ParamDef("other", typ)], returns=BOOL, cpp="{self} < {0}", is_readonly=True, is_pure=True)]

    module.register_type(typ, cpp_type=cpp_t, constructors=constructors, methods=methods, extends=["Comparable", "Equatable"])


def init_module() -> BuiltinModule:
    """Initialize and return the tpy module."""
    module = BuiltinModule(NAME)

    # Register all fixed-width integer types
    for fixed_type in ALL_FIXED_INTS:
        _register_fixed_int(module, fixed_type)

    # Float32: 32-bit IEEE 754 single precision floating point
    _f32_constructors = [
        MethodDef(params=[], returns=FLOAT32, cpp="0.0f"),
        MethodDef(params=[ParamDef("x", FLOAT32)], returns=FLOAT32, cpp="{0}"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=FLOAT32, cpp="static_cast<float>({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=FLOAT32, cpp="static_cast<float>({0})"),
        MethodDef(params=[ParamDef("x", BOOL)], returns=FLOAT32, cpp="static_cast<float>({0})"),
        MethodDef(params=[ParamDef("x", STR)], returns=FLOAT32, cpp="::tpy::float32_from_str({0})"),
    ]
    for _fit in ALL_FIXED_INTS:
        _f32_constructors.append(
            MethodDef(params=[ParamDef("x", _fit)], returns=FLOAT32,
                      cpp="static_cast<float>({0})")
        )
    module.register_type(FLOAT32, cpp_type="float", constructors=_f32_constructors, methods={
        # Float32 + Float32 -> Float32
        "__add__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="({self}) + ({0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="static_cast<double>({self}) + ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="({self}) + static_cast<float>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="({self}) + static_cast<float>({0})"),
        ],
        "__sub__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="({self}) - ({0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="static_cast<double>({self}) - ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="({self}) - static_cast<float>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="({self}) - static_cast<float>({0})"),
        ],
        "__mul__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="({self}) * ({0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="static_cast<double>({self}) * ({0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="({self}) * static_cast<float>({0})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="({self}) * static_cast<float>({0})"),
        ],
        "__truediv__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="::tpy::truediv_f32({self}, {0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="::tpy::truediv(static_cast<double>({self}), {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="::tpy::truediv_f32({self}, static_cast<float>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="::tpy::truediv_f32({self}, static_cast<float>({0}))"),
        ],
        "__floordiv__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="::tpy::floordiv_f32({self}, {0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="::tpy::floordiv(static_cast<double>({self}), {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="::tpy::floordiv_f32({self}, static_cast<float>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="::tpy::floordiv_f32({self}, static_cast<float>({0}))"),
        ],
        "__mod__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="::tpy::fmod_f32({self}, {0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="::tpy::fmod(static_cast<double>({self}), {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="::tpy::fmod_f32({self}, static_cast<float>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="::tpy::fmod_f32({self}, static_cast<float>({0}))"),
        ],
        "__pow__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="std::pow({self}, {0})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::pow(static_cast<double>({self}), {0})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="std::pow({self}, static_cast<float>({0}))"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="std::pow({self}, static_cast<float>({0}))"),
        ],
        "__pos__": [MethodDef(params=[], returns=FLOAT32, cpp="+({self})")],
        "__neg__": [MethodDef(params=[], returns=FLOAT32, cpp="-({self})")],
        # Reverse operators (for int + Float32 -> Float32, float + Float32 -> float)
        "__radd__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="({0}) + ({self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) + static_cast<double>({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="static_cast<float>({0}) + ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="static_cast<float>({0}) + ({self})"),
        ],
        "__rsub__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="({0}) - ({self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) - static_cast<double>({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="static_cast<float>({0}) - ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="static_cast<float>({0}) - ({self})"),
        ],
        "__rmul__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="({0}) * ({self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="({0}) * static_cast<double>({self})"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="static_cast<float>({0}) * ({self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="static_cast<float>({0}) * ({self})"),
        ],
        "__rtruediv__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="::tpy::truediv_f32({0}, {self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="::tpy::truediv({0}, static_cast<double>({self}))"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="::tpy::truediv_f32(static_cast<float>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="::tpy::truediv_f32(static_cast<float>({0}), {self})"),
        ],
        "__rfloordiv__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="::tpy::floordiv_f32({0}, {self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="::tpy::floordiv({0}, static_cast<double>({self}))"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="::tpy::floordiv_f32(static_cast<float>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="::tpy::floordiv_f32(static_cast<float>({0}), {self})"),
        ],
        "__rmod__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="::tpy::fmod_f32({0}, {self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="::tpy::fmod({0}, static_cast<double>({self}))"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="::tpy::fmod_f32(static_cast<float>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="::tpy::fmod_f32(static_cast<float>({0}), {self})"),
        ],
        "__rpow__": [
            MethodDef(params=[ParamDef("other", FLOAT32)], returns=FLOAT32, cpp="std::pow({0}, {self})"),
            MethodDef(params=[ParamDef("other", FLOAT)], returns=FLOAT, cpp="std::pow({0}, static_cast<double>({self}))"),
            MethodDef(params=[ParamDef("other", BIGINT)], returns=FLOAT32, cpp="std::pow(static_cast<float>({0}), {self})"),
            MethodDef(params=[ParamDef("other", INT32)], returns=FLOAT32, cpp="std::pow(static_cast<float>({0}), {self})"),
        ],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)],
        "__lt__": [MethodDef(params=[ParamDef("other", FLOAT32)], returns=BOOL, cpp="{self} < {0}", is_readonly=True, is_pure=True)],
    }, extends=["Comparable", "Equatable"])

    # Array[T, N]: Fixed-size array
    module.type("Array", cpp_type="std::array<{T}, {N}>", type_params=["T", "N"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.INT],
                type_factory=lambda t, n: ArrayType(t, n),
                extends=["NativeIterable[T]", "ReadOnlySpanLike[T]", "Iterable[T]"],
                methods={
        "__iter__": [MethodDef(
            params=[],
            returns=NamedType("Iterator", (T,), is_protocol=True),
            cpp="::tpy::__iter__({self})",
            is_readonly=True, is_pure=True,
        )],
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True, is_pure=True,
        )],
        "unchecked_get": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
            is_readonly=True, is_pure=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="::tpy::__getitem__({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="::tpy::__setitem__({self}, {0}, {1})",
        )],
        "__span__": [MethodDef(
            params=[], returns=SpanType(T, is_readonly=True),
            cpp="::tpy::as_span({self})", is_readonly=True, is_pure=True,
        )],
    })

    # Span[T]: Non-owning mutable view
    module.type("Span", cpp_type="std::span<{T}>", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: SpanType(t),
                extends=["NativeIterable[T]", "ReadOnlySpanLike[T]", "Iterable[T]"],
                constructors=[
                    MethodDef(params=[ParamDef("ptr", PtrType(T)), ParamDef("length", INT32)],
                              returns=VOID,
                              cpp="{cpp}({0}, static_cast<size_t>({1}))"),
                    MethodDef(params=[ParamDef("source", SpanType(T))],
                              returns=VOID,
                              cpp="{cpp}({0})"),
                ],
                methods={
        "__iter__": [MethodDef(
            params=[],
            returns=NamedType("Iterator", (T,), is_protocol=True),
            cpp="::tpy::__iter__({self})",
            is_readonly=True, is_pure=True,
        )],
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
            is_readonly=True, is_pure=True,
        )],
        "unchecked_get": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
            is_readonly=True, is_pure=True,
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="::tpy::__getitem__({self}, {0})",
            is_readonly=True, is_pure=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="::tpy::__setitem__({self}, {0}, {1})",
        )],
        "__span__": [MethodDef(
            params=[], returns=SpanType(T, is_readonly=True),
            cpp="::tpy::as_span({self})", is_readonly=True, is_pure=True,
        )],
        "sort": [MethodDef(
            params=[],
            returns=VOID,
            cpp="std::stable_sort({self}.begin(), {self}.end())",  # stable to match Python
            type_params=["T"],
            type_param_bounds={"T": NamedType("Comparable", is_protocol=True)},
        )],
    })

    # SpanIter[T]: Lightweight iterator over a contiguous span
    module.type("SpanIter", cpp_type="::tpy::SpanIter<{T}>", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: SpanIterType(t),
                extends=["NativeIterable[T]", "Iterable[T]", "Iterator[T]"],
                is_nocopy=True,
                constructors=[
                    MethodDef(params=[ParamDef("source", SpanType(T))],
                              returns=VOID,
                              cpp="{cpp}({0})"),
                ],
                methods={
        "__iter__": [MethodDef(
            params=[], returns=SELF,
            cpp="{self}.__iter__()",
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
                    "__deref__": [MethodDef(params=[], returns=T, cpp="::tpy::deref_check({self})")],
                    "span": [MethodDef(params=[ParamDef("length", INT32)], returns=SpanType(Tspan),
                                       cpp="std::span({self}, static_cast<size_t>({0}))",
                                       is_readonly=True, is_pure=True)],
                })

    # String: Explicit owned string type (std::string)
    module.register_type(STRING, cpp_type="std::string",
        extends=["NativeIterable[Char]", "Iterable[Char]", "Equatable"],
        constructors=[
            MethodDef(params=[], returns=STRING, cpp="std::string()"),
            MethodDef(params=[ParamDef("x", STR)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", STRING)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", STRVIEW)], returns=STRING, cpp="std::string({0})"),
            MethodDef(params=[ParamDef("x", BOOL)], returns=STRING, cpp="std::string(::tpy::bool_to_str({0}))"),
            MethodDef(params=[ParamDef("x", CHAR)], returns=STRING, cpp="std::string(::tpy::char_to_str({0}))"),
            *[MethodDef(params=[ParamDef("x", t)], returns=STRING,
                       cpp=f"::tpy::fixed_to_str<{t.to_cpp()}>({{0}})")
              for t in ALL_FIXED_INTS],
            MethodDef(params=[ParamDef("x", BIGINT)], returns=STRING, cpp="({0}).to_string()"),
            MethodDef(params=[ParamDef("x", FLOAT)], returns=STRING, cpp="::tpy::float_to_str({0})"),
            MethodDef(params=[ParamDef("x", FLOAT32)], returns=STRING, cpp="::tpy::float_to_str(static_cast<double>({0}))"),
        ],
        methods={
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
                returns=ListType(STR),
                cpp="::tpy::str_split_whitespace({self})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR)],
                returns=ListType(STR),
                cpp="::tpy::str_split({self}, {0})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR), ParamDef("maxsplit", INT32)],
                returns=ListType(STR),
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
            params=[], returns=ListType(STR),
            cpp="::tpy::str_splitlines({self})", is_readonly=True, is_pure=True,
        )],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)],
    })

    # StrView: Explicit string view type (std::string_view)
    module.register_type(STRVIEW, cpp_type="std::string_view",
        extends=["NativeIterable[Char]", "Iterable[Char]", "Equatable"],
        constructors=[
            MethodDef(params=[], returns=STRVIEW, cpp='std::string_view()'),
            MethodDef(params=[ParamDef("x", STR)], returns=STRVIEW, cpp="std::string_view({0})"),
            MethodDef(params=[ParamDef("x", STRING)], returns=STRVIEW, cpp="std::string_view({0})"),
            MethodDef(params=[ParamDef("x", STRVIEW)], returns=STRVIEW, cpp="{0}"),
        ],
        methods={
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
                returns=ListType(STR),
                cpp="::tpy::str_split_whitespace({self})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR)],
                returns=ListType(STR),
                cpp="::tpy::str_split({self}, {0})",
                is_readonly=True, is_pure=True,
            ),
            MethodDef(
                params=[ParamDef("sep", STR), ParamDef("maxsplit", INT32)],
                returns=ListType(STR),
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
            params=[], returns=ListType(STR),
            cpp="::tpy::str_splitlines({self})", is_readonly=True, is_pure=True,
        )],
        "__hash__": [MethodDef(params=[], returns=UINT64, cpp="::tpy::__hash__({self})", is_readonly=True, is_pure=True)],
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

    # span(x) -- get ReadOnlySpan[T] from a ReadOnlySpanLike[T]
    module.function("span", overloads=[
        MethodDef(
            params=[ParamDef("x", NamedType("ReadOnlySpanLike", (T,), is_protocol=True))],
            returns=SpanType(T, is_readonly=True),
            cpp="::tpy::as_span({0})",
            is_readonly=True, is_pure=True,
        ),
    ], type_params=["T"])

    # deref(x) -- dereference a Deref[T] to get T
    module.function("deref", overloads=[
        MethodDef(
            params=[ParamDef("x", NamedType("Deref", (T,), is_protocol=True))],
            returns=T,
            cpp="::tpy::deref_check({0})",
            is_readonly=True, is_pure=True,
        ),
    ], type_params=["T"])

    # make_default() -- portable default construction for generic T
    module.function("make_default", overloads=[
        MethodDef(params=[], returns=OwnType(T), cpp="{T}{{}}"),
    ], type_params=["T"],
       type_param_bounds={"T": NamedType("Default", (), is_protocol=True)})

    # Float64: alias for float (double precision) with its own constructor overloads.
    from tpyc.modules import BuiltinTypeDef
    _f64_constructors = [
        MethodDef(params=[], returns=FLOAT, cpp="0.0"),
        MethodDef(params=[ParamDef("x", FLOAT)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", FLOAT32)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", BOOL)], returns=FLOAT, cpp="static_cast<double>({0})"),
        MethodDef(params=[ParamDef("x", STR)], returns=FLOAT, cpp="::tpy::float_from_str({0})"),
    ]
    for _fit in ALL_FIXED_INTS:
        _f64_constructors.append(
            MethodDef(params=[ParamDef("x", _fit)], returns=FLOAT,
                      cpp=f"static_cast<double>({{0}})")
        )
    module.types["tpy.Float64"] = BuiltinTypeDef(
        type_obj=FLOAT,
        cpp_type="double",
        constructors=_f64_constructors,
        methods={},
        extends=["Comparable", "Equatable"],
    )

    return module
