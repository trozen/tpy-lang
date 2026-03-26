"""
TurboPython-specific types (tpy module).

Special-handling functions that can't be expressed as .py stubs.
Most tpy definitions are in lib/tpy/tpy/.
"""

from tpyc.modules.defs import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import TypeParamRef, OwnType

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
    # Stub only (return type placeholder); real sema in _analyze_tpy_copy_iter
    # which returns CopyIterType(elem_type).
    module.function("copy_iter", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # own_iter() - consuming iteration: moves list into OwnIter.
    # Stub only (return type placeholder); real sema in _analyze_tpy_own_iter
    # which returns OwnIterType(elem_type).
    module.function("own_iter", overloads=[
        MethodDef(
            params=[ParamDef("x", T)],
            returns=OwnType(T),
            cpp="{0}",
        ),
    ], special_handling=True)

    # try_parse(EnumType, str) -> Optional[EnumType]
    module.function("try_parse", overloads=[], special_handling=True)



    return module
