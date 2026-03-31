"""
CPython backend for TurboPython Macro API.

Provides the same public API as tpyc.macro_api but targets Python runtime
objects instead of compiler AST nodes. Macro modules (dataclasses.py, model.py,
user macros) import from tpyc.macro_api -- under CPython this module is found
via PYTHONPATH and makes macros work as real class/function decorators.
"""
from __future__ import annotations

import ast as _ast
import builtins as _builtins_mod
import copy as _copy
import enum as _enum_mod
import functools
import importlib
import inspect
import operator
import sys as _sys
import textwrap as _textwrap
import types as _types_mod
import warnings as _warnings
from typing import Any, Callable, NoReturn, Union, get_args, get_origin


# ===================================================================
# CpyType hierarchy -- lightweight stand-in for tpyc.typesys.TpyType
# ===================================================================

class CpyType:
    """Base type for the CPython macro backend."""

    def is_value_type(self) -> bool:
        return False

    def __str__(self) -> str:
        return "<?>"

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


class CpyVoidType(CpyType):
    def __str__(self) -> str:
        return "None"


class CpyBoolType(CpyType):
    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return "bool"


class CpyStrType(CpyType):
    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return "str"


class CpyStrViewType(CpyType):
    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return "StrView"


class CpyFloatType(CpyType):
    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return "float"


class CpyFloat32Type(CpyType):
    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return "Float32"


class CpyBigIntType(CpyType):
    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return "int"


class CpyFixedIntType(CpyType):
    def __init__(self, name: str) -> None:
        self._name = name

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return self._name


class CpyNamedType(CpyType):
    def __init__(self, name: str, *, value_type: bool = True, is_record: bool = False) -> None:
        self._name = name
        self._value_type = value_type
        self.is_record = is_record

    def is_value_type(self) -> bool:
        return self._value_type

    def __str__(self) -> str:
        return self._name


class CpyOwnType(CpyType):
    def __init__(self, inner: CpyType) -> None:
        self.wrapped = inner

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"Own[{self.wrapped}]"


class CpyOptionalType(CpyType):
    def __init__(self, inner: CpyType) -> None:
        self.inner = inner

    def is_value_type(self) -> bool:
        return self.inner.is_value_type()

    def __str__(self) -> str:
        return f"{self.inner} | None"


class CpyListType(CpyType):
    def __init__(self, element_type: CpyType | None) -> None:
        self.element_type = element_type

    def __str__(self) -> str:
        et = self.element_type or "?"
        return f"list[{et}]"


class CpyDictType(CpyType):
    def __init__(self, key_type: CpyType | None, value_type: CpyType | None) -> None:
        self.key_type = key_type
        self.value_type = value_type

    def __str__(self) -> str:
        return f"dict[{self.key_type or '?'}, {self.value_type or '?'}]"


class CpyTupleType(CpyType):
    def __init__(self, element_types: tuple[CpyType, ...]) -> None:
        self.element_types = element_types

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        inner = ", ".join(str(t) for t in self.element_types)
        return f"tuple[{inner}]"


class CpyEnumType(CpyType):
    def __init__(self, name: str) -> None:
        self._name = name

    def is_value_type(self) -> bool:
        return True

    def __str__(self) -> str:
        return self._name


class CpyUnionType(CpyType):
    def __init__(self, members: tuple[CpyType, ...]) -> None:
        self.members = members

    def is_value_type(self) -> bool:
        return all(m.is_value_type() for m in self.members)

    def __str__(self) -> str:
        return " | ".join(str(m) for m in self.members)


# ===================================================================
# Type singletons
# ===================================================================

_VOID = CpyVoidType()
_STR = CpyStrType()
_STRVIEW = CpyStrViewType()
_BOOL = CpyBoolType()
_FLOAT = CpyFloatType()
_FLOAT32 = CpyFloat32Type()
_BIGINT = CpyBigIntType()

_INT8 = CpyFixedIntType("Int8")
_INT16 = CpyFixedIntType("Int16")
_INT32 = CpyFixedIntType("Int32")
_INT64 = CpyFixedIntType("Int64")
_UINT8 = CpyFixedIntType("UInt8")
_UINT16 = CpyFixedIntType("UInt16")
_UINT32 = CpyFixedIntType("UInt32")
_UINT64 = CpyFixedIntType("UInt64")

_FIXED_INT_NAMES: frozenset[str] = frozenset([
    "Int8", "Int16", "Int32", "Int64",
    "UInt8", "UInt16", "UInt32", "UInt64",
])


# ===================================================================
# MacroError
# ===================================================================

class MacroError(Exception):
    """Raised by macro code to report a compile error."""

    def __init__(self, msg: str, loc: Any = None) -> None:
        super().__init__(msg)
        self.loc = loc


# ===================================================================
# Registries -- track macro-decorated classes
# ===================================================================

_class_registry: dict[str, type] = {}
_class_dc_fields: dict[str, list[FieldInfo]] = {}

# Shared exec namespace for compiled functions.  Populated by macro_deps()
# and _apply_class_macro.  Functions compiled via ast.function() look up
# names here at call time (late binding).
_FACTORY_MISSING = object()

_macro_exec_ns: dict[str, Any] = {
    "__builtins__": _builtins_mod.__dict__,
    "_FACTORY_MISSING": _FACTORY_MISSING,
}


# ===================================================================
# TypeInfo
# ===================================================================

_tpy_int_types_cache: dict[type, str] | None = None


def _get_tpy_int_types() -> dict[type, str]:
    """Lazily load tpy fixed-int type mappings to avoid circular imports."""
    global _tpy_int_types_cache
    if _tpy_int_types_cache is not None:
        return _tpy_int_types_cache
    result: dict[type, str] = {}
    try:
        import tpy
        for name in ("Int8", "Int16", "Int32", "Int64",
                      "UInt8", "UInt16", "UInt32", "UInt64"):
            t = getattr(tpy, name, None)
            if t is not None:
                result[t] = name
    except ImportError:
        pass
    _tpy_int_types_cache = result
    return result


