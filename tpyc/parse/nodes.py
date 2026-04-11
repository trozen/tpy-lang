"""
TurboPython AST node definitions.

Pure data definitions: dataclasses, ParseError, SourceLocation.
No parsing logic lives here.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Literal, Optional, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, FieldInfo, FunctionInfo,
    MethodSignature, TypeParamKind,
    EnumType, IntEnumType,
)


class ParseError(Exception):
    """Error during parsing."""
    def __init__(self, message: str, node: Optional[ast.AST] = None):
        self.node = node
        self.message = message
        self.lineno = node.lineno if node and hasattr(node, 'lineno') else None
        loc = f" at line {self.lineno}" if self.lineno else ""
        super().__init__(f"{message}{loc}")

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        if self.lineno:
            return f"{filename}:{self.lineno}: error: {self.message}"
        return f"{filename}: error: {self.message}"


# Source location for error reporting and source mapping

@dataclass
class SourceLocation:
    """Source code location for error reporting and source mapping."""
    line: int  # 1-indexed line number
    column: int = 0  # 0-indexed column
    file: str | None = None  # Source file path (optional)


# AST node types for TurboPython

@dataclass
class TpyExpr:
    """Base class for expressions."""
    loc: SourceLocation | None = field(default=None, kw_only=True)

    def children(self) -> list['TpyExpr']:
        """Return child expression nodes for generic tree walking."""
        return []


if TYPE_CHECKING:
    from ..coercions import Coercion
    from ..typesys import FunctionInfo


@dataclass
class TpyIntLiteral(TpyExpr):
    """Integer literal."""
    value: int


@dataclass
class TpyFloatLiteral(TpyExpr):
    """Floating point literal."""
    value: float


@dataclass
class TpyStrLiteral(TpyExpr):
    """String literal."""
    value: str


@dataclass
class TpyBytesLiteral(TpyExpr):
    """Bytes literal (b"...")."""
    value: bytes


# F-string conversion codes (from CPython's ast module)
FSTRING_CONV_NONE = -1
FSTRING_CONV_STR = 115    # !s
FSTRING_CONV_REPR = 114   # !r
FSTRING_CONV_ASCII = 97   # !a


@dataclass
class TpyFStringValue:
    """Formatted expression inside an f-string: {expr:spec}."""
    expr: TpyExpr
    conversion: int = FSTRING_CONV_NONE
    format_spec: str | None = None


@dataclass
class TpyFString(TpyExpr):
    """F-string: f"text {expr:spec} more text"."""
    parts: list[str | TpyFStringValue]

    def children(self) -> list[TpyExpr]:
        return [p.expr for p in self.parts if isinstance(p, TpyFStringValue)]


@dataclass
class TpyBoolLiteral(TpyExpr):
    """Boolean literal."""
    value: bool


@dataclass
class TpyNoneLiteral(TpyExpr):
    """None literal."""
    pass


@dataclass
class TpyName(TpyExpr):
    """Variable reference."""
    name: str
    is_function_ref: bool = False  # Set by sema: name resolves to a function used as a value
    function_ref_info: 'FunctionInfo | None' = None  # Set by sema: resolved function for codegen
    function_ref_type_args: 'tuple[TpyType, ...] | None' = None  # Set by sema: inferred type args for generic function refs


@dataclass
class TpyBinOp(TpyExpr):
    """Binary operation."""
    left: TpyExpr
    op: str  # '+', '-', '*', '/', '%', '==', '!=', '<', '>', '<=', '>='
    right: TpyExpr
    resolved_binop: 'ResolvedBinop | None' = None  # Set by sema for builtin ops
    resolved_contains: 'FunctionInfo | None' = None  # Set by sema for 'in'/'not in' with __contains__
    typed_dict_in_field: str | None = None  # Set by sema: "key" in TypedDict -> field presence check
    typed_dict_in_always_true: bool = False  # Set by sema: total=True field, fold to True
    optional_safe_eq: bool = False  # Set by sema: ==/!= with Optional value-type operand(s)
    int_enum_coercion: 'IntEnumType | None' = None  # Set by sema: IntEnum arithmetic coerced to underlying type
    divisor_non_zero: bool = False  # Set by sema: divisor provably non-zero, skip div-zero check

    def children(self) -> list[TpyExpr]:
        return [self.left, self.right]


@dataclass
class TpyChainedCompare(TpyExpr):
    """Chained comparison: a < b < c desugars to (a < b) and (b < c)."""
    left: TpyExpr
    ops: list[str]
    comparators: list[TpyExpr]
    # Set by sema: synthetic TpyBinOp for each comparison pair
    pairs: list['TpyBinOp'] | None = None

    def children(self) -> list[TpyExpr]:
        return [self.left] + self.comparators


@dataclass
class TpyUnaryOp(TpyExpr):
    """Unary operation."""
    op: str  # '-', 'not'
    operand: TpyExpr
    resolved_unaryop: 'ResolvedUnaryop | None' = None  # Set by sema for builtin ops

    def children(self) -> list[TpyExpr]:
        return [self.operand]


@dataclass
class TpyTypeParamConstruct(TpyExpr):
    """Default-construction of a type parameter: T() in a default value."""
    param_name: str


@dataclass
class TpyVarargPack(TpyExpr):
    """Pack of variadic arguments at a call site (created by sema, not parser).

    Represents the trailing args that get packed into a Span[readonly[T]] for
    a *args: T parameter.
    """
    args: list[TpyExpr]
    element_type: TpyType

    def children(self) -> list[TpyExpr]:
        return list(self.args)


@dataclass
class TpyStarUnpack(TpyExpr):
    """Star unpacking at a call site: f(*expr). Created by parser for *expr in calls."""
    expr: TpyExpr

    def children(self) -> list[TpyExpr]:
        return [self.expr]


@dataclass
class TpyCall(TpyExpr):
    """Function or constructor call.

    For generic function calls like first[Int32](items):
    - type_args stores the explicit type arguments (e.g., (Int32,))
    - inferred_type_args is set by sema for codegen (resolved from inference or explicit)
    """
    func: TpyExpr  # TpyName for simple calls; arbitrary TpyExpr for expression callees
    args: list[TpyExpr]
    call_type: Optional[TpyType] = None  # For generic instantiation like MyContainer[T, N]()
    type_args: tuple[TpyType, ...] = ()  # Explicit type args for generic function calls: func[T](args)
    inferred_type_args: tuple[TpyType, ...] | None = None  # Set by sema for generic function calls
    type_args_parse_error: str | None = None  # Set if subscript had args that couldn't be parsed as types
    subscript_callee: 'TpyExpr | None' = None  # Set by parser: fns[0](args) -> stores TpySubscript(fns, 0) for sema fallback
    kwargs: dict[str, TpyExpr] = field(default_factory=dict)  # Keyword arguments (limited support)
    double_star_unpack: 'TpyExpr | None' = None  # **expr unpacking at call site
    kwarg_td_call: 'TpyExpr | None' = None  # Set by sema: synthetic TypedDict construction for **kwargs
    resolved_import: tuple[str, str] | None = None  # Set by parser: (module, name) for resolved imports
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved function overloads
    enum_from_value: EnumType | None = None  # Set by sema for enum value lookup: Color(0)
    enum_try_parse: EnumType | None = None   # Set by sema for tpy.try_parse(Color, "Red")
    isinstance_var: str | None = None        # Set by sema: variable name being isinstance-checked
    isinstance_type: TpyType | None = None   # Set by sema: resolved type being checked for
    isinstance_is_protocol: bool = False     # Set by sema: protocol isinstance (if constexpr)
    macro_expansion: 'TpyExpr | None' = None  # Set by sema: replacement expr from @call_macro
    dunder_call: 'TpyMethodCall | None' = None  # Set by sema: obj(args) -> obj.__call__(args)

    @property
    def func_name(self) -> str:
        """Name of the callee. Only valid when func is a TpyName (simple call)."""
        assert isinstance(self.func, TpyName), f"func_name on non-Name callee: {type(self.func).__name__}"
        return self.func.name

    def children(self) -> list[TpyExpr]:
        if self.macro_expansion is not None:
            return [self.macro_expansion]
        extra = [self.subscript_callee] if self.subscript_callee is not None else []
        return [self.func] + list(self.args) + list(self.kwargs.values()) + extra


@dataclass
class TpyMethodCall(TpyExpr):
    """Method call on an object."""
    obj: TpyExpr
    method: str
    args: list[TpyExpr]
    kwargs: dict[str, TpyExpr] = field(default_factory=dict)
    double_star_unpack: 'TpyExpr | None' = None  # **expr unpacking at call site
    resolved_import: tuple[str, str] | None = None  # Set by parser: (module, name) for resolved imports
    type_args: tuple[TpyType, ...] = ()  # Explicit type args for module.func[T](args) syntax
    type_args_parse_error: str | None = None  # Set if subscript had args that couldn't be parsed as types
    is_static_call: bool = False  # Set by sema for ClassName.staticmethod() calls
    super_parent_type: Optional[TpyType] = None  # Set by sema for super().method() calls
    user_module_call: Optional[str] = None  # Set by sema for module.func() calls to user modules
    builtin_module_call: Optional[str] = None  # Set by sema for builtin module.func() calls (canonical module name)
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved method overloads
    inferred_type_args: tuple[TpyType, ...] | None = None  # Set by sema for generic builtin module calls
    deref_depth: int = 0  # Set by sema: number of __deref__ steps applied before method resolution
    ptr_non_null: bool = False  # Set by sema: receiver is a provably non-null Ptr (or Ptr[readonly[T]])
    is_callable_field: bool = False  # Set by sema: method name is a Callable-typed field
    macro_expansion: 'TpyExpr | None' = None  # Set by sema: replacement expr from @call_macro
    typed_dict_get_field: str | None = None  # Set by sema: td.get("key") -> field access
    typed_dict_get_optional: bool = False  # Set by sema: total=False field, absent by default
    is_nested_constructor: bool = False  # Set by sema: Outer.Inner() nested record constructor
    is_nested_enum_constructor: bool = False  # Set by sema: Outer.Kind(v) nested enum from_value
    nested_type_name: str | None = None  # Set by sema: dotted name for nested type calls

    def children(self) -> list[TpyExpr]:
        if self.macro_expansion is not None:
            return [self.macro_expansion]
        return [self.obj] + list(self.args) + list(self.kwargs.values())


@dataclass
class TpyFieldAccess(TpyExpr):
    """Field access on a value or pointer."""
    obj: TpyExpr
    field: str
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access
    deref_depth: int = 0  # Set by sema: number of __deref__ steps applied before field lookup
    ptr_non_null: bool = False  # Set by sema: receiver is a provably non-null Ptr (or Ptr[readonly[T]])
    is_property_access: bool = False  # Set by sema: this is a property getter
    property_setter: bool = False  # Set by sema: assignment target is a property setter
    property_getter_call: 'TpyMethodCall | None' = None  # Set by sema: getter method call for codegen
    property_setter_call: 'TpyMethodCall | None' = None  # Set by sema: setter method call for codegen

    def children(self) -> list[TpyExpr]:
        return [self.obj]


@dataclass
class TpyArrayLiteral(TpyExpr):
    """Array literal: [expr, expr, ...]"""
    elements: list[TpyExpr]

    def children(self) -> list[TpyExpr]:
        return list(self.elements)


class TupleElemCapture(IntEnum):
    """How a tuple literal element captures its value."""
    VALUE = 0       # T -- owned copy
    REF = 1         # T& -- mutable reference
    CONST_REF = 2   # const T& -- immutable reference


@dataclass
class TpyTupleLiteral(TpyExpr):
    """Tuple literal: (expr, expr, ...)"""
    elements: list[TpyExpr]
    # Set by sema: per-element capture mode
    elem_capture: list[TupleElemCapture] = field(default_factory=list)

    def children(self) -> list[TpyExpr]:
        return list(self.elements)


@dataclass
class TpyListRepeat(TpyExpr):
    """List repetition: [elements...] * count -> sequence repeated count times"""
    elements: list[TpyExpr]
    count: TpyExpr

    def children(self) -> list[TpyExpr]:
        return list(self.elements) + [self.count]


@dataclass
class TpyComprehensionGenerator:
    """Single generator clause: for var in iterable [if cond]*"""
    var: str
    iterable: TpyExpr
    conditions: list[TpyExpr]
    unpack_vars: list[str | None] | None = None  # Phase 3: tuple unpacking
    const_loop_var: bool = False


@dataclass
class TpyListComprehension(TpyExpr):
    """List comprehension: [expr for var in iterable if cond]"""
    element_expr: TpyExpr
    generator: TpyComprehensionGenerator
    result_elem_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return [self.element_expr, self.generator.iterable] + self.generator.conditions


@dataclass
class TpyDictComprehension(TpyExpr):
    """Dict comprehension: {key: value for var in iterable if cond}"""
    key_expr: TpyExpr
    value_expr: TpyExpr
    generator: TpyComprehensionGenerator
    result_key_type: 'TpyType | None' = None  # set by sema
    result_value_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return [self.key_expr, self.value_expr, self.generator.iterable] + self.generator.conditions


@dataclass
class TpyDictLiteral(TpyExpr):
    """Dict literal: {key: value, key: value, ...}"""
    keys: list[TpyExpr]
    values: list[TpyExpr]

    def children(self) -> list[TpyExpr]:
        return list(self.keys) + list(self.values)


@dataclass
class TpySetComprehension(TpyExpr):
    """Set comprehension: {expr for var in iterable if cond}"""
    element_expr: TpyExpr
    generator: TpyComprehensionGenerator
    result_elem_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return [self.element_expr, self.generator.iterable] + self.generator.conditions


@dataclass
class TpyGeneratorExpression(TpyExpr):
    """Generator expression: (expr for var in iterable if cond)"""
    element_expr: TpyExpr
    generator: TpyComprehensionGenerator
    result_elem_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return [self.element_expr, self.generator.iterable] + self.generator.conditions


@dataclass
class TpyLambda(TpyExpr):
    """Lambda expression: lambda x, y: x + y.

    Parameter types are inferred from context (Fn type hint) during sema.
    """
    param_names: list[str]
    body: TpyExpr
    inferred_param_types: list['TpyType'] = field(default_factory=list)
    inferred_return_type: 'TpyType | None' = None
    captured_names: list[str] = field(default_factory=list)
    captures_by_value: bool = False  # True for Callable context (captures escape)
    readonly_params: bool = False  # True when params should be const (key functions)

    def children(self) -> list[TpyExpr]:
        # Lambda creates its own scope; outer walks should not recurse into
        # the body. Lambda analysis in sema recurses into body explicitly.
        return []


@dataclass
class TpySetLiteral(TpyExpr):
    """Set literal: {value, value, ...}"""
    elements: list[TpyExpr]

    def children(self) -> list[TpyExpr]:
        return list(self.elements)


@dataclass
class TpySlice(TpyExpr):
    """Slice expression: lower:upper or lower:upper:step."""
    lower: TpyExpr | None = None
    upper: TpyExpr | None = None
    step: TpyExpr | None = None  # reserved for future step support

    def children(self) -> list[TpyExpr]:
        return [x for x in (self.lower, self.upper, self.step) if x is not None]


@dataclass
class TpySubscript(TpyExpr):
    """Subscript indexing: obj[index]"""
    obj: TpyExpr
    index: TpyExpr  # TpySlice for slicing, other TpyExpr for single-index
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access
    enum_from_name: 'EnumType | None' = None    # Set by sema for Color["Red"] name lookup
    bounds_safe: bool = False  # Set by sema: index provably in [0, len(obj)), skip bounds check
    is_stepped_slice: bool = False  # Set by sema: slice has step (a[::2])
    slice_function_info: 'FunctionInfo | None' = None  # Set by sema: resolved __getitem__ for slice
    typed_dict_field: str | None = None  # Set by sema: d["key"] on TypedDict -> field access
    typed_dict_optional: bool = False  # Set by sema: total=False field, needs runtime check

    def children(self) -> list[TpyExpr]:
        return [self.obj, self.index]


@dataclass
class TpyCoerce(TpyExpr):
    """Expression with an explicit coercion attached by semantic analysis."""
    expr: TpyExpr
    actual_type: TpyType
    expected_type: TpyType
    coercion: "Coercion"
    context_kind: str
    context_msg: str
    runtime_bigint: bool = False

    def children(self) -> list[TpyExpr]:
        return [self.expr]


@dataclass
class TpyIfExpr(TpyExpr):
    """Ternary conditional expression: then_expr if condition else else_expr."""
    condition: TpyExpr
    then_expr: TpyExpr
    else_expr: TpyExpr

    def children(self) -> list[TpyExpr]:
        return [self.condition, self.then_expr, self.else_expr]


@dataclass
class TpyNamedExpr(TpyExpr):
    """Walrus operator: (x := expr)."""
    target: str
    value: TpyExpr

    def children(self) -> list[TpyExpr]:
        return [self.value]


@dataclass
class TpyStmt:
    """Base class for statements."""
    loc: SourceLocation | None = field(default=None, kw_only=True)

    def exprs(self) -> list[TpyExpr]:
        """Return direct child expressions for generic tree walking."""
        return []

    def sub_bodies(self) -> list[list[TpyStmt]]:
        """Return sub-statement bodies for recursive walking."""
        return []


class VarLinkage(Enum):
    """Linkage mode for global variable imports."""
    DEFAULT = "default"
    NATIVE = "native"           # C++ global import
    NATIVE_C = "native_c"       # C global import (extern "C")
    NATIVE_C_ARRAY = "native_c_array"  # C array global (extern "C" T name[])


@dataclass
class TpyVarDecl(TpyStmt):
    """Variable declaration with optional initializer."""
    name: str
    type: Optional[TpyType]
    init: Optional[TpyExpr]
    linkage: VarLinkage = VarLinkage.DEFAULT
    native_name: str | None = None
    # TODO: is_final (and linkage) could be generalized into a modifiers set
    # (e.g. modifiers: set[str]) to avoid per-feature boolean fields
    is_final: bool = False  # Set by sema for Final[T] constant globals
    # Set by sema: union assignment narrowing facts for codegen
    then_type_facts: dict[str, TpyType] = field(default_factory=dict)

    def exprs(self) -> list[TpyExpr]:
        return [self.init] if self.init else []


@dataclass
class TpyTupleUnpack(TpyStmt):
    """Tuple unpacking: a, b = expr. None in targets means discard (_)."""
    targets: list[str | None]
    value: TpyExpr
    # Set by sema:
    target_types: list[TpyType] = field(default_factory=list)
    is_new: list[bool] = field(default_factory=list)
    is_owned: list[bool] = field(default_factory=list)
    is_ref: list[bool] = field(default_factory=list)
    is_const_ref: list[bool] = field(default_factory=list)

    def exprs(self) -> list[TpyExpr]:
        return [self.value]


@dataclass
class TpyAssign(TpyStmt):
    """Assignment to variable or field."""
    target: TpyExpr
    value: TpyExpr

    def exprs(self) -> list[TpyExpr]:
        return [self.target, self.value]


@dataclass
class TpyAugAssign(TpyStmt):
    """Augmented assignment (+=, -=, etc.)"""
    target: TpyExpr
    op: str
    value: TpyExpr
    resolved_binop: 'ResolvedBinop | None' = None  # Set by sema for builtin ops
    resolved_inplace: 'ResolvedBinop | None' = None  # Set by sema for in-place ops (__iadd__, __ior__, etc.)

    def exprs(self) -> list[TpyExpr]:
        return [self.target, self.value]


@dataclass
class TpyDelItem(TpyStmt):
    """Delete statement: del obj[key], obj2[key2], ..."""
    targets: list[TpySubscript]

    def exprs(self) -> list[TpyExpr]:
        return list(self.targets)


@dataclass
class TpyDelVar(TpyStmt):
    """Delete statement for local variables: del x, y, ..."""
    names: list[str]

    def exprs(self) -> list[TpyExpr]:
        return []


@dataclass
class TpyExprStmt(TpyStmt):
    """Expression statement (e.g., function call)."""
    expr: TpyExpr

    def exprs(self) -> list[TpyExpr]:
        return [self.expr]


@dataclass
class TpyReturn(TpyStmt):
    """Return statement."""
    value: Optional[TpyExpr]
    # Set by sema: the analyzed type of the return expression (before coercion)
    value_type: Optional['TpyType'] = None

    def exprs(self) -> list[TpyExpr]:
        return [self.value] if self.value else []


@dataclass
class TpyYield(TpyStmt):
    """Yield statement in a generator function."""
    value: TpyExpr

    def exprs(self) -> list[TpyExpr]:
        return [self.value]


@dataclass
class TpyAssert(TpyStmt):
    """Assert statement."""
    condition: TpyExpr
    message: TpyExpr | None = None
    # Set by sema: isinstance union narrowing facts that hold after a passing assert
    then_type_facts: dict[str, TpyType] = field(default_factory=dict)

    def exprs(self) -> list[TpyExpr]:
        result = [self.condition]
        if self.message:
            result.append(self.message)
        return result


@dataclass
class TpyIf(TpyStmt):
    """If statement."""
    condition: TpyExpr
    then_body: list[TpyStmt]
    else_body: list[TpyStmt]
    # Set by sema: isinstance union narrowing facts for codegen
    then_type_facts: dict[str, TpyType] = field(default_factory=dict)
    else_type_facts: dict[str, TpyType] = field(default_factory=dict)

    def exprs(self) -> list[TpyExpr]:
        return [self.condition]

    def sub_bodies(self) -> list[list[TpyStmt]]:
        return [self.then_body, self.else_body]


@dataclass
class TpyWhile(TpyStmt):
    """While loop."""
    condition: TpyExpr
    body: list[TpyStmt]
    orelse: list[TpyStmt] = field(default_factory=list)
    # Set by sema: isinstance union narrowing facts for codegen
    then_type_facts: dict[str, TpyType] = field(default_factory=dict)

    def exprs(self) -> list[TpyExpr]:
        return [self.condition]

    def sub_bodies(self) -> list[list[TpyStmt]]:
        return [self.body, self.orelse]


@dataclass
class TpyForEach(TpyStmt):
    """For-each loop over a collection."""
    var: str
    iterable: TpyExpr
    body: list[TpyStmt]
    orelse: list[TpyStmt] = field(default_factory=list)
    enum_iterable: 'EnumType | None' = None  # set by sema when iterating over enum type
    elem_type: 'TpyType | None' = None  # set by sema: resolved element type for codegen
    is_tuple_unpack: bool = False  # set by parser: synthetic loop var for tuple destructuring
    const_loop_var: bool = False  # set by sema: loop var is never mutated, safe for const auto&
    hoist_loop_var: bool = False  # set by sema: loop var used after loop, needs pre-declaration
    consuming_iter_fi: 'FunctionInfo | None' = None  # set by sema: consuming __iter__ overload at last use

    def exprs(self) -> list[TpyExpr]:
        return [self.iterable]

    def sub_bodies(self) -> list[list[TpyStmt]]:
        return [self.body, self.orelse]


@dataclass
class TpyBreak(TpyStmt):
    """Break statement."""
    pass


@dataclass
class TpyContinue(TpyStmt):
    """Continue statement."""
    pass


@dataclass
class TpyPassStmt(TpyStmt):
    """Pass statement (no-op)."""
    pass


@dataclass
class TpyGlobal(TpyStmt):
    """global x, y -- declares names as referring to module-level variables."""
    names: list[str]


@dataclass
class TpyNonlocal(TpyStmt):
    """nonlocal x, y -- declares names as mutable captures from enclosing scope."""
    names: list[str]


@dataclass
class TpyRaise(TpyStmt):
    """raise E, raise E(args), raise <expr>, or bare raise (re-raise in except block)."""
    exception_type: str | None = None  # type-constructor form (set by parser, qualified by sema)
    args: list[TpyExpr] = field(default_factory=list)  # constructor arguments
    raise_expr: TpyExpr | None = None  # expression form: raise <expr> (throw-tier only)
    is_call_form: bool = False  # raise Name() vs raise Name (set by parser)


@dataclass
class TpyExceptHandler:
    """A single except clause in a try statement."""
    exception_type: str | None  # None for bare except:
    binding: str | None         # from "as e"
    body: list[TpyStmt]
    loc: SourceLocation | None = None


@dataclass
class TpyTry(TpyStmt):
    """try/except/else/finally statement (both return-tier and throw-tier)."""
    try_body: list[TpyStmt]
    handlers: list[TpyExceptHandler]  # 0+ except clauses
    else_body: list[TpyStmt]         # may be empty
    finally_body: list[TpyStmt]      # may be empty
    # Set by sema: "return" for ReturnException goto-based, "throw" for C++ try/catch
    tier: Literal["return", "throw", "finally_only"] | None = None

    def sub_bodies(self) -> list[list[TpyStmt]]:
        bodies = [self.try_body, self.else_body, self.finally_body]
        for h in self.handlers:
            bodies.append(h.body)
        return bodies


@dataclass
class TpyWithItem:
    """A single context manager in a with statement."""
    context_expr: TpyExpr
    target: str | None  # as-variable name, or None if no `as`
    loc: SourceLocation | None = None
    # Set by sema: type returned by __enter__()
    enter_type: TpyType | None = None


@dataclass
class TpyWith(TpyStmt):
    """with statement (context managers)."""
    items: list[TpyWithItem]
    body: list[TpyStmt]

    def exprs(self) -> list[TpyExpr]:
        return [item.context_expr for item in self.items]

    def sub_bodies(self) -> list[list[TpyStmt]]:
        return [self.body]


@dataclass
class TpyNestedDef(TpyStmt):
    """Nested function definition inside a function body.

    Compiles to a C++ lambda assigned to a local auto variable.
    Capture analysis is performed by sema.
    """
    func: 'TpyFunction'
    # Set by sema:
    captured_names: list[str] = field(default_factory=list)
    nonlocal_names: set[str] = field(default_factory=set)
    escapes: bool = False
    # Captures that should use by-reference even in escaping closures
    # (non-value outer params whose original object outlives the closure)
    ref_captures: set[str] = field(default_factory=set)
    # Non-value locals to move into the closure (last use, no copy needed)
    move_captures: set[str] = field(default_factory=set)


# -- Pattern matching nodes --

@dataclass
class TpyPattern:
    """Base class for match/case patterns."""
    loc: SourceLocation | None = field(default=None, kw_only=True)


@dataclass
class TpyWildcardPattern(TpyPattern):
    """case _:"""
    pass


@dataclass
class TpyCapturePattern(TpyPattern):
    """case x:"""
    name: str


@dataclass
class TpyClassPattern(TpyPattern):
    """case Circle(): / case Circle(radius=r):"""
    cls: TpyExpr
    positional: list[TpyPattern]
    keywords: list[tuple[str, TpyPattern]]
    # Set by sema: resolved record type for this class pattern
    resolved_type: TpyType | None = None


@dataclass
class TpyLiteralPattern(TpyPattern):
    """case 42: / case "hello": / case True: / case None:"""
    value: int | float | str | bool | None


@dataclass
class TpyValuePattern(TpyPattern):
    """case Color.RED: -- named constant via attribute access"""
    expr: TpyExpr


@dataclass
class TpyOrPattern(TpyPattern):
    """case Dog() | Cat():"""
    patterns: list[TpyPattern]


@dataclass
class TpyAsPattern(TpyPattern):
    """case P() as x:"""
    pattern: TpyPattern
    name: str


@dataclass
class TpyMatchCase:
    """A single case arm in a match statement."""
    pattern: TpyPattern
    guard: TpyExpr | None
    body: list[TpyStmt]
    loc: SourceLocation | None = None
    # Set by sema: narrowing facts for the subject variable in this arm
    type_facts: dict[str, TpyType] = field(default_factory=dict)


@dataclass
class TpyMatch(TpyStmt):
    """match/case statement."""
    subject: TpyExpr
    cases: list[TpyMatchCase]
    # Set by sema: resolved type of the subject expression
    subject_type: TpyType | None = None

    def exprs(self) -> list[TpyExpr]:
        result: list[TpyExpr] = [self.subject]
        for case in self.cases:
            if case.guard is not None:
                result.append(case.guard)
        return result

    def sub_bodies(self) -> list[list[TpyStmt]]:
        return [case.body for case in self.cases]


@dataclass(frozen=True)
class RelativeImportKey:
    """Structured key for relative import placeholders in import dicts.

    Used as a temporary dict key before relative imports are resolved to
    canonical module names during discovery.
    """
    # Prefix uses \x00 which cannot appear in a Python identifier,
    # making collision with real module names structurally impossible.
    _PREFIX = "\x00rel:"

    level: int
    line: int
    col: int
    partial: str  # Module name after dots ("" for bare "from . import X")

    def encode(self) -> str:
        """Encode as a unique string key for use in dicts."""
        return f"{self._PREFIX}{self.level}:{self.line}_{self.col}:{self.partial}"

    @staticmethod
    def decode(key: str) -> 'RelativeImportKey':
        """Decode a placeholder string back into structured fields."""
        # Format: \x00rel:{level}:{line}_{col}:{partial}
        body = key[len(RelativeImportKey._PREFIX):]
        level_str, loc, partial = body.split(":", 2)
        line_str, col_str = loc.split("_", 1)
        return RelativeImportKey(level=int(level_str), line=int(line_str), col=int(col_str), partial=partial)

    @staticmethod
    def is_placeholder(key: str) -> bool:
        """Check if a string key is a relative import placeholder."""
        return key.startswith(RelativeImportKey._PREFIX)


@dataclass
class TpyImport(TpyStmt):
    """Import statement for user modules.

    Only user module imports (not builtins like tpy, typing) become TpyImport nodes.
    These are emitted as __tpy_init() calls in codegen.

    For relative imports:
    - level: Number of dots (0=absolute, 1=".", 2="..", etc.)
    - relative_name: Original module name after dots (None for "from . import X")
    - module_name: Initially a RelativeImportKey.encode() placeholder, resolved during discovery

    For aliased imports (import X as Y):
    - alias: The local name (Y) if different from module_name
    """
    module_name: str
    level: int = 0
    relative_name: str | None = None
    alias: str | None = None


class RecordLinkage(Enum):
    """Linkage mode for records (classes)."""
    DEFAULT = "default"
    NATIVE = "native"        # C++ class import (fields only)
    NATIVE_C = "native_c"    # C struct import (fields only)


class FunctionLinkage(Enum):
    """Linkage mode for functions."""
    DEFAULT = "default"
    NATIVE = "native"        # C++ import (stub, no body)
    NATIVE_C = "native_c"    # C import (stub, no body)
    EXPORT_C = "export_c"    # C export (has body)


@dataclass
class TpyFunction:
    """Function definition.

    For generic functions like def first[T](items: list[T]) -> T:
    - type_params stores the type parameter names (e.g., ["T"])
    - type_param_bounds stores bounds for each bounded type param (e.g., {"T": Comparable})
    """
    name: str
    params: list[tuple[str, TpyType]]
    return_type: TpyType
    body: list[TpyStmt]
    is_noalloc: bool = False
    is_readonly: bool = False
    readonly_opt_out: bool = False
    is_pure: bool = False
    is_override: bool = False
    hides_parent: bool = False
    is_overload_stub: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    is_property_getter: bool = False
    is_property_setter: bool = False
    property_name: str | None = None  # for setter: which property it belongs to
    is_consuming: bool = False
    # Transient: True only during parsing for @auto_readonly methods.
    # After _clone_auto_readonly runs, both clones have auto_readonly=False.
    auto_readonly: bool = False
    # Set on the mutable clone produced by _clone_auto_readonly.
    # Used instead of params-list identity to detect mutable+const clone pairs.
    is_auto_readonly_mutable_clone: bool = False
    # Set on both clones after _clone_auto_readonly resolves AutoReadonlyType
    # in params. Tells sema/codegen not to blanket-apply readonly to all params
    # (each param already carries ReadonlyType or not from the clone).
    auto_readonly_params_resolved: bool = False
    # Transient: True when self: auto_own[Self] is detected.
    # After _clone_auto_own runs, both clones have auto_own=False.
    auto_own: bool = False
    # Set on the borrowing clone produced by _clone_auto_own.
    is_auto_own_borrowing_clone: bool = False
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: str | None = None
    native_function: bool = False
    native_preserves_refs: bool = False
    cpp_template: str | None = None
    is_stub: bool = False
    value_ptr_coercion: bool = False
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "tpy.extern.DefaultInt"}
    defaults: list['TpyExpr | None'] = field(default_factory=list)  # len == len(params); None = no default
    keyword_only_start: int | None = None  # index into params where keyword-only begins
    vararg_name: str | None = None  # name of *args parameter
    vararg_type: 'TpyType | None' = None  # element type T from *args: T
    kwarg_name: str | None = None  # name of **kwargs parameter
    kwarg_type: 'TpyType | None' = None  # TypedDict type from **kwargs: Unpack[TD]
    error_return: str | None = None  # @error_return(E) exception type name
    builtin_decorator_key: str | None = None  # @builtin_decorator("tpy.readonly")
    builtin_function_key: str | None = None  # @builtin_function("tpy.extern.native_global")
    is_generator: bool = False  # Set by parser: body contains yield
    generator_yield_type: 'TpyType | None' = None  # Set by sema: T from Iterator[T]
    generator_locals: 'list[tuple[str, TpyType]] | None' = None  # Set by sema: local vars for struct fields
    loc: SourceLocation | None = None

    @property
    def is_extern_c(self) -> bool:
        return self.linkage == FunctionLinkage.EXPORT_C

    @property
    def is_export(self) -> bool:
        return self.linkage == FunctionLinkage.EXPORT_C

    @property
    def extern_name(self) -> str | None:
        return self.native_name


@dataclass
class TpyRecord:
    """Record (class) definition.

    For generic records like Stack[T]:
    - type_params stores the type parameter names (e.g., ["T"])
    - type_param_kinds stores the kind of each type param (TYPE or INT)
    - type_param_bounds stores bounds for each bounded type param (e.g., {"T": Comparable})

    For generic records with integer type params like Matrix[T, N: int]:
    - type_params = ["T", "N"]
    - type_param_kinds = [TYPE, INT]

    For class inheritance:
    - bases stores the parsed base types (classes or protocols)
    - Classification into parent class vs protocol implementations is done in sema
    """
    name: str
    fields: list[FieldInfo]
    methods: list[TpyFunction] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)
    type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    bases: list[TpyType] = field(default_factory=list)
    linkage: RecordLinkage = RecordLinkage.DEFAULT
    native_name: str | None = None
    is_nocopy: bool = False
    is_frozen: bool = False
    is_typed_dict: bool = False
    is_total_false: bool = False  # TypedDict(total=False): all fields Optional
    builtin_type_key: str | None = None
    pending_macros: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    nested_records: list[TpyRecord] = field(default_factory=list)
    nested_enums: list[TpyEnum] = field(default_factory=list)
    loc: SourceLocation | None = None

    @property
    def init_method(self) -> Optional[TpyFunction]:
        """Get __init__ method if present."""
        for m in self.methods:
            if m.name == "__init__":
                return m
        return None

    @property
    def del_method(self) -> Optional[TpyFunction]:
        """Get __del__ method if present."""
        for m in self.methods:
            if m.name == "__del__":
                return m
        return None


@dataclass
class TpyProtocol:
    """Protocol definition for structural subtyping."""
    name: str
    methods: list[MethodSignature]
    fields: list[tuple[str, TpyType]] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)
    parent_protocols: list[str] = field(default_factory=list)
    is_dynamic: bool = False
    cpp_concept: str | None = None
    loc: SourceLocation | None = None


@dataclass
class TpyEnum:
    """Enum definition -- symbolic constants grouped under a named type."""
    name: str
    members: list[tuple[str, int, SourceLocation | None]]  # auto() already resolved to int by parser
    is_int_enum: bool = False
    underlying_type_name: str | None = None  # e.g. "int", "Int8", "UInt32"
    loc: SourceLocation | None = None


@dataclass
class ParseWarning:
    """A warning generated during parsing."""
    message: str
    loc: SourceLocation | None


@dataclass
class ModuleDirectives:
    """Module-level compiler directives from # tpy: comments."""
    # Each entry: (include_path, platform_filter_or_None)
    includes: list[tuple[str, str | None]] = field(default_factory=list)
    # Each entry: (lib_name, platform_filter_or_None)
    link_libs: list[tuple[str, str | None]] = field(default_factory=list)
    native_module: bool = False
    # Override C++ namespace (replaces tpyapp::module_name)
    cpp_namespace: str | None = None


