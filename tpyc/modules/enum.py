"""
TurboPython enum module.

Provides Enum and IntEnum base classes for defining enumeration types.
Actual enum semantics are handled by sema and codegen -- this module
exists so that `--print-types` includes enum documentation.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import STRVIEW, BOOL

NAME = "enum"


def init_module() -> BuiltinModule:
    """Initialize and return the enum module."""
    module = BuiltinModule(NAME)

    # Enum -- base class for enumeration types.
    # Registered purely for documentation; actual analysis is in sema.
    module.type("Enum", cpp_type="/* enum class */",
        methods={
            "name": [MethodDef(params=[], returns=STRVIEW,
                               cpp="tpy::EnumUtil<{self_type}>::name({self})")],
        },
    )

    # IntEnum -- enum that also behaves as its underlying integer type.
    module.type("IntEnum", cpp_type="/* enum class */",
        extends=["Enum"],
        methods={
            "name": [MethodDef(params=[], returns=STRVIEW,
                               cpp="tpy::EnumUtil<{self_type}>::name({self})")],
        },
    )

    return module
