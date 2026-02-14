"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, StaticList, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, BIGINT, FLOAT, STR, CHAR, VOID, BOOL, SELF,
    ALL_FIXED_INTS, FixedIntType,
    ArrayType, SpanType, ModuleType, TypeParamRef, PtrType, ConstPtrType, NamedType, OwnType, OptionalType,
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

    methods = {
        "__int__": [MethodDef(params=[], returns=BIGINT, cpp="tpy::BigInt({self})")],
        **make_binop_methods({
            "__add__": (f"tpy::add_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__sub__": (f"tpy::sub_check<{cpp_t}>({{self}}, {{0}})", typ),
            "__mul__": (f"tpy::mul_check<{cpp_t}>({{self}}, {{0}})", typ),
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
        )],
        "get": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
            is_readonly=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="{self}[{0}] = {1}",
        )],
        "unsafe_ptr": [MethodDef(
            params=[],
            returns=PtrType(T),
            cpp="{self}.data()",
        )],
    })

    # Span[T]: Non-owning read-only view
    module.type("Span", cpp_type="std::span<const {T}>", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: SpanType(t),
                extends=["NativeIterable[T]", "NativeContiguous[T]"],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="static_cast<int32_t>({self}.size())",
        )],
        "get": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
        )],
        "__getitem__": [MethodDef(
            params=[ParamDef("index", INT32)],
            returns=T,
            cpp="{self}[{0}]",
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
                    "unsafe_load": [MethodDef(
                        params=[ParamDef("index", INT32)],
                        returns=T,
                        cpp="{self}[{0}]",
                    )],
                    "unsafe_store": [MethodDef(
                        params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
                        returns=VOID,
                        cpp="{self}[{0}] = {1}",
                    )],
                })

    # ConstPtr[T]: Read-only pointer
    module.type("ConstPtr", cpp_type="const {T}*", type_params=["T"],
                param_kinds=[TypeParamKind.TYPE],
                type_factory=lambda t: ConstPtrType(t),
                extends=["Deref[T]"],
                constructors=[
                    MethodDef(params=[], returns=VOID, cpp="nullptr"),
                    MethodDef(params=[ParamDef("x", T, requires_lvalue=True)], returns=T, cpp="&{0}"),
                ],
                methods={
                    "__deref__": [MethodDef(params=[], returns=T, cpp="tpy::deref_check({self})")],
                    "unsafe_load": [MethodDef(
                        params=[ParamDef("index", INT32)],
                        returns=T,
                        cpp="{self}[{0}]",
                    )],
                })

    # StaticList[T, N]: Fixed-capacity container
    module.type("StaticList", cpp_type="StaticList<{T}, {N}>", type_params=["T", "N"],
                param_kinds=[TypeParamKind.TYPE, TypeParamKind.INT],
                type_factory=lambda t, n: ModuleType("tpy.StaticList", (t, n)),
                extends=["NativeIterable[T]", "NativeContiguous[T]", "NativeRangeConstructible[T]"],
                constructors=[
                    MethodDef(params=[], returns=VOID, cpp=""),
                    MethodDef(params=[ParamDef("items", SpanType(T))], returns=VOID, cpp="{0}"),
                ],
                methods={
        "__len__": [MethodDef(
            params=[],
            returns=INT32,
            cpp="{self}.size()",
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
            cpp="tpy::get_item({self}, {0})",
            is_readonly=True,
        )],
        "__setitem__": [MethodDef(
            params=[ParamDef("index", INT32), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="tpy::set_item({self}, {0}, {1})",
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
        )],
        "count": [MethodDef(
            params=[ParamDef("value", T)],
            returns=INT32,
            cpp="tpy::staticlist_count({self}, {0})",
        )],
        "reverse": [MethodDef(
            params=[],
            returns=VOID,
            cpp="tpy::staticlist_reverse({self})",
        )],
    })

    # Deref[T] protocol: types that can be dereferenced to yield T
    # Structural protocol — any type with __deref__() -> T conforms automatically.
    # Ptr[T] and ConstPtr[T] explicitly extend this for clarity.
    module.protocol("Deref",
        type_params=["T"],
        methods={
            "__deref__": MethodDef(params=[], returns=T, cpp="{self}.__deref__()"),
        },
        cpp_concept="tpy::Deref",
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

    # copy() - explicit copy for ownership transfer
    module.function("copy", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # native_c_global() / native_global()
    module.function("native_c_global", overloads=[
        MethodDef(params=[ParamDef("name", STR)], returns=VOID, cpp=""),
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    module.function("native_global", overloads=[
        MethodDef(params=[ParamDef("name", STR)], returns=VOID, cpp=""),
        MethodDef(params=[], returns=VOID, cpp=""),
    ], special_handling=True)

    return module
