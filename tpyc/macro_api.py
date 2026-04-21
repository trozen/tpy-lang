"""
TurboPython Macro API

Public API for macro modules. Macro modules import from this package to inspect
and modify class definitions during compilation.

Macro modules are normal .py files with a ``# tpy: macro_module`` directive.
They are executed via CPython during compilation (not compiled to C++).
"""

from __future__ import annotations

import copy
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, NoReturn, TYPE_CHECKING

from .parse.nodes import ParseError as _ParseError
from .typesys import (
    TpyType, NominalType, OwnType,
    OptionalType,
    TupleType, UnionType, make_set, make_dict, make_list,
    FieldInfo as InternalFieldInfo,
    INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    ALL_FIXED_INTS,
    is_float_type,
)
from .typesys import (
    VOID as _VOID, STR as _STR, STRVIEW as _STRVIEW, BOOL as _BOOL,
    FLOAT as _FLOAT, FLOAT32 as _FLOAT32, BIGINT as _BIGINT,
)
from .parse.parser import FragmentParser as _FragmentParser
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
    TpySetComprehension, TpyIfExpr,
    TpyFString, TpyFStringValue,
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

# Re-exports from the tpyc package so macro modules only need to import
# from tpyc.macro_api.
from . import __version__, VERSION_INFO

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

    Called at module level in a macro module. The module name is bound
    in the macro namespace so macro expansions can use qualified calls
    (e.g. ``ast.method_call(ast.name("mod"), "func", args)``).
    Individual function names are NOT injected into user scope.

    Each argument is a module name string: ``"tplib.json.parser"``.
    The tuple form ``("module", "name1", ...)`` is accepted for backward
    compatibility but the name filter is ignored -- use qualified calls.

    Example::

        macro_deps("log_infra", "tpy.unsafe")
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
# MacroFStringPart -- typed expression from a decomposed f-string
# ---------------------------------------------------------------------------

@dataclass
class MacroFStringPart:
    """One expression part of a decomposed f-string.

    Provides the original AST expression, its resolved type, and the
    format spec (if any). Used by call macros to apply per-type wrapping
    when building logging or serialization calls.
    """
    expr: TpyExpr
    type: TypeInfo
    format_spec: str | None = None
    conversion: int = -1  # FSTRING_CONV_NONE

    @property
    def is_string_literal(self) -> bool:
        """True if the expression is a string literal (static storage in C++)."""
        return isinstance(self.expr, TpyStrLiteral)

    @property
    def is_static_str(self) -> bool:
        """True if the expression resolves to static string storage at compile time.

        Detects direct string literals and ternary expressions where both
        branches are static strings (recursively).
        """
        return _is_static_str(self.expr)


def _is_static_str(expr: TpyExpr) -> bool:
    """Check if an expression resolves to static string storage at compile time."""
    if isinstance(expr, TpyStrLiteral):
        return True
    if isinstance(expr, TpyIfExpr):
        return _is_static_str(expr.then_expr) and _is_static_str(expr.else_expr)
    return False


# ---------------------------------------------------------------------------
# MacroArg -- argument wrapper for call-site macros
# ---------------------------------------------------------------------------

@dataclass
class MacroArg:
    """Argument passed to a call-site macro: the AST expression + resolved type."""
    expr: TpyExpr
    type: TypeInfo
    _fstring_parts: list[MacroFStringPart] | None = field(default=None, repr=False)

    @property
    def is_fstring(self) -> bool:
        """True if this argument is an f-string literal (not a plain string).

        Note: as_fstring() also accepts plain string literals -- use it
        directly when plain strings should be treated as static f-strings.
        """
        return isinstance(self.expr, TpyFString)

    def as_fstring(self) -> tuple[str, list[MacroFStringPart]] | None:
        """Decompose an f-string argument into format template + typed parts.

        Returns (format_template, parts) where format_template has ``{}``
        placeholders (with optional format specs like ``{:.2f}``) and parts
        is a list of MacroFStringPart for each expression.

        Also accepts plain string literals (treated as a static format
        template with no expression parts).

        Returns None if this argument is not an f-string or string literal.
        """
        if isinstance(self.expr, TpyStrLiteral):
            fmt = self.expr.value.replace("{", "{{").replace("}", "}}")
            return fmt, []
        if not isinstance(self.expr, TpyFString):
            return None
        assert self._fstring_parts is not None, \
            "f-string parts not pre-computed (internal error)"
        fmt_pieces: list[str] = []
        for part in self.expr.parts:
            if isinstance(part, str):
                fmt_pieces.append(part.replace("{", "{{").replace("}", "}}"))
            elif isinstance(part, TpyFStringValue):
                spec = part.format_spec
                if spec is not None:
                    fmt_pieces.append("{:" + spec + "}")
                else:
                    fmt_pieces.append("{}")
        return "".join(fmt_pieces), list(self._fstring_parts)


