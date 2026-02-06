"""
TurboPython Parser

Uses CPython's ast module to parse TurboPython source code.
Validates that only allowed constructs are used.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
from typing import Optional, Union, TYPE_CHECKING

from .typesys import (
    TpyType, NamedType, PtrType, ConstPtrType, OwnType, SelfType, TypeParamRef,
    INT32, VOID, STR, CHAR, BOOL, FLOAT, BIGINT, SELF, FieldInfo, RecordInfo, TypeRegistry,
    MethodSignature, ProtocolInfo, TypeParamKind
)
from .modules import lookup_generic_type, lookup_protocol as lookup_builtin_protocol, BuiltinTypeDef


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


if TYPE_CHECKING:
    from .coercions import Coercion


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
    # Note: sema sets resolved_function_info (FunctionInfo) for codegen


@dataclass
class TpyFieldAccess(TpyExpr):
    """Field access on a value or pointer."""
    obj: TpyExpr
    field: str


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


@dataclass
class TpyVarDecl(TpyStmt):
    """Variable declaration with optional initializer."""
    name: str
    type: Optional[TpyType]
    init: Optional[TpyExpr]


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
class TpyFor(TpyStmt):
    """For loop (range-based only)."""
    var: str
    start: TpyExpr
    end: TpyExpr
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
class TpyImport(TpyStmt):
    """Import statement for user modules.

    Only user module imports (not builtins like tpy, typing) become TpyImport nodes.
    These are emitted as __tpy_init() calls in codegen.

    For relative imports:
    - level: Number of dots (0=absolute, 1=".", 2="..", etc.)
    - relative_name: Original module name after dots (None for "from . import X")
    - module_name: Initially a placeholder "__rel__{level}__{name}", resolved during discovery

    For aliased imports (import X as Y):
    - alias: The local name (Y) if different from module_name
    """
    module_name: str
    level: int = 0
    relative_name: str | None = None
    alias: str | None = None


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
    is_method: bool = False
    is_staticmethod: bool = False
    type_params: list[str] = field(default_factory=list)
    type_param_bounds: dict[str, TpyType] = field(default_factory=dict)
    loc: SourceLocation | None = None


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


# Modules with special parser handling (not resolved as user files)
# tpy: type imports, __future__: ignored, typing: type hints, builtins: always available
# Note: math, time, sys can be shadowed by user files and are NOT in this set
SPECIAL_MODULES = {"tpy", "__future__", "typing", "builtins"}

# Types from tpy that require explicit import (not auto-available like Python builtins)
# Python builtins (int, str, bool, list, float, None) remain auto-available
TPY_TYPES = {
    "Int32", "Char", "Bool",  # Basic tpy types
    "Span", "Array", "StaticList",  # Container types
    "Ptr", "ConstPtr", "Own",  # Pointer types
}

# Operator-to-string mappings for AST binary, comparison, and unary operators
_BINOP_TO_STR: dict[type, str] = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
    ast.Div: "div", ast.Mod: "%", ast.FloorDiv: "//",
    ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
    ast.LShift: "<<", ast.RShift: ">>",
    ast.Pow: "**",
}

_CMPOP_TO_STR: dict[type, str] = {
    ast.Eq: "==", ast.NotEq: "!=",
    ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=",
    ast.In: "in", ast.NotIn: "not in",
}

_UNARYOP_TO_STR: dict[type, str] = {
    ast.USub: "-", ast.Not: "!", ast.Invert: "~",
}


def check_tpy_type_imported(
    name: str, resolved_name: str, node: ast.AST,
    tpy_star_import: bool, tpy_import_aliases: dict[str, str],
) -> None:
    """Check that a tpy type was explicitly imported before use."""
    if resolved_name not in TPY_TYPES:
        return
    if tpy_star_import:
        return
    if name in tpy_import_aliases:
        return
    raise ParseError(
        f"'{name}' is not defined. Did you mean: from tpy import {resolved_name}",
        node
    )


class Parser:
    """Parser for TurboPython source code."""

    FORBIDDEN_CONSTRUCTS = {
        "dict", "set", "tuple",
        "try", "raise", "with", "async", "await",
        "lambda", "yield", "global", "nonlocal",
    }

    def __init__(self):
        self.registry = TypeRegistry()
        self.source_lines: list[str] = []
        self._type_param_scope: dict[str, TypeParamKind] | None = None  # Current type parameter scope for generic classes
        self._warnings: list[ParseWarning] = []  # Warnings accumulated during parsing
        self._tpy_import_aliases: dict[str, str] = {}  # local_name -> original_name for tpy imports
        self._tpy_star_import: bool = False  # True if "from tpy import *" was used

    def _loc(self, node: ast.AST) -> SourceLocation | None:
        """Create a SourceLocation from an AST node."""
        if hasattr(node, 'lineno'):
            col = getattr(node, 'col_offset', 0)
            return SourceLocation(line=node.lineno, column=col)
        return None

    def _warn(self, message: str, node: ast.AST | None = None) -> None:
        """Record a parser warning."""
        loc = self._loc(node) if node else None
        self._warnings.append(ParseWarning(message, loc))

    def _is_ignorable_for_import_order(self, node: ast.stmt) -> bool:
        """Check if a statement should be ignored for import ordering.

        Module docstrings and pass statements don't count as "code"
        for the purpose of detecting late imports.
        """
        if isinstance(node, ast.Pass):
            return True
        # Module docstring (expression statement with a string literal)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return True
        return False

    def parse(self, source: str) -> TpyModule:
        """Parse TurboPython source code into a TpyModule."""
        self.source_lines = source.splitlines()
        self._warnings = []  # Reset warnings for each parse
        self._tpy_import_aliases = {}  # Reset tpy import aliases
        self._tpy_star_import = False  # Reset star import flag
        tree = ast.parse(source)
        return self._parse_module(tree)

    def _parse_module(self, tree: ast.Module) -> TpyModule:
        """Parse a module."""
        records = []
        functions = []
        protocols = []
        top_level_stmts = []
        imports: dict[str, set[tuple[str, str]] | None | str] = {}
        user_module_imports: dict[str, int] = {}
        module_aliases: dict[str, str] = {}
        seen_non_import = False

        for node in tree.body:
            is_import = isinstance(node, (ast.Import, ast.ImportFrom))

            # Check for late imports (imports after non-import code)
            if is_import and seen_non_import:
                self._warn("Import statement should be at the top of the file", node)

            if isinstance(node, ast.ImportFrom):
                self._check_import_from(node, imports, user_module_imports, top_level_stmts, module_aliases)
            elif isinstance(node, ast.Import):
                self._check_import(node, imports, user_module_imports, top_level_stmts, module_aliases)
            elif isinstance(node, ast.ClassDef):
                seen_non_import = True
                result = self._parse_class(node)
                if isinstance(result, TpyProtocol):
                    protocols.append(result)
                    # Register the protocol type
                    self.registry.register_protocol(ProtocolInfo(
                        name=result.name,
                        methods=result.methods,
                        type_params=result.type_params
                    ))
                else:
                    records.append(result)
                    # Register the record type
                    self.registry.register_record(RecordInfo(
                        name=result.name,
                        fields=result.fields,
                        has_init=result.init_method is not None
                    ))
            elif isinstance(node, ast.FunctionDef):
                seen_non_import = True
                func = self._parse_function(node)
                functions.append(func)
            else:
                # Skip docstrings and pass statements for late import detection
                if not self._is_ignorable_for_import_order(node):
                    seen_non_import = True
                # All other statements go through _parse_stmt (same as function bodies)
                top_level_stmts.append(self._parse_stmt(node))

        return TpyModule(records=records, functions=functions, protocols=protocols, top_level_stmts=top_level_stmts, source_lines=self.source_lines, imports=imports, user_module_imports=user_module_imports, module_aliases=module_aliases, parse_warnings=self._warnings)

    def _check_import(self, node: ast.Import, imports: dict[str, set[tuple[str, str]] | None | str], user_module_imports: dict[str, int], top_level_stmts: list[TpyStmt], module_aliases: dict[str, str]) -> None:
        """Check and track 'import X' or 'import X as Y' statement."""
        for alias in node.names:
            module_name = alias.name
            local_name = alias.asname or module_name
            # Check if it's a user module (not builtin)
            if module_name not in SPECIAL_MODULES:
                # User module import - track line number and add to statements
                user_module_imports[module_name] = node.lineno
                imports[module_name] = None
                # Track alias if different from module name
                if local_name != module_name:
                    module_aliases[module_name] = local_name
                # Add TpyImport statement (only first time we see this module)
                if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                    import_alias = local_name if local_name != module_name else None
                    top_level_stmts.append(TpyImport(module_name=module_name, alias=import_alias, loc=SourceLocation(node.lineno, node.col_offset)))
                continue
            # 'import X' or 'import X as Y' -> module_name: None (whole module imported)
            imports[module_name] = None
            if local_name != module_name:
                module_aliases[module_name] = local_name

    def _check_import_from(self, node: ast.ImportFrom, imports: dict[str, set[tuple[str, str]] | None | str], user_module_imports: dict[str, int], top_level_stmts: list[TpyStmt], module_aliases: dict[str, str]) -> None:
        """Check and track 'from X import Y' statement."""
        module_name = node.module
        level = node.level

        # Handle relative imports (level > 0)
        if level > 0:
            # Create placeholder name that will be resolved during discovery
            # Format: __rel__{level}__{lineno}_{col}__{partial_name}
            # Include line:col to keep each import statement unique (handles same-line imports)
            partial = module_name or ""
            placeholder = f"__rel__{level}__{node.lineno}_{node.col_offset}__{partial}"

            # Track as user module import
            user_module_imports[placeholder] = node.lineno

            imports[placeholder] = set()
            for alias in node.names:
                if alias.name == "*":
                    raise ParseError("'from ... import *' not supported for relative imports", node)
                local_name = alias.asname or alias.name
                imports[placeholder].add((alias.name, local_name))

            # Each relative import statement gets its own TpyImport
            top_level_stmts.append(TpyImport(
                module_name=placeholder,
                level=level,
                relative_name=module_name,
                loc=SourceLocation(node.lineno, node.col_offset)
            ))
            return

        if module_name is None:
            raise ParseError("Invalid import: no module name", node)

        # Check if it's a user module (not builtin)
        if module_name not in SPECIAL_MODULES:
            # User module import: from utils import add, Point - track line number
            user_module_imports[module_name] = node.lineno
            if module_name not in imports:
                imports[module_name] = set()
            current = imports[module_name]
            if current is not None and current != "*":
                for alias in node.names:
                    if alias.name == "*":
                        raise ParseError(f"'from {module_name} import *' not supported for user modules", node)
                    local_name = alias.asname if alias.asname else alias.name
                    current.add((alias.name, local_name))
            # Add TpyImport statement (only first time we see this module)
            if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                top_level_stmts.append(TpyImport(module_name=module_name, loc=SourceLocation(node.lineno, node.col_offset)))
            return
        # Skip __future__ imports - they affect CPython parsing but are no-op for TurboPython
        if module_name == "__future__":
            return
        # Track tpy imports like other modules - sema will determine if they're
        # types (Int32) or functions (copy) and handle accordingly
        # Store as (original_name, local_name) tuples to support aliases
        if module_name == "tpy":
            # Handle "from tpy import *" specially
            if any(alias.name == "*" for alias in node.names):
                imports["tpy"] = "*"
                self._tpy_star_import = True
                return
            if "tpy" not in imports:
                imports["tpy"] = set()
            current = imports["tpy"]
            if current is not None and current != "*":
                for alias in node.names:
                    original_name = alias.name
                    local_name = alias.asname if alias.asname else alias.name
                    current.add((original_name, local_name))
                    # Track alias for type annotation resolution
                    self._tpy_import_aliases[local_name] = original_name
            return
        # 'from X import Y, Z' -> module_name: {(original, local), ...}
        # Store as (original_name, local_name) tuples to support aliases
        if module_name not in imports:
            imports[module_name] = set()
        current = imports[module_name]
        if current is not None and current != "*":  # Not overridden by 'import X' or '*'
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                current.add((alias.name, local_name))

    def _parse_class(self, node: ast.ClassDef) -> TpyRecord | TpyProtocol:
        """Parse a class definition as a record or protocol."""
        # Check if this is a Protocol definition (has Protocol as one of its bases)
        if node.bases:
            has_protocol = any(
                isinstance(base, ast.Name) and base.id == "Protocol"
                for base in node.bases
            )
            if has_protocol:
                return self._parse_protocol(node)

        if node.decorator_list:
            raise ParseError(f"Decorators not allowed on class '{node.name}'", node)

        # Extract type parameters FIRST so they're in scope when parsing bases
        # Python 3.12+ syntax: class Foo[T, U]:
        # Also extract bounds: class Foo[T: Comparable]: or class Foo[N: int]:
        type_params = []
        type_param_kinds: list[TypeParamKind] = []
        type_param_bounds: dict[str, TpyType] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                    if tp.bound is not None:
                        # Check for N: int syntax (integer type parameter)
                        if isinstance(tp.bound, ast.Name) and tp.bound.id == 'int':
                            type_param_kinds.append(TypeParamKind.INT)
                        else:
                            # Regular protocol bound
                            type_param_kinds.append(TypeParamKind.TYPE)
                            bound_type = self._parse_type_annotation(tp.bound)
                            if not isinstance(bound_type, NamedType) or not bound_type.is_protocol:
                                raise ParseError(f"Type parameter bound must be a protocol or 'int', got {bound_type}", tp)
                            type_param_bounds[tp.name] = bound_type
                    else:
                        type_param_kinds.append(TypeParamKind.TYPE)
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Create a dict of type param names to kinds for scope during parsing
        type_param_scope = dict(zip(type_params, type_param_kinds)) if type_params else None
        # Store scope for use during method body parsing (expression parsing uses this)
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

        # Parse base classes/protocols for inheritance (with type params in scope)
        # Classification into parent class vs protocol is deferred to sema
        bases: list[TpyType] = []
        for base in node.bases:
            base_type = self._parse_type_annotation(base, type_param_scope)
            bases.append(base_type)

        fields = []
        methods = []

        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                # Field declaration: name: Type = default
                if not isinstance(item.target, ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.target.id
                field_type = self._parse_type_annotation(item.annotation, type_param_scope)
                default_val = None
                if item.value is not None:
                    default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val, loc=self._loc(item)))
            elif isinstance(item, ast.Assign):
                # Field with inferred type: name = Int32(0)
                if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.targets[0].id
                field_type = self._infer_type_from_expr(item.value)
                if field_type is None:
                    raise ParseError(f"Cannot infer type for field '{field_name}'", item)
                default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val, loc=self._loc(item)))
            elif isinstance(item, ast.FunctionDef):
                methods.append(self._parse_method(item, node.name, type_param_scope))
            elif isinstance(item, ast.Pass):
                pass
            else:
                raise ParseError(f"Unsupported construct in class '{node.name}'", item)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyRecord(name=node.name, fields=fields, methods=methods, type_params=type_params, type_param_kinds=type_param_kinds, type_param_bounds=type_param_bounds, bases=bases, loc=self._loc(node))

    def _parse_protocol(self, node: ast.ClassDef) -> TpyProtocol:
        """Parse a protocol definition."""
        if node.decorator_list:
            raise ParseError(f"Decorators not allowed on protocol '{node.name}'", node)

        # Extract parent protocols (excluding Protocol itself)
        parent_protocols = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                if base.id != "Protocol":
                    parent_protocols.append(base.id)
            elif isinstance(base, ast.Subscript):
                # Generic parent protocols like Parent[T] are not yet supported
                if isinstance(base.value, ast.Name) and base.value.id != "Protocol":
                    raise ParseError(
                        f"Generic parent protocols are not yet supported: {base.value.id}[...]. "
                        f"Use non-generic parent protocols instead.",
                        base
                    )

        # Extract type parameters from Python 3.12+ syntax: class Foo[T](Protocol):
        # Note: Protocols don't support INT type params (only TYPE)
        type_params = []
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                else:
                    raise ParseError(f"Only simple type parameters supported in protocols, got {type(tp).__name__}", node)

        # Set type param scope for parsing method signatures (all TYPE kind for protocols)
        old_scope = self._type_param_scope
        self._type_param_scope = {tp: TypeParamKind.TYPE for tp in type_params} if type_params else None

        methods = []
        fields = []

        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                # Parse method signature (body should be ... or pass)
                params = []
                for i, arg in enumerate(item.args.args):
                    if i == 0:
                        if arg.arg != "self":
                            raise ParseError(f"First parameter of protocol method '{item.name}' must be 'self'", item)
                        continue
                    if arg.annotation is None:
                        raise ParseError(f"Protocol method parameter '{arg.arg}' must have type annotation", item)
                    param_type = self._parse_type_annotation(arg.annotation)
                    params.append((arg.arg, param_type))

                return_type = VOID
                if item.returns:
                    return_type = self._parse_type_annotation(item.returns)

                methods.append(MethodSignature(
                    name=item.name,
                    params=params,
                    return_type=return_type
                ))
            elif isinstance(item, ast.AnnAssign):
                # Field declaration: name: Type
                if not isinstance(item.target, ast.Name):
                    raise ParseError("Invalid field declaration in protocol", item)
                field_name = item.target.id
                field_type = self._parse_type_annotation(item.annotation)
                fields.append((field_name, field_type))
            elif isinstance(item, ast.Pass):
                pass
            elif isinstance(item, ast.Expr):
                # Allow docstrings (string literals) and ... (Ellipsis)
                if isinstance(item.value, ast.Constant):
                    pass  # Docstring
                elif isinstance(item.value, ast.Ellipsis):
                    pass  # Ellipsis at class level
                else:
                    raise ParseError(f"Unexpected expression in protocol '{node.name}'", item)
            else:
                raise ParseError(f"Unsupported construct in protocol '{node.name}': {type(item).__name__}", item)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyProtocol(name=node.name, methods=methods, fields=fields, type_params=type_params, parent_protocols=parent_protocols, loc=self._loc(node))

    def _parse_method(self, node: ast.FunctionDef, class_name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyFunction:
        """Parse a method definition."""
        # Check for @staticmethod decorator
        is_staticmethod = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "staticmethod":
                is_staticmethod = True
            else:
                dec_name = dec.id if isinstance(dec, ast.Name) else type(dec).__name__
                raise ParseError(f"Unknown decorator '{dec_name}' on method '{node.name}'", dec)

        params = []
        args_iter = iter(enumerate(node.args.args))
        for i, arg in args_iter:
            if i == 0 and not is_staticmethod:
                # Non-static methods must have 'self' as first parameter
                if arg.arg != "self":
                    raise ParseError(f"First parameter of method '{node.name}' must be 'self'", node)
                continue
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_annotation(arg.annotation, type_param_scope)
            params.append((arg.arg, param_type))

        # Get return type (default to Void for __init__)
        return_type = VOID
        if node.name != "__init__" and node.returns:
            return_type = self._parse_type_annotation(node.returns, type_param_scope)

        body = [self._parse_stmt(stmt) for stmt in node.body]
        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_method=True,
            is_staticmethod=is_staticmethod,
            loc=self._loc(node)
        )

    def _parse_function(self, node: ast.FunctionDef) -> TpyFunction:
        """Parse a function definition."""
        is_noalloc = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "noalloc":
                is_noalloc = True
            else:
                raise ParseError(f"Unknown decorator on function '{node.name}'", dec)

        # Extract type parameters from Python 3.12+ syntax: def foo[T, U]():
        # Also extract bounds: def foo[T: Comparable]():
        # Note: Functions don't currently support INT type params (only TYPE)
        type_params = []
        type_param_bounds: dict[str, TpyType] = {}
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                    if tp.bound is not None:
                        bound_type = self._parse_type_annotation(tp.bound)
                        if not isinstance(bound_type, NamedType) or not bound_type.is_protocol:
                            raise ParseError(f"Type parameter bound must be a protocol, got {bound_type}", tp)
                        type_param_bounds[tp.name] = bound_type
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Set scope for parsing parameter and return types (all TYPE kind for functions)
        type_param_scope = {tp: TypeParamKind.TYPE for tp in type_params} if type_params else None
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

        params = []
        for arg in node.args.args:
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_annotation(arg.annotation, type_param_scope)
            params.append((arg.arg, param_type))

        return_type = VOID
        if node.returns:
            return_type = self._parse_type_annotation(node.returns, type_param_scope)

        body = [self._parse_stmt(stmt) for stmt in node.body]

        # Restore the scope
        self._type_param_scope = old_scope

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_noalloc=is_noalloc,
            type_params=type_params,
            type_param_bounds=type_param_bounds,
            loc=self._loc(node)
        )

    def _parse_type_annotation(self, node: ast.expr, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyType:
        """Parse a type annotation.

        Args:
            node: The AST node representing the type annotation.
            type_param_scope: Dict of type parameter names to their kinds currently in scope.
                              Falls back to self._type_param_scope if not provided.
        """
        # Use instance variable as fallback for type parameter scope
        if type_param_scope is None:
            type_param_scope = self._type_param_scope
        if isinstance(node, ast.Name):
            name = node.id
            # Check if this is a type parameter reference
            if type_param_scope and name in type_param_scope:
                kind = type_param_scope[name]
                return TypeParamRef(name, kind=kind)
            # Resolve tpy import aliases (e.g., "from tpy import Int32 as I" allows using "I")
            resolved_name = self._tpy_import_aliases.get(name, name)
            # Check if tpy type was explicitly imported
            check_tpy_type_imported(name, resolved_name, node, self._tpy_star_import, self._tpy_import_aliases)
            if resolved_name == "Self":
                return SELF
            elif resolved_name == "Int32":
                return INT32
            elif resolved_name == "int":
                return BIGINT
            elif resolved_name == "float":
                return FLOAT
            elif resolved_name == "Bool":
                return BOOL
            elif resolved_name == "None":
                return VOID
            elif resolved_name == "str":
                return STR
            elif resolved_name == "Char":
                return CHAR
            elif (user_protocol := self.registry.get_protocol(resolved_name)) is not None:
                # User-defined protocol type
                # Check if generic protocol requires type arguments
                if user_protocol.type_params:
                    raise ParseError(
                        f"Generic protocol '{resolved_name}' requires type arguments: "
                        f"{resolved_name}[{', '.join(user_protocol.type_params)}]",
                        node
                    )
                return NamedType(resolved_name, is_protocol=True)
            elif (protocol_def := lookup_builtin_protocol(resolved_name)) is not None:
                # Built-in protocol type (e.g., Sized)
                # Check if generic protocol requires type arguments
                if protocol_def.type_params:
                    raise ParseError(
                        f"Generic protocol '{resolved_name}' requires type arguments: "
                        f"{resolved_name}[{', '.join(protocol_def.type_params)}]",
                        node
                    )
                return NamedType(resolved_name, is_protocol=True)
            elif self.registry.is_known_type(resolved_name) or resolved_name[0].isupper():
                # Assume it's a record type (will be validated later)
                return NamedType(resolved_name)
            else:
                raise ParseError(f"Unknown type: {name}", node)

        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                container = node.value.id
                # Resolve tpy import aliases for container names
                resolved_container = self._tpy_import_aliases.get(container, container)
                # Check if tpy type was explicitly imported
                check_tpy_type_imported(container, resolved_container, node, self._tpy_star_import, self._tpy_import_aliases)
                # Pointer types are fundamental, not module-defined
                if resolved_container == "Ptr":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return PtrType(inner)
                elif resolved_container == "ConstPtr":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return ConstPtrType(inner)
                elif resolved_container == "Own":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return OwnType(inner)

                # Generic protocols (e.g., Sequence[Int32])
                if protocol_def := lookup_builtin_protocol(resolved_container):
                    if protocol_def.type_params:
                        type_args = self._parse_protocol_type_args(node, resolved_container, protocol_def.type_params, type_param_scope)
                        return NamedType(resolved_container, type_args, is_protocol=True)

                # Module-defined generic types (list, Array, Span, etc.)
                if lookup := lookup_generic_type(resolved_container):
                    return self._parse_generic_type(node, resolved_container, lookup.type_def, type_param_scope)

                # User-defined generic protocols (e.g., Container[Int32])
                if user_protocol := self.registry.get_protocol(resolved_container):
                    if user_protocol.type_params:
                        type_args = self._parse_protocol_type_args(node, resolved_container, user_protocol.type_params, type_param_scope)
                        return NamedType(resolved_container, type_args, is_protocol=True)

                # User-defined generic records (e.g., Stack[Int32])
                # Check if it's a known record or looks like a record name (capitalized)
                if self.registry.get_record(resolved_container) is not None or resolved_container[0].isupper():
                    type_args = self._parse_record_type_args(node, resolved_container, type_param_scope)
                    return NamedType(resolved_container, type_args)

                raise ParseError(f"Unknown generic type: {container}", node)

        elif isinstance(node, ast.Constant) and node.value is None:
            return VOID

        raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

    def _parse_protocol_type_args(self, node: ast.Subscript, name: str,
                                    type_params: list[str], type_param_scope: dict[str, TypeParamKind] | None = None) -> tuple[TpyType, ...]:
        """Parse type arguments for a generic protocol like Sequence[Int32]."""
        expected_count = len(type_params)

        # Extract slice elements
        if expected_count == 1:
            slices = [node.slice]
        elif isinstance(node.slice, ast.Tuple):
            slices = node.slice.elts
        else:
            raise ParseError(f"{name} requires {expected_count} type parameters", node)

        if len(slices) != expected_count:
            raise ParseError(f"{name} requires exactly {expected_count} type parameters", node)

        # Parse each type argument
        type_args = tuple(self._parse_type_annotation(s, type_param_scope) for s in slices)
        return type_args

    def _parse_record_type_args(self, node: ast.Subscript, name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> tuple[TpyType | int, ...]:
        """Parse type arguments for a user-defined generic record like Stack[Int32] or Matrix[Int32, 8].

        For records with integer type parameters, integer literals are allowed in type argument positions.
        The validation of which positions accept integers is done in sema (since the record info
        may not be registered yet during parsing).
        """
        # Extract slice elements
        if isinstance(node.slice, ast.Tuple):
            slices = node.slice.elts
        else:
            slices = [node.slice]

        # Parse each type argument (allowing integer literals)
        type_args: list[TpyType | int] = []
        for s in slices:
            # Check for integer literals
            if isinstance(s, ast.Constant) and isinstance(s.value, int):
                type_args.append(s.value)
            # Check for type parameter references that are INT kind (forward as TypeParamRef)
            elif isinstance(s, ast.Name) and type_param_scope and s.id in type_param_scope:
                kind = type_param_scope[s.id]
                type_args.append(TypeParamRef(s.id, kind=kind))
            else:
                type_args.append(self._parse_type_annotation(s, type_param_scope))
        return tuple(type_args)

    def _parse_type_args_from_subscript(self, node: ast.Subscript) -> tuple[TpyType, ...]:
        """Extract type arguments from a subscript for generic function calls like first[Int32](x).

        Raises ParseError if any element is not a valid type. The caller should catch
        this for cases where non-type arguments are valid (e.g., StaticList[Int32, 8]).
        """
        # Extract slice elements
        if isinstance(node.slice, ast.Tuple):
            slices = node.slice.elts
        else:
            slices = [node.slice]

        # Parse each type argument - raise error if any fails
        type_args = []
        for s in slices:
            # Integer constants are not valid type arguments
            if isinstance(s, ast.Constant) and isinstance(s.value, int):
                raise ParseError(f"Integer '{s.value}' is not a valid type argument", s)
            # Variable names that aren't types
            if isinstance(s, ast.Name) and not self.registry.is_known_type(s.id) and s.id[0].islower():
                raise ParseError(f"'{s.id}' is not a valid type", s)
            type_args.append(self._parse_type_annotation(s))
        return tuple(type_args)

    def _parse_generic_type(self, node: ast.Subscript, name: str, type_def: BuiltinTypeDef, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyType:
        """Parse a module-defined generic type using its metadata."""
        param_kinds = type_def.param_kinds
        expected_count = len(param_kinds)

        # Extract slice elements
        if expected_count == 1:
            slices = [node.slice]
        elif isinstance(node.slice, ast.Tuple):
            slices = node.slice.elts
        else:
            raise ParseError(f"{name} requires {expected_count} type parameters", node)

        if len(slices) != expected_count:
            raise ParseError(f"{name} requires exactly {expected_count} type parameters", node)

        # Parse each parameter according to its kind
        parsed_args: list[TpyType | int] = []
        for i, (slice_node, kind) in enumerate(zip(slices, param_kinds)):
            if kind == TypeParamKind.TYPE:
                parsed_args.append(self._parse_type_annotation(slice_node, type_param_scope))
            elif kind == TypeParamKind.INT:
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
                    parsed_args.append(slice_node.value)
                elif isinstance(slice_node, ast.Name) and type_param_scope and slice_node.id in type_param_scope:
                    # Allow forwarded INT type params (e.g., Array[T, N] where N: int)
                    param_name = slice_node.id
                    param_kind = type_param_scope[param_name]
                    if param_kind == TypeParamKind.INT:
                        parsed_args.append(TypeParamRef(param_name, kind=TypeParamKind.INT))
                    else:
                        raise ParseError(f"{name} parameter {i + 1} requires an integer, got type parameter '{param_name}'", node)
                else:
                    raise ParseError(f"{name} parameter {i + 1} must be an integer literal or int type parameter", node)

        assert type_def.type_factory is not None
        try:
            return type_def.type_factory(*parsed_args)
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"Failed to construct type {name}: {e}", node) from e

    def _parse_stmt(self, node: ast.stmt) -> TpyStmt:
        """Parse a statement."""
        loc = self._loc(node)

        if isinstance(node, ast.AnnAssign):
            # Annotated assignment: x: T = expr
            if not isinstance(node.target, ast.Name):
                raise ParseError("Invalid assignment target", node)
            var_type = self._parse_type_annotation(node.annotation)
            init_expr = self._parse_expr(node.value) if node.value else None
            return TpyVarDecl(node.target.id, var_type, init_expr, loc=loc)

        elif isinstance(node, ast.Assign):
            # Simple assignment: x = expr or x.field = expr
            if len(node.targets) != 1:
                raise ParseError("Multiple assignment targets not supported", node)
            target = self._parse_expr(node.targets[0])
            value = self._parse_expr(node.value)
            # Check if this is a variable declaration (unannotated)
            if isinstance(target, TpyName):
                return TpyVarDecl(target.name, None, value, loc=loc)
            return TpyAssign(target, value, loc=loc)

        elif isinstance(node, ast.AugAssign):
            # Augmented assignment: x += expr
            target = self._parse_expr(node.target)
            value = self._parse_expr(node.value)
            op = self._binop_to_str(node.op)
            return TpyAugAssign(target, op, value, loc=loc)

        elif isinstance(node, ast.Expr):
            return TpyExprStmt(self._parse_expr(node.value), loc=loc)

        elif isinstance(node, ast.Return):
            value = self._parse_expr(node.value) if node.value else None
            return TpyReturn(value, loc=loc)

        elif isinstance(node, ast.If):
            cond = self._parse_expr(node.test)
            then_body = [self._parse_stmt(s) for s in node.body]
            else_body = [self._parse_stmt(s) for s in node.orelse]
            return TpyIf(cond, then_body, else_body, loc=loc)

        elif isinstance(node, ast.While):
            cond = self._parse_expr(node.test)
            body = [self._parse_stmt(s) for s in node.body]
            return TpyWhile(cond, body, loc=loc)

        elif isinstance(node, ast.For):
            if not isinstance(node.target, ast.Name):
                raise ParseError("For loop target must be a simple variable", node)
            var = node.target.id
            body = [self._parse_stmt(s) for s in node.body]

            # Check if it's a range-based for loop
            if (isinstance(node.iter, ast.Call) and
                    isinstance(node.iter.func, ast.Name) and
                    node.iter.func.id == "range"):
                args = node.iter.args
                if len(args) == 1:
                    start = TpyIntLiteral(0)
                    end = self._parse_expr(args[0])
                elif len(args) == 2:
                    start = self._parse_expr(args[0])
                    end = self._parse_expr(args[1])
                else:
                    raise ParseError("range() must have 1 or 2 arguments", node)
                return TpyFor(var, start, end, body, loc=loc)

            # Otherwise it's a for-each loop over a collection
            iterable = self._parse_expr(node.iter)
            return TpyForEach(var, iterable, body, loc=loc)

        elif isinstance(node, ast.Pass):
            return TpyPassStmt(loc=loc)

        elif isinstance(node, ast.Break):
            return TpyBreak(loc=loc)

        elif isinstance(node, ast.Continue):
            return TpyContinue(loc=loc)

        else:
            raise ParseError(f"Unsupported statement: {type(node).__name__}", node)

    def _parse_expr(self, node: ast.expr) -> TpyExpr:
        """Parse an expression."""
        loc = self._loc(node)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return TpyBoolLiteral(node.value, loc=loc)
            elif isinstance(node.value, int):
                return TpyIntLiteral(node.value, loc=loc)
            elif isinstance(node.value, float):
                return TpyFloatLiteral(node.value, loc=loc)
            elif isinstance(node.value, str):
                return TpyStrLiteral(node.value, loc=loc)
            else:
                raise ParseError(f"Unsupported literal type: {type(node.value).__name__}", node)

        elif isinstance(node, ast.Name):
            if node.id in self.FORBIDDEN_CONSTRUCTS:
                raise ParseError(f"'{node.id}' is not allowed in TurboPython", node)
            return TpyName(node.id, loc=loc)

        elif isinstance(node, ast.BinOp):
            # Handle list repetition: [x, y, ...] * N -> TpyListRepeat
            if isinstance(node.op, ast.Mult) and isinstance(node.left, ast.List):
                elements = [self._parse_expr(e) for e in node.left.elts]
                # Empty list: [] * N -> [] (collapse to empty array literal)
                if not elements:
                    return TpyArrayLiteral([], loc=loc)
                count = self._parse_expr(node.right)
                return TpyListRepeat(elements, count, loc=loc)
            left = self._parse_expr(node.left)
            right = self._parse_expr(node.right)
            op = self._binop_to_str(node.op)
            return TpyBinOp(left, op, right, loc=loc)

        elif isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise ParseError("Chained comparisons not supported", node)
            left = self._parse_expr(node.left)
            right = self._parse_expr(node.comparators[0])
            op = self._cmpop_to_str(node.ops[0])
            return TpyBinOp(left, op, right, loc=loc)

        elif isinstance(node, ast.UnaryOp):
            operand = self._parse_expr(node.operand)
            op = self._unaryop_to_str(node.op)
            return TpyUnaryOp(op, operand, loc=loc)

        elif isinstance(node, ast.BoolOp):
            # Handle 'and' / 'or' - chain as binary ops
            op = "&&" if isinstance(node.op, ast.And) else "||"
            result = self._parse_expr(node.values[0])
            for val in node.values[1:]:
                result = TpyBinOp(result, op, self._parse_expr(val), loc=loc)
            return result

        elif isinstance(node, ast.Call):
            args = [self._parse_expr(a) for a in node.args]
            kwargs = {}

            # Handle keyword arguments (limited support for print)
            if node.keywords:
                func_name = node.func.id if isinstance(node.func, ast.Name) else None
                if func_name == "print":
                    for kw in node.keywords:
                        if kw.arg == "end":
                            kwargs["end"] = self._parse_expr(kw.value)
                        else:
                            raise ParseError(f"print() does not support keyword argument '{kw.arg}'", node)
                else:
                    raise ParseError("Keyword arguments not supported", node)

            if isinstance(node.func, ast.Name):
                return TpyCall(node.func.id, args, kwargs=kwargs, loc=loc)
            elif isinstance(node.func, ast.Attribute):
                obj = self._parse_expr(node.func.value)
                return TpyMethodCall(obj, node.func.attr, args, loc=loc)
            elif isinstance(node.func, ast.Subscript):
                # Could be generic type instantiation (Stack[Int32]()) or generic function call (First[Int32](x))
                # Parse both call_type and type_args - sema decides which applies based on whether
                # the name is a record or a function
                if isinstance(node.func.value, ast.Name):
                    name = node.func.value.id
                    # Try to extract type_args for potential generic function call
                    type_args = ()
                    type_args_parse_error = None
                    try:
                        type_args = self._parse_type_args_from_subscript(node.func)
                    except ParseError as e:
                        # Store error - sema will report it if this turns out to be a function call
                        # (For type instantiations like StaticList[Int32, 8], non-type args are valid)
                        type_args_parse_error = e.message
                    # If name looks like a type (starts with uppercase or is registered), also parse as call_type
                    call_type = None
                    if name[0].isupper() or self.registry.is_known_type(name):
                        try:
                            call_type = self._parse_type_annotation(node.func)
                        except ParseError:
                            # call_type parsing failed - if type_args also failed, sema will report
                            # the type_args_parse_error; otherwise it's a function call
                            pass
                    return TpyCall(name, args, call_type=call_type, type_args=type_args,
                                   type_args_parse_error=type_args_parse_error, loc=loc)
                raise ParseError("Unsupported generic call target", node)
            else:
                raise ParseError("Unsupported call target", node)

        elif isinstance(node, ast.Attribute):
            obj = self._parse_expr(node.value)
            return TpyFieldAccess(obj, node.attr, loc=loc)

        elif isinstance(node, ast.List):
            elements = [self._parse_expr(elt) for elt in node.elts]
            return TpyArrayLiteral(elements=elements, loc=loc)

        elif isinstance(node, ast.Subscript):
            # Subscript can be indexing (values[i]) or type annotation (Array[T, N])
            # If the value is a name that's a known generic type, it's a type annotation context
            # Otherwise, it's indexing
            if isinstance(node.value, ast.Name):
                name = node.value.id
                # Fundamental generic types (not in module system)
                if name in ("Ptr", "ConstPtr", "Own"):
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
                # Module-defined generic types
                from tpyc.modules import lookup_generic_type
                if lookup_generic_type(name) is not None:
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
            obj = self._parse_expr(node.value)
            index = self._parse_expr(node.slice)
            return TpySubscript(obj=obj, index=index, loc=loc)

        else:
            raise ParseError(f"Unsupported expression: {type(node).__name__}", node)

    def _binop_to_str(self, op: ast.operator) -> str:
        """Convert binary operator to string."""
        return _BINOP_TO_STR.get(type(op), "?")

    def _cmpop_to_str(self, op: ast.cmpop) -> str:
        """Convert comparison operator to string."""
        return _CMPOP_TO_STR.get(type(op), "?")

    def _unaryop_to_str(self, op: ast.unaryop) -> str:
        """Convert unary operator to string."""
        return _UNARYOP_TO_STR.get(type(op), "?")

    def _get_default_value(self, node: ast.expr) -> str:
        """Get string representation of a default value for C++."""
        if isinstance(node, ast.Constant):
            val = node.value
            if isinstance(val, bool):
                return "true" if val else "false"
            elif isinstance(val, str):
                escaped = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                return f'"{escaped}"'
            return str(val)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # Int32(x) just becomes x in C++
            if node.func.id == "Int32":
                if not node.args:
                    return "0"
                return self._get_default_value(node.args[0])
            args = ", ".join(str(self._get_default_value(a)) for a in node.args)
            return f"{node.func.id}({args})"
        return "0"

    def _infer_type_from_expr(self, node: ast.expr) -> Optional[TpyType]:
        """Infer type from an expression (for field declarations without annotations)."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return BIGINT
            elif isinstance(node.value, float):
                return FLOAT
            elif isinstance(node.value, str):
                return STR
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            type_name = node.func.id
            if type_name == "Int32":
                return INT32
            if type_name == "int":
                return BIGINT
            if type_name == "float":
                return FLOAT
            # Check if it's a known record type
            record_info = self.registry.get_record(type_name)
            if record_info:
                return NamedType(type_name)
        return None