class TypeInfo:
    """Read-only type metadata exposed to macros."""

    def __init__(
        self,
        name: str,
        type_args: list[TypeInfo] | None = None,
        is_optional: bool = False,
        is_value_type: bool = True,
        is_record: bool = False,
        _tpy_type: CpyType | None = None,
    ) -> None:
        self.name = name
        self.type_args: list[TypeInfo] = type_args if type_args is not None else []
        self.is_optional = is_optional
        self.is_value_type = is_value_type
        self.is_record = is_record
        self._tpy_type = _tpy_type

    @property
    def raw_type(self) -> CpyType:
        assert self._tpy_type is not None, "raw_type on TypeInfo with no underlying type"
        return self._tpy_type

    # -- Type predicates -----------------------------------------------

    @property
    def is_str(self) -> bool:
        return self.name in ("str", "String", "StrView")

    @property
    def is_int(self) -> bool:
        return self.name in _FIXED_INT_NAMES

    @property
    def is_int32(self) -> bool:
        return self.name == "Int32"

    @property
    def is_float(self) -> bool:
        return self.name in ("float", "Float32", "Float64")

    @property
    def is_float32(self) -> bool:
        return self.name == "Float32"

    @property
    def is_bool(self) -> bool:
        return self.name == "bool"

    @property
    def is_bigint(self) -> bool:
        return self.name == "int"

    @property
    def is_enum(self) -> bool:
        return isinstance(self._tpy_type, CpyEnumType)

    @property
    def is_list(self) -> bool:
        return self.name == "list"

    @property
    def is_dict(self) -> bool:
        return self.name == "dict"

    @property
    def is_tuple(self) -> bool:
        return self.name == "tuple"

    @property
    def enum_name(self) -> str:
        assert isinstance(self._tpy_type, CpyEnumType)
        return self._tpy_type._name

    @property
    def int_type_name(self) -> str:
        return self.name

    def unwrap_optional(self) -> TypeInfo | None:
        if not self.is_optional:
            return None
        if isinstance(self._tpy_type, CpyOptionalType):
            return TypeInfo.from_tpy_type(self._tpy_type.inner)
        if self.type_args:
            return self.type_args[0]
        return None

    @property
    def tuple_element_types(self) -> list[TypeInfo]:
        return list(self.type_args)

    # -- Constructors --------------------------------------------------

    @staticmethod
    def from_tpy_type(typ: CpyType) -> TypeInfo:
        """Construct TypeInfo from a CpyType."""
        type_args: list[TypeInfo] = []
        is_optional = isinstance(typ, CpyOptionalType)
        is_record = False
        name = str(typ)

        if isinstance(typ, CpyOptionalType):
            inner_ti = TypeInfo.from_tpy_type(typ.inner)
            name = inner_ti.name
            type_args = inner_ti.type_args
            is_record = inner_ti.is_record
        elif isinstance(typ, CpyListType) and typ.element_type:
            name = "list"
            type_args = [TypeInfo.from_tpy_type(typ.element_type)]
        elif isinstance(typ, CpyDictType) and typ.key_type and typ.value_type:
            name = "dict"
            type_args = [TypeInfo.from_tpy_type(typ.key_type),
                         TypeInfo.from_tpy_type(typ.value_type)]
        elif isinstance(typ, CpyTupleType):
            name = "tuple"
            type_args = [TypeInfo.from_tpy_type(et) for et in typ.element_types]
        elif isinstance(typ, CpyNamedType):
            name = typ._name
            is_record = typ.is_record or name in _class_registry

        return TypeInfo(
            name=name,
            type_args=type_args,
            is_optional=is_optional,
            is_value_type=typ.is_value_type(),
            is_record=is_record,
            _tpy_type=typ,
        )

    @staticmethod
    def from_python_type(annotation: Any) -> TypeInfo:
        """Construct TypeInfo from a Python type annotation."""
        origin = get_origin(annotation)
        args = get_args(annotation)

        # Optional[T] == Union[T, None] (also handles T | None via types.UnionType)
        if origin is Union or origin is _types_mod.UnionType:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1 and type(None) in args:
                inner = TypeInfo.from_python_type(non_none[0])
                return TypeInfo(
                    name=inner.name,
                    type_args=inner.type_args,
                    is_optional=True,
                    is_value_type=inner.is_value_type,
                    is_record=inner.is_record,
                    _tpy_type=CpyOptionalType(inner._tpy_type or CpyNamedType(inner.name)),
                )

        # list[T]
        if origin is list:
            if args:
                elem = TypeInfo.from_python_type(args[0])
                return TypeInfo(name="list", type_args=[elem],
                                is_value_type=False,
                                _tpy_type=CpyListType(elem._tpy_type))
            return TypeInfo(name="list", is_value_type=False, _tpy_type=CpyListType(None))

        # dict[K, V]
        if origin is dict:
            if len(args) == 2:
                k = TypeInfo.from_python_type(args[0])
                v = TypeInfo.from_python_type(args[1])
                return TypeInfo(name="dict", type_args=[k, v],
                                is_value_type=False,
                                _tpy_type=CpyDictType(k._tpy_type, v._tpy_type))
            return TypeInfo(name="dict", is_value_type=False,
                            _tpy_type=CpyDictType(None, None))

        # tuple[T1, T2, ...]
        if origin is tuple:
            elems = [TypeInfo.from_python_type(a) for a in args]
            return TypeInfo(name="tuple", type_args=elems, is_value_type=True,
                            _tpy_type=CpyTupleType(tuple(e._tpy_type or CpyNamedType(e.name) for e in elems)))

        # set[T]
        if origin is set:
            if args:
                elem = TypeInfo.from_python_type(args[0])
                return TypeInfo(name="set", type_args=[elem], is_value_type=False,
                                _tpy_type=CpyNamedType("set"))
            return TypeInfo(name="set", is_value_type=False, _tpy_type=CpyNamedType("set"))

        # Primitives
        if annotation is bool:
            return TypeInfo(name="bool", is_value_type=True, _tpy_type=_BOOL)
        if annotation is int:
            return TypeInfo(name="int", is_value_type=True, _tpy_type=_BIGINT)
        if annotation is float:
            return TypeInfo(name="float", is_value_type=True, _tpy_type=_FLOAT)
        if annotation is str:
            return TypeInfo(name="str", is_value_type=True, _tpy_type=_STR)

        # Fixed-width int types from tpy stubs
        int_map = _get_tpy_int_types()
        if annotation in int_map:
            name = int_map[annotation]
            return TypeInfo(name=name, is_value_type=True,
                            _tpy_type=CpyFixedIntType(name))

        # Enum check
        try:
            if isinstance(annotation, type) and issubclass(annotation, _enum_mod.IntEnum):
                return TypeInfo(name=annotation.__name__, is_value_type=True,
                                _tpy_type=CpyEnumType(annotation.__name__))
            if isinstance(annotation, type) and issubclass(annotation, _enum_mod.Enum):
                return TypeInfo(name=annotation.__name__, is_value_type=True,
                                _tpy_type=CpyEnumType(annotation.__name__))
        except ImportError:
            pass

        # User class (record)
        if isinstance(annotation, type):
            name = annotation.__name__
            is_record = name in _class_registry or hasattr(annotation, "__annotations__")
            return TypeInfo(name=name, is_record=is_record, is_value_type=True,
                            _tpy_type=CpyNamedType(name, value_type=True, is_record=is_record))

        # String annotation (from __future__ annotations or forward refs)
        if isinstance(annotation, str):
            name = annotation
            is_record = name in _class_registry
            return TypeInfo(name=name, is_record=is_record, is_value_type=True,
                            _tpy_type=CpyNamedType(name, value_type=True, is_record=is_record))

        # Fallback
        name = getattr(annotation, "__name__", str(annotation))
        return TypeInfo(name=name, _tpy_type=CpyNamedType(name))

    @staticmethod
    def from_runtime_value(value: Any) -> TypeInfo:
        """Construct TypeInfo from a runtime value (for call macros)."""
        t = type(value)
        name = t.__name__
        if name in _class_registry:
            return TypeInfo(name=name, is_record=True, is_value_type=True,
                            _tpy_type=CpyNamedType(name, value_type=True))
        return TypeInfo.from_python_type(t)