# ---------------------------------------------------------------------------
# CallMacroContext -- context for call-site macros
# ---------------------------------------------------------------------------

class CallMacroContext:
    """Context passed to call-site macros for type introspection and diagnostics."""

    def __init__(self, ctx: SemanticContext, loc: Any = None) -> None:
        self._ctx = ctx
        self._loc = loc

    def get_record_fields(self, name: str) -> list[FieldInfo] | None:
        """Get all fields (parent + own) for a macro record, or None.

        Only returns fields for records that called set_match_args().
        """
        record_info = self._ctx.registry.get_record(name)
        if record_info is None or record_info.match_args is None:
            return None
        all_fields = self._ctx.registry.get_all_fields(record_info)
        return [FieldInfo.from_internal(f) for f in all_fields]

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

    @property
    def in_method(self) -> bool:
        """True if the call site is inside a method body (self is available)."""
        record = self._ctx.record_ctx.record
        if record is None:
            return False
        func = self._ctx.func.current_function
        return isinstance(func, TpyFunction) and func.is_method

    @property
    def self_type(self) -> TypeInfo | None:
        """The current class as TypeInfo, or None if not in a method."""
        record = self._ctx.record_ctx.record
        if record is None:
            return None
        func = self._ctx.func.current_function
        if not isinstance(func, TpyFunction) or not func.is_method:
            return None
        return TypeInfo.from_tpy_type(NominalType(record.name))

    @property
    def first_param(self) -> tuple[str, TypeInfo] | None:
        """First parameter of the current function (self for methods), or None.

        For methods, returns ("self", <class TypeInfo>).
        For free functions, returns the first declared parameter.
        """
        func = self._ctx.func.current_function
        if not isinstance(func, TpyFunction):
            return None
        if func.is_method and not func.is_staticmethod:
            st = self.self_type
            if st is not None:
                return ("self", st)
            return None
        if not func.params:
            return None
        name, tpy_type = func.params[0]
        return (name, TypeInfo.from_tpy_type(tpy_type))

    def self_field(self, name: str) -> TpyExpr:
        """Return AST for ``self.<name>``. Only valid when in_method is True."""
        if not self.in_method:
            self.error("self_field() called outside a method context")
        return TpyFieldAccess(obj=TpyName("self"), field=name)

    def _get_record_info(self, type_info: TypeInfo) -> Any:
        """Look up RecordInfo for a TypeInfo, or None."""
        if type_info._tpy_type is None:
            return None
        return self._ctx.registry.get_record(type_info.name)

    def get_field_type(self, type_info: TypeInfo, name: str) -> TypeInfo | None:
        """Get the type of a named field on a record (including inherited), or None."""
        record_info = self._get_record_info(type_info)
        if record_info is None:
            return None
        for f in self._ctx.registry.get_all_fields(record_info):
            if f.name == name:
                return TypeInfo.from_tpy_type(f.type)
        return None

    def get_method_return_type(self, type_info: TypeInfo, name: str) -> TypeInfo | None:
        """Get the return type of a named method on a record (including inherited), or None."""
        record_info = self._get_record_info(type_info)
        if record_info is None:
            return None
        overloads = self._ctx.registry.get_method_overloads_with_parents(record_info, name)
        if not overloads:
            return None
        return TypeInfo.from_tpy_type(overloads[0].return_type)

    def qualified_name(self, type_info: TypeInfo) -> str:
        """Module-qualified type name (e.g. 'log_infra.LogHandle').

        For builtin/module types, uses the type's own qualified name.
        For user records, searches the registry modules.
        Falls back to the short name if the module is unknown.
        """
        tpy_type = type_info._tpy_type
        if isinstance(tpy_type, NominalType):
            qn = tpy_type.qualified_name()
            if qn is not None:
                return qn
            # Search registry modules for user-defined records
            for mod_name, mod_info in self._ctx.registry.modules.items():
                if type_info.name in mod_info.records:
                    return f"{mod_name}.{type_info.name}"
        return type_info.name

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
        from .type_def_registry import is_fixed_int_type
        return is_fixed_int_type(self._tpy_type)

    @property
    def is_int32(self) -> bool:
        from .type_def_registry import int_traits_of
        tr = int_traits_of(self._tpy_type)
        return tr is not None and tr.bits == 32 and tr.signed

    @property
    def is_float(self) -> bool:
        return is_float_type(self._tpy_type)

    @property
    def is_float32(self) -> bool:
        from .type_def_registry import is_float32_type
        return is_float32_type(self._tpy_type)

    @property
    def is_bool(self) -> bool:
        from .type_def_registry import is_bool_type
        return is_bool_type(self._tpy_type)

    @property
    def is_bigint(self) -> bool:
        from .type_def_registry import is_big_int_type
        return is_big_int_type(self._tpy_type)

    @property
    def is_enum(self) -> bool:
        from .type_def_registry import is_enum_type
        return is_enum_type(self._tpy_type)

    @property
    def is_list(self) -> bool:
        from .type_def_registry import is_list as _is_list
        return _is_list(self._tpy_type)

    @property
    def is_dict(self) -> bool:
        from .type_def_registry import is_dict as _is_dict
        return _is_dict(self._tpy_type)

    @property
    def is_set(self) -> bool:
        from .type_def_registry import is_set as _is_set
        return _is_set(self._tpy_type)

    @property
    def is_tuple(self) -> bool:
        return isinstance(self._tpy_type, TupleType)

    @property
    def enum_name(self) -> str:
        """Get the enum class name (only valid when is_enum is True)."""
        from .type_def_registry import is_enum_type
        assert is_enum_type(self._tpy_type)
        return self._tpy_type.name

    @property
    def int_type_name(self) -> str:
        """Get the fixed-int type name e.g. 'Int32' (only valid when is_int)."""
        from .type_def_registry import is_fixed_int_type
        assert is_fixed_int_type(self._tpy_type)
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
        from .typesys import RefType
        # Strip sema-internal wrappers -- macros see user-facing types
        if isinstance(typ, OwnType):
            typ = typ.wrapped
        if isinstance(typ, RefType):
            typ = typ.wrapped
        type_args: list[TypeInfo] = []
        if isinstance(typ, TupleType):
            type_args = [TypeInfo.from_tpy_type(et) for et in typ.element_types]
        elif isinstance(typ, NominalType) and typ.type_args:
            type_args = [TypeInfo.from_tpy_type(ta) for ta in typ.type_args]
        return TypeInfo(
            name=str(typ),
            type_args=type_args,
            is_optional=isinstance(typ, OptionalType),
            is_value_type=typ.is_value_type(),
            is_record=isinstance(typ, NominalType) and typ.is_user_record,
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

    def set_default(self, expr: TpyExpr | int | float | str | bool | None,
                    default_value: str | None = None,
                    is_factory: bool = False) -> None:
        """Replace this field's default expression (updates the underlying record).

        Accepts plain Python values (from unwrapped macro kwargs) and wraps
        them in TpyExpr nodes automatically.
        """
        expr = _wrap_literal(expr)
        self.default_expr = expr
        self.has_default = expr is not None
        self.is_factory_default = is_factory
        if self._internal is not None:
            self._internal.default_expr = expr
            self._internal.default_value = default_value
            self._internal.is_factory_default = is_factory

    def clear_default(self) -> None:
        """Remove the field's default (e.g. after unwrapping a descriptor)."""
        self.default_expr = None
        self.has_default = False
        self.is_factory_default = False
        if self._internal is not None:
            self._internal.default_expr = None
            self._internal.default_value = None
            self._internal.is_factory_default = False

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
# ClassInfo -- mutable wrapper for macro class transformation
# ---------------------------------------------------------------------------

class ClassInfo:
    """Class metadata passed to class macros.

    Macros inspect fields/methods and can add methods, set flags, and
    set match_args. Mutations are applied back to the TpyRecord
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
        self._match_args: list[str] | None = None

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
    def is_frozen(self) -> bool:
        return self._record.is_frozen

    @is_frozen.setter
    def is_frozen(self, value: bool) -> None:
        self._record.is_frozen = value

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
        """Get all fields from parent record (including inherited).

        Returns fields for the first base that is a registered record,
        or empty list if no record parent. The caller decides whether
        to use these based on its own eligibility checks.
        """
        for base in self._record.bases:
            if not isinstance(base, NominalType):
                continue
            parent_info = self._ctx.registry.get_record(base.name)
            if parent_info is not None:
                all_parent = self._ctx.registry.get_all_fields(parent_info)
                return [FieldInfo.from_internal(f) for f in all_parent]
        return []

    # -- Mutation methods --

    def add_method(self, func: TpyFunction) -> None:
        """Add a method from a TpyFunction AST node (power user API)."""
        self._added_methods.append(func)

    def add_method_from_source(self, source: str) -> None:
        """Parse a method definition from TPy source and add it.

        Equivalent to ``self.add_method(ast.quote_fun(source))``.
        """
        self.add_method(ast.quote_fun(source))

    # TODO: property getter+setter emission from macros.
    # FragmentParser parses fragments one at a time, so the setter's
    # `@getter_name.setter` decorator has no `property_names` context
    # and falls through as "Unknown decorator".  To support macro-
    # generated properties, add something like
    #   add_property_pair(getter_source: str, setter_source: str) -> None
    # that parses both fragments together, threading the getter name
    # into FragmentParser so the setter resolves correctly.  The method
    # expansion pipeline already handles property-setter `Own[T]`
    # wrapping, so the fix is purely at the FragmentParser routing
    # layer.  Wait for a real use case before committing to an API shape.

    def set_match_args(self, names: list[str]) -> None:
        """Set positional match arg names (mirrors Python's __match_args__)."""
        self._match_args = list(names)

    def is_parent_frozen(self) -> tuple[bool, str] | None:
        """Check if the first parent record is frozen.

        Returns (is_frozen, parent_name) or None if no record parent.
        """
        for base in self._record.bases:
            if not isinstance(base, NominalType):
                continue
            parent_info = self._ctx.registry.get_record(base.name)
            if parent_info is not None:
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

    def get_match_args(self) -> tuple[str, ...] | None:
        """Return the match args set by the macro, or None."""
        return tuple(self._match_args) if self._match_args is not None else None


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
        """Create a name reference. Dotted names (e.g. 'mylog.infra') are
        decomposed into a field-access chain so module resolution works."""
        if not n or ".." in n or n.startswith(".") or n.endswith("."):
            raise MacroError(f"ast.name(): invalid name {n!r}")
        if "." not in n:
            return TpyName(n)
        parts = n.split(".")
        result: Expr = TpyName(parts[0])
        for part in parts[1:]:
            result = TpyFieldAccess(obj=result, field=part)
        return result

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

    def set_comprehension(self, element_expr: Expr,
                          generator: TpyComprehensionGenerator) -> Expr:
        return TpySetComprehension(element_expr=element_expr, generator=generator)

    def if_expr(self, condition: Expr, then_expr: Expr, else_expr: Expr) -> Expr:
        return TpyIfExpr(condition=condition, then_expr=then_expr, else_expr=else_expr)

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

    def raise_(self, exception_type: str, args: list[Expr] | None = None) -> Stmt:
        if args:
            return TpyRaise(exception_type=exception_type, args=args, is_call_form=True)
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

    # -- Cloning --

    def clone(self, expr: Expr) -> Expr:
        """Deep-copy an expression tree. Use when the same logical expression
        must appear in multiple places (e.g. condition and body of a ternary)
        to avoid sema overwriting resolved types on shared nodes."""
        return copy.deepcopy(expr)

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
                 defaults: list[Expr | None] | None = None,
                 hides_parent: bool = False) -> Function:
        return TpyFunction(
            name=name, params=params, return_type=return_type, body=body,
            is_method=is_method, is_staticmethod=is_staticmethod,
            is_readonly=is_readonly, readonly_opt_out=readonly_opt_out,
            error_return=error_return, defaults=defaults or [],
            hides_parent=hides_parent,
        )

    # -- Source-based quoting --

    def _quote(
        self, source: str, kind: Literal["function", "statements", "expression"],
    ) -> list[Stmt] | Expr | Function:
        try:
            return _FragmentParser.parse_fragment(source, kind=kind)
        except SyntaxError as e:
            clean = textwrap.dedent(source).strip()
            lines = clean.splitlines()
            lineno = (e.lineno or 1) - 1
            bad_line = lines[lineno].strip() if 0 <= lineno < len(lines) else lines[0]
            raise MacroError(f"syntax error in quoted source: `{bad_line}`") from None
        except _ParseError as e:
            preview = textwrap.dedent(source).strip().split("\n")[0]
            if len(preview) > 60:
                preview = preview[:57] + "..."
            raise MacroError(f"{e} (source: `{preview}`)") from None

    def quote(self, source: str) -> list[Stmt]:
        """Parse TPy statements from a source string."""
        return self._quote(source, "statements")

    def quote_expr(self, source: str) -> Expr:
        """Parse a single TPy expression from a source string."""
        return self._quote(source, "expression")

    def quote_fun(self, source: str) -> Function:
        """Parse a complete function definition from a source string.

        Decorators are not supported. To mark as staticmethod, set
        func.is_staticmethod = True on the returned function.
        """
        return self._quote(source, "function")


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
        return NominalType(name)

    def own(self, inner: TpyType) -> TpyType:
        return OwnType(inner)

    def optional(self, inner: TpyType) -> TpyType:
        return OptionalType(inner)

    def list(self, element_type: TpyType) -> TpyType:
        return make_list(element_type)

    def dict(self, key_type: TpyType, value_type: TpyType) -> TpyType:
        return make_dict(key_type, value_type)

    def set(self, element_type: TpyType) -> TpyType:
        return make_set(element_type)

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

def _wrap_literal(value: object) -> TpyExpr | None:
    """Wrap a plain Python value in a TpyExpr node. Pass through TpyExpr unchanged."""
    if isinstance(value, TpyExpr):
        return value
    if value is None:
        return TpyNoneLiteral()
    if isinstance(value, bool):
        return TpyBoolLiteral(value=value)
    if isinstance(value, int):
        return TpyIntLiteral(value=value)
    if isinstance(value, float):
        return TpyFloatLiteral(value=value)
    if isinstance(value, str):
        return TpyStrLiteral(value=value)
    return value  # non-literal (e.g. factory callable)


def expr_to_cpp_default(expr: TpyExpr | int | float | str | bool | None) -> str | None:
    """Convert a TpyExpr or plain value to a C++ default value literal string, or None."""
    # Normalize plain Python values to TpyExpr first
    if not isinstance(expr, TpyExpr) and expr is not None:
        expr = _wrap_literal(expr)
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
    if isinstance(expr, TpyCall) and isinstance(expr.func, TpyName) and expr.func_name in _FIXED_INT_NAMES:
        if not expr.args:
            return "0"
        if len(expr.args) == 1:
            return expr_to_cpp_default(expr.args[0])
    return None
