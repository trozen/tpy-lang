"""
TurboPython AST node definitions.

Pure data definitions: dataclasses, ParseError, SourceLocation.
No parsing logic lives here.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field, is_dataclass
from enum import Enum, IntEnum
from typing import Any, Callable, Iterator, Literal, Optional, TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, FieldInfo, FunctionInfo,
    MethodSignature, TypeParamKind, LiteralValue, FunctionLinkage,
)


# Source location for error reporting and source mapping

@dataclass
class SourceLocation:
    """Source code location for error reporting and source mapping."""
    line: int  # 1-indexed line number
    column: int = 0  # 0-indexed column
    file: str | None = None  # Source file path (optional)
    end_line: int | None = None  # 1-indexed last line of the node, when known


class ParseError(Exception):
    """Error during parsing.

    Accepts either an ast.AST node (for parse-time errors where the ast is
    available) or a SourceLocation (for resolver-time errors where only the
    walked TypeRefNode's loc is known). Lineno is populated from whichever
    is provided.
    """
    def __init__(self, message: str, node: Optional[ast.AST] = None,
                 *, loc: Optional['SourceLocation'] = None):
        self.node = node
        self.message = message
        if node is not None and hasattr(node, 'lineno'):
            self.lineno = node.lineno
        elif loc is not None:
            self.lineno = loc.line
        else:
            self.lineno = None
        loc_str = f" at line {self.lineno}" if self.lineno else ""
        super().__init__(f"{message}{loc_str}")

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        if self.lineno:
            return f"{filename}:{self.lineno}: error: {self.message}"
        return f"{filename}: error: {self.message}"


class ResolutionFailure(ParseError):
    """Recoverable name-resolution failure raised by `TypeResolver`.

    Signals that a `TpyTypeRef` referred to a name the resolver could not
    bind ("Unknown type", "Unknown generic type", "Unsupported qualified
    type").  Lenient callers (macro-fragment resolution) catch this
    specifically and substitute a `NominalType(name, args)` placeholder;
    everyone else treats it as a regular `ParseError`.
    """


# Unresolved type-syntax nodes.
#
# Parser emits these in annotation positions instead of constructing TpyType
# directly. Sema's resolve_type_ref walks them against the TypeDef registry,
# local type-parameter scope, and structural wrappers to produce TpyType.
#
# Four node kinds cover all Python type syntax:
#   - TpyTypeRef       : Name or Name[args]. Handles primitives, generics,
#                        structural wrappers expressed via subscript
#                        (Ptr[T], Own[T], Optional[T], Readonly[T], tuple[T1,T2],
#                        Array[T, N], ...), qualified names (Outer.Inner), type
#                        parameters, Self, None. Integer args (Array[T, N])
#                        sit alongside type args.
#   - TpyUnionRef      : T | U | ... (Python BinOp with BitOr).
#   - TpyCallableRef   : Callable[[P1, P2], R] or Fn[[P1, P2], R] -- the
#                        list-shaped param group doesn't fit a uniform args
#                        tuple.
#   - TpyLiteralRef    : Literal[v1, v2, ...] where the args are values, not
#                        types.

@dataclass(frozen=True)
class TpyTypeRef:
    """Named type reference with optional type arguments.

    `name` is the raw source identifier, possibly dotted for qualified
    references ("Outer.Inner", "module.Name"). Resolution (primitive lookup,
    builtin/user registry lookup, type-parameter substitution, enum/record
    qname minting) happens in sema.
    """
    name: str
    args: tuple['ResolverInputNode | int', ...] = ()
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class TpyUnionRef:
    """Union-syntax type reference: T | U | ..."""
    members: tuple['ResolverInputNode', ...]
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class TpyCallableRef:
    """Callable[[P1, P2], R] or Fn[[P1, P2], R]."""
    kind: Literal["Callable", "Fn"]
    params: tuple['ResolverInputNode', ...]
    return_type: 'ResolverInputNode'
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class TpyLiteralRef:
    """Literal[v1, v2, ...] -- values, not types."""
    values: tuple[LiteralValue, ...]
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class TpyInferFromDefaultRef:
    """Pending marker used by the parser on a FieldInfo whose type could
    not be inferred from its default-value expression at parse time
    (e.g. `Red = auto()` in a class whose `Enum` base was shadowed).

    Emitted instead of raising "Cannot infer type for field 'X'" at parse
    time so that any sema-time base resolution errors on the enclosing
    record fire first. The post-parse `resolve_refs` pass handles this
    node explicitly in the field loop and surfaces the inference error
    as a SemanticError only when base resolution has already succeeded.
    """
    loc: SourceLocation | None = None


# ResolverInputNode is the subset of TypeRefNode that TypeResolver.resolve
# accepts: four parser-walker outputs (named ref, union, callable, literal).
# TpyInferFromDefaultRef is NOT a resolver input -- it's a field-storage
# marker that sema catches explicitly before calling the resolver.
type ResolverInputNode = TpyTypeRef | TpyUnionRef | TpyCallableRef | TpyLiteralRef
type TypeRefNode = ResolverInputNode | TpyInferFromDefaultRef


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
    from ..typesys import FunctionInfo, RecordInfo, ResolvedBinop, ResolvedUnaryop


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
    int_enum_coercion: 'NominalType | None' = None  # Set by sema: IntEnum arithmetic coerced to underlying type
    divisor_non_zero: bool = False  # Set by sema: divisor provably non-zero, skip div-zero check
    # Set by sema on a chained comparison's synthetic pairs past the first:
    # the right operand runs only if the preceding compare passed, while the
    # left is the previous pair's right and has already evaluated.
    cond_right: bool = False

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

    For generic function calls like first[int32](items):
    - type_args stores the explicit type arguments (e.g., (int32,))
    - inferred_type_args is set by sema for codegen (resolved from inference or explicit)
    """
    func: TpyExpr  # TpyName for simple calls; arbitrary TpyExpr for expression callees
    args: list[TpyExpr]
    # Between parse and the post-parse resolve_refs pass,
    # call_type and type_args may hold TypeRefNode in place of TpyType.
    # All readers post-pre-pass see TpyType.
    call_type: 'TpyType | TypeRefNode | None' = None  # For generic instantiation like MyContainer[T, N]()
    type_args: 'tuple[TpyType | TypeRefNode | None, ...]' = ()  # Explicit type args for generic function calls: func[T](args)
    inferred_type_args: tuple[TpyType, ...] | None = None  # Set by sema for generic function calls
    type_args_parse_error: str | None = None  # Set if subscript had args that couldn't be parsed as types
    subscript_callee: 'TpyExpr | None' = None  # Set by parser: fns[0](args) -> stores TpySubscript(fns, 0) for sema fallback
    kwargs: dict[str, TpyExpr] = field(default_factory=dict)  # Keyword arguments (limited support)
    double_star_unpack: 'TpyExpr | None' = None  # **expr unpacking at call site
    kwarg_td_call: 'TpyExpr | None' = None  # Set by sema: synthetic TypedDict construction for **kwargs
    resolved_import: tuple[str, str] | None = None  # Set by parser: (module, name) for resolved imports
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved function overloads
    enum_from_value: NominalType | None = None  # Set by sema for enum value lookup: Color(0)
    isinstance_var: str | None = None        # Set by sema: variable name being isinstance-checked
    isinstance_type: TpyType | None = None   # Set by sema: resolved type being checked for
    isinstance_is_protocol: bool = False     # Set by sema: protocol isinstance (if constexpr)
    isinstance_type_param: bool = False      # Set by sema: subject is a non-poly class-bounded type param -> compile-time tpy::isinstance_static
    # Set by sema (>0) when the isinstance source is an owning wrapper
    # (Box[Pet]/Rc[Pet]): the dispatch target is the polymorphic payload
    # reached through `depth` reference-returning __deref__ steps, not the
    # variable itself. Drives deref-view narrowing (the wrapper's own type is
    # never narrowed) and the deref-payload-pointer cast in codegen.
    isinstance_deref_depth: int = 0
    cast_target_type: TpyType | None = None  # Set by sema for typing.cast(T, x): the resolved target type
    cast_source_is_any: bool = False         # Set by sema for typing.cast: True iff source's static type is Any
    compile_time_assert: bool = False        # Set by sema for assert_send[T]()/assert_sync[T](): checked in sema, elided in codegen
    macro_expansion: 'TpyExpr | None' = None  # Set by sema: replacement expr from @call_macro
    dunder_call: 'TpyMethodCall | None' = None  # Set by sema: obj(args) -> obj.__call__(args)
    # D16 v1.5: try/catch lambda forms. Inner __getattr__ call is stored here;
    # codegen wraps it in a lambda that catches AttributeError.
    dyn_hasattr_call: 'TpyMethodCall | None' = None       # hasattr(obj, "name") -> bool
    dyn_getattr_default_call: 'TpyMethodCall | None' = None  # getattr(obj, "name", default) -> T
    # Method-local type params marked representational (`Ptr[U] -> Ptr[T]` in
    # the callee body) whose substituted bound is @dynamic AND whose inferred
    # type arg is a structural conformer needing Adapter wrap. Set by sema
    # during call analysis; consulted by codegen to substitute the C++ template
    # arg `U -> Adapter<T_sub, U_sub>`. None when the call doesn't need any
    # substitution. Empty frozenset is never written -- absence is None.
    representational_subst_params: frozenset[str] | None = None

    @property
    def func_name(self) -> str:
        """Name of the callee. Only valid when func is a TpyName (simple call)."""
        assert isinstance(self.func, TpyName), f"func_name on non-Name callee: {type(self.func).__name__}"
        return self.func.name

    @property
    def maybe_func_name(self) -> str | None:
        """Name of the callee, or None if the callee isn't a TpyName
        (subscript-callee `arr[0]()`, expression callee `(get())()` etc.).
        Use when probing whether a TpyCall targets a known free function
        without first narrowing the callee shape.
        """
        return self.func.name if isinstance(self.func, TpyName) else None

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
    # May hold TypeRefNode pre-sema-pre-pass.
    type_args: 'tuple[TpyType | TypeRefNode | None, ...]' = ()  # Explicit type args for module.func[T](args) syntax
    type_args_parse_error: str | None = None  # Set if subscript had args that couldn't be parsed as types
    subscript_callee: 'TpyExpr | None' = None  # Indexed field alternative to an explicit generic method call
    is_static_call: bool = False  # Set by sema for ClassName.staticmethod() calls
    # Set by sema alongside is_static_call: the resolved owning record. Codegen
    # needs it when the receiver's spelling names no record itself (`cls`).
    static_call_owner: Optional['RecordInfo'] = None
    super_parent_type: Optional[TpyType] = None  # Set by sema for super().method() calls
    unbound_self_parent_type: Optional[TpyType] = None  # Set by sema for BaseN.method(self, ...) calls on an ancestor
    user_module_call: Optional[str] = None  # Set by sema for module.func() calls to user modules
    builtin_module_call: Optional[str] = None  # Set by sema for builtin module.func() calls (canonical module name)
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved method overloads
    inferred_type_args: tuple[TpyType, ...] | None = None  # Set by sema for generic builtin module calls
    deref_depth: int = 0  # Set by sema: number of __deref__ steps applied before method resolution
    # Set by sema to the narrowed subclass when this method resolves through a
    # deref-view narrowing (`if isinstance(rc, Dog): rc.bark()`); codegen casts
    # the deref payload pointer to it instead of emitting a plain __deref__ call.
    deref_narrowed_to: TpyType | None = None
    ptr_non_null: bool = False  # Set by sema: receiver is a provably non-null Ptr (or Ptr[readonly[T]])
    is_callable_field: bool = False  # Set by sema: method name is a Callable-typed field
    macro_expansion: 'TpyExpr | None' = None  # Set by sema: replacement expr from @call_macro
    typed_dict_get_field: str | None = None  # Set by sema: td.get("key") -> field access
    typed_dict_get_optional: bool = False  # Set by sema: total=False field, absent by default
    fstr_expansion: 'TpyExpr | None' = None  # Set by sema: inlined FStr method body
    is_nested_constructor: bool = False  # Set by sema: Outer.Inner() nested record constructor
    is_nested_enum_constructor: bool = False  # Set by sema: Outer.Kind(v) nested enum from_value
    nested_type_name: str | None = None  # Set by sema: dotted name for nested type calls
    # See TpyCall.representational_subst_params for the contract.
    representational_subst_params: frozenset[str] | None = None

    def children(self) -> list[TpyExpr]:
        if self.fstr_expansion is not None:
            return [self.fstr_expansion]
        if self.macro_expansion is not None:
            return [self.macro_expansion]
        callee = self.subscript_callee if self.subscript_callee is not None else self.obj
        return [callee] + list(self.args) + list(self.kwargs.values())


@dataclass
class TpyFieldAccess(TpyExpr):
    """Field access on a value or pointer."""
    obj: TpyExpr
    field: str
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access
    deref_depth: int = 0  # Set by sema: number of __deref__ steps applied before field lookup
    # Set by sema to the narrowed subclass when this field access resolves
    # through a deref-view narrowing; codegen casts the deref payload pointer.
    deref_narrowed_to: TpyType | None = None
    ptr_non_null: bool = False  # Set by sema: receiver is a provably non-null Ptr (or Ptr[readonly[T]])
    # Set by sema BEFORE the target of an assignment is analysed as a read:
    # the write position is the one place a property is not its getter, so
    # this access keeps the field-access node kind and the setter is looked
    # up by the field name it still carries.
    is_write_target: bool = False
    # Set by sema when the access resolves to a @property. A READ consumes it
    # immediately -- the node becomes the getter call -- so an access still
    # carrying it is a write target, and this is the fact the setter lookup
    # and the augmented-assignment reject key on.
    resolved_property_getter: 'FunctionInfo | None' = None
    property_setter: bool = False  # Set by sema: assignment target is a property setter
    property_setter_call: 'TpyMethodCall | None' = None  # Set by sema: setter method call for codegen
    dyn_getattr_call: 'TpyMethodCall | None' = None  # Set by sema: __getattr__ fallback method call (D16)
    dyn_setattr_call: 'TpyMethodCall | None' = None  # Set by sema: __setattr__ fallback method call (D16)
    dyn_delattr_call: 'TpyMethodCall | None' = None  # Set by sema: __delattr__ fallback method call (D16)
    unbound_self_parent_type: Optional[TpyType] = None  # Set by sema for BaseN.field access on an ancestor subobject
    class_constant_owner: Optional['RecordInfo'] = None  # Set by sema: RecordInfo for ClassName.X class-constant access; codegen emits <cpp_qname>::<member>
    module_var_access: Optional[tuple[str, str]] = None  # Set by sema for `pkg.sub.X` variable access on a dotted module: (module_qname, var_name)
    accessed_field_is_interior: bool = False  # Set by sema: matched field is `unsafe_interior_mutable[...]` (outside the readonly boundary)
    native_field_name: Optional[str] = None  # Set by sema: the matched field's native_field() C++ rename (own-fields-first lookup, so a subclass redeclaration shadows an ancestor's rename)
    enum_member_of: Optional[TpyType] = None  # Set by sema: type-level enum member access (Color.RED); the enum NominalType

    @property
    def hidden_call(self) -> 'TpyMethodCall | None':
        """The user method this access dispatches to, if any.

        A `__getattr__`/`__setattr__`/`__delattr__` access keeps the
        field-access node kind but renders as a method call, so a consumer
        reading the node kind alone sees an inert field read. Anything
        deciding whether a render may be duplicated or reordered has to ask
        here rather than infer from the node type. A property READ is not
        one of these: it becomes a `TpyMethodCall` outright, so the node
        kind answers for it.

        Covers the write slots too, though no current consumer can see an
        assignment target: this is the one enumeration, so it enumerates.
        A slot added to the node and not to this list silently un-vetoes
        whatever it feeds -- `test_hidden_call_covers_every_call_slot`
        (`tpyc/parse/test_nodes.py`) fails instead.
        """
        return (self.property_setter_call
                or self.dyn_getattr_call or self.dyn_setattr_call
                or self.dyn_delattr_call)

    def children(self) -> list[TpyExpr]:
        return [self.obj]


def is_property_getter_read(expr: TpyExpr) -> bool:
    """Whether `expr` is a `@property` read sema has already resolved.

    A property read IS its getter call, and sema makes it one
    (`become_method_call`), so the node kind no longer separates the two
    spellings -- the getter's own `FunctionInfo` does. A getter is pruned
    from the record's method table, so no user-spelled call can answer True
    here, and the same node answering twice is a RE-analysis, not a fresh
    one.
    """
    if not isinstance(expr, TpyMethodCall):
        return False
    fi = expr.resolved_function_info
    return fi is not None and fi.is_property_getter


def become_method_call(node: TpyFieldAccess, *, method: str,
                       args: list[TpyExpr],
                       fi: 'FunctionInfo') -> TpyMethodCall:
    """Turn `node` INTO the method call it dispatches to, in place.

    Identity is the whole argument: every fact sema recorded while analysing
    the read is filed in an IdentityMap keyed on this object, so the type
    stamp, the loc and the narrowing facts all survive a change of kind that
    building a fresh node would strand. The AST dataclasses carry no
    `__slots__`, so the swap is the ordinary Python one, and no parent slot
    is written -- nothing walks the body.

    The receiver-shape markers do NOT transfer. They were stamped for a
    FIELD lookup on the receiver, and the accessor dispatch reaches the
    getter through its own resolution -- a `Ptr` receiver's deref, a
    narrowed deref view, an unproven Optional. Carrying the field's answers
    onto the call would make the dispatch spell hops the getter render does
    not take.
    """
    call = TpyMethodCall(obj=node.obj, method=method, args=args, loc=node.loc)
    call.resolved_function_info = fi
    node.__dict__.clear()
    node.__dict__.update(call.__dict__)
    node.__class__ = TpyMethodCall
    return node  # type: ignore[return-value]


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
    # Set by sema when the iterable yields Own[T]: codegen moves a bare last-use
    # element into the result instead of copying (the consuming for-append move).
    owns_elements: bool = False


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
    # Set by sema on the frame route: the generator function this expression
    # creates, and the analyzed reads of the enclosing names it takes as
    # reference captures (the function's params after its source, in order).
    frame_func: 'TpyFunction | None' = None
    frame_captures: 'tuple[TpyName, ...]' = ()
    # Captures something may REBIND between two pulls. A reference field
    # follows a rebind of a variable that lives in place; a local the enclosing
    # body keeps behind a pointer it re-seats has no such field to follow.
    frame_rebindable: 'tuple[str, ...]' = ()
    # A `range(...)` source's bounds: the function takes them by value as its
    # leading params and loops over a range of them, in place of a source.
    frame_range_args: 'tuple[TpyExpr, ...]' = ()
    # The call the expression IS, as borrow provenance asks it: the frame
    # function over the source (or range bounds) and the captures.
    frame_creation: 'TpyCall | None' = None

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
    # Set by sema: capture-list FrameType for Send/Sync conversion checks
    frame_type: 'TpyType | None' = None

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
    enum_from_name: 'NominalType | None' = None    # Set by sema for Color["Red"] name lookup
    bounds_safe: bool = False  # Set by sema: index provably in [0, len(obj)), skip bounds check
    is_stepped_slice: bool = False  # Set by sema: slice has step (a[::2])
    slice_function_info: 'FunctionInfo | None' = None  # Set by sema: resolved __getitem__ for slice
    # Set by sema: the resolved user-record single-key __getitem__ this
    # subscript calls. Distinguishes a real method CALL (whose return follows
    # the call convention -- e.g. a pointer-repr Optional returns T*) from a
    # container ELEMENT read (a storage lvalue); the borrow/storage and
    # value-category classifiers key on it.
    getitem_function_info: 'FunctionInfo | None' = None
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
class TpyAwait(TpyExpr):
    """`await x` expression. Lowered by PR 3 (resumable-frame codegen)
    to a poll-and-park sequence on the operand's Awaitable[T] conformance.
    Sema rejects awaits outside an async def body.

    Sema attaches one of two resolution shapes:
    - `awaited_async_func_name`: inline mode -- the operand is a direct
      call to a known async def. Codegen emits the sub-coroutine struct
      as a frame field via `std::optional<__<name>Coro>` and constructs
      it in place from the call's args.
      For async methods, `awaited_method_owner_type` carries the
      receiver's NominalType (including its type args) so the sub-coro
      struct can be uniquely named (`__coro_<Record>_<method>`) and
      qualified with the receiver's class-level template args.
    - `awaited_task_inner`: erased mode -- the operand has type
      `Task[T]`. Codegen emits the sub-future field as
      `std::optional<::tpy::Task<T>>` and moves the operand value in.
    Exactly one is set on a successfully-analyzed TpyAwait.

    For generic async defs (`async def f[T](...) -> T`), codegen reads
    the inferred type args off the operand call node directly
    (`value.inferred_type_args`) to qualify the sub-coro struct name as
    `__coro_<name><A, B>`. The await expression's substituted result
    type is recorded via the standard expression-type table.

    `suspension_index` is the ordinal (in source order) within the
    enclosing async def, used to name __sub_<i> fields and
    S_AFTER_AWAIT_<i> states.
    """
    value: TpyExpr
    awaited_async_func_name: str | None = field(default=None, kw_only=True)
    awaited_method_owner_type: 'NominalType | None' = field(default=None, kw_only=True)
    awaited_task_inner: 'TpyType | None' = field(default=None, kw_only=True)
    # Prebuilt-slot mode: the operand is a bound coroutine handle
    # (ConcreteCoroType local) -- the await polls the local's own frame
    # slot in place (no sub-future field, no allocation). Holds the name.
    awaited_prebuilt_slot: str | None = field(default=None, kw_only=True)
    suspension_index: int | None = field(default=None, kw_only=True)
    # The awaited coroutine's result is a borrow (pointer payload aliasing
    # caller-durable storage), per the callee's declared-return form. Set
    # by sema's await analysis; value_category / binding classification
    # read it instead of re-deriving the callee convention.
    await_result_is_borrow: bool = field(default=False, kw_only=True)

    def children(self) -> list[TpyExpr]:
        return [self.value]


class RebindStorage(Enum):
    """Where an rvalue rebind of a reference-typed name writes -- decided by
    sema's alias-rebind storage pass, consumed by the lowering. IN_PLACE:
    through the name into its current storage (the superseded object dies
    here, as under CPython). OWN: storage private to this site, because a
    live loan may still point at the current storage."""
    IN_PLACE = "in_place"
    OWN = "own"


class TryTier(Enum):
    """How a `try` statement dispatches -- decided by sema's tier
    classification, read by the lowering and the resumable CFG. RETURN:
    ReturnException handlers, goto-based dispatch. THROW: ordinary
    handlers, a C++ try/catch. FINALLY_ONLY: no handlers, cleanup only."""
    RETURN = "return"
    THROW = "throw"
    FINALLY_ONLY = "finally_only"


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
    """Variable declaration with optional initializer.

    `type` may temporarily hold a TypeRefNode when emitted by the parser at
    annotation sites; the sema writer (`_analyze_var_decl`) calls
    `TypeOperations.resolve_type_ref` to produce a TpyType and writes it
    back in place. All downstream sema/codegen reads see only TpyType.
    """
    name: str
    type: Optional[TpyType | TypeRefNode]
    init: Optional[TpyExpr]
    linkage: VarLinkage = VarLinkage.DEFAULT
    native_name: str | None = None
    # TODO: is_final (and linkage) could be generalized into a modifiers set
    # (e.g. modifiers: set[str]) to avoid per-feature boolean fields
    is_final: bool = False  # Set by sema for Final[T] constant globals
    # A synthetic temp scoped to its enclosing init/function body -- never
    # promote it to a module global even at top level (set by the tuple-unpack
    # desugar for its hidden evaluate-all-then-bind temps).
    module_init_local: bool = field(default=False, kw_only=True)
    # Set by sema: union assignment narrowing facts for codegen
    then_type_facts: dict[str, TpyType] = field(default_factory=dict)
    # Stamped by liveness: names still live AFTER this statement (see
    # analyze_last_uses). None means the liveness walk never reached this
    # node, where the alias-rebind pass stays silent.
    live_names_after: frozenset[str] | None = field(default=None, repr=False)
    # Stamped by sema on an rvalue REBIND of a reference-typed local -- the
    # sites the alias-rebind storage pass decides (see RebindStorage); read
    # by the lowering and the frame layout.
    rebind_storage: RebindStorage | None = field(default=None, repr=False)

    def exprs(self) -> list[TpyExpr]:
        return [self.init] if self.init else []


@dataclass
class TpyTupleUnpack(TpyStmt):
    """Tuple unpacking: a, b = expr. None in targets means discard (_)."""
    targets: list[str | None]
    value: TpyExpr
    # The head of a `for a, b in ...` loop: `value` is the synthetic
    # per-iteration holder the target was desugared into, fresh on every
    # iteration, so an `Own` element off it is consumed rather than
    # borrowed. Sema sets it off the enclosing `TpyForEach.is_tuple_unpack`,
    # so a macro-built loop head is marked like a parsed one.
    is_loop_head: bool = False
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
    # Stamped by liveness -- see TpyVarDecl.live_names_after.
    live_names_after: frozenset[str] | None = field(default=None, repr=False)
    # Stamped by sema for a name target -- see TpyVarDecl.rebind_storage.
    rebind_storage: RebindStorage | None = field(default=None, repr=False)

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
class TpyDelAttr(TpyStmt):
    """Delete statement for attributes: del obj.foo, obj2.bar, ... (D16 Phase 3)"""
    targets: list[TpyFieldAccess]

    def exprs(self) -> list[TpyExpr]:
        return list(self.targets)


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
    # Set by sema: the value is a reference-type local whose storage an
    # enclosing finally body still reads. Codegen captures a borrow before
    # the inline finally chain and materializes (moves) the return value
    # after it, so finally mutations stay visible (CPython aliasing).
    finally_deferred_capture: bool = False

    def exprs(self) -> list[TpyExpr]:
        return [self.value] if self.value else []


def is_none_return(stmt: TpyReturn) -> bool:
    """Whether the return's operand is the None literal -- the canonical
    spelling of a bare `return` (every producer synthesizes it), so this is
    the ONE test for "this return names no value"; whether a None operand
    renders a value is decided by the return slot's type, not here."""
    v = stmt.value
    while isinstance(v, TpyCoerce):
        v = v.expr
    return v is None or isinstance(v, TpyNoneLiteral)


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
    enum_iterable: 'NominalType | None' = None  # set by sema when iterating over enum type
    elem_type: 'TpyType | None' = None  # set by sema: resolved element type for codegen
    is_tuple_unpack: bool = False  # set by parser: synthetic loop var for tuple destructuring
    const_loop_var: bool = False  # set by sema: loop var is never mutated, safe for const auto&
    hoist_loop_var: bool = False  # set by sema: loop var used after loop, needs pre-declaration
    # Set by sema at the ITER registration: the lvalue iterable names storage
    # no loan key can spell (`self.grid.rows[i]`, `table[k].cells`), so nothing
    # matches a mutation of what is iterated against the iteration's borrow.
    # Codegen refuses such a loop rather than hand out unguarded references.
    iter_borrow_unplaceable: bool = False
    consuming_iter_fi: 'FunctionInfo | None' = None  # set by sema: consuming __iter__ overload at last use
    is_async: bool = False  # Set by parser: `async for` (lowers to __aiter__/await __anext__ inside async def)
    async_aiter_type: 'NominalType | None' = None  # set by sema for async-for: the DEFINING record of __anext__ (an ancestor of the iterator when inherited), used by codegen to name the __anext__ coro struct

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
    deref_depth: int = 0  # Set by sema: __deref__ steps to peel before calling __raise__ (Phase 20)
    # The exception's __init__, so codegen lowers each ctor arg through the same
    # shared arg-lowering loop a normal `X(args)` construction uses (protocol /
    # covariant / optional-ptr / union / mutated-temp dispatch, not just the
    # plain coercion fallback). mutated_params is read finalized at codegen via
    # this live reference.
    resolved_ctor_init: 'FunctionInfo | None' = None
    # Set by sema when the raised class is @virtual_raise: codegen emits
    # `X(args).__raise__();` so the class's dispatching __raise__ applies.
    raise_via_virtual: bool = False

    def exprs(self) -> list[TpyExpr]:
        result = list(self.args)
        if self.raise_expr is not None:
            result.append(self.raise_expr)
        return result


@dataclass
class TpyExceptHandler:
    """A single except clause in a try statement.

    The parser expands the tuple form `except (A, B):` into one of these per
    element, so every later pass sees only ordinary single-type clauses.
    """
    exception_type: str | None  # None for bare except:
    binding: str | None         # from "as e"
    body: list[TpyStmt]
    loc: SourceLocation | None = None
    # True when this clause was one element of a tuple form. Diagnostics only:
    # lets a message about handler count name what the user actually wrote.
    from_tuple_clause: bool = False


@dataclass
class TpyTry(TpyStmt):
    """try/except/else/finally statement (both return-tier and throw-tier)."""
    try_body: list[TpyStmt]
    handlers: list[TpyExceptHandler]  # 0+ except clauses
    else_body: list[TpyStmt]         # may be empty
    finally_body: list[TpyStmt]      # may be empty
    # Set by sema; None until the tier classification runs.
    tier: TryTier | None = None
    # Set by sema on a RETURN tier: does the try body hold a call whose
    # @error_return failure this handler catches? Nothing else can enter a
    # return-tier handler (a `raise` of the error type returns from the
    # enclosing @error_return function instead of dispatching locally), so
    # when this is False the handler is dead code.
    handled_error_return: bool = False

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
    # Set by sema: True iff this ctx-manager's __exit__ returns bool (i.e.
    # can suppress exceptions). False for None-returning __exit__.
    exit_can_suppress: bool = False
    # Set by sema: True iff exc_val is typed as Optional[BaseException]
    # (i.e. the body inspects exceptions). False when exc_val: None.
    # Codegen call site passes &__exc / nullptr when True, else {}.
    exit_takes_exc_val: bool = False
    # Set by sema: True iff the manager is an lvalue, so the with-region must
    # borrow it -- a by-value ctx slot would mutate a throwaway copy.
    manager_borrowed: bool = False
    # Set by sema: False iff `__enter__` demonstrably lends storage that is NOT
    # the manager's own (a global, a parameter) -- then a target aliasing it does
    # not force the manager to outlive the statement, and forcing that anyway
    # would delay the manager's destruction past where CPython drops it.
    manager_owns_enter_result: bool = True
    # Set by the liveness pass: is the `as`-target live once this statement
    # finishes? Decides whether storage the target aliases -- an OWNED manager
    # -- has to outlive the statement; without a later read there is nothing to
    # dangle, so the manager keeps its cheap block scope. The default covers a
    # body the pass never walks, and nothing else: where the walk does run, a
    # False here is its verdict, and this is NAME liveness, which is weaker
    # than the borrow-escape question the manager's lifetime actually needs
    # (BUGS.md).
    target_read_after: bool = True
    # Set by sema for `async with`: the DEFINING record of __aenter__ /
    # __aexit__ (an ancestor of the manager type when inherited). Codegen
    # names the sub-coro struct from these, not the manager's subclass type
    # -- the struct is emitted once, for the defining record.
    aenter_owner_type: 'NominalType | None' = field(default=None, kw_only=True)
    aexit_owner_type: 'NominalType | None' = field(default=None, kw_only=True)
    # Set by sema for `async with`: __aenter__'s result is a borrow
    # (pointer Poll payload aliasing the manager), so the as-binding must
    # be an aliasing pointer frame field, not an owning copy. The const
    # twin marks a readonly-declared borrow (const T* field).
    aenter_result_is_borrow: bool = field(default=False, kw_only=True)
    aenter_result_is_const: bool = field(default=False, kw_only=True)


@dataclass
class TpyWith(TpyStmt):
    """with statement (context managers).

    `is_async=True` when this came from Python `async with`; the
    context managers' `__aenter__` / `__aexit__` are async methods and
    each call is a suspension point. Only allowed inside `async def`.
    """
    items: list[TpyWithItem]
    body: list[TpyStmt]
    is_async: bool = False

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
    # Optional-subject capture: True when the arm also matches None and the
    # binding carries the full `T | None` subject (no earlier arm covered
    # None); codegen then binds the subject instead of its dereference.
    binds_full_optional: bool = False
    # Sema-assigned binding form: True iff the captured type is a free-copy
    # scalar that codegen binds BY VALUE (a durable snapshot that survives
    # subject mutation). Default False = by-reference borrow, so a capture
    # sema's annotation pass misses degrades to a warned `auto&`, never a
    # silent alias. Single source of truth for codegen and the dangle warning.
    bind_by_value: bool = False
    # Sema-assigned type this capture binds. A nested match rebinds the
    # ENCLOSING local, so the hoist compares its binds against the outer
    # arm's: one slot cannot hold two types.
    bound_type: TpyType | None = None


def iter_capture_bindings(
    pattern: "TpyPattern",
) -> "Iterator[TpyCapturePattern | TpyAsPattern]":
    """Shared by sema (sets `bind_by_value`, drives the dangle warning) and
    codegen (builds the per-arm binding-mode map): one traversal means the two
    see the same capture set and cannot disagree about a binding's form."""
    if isinstance(pattern, TpyCapturePattern):
        yield pattern
    elif isinstance(pattern, TpyAsPattern):
        yield pattern
        yield from iter_capture_bindings(pattern.pattern)
    elif isinstance(pattern, TpyClassPattern):
        for sub in pattern.positional:
            yield from iter_capture_bindings(sub)
        for _, sub in pattern.keywords:
            yield from iter_capture_bindings(sub)
    elif isinstance(pattern, TpyOrPattern):
        for alt in pattern.patterns:
            yield from iter_capture_bindings(alt)


@dataclass
class TpyClassPattern(TpyPattern):
    """case Circle(): / case Circle(radius=r):"""
    cls: TpyExpr
    positional: list[TpyPattern]
    keywords: list[tuple[str, TpyPattern]]
    # Set by sema: resolved record type for this class pattern
    resolved_type: TpyType | None = None
    # Set by sema: True when this is a field sub-pattern matching against
    # a union-typed field (codegen emits holds_alternative + std::get)
    is_union_field_guard: bool = False


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
    # See TpyCapturePattern.binds_full_optional (applies when the inner
    # pattern is a wildcard/capture on an Optional subject).
    binds_full_optional: bool = False
    # See TpyCapturePattern.bind_by_value: the `as` binding's form.
    bind_by_value: bool = False
    # See TpyCapturePattern.bound_type: the `as` binding's bound type.
    bound_type: TpyType | None = None


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
    # Set by sema: true when the match covers every possible subject value
    # (either via a wildcard arm or by enumerating every value of a finite
    # type). Codegen relies on this to emit ``std::unreachable()`` past the
    # match end-label so non-void functions whose only exit is the match
    # don't trip -Wreturn-type.
    is_exhaustive: bool = False
    # Set by sema: true when the subject is a @dynamic-protocol / polymorphic-
    # class value (bare, Ptr, or reached through an owning wrapper's deref
    # view). Routes codegen to the dynamic_cast dispatch strategy rather than
    # variant-index / field-value matching.
    polymorphic_dispatch: bool = False

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


class OverloadForm(Enum):
    """The two overload decorators.

    OVERLOAD is CPython's `typing.overload`: bodyless stubs declaring the
    signatures of one trailing implementation, which sema specializes per
    stub. DISPATCH is `tpy.dispatch`: every variant is its own
    implementation (a body, `@native` or `@cpp_template`) and the set has
    no trailing implementation. Both register the same overload set; only
    group validation and the CPython runtime differ.
    """
    OVERLOAD = "overload"
    DISPATCH = "dispatch"


# The name prefix of every generator expression's function.
GENEXPR_FUNC_PREFIX = "__genexpr_"


@dataclass
class TpyFunction:
    """Function definition.

    For generic functions like def first[T](items: list[T]) -> T:
    - type_params stores the type parameter names (e.g., ["T"])
    - type_param_bounds stores bounds for each bounded type param (e.g., {"T": Comparable})
    """
    name: str
    # Between parse and the post-parse resolve_refs pass, params
    # and return_type for top-level (non-method) functions may hold
    # TypeRefNode in place of TpyType. All readers post-pre-pass see
    # TpyType.  `return_type` may also be None when no annotation was
    # provided -- sema substitutes VOID during the pre-pass.
    params: list[tuple[str, TpyType | TypeRefNode]]
    return_type: 'TpyType | TypeRefNode | None'
    body: list[TpyStmt]
    is_noalloc: bool = False
    is_hotpath: bool = False
    is_inline: bool = False
    is_readonly: bool = False
    readonly_opt_out: bool = False
    is_pure: bool = False
    is_override: bool = False
    hides_parent: bool = False
    # Which overload decorator the def carries, or None for a plain def.
    # Decided in the parser; sema and codegen read `is_overload_stub` for
    # "member of an overload set" and `overload_form` only where the two
    # forms differ (group validation, diagnostics).
    overload_form: OverloadForm | None = None
    is_method: bool = False
    # A `def` inside a function body (the func of a TpyNestedDef). Its
    # FunctionInfo lives in the enclosing body's namespace, never in the
    # module registry or on a record, so a registry lookup by name would
    # answer with a same-named SIBLING's verdicts.
    is_nested_def: bool = False
    is_staticmethod: bool = False
    # A @classmethod also sets is_staticmethod (no receiver param, static
    # emission); this flag only distinguishes it for diagnostics and for
    # binding `cls` to the defining record.
    is_classmethod: bool = False
    is_property_getter: bool = False
    is_property_setter: bool = False
    property_name: str | None = None  # for setter: which property it belongs to
    is_consuming: bool = False
    # Transient input to sema.method_expansion._clone_auto_readonly:
    # true for @auto_readonly methods, @property getters, methods with
    # `self: auto_readonly[Self]`, and methods with per-param
    # `auto_readonly[T]`. After cloning, both clones carry False.
    auto_readonly: bool = False
    # Set on the mutable clone produced by
    # sema.method_expansion._clone_auto_readonly; used to detect
    # mutable+const clone pairs without relying on params-list identity.
    is_auto_readonly_mutable_clone: bool = False
    # Set on both clones after _clone_auto_readonly resolves AutoReadonlyType
    # in params. Tells sema/codegen not to blanket-apply readonly to all params
    # (each param already carries ReadonlyType or not from the clone).
    auto_readonly_params_resolved: bool = False
    # Set on each clone by _clone_auto_readonly: 'strip' (mutable half) or
    # 'apply' (const half). Lets the body's construction type-args carrying an
    # `auto_readonly[...]` marker (e.g. `Rc[auto_readonly[T]](...)`) resolve per
    # overload -- the const half builds Rc[readonly[T]], the mutable Rc[T] --
    # from a single shared body. None on ordinary methods.
    auto_readonly_polarity: Literal["strip", "apply"] | None = None
    # Transient input to sema.method_expansion._clone_auto_own: true when
    # `self: auto_own[Self]` is detected. After cloning, both clones have
    # auto_own=False.
    auto_own: bool = False
    # Set on the borrowing clone produced by
    # sema.method_expansion._clone_auto_own.
    is_auto_own_borrowing_clone: bool = False
    # ...and on its consuming twin. Both halves own an independent body (the
    # consuming one deep-copies), which is what lets a consumer treat the pair
    # as two separate callables rather than one shared impl.
    is_auto_own_consuming_clone: bool = False
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: str | None = None
    native_function: bool = False
    native_preserves_refs: bool = False
    # Set when @export is applied inside an `# tpy: ext_module`: the function
    # stays DEFAULT linkage (an ordinary TPy function) but the extension glue
    # generator emits a CPython wrapper + PyMethodDef entry for it.
    exposed_to_host: bool = False
    # @copy_returns_warn: Own[V] accessor that copies where its CPython
    # namesake aliases, so sema warns at call sites.
    copy_returns_warn: bool = False
    # @native(cpp_return_type=T) -- see FunctionInfo.native_cpp_return_type.
    native_cpp_return_type: str | None = None
    cpp_template: str | None = None
    is_stub: bool = False
    value_ptr_coercion: bool = False
    type_params: list[str] = field(default_factory=list)
    # Parallel with type_params (like TpyRecord.type_param_kinds). Carries
    # TypeParamKind.INT for `def f[N: int](...)` so sema-time resolution
    # of ref-based params can tell INT-kind type params from TYPE-kind.
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)
    # Between parse and the post-parse resolve_refs pass, bounds
    # may hold TypeRefNode in place of TpyType.  All readers post-pre-pass
    # see TpyType.
    type_param_bounds: 'dict[str, TpyType | TypeRefNode]' = field(default_factory=dict)
    type_param_defaults: dict[str, str] = field(default_factory=dict)  # e.g. {"T": "tpy.extern.DefaultInt"}
    defaults: list['TpyExpr | None'] = field(default_factory=list)  # len == len(params); None = no default
    keyword_only_start: int | None = None  # index into params where keyword-only begins
    num_posonly_params: int = 0  # leading params before a '/' separator (self excluded)
    vararg_name: str | None = None  # name of *args parameter
    vararg_type: 'TpyType | TypeRefNode | None' = None  # element type T from *args: T
    kwarg_name: str | None = None  # name of **kwargs parameter
    # Between parse and the post-parse resolve_refs pass, kwarg_type
    # may hold TypeRefNode in place of TpyType.
    kwarg_type: 'TpyType | TypeRefNode | None' = None  # TypedDict type from **kwargs: Unpack[TD]
    error_return: str | None = None  # @error_return(E) exception type name
    builtin_decorator_key: str | None = None  # @builtin_decorator("tpy.readonly")
    builtin_function_key: str | None = None  # @builtin_function("tpy.extern.native_global")
    is_generator: bool = False  # Set by parser: body contains yield
    # A generator expression's function: built by sema at the expression, its
    # yield type inferred from the element, analyzed at its creation site.
    is_genexpr: bool = False
    # Params that are lexical captures of the enclosing function: the frame
    # holds each by reference whatever its type, since the enclosing body may
    # rebind the name between two pulls.
    capture_params: 'tuple[str, ...]' = ()
    # Set by sema on a genexpr's function: its deduced source slot is bound to
    # a native container LVALUE (a nested def cannot give that one its concrete
    # type), whose adaptor copies a value-tuple step result -- so an unpack
    # head must not alias reference members through it.
    genexpr_container_by_protocol: bool = False
    # The function the genexpr is written in (None at module level): what a
    # diagnostic about the body names, since the user never wrote this one.
    genexpr_owner: str | None = None
    generator_yield_type: 'TpyType | None' = None  # Set by sema: T from Iterator[T]
    # Set by sema for a generator whose yield type is an open `T`: does the
    # frame LEND what it yields (the `val_or_ref<T>` slot) or hand out a value?
    # One slot type serves the whole frame, so the provenance of every yield
    # source settles it once, at the end of body analysis.
    generic_yield_borrows: bool = False
    generator_locals: 'list[tuple[str, TpyType]] | None' = None  # Set by sema: local vars for struct fields
    forwarded_locals: 'dict[str, str] | None' = None  # Set by sema: hoisted local -> backing static-protocol param it forwards to
    is_async: bool = False  # Set by parser: `async def`. Lowered to a state-machine
                            # struct conforming to Awaitable[T] in PR 3 (codegen).
    # Send/Sync frame overrides: True from @unsafe_send/@unsafe_sync,
    # False from @nosend/@nosync, None = structural classification
    send_override: bool | None = None
    sync_override: bool | None = None
    # Set by `ClassInfo.add_method` for any function added through the macro
    # API. Lets sema diagnostics distinguish synthesized methods (e.g.
    # @dataclass __init__) from user-written ones, so messages can point the
    # user at the macro/decorator rather than at code they didn't write.
    is_macro_generated: bool = False
    # Optional name of the macro/decorator that produced this function
    # (e.g. "dataclass", "model", "total_ordering"). Set by macro modules
    # when calling `ClassInfo.add_method(..., macro_origin=...)`. Used by
    # diagnostics to name the responsible decorator in user-facing messages.
    macro_origin: str | None = None
    skip_codegen: bool = False  # Set by sema: @inline function, body inlined at call sites
    # Preserves the self annotation for methods (e.g. OwnType(SelfType),
    # AutoOwnType(SelfType), AutoReadonlyType(SelfType)).  Between parse
    # and the post-parse resolve_refs pass this may hold a
    # TypeRefNode; sema resolves before `method_expansion` reads it.
    # None when self had no explicit annotation or for non-methods.
    # Carried through `dc_replace` on auto_own / auto_readonly clones.
    self_annotation: 'TpyType | TypeRefNode | None' = None
    # Set by the parser when the method carries `@auto_readonly`.
    # `method_expansion` uses this to drive AutoReadonlyType param
    # wrapping; cleared on the auto_readonly clones once expanded.
    has_auto_readonly_decorator: bool = False
    # Unrecognized decorators on a free function, resolved as @function_macro
    # at sema time (mirrors TpyRecord.pending_macros). (qname, kwargs) pairs.
    pending_macros: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Closures registered via ctx.defer_until_sema_complete during pass 5.5;
    # drained after this function's body is type-checked so they can read
    # inferred expression types (which don't exist yet at macro-expansion time).
    pending_deferred_sema_macros: list[Any] = field(default_factory=list)
    loc: SourceLocation | None = None

    @property
    def is_overload_stub(self) -> bool:
        return self.overload_form is not None

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
    # Between parse and the post-parse resolve_refs pass, bounds
    # may hold TypeRefNode in place of TpyType.  All readers post-pre-pass
    # see TpyType.
    type_param_bounds: 'dict[str, TpyType | TypeRefNode]' = field(default_factory=dict)
    # Between parse and the post-parse resolve_refs pass, bases
    # may hold TypeRefNode in place of TpyType.  All readers post-pre-
    # pass see TpyType.
    bases: 'list[TpyType | TypeRefNode]' = field(default_factory=list)
    linkage: RecordLinkage = RecordLinkage.DEFAULT
    native_name: str | None = None
    is_nocopy: bool = False
    is_frozen: bool = False
    is_typed_dict: bool = False
    is_total_false: bool = False  # TypedDict(total=False): all fields Optional
    builtin_type_key: str | None = None
    # @virtual_raise: the class's C++ __raise__ dispatches (not `throw *this`),
    # so `raise X(args)` must route through it, not the fresh-throw peephole.
    virtual_raise: bool = False
    # @native(indirecting=True): record owns indirect (heap-backed) storage
    # of its type parameter that the compiler cannot introspect (e.g. list/
    # dict/set, or a user @native record wrapping a C++ unique_ptr).
    # Consulted by cycle detection so the type breaks recursive size cycles.
    # User TPy records with a Ptr[T] field do NOT set this -- the field walk
    # infers indirection structurally.
    is_indirecting: bool = False
    # Send/Sync trait overrides: True from @unsafe_send/@unsafe_sync,
    # False from @nosend/@nosync, None = structural auto-derive
    send_override: bool | None = None
    sync_override: bool | None = None
    # Conditional overrides from @unsafe_send/@unsafe_sync (if_params_*): a tuple of
    # marker-trait qnames every type param must satisfy for the trait to hold;
    # None = not conditional. Evaluated per instantiation.
    send_override_when: tuple[str, ...] | None = None
    sync_override_when: tuple[str, ...] | None = None
    move_override: bool | None = None   # False from @nomove; None = structural
    pending_macros: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Callbacks registered via `ClassInfo.defer_until_macros_complete()`.
    # Run after the eager macro pass so a macro can inspect the final
    # method set produced by composition (e.g. @total_ordering picking
    # an anchor among ordering ops other macros may add).
    pending_deferred_macros: list[Any] = field(default_factory=list)
    nested_records: list[TpyRecord] = field(default_factory=list)
    nested_enums: list[TpyEnum] = field(default_factory=list)
    # Bare `@export` inside an `# tpy: ext_module`: expose this class to the
    # host CPython module as a real Python type (PyType_FromSpec). Mirrors
    # TpyFunction.exposed_to_host; the record keeps DEFAULT linkage (the glue
    # is separate). Set only by the parser inside an ext_module.
    exposed_to_host: bool = False
    # The class body's leading string literal. A function/method keeps its
    # docstring as `body[0]`, but a record has no statement body to read it
    # back from, so the text is captured here at parse time. Consumed only by
    # the CPython-extension glue (`Py_tp_doc`).
    docstring: str | None = None
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

    @property
    def move_method(self) -> Optional[TpyFunction]:
        """Get __move__ method if present (custom relocating move ctor body)."""
        for m in self.methods:
            if m.name == "__move__":
                return m
        return None


@dataclass
class TpyProtocol:
    """Protocol definition for structural subtyping."""
    name: str
    methods: list[MethodSignature]
    # Between parse and the post-parse resolve_refs pass, field
    # types may hold TypeRefNode in place of TpyType.
    fields: 'list[tuple[str, TpyType | TypeRefNode]]' = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)
    # Parent protocols. Parser emits TypeRefNode (TpyTypeRef) entries; sema's
    # resolve_refs pass replaces them in place with NominalType. Generic
    # parents like `Iterable[T]` carry their type args in NominalType.type_args.
    parent_protocols: 'list[NominalType | TypeRefNode]' = field(default_factory=list)
    is_dynamic: bool = False
    cpp_concept: str | None = None
    loc: SourceLocation | None = None


