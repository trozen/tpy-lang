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
    TpyType, RecordType, PtrType, ConstPtrType, OwnType, ProtocolType, SelfType, TypeParamRef,
    INT32, VOID, STR, CHAR, BOOL, FLOAT, BIGINT, SELF, FieldInfo, RecordInfo, TypeRegistry,
    MethodSignature, ProtocolInfo
)
from .modules import lookup_generic_type, lookup_protocol as lookup_builtin_protocol, TypeParamKind, BuiltinTypeDef


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


@dataclass
class TpyUnaryOp(TpyExpr):
    """Unary operation."""
    op: str  # '-', 'not'
    operand: TpyExpr


@dataclass
class TpyCall(TpyExpr):
    """Function or constructor call."""
    func: str
    args: list[TpyExpr]
    call_type: Optional[TpyType] = None  # For generic instantiation like MyContainer[T, N]()
    kwargs: dict[str, TpyExpr] = field(default_factory=dict)  # Keyword arguments (limited support)


@dataclass
class TpyMethodCall(TpyExpr):
    """Method call on an object."""
    obj: TpyExpr
    method: str
    args: list[TpyExpr]
    resolved_method: Any = None  # Set by sema for builtin method overload resolution


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
class TpyFunction:
    """Function definition."""
    name: str
    params: list[tuple[str, TpyType]]
    return_type: TpyType
    body: list[TpyStmt]
    is_noalloc: bool = False
    is_method: bool = False
    loc: SourceLocation | None = None


@dataclass
class TpyRecord:
    """Record (class) definition.

    For generic records like Stack[T]:
    - type_params stores the type parameter names (e.g., ["T"])
    """
    name: str
    fields: list[FieldInfo]
    methods: list[TpyFunction] = field(default_factory=list)
    type_params: list[str] = field(default_factory=list)

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
    loc: SourceLocation | None = None


@dataclass
class TpyModule:
    """Top-level module."""
    records: list[TpyRecord]
    functions: list[TpyFunction]
    protocols: list[TpyProtocol] = field(default_factory=list)
    top_level_stmts: list[TpyStmt] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)  # Original source lines for source mapping
    # Import tracking: module_name -> set of imported names (for "from X import Y")
    #                  module_name -> None (for "import X")
    imports: dict[str, set[str] | None] = field(default_factory=dict)


