"""
TurboPython AST node definitions.

Pure data definitions: dataclasses, ParseError, SourceLocation.
No parsing logic lives here.
"""

from __future__ import annotations
import ast
import copy
from dataclasses import dataclass, field, is_dataclass, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Iterator, Literal, Optional, TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, FieldInfo, FunctionInfo,
    MethodSignature, TypeParamKind, LiteralValue, FunctionLinkage,
    param_takes_ownership,
)

# Source location for error reporting and source mapping

@dataclass
class SourceLocation:
    """Source code location for error reporting and source mapping."""
    line: int  # 1-indexed line number
    column: int = 0  # 0-indexed column
    file: str | None = None  # Source file path (optional)
    end_line: int | None = None  # 1-indexed last line of the node, when known
    end_column: int | None = None  # 0-indexed column just past the node's end


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
    # Set by sema (`iter_loans.record_iteration_pull`): a step of iterating
    # this source may run TPy code, a call no node kind shows.
    pull_runs_code: bool = field(default=False, kw_only=True)

    def children(self) -> list['TpyExpr']:
        """Return child expression nodes for generic tree walking."""
        return []


if TYPE_CHECKING:
    from ..coercions import Coercion
    from ..sema.arg_binding import ArgBinding
    from ..holders import Holder
    from ..sema.context import LoanInfo
    from ..sema.storage_walk import StorageKey
    from ..symbol_binding import SymbolBinding
    from ..namespace import NameBinding
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
class DefaultReferent:
    """What a name in a parameter default names in the scope the default
    is written in, as CPython binds it once at the `def`: a module variable
    (`module_var`: its defining module and name), a class constant of the
    class whose body holds the `def` (`class_owner`), or, for a
    constructor call's callee, the binding of the class it constructs
    (`ctor`), which the call is dispatched on wherever it is filled."""
    module_var: tuple[str, str] | None = None
    class_owner: 'RecordInfo | None' = None
    ctor: 'NameBinding | None' = None


# The parser judges a default by its shape; sema refuses with the same words
# a callee that shape admits but that names no class where it is written.
NON_CONSTANT_DEFAULT_MSG = (
    "Default parameter value must be a constant expression "
    "(literal, None, fixed-int constructor like int32(5), "
    "an enum member like Color.RED, or a Final[T] module constant)")


@dataclass
class TpyName(TpyExpr):
    """Variable reference."""
    name: str
    is_function_ref: bool = False  # Set by sema: name resolves to a function used as a value
    function_ref_info: 'FunctionInfo | None' = None  # Set by sema: resolved function for codegen
    function_ref_type_args: 'tuple[TpyType, ...] | None' = None  # Set by sema: inferred type args for generic function refs
    # Set by sema: a class name at a Callable/Fn slot stands for this
    # synthesized `lambda *a: C(*a)`; read it through `lambda_of`.
    factory_expansion: 'TpyLambda | None' = None
    # Set by sema on a name in a parameter default: what it names where the
    # default is written. Stamped on the default's own node, which every
    # holder of the expression shares (ParamInfo, init_params, protocol
    # signatures, TpyFunction.defaults).
    default_referent: DefaultReferent | None = None


_OP_SPELLINGS = {"div": "/", "!": "not", "&&": "and", "||": "or"}


def op_spelling(op: str) -> str:
    """How a diagnostic spells an operator: the parser keys true division as
    `div` to keep it apart from `//`, and `not` / `and` / `or` by their C++
    spellings."""
    return _OP_SPELLINGS.get(op, op)


class NoneUseKind(Enum):
    """The operation a runtime None check guards, which picks CPython's
    exception and message for a None value."""
    ATTR = "attr"          # AttributeError: attribute read / write / method lookup
    SUBSCRIPT = "item"     # TypeError: not subscriptable
    OPERAND = "operand"    # TypeError: an operator's operand (binary or unary)
    CALL = "call"          # TypeError: not callable


@dataclass(frozen=True)
class NoneUse:
    """A None check sema could not prove away, decided where sema warns
    *Potential None access*: the operation and the short parts of its
    message -- the attribute name, or the operator's source spelling and
    the other operand's Python type name (`other` None for a unary operator,
    `none_left` the side the checked operand is on). The runtime composes
    the message from these.

    `through_ptr` marks a `Ptr` auto-deref check: a pointer is not an
    Optional, so the Optional-access gates (`needs_optional_runtime_check`)
    do not see it, but its check renders the same."""
    kind: NoneUseKind
    attr: str | None = None
    op: str | None = None
    other: str | None = None
    none_left: bool = True
    through_ptr: bool = False


class NoneChecked:
    """A node whose access sema may guard with a None check (`none_use`)."""
    none_use: NoneUse | None

    @property
    def needs_optional_runtime_check(self) -> bool:
        """The access is an unproven Optional one (a `Ptr` deref aside)."""
        return self.none_use is not None and not self.none_use.through_ptr


@dataclass
class TpyBinOp(TpyExpr):
    """Binary operation."""
    left: TpyExpr
    op: str  # '+', '-', '*', '/', '%', '==', '!=', '<', '>', '<=', '>='
    right: TpyExpr
    resolved_binop: 'ResolvedBinop | None' = None  # Set by sema for builtin ops
    resolved_contains: 'FunctionInfo | None' = None  # Set by sema for 'in'/'not in' with __contains__
    # Set by sema with `resolved_contains`: the call `right.__contains__(left)`
    # the test is, carrying the winner substituted at the receiver as an
    # explicit call does (a runtime-bound winner's needle unconverted). Not a
    # child: its operands are this node's.
    contains_call: 'TpyMethodCall | None' = None
    typed_dict_in_field: str | None = None  # Set by sema: "key" in TypedDict -> field presence check
    typed_dict_in_always_true: bool = False  # Set by sema: total=True field, fold to True
    optional_safe_eq: bool = False  # Set by sema: ==/!= with Optional value-type operand(s)
    # Set by sema: `is` / `is not` between two objects, which compares their
    # addresses: each operand's object type (left, right).
    identity_pointees: 'tuple[TpyType, TpyType] | None' = None
    int_enum_coercion: 'NominalType | None' = None  # Set by sema: IntEnum arithmetic coerced to underlying type
    divisor_non_zero: bool = False  # Set by sema: divisor provably non-zero, skip div-zero check
    # Set by sema on a chained comparison's synthetic pairs past the first:
    # the right operand runs only if the preceding compare passed, while the
    # left is the previous pair's right and has already evaluated.
    cond_right: bool = False
    # Set by sema on every analysis: the None check each operand takes, an
    # unproven value Optional consumed as its inner value.
    none_left: NoneUse | None = None
    none_right: NoneUse | None = None

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
    # Set by sema on every analysis: the operand's None check (see TpyBinOp).
    none_operand: NoneUse | None = None

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


