"""
TurboPython typing module.

Provides Protocol types matching Python's typing module.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import BOOL, INT32, VOID, SELF, TypeParamRef, NamedType, OptionalType

# Shorthand for type parameter T
T = TypeParamRef("T")

NAME = "typing"


def init_module() -> BuiltinModule:
    """Initialize and return the typing module."""
    module = BuiltinModule(NAME)

    # Sized is defined in lib/stdlib/typing.py (implicitly compiled).
    # No builtin definition needed.

    # Sequence[T] protocol: types that support len() and indexing
    # Generic protocol with type parameter T
    module.protocol("Sequence",
        type_params=["T"],
        methods={
            "__len__": MethodDef(params=[], returns=INT32, cpp="::tpy::__len__({self})"),
            "__getitem__": MethodDef(params=[ParamDef("index", INT32)], returns=T, cpp="::tpy::__getitem__({self}, {0})"),
        },
        cpp_concept="::tpy::Sequence",
        is_readonly=True,
    )

    # MutableSequence[T] protocol: types that support len(), read indexing, and write indexing
    # Used for semantic checking of subscript assignment (Span and str don't conform)
    module.protocol("MutableSequence",
        type_params=["T"],
        methods={
            "__len__": MethodDef(params=[], returns=INT32, cpp="::tpy::__len__({self})"),
            "__getitem__": MethodDef(params=[ParamDef("index", INT32)], returns=T, cpp="::tpy::__getitem__({self}, {0})"),
            "__setitem__": MethodDef(params=[ParamDef("index", INT32), ParamDef("value", T)], returns=VOID, cpp="::tpy::__setitem__({self}, {0}, {1})"),
        },
        cpp_concept="::tpy::MutableSequence",
    )

    # Iterator[T] -- Python-compatible iterator protocol
    # Conformance checked against __next__ (return T) on records.
    # All iterator types have __next__() via @error_return.
    module.protocol("Iterator",
        type_params=["T"],
        methods={
            "__next__": MethodDef(params=[], returns=T, cpp="{self}.__next__()"),
            "__iter__": MethodDef(params=[], returns=SELF, cpp="{self}.__iter__()"),
        },
        cpp_concept="::tpy::Iterator",
    )

    # Iterable[T] -- types with __iter__() returning an Iterator[T]
    module.protocol("Iterable",
        type_params=["T"],
        methods={
            "__iter__": MethodDef(params=[], returns=NamedType("Iterator", (T,), is_protocol=True),
                                  cpp="::tpy::__iter__({self})"),
        },
        cpp_concept="::tpy::Iterable",
    )

    # Protocol is recognized by the parser as the base class for user-defined protocols
    # e.g., `class Measurable(Protocol):` generates a C++20 concept

    return module