@dataclass
class TpyModule:
    """Top-level module."""
    records: list[TpyRecord]
    functions: list[TpyFunction]
    protocols: list[TpyProtocol] = field(default_factory=list)
    enums: list[TpyEnum] = field(default_factory=list)
    top_level_stmts: list[TpyStmt] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)  # Original source lines for source mapping
    # Import tracking: module_name -> set of (original_name, local_name) tuples (for "from X import Y as Z")
    #                  module_name -> None (for "import X")
    imports: dict[str, set[tuple[str, str]] | None] = field(default_factory=dict)
    # True when "from tpy import *" was used (triggers full tpy registration in sema)
    tpy_star_import: bool = False
    # Set of module names that had 'from X import *'
    star_imports: set[str] = field(default_factory=set)
    # Modules to resolve as files: {module_name: line_number}
    user_module_imports: dict[str, int] = field(default_factory=dict)
    # Module aliases from "from . import submod" -> {canonical_name: local_name}
    module_aliases: dict[str, str] = field(default_factory=dict)
    # Modules that had bare `import X` statements (needed for module binding in sema)
    bare_module_imports: set[str] = field(default_factory=set)
    # Type aliases (e.g., Shape = Circle | Rect) -> (resolved type, source location)
    type_aliases: dict[str, tuple[TpyType, SourceLocation | None]] = field(default_factory=dict)
    # Parser warnings (e.g., imports after non-import code)
    parse_warnings: list[ParseWarning] = field(default_factory=list)
    # Module-level # tpy: directives
    directives: ModuleDirectives = field(default_factory=ModuleDirectives)

    def all_records(self) -> list[TpyRecord]:
        """All records including nested, in definition order (depth-first)."""
        result: list[TpyRecord] = []
        def collect(records: list[TpyRecord]) -> None:
            for r in records:
                result.append(r)
                collect(r.nested_records)
        collect(self.records)
        return result

    def all_enums(self) -> list[TpyEnum]:
        """All enums including those nested inside records, in definition order."""
        result: list[TpyEnum] = list(self.enums)
        def collect(records: list[TpyRecord]) -> None:
            for r in records:
                result.extend(r.nested_enums)
                collect(r.nested_records)
        collect(self.records)
        return result


def is_super_del_call(stmt: TpyStmt) -> bool:
    """Check if a statement is a super().__del__() call."""
    if isinstance(stmt, TpyExprStmt):
        expr = stmt.expr
        if isinstance(expr, TpyMethodCall) and expr.method == "__del__":
            return expr.super_parent_type is not None
    return False


def collect_name_refs(expr: TpyExpr) -> set[str]:
    """Collect all name references in an expression tree.

    Uses TpyExpr.children() for generic traversal. TpyCall.func is a
    TpyName child, so callable variable names are captured automatically.
    """
    names: set[str] = set()
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, TpyName):
            names.add(node.name)
        else:
            stack.extend(node.children())
    return names