class ResultForm(Enum):
    """What one call to a borrow-declared binding hands back
    (`TpyCallLike.result_form`)."""
    # Not a borrow-declared call: the callee's own convention answers.
    NOT_DECLARED = "not_declared"
    # A borrow of storage that outlives the call: holdable by reference.
    BORROW = "borrow"
    # A reference into the operands valid for the statement only (one of
    # them is a temporary, or the step of the caller's iterator): written in
    # place, held only as a copy.
    REFERENCE_VALUE = "reference_value"
    # The C++ hands back a copy (the callee walked a source that is not a
    # container with an iterator of its own, gone at the return).
    COPY = "copy"
    # A value-shaped result (a number, a `str`, an Optional / union / tuple
    # of values) handed back by value: nothing is lent, and nothing CPython
    # would share is copied.
    VALUE = "value"

    @property
    def is_declared(self) -> bool:
        """Sema stamped the form from the callee's lend contract
        (`CallAnalyzer.stamp_result_borrow`), which then says what the
        result lends rather than the declared return alone."""
        return self is not ResultForm.NOT_DECLARED

    @property
    def is_fresh(self) -> bool:
        """The result is a fresh value to every holder."""
        return self in (ResultForm.REFERENCE_VALUE, ResultForm.COPY,
                        ResultForm.VALUE)

    @property
    def by_value(self) -> bool:
        """The C++ result is a value the caller owns, not a reference."""
        return self in (ResultForm.COPY, ResultForm.VALUE)

    @property
    def from_operand(self) -> bool:
        """The result is a lent operand, or a copy of one: a declared
        verdict that relates the result to the call's operands."""
        return self in (ResultForm.BORROW, ResultForm.REFERENCE_VALUE,
                        ResultForm.COPY)

    @property
    def copies_operand(self) -> bool:
        """A fresh result made from a lent operand, which a holder copies."""
        return self in (ResultForm.REFERENCE_VALUE, ResultForm.COPY)


def _relaid_deepcopy(self, memo):
    """A deep copy of a call is a new analysis site: it
    is written with what the call was written with, never with a layout an
    analysis committed (`relaid_copy`)."""
    # Lazy: sema imports this module; the copy reads the call's binding.
    from ..sema.arg_binding import relaid_copy
    return relaid_copy(self, lambda e: copy.deepcopy(e, memo),
                       lambda v: copy.deepcopy(v, memo), memo)


@dataclass
class TpyCallLike(TpyExpr):
    """A call whose result sema may stamp a borrow-or-value verdict: a free
    call (`TpyCall`) or a method call (`TpyMethodCall`). The verdict and the
    argument binding live here; every other call fact stays on its own node
    kind."""
    # The binding the last analysis committed (`ArgBinding`, set by sema):
    # once committed, `args` is laid out in parameter order and the call as
    # written is read back through it (`source_view`).
    binding: 'ArgBinding | None' = field(default=None, kw_only=True,
                                         compare=False, repr=False)
    # Set by sema on a call to a bodyless binding that declares which
    # arguments its result borrows (`borrows=` / `element_of=`); NOT_DECLARED for every
    # other call. Recomputed on each analysis of the node, never re-derived
    # downstream.
    result_form: ResultForm = field(default=ResultForm.NOT_DECLARED,
                                    kw_only=True)
    # With a fresh form (REFERENCE_VALUE / COPY): some lent argument is still
    # reached by the program (a named object, a handle or view into such
    # storage), so a sink that HOLDS the fresh value copies an object CPython
    # would alias -- the owning sinks and the local binding warn. False when
    # every lent argument is a fresh owner, where nothing can tell the copy
    # apart.
    copy_observable: bool = field(default=False, kw_only=True)

    __deepcopy__ = _relaid_deepcopy

    @property
    def call_display(self) -> str:
        """How a diagnostic names the call: `'max(...)'`, `'get(...)'`."""
        raise NotImplementedError


@dataclass
class TpyCall(TpyCallLike):
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
    resolved_import: tuple[str, str] | None = None  # Set by parser: (module, name) for resolved imports
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved function overloads
    enum_from_value: NominalType | None = None  # Set by sema for enum value lookup: Color(0)
    isinstance_var: str | None = None        # Set by sema: variable name being isinstance-checked
    isinstance_type: TpyType | None = None   # Set by sema: resolved type being checked for
    isinstance_is_protocol: bool = False     # Set by sema: protocol isinstance (if constexpr)
    isinstance_type_param: bool = False      # Set by sema: subject is a non-poly class-bounded type param -> compile-time tpy::isinstance_static
    cast_target_type: TpyType | None = None  # Set by sema for typing.cast(T, x): the resolved target type
    cast_source_is_any: bool = False         # Set by sema for typing.cast: True iff source's static type is Any
    compile_time_assert: bool = False        # Set by sema for assert_send[T]()/assert_sync[T](): checked in sema, elided in codegen
    # Set by sema: the expression this call IS, lowered in its place -- a
    # @call_macro's replacement or a getattr/hasattr synth.
    macro_expansion: 'TpyExpr | None' = None
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
    def call_display(self) -> str:
        return f"'{self.func.name if isinstance(self.func, TpyName) else 'call'}(...)'"

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
        if self.double_star_unpack is not None:
            extra.append(self.double_star_unpack)
        return [self.func] + list(self.args) + list(self.kwargs.values()) + extra


@dataclass
class TpyMethodCall(TpyCallLike, NoneChecked):
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
    # Set by sema on a generic method call with explicit type arguments: per
    # written argument, the method's own type parameter it binds, None for a
    # class type parameter or a `_` wildcard.
    written_type_params: tuple[str | None, ...] | None = None
    # Set by sema alongside is_static_call: the resolved owning record. Codegen
    # needs it when the receiver's spelling names no record itself (`cls`).
    static_call_owner: Optional['RecordInfo'] = None
    super_parent_type: Optional[TpyType] = None  # Set by sema for super().method() calls
    unbound_self_parent_type: Optional[TpyType] = None  # Set by sema for BaseN.method(self, ...) calls on an ancestor
    # Set by sema on a base-part call: the `self` it runs on. For
    # `BaseN.method(self, ...)` the written `self`, its first written
    # positional, out of `args` (`ArgBinding.receiver`); for
    # `super().method(...)` a `self` read standing for the proxy.
    explicit_receiver: 'TpyExpr | None' = field(default=None, compare=False,
                                                repr=False)
    # Set by sema on a parent-initializer call (`super().__init__(...)` /
    # `Base.__init__(self, ...)`): the direct base, as the child spells it in
    # its bases, whose C++ constructor runs the named `__init__` -- None when
    # no direct base does. The named class may sit past an `__init__`-less
    # direct base, which inherits the constructor.
    base_init_direct_base: Optional[TpyType] = None
    user_module_call: Optional[str] = None  # Set by sema for module.func() calls to user modules
    builtin_module_call: Optional[str] = None  # Set by sema for builtin module.func() calls (canonical module name)
    # Set by sema on every analysis: the receiver's None check, or a callable
    # field's callee check (`is_callable_field`).
    none_use: NoneUse | None = None
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved method overloads
    # Set by sema: the receiver passes const so C++ runs the readonly variant
    # sema ranked (`pins_const_receiver`).
    const_receiver: bool = False
    inferred_type_args: tuple[TpyType, ...] | None = None  # Set by sema for generic builtin module calls
    deref_depth: int = 0  # Set by sema: number of __deref__ steps applied before method resolution
    ptr_non_null: bool = False  # Set by sema: receiver is a provably non-null Ptr (or Ptr[readonly[T]])
    is_callable_field: bool = False  # Set by sema: method name is a Callable-typed field
    # Set by sema: the expression this call IS, lowered in its place -- a
    # @call_macro's replacement, or the constructor / `from_value` call of
    # the class or enum a dotted callee names (`m.Outer.Inner(5)`).
    macro_expansion: 'TpyExpr | None' = None
    typed_dict_get_field: str | None = None  # Set by sema: td.get("key") -> field access
    typed_dict_get_optional: bool = False  # Set by sema: total=False field, absent by default
    # See TpyCall.representational_subst_params for the contract.
    representational_subst_params: frozenset[str] | None = None

    @property
    def call_display(self) -> str:
        return f"'{self.method}(...)'"

    def children(self) -> list[TpyExpr]:
        if self.macro_expansion is not None:
            return [self.macro_expansion]
        callee = self.subscript_callee if self.subscript_callee is not None else self.obj
        dstar = [self.double_star_unpack] if self.double_star_unpack is not None else []
        return [callee] + list(self.args) + list(self.kwargs.values()) + dstar


