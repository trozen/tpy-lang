"""
TurboPython memory primitives (tpy.mem module).

Low-level uninitialized storage types for building containers.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.typesys import (
    VOID, UINT32,
    NamedType, TypeParamRef, PtrType, OwnType,
)

T = TypeParamRef("T")

NAME = "tpy.mem"


def init_module() -> BuiltinModule:
    module = BuiltinModule(NAME)

    def _storage_methods() -> dict[str, list[MethodDef]]:
        return {
            # Indexed operations
            "init": [MethodDef(
                params=[ParamDef("index", UINT32), ParamDef("value", OwnType(T))],
                returns=VOID,
                cpp="{self}.init({0}, {1})",
            )],
            "drop": [MethodDef(
                params=[ParamDef("index", UINT32)],
                returns=VOID,
                cpp="{self}.drop({0})",
            )],
            "load": [MethodDef(
                params=[ParamDef("index", UINT32)],
                returns=T,
                cpp="{self}.load({0})",
                is_readonly=True,
            )],
            # Single-element (index 0) shortcuts
            "init0": [MethodDef(
                params=[ParamDef("value", OwnType(T))],
                returns=VOID,
                cpp="{self}.init0({0})",
            )],
            "drop0": [MethodDef(
                params=[],
                returns=VOID,
                cpp="{self}.drop0()",
            )],
            "load0": [MethodDef(
                params=[],
                returns=T,
                cpp="{self}.load0()",
                is_readonly=True,
            )],
            # Move out (load + drop in one operation)
            "take": [MethodDef(
                params=[ParamDef("index", UINT32)],
                returns=OwnType(T),
                cpp="{self}.take({0})",
            )],
            "take0": [MethodDef(
                params=[],
                returns=OwnType(T),
                cpp="{self}.take0()",
            )],
            # Raw pointer access
            "ptr": [MethodDef(
                params=[],
                returns=PtrType(T),
                cpp="{self}.ptr()",
                is_readonly=True,
            )],
        }

    # UninitArrayStorage[T, N]: inline uninitialized storage
    module.type("UninitArrayStorage",
        cpp_type="UninitArrayStorage<{T}, {N}>",
        type_params=["T", "N"],
        param_kinds=[TypeParamKind.TYPE, TypeParamKind.INT],
        type_factory=lambda t, n: NamedType("UninitArrayStorage", (t, n), _module_qname="tpy.mem.UninitArrayStorage"),
        constructors=[
            MethodDef(params=[], returns=VOID, cpp=""),
        ],
        methods=_storage_methods(),
    )

    # UninitHeapStorage[T]: heap-allocated uninitialized storage
    module.type("UninitHeapStorage",
        cpp_type="UninitHeapStorage<{T}>",
        type_params=["T"],
        param_kinds=[TypeParamKind.TYPE],
        type_factory=lambda t: NamedType("UninitHeapStorage", (t,), _module_qname="tpy.mem.UninitHeapStorage"),
        constructors=[
            MethodDef(
                params=[ParamDef("capacity", UINT32)],
                returns=VOID,
                cpp="{0}",
            ),
        ],
        methods=_storage_methods(),
    )

    return module
