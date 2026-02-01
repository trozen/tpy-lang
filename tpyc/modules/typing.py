"""
TurboPython typing module.

Provides Protocol types matching Python's typing module.
"""

from tpyc.modules import BuiltinModule, MethodDef
from tpyc.typesys import INT32

module = BuiltinModule("typing")

# Sized protocol: types that support len()
# The cpp template is used for calling __len__ on protocol-typed variables
module.protocol("Sized",
    methods={"__len__": MethodDef(params=[], returns=INT32, cpp="tpy::__len__({self})")},
    cpp_concept="tpy::Sized",
)

# Protocol is recognized by the parser as the base class for user-defined protocols
# e.g., `class Measurable(Protocol):` generates a C++20 concept
