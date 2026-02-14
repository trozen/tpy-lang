"""
TurboPython AST node definitions.

Pure data definitions: dataclasses, ParseError, SourceLocation.
No parsing logic lives here.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, FieldInfo,
    MethodSignature, TypeParamKind,
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
    narrowed_optional_proven: bool = field(default=False, kw_only=True)


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


@dataclass
class TpyBinOp(TpyExpr):
    """Binary operation."""
    left: TpyExpr
    op: str  # '+', '-', '*', '/', '%', '==', '!=', '<', '>', '<=', '>='
    right: TpyExpr
    resolved_binop: 'ResolvedBinop | None' = None  # Set by sema for builtin ops


@dataclass
class TpyUnaryOp(TpyExpr):
    """Unary operation."""
    op: str  # '-', 'not'
    operand: TpyExpr
    resolved_unaryop: 'ResolvedUnaryop | None' = None  # Set by sema for builtin ops


@dataclass
class TpyCall(TpyExpr):
    """Function or constructor call.

    For generic function calls like first[Int32](items):
    - type_args stores the explicit type arguments (e.g., (Int32,))
    - inferred_type_args is set by sema for codegen (resolved from inference or explicit)
    """
    func: str
    args: list[TpyExpr]
    call_type: Optional[TpyType] = None  # For generic instantiation like MyContainer[T, N]()
    type_args: tuple[TpyType, ...] = ()  # Explicit type args for generic function calls: func[T](args)
    inferred_type_args: tuple[TpyType, ...] | None = None  # Set by sema for generic function calls
    type_args_parse_error: str | None = None  # Set if subscript had args that couldn't be parsed as types
    kwargs: dict[str, TpyExpr] = field(default_factory=dict)  # Keyword arguments (limited support)
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved function overloads


@dataclass
class TpyMethodCall(TpyExpr):
    """Method call on an object."""
    obj: TpyExpr
    method: str
    args: list[TpyExpr]
    is_static_call: bool = False  # Set by sema for ClassName.staticmethod() calls
    super_parent_type: Optional[TpyType] = None  # Set by sema for super().method() calls
    user_module_call: Optional[str] = None  # Set by sema for module.func() calls to user modules
    builtin_module_call: Optional[str] = None  # Set by sema for builtin module.func() calls (canonical module name)
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access
    resolved_function_info: FunctionInfo | None = None  # Set by sema for resolved method overloads


@dataclass
class TpyFieldAccess(TpyExpr):
    """Field access on a value or pointer."""
    obj: TpyExpr
    field: str
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access


@dataclass
class TpyArrayLiteral(TpyExpr):
    """Array literal: [expr, expr, ...]"""
    elements: list[TpyExpr]


@dataclass
class TpyListRepeat(TpyExpr):
    """List repetition: [elements...] * count -> sequence repeated count times"""
    elements: list[TpyExpr]
    count: TpyExpr


@dataclass
class TpySubscript(TpyExpr):
    """Subscript indexing: obj[index]"""
    obj: TpyExpr
    index: TpyExpr
    needs_optional_runtime_check: bool = False  # Set by sema for unproven Optional access


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


@dataclass
class TpyStmt:
    """Base class for statements."""
    loc: SourceLocation | None = field(default=None, kw_only=True)


class VarLinkage(Enum):
    """Linkage mode for global variable imports."""
    DEFAULT = "default"
    NATIVE = "native"        # C++ global import
    NATIVE_C = "native_c"    # C global import (extern "C")


@dataclass
class TpyVarDecl(TpyStmt):
    """Variable declaration with optional initializer."""
    name: str
    type: Optional[TpyType]
    init: Optional[TpyExpr]
    linkage: VarLinkage = VarLinkage.DEFAULT
    native_name: str | None = None


@dataclass
class TpyAssign(TpyStmt):
    """Assignment to variable or field."""
    target: TpyExpr
    value: TpyExpr


@dataclass
class TpyAugAssign(TpyStmt):
    """Augmented assignment (+=, -=, etc.)"""
    target: TpyExpr
    op: str
    value: TpyExpr
    resolved_binop: 'ResolvedBinop | None' = None  # Set by sema for builtin ops


@dataclass
class TpyExprStmt(TpyStmt):
    """Expression statement (e.g., function call)."""
    expr: TpyExpr


@dataclass
class TpyReturn(TpyStmt):
    """Return statement."""
    value: Optional[TpyExpr]


@dataclass
class TpyAssert(TpyStmt):
    """Assert statement."""
    condition: TpyExpr
    message: TpyExpr | None = None


@dataclass
class TpyIf(TpyStmt):
    """If statement."""
    condition: TpyExpr
    then_body: list[TpyStmt]
    else_body: list[TpyStmt]


@dataclass
class TpyWhile(TpyStmt):
    """While loop."""
    condition: TpyExpr
    body: list[TpyStmt]


@dataclass
class TpyForEach(TpyStmt):
    """For-each loop over a collection."""
    var: str
    iterable: TpyExpr
    body: list[TpyStmt]


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
    """global x, y — declares names as referring to module-level variables."""
    names: list[str]


@dataclass
class TpyRaiseStopIteration(TpyStmt):
    """raise StopIteration — terminates __next__ method."""
    pass


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
    EXTERN_C = "extern_c"    # C export (has body)


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
    is_method: bool = False
    is_staticmethod: bool = False
    linkage: FunctionLinkage = FunctionLinkage.DEFAULT
    native_name: str | None = None
    is_stub: bool = False
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    loc: SourceLocation | None = None

    @property
    def is_extern_c(self) -> bool:
        return self.linkage == FunctionLinkage.EXTERN_C

    @property
    def is_extern_cpp(self) -> bool:
        return self.linkage == FunctionLinkage.NATIVE

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
    loc: SourceLocation | None = None

    @property
    def init_method(self) -> Optional[TpyFunction]:
        """Get __init__ method if present."""
        for m in self.methods:
            if m.name == "__init__":
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
    loc: SourceLocation | None = None


@dataclass
class ParseWarning:
    """A warning generated during parsing."""
    message: str
    loc: SourceLocation | None


@dataclass
class TpyModule:
    """Top-level module."""
    records: list[TpyRecord]
    functions: list[TpyFunction]
    protocols: list[TpyProtocol] = field(default_factory=list)
    top_level_stmts: list[TpyStmt] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)  # Original source lines for source mapping
    # Import tracking: module_name -> set of (original_name, local_name) tuples (for "from X import Y as Z")
    #                  module_name -> None (for "import X")
    #                  module_name -> "*" (for "from X import *")
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    # User module imports (modules not in SPECIAL_MODULES, resolved as files): {module_name: line_number}
    user_module_imports: dict[str, int] = field(default_factory=dict)
    # Module aliases from "from . import submod" -> {canonical_name: local_name}
    module_aliases: dict[str, str] = field(default_factory=dict)
    # Parser warnings (e.g., imports after non-import code)
    parse_warnings: list[ParseWarning] = field(default_factory=list)