@dataclass
class TpyEnum:
    """Enum definition -- symbolic constants grouped under a named type."""
    name: str
    members: list[tuple[str, int, SourceLocation | None]]  # auto() already resolved to int by parser
    is_int_enum: bool = False
    underlying_type_name: str | None = None  # e.g. "int", "int8", "uint32"
    is_native: bool = False
    native_name: str | None = None  # raw @native arg; sema normalizes to canonical ::-prefixed form
    # Per-member C++ rename map (Python name -> C++ enumerator name). Used
    # for @native enums where the C++ side has a name TPy can't spell
    # (Python keywords like `None`) or uses a different convention.
    # Members without an entry use the Python name as the C++ name.
    cpp_member_names: dict[str, str] = field(default_factory=dict)
    # True when the user spelled at least one explicit integer literal as a
    # member value. For @native enums this triggers per-member static_assert
    # emission so the TPy-declared value is verified against the C++ side at
    # compile time. False when all members used auto() / native_member()
    # (the user opted out of declaring values; C++ is the truth).
    has_explicit_values: bool = False
    # True for a bare `@export` enum in an ext_module: recreated as a CPython
    # IntEnum/Enum at PyInit_. Mirrors TpyFunction/TpyRecord.exposed_to_host.
    exposed_to_host: bool = False
    # The enum body's leading string literal, captured here for the same
    # reason TpyRecord's is: there is no statement body to read it back
    # from. Consumed only by the CPython-extension glue.
    docstring: str | None = None
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
    # Third-party library declarations: (registry_name, platform_filter).
    # Resolved at build time via tpyc.build.third_party into include paths,
    # link flags, and CMake snippets based on the user's selected mode.
    third_party_deps: list[tuple[str, str | None]] = field(default_factory=list)
    native_module: bool = False
    # CPython extension module: @export exposes to the host interpreter and
    # the build emits a PyInit_-exporting .so instead of an executable.
    ext_module: bool = False
    # Location of the `ext_module` directive, for diagnostics that point at it
    # (e.g. the ext_module/native_module conflict).
    ext_module_loc: 'SourceLocation | None' = None
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
    # The module's leading string literal. Captured here rather than read
    # back from `top_level_stmts[0]`: later passes prepend synthesized decls,
    # so the docstring's position is not stable. Consumed only by the
    # CPython-extension glue (`m_doc`).
    docstring: str | None = None
    source_lines: list[str] = field(default_factory=list)  # Original source lines for source mapping
    # Set by the parser: a generator expression, outside any generic function
    # or record, in a body that may be emitted in a header (a method, a
    # generator, an async def). Its frame is then a header generator whose
    # `__next__` goes to `<mod>_inl.hpp`, a verdict asked before sema builds
    # the function (a cycle peer can ask first).
    has_header_genexpr: bool = False
    # Import tracking: module_name -> set of (original_name, local_name) tuples (for "from X import Y as Z")
    #                  module_name -> None (for "import X")
    imports: dict[str, set[tuple[str, str]] | None] = field(default_factory=dict)
    # True when "from tpy import *" was used (triggers full tpy registration in sema)
    tpy_star_import: bool = False
    # Set of module names that had 'from X import *'
    star_imports: set[str] = field(default_factory=set)
    # Module's own `__all__` literal, captured at parse time. `None` when
    # the module does not define `__all__`; consumers treat that as "no
    # filter applied beyond the leading-underscore rule".
    module_all: frozenset[str] | None = None
    # Source location of the `__all__` literal (the assignment's RHS), used
    # for diagnostics that point at the `__all__` declaration -- e.g. the
    # phantom-name warning when `__all__` lists a name the module does not
    # actually export. None whenever `module_all` is None.
    module_all_loc: SourceLocation | None = None
    # Modules to resolve as files: {module_name: line_number}
    user_module_imports: dict[str, int] = field(default_factory=dict)
    # Module aliases from "from . import submod" -> {canonical_name: local_name}
    module_aliases: dict[str, str] = field(default_factory=dict)
    # Modules that had bare `import X` statements (needed for module binding in sema)
    bare_module_imports: set[str] = field(default_factory=set)
    # Type aliases (e.g., Shape = Circle | Rect) -> (resolved type, source
    # location, type_params, type_param_kinds).  `type_params` /
    # `type_param_kinds` are non-empty only for generic aliases
    # (`type Tree[T] = ...`); v1 of generic aliases keeps storage
    # extensible without consuming type_params yet -- see
    # `docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md`.
    # Between parse and the post-parse resolve_refs pass, alias
    # RHS values may hold TypeRefNode in place of TpyType.  Sema resolves
    # each alias passing `pending_alias=alias_name` through the resolver
    # API so same-body self-refs become NominalType(name) placeholders,
    # then detects recursive unions post-resolution.
    type_aliases: 'dict[str, tuple[TpyType | TypeRefNode, SourceLocation | None, list[str], list[TypeParamKind]]]' = field(default_factory=dict)
    # Parser warnings (e.g., imports after non-import code)
    parse_warnings: list[ParseWarning] = field(default_factory=list)
    # Module-level # tpy: directives
    directives: ModuleDirectives = field(default_factory=ModuleDirectives)
    # Union type aliases that need wrapper-struct representation (self- or mutually-recursive)
    recursive_union_names: set[str] = field(default_factory=set)
    # Parser resolver: a TypeResolver instance attached at end-of-parse
    # that resolves a TypeRefNode (emitted by the walker at annotation
    # sites) to a TpyType.  Sema invokes `resolver.resolve(ref, scope)`
    # via `TypeOperations.resolve_type_ref` when a writer encounters an
    # unresolved ref.  Holds a back-reference to the parser so it carries
    # live resolution state (registry, imports, local_defs,
    # module_class_names, type_alias_names, reverse_module_aliases,
    # bare_module_imports).
    resolver: 'Any | None' = None
    # Opaque, module-scoped payload a frontend plugin hands to its own
    # `@function_macro`s (set by lowering from `FrontendModule.macro_data`;
    # always None for parser-produced modules). The function-macro phase
    # exposes it as `ctx.module_data`.
    macro_data: 'Any' = None

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


