"""
TurboPython-specific types (tpy module).

Defines types like Array, Span, Int32, etc.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef, TypeParamKind
from tpyc.modules.helpers import make_binop_methods
from tpyc.typesys import (
    INT32, UINT64, BIGINT, FLOAT, FLOAT32, STR, STRING, STRVIEW, CHAR, VOID, BOOL, SELF,
    ALL_FIXED_INTS, FixedIntType,
    ListType, TypeParamRef, PtrType, NamedType, OwnType,
)

# Shorthand for type parameter T
T = TypeParamRef("T")

NAME = "tpy"


def init_module() -> BuiltinModule:
    """Initialize and return the tpy module."""
    module = BuiltinModule(NAME)

    module.function("copy", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # copy_iter() - explicit element-by-element copy acknowledgment for iterables.
    # Parallel to copy() for single elements. Suppresses the bulk copy warning
    # on extend(), list(), set() etc. Currently a sema-level no-op (same as copy);
    # will be replaced with CopyIter codegen in a future phase.
    module.function("copy_iter", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # try_parse(EnumType, str) -> Optional[EnumType]
    module.function("try_parse", overloads=[], special_handling=True)



    return module