@dataclass
class TpyFieldAccess(TpyExpr, NoneChecked):
    """Field access on a value or pointer."""
    obj: TpyExpr
    field: str
    none_use: NoneUse | None = None  # Set by sema on every analysis: the receiver's None check
    deref_depth: int = 0  # Set by sema: number of __deref__ steps applied before field lookup
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
    native_field_name: Optional[str] = None  # Set by sema: the matched field's native_field() C++ rename (own-fields-first lookup, so a subclass redeclaration shadows an ancestor's rename)
    enum_member_of: Optional[TpyType] = None  # Set by sema: type-level enum member access (Color.RED); the enum NominalType
    # Set by sema: the class or enum this dotted path names (`m.Color` in
    # `m.Color.RED`), when a member is read off it; it is no storage.
    names_type: 'SymbolBinding | None' = None
    # Set by sema: a dotted class (`m.Outer.Inner`) at a Callable/Fn slot
    # stands for this synthesized `lambda *a: C(*a)`; read it through
    # `lambda_of`.
    factory_expansion: 'TpyLambda | None' = None
    # Set by sema: a consuming method's `self.<field>` read that MAY move the
    # field out (it sits in a return nothing of the method runs after); a
    # position whose lowering has no move row still rejects. Every other
    # such read borrows.
    consuming_move: bool = False
    # Set by sema on a parameter default filled at a call whose own scope
    # cannot name the default's referent: the read is spelled entirely by
    # its stamp (`module_var_access` / `class_constant_owner` /
    # `enum_member_of`), and `obj` is a placeholder no scope binds, so the
    # node is a leaf.
    default_fill: bool = False

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
        return [] if self.default_fill else [self.obj]


def name_path(expr: TpyExpr) -> list[str] | None:
    """The names of a dotted name chain (`m.Outer.Inner` ->
    `["m", "Outer", "Inner"]`), or None for any other expression."""
    parts: list[str] = []
    while isinstance(expr, TpyFieldAccess):
        parts.append(expr.field)
        expr = expr.obj
    if not isinstance(expr, TpyName):
        return None
    parts.append(expr.name)
    parts.reverse()
    return parts


def const_tuple_index(index: TpyExpr) -> int | None:
    """The compile-time index of a tuple subscript: an int literal or a
    negated one (still negative here), else None."""
    if isinstance(index, TpyIntLiteral):
        return index.value
    if (isinstance(index, TpyUnaryOp) and index.op == "-"
            and isinstance(index.operand, TpyIntLiteral)):
        return -index.operand.value
    return None