def is_docstring(stmt: TpyStmt) -> bool:
    """True for a bare string-literal expression statement -- i.e. the
    syntactic form Python recognises as a docstring at the top of a
    function/method/class/module body.
    """
    return isinstance(stmt, TpyExprStmt) and isinstance(stmt.expr, TpyStrLiteral)


def is_super_del_call(stmt: TpyStmt) -> bool:
    """Check if a statement is a super().__del__() call."""
    if isinstance(stmt, TpyExprStmt):
        expr = stmt.expr
        if isinstance(expr, TpyMethodCall) and expr.method == "__del__":
            return expr.super_parent_type is not None
    return False


def is_base_init_call(stmt: TpyStmt) -> bool:
    """True for super().__init__(...) or BaseN.__init__(self, ...). Both forms
    belong in the init section and hoist into the C++ member initializer list.
    """
    if isinstance(stmt, TpyExprStmt):
        expr = stmt.expr
        if isinstance(expr, TpyMethodCall) and expr.method == "__init__":
            return (expr.super_parent_type is not None
                    or expr.unbound_self_parent_type is not None)
    return False


def collect_name_refs(expr: TpyExpr, *, into_lambdas: bool = False) -> set[str]:
    """Collect all name references in an expression tree.

    Uses TpyExpr.children() for generic traversal. TpyCall.func is a
    TpyName child, so callable variable names are captured automatically.

    `into_lambdas` also walks a lambda's body, which `children()` withholds
    because the lambda is its own scope -- for a caller asking which names
    the expression can READ rather than which ones it binds here. The
    lambda's own parameters are reported too, so such a caller has to be one
    that can afford the conservative answer.
    """
    names: set[str] = set()
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, TpyName):
            names.add(node.name)
        else:
            stack.extend(node.children())
            if into_lambdas and isinstance(node, TpyLambda):
                stack.append(node.body)
    return names