# ===================================================================
# FieldInfo
# ===================================================================

def _value_to_ast_default(value: Any) -> _ast.expr | None:
    """Convert a simple Python value to a python-ast Constant node."""
    if isinstance(value, bool):
        return _ast.Constant(value=value)
    if isinstance(value, int):
        return _ast.Constant(value=int(value))
    if isinstance(value, float):
        return _ast.Constant(value=float(value))
    if isinstance(value, str):
        return _ast.Constant(value=value)
    if value is None:
        return _ast.Constant(value=None)
    return None


class FieldInfo:
    """Field metadata exposed to macros."""

    def __init__(
        self,
        name: str,
        type: TypeInfo,
        has_default: bool,
        default_expr: Any = None,
        is_factory_default: bool = False,
        loc: Any = None,
        default_obj: Any = None,
    ) -> None:
        self.name = name
        self.type = type
        self.has_default = has_default
        self.default_expr = default_expr
        self.is_factory_default = is_factory_default
        self.loc = loc
        self.default_obj = default_obj

    def set_default(self, value: Any, default_value: str | None = None,
                    is_factory: bool = False) -> None:
        """Set the default expression from an AST node or Python value."""
        if isinstance(value, _ast.AST):
            self.default_expr = value
        elif is_factory and callable(value):
            name = getattr(value, "__name__", str(value))
            self.default_expr = _ast.Call(
                func=_ast.Name(id=name, ctx=_ast.Load()),
                args=[], keywords=[],
            )
        elif isinstance(value, bool):
            self.default_expr = _ast.Constant(value=value)
        elif isinstance(value, int):
            self.default_expr = _ast.Constant(value=int(value))
        elif isinstance(value, float):
            self.default_expr = _ast.Constant(value=float(value))
        elif isinstance(value, str):
            self.default_expr = _ast.Constant(value=value)
        elif value is None:
            self.default_expr = _ast.Constant(value=None)
        else:
            self.default_expr = None
        self.has_default = self.default_expr is not None
        self.is_factory_default = is_factory

    def to_internal(self) -> FieldInfo:
        return self


# ===================================================================
# MethodInfo / MethodStub
# ===================================================================

class MethodInfo:
    """Method metadata (read-only)."""

    def __init__(self, name: str, is_readonly: bool = False,
                 is_staticmethod: bool = False) -> None:
        self.name = name
        self.is_readonly = is_readonly
        self.is_staticmethod = is_staticmethod


class MethodStub:
    """A method signature whose body is generated by the CPython backend."""

    def __init__(self, name: str, params: list[tuple[str, CpyType]],
                 return_type: CpyType, is_readonly: bool = False) -> None:
        self.name = name
        self.params = params
        self.return_type = return_type
        self.is_readonly = is_readonly


# ===================================================================
# MacroArg (for call-site macros)
# ===================================================================

class MacroArg:
    """Argument passed to a call-site macro."""

    def __init__(self, expr: Any, type: TypeInfo) -> None:
        self.expr = expr
        self.type = type


# ===================================================================
# CallMacroContext
# ===================================================================

class CallMacroContext:
    """Context for call-site macros with runtime class introspection."""

    def __init__(self, loc: Any = None) -> None:
        self._loc = loc

    def get_record_fields(self, name: str) -> list[FieldInfo] | None:
        if name not in _class_dc_fields:
            return None
        return list(_class_dc_fields[name])

    def is_dataclass(self, name: str) -> bool:
        return name in _class_dc_fields

    def get_iterable_element_type(self, type_info: TypeInfo) -> TypeInfo | None:
        if type_info.name == "list" and type_info.type_args:
            return type_info.type_args[0]
        if type_info.name == "set" and type_info.type_args:
            return type_info.type_args[0]
        return None

    def warning(self, msg: str, loc: Any = None) -> None:
        _warnings.warn(msg, stacklevel=2)

    def error(self, msg: str, loc: Any = None) -> NoReturn:
        raise MacroError(msg, loc)


# ===================================================================
# ClassInfo
# ===================================================================