class Parser:
    """Parser for TurboPython source code."""

    FORBIDDEN_CONSTRUCTS = {
        "dict", "set", "tuple",
        "try", "raise", "with", "async", "await",
        "lambda", "yield", "global", "nonlocal",
    }

    ALLOWED_IMPORTS = {"tpy", "time", "sys", "math", "typing", "__future__"}

    def __init__(self):
        self.registry = TypeRegistry()
        self.source_lines: list[str] = []
        self._type_param_scope: set[str] | None = None  # Current type parameter scope for generic classes

    def _loc(self, node: ast.AST) -> SourceLocation | None:
        """Create a SourceLocation from an AST node."""
        if hasattr(node, 'lineno'):
            col = getattr(node, 'col_offset', 0)
            return SourceLocation(line=node.lineno, column=col)
        return None

    def parse(self, source: str) -> TpyModule:
        """Parse TurboPython source code into a TpyModule."""
        self.source_lines = source.splitlines()
        tree = ast.parse(source)
        return self._parse_module(tree)

    def _parse_module(self, tree: ast.Module) -> TpyModule:
        """Parse a module."""
        records = []
        functions = []
        protocols = []
        top_level_stmts = []
        imports: dict[str, set[str] | None] = {}

        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                self._check_import_from(node, imports)
            elif isinstance(node, ast.Import):
                self._check_import(node, imports)
            elif isinstance(node, ast.ClassDef):
                result = self._parse_class(node)
                if isinstance(result, TpyProtocol):
                    protocols.append(result)
                    # Register the protocol type
                    self.registry.register_protocol(ProtocolInfo(
                        name=result.name,
                        methods=result.methods
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
                func = self._parse_function(node)
                functions.append(func)
            else:
                # All other statements go through _parse_stmt (same as function bodies)
                top_level_stmts.append(self._parse_stmt(node))

        return TpyModule(records=records, functions=functions, protocols=protocols, top_level_stmts=top_level_stmts, source_lines=self.source_lines, imports=imports)

    def _check_import(self, node: ast.Import, imports: dict[str, set[str] | None]) -> None:
        """Check and track 'import X' statement."""
        for alias in node.names:
            module_name = alias.name
            if alias.asname is not None:
                raise ParseError(f"Import aliases not supported: 'import {module_name} as {alias.asname}'", node)
            if module_name not in self.ALLOWED_IMPORTS:
                raise ParseError(f"Import of '{module_name}' not allowed.", node)
            # Skip tpy - it's handled differently (type imports)
            if module_name == "tpy":
                continue
            # 'import X' -> module_name: None (whole module imported)
            imports[module_name] = None

    def _check_import_from(self, node: ast.ImportFrom, imports: dict[str, set[str] | None]) -> None:
        """Check and track 'from X import Y' statement."""
        if node.module not in self.ALLOWED_IMPORTS:
            raise ParseError(f"Import from '{node.module}' not allowed.", node)
        # Skip tpy - it's handled differently (type imports)
        if node.module == "tpy":
            return
        # Skip __future__ imports - they affect CPython parsing but are no-op for TurboPython
        if node.module == "__future__":
            return
        # 'from X import Y, Z' -> module_name: {Y, Z}
        module_name = node.module
        if module_name not in imports:
            imports[module_name] = set()
        current = imports[module_name]
        if current is not None:  # Not overridden by 'import X'
            for alias in node.names:
                if alias.asname is not None:
                    raise ParseError(f"Import aliases not supported: 'from {module_name} import {alias.name} as {alias.asname}'", node)
                current.add(alias.name)

    def _parse_class(self, node: ast.ClassDef) -> TpyRecord | TpyProtocol:
        """Parse a class definition as a record or protocol."""
        # Check if this is a Protocol definition
        if node.bases:
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "Protocol":
                    return self._parse_protocol(node)
            raise ParseError(f"Inheritance not allowed in class '{node.name}'", node)

        if node.decorator_list:
            raise ParseError(f"Decorators not allowed on class '{node.name}'", node)

        # Extract type parameters from Python 3.12+ syntax: class Foo[T, U]:
        type_params = []
        if hasattr(node, 'type_params') and node.type_params:
            for tp in node.type_params:
                if isinstance(tp, ast.TypeVar):
                    type_params.append(tp.name)
                else:
                    raise ParseError(f"Only simple type parameters supported, got {type(tp).__name__}", node)

        # Create a set of type param names for scope during parsing
        type_param_scope = set(type_params) if type_params else None
        # Store scope for use during method body parsing (expression parsing uses this)
        old_scope = self._type_param_scope
        self._type_param_scope = type_param_scope

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
                fields.append(FieldInfo(field_name, field_type, default_val))
            elif isinstance(item, ast.Assign):
                # Field with inferred type: name = Int32(0)
                if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.targets[0].id
                field_type = self._infer_type_from_expr(item.value)
                if field_type is None:
                    raise ParseError(f"Cannot infer type for field '{field_name}'", item)
                default_val = self._get_default_value(item.value)
                fields.append(FieldInfo(field_name, field_type, default_val))
            elif isinstance(item, ast.FunctionDef):
                methods.append(self._parse_method(item, node.name, type_param_scope))
            elif isinstance(item, ast.Pass):
                pass
            else:
                raise ParseError(f"Unsupported construct in class '{node.name}'", item)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyRecord(name=node.name, fields=fields, methods=methods, type_params=type_params)

    def _parse_protocol(self, node: ast.ClassDef) -> TpyProtocol:
        """Parse a protocol definition."""
        if node.decorator_list:
            raise ParseError(f"Decorators not allowed on protocol '{node.name}'", node)

        methods = []

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

        return TpyProtocol(name=node.name, methods=methods, loc=self._loc(node))

    def _parse_method(self, node: ast.FunctionDef, class_name: str, type_param_scope: set[str] | None = None) -> TpyFunction:
        """Parse a method definition."""
        params = []
        for i, arg in enumerate(node.args.args):
            if i == 0:
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

        params = []
        for arg in node.args.args:
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_annotation(arg.annotation)
            params.append((arg.arg, param_type))

        return_type = VOID
        if node.returns:
            return_type = self._parse_type_annotation(node.returns)

        body = [self._parse_stmt(stmt) for stmt in node.body]

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_noalloc=is_noalloc,
            loc=self._loc(node)
        )

    def _parse_type_annotation(self, node: ast.expr, type_param_scope: set[str] | None = None) -> TpyType:
        """Parse a type annotation.

        Args:
            node: The AST node representing the type annotation.
            type_param_scope: Set of type parameter names currently in scope (for generic classes).
                              Falls back to self._type_param_scope if not provided.
        """
        # Use instance variable as fallback for type parameter scope
        if type_param_scope is None:
            type_param_scope = self._type_param_scope
        if isinstance(node, ast.Name):
            name = node.id
            # Check if this is a type parameter reference
            if type_param_scope and name in type_param_scope:
                return TypeParamRef(name)
            if name == "Self":
                return SELF
            elif name == "Int32":
                return INT32
            elif name == "int":
                return BIGINT
            elif name == "float":
                return FLOAT
            elif name == "Bool":
                return BOOL
            elif name == "None":
                return VOID
            elif name == "str":
                return STR
            elif name == "Char":
                return CHAR
            elif self.registry.get_protocol(name) is not None:
                # User-defined protocol type
                return ProtocolType(name)
            elif (protocol_def := lookup_builtin_protocol(name)) is not None:
                # Built-in protocol type (e.g., Sized)
                # Check if generic protocol requires type arguments
                if protocol_def.type_params:
                    raise ParseError(
                        f"Generic protocol '{name}' requires type arguments: "
                        f"{name}[{', '.join(protocol_def.type_params)}]",
                        node
                    )
                return ProtocolType(name)
            elif self.registry.is_known_type(name) or name[0].isupper():
                # Assume it's a record type (will be validated later)
                return RecordType(name)
            else:
                raise ParseError(f"Unknown type: {name}", node)

        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                container = node.value.id
                # Pointer types are fundamental, not module-defined
                if container == "Ptr":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return PtrType(inner)
                elif container == "ConstPtr":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return ConstPtrType(inner)
                elif container == "Own":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return OwnType(inner)

                # Generic protocols (e.g., Sequence[Int32])
                if protocol_def := lookup_builtin_protocol(container):
                    if protocol_def.type_params:
                        type_args = self._parse_protocol_type_args(node, container, protocol_def.type_params, type_param_scope)
                        return ProtocolType(container, type_args)

                # Module-defined generic types (list, Array, Span, etc.)
                if lookup := lookup_generic_type(container):
                    return self._parse_generic_type(node, container, lookup.type_def, type_param_scope)

                # User-defined generic records (e.g., Stack[Int32])
                # Check if it's a known record or looks like a record name (capitalized)
                if self.registry.get_record(container) is not None or container[0].isupper():
                    type_args = self._parse_record_type_args(node, container, type_param_scope)
                    return RecordType(container, type_args)

                raise ParseError(f"Unknown generic type: {container}", node)

        elif isinstance(node, ast.Constant) and node.value is None:
            return VOID

        raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

    def _parse_protocol_type_args(self, node: ast.Subscript, name: str,
                                    type_params: list[str], type_param_scope: set[str] | None = None) -> tuple[TpyType, ...]:
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

    def _parse_record_type_args(self, node: ast.Subscript, name: str, type_param_scope: set[str] | None = None) -> tuple[TpyType, ...]:
        """Parse type arguments for a user-defined generic record like Stack[Int32]."""
        # Extract slice elements
        if isinstance(node.slice, ast.Tuple):
            slices = node.slice.elts
        else:
            slices = [node.slice]

        # Parse each type argument
        type_args = tuple(self._parse_type_annotation(s, type_param_scope) for s in slices)
        return type_args

    def _parse_generic_type(self, node: ast.Subscript, name: str, type_def: BuiltinTypeDef, type_param_scope: set[str] | None = None) -> TpyType:
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
                else:
                    raise ParseError(f"{name} parameter {i + 1} must be an integer literal", node)

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
                # Generic type instantiation: MyContainer[T, N]()
                call_type = self._parse_type_annotation(node.func)
                if isinstance(node.func.value, ast.Name):
                    return TpyCall(node.func.value.id, args, call_type, loc=loc)
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
        ops = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
            ast.Div: "div", ast.Mod: "%", ast.FloorDiv: "//",
            ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
            ast.LShift: "<<", ast.RShift: ">>",
            ast.Pow: "**",
        }
        return ops.get(type(op), "?")

    def _cmpop_to_str(self, op: ast.cmpop) -> str:
        """Convert comparison operator to string."""
        ops = {
            ast.Eq: "==", ast.NotEq: "!=",
            ast.Lt: "<", ast.LtE: "<=",
            ast.Gt: ">", ast.GtE: ">=",
            ast.In: "in", ast.NotIn: "not in",
        }
        return ops.get(type(op), "?")

    def _unaryop_to_str(self, op: ast.unaryop) -> str:
        """Convert unary operator to string."""
        ops = {ast.USub: "-", ast.Not: "!", ast.Invert: "~"}
        return ops.get(type(op), "?")

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
                return RecordType(type_name)
        return None