def expr_reads_self_field(expr: TpyExpr, fields: set[str]) -> bool:
    """True if `expr` reads `self.X` for any X in `fields`, or makes any
    `self.method(...)` call while `fields` is non-empty (methods can read
    arbitrary fields, so we treat them conservatively).

    Intended for ordering checks between an own-field MIL hoist and prior
    body writes to `self.*`. Not a general-purpose aliasing oracle -- the
    method-call rule is over-conservative for that one use case.

    See also `expr_contains_self_method_call` -- the method-only variant
    (no field-name set, filters out static-method calls). Pick that one
    when you only care whether any instance method on self was invoked.
    """
    if not fields:
        return False
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, TpyFieldAccess):
            if isinstance(node.obj, TpyName) and node.obj.name == "self":
                if node.field in fields:
                    return True
        elif isinstance(node, TpyMethodCall):
            if isinstance(node.obj, TpyName) and node.obj.name == "self":
                return True
        stack.extend(node.children())
    return False


def expr_contains_self_method_call(expr: TpyExpr) -> bool:
    """True if `expr` contains any non-static `self.method(...)` call.

    Used by __init__ analysis to flag method calls in field-init RHS that
    might observe still-uninitialized fields.

    See also `expr_reads_self_field` -- combines field-read detection
    (for a caller-supplied set of field names) with method-call
    detection. Pick that one when you also need to know if specific
    self.X reads occur, not just method calls.
    """
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, TpyMethodCall):
            if (isinstance(node.obj, TpyName)
                    and node.obj.name == "self"
                    and not node.is_static_call):
                return True
        stack.extend(node.children())
    return False


