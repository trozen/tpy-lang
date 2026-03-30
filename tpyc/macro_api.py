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
    TpyType, NamedType, OwnType, VoidType, BoolType, StrType, StrViewType,
    FloatType, Float32Type, FixedIntType, OptionalType, ListType, DictType,
    TupleType, EnumType, BigIntType, UnionType,
    FunctionInfo, FieldInfo as InternalFieldInfo,
    INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    ALL_FIXED_INTS,
)
from .typesys import (
    VOID as _VOID, STR as _STR, STRVIEW as _STRVIEW, BOOL as _BOOL,
    FLOAT as _FLOAT, FLOAT32 as _FLOAT32, BIGINT as _BIGINT,
)
from .parse import (
    TpyRecord, TpyFunction, TpyExpr, TpyStmt,
    TpyAssign, TpyFieldAccess, TpyName, TpyBinOp, TpyReturn,
    TpyMethodCall, TpyCall, TpyExprStmt,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral,
    TpyNoneLiteral, TpyUnaryOp,
    TpyVarDecl, TpyTupleUnpack, TpyIf, TpyWhile, TpyForEach,
    TpyRaise, TpyAssert, TpyExceptHandler, TpyTry,
    TpyMatch, TpyMatchCase, TpyLiteralPattern, TpyWildcardPattern,
    TpySubscript, TpyTupleLiteral, TpyArrayLiteral, TpyDictLiteral,
    TpyListComprehension, TpyComprehensionGenerator, TpyDictComprehension,
    TpyPattern,
)

# Public type aliases for macro module type annotations
Expr = TpyExpr
Stmt = TpyStmt
Function = TpyFunction
Type = TpyType
MatchCase = TpyMatchCase
ExceptHandler = TpyExceptHandler
Pattern = TpyPattern
ComprehensionGenerator = TpyComprehensionGenerator

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
    def raw_type(self) -> TpyType:
        """Access the underlying compiler type object (escape hatch)."""
        assert self._tpy_type is not None, "raw_type on TypeInfo with no underlying type"
        return self._tpy_type

    @property
    def is_str(self) -> bool:
        """True if this is any string type (str, String, StrView)."""
        from .typesys import is_any_str_type
        return self._tpy_type is not None and is_any_str_type(self._tpy_type)

    @property
    def is_int(self) -> bool:
        return isinstance(self._tpy_type, FixedIntType)

    @property
    def is_int32(self) -> bool:
        return (isinstance(self._tpy_type, FixedIntType)
                and self._tpy_type.bits == 32 and self._tpy_type.signed)

    @property
    def is_float(self) -> bool:
        return isinstance(self._tpy_type, (FloatType, Float32Type))

    @property
    def is_float32(self) -> bool:
        return isinstance(self._tpy_type, Float32Type)

    @property
    def is_bool(self) -> bool:
        return isinstance(self._tpy_type, BoolType)

    @property
    def is_bigint(self) -> bool:
        return isinstance(self._tpy_type, BigIntType)

    @property
    def is_enum(self) -> bool:
        return isinstance(self._tpy_type, EnumType)

    @property
    def is_list(self) -> bool:
        return isinstance(self._tpy_type, ListType)

    @property
    def is_dict(self) -> bool:
        return isinstance(self._tpy_type, DictType)

    @property
    def is_tuple(self) -> bool:
        return isinstance(self._tpy_type, TupleType)

    @property
    def enum_name(self) -> str:
        """Get the enum class name (only valid when is_enum is True)."""
        assert isinstance(self._tpy_type, EnumType)
        return self._tpy_type.name

    @property
    def int_type_name(self) -> str:
        """Get the fixed-int type name e.g. 'Int32' (only valid when is_int)."""
        assert isinstance(self._tpy_type, FixedIntType)
        return str(self._tpy_type)

    def unwrap_optional(self) -> TypeInfo | None:
        """If this is Optional[T], return TypeInfo for T. Otherwise None."""
        if self.is_optional and isinstance(self._tpy_type, OptionalType):
            return TypeInfo.from_tpy_type(self._tpy_type.inner)
        return None

    @property
    def tuple_element_types(self) -> list[TypeInfo]:
        """Get element types of a tuple (only valid when is_tuple)."""
        assert isinstance(self._tpy_type, TupleType)
        return [TypeInfo.from_tpy_type(et) for et in self._tpy_type.element_types]

    @staticmethod
    def from_tpy_type(typ: TpyType) -> TypeInfo:
        from .typesys import SetType, ArrayType
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

    def is_parent_frozen(self) -> tuple[bool, str] | None:
        """Check if the parent @dataclass is frozen.

        Returns (is_frozen, parent_name) or None if no dataclass parent.
        """
        for base in self._record.bases:
            if not isinstance(base, NamedType):
                continue
            parent_info = self._ctx.registry.get_record(base.name)
            if parent_info is not None and parent_info.is_dataclass:
                return (parent_info.is_frozen, base.name)
        return None

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
) -> Function:
    """Build a synthetic __init__ method from field lists.

    Mirrors the exact AST structure that the hardcoded @dataclass synthesis
    produced, ensuring identical C++ output.
    """
    params: list[tuple[str, TpyType]] = []
    defaults: list[Expr | None] = []
    body: list[Stmt] = []

    # Parent fields come first; forwarded via super().__init__()
    for fld in parent_fields:
        param_type = fld.type.raw_type
        if not param_type.is_value_type():
            param_type = OwnType(param_type)
        params.append((fld.name, param_type))
        defaults.append(fld.default_expr)

    if parent_fields:
        super_args = [ast.name(fld.name) for fld in parent_fields]
        super_call = ast.method_call(
            ast.call("super"), "__init__", super_args,
        )
        body.append(ast.expr_stmt(super_call))

    # Own fields
    for fld in own_fields:
        param_type = fld.type.raw_type
        if not param_type.is_value_type():
            param_type = OwnType(param_type)
        params.append((fld.name, param_type))
        defaults.append(fld.default_expr)
        body.append(ast.assign(
            ast.field_access(ast.name("self"), fld.name),
            ast.name(fld.name),
        ))

    return ast.function(
        "__init__", params, _VOID, body,
        is_method=True, defaults=defaults,
    )


