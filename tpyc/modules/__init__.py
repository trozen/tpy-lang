"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tpyc.typesys import TpyType


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: TpyType


@dataclass
class MethodDef:
    """Definition of a function overload or type method."""
    params: list[ParamDef]
    returns: TpyType
    cpp: str  # Template: "{0}" for args, "{self}" for receiver


@dataclass
class BuiltinFunctionDef:
    """Definition of a built-in function with its overloads."""
    name: str
    overloads: list[MethodDef]


@dataclass
class BuiltinTypeDef:
    """Definition of a built-in type with its methods."""
    qualified_name: str
    cpp_type: str
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)


class BuiltinModule:
    """A module containing built-in functions and types."""

    def __init__(self, name: str):
        self.name = name
        self.functions: dict[str, BuiltinFunctionDef] = {}
        self.types: dict[str, BuiltinTypeDef] = {}  # keyed by qualified name

    def function(self, name: str, overloads: list[MethodDef]):
        """Register a built-in function."""
        self.functions[name] = BuiltinFunctionDef(name=name, overloads=overloads)

    def type(self, name: str, cpp_type: str, methods: dict[str, list[MethodDef]] | None = None):
        """Register a built-in type. Stored with qualified name (module.name)."""
        qualified_name = f"{self.name}.{name}"
        self.types[qualified_name] = BuiltinTypeDef(
            qualified_name=qualified_name,
            cpp_type=cpp_type,
            methods=methods or {},
        )


# Module instances (lazy-loaded)
_builtins: BuiltinModule | None = None
_tpy: BuiltinModule | None = None


def get_builtins() -> BuiltinModule:
    """Get the builtins module, loading it on first access."""
    global _builtins
    if _builtins is None:
        from tpyc.modules import builtins as builtins_module
        _builtins = builtins_module.module
    assert _builtins is not None
    return _builtins


def get_tpy() -> BuiltinModule:
    """Get the tpy module, loading it on first access."""
    global _tpy
    if _tpy is None:
        from tpyc.modules import tpy as tpy_module
        _tpy = tpy_module.module
    assert _tpy is not None
    return _tpy


def _all_modules() -> list[BuiltinModule]:
    """Get all loaded modules."""
    return [get_builtins(), get_tpy()]


def lookup_function(name: str) -> BuiltinFunctionDef | None:
    """Lookup a function by name in the built-in modules."""
    for module in _all_modules():
        if fn := module.functions.get(name):
            return fn
    return None


def lookup_type(qualified_name: str) -> BuiltinTypeDef | None:
    """Lookup a type by qualified name (e.g., 'tpy.Array', 'builtins.list')."""
    for module in _all_modules():
        if typ := module.types.get(qualified_name):
            return typ
    return None


def lookup_type_method(qualified_type_name: str, method_name: str) -> list[MethodDef] | None:
    """Lookup a method on a built-in type by qualified type name."""
    if typ := lookup_type(qualified_type_name):
        return typ.methods.get(method_name)
    return None


# Operator to method name mappings
BINOP_TO_METHOD = {
    "+": "__add__", "-": "__sub__", "*": "__mul__",
    "//": "__floordiv__", "%": "__mod__", "**": "__pow__",
    "<<": "__lshift__", ">>": "__rshift__",
    "&": "__and__", "|": "__or__", "^": "__xor__",
}

BINOP_TO_RMETHOD = {
    "+": "__radd__", "-": "__rsub__", "*": "__rmul__",
    "//": "__rfloordiv__", "%": "__rmod__", "**": "__rpow__",
    "<<": "__rlshift__", ">>": "__rrshift__",
    "&": "__rand__", "|": "__ror__", "^": "__rxor__",
}

UNARYOP_TO_METHOD = {
    "-": "__neg__",
    "~": "__invert__",
}


@dataclass
class BinopResult:
    """Result of binary operator lookup."""
    method: MethodDef
    left_wrapper: str   # cpp template for left, e.g., "tpy::BigInt({expr})" or "{expr}"
    right_wrapper: str  # cpp template for right
    is_reverse: bool = False  # True if using reverse operator (swap {self} and {0})
    receiver_type: "TpyType | None" = None  # Type of the receiver ({self})


@dataclass
class UnaryopResult:
    """Result of unary operator lookup."""
    method: MethodDef


def _get_effective_qname(tpy_type: "TpyType") -> str | None:
    """Get qualified name for method lookup, treating IntLiteralType as builtins.int."""
    from tpyc.typesys import IntLiteralType

    if isinstance(tpy_type, IntLiteralType):
        return "builtins.int"
    return tpy_type.qualified_name()