def collect_top_level_local_names(stmts: list[TpyStmt]) -> set[str]:
    """Collect names bound by top-level statements in a function body.

    Covers the forms that introduce locals at this scope: `x: T = ...`
    (TpyVarDecl), `x = ...` (TpyAssign with a bare-name target), and
    `a, b = ...` (TpyTupleUnpack). Does NOT descend into control flow:
    names bound inside `if`, `for`, `while`, etc. are not included --
    callers reasoning about the top-level MIL/init split only need
    names visible at statement depth 0.
    """
    names: set[str] = set()
    for s in stmts:
        if isinstance(s, TpyVarDecl):
            names.add(s.name)
        elif isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
            names.add(s.target.name)
        elif isinstance(s, TpyTupleUnpack):
            names.update(t for t in s.targets if t is not None)
    return names


def is_stable_address_lvalue(expr: TpyExpr) -> bool:
    """True iff `expr` resolves to a stable lvalue: a bare name (local /
    parameter / frame field) or a chain of field accesses rooted at a
    name. Subscripts, calls, binops, conditional / comprehension
    expressions return temporaries whose address would dangle across
    an async suspension or a stored borrow.

    Stricter than `sema/compatibility.py::is_lvalue`: subscripts
    (addressable in C++ but yield container-element temporaries
    across suspensions) and `TpyCoerce` (sema-only wrapper, doesn't
    reach codegen) are both excluded.

    Used by:
      * `sema/expressions.py::analyze_await` to reject
        `await rvalue.method()` (M4 async-method receiver capture).
      * `codegen_cpp/gen_async.py::_make_await_payload` to choose
        BORROWED vs ERASED await mode.
    """
    e = expr
    while isinstance(e, TpyFieldAccess):
        e = e.obj
    return isinstance(e, TpyName)


