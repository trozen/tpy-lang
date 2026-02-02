"""
TurboPython typing module.

Provides Protocol types matching Python's typing module.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import INT32, VOID, TypeParamRef

# Shorthand for type parameter T
T = TypeParamRef("T")

module = BuiltinModule("typing")

# Sized protocol: types that support len()
# The cpp template is used for calling __len__ on protocol-typed variables
module.protocol("Sized",
    methods={"__len__": MethodDef(params=[], returns=INT32, cpp="tpy::__len__({self})")},
    cpp_concept="tpy::Sized",
)

# Sequence[T] protocol: types that support len() and indexing
# Generic protocol with type parameter T
module.protocol("Sequence",
    type_params=["T"],
    methods={
        "__len__": MethodDef(params=[], returns=INT32, cpp="tpy::__len__({self})"),
        "__getitem__": MethodDef(params=[ParamDef("index", INT32)], returns=T, cpp="{self}[{0}]"),
    },
    cpp_concept="tpy::Sequence",
)

# MutableSequence[T] protocol: types that support len(), read indexing, and write indexing
# Used for semantic checking of subscript assignment (Span and str don't conform)
module.protocol("MutableSequence",
    type_params=["T"],
    methods={
        "__len__": MethodDef(params=[], returns=INT32, cpp="tpy::__len__({self})"),
        "__getitem__": MethodDef(params=[ParamDef("index", INT32)], returns=T, cpp="{self}[{0}]"),
        "__setitem__": MethodDef(params=[ParamDef("index", INT32), ParamDef("value", T)], returns=VOID, cpp="{self}[{0}] = {1}"),
    },
    cpp_concept="tpy::MutableSequence",
)

# Protocol is recognized by the parser as the base class for user-defined protocols
# e.g., `class Measurable(Protocol):` generates a C++20 concept
