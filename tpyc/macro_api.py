"""
TurboPython Macro API

Public API for macro modules. Macro modules import from this package to inspect
and modify class definitions during compilation.

Macro modules are normal .py files with a ``# tpy: macro_module`` directive.
They are executed via CPython during compilation (not compiled to C++).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, NoReturn, TYPE_CHECKING

from .typesys import (
    TpyType, NamedType, OwnType, VoidType, BoolType, StrType, FunctionInfo,
    FieldInfo as InternalFieldInfo,
    UINT64, ALL_FIXED_INTS,
)
from .parse import (
    TpyRecord, TpyFunction, TpyExpr, TpyStmt,
    TpyAssign, TpyFieldAccess, TpyName, TpyBinOp, TpyReturn,
    TpyMethodCall, TpyCall, TpyExprStmt,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral,
    TpyNoneLiteral, TpyUnaryOp,
)

if TYPE_CHECKING:
    from .sema.context import SemanticContext


_FIXED_INT_NAMES: frozenset[str] = frozenset(str(t) for t in ALL_FIXED_INTS)


# ---------------------------------------------------------------------------
# MacroError -- raised by macros to report compile errors
# ---------------------------------------------------------------------------

class MacroError(Exception):
    """Raised by macro code to report a compile error.

    The macro infrastructure catches this and converts it to a SemanticError
    with the appropriate source location. If ``loc`` is provided, it overrides
    the default location (the decorator/call site).
    """
    def __init__(self, msg: str, loc: Any = None) -> None:
        super().__init__(msg)
        self.loc = loc


# ---------------------------------------------------------------------------
# Decorator for marking macro functions
# ---------------------------------------------------------------------------

def class_macro(fn: Callable) -> Callable:
    """Mark a function as a class macro.

    Class macros receive a ClassInfo and modify it (add methods, set flags).
    """
    fn._is_class_macro = True
    return fn


def call_macro(fn: Callable) -> Callable:
    """Mark a function as a call-site macro.

    Call macros receive a CallMacroContext + MacroArg per argument.
    They return a replacement TpyExpr that sema analyzes.
    """
    fn._is_call_macro = True
    return fn


# ---------------------------------------------------------------------------
# Macro module dependency declaration
# ---------------------------------------------------------------------------

# Set by macro_deps(), read by MacroRegistry.load_module().
_pending_macro_deps: dict[str, list[str] | None] | None = None


def macro_deps(*args: str | tuple[str, ...]) -> None:
    """Declare modules that macro-generated code depends on.

    Called at module level in a macro module. Each argument is either:
    - A module name string (all exports available): ``"tplib.json.parser"``
    - A tuple of ``(module, name1, name2, ...)`` for specific names:
      ``("tpy", "try_parse")``

    Example::

        macro_deps(
            "tplib.json.parser",
            "tplib.json.writer",
            ("tpy", "try_parse"),
        )
    """
    global _pending_macro_deps
    if _pending_macro_deps is not None:
        raise MacroError("macro_deps() called twice in the same module")
    deps: dict[str, list[str] | None] = {}
    for arg in args:
        if isinstance(arg, str):
            deps[arg] = None
        else:
            module = arg[0]
            names = list(arg[1:])
            if module in deps and deps[module] is None:
                pass  # None (all exports) already set
            elif module in deps and deps[module] is not None:
                deps[module] = list(set(deps[module]) | set(names))
            else:
                deps[module] = names
    _pending_macro_deps = deps


# ---------------------------------------------------------------------------
# MacroArg -- argument wrapper for call-site macros
# ---------------------------------------------------------------------------

@dataclass
class MacroArg:
    """Argument passed to a call-site macro: the AST expression + resolved type."""
    expr: TpyExpr
    type: TypeInfo


# ---------------------------------------------------------------------------
# CallMacroContext -- context for call-site macros
# ---------------------------------------------------------------------------

class CallMacroContext:
    """Context passed to call-site macros for type introspection and diagnostics."""

    def __init__(self, ctx: SemanticContext, loc: Any = None) -> None:
        self._ctx = ctx
        self._loc = loc

    def get_record_fields(self, name: str) -> list[FieldInfo] | None:
        """Get dataclass fields for a record type, or None if not a dataclass."""
        record_info = self._ctx.registry.get_record(name)
        if record_info is None or not record_info.is_dataclass:
            return None
        return [FieldInfo.from_internal(f) for f in record_info.dataclass_fields]

    def is_dataclass(self, name: str) -> bool:
        """Check if a type name refers to a registered @dataclass."""
        record_info = self._ctx.registry.get_record(name)
        return record_info is not None and record_info.is_dataclass

    def get_iterable_element_type(self, type_info: TypeInfo) -> TypeInfo | None:
        """Get the element type of an iterable type, or None if not iterable.

        Handles built-in containers, user types with __iter__, and protocols.
        """
        if type_info._tpy_type is None:
            return None
        from .sema.list_literals import IterableHelper
        helper = IterableHelper(self._ctx)
        elem = helper.get_iterable_element_type_or_none(type_info._tpy_type)
        if elem is not None:
            return TypeInfo.from_tpy_type(elem)
        return None

    def warning(self, msg: str, loc: Any = None) -> None:
        """Emit a compiler warning."""
        self._ctx.warning_from_loc(msg, loc or self._loc)

    def error(self, msg: str, loc: Any = None) -> NoReturn:
        """Raise a compile error."""
        from .sema.diagnostics import SemanticError
        raise SemanticError(msg, loc or self._loc)


# ---------------------------------------------------------------------------
# TypeInfo -- read-only wrapper around TpyType
# ---------------------------------------------------------------------------

@dataclass
class TypeInfo:
    """Read-only type metadata exposed to macros."""
    name: str
    type_args: list[TypeInfo] = field(default_factory=list)
    is_optional: bool = False
    is_value_type: bool = True
    is_record: bool = False
    _tpy_type: TpyType | None = None


    @property
    def is_str(self) -> bool:
        """True if this is any string type (str, String, StrView)."""
        from .typesys import is_any_str_type
        return self._tpy_type is not None and is_any_str_type(self._tpy_type)

    @property
    def is_dict(self) -> bool:
        from .typesys import DictType
        return isinstance(self._tpy_type, DictType)

    @property
    def is_tuple(self) -> bool:
        from .typesys import TupleType
        return isinstance(self._tpy_type, TupleType)

    @staticmethod
    def from_tpy_type(typ: TpyType) -> TypeInfo:
        from .typesys import OptionalType, ListType, DictType, SetType, ArrayType, TupleType
        type_args: list[TypeInfo] = []
        if isinstance(typ, TupleType):
            type_args = [TypeInfo.from_tpy_type(et) for et in typ.element_types]
        elif isinstance(typ, NamedType) and typ.type_args:
            type_args = [TypeInfo.from_tpy_type(ta) for ta in typ.type_args]
        elif isinstance(typ, ListType):
            type_args = [TypeInfo.from_tpy_type(typ.element_type)]
        elif isinstance(typ, DictType):
            type_args = [TypeInfo.from_tpy_type(typ.key_type),
                         TypeInfo.from_tpy_type(typ.value_type)]
        elif isinstance(typ, SetType):
            type_args = [TypeInfo.from_tpy_type(typ.element_type)]
        elif isinstance(typ, ArrayType):
            type_args = [TypeInfo.from_tpy_type(typ.element_type)]
        return TypeInfo(
            name=typ.__class__.__name__ if not isinstance(typ, NamedType) else typ.name,
            type_args=type_args,
            is_optional=isinstance(typ, OptionalType),
            is_value_type=typ.is_value_type(),
            is_record=isinstance(typ, NamedType) and typ.is_user_record,
            _tpy_type=typ,
        )


# ---------------------------------------------------------------------------
# FieldInfo -- read-only wrapper around typesys.FieldInfo
# ---------------------------------------------------------------------------

@dataclass
class FieldInfo:
    """Field metadata exposed to macros."""
    name: str
    type: TypeInfo
    has_default: bool
    default_expr: TpyExpr | None = None
    is_factory_default: bool = False
    loc: Any = None
    default_obj: Any = None
    _internal: InternalFieldInfo | None = None

    @staticmethod
    def from_internal(fld: InternalFieldInfo) -> FieldInfo:
        return FieldInfo(
            name=fld.name,
            type=TypeInfo.from_tpy_type(fld.type),
            has_default=fld.default_expr is not None,
            default_expr=fld.default_expr,
            is_factory_default=fld.is_factory_default,
            loc=fld.loc,
            _internal=fld,
        )

    def set_default(self, expr: TpyExpr | None, default_value: str | None = None,
                    is_factory: bool = False) -> None:
        """Replace this field's default expression (updates the underlying record)."""
        self.default_expr = expr
        self.has_default = expr is not None
        self.is_factory_default = is_factory
        if self._internal is not None:
            self._internal.default_expr = expr
            self._internal.default_value = default_value
            self._internal.is_factory_default = is_factory

    def to_internal(self) -> InternalFieldInfo:
        """Return the underlying compiler FieldInfo."""
        if self._internal is not None:
            return self._internal
        assert self.type._tpy_type is not None
        return InternalFieldInfo(
            name=self.name,
            type=self.type._tpy_type,
            default_expr=self.default_expr,
            is_factory_default=self.is_factory_default,
        )


