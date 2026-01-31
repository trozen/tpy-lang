"""
TurboPython sys module.

Provides system-specific parameters and functions matching Python's sys module.
"""

from tpyc.modules import BuiltinModule
from tpyc.typesys import STR, ListType

module = BuiltinModule("sys")

# sys.argv is registered as a module variable, not a function.
# It's accessed as tpy::sys_argv in C++ (a vector<string_view>).
# The module variable support is handled specially in the semantic analyzer.
MODULE_VARS = {
    "argv": ListType(STR),  # list[str]
}