def _get_type_for_qname(qname: str) -> "TpyType | None":
    """Get the TpyType instance for a qualified name."""
    from tpyc.typesys import BIGINT, INT32
    if qname == "builtins.int":
        return BIGINT
    if qname == "tpy.Int32":
        return INT32
    return None


def _type_matches_param(arg_type: "TpyType", param_type: "TpyType") -> bool:
    """Check if an argument type matches a parameter type."""
    # Import here to avoid circular imports
    from tpyc.typesys import IntLiteralType, Int32Type, BigIntType

    if arg_type == param_type:
        return True
    # IntLiteralType can match Int32 or BigInt
    if isinstance(arg_type, IntLiteralType):
        return isinstance(param_type, (Int32Type, BigIntType))
    return False


def _find_matching_overload(methods: list[MethodDef] | None, arg_type: "TpyType") -> MethodDef | None:
    """Find an overload that accepts the given argument type."""
    if not methods:
        return None
    for method in methods:
        if len(method.params) == 1 and _type_matches_param(arg_type, method.params[0].type):
            return method
    return None


def lookup_binop(left_type: "TpyType", op: str, right_type: "TpyType") -> BinopResult | None:
    """
    Lookup binary operator implementation.

    Tries in order:
    1. left.__add__(right) - direct match
    2. If left has __int__ returning right's type, promote left and use right's __add__
    3. right.__radd__(left) - reverse operator
    4. If right has __int__ returning left's type, promote right and use left's __add__

    Returns BinopResult with method and wrapper templates, or None if not found.
    """
    method_name = BINOP_TO_METHOD.get(op)
    rmethod_name = BINOP_TO_RMETHOD.get(op)
    if not method_name:
        return None

    left_qname = _get_effective_qname(left_type)
    right_qname = _get_effective_qname(right_type)

    # 1. Try direct: left.__add__(right)
    if left_qname:
        methods = lookup_type_method(left_qname, method_name)
        if method := _find_matching_overload(methods, right_type):
            receiver = _get_type_for_qname(left_qname)
            return BinopResult(method=method, left_wrapper="{expr}", right_wrapper="{expr}",
                               receiver_type=receiver)

    # 2. Try promoting left to right's type via __int__
    if left_qname and right_qname:
        int_methods = lookup_type_method(left_qname, "__int__")
        if int_methods:
            promoted_type = int_methods[0].returns
            if promoted_type.qualified_name() == right_qname:
                # left can promote to right's type
                methods = lookup_type_method(right_qname, method_name)
                if method := _find_matching_overload(methods, right_type):
                    receiver = _get_type_for_qname(right_qname)
                    return BinopResult(
                        method=method,
                        left_wrapper=int_methods[0].cpp,  # e.g., "tpy::BigInt({self})"
                        right_wrapper="{expr}",
                        receiver_type=receiver,
                    )

    # 3. Try reverse: right.__radd__(left)
    if right_qname and rmethod_name:
        methods = lookup_type_method(right_qname, rmethod_name)
        if method := _find_matching_overload(methods, left_type):
            receiver = _get_type_for_qname(right_qname)
            return BinopResult(method=method, left_wrapper="{expr}", right_wrapper="{expr}",
                               is_reverse=True, receiver_type=receiver)

    # 4. Try promoting right to left's type via __int__, then use left's operator
    if right_qname and left_qname:
        int_methods = lookup_type_method(right_qname, "__int__")
        if int_methods:
            promoted_type = int_methods[0].returns
            if promoted_type.qualified_name() == left_qname:
                # right can promote to left's type
                methods = lookup_type_method(left_qname, method_name)
                if method := _find_matching_overload(methods, promoted_type):
                    receiver = _get_type_for_qname(left_qname)
                    return BinopResult(
                        method=method,
                        left_wrapper="{expr}",
                        right_wrapper=int_methods[0].cpp,  # e.g., "tpy::BigInt({self})"
                        receiver_type=receiver,
                    )

    return None


def lookup_unaryop(operand_type: "TpyType", op: str) -> UnaryopResult | None:
    """Lookup unary operator implementation."""
    method_name = UNARYOP_TO_METHOD.get(op)
    if not method_name:
        return None

    qname = operand_type.qualified_name()
    if not qname:
        return None

    methods = lookup_type_method(qname, method_name)
    if methods and len(methods) > 0 and len(methods[0].params) == 0:
        return UnaryopResult(method=methods[0])

    return None