def stmt_has_any_suspension(stmt: TpyStmt) -> bool:
    """Walk a statement (its expression slots and sub_bodies) for any
    suspension point -- a `TpyAwait` (async) or a `TpyYield` (generator).
    `async with` and `async for` always count even when their bodies have
    none: the `__aenter__` / `__aexit__` / `__anext__` calls are themselves
    suspensions. Does not descend into nested function/lambda bodies (a
    suspension there belongs to the inner callable, not this one)."""
    if isinstance(stmt, TpyYield):
        return True
    if isinstance(stmt, TpyWith) and stmt.is_async:
        return True
    if isinstance(stmt, TpyForEach) and stmt.is_async:
        return True

    def walk_expr(e: TpyExpr | None) -> bool:
        if e is None:
            return False
        if isinstance(e, TpyAwait):
            return True
        for c in (e.children() if hasattr(e, "children") else ()):
            if walk_expr(c):
                return True
        return False

    if hasattr(stmt, "exprs"):
        for e in stmt.exprs():
            if walk_expr(e):
                return True
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                if stmt_has_any_suspension(s):
                    return True
    return False


def stmts_have_any_suspension(stmts: list[TpyStmt]) -> bool:
    return any(stmt_has_any_suspension(s) for s in stmts)


def stmts_have_any_return(stmts: list[TpyStmt]) -> bool:
    """True if any of `stmts` (recursively through sub_bodies) contains a
    TpyReturn. Does not descend into nested function/lambda bodies."""
    for s in stmts:
        if isinstance(s, TpyReturn):
            return True
        if hasattr(s, "sub_bodies"):
            for b in s.sub_bodies():
                if stmts_have_any_return(b):
                    return True
    return False