def build_eq(cls: ClassInfo, all_fields: list[FieldInfo]) -> Function:
    """Build a synthetic __eq__ method from field list.

    Mirrors the exact AST structure that the hardcoded @dataclass synthesis
    produced, ensuring identical C++ output.
    """
    other_type = NamedType(cls.name)
    comparisons = [
        ast.binop(
            ast.field_access(ast.name("self"), fld.name),
            "==",
            ast.field_access(ast.name("other"), fld.name),
        )
        for fld in all_fields
    ]
    eq_expr: Expr = comparisons[0]
    for cmp in comparisons[1:]:
        eq_expr = ast.binop(eq_expr, "&&", cmp)

    return ast.function(
        "__eq__", [("other", other_type)], _BOOL, [ast.return_(eq_expr)],
        is_method=True,
    )


# ---------------------------------------------------------------------------
# AstBuilder -- AST node construction for macro modules
# ---------------------------------------------------------------------------

class AstBuilder:
    """Builder for AST nodes. Macro modules use this instead of importing
    compiler-internal node types directly.

    Usage: ``from tpyc.macro_api import ast`` then ``ast.name("x")``.
    """

    def __init__(self) -> None:
        self._tmp_counter = 0

    def reset_tmp_counter(self) -> None:
        """Reset the fresh-name counter. Called automatically before each macro invocation."""
        self._tmp_counter = 0

    def fresh_tmp(self, hint: str = "v") -> str:
        """Generate a unique temporary variable name."""
        self._tmp_counter += 1
        return f"__{hint}_{self._tmp_counter}"

    # -- Expressions --

    def name(self, n: str) -> Expr:
        return TpyName(n)

    def call(self, func: str, args: list[Expr] | None = None,
             call_type: TpyType | None = None) -> Expr:
        return TpyCall(func=TpyName(func), args=args or [], call_type=call_type)

    def method_call(self, obj: Expr, method: str, args: list[Expr] | None = None) -> Expr:
        return TpyMethodCall(obj=obj, method=method, args=args or [])

    def field_access(self, obj: Expr, field: str) -> Expr:
        return TpyFieldAccess(obj=obj, field=field)

    def binop(self, left: Expr, op: str, right: Expr) -> Expr:
        return TpyBinOp(left=left, op=op, right=right)

    def subscript(self, obj: Expr, index: Expr) -> Expr:
        return TpySubscript(obj=obj, index=index)

    def unary(self, op: str, operand: Expr) -> Expr:
        return TpyUnaryOp(op=op, operand=operand)

    def str_lit(self, s: str) -> Expr:
        return TpyStrLiteral(value=s)

    def int_lit(self, n: int) -> Expr:
        return TpyIntLiteral(value=n)

    def float_lit(self, f: float) -> Expr:
        return TpyFloatLiteral(value=f)

    def bool_lit(self, b: bool) -> Expr:
        return TpyBoolLiteral(value=b)

    def none_lit(self) -> Expr:
        return TpyNoneLiteral()

    def list_lit(self, elements: list[Expr] | None = None) -> Expr:
        return TpyArrayLiteral(elements=elements or [])

    def dict_lit(self, keys: list[Expr] | None = None, values: list[Expr] | None = None) -> Expr:
        return TpyDictLiteral(keys=keys or [], values=values or [])

    def tuple_lit(self, elements: list[Expr]) -> Expr:
        return TpyTupleLiteral(elements=elements)

    def list_comprehension(self, element_expr: Expr,
                           generator: TpyComprehensionGenerator) -> Expr:
        return TpyListComprehension(element_expr=element_expr, generator=generator)

    def dict_comprehension(self, key_expr: Expr, value_expr: Expr,
                           generator: TpyComprehensionGenerator) -> Expr:
        return TpyDictComprehension(key_expr=key_expr, value_expr=value_expr,
                                    generator=generator)

    def comprehension_generator(self, var: str, iterable: Expr,
                                conditions: list[Expr] | None = None,
                                unpack_vars: list[str | None] | None = None) -> TpyComprehensionGenerator:
        return TpyComprehensionGenerator(var=var, iterable=iterable,
                                         conditions=conditions or [],
                                         unpack_vars=unpack_vars)

    # -- Statements --

    def var_decl(self, name: str, type: TpyType | None = None,
                 init: Expr | None = None) -> Stmt:
        return TpyVarDecl(name=name, type=type, init=init)

    def assign(self, target: Expr, value: Expr) -> Stmt:
        return TpyAssign(target=target, value=value)

    def expr_stmt(self, expr: Expr) -> Stmt:
        return TpyExprStmt(expr=expr)

    def return_(self, value: Expr | None = None) -> Stmt:
        return TpyReturn(value=value)

    def if_(self, condition: Expr, then_body: list[Stmt],
            else_body: list[Stmt] | None = None) -> Stmt:
        return TpyIf(condition=condition, then_body=then_body,
                      else_body=else_body or [])

    def while_(self, condition: Expr, body: list[Stmt]) -> Stmt:
        return TpyWhile(condition=condition, body=body)

    def for_each(self, var: str, iterable: Expr, body: list[Stmt],
                 is_tuple_unpack: bool = False) -> Stmt:
        return TpyForEach(var=var, iterable=iterable, body=body,
                          is_tuple_unpack=is_tuple_unpack)

    def raise_(self, exception_type: str) -> Stmt:
        return TpyRaise(exception_type=exception_type)

    def assert_(self, condition: Expr, message: Expr | None = None) -> Stmt:
        return TpyAssert(condition=condition, message=message)

    def try_(self, try_body: list[Stmt], handlers: list | None = None,
             else_body: list[Stmt] | None = None,
             finally_body: list[Stmt] | None = None) -> Stmt:
        return TpyTry(try_body=try_body, handlers=handlers or [],
                       else_body=else_body or [], finally_body=finally_body or [])

    def except_handler(self, exception_type: str | None = None,
                       binding: str | None = None,
                       body: list[Stmt] | None = None) -> TpyExceptHandler:
        return TpyExceptHandler(exception_type=exception_type,
                                binding=binding, body=body or [])

    def match(self, subject: Expr, cases: list) -> Stmt:
        return TpyMatch(subject=subject, cases=cases)

    def match_case(self, pattern: Any, body: list[Stmt],
                   guard: Expr | None = None) -> TpyMatchCase:
        return TpyMatchCase(pattern=pattern, guard=guard, body=body)

    def literal_pattern(self, value: int | float | str | bool | None) -> TpyLiteralPattern:
        return TpyLiteralPattern(value=value)

    def wildcard_pattern(self) -> TpyWildcardPattern:
        return TpyWildcardPattern()

    def tuple_unpack(self, targets: list[str | None], value: Expr) -> Stmt:
        return TpyTupleUnpack(targets=targets, value=value)

    # -- Introspection --

    def get_field_name(self, expr: Expr) -> str | None:
        """If expr is a field access node, return the field name. Otherwise None."""
        if isinstance(expr, TpyFieldAccess):
            return expr.field
        return None

    def get_name(self, expr: Expr) -> str | None:
        """If expr is a simple name node, return the name. Otherwise None."""
        if isinstance(expr, TpyName):
            return expr.name
        return None

    # -- Functions --

    def function(self, name: str, params: list[tuple[str, TpyType]],
                 return_type: TpyType, body: list[Stmt], *,
                 is_method: bool = False, is_staticmethod: bool = False,
                 is_readonly: bool = False, readonly_opt_out: bool = False,
                 error_return: str | None = None,
                 defaults: list[Expr | None] | None = None) -> Function:
        return TpyFunction(
            name=name, params=params, return_type=return_type, body=body,
            is_method=is_method, is_staticmethod=is_staticmethod,
            is_readonly=is_readonly, readonly_opt_out=readonly_opt_out,
            error_return=error_return, defaults=defaults or [],
        )