# ---------------------------------------------------------------------------
# MethodInfo -- read-only view of a method
# ---------------------------------------------------------------------------

@dataclass
class MethodInfo:
    """Method metadata exposed to macros (read-only)."""
    name: str
    is_readonly: bool = False
    is_staticmethod: bool = False


# ---------------------------------------------------------------------------
# MethodStub -- for methods whose body is generated by codegen
# ---------------------------------------------------------------------------

@dataclass
class MethodStub:
    """A method signature without body (codegen generates the C++)."""
    name: str
    params: list[tuple[str, TpyType]]
    return_type: TpyType
    is_readonly: bool = False


# ---------------------------------------------------------------------------
# ClassInfo -- mutable wrapper for macro class transformation
# ---------------------------------------------------------------------------

class ClassInfo:
    """Class metadata passed to class macros.

    Macros inspect fields/methods and can add methods, set flags, and
    set dataclass_fields. Mutations are applied back to the TpyRecord
    via ``apply_to_record()``.
    """

    def __init__(
        self,
        record: TpyRecord,
        ctx: SemanticContext,
    ) -> None:
        self._record = record
        self._ctx = ctx
        self._added_methods: list[TpyFunction] = []
        self._method_stubs: list[MethodStub] = []
        self._dataclass_fields: list[InternalFieldInfo] | None = None

        # Populate read-only views
        self.name: str = record.name
        self.type_params: list[str] = list(record.type_params)
        self.fields: list[FieldInfo] = [
            FieldInfo.from_internal(f) for f in record.fields
        ]
        self.parent: TypeInfo | None = (
            TypeInfo.from_tpy_type(record.bases[0])
            if record.bases else None
        )

    # -- Flags (settable by macro, propagated to TpyRecord) --

    @property
    def is_dataclass(self) -> bool:
        return self._record.is_dataclass

    @is_dataclass.setter
    def is_dataclass(self, value: bool) -> None:
        self._record.is_dataclass = value

    @property
    def is_frozen(self) -> bool:
        return self._record.is_frozen

    @is_frozen.setter
    def is_frozen(self, value: bool) -> None:
        self._record.is_frozen = value

    @property
    def is_ordered(self) -> bool:
        return self._record.is_ordered

    @is_ordered.setter
    def is_ordered(self, value: bool) -> None:
        self._record.is_ordered = value

    # -- Read methods --

    @property
    def methods(self) -> list[MethodInfo]:
        """Current methods on the class (including macro-added ones)."""
        result = [
            MethodInfo(
                name=m.name,
                is_readonly=m.is_readonly,
                is_staticmethod=m.is_staticmethod,
            )
            for m in self._record.methods
        ]
        result.extend(
            MethodInfo(
                name=m.name,
                is_readonly=m.is_readonly,
                is_staticmethod=m.is_staticmethod,
            )
            for m in self._added_methods
        )
        return result

    def has_method(self, name: str) -> bool:
        """Check if the class has a method with the given name."""
        for m in self._record.methods:
            if m.name == name:
                return True
        for m in self._added_methods:
            if m.name == name:
                return True
        return False

    def get_parent_fields(self) -> list[FieldInfo]:
        """Get inherited dataclass fields from parent @dataclass (if any).

        Uses the registry to look up parent's dataclass_fields. Parent must
        already be registered (guaranteed by compilation order).
        """
        result: list[FieldInfo] = []
        for base in self._record.bases:
            if not isinstance(base, NamedType):
                continue
            parent_info = self._ctx.registry.get_record(base.name)
            if parent_info is not None and parent_info.is_dataclass:
                result = [FieldInfo.from_internal(f) for f in parent_info.dataclass_fields]
                break
        return result

    # -- Mutation methods --

    def add_method(self, func: TpyFunction) -> None:
        """Add a method from a TpyFunction AST node (power user API)."""
        self._added_methods.append(func)

    def add_method_stub(
        self,
        name: str,
        params: list[tuple[str, TpyType]],
        return_type: TpyType,
        is_readonly: bool = False,
    ) -> None:
        """Register a method signature stub (body generated by codegen)."""
        self._method_stubs.append(MethodStub(
            name=name,
            params=params,
            return_type=return_type,
            is_readonly=is_readonly,
        ))

    def set_dataclass_fields(self, fields: list[FieldInfo]) -> None:
        """Set the all_dc_fields list (parent + own) for codegen."""
        self._dataclass_fields = [f.to_internal() for f in fields]

    def get_method_loc(self, name: str) -> Any:
        """Get the source location of a method by name."""
        for m in self._record.methods:
            if m.name == name:
                return m.loc or self._record.loc
        return self._record.loc

    # -- Diagnostics --

    def warning(self, msg: str, loc: Any = None) -> None:
        """Emit a compiler warning."""
        self._ctx.warning_from_loc(msg, loc or self._record.loc)

    def error(self, msg: str, loc: Any = None) -> NoReturn:
        """Raise a compile error."""
        from .sema.diagnostics import SemanticError
        raise SemanticError(msg, loc or self._record.loc)

    # -- Apply mutations back to TpyRecord --

    def apply_to_record(self) -> None:
        """Write macro mutations back to the TpyRecord."""
        for func in self._added_methods:
            if func.name == "__init__":
                self._record.methods.insert(0, func)
            else:
                self._record.methods.append(func)

    def get_method_stubs(self) -> list[MethodStub]:
        """Return accumulated method stubs for registration."""
        return self._method_stubs

    def get_dataclass_fields(self) -> list[InternalFieldInfo] | None:
        """Return the dataclass fields set by the macro, or None."""
        return self._dataclass_fields