class ClassInfo:
    """Class metadata passed to class macros under CPython."""

    def __init__(self, cls: type) -> None:
        self._cls = cls
        self._added_methods: list[Any] = []   # compiled function objects
        self._method_stubs: list[MethodStub] = []
        self._dataclass_fields: list[FieldInfo] | None = None

        self.name: str = cls.__name__
        self.type_params: list[str] = []

        # Build field list from annotations.
        # Use get_type_hints() to resolve string annotations (from __future__
        # annotations or forward references), with fallback to raw annotations.
        own_annotations = {}
        if "__annotations__" in cls.__dict__:
            own_annotations = cls.__dict__["__annotations__"]
        try:
            import typing as _typing_mod
            resolved_hints = _typing_mod.get_type_hints(cls)
        except Exception:
            resolved_hints = own_annotations
        self.fields: list[FieldInfo] = []
        for attr_name, annotation in own_annotations.items():
            annotation = resolved_hints.get(attr_name, annotation)
            has_default = attr_name in cls.__dict__
            default_val = cls.__dict__.get(attr_name)
            default_expr = _value_to_ast_default(default_val) if has_default else None
            self.fields.append(FieldInfo(
                name=attr_name,
                type=TypeInfo.from_python_type(annotation),
                has_default=has_default,
                default_expr=default_expr,
                default_obj=default_val if has_default else None,
            ))

        # Parent type
        bases = [b for b in cls.__bases__ if b is not object]
        self.parent: TypeInfo | None = None
        if bases:
            self.parent = TypeInfo.from_python_type(bases[0])

    # -- Flags ---------------------------------------------------------

    @property
    def is_dataclass(self) -> bool:
        return getattr(self._cls, "_cpy_is_dataclass", False)

    @is_dataclass.setter
    def is_dataclass(self, value: bool) -> None:
        self._cls._cpy_is_dataclass = value  # type: ignore[attr-defined]

    @property
    def is_frozen(self) -> bool:
        return getattr(self._cls, "_cpy_is_frozen", False)

    @is_frozen.setter
    def is_frozen(self, value: bool) -> None:
        self._cls._cpy_is_frozen = value  # type: ignore[attr-defined]

    @property
    def is_ordered(self) -> bool:
        return getattr(self._cls, "_cpy_is_ordered", False)

    @is_ordered.setter
    def is_ordered(self, value: bool) -> None:
        self._cls._cpy_is_ordered = value  # type: ignore[attr-defined]

    # -- Read methods --------------------------------------------------

    @property
    def methods(self) -> list[MethodInfo]:
        result: list[MethodInfo] = []
        for name, val in self._cls.__dict__.items():
            if callable(val) and not name.startswith("_cpy_"):
                is_static = isinstance(
                    self._cls.__dict__.get(name), staticmethod)
                result.append(MethodInfo(name=name, is_staticmethod=is_static))
        for fn in self._added_methods:
            fname = getattr(fn, "__name__", "?")
            result.append(MethodInfo(name=fname))
        return result

    def has_method(self, name: str) -> bool:
        if name in self._cls.__dict__:
            val = self._cls.__dict__[name]
            if callable(val) or isinstance(val, (staticmethod, classmethod)):
                return True
        for fn in self._added_methods:
            if getattr(fn, "__name__", None) == name:
                return True
        return False

    def get_parent_fields(self) -> list[FieldInfo]:
        bases = [b for b in self._cls.__bases__ if b is not object]
        for base in bases:
            if base.__name__ in _class_dc_fields:
                return list(_class_dc_fields[base.__name__])
        return []

    def get_method_loc(self, name: str) -> Any:
        return None

    def is_parent_frozen(self) -> tuple[bool, str] | None:
        bases = [b for b in self._cls.__bases__ if b is not object]
        for base in bases:
            if base.__name__ in _class_dc_fields:
                return (getattr(base, "_cpy_is_frozen", False), base.__name__)
        return None

    # -- Mutation methods ----------------------------------------------

    def add_method(self, func: Any) -> None:
        """Add a compiled function as a method."""
        self._added_methods.append(func)

    def add_method_from_source(self, source: str) -> None:
        """Parse a method definition from source and add it."""
        self.add_method(ast.quote_fun(source))

    def add_method_stub(
        self,
        name: str,
        params: list[tuple[str, CpyType]],
        return_type: CpyType,
        is_readonly: bool = False,
    ) -> None:
        self._method_stubs.append(MethodStub(
            name=name, params=params,
            return_type=return_type, is_readonly=is_readonly,
        ))

    def set_dataclass_fields(self, fields: list[FieldInfo]) -> None:
        self._dataclass_fields = list(fields)

    # -- Diagnostics ---------------------------------------------------

    def warning(self, msg: str, loc: Any = None) -> None:
        _warnings.warn(msg, stacklevel=2)

    def error(self, msg: str, loc: Any = None) -> NoReturn:
        raise MacroError(msg, loc)

    # -- Apply mutations -----------------------------------------------

    def apply_to_record(self) -> None:
        """Write macro mutations back to the Python class."""
        cls = self._cls

        # Attach added methods
        for fn in self._added_methods:
            name = getattr(fn, "__name__", None)
            if name:
                if getattr(fn, "is_staticmethod", False) and not isinstance(fn, staticmethod):
                    fn = staticmethod(fn)
                setattr(cls, name, fn)

        # Generate real methods for stubs
        all_fields = self._dataclass_fields or self.fields
        for stub in self._method_stubs:
            method = _generate_stub_method(stub, cls.__name__, all_fields)
            if method is not None:
                setattr(cls, stub.name, method)

        # If frozen, prevent attribute mutation
        if getattr(cls, "_cpy_is_frozen", False):
            _apply_frozen(cls)

        # Set __match_args__ for match/case positional patterns
        if self._dataclass_fields is not None:
            cls.__match_args__ = tuple(f.name for f in self._dataclass_fields)  # type: ignore[attr-defined]

        # Register in class registry for call macros
        _class_registry[cls.__name__] = cls
        _macro_exec_ns[cls.__name__] = cls
        if self._dataclass_fields is not None:
            _class_dc_fields[cls.__name__] = list(self._dataclass_fields)

        # Clean up class-level field descriptors (like Field instances)
        # so they don't shadow instance attributes
        for fld in self.fields:
            if fld.name in cls.__dict__:
                val = cls.__dict__[fld.name]
                if not isinstance(val, (staticmethod, classmethod, property)):
                    try:
                        delattr(cls, fld.name)
                    except AttributeError:
                        pass

    def get_method_stubs(self) -> list[MethodStub]:
        return self._method_stubs

    def get_dataclass_fields(self) -> list[FieldInfo] | None:
        return self._dataclass_fields


def _generate_stub_method(
    stub: MethodStub,
    cls_name: str,
    fields: list[FieldInfo],
) -> Callable | None:
    """Generate a real Python method for a method stub."""
    if stub.name == "__repr__":
        field_names = [f.name for f in fields]
        def __repr__(self: Any) -> str:
            parts = ", ".join(
                f"{n}={getattr(self, n)!r}" for n in field_names
            )
            return f"{cls_name}({parts})"
        __repr__.__name__ = "__repr__"
        return __repr__

    if stub.name == "__hash__":
        field_names = [f.name for f in fields]
        def __hash__(self: Any) -> int:
            return hash(tuple(getattr(self, n) for n in field_names))
        __hash__.__name__ = "__hash__"
        return __hash__

    order_ops = {
        "__lt__": operator.lt,
        "__le__": operator.le,
        "__gt__": operator.gt,
        "__ge__": operator.ge,
    }
    if stub.name in order_ops:
        op = order_ops[stub.name]
        field_names = [f.name for f in fields]
        def _order(self: Any, other: Any, _op: Any = op,
                   _fnames: list[str] = field_names) -> bool:
            self_t = tuple(getattr(self, n) for n in _fnames)
            other_t = tuple(getattr(other, n) for n in _fnames)
            return _op(self_t, other_t)
        _order.__name__ = stub.name
        return _order

    return None


def _apply_frozen(cls: type) -> None:
    """Make a class frozen by overriding __setattr__ and __delattr__."""
    def _frozen_setattr(self: Any, name: str, value: Any) -> None:
        raise AttributeError(
            f"cannot assign to field '{name}' of frozen class '{cls.__name__}'"
        )

    def _frozen_delattr(self: Any, name: str) -> None:
        raise AttributeError(
            f"cannot delete field '{name}' of frozen class '{cls.__name__}'"
        )

    cls.__setattr__ = _frozen_setattr  # type: ignore[assignment]
    cls.__delattr__ = _frozen_delattr  # type: ignore[assignment]


# ===================================================================
# AstBuilder -- produces python ast nodes
# ===================================================================

# Operator dispatch tables
_COMPARE_OPS: dict[str, _ast.cmpop] = {
    "==": _ast.Eq(), "!=": _ast.NotEq(),
    "<": _ast.Lt(), "<=": _ast.LtE(),
    ">": _ast.Gt(), ">=": _ast.GtE(),
    "is": _ast.Is(), "is not": _ast.IsNot(),
    "in": _ast.In(), "not in": _ast.NotIn(),
}