def read_names(e: TpyExpr) -> set[str]:
    """Every name an expression reads, nested sub-expressions included."""
    out: set[str] = set()
    stack = [e]
    while stack:
        n = stack.pop()
        if isinstance(n, TpyName):
            out.add(n.name)
        stack.extend(n.children())
    return out


def written_names(stmt: TpyStmt, *, match_binds: bool = True) -> set[str]:
    """Roots written AT NAME LEVEL by `stmt` itself (not its sub-bodies) --
    a rebind, bare-name value write, re-decl, unpack target, `del name`, a
    compound statement's OWN binding targets (a for-loop's loop var, a
    `with ... as` name, an `except ... as` name, a `match` arm's capture
    patterns), or a walrus target anywhere in the statement's expressions.
    Field/subscript writes THROUGH a root (`bb.val = 9`, `del bb[k]`) are
    excluded: an aliasing binding (a match capture's `auto&`) lowers a
    through-write identically -- only a write to the binding NAME itself has
    no aliasing render. Comprehension loop vars are their own scope in
    Python 3 and are correctly NOT surfaced (they are not statement-level
    for-targets and not walrus targets).

    `match_binds=False` drops the match-capture names, leaving only the binds
    that WRITE THROUGH an enclosing alias. A match arm binds its captures by
    re-seating (pointer form) or assigning (value form) the binding itself, so
    it rebinds the name without writing through an outer alias of it; every
    other form here writes through. Callers asking "is this name rebound?" for
    scoping want the default; callers asking "would this corrupt the object an
    outer alias points at?" want False."""
    out: set[str] = set()
    if isinstance(stmt, (TpyAssign, TpyAugAssign)):
        if isinstance(stmt.target, TpyName):
            out.add(stmt.target.name)
    elif isinstance(stmt, TpyVarDecl):
        out.add(stmt.name)
    elif isinstance(stmt, TpyTupleUnpack):
        out.update(name for name in stmt.targets if name is not None)
    elif isinstance(stmt, TpyDelVar):
        out.update(stmt.names)
    elif isinstance(stmt, TpyForEach):
        out.add(stmt.var)
    elif isinstance(stmt, TpyWith):
        out.update(item.target for item in stmt.items
                   if item.target is not None)
    elif isinstance(stmt, TpyTry):
        # `except E as v:` binds v -- sub_bodies() covers the handler
        # BODIES only, not the binding names.
        out.update(h.binding for h in stmt.handlers
                   if h.binding is not None)
    elif isinstance(stmt, TpyNestedDef):
        out.add(stmt.func.name)
    elif isinstance(stmt, TpyMatch) and match_binds:
        # A `match` is not its own scope in Python: an arm's captures bind the
        # ENCLOSING function's locals, so a nested match reusing an outer
        # capture name rebinds it rather than shadowing it.
        for case in stmt.cases:
            out.update(n.name for n in iter_capture_bindings(case.pattern))

    out.update(ne.target for ne in walrus_bindings(stmt))
    return out