# ---------------------------------------------------------------------------
# AST builder helpers for common macro patterns
# ---------------------------------------------------------------------------

def build_init(
    cls: ClassInfo,
    parent_fields: list[FieldInfo],
    own_fields: list[FieldInfo],
) -> TpyFunction:
    """Build a synthetic __init__ method from field lists.

    Mirrors the exact AST structure that the hardcoded @dataclass synthesis
    produced, ensuring identical C++ output.
    """
    params: list[tuple[str, TpyType]] = []
    defaults: list[TpyExpr | None] = []
    body: list[TpyStmt] = []

    # Parent fields come first; forwarded via super().__init__()
    for fld in parent_fields:
        assert fld.type._tpy_type is not None
        param_type = fld.type._tpy_type
        if not param_type.is_value_type():
            param_type = OwnType(param_type)
        params.append((fld.name, param_type))
        defaults.append(fld.default_expr)

    if parent_fields:
        super_args = [TpyName(fld.name) for fld in parent_fields]
        super_call = TpyMethodCall(
            obj=TpyCall(func="super", args=[]),
            method="__init__",
            args=super_args,
        )
        body.append(TpyExprStmt(expr=super_call))

    # Own fields
    for fld in own_fields:
        assert fld.type._tpy_type is not None
        param_type = fld.type._tpy_type
        if not param_type.is_value_type():
            param_type = OwnType(param_type)
        params.append((fld.name, param_type))
        defaults.append(fld.default_expr)
        body.append(TpyAssign(
            target=TpyFieldAccess(obj=TpyName("self"), field=fld.name),
            value=TpyName(fld.name),
        ))

    return TpyFunction(
        name="__init__",
        params=params,
        return_type=VoidType(),
        body=body,
        is_method=True,
        defaults=defaults,
    )


