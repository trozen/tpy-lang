"""
Built-in module data classes and constant tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tpyc.typesys import TpyType

from tpyc.typesys import NominalType  # noqa: F401 -- forward ref in MethodDef.type_param_bounds


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: "TpyType"  # TpyType, use TypeParamRef("T") for type params
    requires_mutable_lvalue: bool = False


@dataclass
class MethodDef:
    """Definition of a function overload or type method."""
    params: list[ParamDef]
    returns: "TpyType"  # TpyType, use TypeParamRef("T") for type params
    cpp: str  # Template: "{0}" for args, "{self}" for receiver
    is_noalloc: bool = False
    is_readonly: bool = False
    is_pure: bool = False
    is_static: bool = False
    # Per-method type params and bounds (for constraining class-level type params)
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, "NominalType"] = field(default_factory=dict)


# C++ expression templates for Python dunder methods.
# Used when .py-defined protocols don't carry cpp templates.
DUNDER_CPP_TEMPLATES: dict[str, str] = {
    "__len__": "::tpy::__len__({self})",
    "__getitem__": "::tpy::__getitem__({self}, {0})",
    "__setitem__": "::tpy::__setitem__({self}, {0}, {1})",
    "__iter__": "::tpy::__iter__({self})",
    "__next__": "{self}.__next__()",
    "__contains__": "::tpy::__contains__({self}, {0})",
    "__delitem__": "::tpy::__delitem__({self}, {0})",
    "__bool__": "::tpy::__bool__({self})",
    "__str__": "::tpy::__str__({self})",
    "__repr__": "::tpy::__repr__({self})",
    "__hash__": "::tpy::__hash__({self})",
    "__deref__": "::tpy::deref_check({self})",
    "__span__": "::tpy::as_span({self})",
}


def get_dunder_cpp_template(method_name: str) -> str | None:
    """Get the C++ expression template for a dunder method, or None."""
    return DUNDER_CPP_TEMPLATES.get(method_name)


# Operator to method name mappings
BINOP_TO_METHOD = {
    "+": "__add__", "-": "__sub__", "*": "__mul__",
    "div": "__truediv__", "//": "__floordiv__", "%": "__mod__", "**": "__pow__",
    "<<": "__lshift__", ">>": "__rshift__",
    "&": "__and__", "|": "__or__", "^": "__xor__",
    "==": "__eq__", "!=": "__ne__",
    "<": "__lt__", "<=": "__le__", ">": "__gt__", ">=": "__ge__",
}

BINOP_TO_RMETHOD = {
    "+": "__radd__", "-": "__rsub__", "*": "__rmul__",
    "div": "__rtruediv__", "//": "__rfloordiv__", "%": "__rmod__", "**": "__rpow__",
    "<<": "__rlshift__", ">>": "__rrshift__",
    "&": "__rand__", "|": "__ror__", "^": "__rxor__",
}

AUGOP_TO_IMETHOD = {
    "+": "__iadd__", "-": "__isub__", "*": "__imul__",
    "div": "__itruediv__", "//": "__ifloordiv__", "%": "__imod__",
    "<<": "__ilshift__", ">>": "__irshift__",
    "|": "__ior__", "&": "__iand__", "^": "__ixor__",
}

UNARYOP_TO_METHOD = {
    "+": "__pos__",
    "-": "__neg__",
    "~": "__invert__",
}
