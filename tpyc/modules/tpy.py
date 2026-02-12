"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, StaticList, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import INT32, BIGINT, VOID, BOOL, SELF, ArrayType, SpanType, ModuleType, TypeParamRef, PtrType, NamedType, OwnType

# Shorthand for type parameter T
T = TypeParamRef("T")

NAME = "tpy"


def init_module() -> BuiltinModule:
    """Initialize and return the tpy module."""
    module = BuiltinModule(NAME)

    # Int32: 32-bit signed integer with checked arithmetic
    module.register_type(INT32, cpp_type="int32_t", constructors=[
        MethodDef(params=[], returns=INT32, cpp="0"),
        MethodDef(params=[ParamDef("x", INT32)], returns=INT32, cpp="{0}"),
        MethodDef(params=[ParamDef("x", BIGINT)], returns=INT32, cpp="{0}.to_int32()"),
    ], methods={
        "__int__": [MethodDef(params=[], returns=BIGINT, cpp="tpy::BigInt({self})")],
        **make_binop_methods({
            "__add__": ("tpy::int32_add({self}, {0})", INT32),
            "__sub__": ("tpy::int32_sub({self}, {0})", INT32),
            "__mul__": ("tpy::int32_mul({self}, {0})", INT32),
            "__floordiv__": ("tpy::int32_div({self}, {0})", INT32),
            "__mod__": ("tpy::int32_mod({self}, {0})", INT32),
            "__pow__": ("tpy::int32_pow({self}, {0})", INT32),
            "__lshift__": ("tpy::int32_lshift({self}, {0})", INT32),
            "__rshift__": ("tpy::int32_rshift({self}, {0})", INT32),
            "__and__": ("({self}) & ({0})", INT32),
            "__or__": ("({self}) | ({0})", INT32),
            "__xor__": ("({self}) ^ ({0})", INT32),
        }, self_type=INT32),
        "__neg__": [MethodDef(params=[], returns=INT32, cpp="tpy::int32_neg({self})")],
        "__invert__": [MethodDef(params=[], returns=INT32, cpp="~({self})")],
    }, extends=["Comparable"])

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

    # StaticList[T, N]: Fixed-capacity container
    # Python interface (append/pop/clear), C++ uses std::vector-like names (push_back/pop_back)
    # Fully module-defined via ModuleType - no hardcoded StaticListType class needed
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

    # Comparable protocol: types that support < operator
    # Used for bounded type parameters like T: Comparable
    module.protocol("Comparable",
        methods={
            "__lt__": MethodDef(params=[ParamDef("other", SELF)], returns=BOOL, cpp="{self} < {0}"),
        },
        cpp_concept="tpy::Comparable",
    )

    # NativeIterator[T] protocol: types that produce values lazily via next()
    # This is a marker protocol - types declare conformance via extends=["NativeIterator[T]"].
    # Maps to C++ next() -> std::optional<T> pattern.
    # Future: structural next() conformance checking.
    module.protocol("NativeIterator",
        type_params=["T"],
        methods={},  # Marker protocol for now
        cpp_concept="tpy::NativeIterator",
    )

    # NativeIterable[T] protocol: types that support C++ range-based for loops
    # This is a marker protocol - types declare conformance via extends=["NativeIterable[T]"].
    # Maps to C++ begin()/end() iteration pattern.
    # Future: Iterable[T] will use Python's __iter__() -> Iterator[T] protocol.
    module.protocol("NativeIterable",
        type_params=["T"],
        methods={},  # Marker protocol - conformance via extends declaration
        cpp_concept="tpy::NativeIterable",
    )

    # NativeContiguous[T] protocol: types with elements laid out contiguously in memory
    # This is a marker protocol - types declare conformance via extends=["NativeContiguous[T]"].
    # Types conforming to NativeContiguous[T] can be implicitly converted to Span[T].
    # Maps to C++ std::ranges::contiguous_range concept.
    module.protocol("NativeContiguous",
        type_params=["T"],
        methods={},  # Marker protocol - conformance via extends declaration
        cpp_concept="tpy::NativeContiguous",
    )

    # NativeRangeConstructible[T] protocol: types that can be constructed from a range
    # This is a marker protocol - types declare conformance via extends=["NativeRangeConstructible[T]"].
    # Used by codegen to generate tpy::from_range<Container>(range) calls.
    module.protocol("NativeRangeConstructible",
        type_params=["T"],
        methods={},  # Marker protocol - conformance via extends declaration
        cpp_concept="tpy::NativeRangeConstructible",
    )

    # copy() - explicit copy for ownership transfer
    # Truly generic: def copy[T](x: T) -> Own[T]
    # Requires explicit import: from tpy import copy
    # Special handling in sema (_analyze_tpy_copy) and codegen because:
    # - It's truly generic (works with any type including user records)
    # - The module system's overload matching can't handle T -> Own[T]
    module.function("copy", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    return module