_BOOL_OPS: dict[str, _ast.boolop] = {
    "&&": _ast.And(), "||": _ast.Or(),
    "and": _ast.And(), "or": _ast.Or(),
}

_BIN_OPS: dict[str, _ast.operator] = {
    "+": _ast.Add(), "-": _ast.Sub(),
    "*": _ast.Mult(), "/": _ast.Div(),
    "//": _ast.FloorDiv(), "%": _ast.Mod(),
    "**": _ast.Pow(),
    "&": _ast.BitAnd(), "|": _ast.BitOr(), "^": _ast.BitXor(),
    "<<": _ast.LShift(), ">>": _ast.RShift(),
}

_UNARY_OPS: dict[str, _ast.unaryop] = {
    "-": _ast.USub(), "+": _ast.UAdd(),
    "~": _ast.Invert(), "not": _ast.Not(),
}


def _to_store(node: _ast.expr) -> _ast.expr:
    """Deep-copy an expression and set all contexts to Store."""
    node = _copy.copy(node)
    if isinstance(node, _ast.Name):
        node.ctx = _ast.Store()
    elif isinstance(node, _ast.Attribute):
        node.ctx = _ast.Store()
    elif isinstance(node, _ast.Subscript):
        node.ctx = _ast.Store()
    elif isinstance(node, _ast.Tuple):
        node.ctx = _ast.Store()
        node.elts = [_to_store(e) for e in node.elts]
    elif isinstance(node, _ast.List):
        node.ctx = _ast.Store()
        node.elts = [_to_store(e) for e in node.elts]
    return node


def _dotted_name(name: str) -> _ast.expr:
    """Convert 'a.b.c' to Attribute(Attribute(Name('a'), 'b'), 'c')."""
    parts = name.split(".")
    node: _ast.expr = _ast.Name(id=parts[0], ctx=_ast.Load())
    for part in parts[1:]:
        node = _ast.Attribute(value=node, attr=part, ctx=_ast.Load())
    return node