# Module-level singleton
ast = AstBuilder()


# ---------------------------------------------------------------------------
# TypeBuilder -- type construction for macro modules
# ---------------------------------------------------------------------------

class TypeBuilder:
    """Builder for TpyType objects. Macro modules use this instead of importing
    compiler-internal type classes directly.

    Usage: ``from tpyc.macro_api import types`` then ``types.named("Foo")``.
    """

    # -- Constructors --

    def named(self, name: str) -> TpyType:
        return NamedType(name)

    def own(self, inner: TpyType) -> TpyType:
        return OwnType(inner)

    def optional(self, inner: TpyType) -> TpyType:
        return OptionalType(inner)

    def list(self, element_type: TpyType) -> TpyType:
        return ListType(element_type)

    def dict(self, key_type: TpyType, value_type: TpyType) -> TpyType:
        return DictType(key_type, value_type)

    def tuple(self, element_types: tuple[TpyType, ...] | list[TpyType]) -> TpyType:
        if isinstance(element_types, list):
            element_types = tuple(element_types)
        return TupleType(element_types)

    def union(self, member_types: tuple[TpyType, ...] | list[TpyType]) -> TpyType:
        if isinstance(member_types, list):
            member_types = tuple(member_types)
        return UnionType(member_types)

    # -- Singleton types --

    @property
    def void(self) -> TpyType:
        return _VOID

    @property
    def str(self) -> TpyType:
        return _STR

    @property
    def str_view(self) -> TpyType:
        return _STRVIEW

    @property
    def bool(self) -> TpyType:
        return _BOOL

    # -- Fixed-width integers --

    @property
    def int8(self) -> TpyType:
        return INT8

    @property
    def int16(self) -> TpyType:
        return INT16

    @property
    def int32(self) -> TpyType:
        return INT32

    @property
    def int64(self) -> TpyType:
        return INT64

    @property
    def uint8(self) -> TpyType:
        return UINT8

    @property
    def uint16(self) -> TpyType:
        return UINT16

    @property
    def uint32(self) -> TpyType:
        return UINT32

    @property
    def uint64(self) -> TpyType:
        return UINT64

    # -- Floating point --

    @property
    def float(self) -> TpyType:
        return _FLOAT

    @property
    def float64(self) -> TpyType:
        return _FLOAT

    @property
    def float32(self) -> TpyType:
        return _FLOAT32

    # -- Arbitrary precision --

    @property
    def bigint(self) -> TpyType:
        return _BIGINT


# Module-level singleton
types = TypeBuilder()


# ---------------------------------------------------------------------------
# AST builder helpers for common macro patterns
# ---------------------------------------------------------------------------

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
    if isinstance(expr, TpyCall) and expr.func_name in _FIXED_INT_NAMES:
        if not expr.args:
            return "0"
        if len(expr.args) == 1:
            return expr_to_cpp_default(expr.args[0])
    return None