def lambda_of(expr: TpyExpr) -> TpyExpr:
    """`expr` as the callable value it stands for: the lambda sema
    synthesized for a class name used at a Callable/Fn slot, else `expr`."""
    if (isinstance(expr, (TpyName, TpyFieldAccess))
            and expr.factory_expansion is not None):
        return expr.factory_expansion
    return expr


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

    The receiver-shape markers transfer: the getter is reached through the
    same hops as the field lookup was (a `Ptr` receiver's deref, an
    unproven Optional), so the call carries exactly the
    markers `obj.getter()` would, and the method-call arms spell them.
    """
    call = TpyMethodCall(obj=node.obj, method=method, args=args, loc=node.loc)
    call.resolved_function_info = fi
    call.deref_depth = node.deref_depth
    call.ptr_non_null = node.ptr_non_null
    call.none_use = node.none_use
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
    # Set by sema: the variable takes its own copy of the element -- the
    # clause may write its source while it is live
    # (`loop_frames.stamp_copy_verdicts`, read by the binding planner).
    copies_var: bool = False
    # Set by sema before the clause's body: the leaf paths of each step the
    # iteration hands over (`step_handover_paths`), as `TpyForEach` stamps
    # them; a sink moves a bare last-use target that owns its element.
    step_handover: frozenset[tuple[int, ...]] = frozenset()
    # Set by sema on an unpack clause: its head, the per-target unpack of
    # the clause variable (the step), stamped as a for head's is.
    head: 'TpyTupleUnpack | None' = None

    @property
    def targets(self) -> list[str]:
        """The names this clause binds."""
        if self.unpack_vars is not None:
            return [u for u in self.unpack_vars if u is not None]
        return [self.var]


def comp_clause_children(generators: 'list[TpyComprehensionGenerator]'
                         ) -> list[TpyExpr]:
    """Every clause's iterable and filters, in the order CPython evaluates
    them (clause 0's iterable first, each clause's filters before the next
    clause's iterable)."""
    out: list[TpyExpr] = []
    for gen in generators:
        out.append(gen.iterable)
        out.extend(gen.conditions)
    return out


@dataclass
class TpyListComprehension(TpyExpr):
    """List comprehension: [expr for var in iterable if cond ...], one clause
    per `for`."""
    element_expr: TpyExpr
    generators: list[TpyComprehensionGenerator]
    result_elem_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return comp_clause_children(self.generators) + [self.element_expr]


@dataclass
class TpyDictComprehension(TpyExpr):
    """Dict comprehension: {key: value for var in iterable if cond ...}"""
    key_expr: TpyExpr
    value_expr: TpyExpr
    generators: list[TpyComprehensionGenerator]
    result_key_type: 'TpyType | None' = None  # set by sema
    result_value_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return comp_clause_children(self.generators) + [self.key_expr, self.value_expr]


@dataclass
class TpyDictLiteral(TpyExpr):
    """Dict literal: {key: value, key: value, ...}"""
    keys: list[TpyExpr]
    values: list[TpyExpr]

    def children(self) -> list[TpyExpr]:
        return list(self.keys) + list(self.values)


@dataclass
class TpySetComprehension(TpyExpr):
    """Set comprehension: {expr for var in iterable if cond ...}"""
    element_expr: TpyExpr
    generators: list[TpyComprehensionGenerator]
    result_elem_type: 'TpyType | None' = None  # set by sema

    def children(self) -> list[TpyExpr]:
        return comp_clause_children(self.generators) + [self.element_expr]


@dataclass
class TpyGeneratorExpression(TpyExpr):
    """Generator expression: (expr for var in iterable if cond ...)"""
    element_expr: TpyExpr
    generators: list[TpyComprehensionGenerator]
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
    # Set by the parser: the walruses this is the innermost generator
    # expression of whose target the enclosing scope (PEP 572's binding
    # scope) uses outside it: the frame writes that scope's variable.
    walrus_used_outside: 'tuple[TpyNamedExpr, ...]' = ()

    def children(self) -> list[TpyExpr]:
        return comp_clause_children(self.generators) + [self.element_expr]


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
    # Set by sema: the captures that read the creating body's own binding,
    # not one a comprehension or lambda around the lambda makes.
    body_captures: tuple[str, ...] = ()
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


class SubscriptDunder(Enum):
    """The dunder one form of a subscript calls: a read `t[k]`, a store
    `t[k] = v`, a `del t[k]` (an augmented `t[k] op= v` calls both the
    first two)."""
    GET = "__getitem__"
    SET = "__setitem__"
    DEL = "__delitem__"


@dataclass(frozen=True)
class SubscriptDunders:
    """The dunder variant each form of one subscript calls (`SubscriptDunder`),
    None for a form it does not call."""
    get: 'FunctionInfo | None' = None
    set: 'FunctionInfo | None' = None
    delete: 'FunctionInfo | None' = None

    _FIELDS = {SubscriptDunder.GET: "get", SubscriptDunder.SET: "set",
               SubscriptDunder.DEL: "delete"}

    def of(self, kind: SubscriptDunder) -> 'FunctionInfo | None':
        return getattr(self, self._FIELDS[kind])

    def stamped(self, kind: SubscriptDunder,
                fi: 'FunctionInfo') -> 'SubscriptDunders':
        return replace(self, **{self._FIELDS[kind]: fi})

    def __bool__(self) -> bool:
        return any(f is not None for f in (self.get, self.set, self.delete))


@dataclass
class TpySubscript(TpyExpr, NoneChecked):
    """Subscript indexing: obj[index]"""
    obj: TpyExpr
    index: TpyExpr  # TpySlice for slicing, other TpyExpr for single-index
    none_use: NoneUse | None = None  # Set by sema on every analysis: the receiver's None check
    enum_from_name: 'NominalType | None' = None    # Set by sema for Color["Red"] name lookup
    bounds_safe: bool = False  # Set by sema: index provably in [0, len(obj)), skip bounds check
    is_stepped_slice: bool = False  # Set by sema: slice has step (a[::2])
    # Set by sema: the key is a slice, written (`a[i:j]`) or a slice-typed
    # value (`a[s]`).
    slice_key: bool = False
    # Set by sema: the dunder variant each form of this subscript calls on a
    # record receiver (a class, library or `@native` record), resolved once
    # as the explicit `t.__getitem__(k)` / `__setitem__` / `__delitem__` call
    # is and substituted at the receiver's type args; read through `dunder`.
    dunders: SubscriptDunders = SubscriptDunders()
    # Set by sema with `dunders`: the key as written, before the variant's
    # conversion put on `index`; and on an augmented `t[k] op= v`, the key
    # as the `__setitem__` variant receives it -- the written key under that
    # variant's own conversion, so both reads evaluate it once.
    written_index: 'TpyExpr | None' = None
    setitem_index: 'TpyExpr | None' = None
    # Set by sema with `dunders`: the forms whose call passes the receiver
    # const so C++ runs the readonly variant sema ranked
    # (`pins_const_receiver`), each decided for its own variant.
    const_dunders: frozenset[SubscriptDunder] = frozenset()
    # Set by sema BEFORE the target of a plain assignment is analysed as a
    # read: `g[k] = v` calls `__setitem__` only, so the read-side analysis
    # must not credit a `__getitem__` call on the receiver.
    is_write_target: bool = False
    # Set where `t[k] op= v` is built (`TpyAugAssign`): its key is the
    # argument of both `__getitem__` and `__setitem__`.
    is_aug_target: bool = False
    typed_dict_field: str | None = None  # Set by sema: d["key"] on TypedDict -> field access
    typed_dict_optional: bool = False  # Set by sema: total=False field, needs runtime check

    def children(self) -> list[TpyExpr]:
        return [self.obj, self.index]

    def reset_dunders(self) -> None:
        """Clear what sema decided about the dunder calls: every analysis
        decides them afresh (an overload trial may have analyzed this node
        under a receiver the winner does not have), from the key as
        written."""
        if self.written_index is not None:
            self.index = self.written_index
        self.none_use = None
        self.dunders = SubscriptDunders()
        self.written_index = self.setitem_index = None
        self.const_dunders = frozenset()
        self.slice_key = self.is_stepped_slice = self.bounds_safe = False

    def dunder(self, kind: SubscriptDunder) -> 'FunctionInfo | None':
        """The dunder variant form `kind` of this subscript calls, as sema
        resolved it -- the explicit `t.__getitem__(k)` / `__setitem__` /
        `__delitem__` call's winner, substituted at the receiver; None for a
        tuple, a TypedDict field, an enum name lookup, a protocol receiver
        or a form this subscript is not."""
        return self.dunders.of(kind)

    @property
    def slice_function_info(self) -> 'FunctionInfo | None':
        """The slice variant this subscript calls: None unless its key is
        a slice, then the `__setitem__` winner of a slice store and the
        `__getitem__` winner of a slice read."""
        if not self.slice_key:
            return None
        return (self.dunders.set if self.is_write_target
                else self.dunders.get)


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
    # Placed by the step of the node holding it (`coerce_expr`), which
    # decides it anew whenever it runs; one that is part of what a call is
    # written with (a value passed at a container's leaf) is not.
    by_step: bool = field(default=False, compare=False)
    # The member of a value-union argument slot the value lands in, as the
    # compatibility check chose it; lowering renders the value at it.
    union_member: "TpyType | None" = None

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
    # A target bind of a tuple unpack, `x = __unpack_N_i` (the init names
    # the parser's temp holding the element).
    unpack_target: bool = field(default=False, kw_only=True)
    # A temporary the parser or a desugar binds: the program's variable it
    # holds a value for (`b` for `__unpack_0_1` in `a, b = (x, y)`), or with
    # no variable to name, the target as written; what a diagnostic about the
    # temporary shows (`program_names`), stamped by `stamp_stands_for`.
    stands_for: str | None = field(default=None, kw_only=True)
    # Set by sema: union assignment narrowing facts for codegen
    then_type_facts: dict[str, TpyType] = field(default_factory=dict)
    # Stamped by liveness: names (and running loop iterators) still live
    # AFTER this statement (see analyze_last_uses). None means the liveness walk never reached this
    # node, where the alias-rebind pass stays silent.
    live_names_after: frozenset[Holder] | None = field(default=None, repr=False)
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
    # Set by sema at a loop head: its loop's `TpyForEach.loop_body_rebinds`.
    loop_body_rebinds: frozenset[str] = frozenset()
    # Set by sema at a loop head: its loop's `TpyForEach.step_handover`.
    step_handover: frozenset[tuple[int, ...]] = frozenset()
    # Set by sema:
    target_types: list[TpyType] = field(default_factory=list)
    is_new: list[bool] = field(default_factory=list)
    is_owned: list[bool] = field(default_factory=list)
    is_ref: list[bool] = field(default_factory=list)
    is_const_ref: list[bool] = field(default_factory=list)
    # Set by sema on a for head: per target, it takes its own copy of the
    # element because the loop may write its source
    # (`loop_frames.stamp_copy_verdicts`).
    target_copies: list[bool] = field(default_factory=list)
    # A borrowed target the name is later rebound in: it binds a pointer
    # local, since a reference alias would write the rebind through.
    is_rebound: list[bool] = field(default_factory=list)
    # Stamped by liveness -- see TpyVarDecl.live_names_after.
    live_names_after: frozenset[Holder] | None = field(default=None, repr=False)

    def exprs(self) -> list[TpyExpr]:
        return [self.value]

    def owns_target(self, i: int) -> bool:
        """Whether target `i` takes its element over (`is_owned`; a head
        sema has not stamped owns none)."""
        return i < len(self.is_owned) and self.is_owned[i]


@dataclass
class TpyAssign(TpyStmt):
    """Assignment to variable or field."""
    target: TpyExpr
    value: TpyExpr
    # Stamped by liveness -- see TpyVarDecl.live_names_after.
    live_names_after: frozenset[Holder] | None = field(default=None, repr=False)
    # Stamped by sema for a name target -- see TpyVarDecl.rebind_storage.
    rebind_storage: RebindStorage | None = field(default=None, repr=False)

    def exprs(self) -> list[TpyExpr]:
        return [self.target, self.value]


@dataclass
class AugHeldPart:
    """One part of `t[k] op= v` / `o.n op= v`'s target evaluated once.
    `receiver`: a field, element or method is read off it in place. Not
    frozen: a tree rewrite after sema (a settled number's conversion
    placeholder spliced out) updates `expr` with the tree."""
    expr: TpyExpr
    receiver: bool = False
    # The key as written, a value the statement creates, at a record
    # `__setitem__` taking its key by ownership: the setter consumes it.
    setter_consumes: bool = False


@dataclass
class TpyAugAssign(TpyStmt):
    """Augmented assignment (+=, -=, etc.)"""
    target: TpyExpr
    op: str
    value: TpyExpr
    resolved_binop: 'ResolvedBinop | None' = None  # Set by sema for builtin ops
    resolved_inplace: 'ResolvedBinop | None' = None  # Set by sema for in-place ops (__iadd__, __ior__, etc.)
    # Set by sema where the store receives the operation's result through a
    # conversion: a stand-in `target op value` node (which lowering supplies
    # the operation for) under that conversion -- a record subscript's
    # `__setitem__` value parameter, or the checked narrowing into any other
    # target (`x8 += 1` computes in the default int). Not a child.
    stored_value: 'TpyExpr | None' = None
    # Set by sema on a subscript or attribute target: the target parts the
    # statement evaluates once, ahead of the store, and reads by name
    # (`sema/aug_hold.py`), in evaluation order. Not children.
    hold_parts: 'tuple[AugHeldPart, ...]' = ()

    def __post_init__(self) -> None:
        if isinstance(self.target, TpySubscript):
            self.target.is_aug_target = True

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
    # With the stamp on a returned name: that name, under the identity
    # coercions the return may wrap it in.
    finally_deferred_name: Optional['TpyName'] = None
    # With the stamp on a returned tuple literal: the index paths of the
    # members that are such locals (each captured by pointer before the
    # chain); every other member is evaluated into a temporary there.
    finally_deferred_leaves: tuple[tuple[int, ...], ...] = ()

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
    # Set by sema: the variable takes its own copy of the element -- the
    # loop may write its source while it is live
    # (`loop_frames.stamp_copy_verdicts`, read by the view rule and the
    # binding planner).
    copies_var: bool = False
    hoist_loop_var: bool = False  # set by sema: loop var used after loop, needs pre-declaration
    # Set by sema: the names the loop body rebinds -- the variable's (or a
    # head unpack's targets') binding sees only these, since each pass binds
    # it afresh. None until sema reaches the loop.
    loop_body_rebinds: frozenset[str] | None = None
    # Stamped by liveness: the loop variable is live on some exit from the
    # loop (a read after it, in its `else` clause, or on a later pass of an
    # enclosing loop) -- the only place a zero-trip head's leftover value is
    # observable. None means the liveness walk never reached this node.
    var_live_after: bool | None = field(default=None, repr=False)
    # Set by sema at the ITER registration: the lvalue iterable names storage
    # no loan key can spell (`self.grid.rows[i]`, `table[k].cells`), so nothing
    # matches a mutation of what is iterated against the iteration's borrow.
    # Codegen refuses such a loop rather than hand out unguarded references.
    iter_borrow_unplaceable: bool = False
    consuming_iter_fi: 'FunctionInfo | None' = None  # set by sema: consuming __iter__ overload at last use
    # Set by sema before the body: the leaf paths of each step the iteration
    # hands over (`step_handover_paths`), stamped on the head unpack too.
    step_handover: frozenset[tuple[int, ...]] = frozenset()
    is_async: bool = False  # Set by parser: `async for` (lowers to __aiter__/await __anext__ inside async def)
    async_aiter_type: 'NominalType | None' = None  # set by sema for async-for: the DEFINING record of __anext__ (an ancestor of the iterator when inherited), used by codegen to name the __anext__ coro struct
    # Set by parser: the iterable as the program spells it, the loop's name
    # in a diagnostic about its iterator.
    iter_text: str | None = None

    def exprs(self) -> list[TpyExpr]:
        return [self.iterable]

    def sub_bodies(self) -> list[list[TpyStmt]]:
        return [self.body, self.orelse]

    def head_unpack(self) -> 'TpyTupleUnpack | None':
        """The head's unpack of the per-iteration holder into its targets
        (`for a, b in xs`), else None."""
        if self.is_tuple_unpack and self.body and isinstance(
                self.body[0], TpyTupleUnpack):
            return self.body[0]
        return None

    def head_targets(self) -> list[str]:
        """The names the head binds: the head unpack's targets, else the
        loop variable."""
        head = self.head_unpack()
        if head is None:
            return [self.var]
        return [t for t in head.targets if t is not None]


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
    """raise E, raise <expr>, or bare raise (re-raise in except block).

    `raise X(args)` is the expression form: its operand is the call as
    written, a construction sema analyzes as any other."""
    exception_type: str | None = None  # type form `raise E` (set by parser, qualified by sema)
    raise_expr: TpyExpr | None = None  # expression form: raise <expr>
    deref_depth: int = 0  # Set by sema: __deref__ steps to peel before calling __raise__ (Phase 20)
    # Set by sema: the class raised -- the type form's (its record's type),
    # or the instantiation a construction operand builds -- so the raise
    # tier and C++ spelling read the class, not its name. None for a value
    # operand (`raise e`), whose dynamic class `__raise__()` dispatches on.
    resolved_type: Optional[TpyType] = None
    # Set by sema when the raised class is @virtual_raise: codegen emits
    # `X(args).__raise__();` so the class's dispatching __raise__ applies.
    raise_via_virtual: bool = False

    def exprs(self) -> list[TpyExpr]:
        return [self.raise_expr] if self.raise_expr is not None else []

    def raised_construction(self) -> TpyExpr | None:
        """The construction the operand is, when it raises a fresh object of
        the class sema resolved (`resolved_type`): the call node that
        builds it (a dotted callee's expansion)."""
        if self.raise_expr is None or self.resolved_type is None:
            return None
        node = self.raise_expr
        while getattr(node, "macro_expansion", None) is not None:
            node = node.macro_expansion
        return node


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
    # The names the clause spells (`("m", "Outer", "Err")`); sema resolves
    # them like any type path.
    exception_path: tuple[str, ...] = ()
    # Set by sema: the class caught (its record's type).
    resolved_type: Optional[TpyType] = None


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
    # Set by sema where it resolves `__exit__`: whether the exit step runs
    # TPy code (None: not decided yet), and then the hidden call on the
    # manager every call-recognizer reads, as `TpyBinOp.contains_call`;
    # `enter_call` is the same for `__enter__`.
    exit_runs_code: bool | None = None
    exit_call: 'TpyMethodCall | None' = None
    enter_call: 'TpyMethodCall | None' = None
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
    # Set by sema: capture-list FrameType, the def's own frame fact
    frame_type: 'TpyType | None' = None


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
    # Set by liveness: the names live right after the arm's captures bind
    # (its guard and body, and the arms a failed guard falls through to).
    live_names_after: frozenset[Holder] | None = field(default=None, repr=False)


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

# The record methods generated special members run around: the destructor
# body (under its drop flag) and the copy / relocating move constructor bodies.
DEL_HOOK, COPY_HOOK, MOVE_HOOK = "__del__", "__copy__", "__move__"
LIFECYCLE_HOOKS = frozenset({DEL_HOOK, COPY_HOOK, MOVE_HOOK})
INIT_METHOD = "__init__"


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
    is_readonly: bool = False
    readonly_opt_out: bool = False
    is_pure: bool = False
    is_transient: bool = False
    # `checks_signals=True` on the binding -- see FunctionInfo.checks_signals.
    checks_signals: bool = False
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
    # Equivalent to `clone_of is not None`: the two record one fact.
    is_auto_readonly_mutable_clone: bool = False
    # On the mutable clone: the const clone of the same def, which defines
    # the pair's one callable. A link rather than a shared id so a reader
    # finds the defining body without a lookup; kept out of eq/repr, which
    # would otherwise walk into the other clone.
    clone_of: TpyFunction | None = field(default=None, compare=False, repr=False)
    # On both clones: the result as declared, with its `auto_readonly[...]`
    # markers (a property getter's, or an `@auto_readonly` def's unmarked
    # reference result, marked whole), and the components those markers sit
    # at (`typesys.auto_readonly_components`).
    marked_return_type: Any = field(default=None, compare=False, repr=False)
    following_components: frozenset = frozenset()
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
    # @native / @cpp_template(borrows=(...), element_of=(...)): the parameter
    # names a bodyless binding's result IS / is an element of, validated and
    # stamped into FunctionInfo at registration. None = neither keyword.
    declared_borrows: tuple[str, ...] | None = None
    declared_element_of: tuple[str, ...] = ()
    # @native(mutates="elements"): see FunctionInfo.native_mutates.
    native_mutates: str | None = None
    # @native(element_effect=...): see FunctionInfo.native_element_effect.
    native_element_effect: str | None = None
    # @native / @cpp_template(element_access=...): see FunctionInfo.element_access.
    element_access: bool | str | None = None
    # Set when @export is applied inside an ext module: the function
    # stays DEFAULT linkage (an ordinary TPy function) but the extension glue
    # generator emits a CPython wrapper + PyMethodDef entry for it.
    exposed_to_host: bool = False
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
    # The captures a genexpr's body WRITES: the target of a walrus the
    # enclosing scope also uses, which PEP 572 binds there. The body reads
    # each as an implicit `nonlocal` of the enclosing scope, never as a
    # parameter of its own.
    genexpr_nonlocals: 'tuple[str, ...]' = ()
    # The module globals a genexpr's body writes the same way, by name:
    # its enclosing scope is the module, or declares them `global`.
    genexpr_globals: 'tuple[str, ...]' = ()
    # Set by sema on a genexpr's function: its deduced source slot is bound to
    # a native container LVALUE (a nested def cannot give that one its concrete
    # type), whose adaptor copies a value-tuple step result -- so an unpack
    # head must not alias reference members through it.
    genexpr_container_by_protocol: bool = False
    # The function the genexpr is written in (None at module level): what a
    # diagnostic about the body names, since the user never wrote this one.
    genexpr_owner: str | None = None
    # Set by sema on a genexpr's function: what each step of its source
    # hands over, as its first clause reads the source where it is written
    # (`step_handover_paths`) -- the source param's type is the clause's
    # element type, substitutions included, so it is no declaration.
    genexpr_step_handover: 'frozenset[tuple[int, ...]]' = frozenset()
    # Set by sema on a genexpr's function: the loans its INNER `for` clauses
    # hold on storage reached through a capture, keyed in the frame's names
    # (a capture param is named as the variable it binds). Such a capture is
    # iterated across pulls like the source, not merely read afresh at each
    # one.
    genexpr_inner_loans: 'tuple[tuple[StorageKey, LoanInfo], ...]' = ()
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
    def genexpr_loop(self) -> 'TpyForEach | None':
        """The loop a generator expression's function is: its whole body, the
        source's for-each around the filters and the yield. None for any
        other function."""
        if not self.is_genexpr or not self.body:
            return None
        loop = self.body[0]
        return loop if isinstance(loop, TpyForEach) else None

    @property
    def genexpr_loops(self) -> 'list[TpyForEach]':
        """Every loop of a generator expression's function, one per `for`
        clause, outermost (`genexpr_loop`) first. Empty for any other
        function."""
        loops: list[TpyForEach] = []
        stmts: list[TpyStmt] = [self.genexpr_loop] if self.genexpr_loop else []
        while stmts:
            nxt: list[TpyStmt] = []
            for s in stmts:
                if isinstance(s, TpyForEach):
                    loops.append(s)
                    nxt.extend(s.body)
                elif isinstance(s, TpyIf):
                    nxt.extend(s.then_body)
            stmts = nxt
        return loops

    def is_capture(self, pname: str) -> bool:
        """Whether `pname` is a lexical capture rather than an argument the
        caller hands over: it binds the enclosing variable, whatever that
        variable owns and in whatever C++ form it holds it there. This body
        never owns it, so it never moves from it."""
        return pname in self.capture_params

    def takes_ownership_of(self, pname: str, ptype: 'TpyType') -> bool:
        """Whether this body owns what parameter `pname` (declared `ptype`)
        carries: an ownership-transfer parameter (`param_takes_ownership`)
        that the caller hands over, never a capture."""
        return not self.is_capture(pname) and param_takes_ownership(ptype)

    @property
    def is_overload_stub(self) -> bool:
        return self.overload_form is not None

    @property
    def is_typing_overload_stub(self) -> bool:
        """A `typing.overload` stub (not a `@dispatch` variant): one
        signature of the trailing implementation, which is specialized and
        emitted per stub."""
        return self.overload_form is OverloadForm.OVERLOAD

    @property
    def is_lifecycle_hook(self) -> bool:
        """A record method the generated special members run around
        (`TpyRecord.del_method` / `copy_method` / `move_method`): its `self`
        is not an initialized object borrowed for a call."""
        return self.is_method and self.name in LIFECYCLE_HOOKS

    @property
    def is_initializer(self) -> bool:
        """A record's `__init__` body (`TpyRecord.init_method` or one of its
        overloads): its `self` is storage under construction."""
        return self.is_method and self.name == INIT_METHOD

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
    # @native(elements=True): a value owns the values of its type arguments
    # as elements in storage of its own (see TypeDef.owns_elements).
    owns_elements: bool = False
    # @native(borrowing_view=True): every value of this (value) type is a
    # borrow handle into storage it does not own, so returns and yields of
    # it are lifetime-checked.
    is_borrowing_view: bool = False
    # @native(iter_yields_ref_tuple_proxies=True): iterating yields tuples of
    # references by value (see TypeDef.iter_yields_ref_tuple_proxies).
    iter_yields_ref_tuple_proxies: bool = False
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
    # Bare `@export` inside an ext module: expose this class to the
    # host CPython module as a real Python type (PyType_FromSpec). Mirrors
    # TpyFunction.exposed_to_host; the record keeps DEFAULT linkage (the glue
    # is separate). Set only by the parser inside an ext_module.
    exposed_to_host: bool = False
    # Set on the companion record an `Enum` body's methods live on
    # (`__enum_<Name>`): the enum's name. The companion is never bound in the
    # module namespace; only the enum's EnumInfo reaches it.
    enum_companion_of: str | None = None

    @property
    def display_name(self) -> str:
        """The name a diagnostic spells: an enum's companion is the enum the
        user wrote, never its internal record name."""
        return self.enum_companion_of or self.name

    # The class body's leading string literal. A function/method keeps its
    # docstring as `body[0]`, but a record has no statement body to read it
    # back from, so the text is captured here at parse time. Consumed only by
    # the CPython-extension glue (`Py_tp_doc`).
    docstring: str | None = None
    loc: SourceLocation | None = None

    @property
    def init_method(self) -> Optional[TpyFunction]:
        """Get __init__ method if present."""
        return next((m for m in self.methods if m.name == INIT_METHOD), None)

    def _lifecycle_hook(self, name: str) -> Optional[TpyFunction]:
        return next((m for m in self.methods if m.name == name), None)

    @property
    def del_method(self) -> Optional[TpyFunction]:
        """Get __del__ method if present (the destructor body, under the drop flag)."""
        return self._lifecycle_hook(DEL_HOOK)

    @property
    def copy_method(self) -> Optional[TpyFunction]:
        """Get __copy__ method if present (custom copy ctor body)."""
        return self._lifecycle_hook(COPY_HOOK)

    @property
    def move_method(self) -> Optional[TpyFunction]:
        """Get __move__ method if present (custom relocating move ctor body)."""
        return self._lifecycle_hook(MOVE_HOOK)


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
    # The record carrying the body's `def`s, or None when it declares none.
    companion: 'TpyRecord | None' = None
    loc: SourceLocation | None = None


@dataclass
class ParseWarning:
    """A warning generated during parsing."""
    message: str
    loc: SourceLocation | None


@dataclass
class ModuleDirectives:
    """Module-level compiler settings from the module's `__tpy__` dict."""
    # Each entry: (include_path, platform_filter_or_None)
    includes: list[tuple[str, str | None]] = field(default_factory=list)
    # Each entry: (lib_name, platform_filter_or_None)
    link_libs: list[tuple[str, str | None]] = field(default_factory=list)
    # Third-party library declarations: (registry_name, platform_filter).
    # Resolved at build time via tpyc.build.third_party into include paths,
    # link flags, and CMake snippets based on the user's selected mode.
    third_party_deps: list[tuple[str, str | None]] = field(default_factory=list)
    # `__tpy__["mod"]`; None for an ordinary module.
    kind: 'Literal["native", "macro", "ext"] | None' = None
    # Override C++ namespace (replaces tpyapp::module_name)
    cpp_namespace: str | None = None

    @property
    def native_module(self) -> bool:
        return self.kind == "native"

    @property
    def ext_module(self) -> bool:
        """CPython extension module: @export exposes to the host interpreter
        and the build emits a PyInit_-exporting .so instead of an executable."""
        return self.kind == "ext"


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
    # Module-level `__tpy__` settings
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


def is_init_trivia(stmt: TpyStmt) -> bool:
    """A docstring or `pass` in `__init__`: it runs nothing, so it neither
    displaces a parent-initializer call from the leading run nor closes the
    init section."""
    return is_docstring(stmt) or isinstance(stmt, TpyPassStmt)


def is_module_declaration(stmt: TpyStmt) -> bool:
    """A module-level statement that runs none of the module's own code:
    an import, a bare `name: T` declaration, a docstring or `pass`."""
    return (isinstance(stmt, TpyImport) or is_init_trivia(stmt)
            or (isinstance(stmt, TpyVarDecl) and stmt.init is None))


def runs_no_init_code(stmt: TpyStmt) -> bool:
    """A module-level statement the module's init runs no code for: a
    declaration, a `Final` constant (a compile-time constant emitted at
    namespace scope) or a `native_global(...)` binding (defined outside the
    module). The import-cycle gate keeps the narrower
    `is_module_declaration`: a constant there would need a peer's header to
    carry it first. For a DIRECT module-level statement it being true
    implies the top-level lowering emits none of the module's own code for
    it (an import emits only the imported modules' init calls); the
    lowering's no-op arms answer a different question -- about a position,
    which also covers statements nested in module-level blocks."""
    return (is_module_declaration(stmt)
            or (isinstance(stmt, TpyVarDecl)
                and (stmt.is_final or stmt.linkage != VarLinkage.DEFAULT)))


def init_leading_run_end(body: list[TpyStmt]) -> int:
    """Index of the first `__init__` statement that is neither trivia nor a
    parent-initializer call. Only the calls before it keep their CPython
    meaning when C++ runs them from the member initializer list, ahead of
    every body statement."""
    for i, stmt in enumerate(body):
        if not (is_init_trivia(stmt) or is_base_init_call(stmt)):
            return i
    return len(body)


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
            # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
            stack.extend(node.children())
            if into_lambdas and isinstance(node, TpyLambda):
                stack.append(node.body)
    return names


def comp_free_reads(expr: TpyExpr) -> list[TpyName]:
    """The name reads `expr` makes of the scope it is written in: a nested
    comprehension's or lambda's own variables are not such reads (clause 0's
    iterable of a nested comprehension is, it runs outside that scope)."""
    if isinstance(expr, TpyName):
        return [expr]
    if isinstance(expr, TpyLambda):
        params = set(expr.param_names)
        return [r for r in comp_free_reads(expr.body) if r.name not in params]
    if isinstance(expr, (TpyListComprehension, TpySetComprehension,
                         TpyDictComprehension, TpyGeneratorExpression)):
        gens = expr.generators
        bound = {n for gen in gens for n in gen.targets}
        out = comp_free_reads(gens[0].iterable)
        for e in expr.children():
            if e is not gens[0].iterable:
                out += [r for r in comp_free_reads(e) if r.name not in bound]
        return out
    return [r for child in expr.children() for r in comp_free_reads(child)]


def comp_sinks(expr: 'TpyListComprehension | TpySetComprehension '
                      '| TpyDictComprehension') -> list[TpyExpr]:
    """The expression(s) a comprehension evaluates per innermost iteration
    after its filters: the element, or the dict key and value."""
    if isinstance(expr, TpyDictComprehension):
        return [expr.key_expr, expr.value_expr]
    return [expr.element_expr]


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
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
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
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
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
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
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
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
        stack.extend(n.children())
    return out


def written_names(stmt: TpyStmt, *, match_binds: bool = True,
                  walrus: bool = True) -> set[str]:
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
    outer alias points at?" want False.

    `walrus=False` leaves the walrus targets out, for a caller that counts
    them one by one (`walrus_bindings`)."""
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

    if walrus:
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
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
        for c in e.children():
            collect(c)

    for e in stmt.exprs():
        collect(e)
    return found


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


def program_names(name: str, body: list[TpyStmt]) -> tuple[str | None, ...]:
    """The program's variables a local of `body` stands for, the names a
    diagnostic about it shows: the local itself, or for a temporary the
    parser or a desugar made, the variable it holds a value for
    (`TpyVarDecl.stands_for`) -- for the element holder of a
    tuple-unpacking `for` (`TpyForEach.is_tuple_unpack`), every target of
    its head unpack (None: `_`)."""
    found: list[tuple[str | None, ...]] = []

    def visit(s: TpyStmt) -> None:
        if found:
            return
        if (isinstance(s, TpyVarDecl) and s.name == name
                and s.stands_for is not None):
            found.append((s.stands_for,))
        elif (isinstance(s, TpyForEach) and s.is_tuple_unpack
              and s.var == name and s.body
              and isinstance(s.body[0], TpyTupleUnpack)):
            found.append(tuple(s.body[0].targets))

    walk_body_stmts(body, lambda e: None, visit)
    return found[0] if found else (name,)


def stamp_stands_for(temps: list[TpyStmt], target: str,
                     value: 'TpyExpr | None') -> None:
    """Stamp the temporaries among `temps` that `value` carries to the
    program's variable `target` -- the one `value` names, and through each
    copy one temporary makes of another, the one it names -- with
    `TpyVarDecl.stands_for`. Every temporary the parser or a desugar mints
    is stamped here; the first variable to claim one keeps it."""
    decls: dict[str, list[TpyVarDecl]] = {}

    def note(s: TpyStmt) -> None:
        if isinstance(s, TpyVarDecl):
            decls.setdefault(s.name, []).append(s)

    walk_body_stmts(temps, lambda e: None, note)
    work = [value.name] if isinstance(value, TpyName) else []
    while work:
        for d in decls.get(work.pop(), ()):
            if d.stands_for is None:
                d.stands_for = target
                if isinstance(d.init, TpyName):
                    work.append(d.init.name)


def walk_body_stmts(
    stmts: list[TpyStmt],
    on_expr: Callable[[TpyExpr], None],
    on_stmt: Callable[[TpyStmt], None],
    *,
    on_stmt_expr: Callable[[TpyStmt, TpyExpr], None] | None = None,
) -> None:
    """Walk statements calling on_expr/on_stmt. Does NOT recurse into
    TpyNestedDef. `on_stmt_expr`, when given, takes each expression in
    place of on_expr, with the statement that holds it."""
    for stmt in stmts:
        on_stmt(stmt)
        if isinstance(stmt, TpyNestedDef):
            continue  # separate scope
        for expr in stmt.exprs():
            if on_stmt_expr is None:
                on_expr(expr)
            else:
                on_stmt_expr(stmt, expr)
        for body in stmt.sub_bodies():
            walk_body_stmts(body, on_expr, on_stmt, on_stmt_expr=on_stmt_expr)


def value_capture_lambdas(stmts: list[TpyStmt], *, nested: bool = False
                          ) -> 'list[tuple[TpyLambda, tuple[TpyLambda, ...]]]':
    """The lambdas that copy their captures (`captures_by_value`) which
    `stmts` create at their own scope, in source order, each with the
    lambdas enclosing it there. A lambda's body is a scope of its own;
    `nested` walks into it too, since a lambda inside one that captures by
    reference copies the outer body's names all the same."""
    found: list[tuple[TpyLambda, tuple[TpyLambda, ...]]] = []

    def visit_in(chain: tuple[TpyLambda, ...]) -> Callable[[TpyExpr], bool]:
        def visit(e: TpyExpr) -> bool:
            if not isinstance(e, TpyLambda):
                return True
            if e.captures_by_value:
                found.append((e, chain))
            if nested:
                walk_expr_tree(e.body, visit_in(chain + (e,)))
            return False
        return visit

    walk_body_stmts(stmts, lambda e: walk_expr_tree(e, visit_in(())),
                    lambda s: None)
    return found


def walk_expr_tree(expr: TpyExpr, visit: Callable[[TpyExpr], bool]) -> None:
    """Pre-order walk of an expression and its `children()`. `visit` answers
    whether to descend into the node's children, so a visitor can judge a
    subtree as a whole (a field chain, a call with its callee name, a slice)
    and skip its parts; the other walks in this module and in sema each
    carry their own `children()` loop and are to move onto this one."""
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if visit(node):
            stack.extend(reversed(node.children()))


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


def keeps_argument_layout(attr: str, old: object, new: object) -> bool:
    """Whether a generic walk storing `new` in place of `old` at field
    `attr` of a node keeps a call's argument layout: an argument list or
    keyword dict replaced whole keeps its length (its operands may change;
    a layout change is the commit's or `relayout`'s)."""
    return (attr not in ("args", "kwargs")
            or not isinstance(old, (list, dict))
            or (isinstance(new, (list, dict)) and len(new) == len(old)))


def rewrite_tree(roots: object, replace: Callable[[object], object],
                 skip: frozenset[str] = frozenset()) -> None:
    """Visit every parse node `roots` holds -- through fields, lists,
    tuples and dicts -- once, in pre-order and field order, storing back
    `replace(v)` for every value; a tuple is rebuilt when an element
    changes. A field named in `skip` is neither rewritten nor walked."""
    seen: set[int] = set()
    stack: list[object] = []

    def fix(v: object) -> object:
        v = replace(v)
        if is_parse_node(v):
            stack.append(v)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                v[i] = fix(x)
        elif isinstance(v, tuple):
            items = tuple(fix(x) for x in v)
            if any(a is not b for a, b in zip(items, v)):
                v = items
        elif isinstance(v, dict):
            for k, x in v.items():
                v[k] = fix(x)
        return v

    fix(roots)
    stack.reverse()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        mark = len(stack)
        for attr, v in list(vars(node).items()):
            if attr in skip:
                continue
            u = fix(v)
            if u is not v:
                assert keeps_argument_layout(attr, v, u), attr
                setattr(node, attr, u)
        stack[mark:] = stack[mark:][::-1]


def unshare(roots: object) -> None:
    """Give every expression and statement that `roots` -- one macro's
    output, not analyzed yet -- holds at more than one place a copy of its
    own at each place after the first. Each place is an evaluation of its
    own, analyzed under its own flow state and hint; one node would carry
    the facts of whichever analysis ran last. A list or dict held at two
    places (one `body` given to two methods) is copied too: unsharing its
    elements in place would leave both places holding the copies. A
    call's indexed-callee alternative (`subscript_callee`) shares its
    receiver with the call by design and is left as it is."""
    met: set[int] = set()

    def own(v: object) -> object:
        if isinstance(v, (TpyExpr, TpyStmt, list, dict)):
            if id(v) in met:
                # A copied container's elements are met already, so the
                # walk then copies each of them.
                return (copy.copy(v) if isinstance(v, (list, dict))
                        else copy.deepcopy(v))
            met.add(id(v))
        return v

    rewrite_tree(roots, own, skip=frozenset(("subscript_callee",)))