class AstBuilder:
    """AST builder that produces Python ast nodes."""

    def __init__(self) -> None:
        self._tmp_counter = 0

    def reset_tmp_counter(self) -> None:
        self._tmp_counter = 0

    def fresh_tmp(self, hint: str = "v") -> str:
        self._tmp_counter += 1
        return f"__{hint}_{self._tmp_counter}"

    # -- Expressions ---------------------------------------------------

    def name(self, n: str) -> _ast.expr:
        return _dotted_name(n)

    def call(self, func: str, args: list[_ast.expr] | None = None,
             call_type: CpyType | None = None) -> _ast.expr:
        return _ast.Call(
            func=_dotted_name(func),
            args=args or [], keywords=[],
        )

    def method_call(self, obj: _ast.expr, method: str,
                    args: list[_ast.expr] | None = None) -> _ast.expr:
        return _ast.Call(
            func=_ast.Attribute(value=obj, attr=method, ctx=_ast.Load()),
            args=args or [], keywords=[],
        )

    def field_access(self, obj: _ast.expr, field_name: str) -> _ast.expr:
        return _ast.Attribute(value=obj, attr=field_name, ctx=_ast.Load())

    def binop(self, left: _ast.expr, op: str, right: _ast.expr) -> _ast.expr:
        if op in _COMPARE_OPS:
            return _ast.Compare(
                left=left, ops=[_COMPARE_OPS[op]], comparators=[right],
            )
        if op in _BOOL_OPS:
            return _ast.BoolOp(op=_BOOL_OPS[op], values=[left, right])
        if op in _BIN_OPS:
            return _ast.BinOp(left=left, op=_BIN_OPS[op], right=right)
        raise ValueError(f"Unknown binary operator: {op!r}")

    def subscript(self, obj: _ast.expr, index: _ast.expr) -> _ast.expr:
        return _ast.Subscript(value=obj, slice=index, ctx=_ast.Load())

    def unary(self, op: str, operand: _ast.expr) -> _ast.expr:
        if op in _UNARY_OPS:
            return _ast.UnaryOp(op=_UNARY_OPS[op], operand=operand)
        raise ValueError(f"Unknown unary operator: {op!r}")

    def str_lit(self, s: str) -> _ast.expr:
        return _ast.Constant(value=s)

    def int_lit(self, n: int) -> _ast.expr:
        return _ast.Constant(value=n)

    def float_lit(self, f: float) -> _ast.expr:
        return _ast.Constant(value=f)

    def bool_lit(self, b: bool) -> _ast.expr:
        return _ast.Constant(value=b)

    def none_lit(self) -> _ast.expr:
        return _ast.Constant(value=None)

    def list_lit(self, elements: list[_ast.expr] | None = None) -> _ast.expr:
        return _ast.List(elts=elements or [], ctx=_ast.Load())

    def dict_lit(self, keys: list[_ast.expr] | None = None,
                 values: list[_ast.expr] | None = None) -> _ast.expr:
        return _ast.Dict(keys=keys or [], values=values or [])

    def tuple_lit(self, elements: list[_ast.expr]) -> _ast.expr:
        return _ast.Tuple(elts=elements, ctx=_ast.Load())

    def list_comprehension(
        self, element_expr: _ast.expr,
        generator: _ast.comprehension,
    ) -> _ast.expr:
        return _ast.ListComp(elt=element_expr, generators=[generator])

    def dict_comprehension(
        self, key_expr: _ast.expr, value_expr: _ast.expr,
        generator: _ast.comprehension,
    ) -> _ast.expr:
        return _ast.DictComp(key=key_expr, value=value_expr,
                             generators=[generator])

    def comprehension_generator(
        self, var: str, iterable: _ast.expr,
        conditions: list[_ast.expr] | None = None,
        unpack_vars: list[str | None] | None = None,
    ) -> _ast.comprehension:
        if unpack_vars:
            target = _ast.Tuple(
                elts=[
                    _ast.Name(id=(v if v else "_"), ctx=_ast.Store())
                    for v in unpack_vars
                ],
                ctx=_ast.Store(),
            )
        else:
            target = _ast.Name(id=var, ctx=_ast.Store())
        return _ast.comprehension(
            target=target, iter=iterable,
            ifs=conditions or [], is_async=0,
        )

    # -- Statements ----------------------------------------------------

    def var_decl(self, name: str, type: CpyType | None = None,
                 init: _ast.expr | None = None) -> _ast.stmt:
        if init is not None:
            return _ast.Assign(
                targets=[_ast.Name(id=name, ctx=_ast.Store())],
                value=init,
            )
        # Type annotation without init -- no-op under CPython
        return _ast.Pass()

    def assign(self, target: _ast.expr, value: _ast.expr) -> _ast.stmt:
        return _ast.Assign(targets=[_to_store(target)], value=value)

    def expr_stmt(self, expr: _ast.expr) -> _ast.stmt:
        return _ast.Expr(value=expr)

    def return_(self, value: _ast.expr | None = None) -> _ast.stmt:
        return _ast.Return(value=value)

    def if_(self, condition: _ast.expr, then_body: list[_ast.stmt],
            else_body: list[_ast.stmt] | None = None) -> _ast.stmt:
        return _ast.If(
            test=condition, body=then_body,
            orelse=else_body or [],
        )

    def while_(self, condition: _ast.expr,
               body: list[_ast.stmt]) -> _ast.stmt:
        return _ast.While(test=condition, body=body, orelse=[])

    def for_each(self, var: str, iterable: _ast.expr,
                 body: list[_ast.stmt],
                 is_tuple_unpack: bool = False) -> _ast.stmt:
        return _ast.For(
            target=_ast.Name(id=var, ctx=_ast.Store()),
            iter=iterable, body=body, orelse=[],
        )

    def raise_(self, exception_type: str) -> _ast.stmt:
        return _ast.Raise(
            exc=_ast.Call(
                func=_dotted_name(exception_type),
                args=[], keywords=[],
            ),
            cause=None,
        )

    def assert_(self, condition: _ast.expr,
                message: _ast.expr | None = None) -> _ast.stmt:
        return _ast.Assert(test=condition, msg=message)

    def try_(self, try_body: list[_ast.stmt],
             handlers: list[_ast.ExceptHandler] | None = None,
             else_body: list[_ast.stmt] | None = None,
             finally_body: list[_ast.stmt] | None = None) -> _ast.stmt:
        return _ast.Try(
            body=try_body, handlers=handlers or [],
            orelse=else_body or [], finalbody=finally_body or [],
        )

    def except_handler(
        self,
        exception_type: str | None = None,
        binding: str | None = None,
        body: list[_ast.stmt] | None = None,
    ) -> _ast.ExceptHandler:
        return _ast.ExceptHandler(
            type=(_dotted_name(exception_type) if exception_type else None),
            name=binding,
            body=body or [_ast.Pass()],
        )

    def match(self, subject: _ast.expr, cases: list[_ast.match_case]) -> _ast.stmt:
        return _ast.Match(subject=subject, cases=cases)

    def match_case(self, pattern: _ast.pattern, body: list[_ast.stmt],
                   guard: _ast.expr | None = None) -> _ast.match_case:
        return _ast.match_case(pattern=pattern, guard=guard, body=body)

    def literal_pattern(self, value: int | float | str | bool | None) -> _ast.pattern:
        if value is True or value is False or value is None:
            return _ast.MatchSingleton(value=value)
        return _ast.MatchValue(value=_ast.Constant(value=value))

    def wildcard_pattern(self) -> _ast.pattern:
        return _ast.MatchAs(pattern=None, name=None)

    def tuple_unpack(self, targets: list[str | None],
                     value: _ast.expr) -> _ast.stmt:
        target_nodes = [
            _ast.Name(id=(t if t else "_"), ctx=_ast.Store())
            for t in targets
        ]
        return _ast.Assign(
            targets=[_ast.Tuple(elts=target_nodes, ctx=_ast.Store())],
            value=value,
        )

    # -- Introspection -------------------------------------------------

    def get_field_name(self, expr: Any) -> str | None:
        if isinstance(expr, _ast.Attribute):
            return expr.attr
        return None

    def get_name(self, expr: Any) -> str | None:
        if isinstance(expr, _ast.Name):
            return expr.id
        # CPython runtime: handle callables passed as values
        if callable(expr) and hasattr(expr, "__name__"):
            return expr.__name__
        return None

    # -- Function compilation ------------------------------------------

    def function(
        self,
        name: str,
        params: list[tuple[str, CpyType]],
        return_type: CpyType,
        body: list[_ast.stmt],
        *,
        is_method: bool = False,
        is_staticmethod: bool = False,
        is_readonly: bool = False,
        readonly_opt_out: bool = False,
        error_return: str | None = None,
        defaults: list[_ast.expr | None] | None = None,
    ) -> Any:
        """Compile an AST function definition into a real Python function."""
        # Build argument list
        args: list[_ast.arg] = []
        if is_method and not is_staticmethod:
            args.append(_ast.arg(arg="self"))
        for pname, _ptype in params:
            args.append(_ast.arg(arg=pname))

        # Build defaults (trailing non-None entries)
        default_nodes: list[_ast.expr] = []
        if defaults:
            for d in defaults:
                if d is not None:
                    default_nodes.append(d)
                elif default_nodes:
                    # Once we've started collecting defaults, None means
                    # "no default" but Python requires contiguous trailing
                    # defaults.  This shouldn't happen if the macro validates
                    # field ordering, but be defensive.
                    break

        func_def = _ast.FunctionDef(
            name=name,
            args=_ast.arguments(
                posonlyargs=[], args=args, vararg=None,
                kwonlyargs=[], kw_defaults=[], kwarg=None,
                defaults=default_nodes,
            ),
            body=body or [_ast.Pass()],
            decorator_list=[],
            returns=None,
        )

        mod = _ast.Module(body=[func_def], type_ignores=[])
        _ast.fix_missing_locations(mod)
        code = compile(mod, f"<macro:{name}>", "exec")
        ns: dict[str, Any] = {}
        exec(code, _macro_exec_ns, ns)
        fn = ns[name]

        if is_staticmethod:
            fn = staticmethod(fn)

        return fn

    # -- Source-based quoting --

    def _wrap_quote_error(self, source: str, e: Exception) -> MacroError:
        lines = source.splitlines()
        if isinstance(e, SyntaxError) and e.lineno:
            lineno = e.lineno - 1
            bad = lines[lineno].strip() if 0 <= lineno < len(lines) else lines[0]
            return MacroError(f"syntax error in quoted source: `{bad}`")
        preview = lines[0] if lines else ""
        if len(preview) > 60:
            preview = preview[:57] + "..."
        return MacroError(f"{e} (source: `{preview}`)")

    def quote(self, source: str) -> list[_ast.stmt]:
        """Parse Python statements from a source string."""
        source = _textwrap.dedent(source).strip()
        try:
            tree = _ast.parse(source)
        except SyntaxError as e:
            raise self._wrap_quote_error(source, e) from None
        return tree.body

    def quote_expr(self, source: str) -> _ast.expr:
        """Parse a single Python expression from a source string."""
        source = _textwrap.dedent(source).strip()
        try:
            tree = _ast.parse(source, mode="eval")
        except SyntaxError as e:
            raise self._wrap_quote_error(source, e) from None
        return tree.body

    def quote_fun(self, source: str) -> Any:
        """Parse and compile a function definition from a source string."""
        source = _textwrap.dedent(source).strip()
        try:
            tree = _ast.parse(source)
        except SyntaxError as e:
            raise self._wrap_quote_error(source, e) from None
        funcs = [n for n in tree.body if isinstance(n, _ast.FunctionDef)]
        if len(funcs) != 1:
            raise MacroError(
                f"quote_fun: expected exactly 1 function definition, got {len(funcs)} "
                f"(source: `{source.splitlines()[0]}`)")
        _ast.fix_missing_locations(tree)
        code = compile(tree, f"<macro:{funcs[0].name}>", "exec")
        ns: dict[str, Any] = {}
        exec(code, _macro_exec_ns, ns)
        return ns[funcs[0].name]


