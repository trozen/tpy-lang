"""
TurboPython unsafe pointer operations (tpy.unsafe module).

Unsafe pointer operations that require explicit import.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import (
    STR, STRVIEW, CHAR, VOID, UINT32, INT64,
    ArrayType, ListType, TypeParamRef, PtrType, ConstPtrType, OwnType,
)

T = TypeParamRef("T")

NAME = "tpy.unsafe"


def init_module() -> BuiltinModule:
    module = BuiltinModule(NAME)

    # unsafe_ptr: get a raw pointer from a container or string
    module.function("unsafe_ptr", type_params=["T"], overloads=[
        # str -> ConstPtr[Char] (non-generic; str is string_view, need .data())
        MethodDef(
            params=[ParamDef("s", STR)],
            returns=ConstPtrType(CHAR),
            cpp="{0}.data()",
        ),
        # Array[T, N] -> Ptr[T]
        MethodDef(
            params=[ParamDef("a", ArrayType(T, TypeParamRef("N")))],
            returns=PtrType(T),
            cpp="{0}.data()",
        ),
        # list[T] -> Ptr[T]
        MethodDef(
            params=[ParamDef("l", ListType(T))],
            returns=PtrType(T),
            cpp="{0}.data()",
        ),
    ])

    # unsafe_load: read a value through a pointer at offset
    module.function("unsafe_load", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p", PtrType(T)), ParamDef("offset", UINT32)],
            returns=T,
            cpp="{0}[{1}]",
        ),
        MethodDef(
            params=[ParamDef("p", ConstPtrType(T)), ParamDef("offset", UINT32)],
            returns=T,
            cpp="{0}[{1}]",
        ),
    ])

    # unsafe_store: write a value through a pointer at offset
    module.function("unsafe_store", type_params=["T"], overloads=[
        MethodDef(
            params=[
                ParamDef("p", PtrType(T)),
                ParamDef("offset", UINT32),
                ParamDef("value", T),
            ],
            returns=VOID,
            cpp="{0}[{1}] = {2}",
        ),
    ])

    # unsafe_copy_n: copy N elements between pointers
    module.function("unsafe_copy_n", type_params=["T"], overloads=[
        MethodDef(
            params=[
                ParamDef("dest", PtrType(T)),
                ParamDef("src", PtrType(T)),
                ParamDef("count", UINT32),
            ],
            returns=VOID,
            cpp="std::copy_n({1}, {2}, {0})",
        ),
        MethodDef(
            params=[
                ParamDef("dest", PtrType(T)),
                ParamDef("src", ConstPtrType(T)),
                ParamDef("count", UINT32),
            ],
            returns=VOID,
            cpp="std::copy_n({1}, {2}, {0})",
        ),
    ])

    # unsafe_const_cast: remove const from a pointer (ConstPtr[T] -> Ptr[T])
    module.function("unsafe_const_cast", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p", ConstPtrType(T))],
            returns=PtrType(T),
            cpp="const_cast<{T}*>({0})",
        ),
    ])

    # unsafe_ptr_add: advance a pointer by a signed element offset
    module.function("unsafe_ptr_add", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p", PtrType(T)), ParamDef("delta", INT64)],
            returns=PtrType(T),
            cpp="({0} + {1})",
        ),
        MethodDef(
            params=[ParamDef("p", ConstPtrType(T)), ParamDef("delta", INT64)],
            returns=ConstPtrType(T),
            cpp="({0} + {1})",
        ),
    ])

    # unsafe_ptr_diff: distance between two pointers in elements
    # Returns Int64 (maps to int64_t). In the future this could become a
    # configurable platform type alias (e.g. PtrDiffType) similar to how
    # default_int works for integer literals.
    module.function("unsafe_ptr_diff", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p1", PtrType(T)), ParamDef("p2", PtrType(T))],
            returns=INT64,
            cpp="static_cast<int64_t>({0} - {1})",
        ),
        MethodDef(
            params=[ParamDef("p1", ConstPtrType(T)), ParamDef("p2", ConstPtrType(T))],
            returns=INT64,
            cpp="static_cast<int64_t>({0} - {1})",
        ),
    ])

    # unsafe_cast: reinterpret a pointer as a different pointee type
    # T = target pointee type (explicit or inferred from context)
    # U = source pointee type (inferred from argument)
    U = TypeParamRef("U")
    module.function("unsafe_cast", type_params=["T", "U"], overloads=[
        # Ptr[U] -> Ptr[T]
        MethodDef(
            params=[ParamDef("p", PtrType(U))],
            returns=PtrType(T),
            cpp="reinterpret_cast<{T}*>({0})",
        ),
        # ConstPtr[U] -> ConstPtr[T]
        MethodDef(
            params=[ParamDef("p", ConstPtrType(U))],
            returns=ConstPtrType(T),
            cpp="reinterpret_cast<const {T}*>({0})",
        ),
    ])

    # unsafe_alloc: allocate raw memory for a single element of type T
    # T inferred from assignment target (Ptr[T]) or explicit type arg
    module.function("unsafe_alloc", type_params=["T"], overloads=[
        MethodDef(
            params=[],
            returns=PtrType(T),
            cpp="static_cast<{T}*>(::operator new(sizeof({T}), std::align_val_t(alignof({T}))))",
        ),
    ])

    # unsafe_alloc_n: allocate raw memory for N elements of type T
    module.function("unsafe_alloc_n", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("count", UINT32)],
            returns=PtrType(T),
            cpp="static_cast<{T}*>(::operator new(sizeof({T}) * {0}, std::align_val_t(alignof({T}))))",
        ),
    ])

    # unsafe_free: free raw memory allocated by unsafe_alloc/unsafe_alloc_n
    module.function("unsafe_free", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p", PtrType(T))],
            returns=VOID,
            cpp="::operator delete({0}, std::align_val_t(alignof({T})))",
        ),
    ])

    # unsafe_init: placement-new construct an object at a pointer location
    module.function("unsafe_init", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p", PtrType(T)), ParamDef("value", OwnType(T))],
            returns=VOID,
            cpp="::new(static_cast<void*>({0})) {T}(std::move({1}))",
        ),
    ])

    # unsafe_drop: call destructor on an object at a pointer location
    # Uses tpy::destroy_at which is a no-op for trivially destructible types
    module.function("unsafe_drop", type_params=["T"], overloads=[
        MethodDef(
            params=[ParamDef("p", PtrType(T))],
            returns=VOID,
            cpp="tpy::destroy_at({0})",
        ),
    ])

    # unsafe_str_view: create a StrView from a Ptr[Char] and length
    module.function("unsafe_str_view", overloads=[
        MethodDef(
            params=[ParamDef("p", PtrType(CHAR)), ParamDef("size", UINT32)],
            returns=STRVIEW,
            cpp="std::string_view({0}, {1})",
        ),
        MethodDef(
            params=[ParamDef("p", ConstPtrType(CHAR)), ParamDef("size", UINT32)],
            returns=STRVIEW,
            cpp="std::string_view({0}, {1})",
        ),
    ])

    return module
