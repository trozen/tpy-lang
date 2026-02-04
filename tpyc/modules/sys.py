"""
TurboPython sys module.

Provides system-specific parameters and functions matching Python's sys module.
"""

from tpyc.modules import BuiltinModule
from tpyc.typesys import STR, ListType

NAME = "sys"


def init_module() -> BuiltinModule:
    """Initialize and return the sys module."""
    module = BuiltinModule(NAME)

    # sys.argv - command line arguments
    # Accessed as tpy::sys_argv in C++ (a vector<string_view>)
    module.variable("argv", ListType(STR), "tpy::sys_argv")

    return module