# Module-level singleton
ast = AstBuilder()


# ===================================================================
# TypeBuilder
# ===================================================================

class TypeBuilder:
    """Builder for CpyType objects."""

    def named(self, name: str) -> CpyType:
        return CpyNamedType(name, value_type=True)

    def own(self, inner: CpyType) -> CpyType:
        return CpyOwnType(inner)

    def optional(self, inner: CpyType) -> CpyType:
        return CpyOptionalType(inner)

    def list(self, element_type: CpyType) -> CpyType:
        return CpyListType(element_type)

    def dict(self, key_type: CpyType, value_type: CpyType) -> CpyType:
        return CpyDictType(key_type, value_type)

    def tuple(self, element_types: tuple[CpyType, ...] | list[CpyType]) -> CpyType:
        if isinstance(element_types, list):
            element_types = tuple(element_types)
        return CpyTupleType(element_types)

    def union(self, member_types: tuple[CpyType, ...] | list[CpyType]) -> CpyType:
        if isinstance(member_types, list):
            member_types = tuple(member_types)
        return CpyUnionType(member_types)

    @property
    def void(self) -> CpyType:
        return _VOID

    @property
    def str(self) -> CpyType:
        return _STR

    @property
    def str_view(self) -> CpyType:
        return _STRVIEW

    @property
    def bool(self) -> CpyType:
        return _BOOL

    @property
    def float(self) -> CpyType:
        return _FLOAT

    @property
    def float64(self) -> CpyType:
        return _FLOAT

    @property
    def float32(self) -> CpyType:
        return _FLOAT32

    @property
    def bigint(self) -> CpyType:
        return _BIGINT

    @property
    def int8(self) -> CpyType:
        return _INT8

    @property
    def int16(self) -> CpyType:
        return _INT16

    @property
    def int32(self) -> CpyType:
        return _INT32

    @property
    def int64(self) -> CpyType:
        return _INT64

    @property
    def uint8(self) -> CpyType:
        return _UINT8

    @property
    def uint16(self) -> CpyType:
        return _UINT16

    @property
    def uint32(self) -> CpyType:
        return _UINT32

    @property
    def uint64(self) -> CpyType:
        return _UINT64


# Module-level singleton
types = TypeBuilder()


# ===================================================================
# Decorators -- class_macro / call_macro
# ===================================================================

def _apply_class_macro(
    macro_fn: Callable, cls: type, kwargs: dict[str, Any],
) -> type:
    """Run a class macro on a Python class and apply mutations."""
    # Make class available in exec namespace before running macro
    # (needed for super() and self-referencing constructors)
    _macro_exec_ns[cls.__name__] = cls
    _class_registry[cls.__name__] = cls

    # Also add the class's module globals so that generated code can
    # reference types from the user's module (e.g. enum types, other
    # model classes referenced in annotations).
    mod = _sys.modules.get(cls.__module__)
    if mod is not None:
        for k, v in vars(mod).items():
            if not k.startswith("_") and k not in _macro_exec_ns:
                _macro_exec_ns[k] = v

    cls_info = ClassInfo(cls)
    ast.reset_tmp_counter()
    macro_fn(cls_info, **kwargs)
    cls_info.apply_to_record()
    return cls


def class_macro(fn: Callable) -> Callable:
    """Mark a function as a class macro.

    Under CPython this wraps the function into a real decorator that
    can be used as ``@decorator`` or ``@decorator(kwarg=...)``.
    """
    fn._is_class_macro = True  # type: ignore[attr-defined]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and isinstance(args[0], type) and not kwargs:
            # @decorator  (no parentheses)
            return _apply_class_macro(fn, args[0], {})
        if not args:
            # @decorator() or @decorator(kwarg=val)
            captured = kwargs

            def decorator(cls: type) -> type:
                return _apply_class_macro(fn, cls, captured)
            return decorator
        raise TypeError(f"Invalid usage of @{fn.__name__}")

    wrapper._is_class_macro = True  # type: ignore[attr-defined]
    wrapper._macro_fn = fn  # type: ignore[attr-defined]
    return wrapper


def call_macro(fn: Callable) -> Callable:
    """Mark a function as a call-site macro.

    Under CPython this wraps the function so that runtime calls:
      1. Create placeholder AST names for each argument
      2. Build MacroArg wrappers with runtime TypeInfo
      3. Call the macro to get a replacement AST expression
      4. compile() + eval() the result with arguments bound

    The compiled code object is cached by (macro_name, arg_type_names,
    kwarg_keys) so repeated calls like asdict(point) with the same type
    skip macro expansion and compile entirely.
    """
    fn._is_call_macro = True  # type: ignore[attr-defined]
    code_cache: dict[tuple, Any] = {}

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Build cache key from argument types and kwarg keys
        arg_type_names = tuple(type(v).__name__ for v in args)
        kw_key = tuple(sorted(
            (k, v if isinstance(v, (str, int, float, bool)) else type(v).__name__)
            for k, v in kwargs.items()
        ))
        cache_key = (arg_type_names, kw_key)

        code = code_cache.get(cache_key)
        if code is None:
            # Build MacroArg for each positional argument
            macro_args: list[MacroArg] = []
            for i, val in enumerate(args):
                placeholder = f"__cpy_arg_{i}"
                macro_args.append(MacroArg(
                    expr=_ast.Name(id=placeholder, ctx=_ast.Load()),
                    type=TypeInfo.from_runtime_value(val),
                ))

            # Extract simple-typed kwargs, wrap others as MacroArg
            macro_kwargs: dict[str, Any] = {}
            for k, v in kwargs.items():
                if isinstance(v, (str, int, float, bool)):
                    macro_kwargs[k] = v
                else:
                    placeholder = f"__cpy_kw_{k}"
                    macro_kwargs[k] = MacroArg(
                        expr=_ast.Name(id=placeholder, ctx=_ast.Load()),
                        type=TypeInfo.from_runtime_value(v),
                    )

            ctx = CallMacroContext()
            ast.reset_tmp_counter()
            result_expr = fn(ctx, *macro_args, **macro_kwargs)

            expr_mod = _ast.Expression(body=result_expr)
            _ast.fix_missing_locations(expr_mod)
            code = compile(expr_mod, f"<call_macro:{fn.__name__}>", "eval")
            code_cache[cache_key] = code

        # Bind runtime values to placeholder names and eval
        bindings: dict[str, Any] = {}
        for i, val in enumerate(args):
            bindings[f"__cpy_arg_{i}"] = val
        for k, v in kwargs.items():
            if not isinstance(v, (str, int, float, bool)):
                bindings[f"__cpy_kw_{k}"] = v
        return eval(code, {**_macro_exec_ns, **bindings})

    wrapper._is_call_macro = True  # type: ignore[attr-defined]
    wrapper._macro_fn = fn  # type: ignore[attr-defined]
    return wrapper