def build_eq(cls: ClassInfo, all_fields: list[FieldInfo]) -> TpyFunction:
    """Build a synthetic __eq__ method from field list.

    Mirrors the exact AST structure that the hardcoded @dataclass synthesis
    produced, ensuring identical C++ output.
    """
    other_type = NamedType(cls.name)
    comparisons = [
        TpyBinOp(
            left=TpyFieldAccess(obj=TpyName("self"), field=fld.name),
            op="==",
            right=TpyFieldAccess(obj=TpyName("other"), field=fld.name),
        )
        for fld in all_fields
    ]
    eq_expr: TpyExpr = comparisons[0]
    for cmp in comparisons[1:]:
        eq_expr = TpyBinOp(left=eq_expr, op="&&", right=cmp)

    return TpyFunction(
        name="__eq__",
        params=[("other", other_type)],
        return_type=BoolType(),
        body=[TpyReturn(value=eq_expr)],
        is_method=True,
    )


def expr_to_cpp_default(expr: TpyExpr) -> str | None:
    """Convert a TpyExpr to a C++ default value literal string, or None."""
    if isinstance(expr, TpyIntLiteral):
        return str(expr.value)
    if isinstance(expr, TpyFloatLiteral):
        return str(expr.value)
    if isinstance(expr, TpyBoolLiteral):
        return "true" if expr.value else "false"
    if isinstance(expr, TpyStrLiteral):
        escaped = expr.value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        return f'"{escaped}"'
    if isinstance(expr, TpyNoneLiteral):
        return "std::nullopt"
    if isinstance(expr, TpyUnaryOp) and expr.op == "-":
        inner = expr_to_cpp_default(expr.operand)
        if inner is not None:
            return f"-{inner}"
    if isinstance(expr, TpyCall) and expr.func in _FIXED_INT_NAMES:
        if not expr.args:
            return "0"
        if len(expr.args) == 1:
            return expr_to_cpp_default(expr.args[0])
    return None
