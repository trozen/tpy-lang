"""
TurboPython built-in modules infrastructure.

Provides a registry for built-in functions and types that can be used
by the semantic analyzer and code generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Union

if TYPE_CHECKING:
    from tpyc.typesys import TpyType


class TypeParamKind(Enum):
    """Kind of type parameter in a generic type."""
    TYPE = "type"  # A type parameter like T
    INT = "int"    # An integer literal like N


# Type alias for method param/return types: concrete TpyType or string type param
# Strings like "T" refer to declared type_params, "Ptr[T]" creates PtrType(T)
TypeOrParam = Union["TpyType", str]


@dataclass
class ParamDef:
    """Parameter definition for a function/method."""
    name: str
    type: TypeOrParam  # TpyType or string like "T" for type param


@dataclass
class MethodDef:
    """Definition of a function overload or type method."""
    params: list[ParamDef]
    returns: TypeOrParam  # TpyType or string like "T", "Ptr[T]"
    cpp: str  # Template: "{0}" for args, "{self}" for receiver


@dataclass
class BuiltinFunctionDef:
    """Definition of a built-in function with its overloads."""
    name: str
    overloads: list[MethodDef]


@dataclass
class BuiltinTypeDef:
    """Definition of a built-in type with its methods."""
    type_obj: "TpyType | None"  # The type object (e.g., INT32), None for parameterized types
    cpp_type: str
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)
    constructors: list[MethodDef] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)  # ["T"], ["T", "N"], etc.
    param_kinds: list[TypeParamKind] = field(default_factory=list)  # Kind of each type param
    type_factory: "Callable[..., TpyType] | None" = None  # Factory to create TpyType from params


class BuiltinModule:
    """A module containing built-in functions and types."""

    def __init__(self, name: str):
        self.name = name
        self.functions: dict[str, BuiltinFunctionDef] = {}
        self.types: dict[str, BuiltinTypeDef] = {}  # keyed by qualified name

    def function(self, name: str, overloads: list[MethodDef]):
        """Register a built-in function."""
        self.functions[name] = BuiltinFunctionDef(name=name, overloads=overloads)

    def register_type(self, type_obj: "TpyType", cpp_type: str,
                      methods: dict[str, list[MethodDef]] | None = None,
                      constructors: list[MethodDef] | None = None):
        """Register a built-in type using its type object. Preferred for non-parameterized types."""
        qname = type_obj.qualified_name()
        assert qname is not None, f"Type {type_obj} has no qualified_name"
        self.types[qname] = BuiltinTypeDef(
            type_obj=type_obj,
            cpp_type=cpp_type,
            methods=methods or {},
            constructors=constructors or [],
        )

    def type(self, name: str, cpp_type: str,
             methods: dict[str, list[MethodDef]] | None = None,
             constructors: list[MethodDef] | None = None,
             type_params: list[str] | None = None,
             param_kinds: list[TypeParamKind] | None = None,
             type_factory: "Callable[..., TpyType] | None" = None):
        """Register a built-in type by name. Use for parameterized types (list, Array, etc.)."""
        type_params = type_params or []
        param_kinds = param_kinds or []

        # Validate parameterized type configuration
        if type_params:
            if not param_kinds:
                raise ValueError(f"Type '{name}' has type_params but no param_kinds")
            if not type_factory:
                raise ValueError(f"Type '{name}' has type_params but no type_factory")
            if len(param_kinds) != len(type_params):
                raise ValueError(
                    f"Type '{name}': param_kinds length ({len(param_kinds)}) "
                    f"!= type_params length ({len(type_params)})"
                )
        else:
            if param_kinds:
                raise ValueError(f"Type '{name}' has param_kinds but no type_params")
            if type_factory:
                raise ValueError(f"Type '{name}' has type_factory but no type_params")

        qualified_name = f"{self.name}.{name}"
        self.types[qualified_name] = BuiltinTypeDef(
            type_obj=None,  # Parameterized type, no single instance
            cpp_type=cpp_type,
            methods=methods or {},
            constructors=constructors or [],
            type_params=type_params,
            param_kinds=param_kinds,
            type_factory=type_factory,
        )


# Module instances (lazy-loaded)
_builtins: BuiltinModule | None = None
_tpy: BuiltinModule | None = None
_time: BuiltinModule | None = None
_sys: BuiltinModule | None = None
_math: BuiltinModule | None = None


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


def get_time() -> BuiltinModule:
    """Get the time module, loading it on first access."""
    global _time
    if _time is None:
        from tpyc.modules import time as time_module
        _time = time_module.module
    assert _time is not None
    return _time


def get_sys() -> BuiltinModule:
    """Get the sys module, loading it on first access."""
    global _sys
    if _sys is None:
        from tpyc.modules import sys as sys_module
        _sys = sys_module.module
    assert _sys is not None
    return _sys


def get_math() -> BuiltinModule:
    """Get the math module, loading it on first access."""
    global _math
    if _math is None:
        from tpyc.modules import math as math_module
        _math = math_module.module
    assert _math is not None
    return _math


def _all_modules() -> list[BuiltinModule]:
    """Get all loaded modules for default lookups (builtins + tpy only).

    Note: time module is excluded - it requires explicit import.
    """
    return [get_builtins(), get_tpy()]


def get_module(name: str) -> BuiltinModule | None:
    """Get a module by name."""
    if name == "builtins":
        return get_builtins()
    elif name == "tpy":
        return get_tpy()
    elif name == "time":
        return get_time()
    elif name == "sys":
        return get_sys()
    elif name == "math":
        return get_math()
    return None


def lookup_module_function(module_name: str, func_name: str) -> BuiltinFunctionDef | None:
    """Lookup a function in a specific module by name."""
    module = get_module(module_name)
    if module:
        return module.functions.get(func_name)
    return None


@dataclass
class ModuleVarDef:
    """Definition of a module-level variable."""
    name: str
    type: "TpyType"
    cpp: str  # C++ expression to access the variable


def lookup_module_var(module_name: str, var_name: str) -> ModuleVarDef | None:
    """Lookup a module variable by name.

    Module variables are defined in MODULE_VARS dict in each module file.
    Returns the variable definition with its type and C++ accessor.
    """
    # Module variables are defined in module files, not in BuiltinModule
    # We handle them specially here
    if module_name == "sys":
        from tpyc.modules import sys as sys_module
        if var_name in sys_module.MODULE_VARS:
            return ModuleVarDef(
                name=var_name,
                type=sys_module.MODULE_VARS[var_name],
                cpp=f"tpy::sys_{var_name}",  # tpy::sys_argv
            )
    return None


def lookup_function(name: str) -> BuiltinFunctionDef | None:
    """Lookup a function by name in the built-in modules."""
    for module in _all_modules():
        if fn := module.functions.get(name):
            return fn
    return None


def lookup_type(type_or_name: "TpyType | str") -> BuiltinTypeDef | None:
    """Lookup a type by type object or qualified name string."""
    if isinstance(type_or_name, str):
        qname = type_or_name
    else:
        qname = type_or_name.qualified_name()
        if qname is None:
            return None
    for module in _all_modules():
        if typ := module.types.get(qname):
            return typ
    return None


def lookup_type_method(type_or_name: "TpyType | str", method_name: str) -> list[MethodDef] | None:
    """Lookup a method on a built-in type."""
    if typ := lookup_type(type_or_name):
        return typ.methods.get(method_name)
    return None


def lookup_type_by_func_name(func_name: str) -> BuiltinTypeDef | None:
    """Lookup a type by constructor function name (e.g., 'Int32').

    Only returns non-generic types with constructors. Generic types (like list)
    go through lookup_generic_type for proper type parameter inference.
    """
    for module in _all_modules():
        qualified = f"{module.name}.{func_name}"
        if typ := module.types.get(qualified):
            # Only return non-generic types with constructors
            if typ.constructors and not typ.type_params:
                return typ
    return None


@dataclass
class GenericTypeLookup:
    """Result of looking up a generic type by name."""
    type_def: BuiltinTypeDef
    qualified_name: str


def lookup_generic_type(name: str) -> GenericTypeLookup | None:
    """Lookup a parameterized type by its simple name (e.g., 'list', 'StaticList').

    Only returns types that have type parameters and a type factory defined.
    Returns first match across modules - names must be unique to avoid ambiguity.
    """
    for module in _all_modules():
        qualified = f"{module.name}.{name}"
        if typ := module.types.get(qualified):
            if typ.type_params and typ.type_factory:
                return GenericTypeLookup(typ, qualified)
    return None


def extract_type_params(tpy_type: "TpyType") -> dict[str, "TpyType"]:
    """Extract type parameters from a concrete type instance.

    For list[Int32], returns {"T": Int32}.
    For StaticList[Point, 10], returns {"T": Point}.

    Note: Only type parameters that are themselves types are extracted.
    Integer parameters like N in StaticList[T, N] are not included.
    """
    from tpyc.typesys import ListType, PendingListType, StaticListType, ArrayType, SpanType

    if isinstance(tpy_type, (ListType, PendingListType)):
        return {"T": tpy_type.element_type}
    elif isinstance(tpy_type, StaticListType):
        return {"T": tpy_type.element_type}  # N is int, not TpyType
    elif isinstance(tpy_type, ArrayType):
        return {"T": tpy_type.element_type}  # N is int, not TpyType
    elif isinstance(tpy_type, SpanType):
        return {"T": tpy_type.element_type}
    return {}


def _resolve_type_or_param(t: TypeOrParam, type_params: dict[str, "TpyType"]) -> "TpyType | str":
    """Resolve a string type parameter to a concrete TpyType.

    Handles:
    - "T" -> type_params["T"]
    - "Ptr[T]" -> PtrType(type_params["T"])
    - "Iterable" -> "Iterable" (kept as string, accepts container types)
    - TpyType -> returned as-is
    """
    if isinstance(t, str):
        # "Iterable" accepts any container type (list, Array, Span, StaticList)
        if t == "Iterable":
            return "Iterable"

        # Check for Ptr[X] pattern
        ptr_match = re.match(r"Ptr\[(\w+)\]", t)
        if ptr_match:
            from tpyc.typesys import PtrType
            inner_name = ptr_match.group(1)
            if inner_name not in type_params:
                raise ValueError(f"Unresolved type parameter in Ptr[{inner_name}]")
            return PtrType(type_params[inner_name])

        # Simple type param like "T"
        if t not in type_params:
            raise ValueError(f"Unresolved type parameter: {t}")
        return type_params[t]
    else:
        return t  # Already a TpyType


def resolve_method(method: MethodDef, type_params: dict[str, "TpyType"]) -> MethodDef:
    """Resolve string type parameters in a method signature to concrete types.

    Given a method with string type param placeholders and a dict mapping
    param names to concrete types, returns a new MethodDef with all types resolved.

    Example:
        method = MethodDef(params=[ParamDef("value", "T")], returns="T", cpp=...)
        resolved = resolve_method(method, {"T": Int32})
        # resolved.params[0].type == Int32, resolved.returns == Int32
    """
    resolved_params = [
        ParamDef(name=p.name, type=_resolve_type_or_param(p.type, type_params))
        for p in method.params
    ]
    resolved_returns = _resolve_type_or_param(method.returns, type_params)
    return MethodDef(params=resolved_params, returns=resolved_returns, cpp=method.cpp)


# Operator to method name mappings
BINOP_TO_METHOD = {
    "+": "__add__", "-": "__sub__", "*": "__mul__",
    "div": "__truediv__", "//": "__floordiv__", "%": "__mod__", "**": "__pow__",
    "<<": "__lshift__", ">>": "__rshift__",
    "&": "__and__", "|": "__or__", "^": "__xor__",
}

BINOP_TO_RMETHOD = {
    "+": "__radd__", "-": "__rsub__", "*": "__rmul__",
    "div": "__rtruediv__", "//": "__rfloordiv__", "%": "__rmod__", "**": "__rpow__",
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
    from tpyc.typesys import BIGINT, INT32, FLOAT
    if qname == "builtins.int":
        return BIGINT
    if qname == "builtins.float":
        return FLOAT
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