# ===================================================================
# Helpers: build_init, build_eq
# ===================================================================

def build_init(
    cls: ClassInfo,
    parent_fields: list[FieldInfo],
    own_fields: list[FieldInfo],
) -> Any:
    """Build a synthetic __init__ method from field lists."""
    params: list[tuple[str, CpyType]] = []
    defaults: list[_ast.expr | None] = []
    body: list[_ast.stmt] = []

    # Parent fields -- forwarded via super().__init__()
    for fld in parent_fields:
        ptype = fld.type._tpy_type or CpyNamedType(fld.type.name)
        params.append((fld.name, ptype))
        defaults.append(fld.default_expr)

    if parent_fields:
        super_args = [ast.name(fld.name) for fld in parent_fields]
        # Use explicit super(ClassName, self) for exec'd methods
        super_node = _ast.Call(
            func=_ast.Name(id="super", ctx=_ast.Load()),
            args=[
                _ast.Name(id=cls.name, ctx=_ast.Load()),
                _ast.Name(id="self", ctx=_ast.Load()),
            ],
            keywords=[],
        )
        super_init = _ast.Call(
            func=_ast.Attribute(value=super_node, attr="__init__",
                                ctx=_ast.Load()),
            args=super_args, keywords=[],
        )
        body.append(_ast.Expr(value=super_init))

    # Own fields
    frozen = getattr(cls, "is_frozen", False)
    for fld in own_fields:
        ptype = fld.type._tpy_type or CpyNamedType(fld.type.name)
        params.append((fld.name, ptype))

        if fld.is_factory_default:
            # Use _FACTORY_MISSING sentinel to avoid colliding with None
            defaults.append(_ast.Name(id="_FACTORY_MISSING", ctx=_ast.Load()))
            factory_check = _ast.If(
                test=_ast.Compare(
                    left=_ast.Name(id=fld.name, ctx=_ast.Load()),
                    ops=[_ast.Is()],
                    comparators=[_ast.Name(id="_FACTORY_MISSING", ctx=_ast.Load())],
                ),
                body=[_ast.Assign(
                    targets=[_ast.Name(id=fld.name, ctx=_ast.Store())],
                    value=fld.default_expr,
                )],
                orelse=[],
            )
            body.append(factory_check)
        else:
            defaults.append(fld.default_expr)

        if frozen:
            # Frozen classes: use object.__setattr__ to bypass __setattr__
            body.append(_ast.Expr(value=_ast.Call(
                func=_ast.Attribute(
                    value=_ast.Name(id="object", ctx=_ast.Load()),
                    attr="__setattr__", ctx=_ast.Load(),
                ),
                args=[
                    _ast.Name(id="self", ctx=_ast.Load()),
                    _ast.Constant(value=fld.name),
                    _ast.Name(id=fld.name, ctx=_ast.Load()),
                ],
                keywords=[],
            )))
        else:
            body.append(ast.assign(
                ast.field_access(ast.name("self"), fld.name),
                ast.name(fld.name),
            ))

    return ast.function(
        "__init__", params, _VOID, body,
        is_method=True, defaults=defaults,
    )


def build_eq(cls: ClassInfo, all_fields: list[FieldInfo]) -> Any:
    """Build a synthetic __eq__ method from field list."""
    if not all_fields:
        return ast.function(
            "__eq__", [("other", CpyNamedType(cls.name))], _BOOL,
            [ast.return_(ast.bool_lit(True))],
            is_method=True,
        )

    comparisons = [
        ast.binop(
            ast.field_access(ast.name("self"), fld.name),
            "==",
            ast.field_access(ast.name("other"), fld.name),
        )
        for fld in all_fields
    ]
    eq_expr: _ast.expr = comparisons[0]
    for cmp in comparisons[1:]:
        eq_expr = ast.binop(eq_expr, "&&", cmp)

    return ast.function(
        "__eq__", [("other", CpyNamedType(cls.name))], _BOOL,
        [ast.return_(eq_expr)],
        is_method=True,
    )


# ===================================================================
# expr_to_cpp_default -- no-op under CPython
# ===================================================================

def expr_to_cpp_default(expr: Any) -> str | None:
    """Under CPython, C++ default string generation is not needed."""
    return None


# ===================================================================
# macro_deps -- populate exec namespace with runtime imports
# ===================================================================

def macro_deps(*args: str | tuple[str, ...]) -> None:
    """Declare modules that macro-generated code depends on.

    Under CPython, this imports the modules and populates the shared
    exec namespace so that compiled macro functions can reference them.
    Under tpyc, macro_loader resets state between modules; under CPython
    each module is only imported once (sys.modules caching), so no guard
    is needed.
    """
    deps: dict[str, list[str] | None] = {}
    for arg in args:
        if isinstance(arg, str):
            deps[arg] = None
        else:
            module = arg[0]
            names = list(arg[1:])
            if module in deps and deps[module] is None:
                pass
            elif module in deps and deps[module] is not None:
                deps[module] = list(set(deps[module]) | set(names))
            else:
                deps[module] = names

    # Actually import and populate the namespace
    for mod_name, names in deps.items():
        try:
            mod = importlib.import_module(mod_name)
            if names is None:
                _macro_exec_ns.update(
                    {k: v for k, v in vars(mod).items()
                     if not k.startswith("_")}
                )
            else:
                for n in names:
                    if hasattr(mod, n):
                        _macro_exec_ns[n] = getattr(mod, n)
            # Also add the top-level package so that qualified names
            # (e.g. tplib.json.parser.JsonError) resolve via attribute access
            top = mod_name.split(".")[0]
            _macro_exec_ns[top] = importlib.import_module(top)
        except ImportError:
            pass  # Module not available under CPython -- that's ok


# ===================================================================
# Type aliases -- match what macro modules import
# ===================================================================

Expr = Any          # python ast.expr under CPython
Stmt = Any          # python ast.stmt under CPython
Function = Any      # compiled function object under CPython
Type = CpyType
MatchCase = Any
ExceptHandler = Any
Pattern = Any
ComprehensionGenerator = Any