def walrus_bindings(stmt: TpyStmt) -> list['TpyNamedExpr']:
    """Every walrus node in `stmt`'s OWN expressions, outermost first.

    Sub-bodies are not descended into -- callers already walk those -- so this
    composes with a statement walk without visiting a nested body twice.
    """
    found: list[TpyNamedExpr] = []

    def collect(e: TpyExpr | None) -> None:
        if not isinstance(e, TpyExpr):
            return
        if isinstance(e, TpyNamedExpr):
            found.append(e)
        for c in e.children():
            collect(c)

    for e in stmt.exprs():
        collect(e)
    return found


def borrow_chain_root(expr: TpyExpr) -> str | None:
    """The name a borrow expression roots at -- peel field / element / coerce
    wrappers until a name is left. None when it bottoms out at something that
    is not a name (a literal, a constructor, a free call), i.e. provenance the
    caller cannot read off the expression alone.

    A method call peels to its RECEIVER, which over-approximates: what the
    callee hands back need not come from the receiver's storage. Callers must
    treat the result as "could root here", never as a provenance trace.
    """
    cur: TpyExpr | None = expr
    while cur is not None and not isinstance(cur, TpyName):
        cur = getattr(cur, "obj", None) or getattr(cur, "expr", None)
    return cur.name if isinstance(cur, TpyName) else None


def returns_borrow_rooted_at_self(func: 'TpyFunction') -> bool:
    """Whether a method's returned borrow can root at `self`.

    Answers "is what this hands back part of the receiver, or something that
    merely passes through it" -- a `with` manager's `__enter__` returning `self`
    or `self.field` lends out its own storage, while one returning a global
    lends someone else's. Only the second is free to outlive the receiver.

    A return root is followed through the body's local bindings, so
    `tmp = self.item; return tmp` reads the same as `return self.item`.

    False needs PROOF, on every return: a root that is external storage (a
    global -- never a local of this body) or a local every one of whose
    bindings is itself external. Everything else keeps the answer True --
    no returns at all, a root that is not a name, a name bound by a form this
    does not model (`written_names` is the chokepoint, so a new binding form
    lands here as unknown rather than as "external"), or a nested def, which
    can rebind an enclosing local out of this walk's sight.
    """
    # name -> the roots its bindings come from; None = unreadable provenance.
    binding_roots: dict[str, list[str | None]] = {}
    return_roots: list[str | None] = []
    saw_return = False
    saw_nested_def = False

    def bind(name: str, source: TpyExpr | None) -> None:
        binding_roots.setdefault(name, []).append(
            borrow_chain_root(source) if source is not None else None)

    def on_stmt(stmt: TpyStmt) -> None:
        nonlocal saw_return, saw_nested_def
        modeled: set[str] = set()
        if isinstance(stmt, TpyVarDecl):
            bind(stmt.name, stmt.init)
            modeled.add(stmt.name)
        elif isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName):
            bind(stmt.target.name, stmt.value)
            modeled.add(stmt.target.name)
        elif isinstance(stmt, TpyForEach):
            bind(stmt.var, stmt.iterable)
            modeled.add(stmt.var)
        elif isinstance(stmt, TpyWith):
            for item in stmt.items:
                if item.target is not None:
                    bind(item.target, item.context_expr)
                    modeled.add(item.target)
        elif isinstance(stmt, TpyNestedDef):
            saw_nested_def = True
        elif isinstance(stmt, TpyReturn):
            saw_return = True
            return_roots.append(
                borrow_chain_root(stmt.value) if stmt.value is not None
                else None)
        for name in written_names(stmt) - modeled:
            bind(name, None)

    walk_body_stmts(func.body, lambda e: None, on_stmt)
    if not saw_return or saw_nested_def:
        return True

    def is_external(root: str | None, external: set[str]) -> bool:
        return (root is not None and root != "self"
                and (root not in binding_roots or root in external))

    external: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, sources in binding_roots.items():
            if name in external:
                continue
            if all(is_external(src, external) for src in sources):
                external.add(name)
                changed = True

    return not all(is_external(root, external) for root in return_roots)


def body_writes_name(body: list[TpyStmt], var: str,
                     *, match_binds: bool = True) -> bool:
    """Whether any statement in `body` (compound bodies included via
    sub_bodies) writes `var` at name level. See written_names for exactly
    what counts as a name-level write and what `match_binds` selects."""
    for s in body:
        if var in written_names(s, match_binds=match_binds):
            return True
        for inner in s.sub_bodies():
            if body_writes_name(inner, var, match_binds=match_binds):
                return True
    return False


def walk_body_stmts(
    stmts: list[TpyStmt],
    on_expr: Callable[[TpyExpr], None],
    on_stmt: Callable[[TpyStmt], None],
) -> None:
    """Walk statements calling on_expr/on_stmt. Does NOT recurse into TpyNestedDef."""
    for stmt in stmts:
        on_stmt(stmt)
        if isinstance(stmt, TpyNestedDef):
            continue  # separate scope
        for expr in stmt.exprs():
            on_expr(expr)
        for body in stmt.sub_bodies():
            walk_body_stmts(body, on_expr, on_stmt)


# Non-node records a caller can actually meet, and so has to be told apart
# from a node: `SourceLocation` hangs off every node and carries no position
# in the tree of its own.
_NON_NODE_RECORDS = frozenset(('SourceLocation',))


def is_parse_node(obj: object) -> bool:
    """True for a dataclass INSTANCE declared in this module, other than the
    records listed above -- which is every parse-tree node.

    It is not an exact characterization of "node": the other dataclasses
    declared here (`ParseWarning`, `ModuleDirectives`, `RelativeImportKey`)
    answer True as well. Nothing is wrong with that today -- no tree walk and
    no per-function tracking state holds one -- but a caller that could meet
    one needs its own test. `ParseError` and the enums are not dataclasses,
    so they answer False.
    """
    return (is_dataclass(obj) and not isinstance(obj, type)
            and type(obj).__module__ == __name__
            and type(obj).__name__ not in _NON_NODE_RECORDS)
