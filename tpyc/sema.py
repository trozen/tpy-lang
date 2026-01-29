"""
TurboPython Semantic Analyzer

Performs type checking and semantic validation:
- Type inference and checking
- Record field validation
- @noalloc constraint enforcement
- Pointer semantics validation
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .typesys import (
    TpyType, Int32Type, VoidType, RecordType, PtrType, ConstPtrType, OwnType,
    StaticListType, ArrayType, SpanType, ListType, PendingListType, ListLiteralInfo,
    StrType, CharType, BoolType, BigIntType, IntLiteralType,
    INT32, VOID, STR, CHAR, BOOL, BIGINT, FieldInfo, RecordInfo, FunctionInfo, TypeRegistry
)
from .parse import (
    SourceLocation,
    TpyModule, TpyRecord, TpyFunction, TpyStmt, TpyExpr,
    TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn, TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue,
    TpyIntLiteral, TpyStrLiteral, TpyBoolLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce
)
from .coercions import resolve_coercion, Coercion
from tpyc import modules as builtin_modules


@dataclass
class Diagnostic:
    """A compiler diagnostic (error or warning)."""
    level: str  # "error" or "warning"
    message: str
    loc: SourceLocation | None = None

    def format(self, filename: str = "<unknown>") -> str:
        """Format diagnostic with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: {self.level}: {self.message}"
        return f"{filename}: {self.level}: {self.message}"


class SemanticError(Exception):
    """Error during semantic analysis."""
    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc
        super().__init__(message)

    def format(self, filename: str = "<unknown>") -> str:
        """Format error with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: error: {self.message}"
        return f"{filename}: error: {self.message}"


@dataclass
class Scope:
    """A scope containing variable bindings."""
    parent: Optional[Scope] = None
    bindings: dict[str, TpyType] = field(default_factory=dict)

    def lookup(self, name: str) -> Optional[TpyType]:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def define(self, name: str, typ: TpyType) -> None:
        self.bindings[name] = typ


class TypedExpr:
    """Expression annotated with its type."""
    def __init__(self, expr: TpyExpr, typ: TpyType):
        self.expr = expr
        self.type = typ


class SemanticAnalyzer:
    """Semantic analyzer for TurboPython."""

    def __init__(self):
        self.registry = TypeRegistry()
        self.global_scope: Scope = Scope()  # Module-level scope
        self.current_scope: Optional[Scope] = None
        self.current_function: Optional[TpyFunction] = None
        self.expr_types: dict[int, TpyType] = {}  # id(expr) -> type
        self.var_types: dict[int, TpyType] = {}  # id(TpyVarDecl) -> resolved type
        self.loop_depth: int = 0  # Track nesting depth of loops
        self.diagnostics: list[Diagnostic] = []  # Collected warnings (errors raise immediately)
        self.is_top_level: bool = False  # True when analyzing top-level statements

        # List literal inference tracking
        self.literal_counter: int = 0
        self.list_literals: dict[int, ListLiteralInfo] = {}  # literal_id -> info
        self.variable_to_literal: dict[str, int] = {}  # var_name -> literal_id
        self.pending_resolutions: list[int] = []  # literal_ids to resolve after function analysis
        self.var_decl_by_name: dict[str, TpyVarDecl] = {}  # var_name -> TpyVarDecl node (current scope)

    def _error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        loc = getattr(node, 'loc', None) if node else None
        return SemanticError(message, loc)

    def _warning(self, message: str, node: TpyExpr | TpyStmt | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        loc = getattr(node, 'loc', None) if node else None
        self.diagnostics.append(Diagnostic("warning", message, loc))

    def analyze(self, module: TpyModule) -> None:
        """Analyze a module for semantic correctness."""
        # First pass: register all records
        for record in module.records:
            self._register_record(record)

        # Second pass: register all functions
        for func in module.functions:
            self._register_function(func)

        # Third pass: register top-level variable declarations (globals)
        if module.top_level_stmts:
            self._register_globals(module.top_level_stmts)

        # Fourth pass: analyze record methods
        for record in module.records:
            self._analyze_record_methods(record)

        # Fifth pass: analyze function bodies
        for func in module.functions:
            self._analyze_function(func)

        # Sixth pass: analyze top-level statements
        if module.top_level_stmts:
            self._analyze_top_level(module.top_level_stmts)

    def _register_record(self, record: TpyRecord) -> None:
        """Register a record type."""
        # Validate field types
        for fld in record.fields:
            self._validate_type(fld.type)

        init_params = []
        if record.init_method:
            for pname, ptype in record.init_method.params:
                init_params.append((pname, ptype, None))

        # Register all methods
        methods = {}
        for method in record.methods:
            for pname, ptype in method.params:
                self._validate_type(ptype)
            self._validate_type(method.return_type)
            methods[method.name] = FunctionInfo(
                name=method.name,
                params=method.params,
                return_type=method.return_type,
                is_method=True
            )

        info = RecordInfo(
            name=record.name,
            fields=record.fields,
            has_init=record.init_method is not None,
            init_params=init_params,
            methods=methods
        )
        self.registry.register_record(info)

    def _register_function(self, func: TpyFunction) -> None:
        """Register a function."""
        for pname, ptype in func.params:
            self._validate_type(ptype)
        self._validate_type(func.return_type)

        info = FunctionInfo(
            name=func.name,
            params=func.params,
            return_type=func.return_type,
            is_noalloc=func.is_noalloc
        )
        self.registry.register_function(info)

    def _validate_type(self, typ: TpyType) -> None:
        """Validate that a type is well-formed."""
        if isinstance(typ, RecordType):
            if not self.registry.get_record(typ.name):
                # Allow forward references during registration
                pass
        elif isinstance(typ, (PtrType, ConstPtrType)):
            self._validate_type(typ.pointee)
        elif isinstance(typ, StaticListType):
            self._validate_type(typ.element_type)
        elif isinstance(typ, ArrayType):
            self._validate_type(typ.element_type)
        elif isinstance(typ, SpanType):
            self._validate_type(typ.element_type)
        elif isinstance(typ, ListType):
            self._validate_type(typ.element_type)

    def _register_globals(self, stmts: list[TpyStmt]) -> None:
        """Register top-level variable declarations in global scope.

        Only registers explicitly typed globals here. Untyped globals are
        fully analyzed in _analyze_top_level, which provides proper context
        for list literal type inference.
        """
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type:
                self.global_scope.define(stmt.name, stmt.type)

    def _analyze_record_methods(self, record: TpyRecord) -> None:
        """Analyze all methods of a record."""
        for method in record.methods:
            self._reset_function_tracking()
            self.current_function = method
            self.current_scope = Scope(parent=self.global_scope)
            # Add 'self' as the record type
            self.current_scope.define("self", RecordType(record.name))

            # Add parameters
            for pname, ptype in method.params:
                self.current_scope.define(pname, ptype)

            # Analyze body
            for stmt in method.body:
                self._analyze_stmt(stmt)

            # Resolve pending list types after analyzing the full method
            self._resolve_pending_list_types()

            self.current_scope = None
            self.current_function = None

    def _analyze_function(self, func: TpyFunction) -> None:
        """Analyze a function body."""
        self._reset_function_tracking()
        self.current_function = func
        self.current_scope = Scope(parent=self.global_scope)

        # Add parameters to scope
        for pname, ptype in func.params:
            self.current_scope.define(pname, ptype)

        # Analyze body
        for stmt in func.body:
            self._analyze_stmt(stmt)

        # Resolve pending list types after analyzing the full function
        self._resolve_pending_list_types()

        self.current_function = None
        self.current_scope = None

    def _analyze_top_level(self, stmts: list[TpyStmt]) -> None:
        """Analyze top-level statements (for generated main()).

        Treats top-level code like a function body so list literals and other
        constructs go through the same analysis path.
        """
        self._reset_function_tracking()
        # Use a sentinel to indicate we're in "module init" context (not None, but not a real function)
        self.current_function = True  # type: ignore
        self.current_scope = Scope(parent=self.global_scope)
        self.is_top_level = True

        for stmt in stmts:
            self._analyze_stmt(stmt)

        # Resolve pending list types (same as function analysis)
        self._resolve_pending_list_types()

        self.current_function = None
        self.current_scope = None
        self.is_top_level = False

    def _analyze_stmt(self, stmt: TpyStmt) -> None:
        """Analyze a statement."""
        if isinstance(stmt, TpyVarDecl):
            self._analyze_var_decl(stmt)
        elif isinstance(stmt, TpyAssign):
            self._analyze_assign(stmt)
        elif isinstance(stmt, TpyAugAssign):
            self._analyze_aug_assign(stmt)
        elif isinstance(stmt, TpyExprStmt):
            self._analyze_expr(stmt.expr)
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                ret_type = self._analyze_expr(stmt.value)
                expected = self.current_function.return_type if self.current_function else VOID
                stmt.value = self._coerce_expr(stmt.value, ret_type, expected, "return value",
                                               coercion_ctx="return", is_return=True)
                # Check for dangling reference (returning local/temporary as reference)
                self._check_dangling_reference(stmt.value, expected, stmt.loc)
        elif isinstance(stmt, TpyIf):
            self._analyze_expr(stmt.condition)
            for s in stmt.then_body:
                self._analyze_stmt(s)
            for s in stmt.else_body:
                self._analyze_stmt(s)
        elif isinstance(stmt, TpyWhile):
            self._analyze_expr(stmt.condition)
            self.loop_depth += 1
            for s in stmt.body:
                self._analyze_stmt(s)
            self.loop_depth -= 1
        elif isinstance(stmt, TpyFor):
            self._analyze_expr(stmt.start)
            self._analyze_expr(stmt.end)
            inner_scope = Scope(self.current_scope)
            inner_scope.define(stmt.var, INT32)
            old_scope = self.current_scope
            self.current_scope = inner_scope
            self.loop_depth += 1
            for s in stmt.body:
                self._analyze_stmt(s)
            self.loop_depth -= 1
            self.current_scope = old_scope
        elif isinstance(stmt, TpyForEach):
            iterable_type = self._analyze_expr(stmt.iterable)
            elem_type = self._get_iterable_element_type(iterable_type)
            inner_scope = Scope(self.current_scope)
            inner_scope.define(stmt.var, elem_type)
            old_scope = self.current_scope
            self.current_scope = inner_scope
            self.loop_depth += 1
            for s in stmt.body:
                self._analyze_stmt(s)
            self.loop_depth -= 1
            self.current_scope = old_scope
        elif isinstance(stmt, TpyBreak):
            if self.loop_depth == 0:
                raise SemanticError("'break' outside loop")
        elif isinstance(stmt, TpyContinue):
            if self.loop_depth == 0:
                raise SemanticError("'continue' outside loop")

    def _analyze_var_decl(self, stmt: TpyVarDecl) -> None:
        """Analyze a variable declaration."""
        # Own[T] is only valid for function parameters and return types, not variables
        if stmt.type and isinstance(stmt.type, OwnType):
            raise self._error(
                f"Own[{stmt.type.wrapped}] cannot be used as a variable type. "
                f"Use '{stmt.type.wrapped}' instead (Own[T] is for parameters and return types only)",
                stmt
            )

        # Check if this is a reassignment (variable already exists in scope)
        existing_type = self.current_scope.lookup(stmt.name)

        if stmt.init:
            # Handle empty list literal or list() constructor with explicit type annotation
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            is_list_constructor = (isinstance(stmt.init, TpyCall) and
                                   stmt.init.func == "list" and
                                   not stmt.init.args)

            if (is_empty_literal or is_list_constructor) and stmt.type:
                if isinstance(stmt.type, ListType):
                    # Use the annotated element type for the empty list
                    elem_type = stmt.type.element_type
                    if self.current_function is None:
                        # Global context: return ListType directly
                        init_type = ListType(elem_type)
                    else:
                        # Function-local context: create PendingListType
                        literal_id = self.literal_counter
                        self.literal_counter += 1
                        info = ListLiteralInfo(
                            literal_id=literal_id,
                            expr=stmt.init,
                            element_type=elem_type,
                            size=0,
                            is_global=self.is_top_level,
                            has_explicit_annotation=True,
                            explicit_type=stmt.type
                        )
                        self.list_literals[literal_id] = info
                        self.pending_resolutions.append(literal_id)
                        init_type = PendingListType(elem_type, 0, literal_id)
                else:
                    raise SemanticError(
                        f"Empty list requires list[T] annotation, got {stmt.type}"
                    )
            else:
                init_type = self._analyze_expr(stmt.init)

            # Track list literal to variable mapping for mutation detection
            if isinstance(init_type, PendingListType):
                literal_id = init_type.literal_id
                self.variable_to_literal[stmt.name] = literal_id
                info = self.list_literals[literal_id]
                info.variable_name = stmt.name

                # If explicit annotation is provided, record it
                if stmt.type and isinstance(stmt.init, TpyArrayLiteral):
                    info.has_explicit_annotation = True
                    info.explicit_type = stmt.type

            if stmt.type:
                # Special case: single-char string literal can be assigned to Char
                if (isinstance(stmt.type, CharType) and isinstance(init_type, StrType) and
                    isinstance(stmt.init, TpyStrLiteral) and len(stmt.init.value) == 1):
                    pass  # Allow str literal -> Char
                else:
                    stmt.init = self._coerce_expr(stmt.init, init_type, stmt.type,
                                                  f"variable '{stmt.name}'",
                                                  coercion_ctx="init")
                var_type = stmt.type
            elif existing_type:
                # Reassignment: check if we need to upgrade IntLiteralType
                if isinstance(existing_type, IntLiteralType) and isinstance(init_type, (Int32Type, BigIntType)):
                    # Upgrade from IntLiteralType to concrete type
                    var_type = init_type
                    # Update var_types so codegen knows the resolved type
                    orig_decl = self.var_decl_by_name.get(stmt.name)
                    if orig_decl:
                        self.var_types[id(orig_decl)] = init_type
                else:
                    # Normal reassignment: use existing type, check compatibility
                    stmt.init = self._coerce_expr(stmt.init, init_type, existing_type,
                                                  f"reassignment to '{stmt.name}'",
                                                  coercion_ctx="assign")
                    var_type = existing_type
            else:
                # New variable: resolve IntLiteralType to BigInt (Python int semantics)
                # This ensures Int32 + untyped_var promotes to BigInt correctly
                if isinstance(init_type, IntLiteralType):
                    var_type = BIGINT
                else:
                    var_type = init_type
        elif stmt.type:
            var_type = stmt.type
        else:
            raise SemanticError(f"Variable '{stmt.name}' has no type annotation and no initializer")

        self.current_scope.define(stmt.name, var_type)
        # Track var_decl for later type updates
        if isinstance(var_type, IntLiteralType):
            self.var_decl_by_name[stmt.name] = stmt

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        target_type = self._analyze_expr(stmt.target)
        value_type = self._analyze_expr(stmt.value)

        # Prevent assignment to Span or str elements (read-only views)
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.get_expr_type(stmt.target.obj)
            if isinstance(obj_type, SpanType):
                raise SemanticError("Cannot assign to elements of Span (read-only view)")
            if isinstance(obj_type, StrType):
                raise SemanticError("Cannot assign to elements of str (read-only)")

        # Prevent assignment through ConstPtr (read-only pointer)
        if isinstance(stmt.target, TpyFieldAccess):
            obj_type = self.get_expr_type(stmt.target.obj)
            if isinstance(obj_type, ConstPtrType):
                raise self._error("Cannot assign through ConstPtr (read-only pointer)", stmt)

        # When reassigning a variable with IntLiteralType to a concrete integer type,
        # update the variable's type to the more specific type
        if isinstance(stmt.target, TpyName) and isinstance(target_type, IntLiteralType):
            if isinstance(value_type, (Int32Type, BigIntType)):
                self.current_scope.define(stmt.target.name, value_type)
                self.expr_types[id(stmt.target)] = value_type
                # Update var_types so codegen knows the resolved type
                var_decl = self.var_decl_by_name.get(stmt.target.name)
                if var_decl:
                    self.var_types[id(var_decl)] = value_type
                return

        stmt.value = self._coerce_expr(stmt.value, value_type, target_type, "assignment",
                                       coercion_ctx="assign")

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        target_type = self._analyze_expr(stmt.target)
        value_type = self._analyze_expr(stmt.value)
        # Both must be integer types for arithmetic augmented assignment
        if not isinstance(target_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Augmented assignment target must be an integer type, got {target_type}")
        if not isinstance(value_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Augmented assignment value must be an integer type, got {value_type}")

    def _analyze_expr_with_hint(self, expr: TpyExpr, type_hint: Optional[TpyType]) -> TpyType:
        """Analyze an expression with an optional type hint for inference.

        The type hint allows constructs like list() to infer their element type
        from context (e.g., function parameter type).
        """
        # Handle list() constructor with type hint
        if (isinstance(expr, TpyCall) and expr.func == "list" and not expr.args
                and isinstance(type_hint, ListType)):
            elem_type = type_hint.element_type
            if self.current_function is None:
                typ = ListType(elem_type)
            else:
                literal_id = self.literal_counter
                self.literal_counter += 1
                info = ListLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=elem_type,
                    size=0,
                    is_global=self.is_top_level,
                    has_explicit_annotation=True,
                    explicit_type=type_hint
                )
                self.list_literals[literal_id] = info
                self.pending_resolutions.append(literal_id)
                typ = PendingListType(elem_type, 0, literal_id)
            self.expr_types[id(expr)] = typ
            return typ

        # Handle empty list literal with type hint
        if (isinstance(expr, TpyArrayLiteral) and not expr.elements
                and isinstance(type_hint, ListType)):
            elem_type = type_hint.element_type
            if self.current_function is None:
                typ = ListType(elem_type)
            else:
                literal_id = self.literal_counter
                self.literal_counter += 1
                info = ListLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=elem_type,
                    size=0,
                    is_global=self.is_top_level,
                    has_explicit_annotation=True,
                    explicit_type=type_hint
                )
                self.list_literals[literal_id] = info
                self.pending_resolutions.append(literal_id)
                typ = PendingListType(elem_type, 0, literal_id)
            self.expr_types[id(expr)] = typ
            return typ

        # Fall back to regular analysis
        return self._analyze_expr(expr)

    def _check_constructor(self, expr: TpyCall, type_def: builtin_modules.BuiltinTypeDef) -> TpyType:
        """Check a type constructor call against its overloads."""
        type_name = expr.func
        arg_types = [self._analyze_expr(arg) for arg in expr.args]

        # Find a matching constructor overload
        for ctor in type_def.constructors:
            if len(ctor.params) != len(arg_types):
                continue
            # Check if all arguments match
            match = True
            for param, arg_type in zip(ctor.params, arg_types):
                if not builtin_modules._type_matches_param(arg_type, param.type):
                    match = False
                    break
            if match:
                return ctor.returns

        # No matching overload found
        if not type_def.constructors:
            raise self._error(f"{type_name}() is not callable", expr)
        elif len(arg_types) == 0:
            raise self._error(f"{type_name}() requires an argument", expr)
        elif len(arg_types) == 1:
            raise self._error(f"{type_name}() cannot convert {arg_types[0]}", expr)
        else:
            raise self._error(f"{type_name}() takes at most 1 argument, got {len(arg_types)}", expr)

    def _analyze_expr(self, expr: TpyExpr) -> TpyType:
        """Analyze an expression and return its type."""
        if isinstance(expr, TpyIntLiteral):
            typ = IntLiteralType(expr.value)
        elif isinstance(expr, TpyStrLiteral):
            # String literals are always str type (including single-char)
            # Char type is only used when explicitly annotated or from string indexing
            typ = STR
        elif isinstance(expr, TpyBoolLiteral):
            typ = BOOL
        elif isinstance(expr, TpyName):
            typ = self._analyze_name(expr)
        elif isinstance(expr, TpyBinOp):
            typ = self._analyze_binop(expr)
        elif isinstance(expr, TpyUnaryOp):
            typ = self._analyze_unaryop(expr)
        elif isinstance(expr, TpyCall):
            typ = self._analyze_call(expr)
        elif isinstance(expr, TpyMethodCall):
            typ = self._analyze_method_call(expr)
        elif isinstance(expr, TpyFieldAccess):
            typ = self._analyze_field_access(expr)
        elif isinstance(expr, TpyArrayLiteral):
            typ = self._analyze_array_literal(expr)
        elif isinstance(expr, TpyListRepeat):
            typ = self._analyze_list_repeat(expr)
        elif isinstance(expr, TpySubscript):
            typ = self._analyze_subscript(expr)
        elif isinstance(expr, TpyCoerce):
            # Coercions are attached post-analysis; treat as the expected type.
            typ = expr.expected_type
        else:
            raise SemanticError(f"Unknown expression type: {type(expr).__name__}")

        self.expr_types[id(expr)] = typ
        return typ

    def _analyze_name(self, expr: TpyName) -> TpyType:
        """Analyze a name reference."""
        typ = self.current_scope.lookup(expr.name)
        if typ is None:
            raise SemanticError(f"Undefined variable: '{expr.name}'")
        return typ

    def _analyze_binop(self, expr: TpyBinOp) -> TpyType:
        """Analyze a binary operation."""
        # Check for unsupported operators
        if expr.op == "div":
            raise SemanticError("True division (/) not supported; use floor division (//) instead", expr.loc)

        left_type = self._analyze_expr(expr.left)
        right_type = self._analyze_expr(expr.right)

        # Helper to check if type is any integer type
        def is_int_type(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType))

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            if is_int_type(left_type) and is_int_type(right_type):
                return BOOL
            return BOOL

        # Membership operators (in, not in) return Bool
        if expr.op in ("in", "not in"):
            # Right side must be iterable
            if isinstance(right_type, (ListType, ArrayType, SpanType, StaticListType, PendingListType)):
                return BOOL
            if isinstance(right_type, StrType):
                return BOOL
            raise SemanticError(f"Cannot use '{expr.op}' with non-iterable type {right_type}")

        # Logical operators return Bool
        if expr.op in ("&&", "||"):
            return BOOL

        # IntLiteral + IntLiteral -> IntLiteral (stays unresolved until context determines type)
        if isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType):
            return IntLiteralType(0)  # Value not tracked for compound expressions

        # Arithmetic/bitwise operators - use module system
        if result := builtin_modules.lookup_binop(left_type, expr.op, right_type):
            return result.method.returns

        raise SemanticError(f"Invalid operand types for '{expr.op}': {left_type} and {right_type}")

    def _analyze_unaryop(self, expr: TpyUnaryOp) -> TpyType:
        """Analyze a unary operation."""
        operand_type = self._analyze_expr(expr.operand)

        # Logical not always returns Bool
        if expr.op == "!":
            return BOOL

        # IntLiteralType special cases - preserve literal nature when possible
        if isinstance(operand_type, IntLiteralType):
            if expr.op == "-":
                return IntLiteralType(-operand_type.value)
            if expr.op == "~":
                # Bitwise not on literal - treat as Int32
                return INT32

        # Use module system for unary operators
        if result := builtin_modules.lookup_unaryop(operand_type, expr.op):
            return result.method.returns

        raise SemanticError(f"Invalid operand type for unary '{expr.op}': {operand_type}")

    def _analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Generic type instantiation (e.g., StaticList[T, N]())
        if expr.call_type is not None:
            for arg in expr.args:
                self._analyze_expr(arg)
            return expr.call_type

        # Check module registry for built-in functions
        if builtin_fn := builtin_modules.lookup_function(expr.func):
            return self._analyze_builtin_call(expr, builtin_fn)

        # Built-in print()
        if expr.func == "print":
            for arg in expr.args:
                self._analyze_expr(arg)
            return VOID

        # Built-in len() - dispatches to __len__ on the argument type
        if expr.func == "len":
            if len(expr.args) != 1:
                raise SemanticError("len() takes exactly 1 argument")
            arg_type = self._analyze_expr(expr.args[0])
            qname = arg_type.qualified_name()
            if qname is None:
                raise SemanticError(f"len() argument must have __len__, got {arg_type}")
            len_methods = builtin_modules.lookup_type_method(qname, "__len__")
            if not len_methods:
                raise SemanticError(f"len() argument must have __len__, got {arg_type}")
            return len_methods[0].returns

        # Check if it's a builtin type constructor (e.g., Int32, int)
        if type_def := builtin_modules.lookup_type_by_func_name(expr.func):
            return self._check_constructor(expr, type_def)

        # Check if it's a record constructor
        record = self.registry.get_record(expr.func)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self._analyze_expr(arg)
            return RecordType(expr.func)

        # Check if it's a function call
        func = self.registry.get_function(expr.func)
        if func:
            if len(expr.args) != len(func.params):
                raise SemanticError(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}")
            for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
                # Handle list() constructor - infer type from parameter
                arg_type = self._analyze_expr_with_hint(arg, ptype)

                # Check for Own[T] passed directly to object type parameter
                # Own[T] is an rvalue (temporary) and can't bind to T& (non-const ref)
                # But if parameter is also Own[T], that's fine (both are by-value)
                if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType) and not ptype.is_value_type():
                    # Give different guidance based on whether arg is a variable or temporary
                    if isinstance(arg, TpyName):
                        hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                    else:
                        hint = "Assign to a variable first: x = func(); other_func(x)"
                    raise self._error(
                        f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                        f"(object types are passed by reference). {hint}",
                        arg
                    )

                # Check for T passed to Own[T] parameter - would be implicit copy
                if isinstance(ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                    raise self._error(
                        f"Cannot pass '{arg_type}' to parameter '{pname}: Own[{ptype.wrapped}]' "
                        f"(would be implicit copy)",
                        arg
                    )

                # Special case: single-char string literal can be passed as Char
                if not (isinstance(ptype, CharType) and isinstance(arg_type, StrType) and
                        isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                    coerced_arg = self._coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                    coercion_ctx="arg")
                    expr.args[i] = coerced_arg

                # Track parameter context for list inference
                if isinstance(arg_type, PendingListType):
                    self._mark_list_param_context(arg, ptype)

            return func.return_type

        # list() without type annotation
        if expr.func == "list":
            raise SemanticError("list() requires type annotation: `x: list[T] = list()`")

        raise SemanticError(f"Unknown function or type: '{expr.func}'")

    def _analyze_builtin_call(self, expr: TpyCall, fn_def: builtin_modules.BuiltinFunctionDef) -> TpyType:
        """Type-check a call to a built-in function from the module registry."""
        arg_types = [self._analyze_expr(arg) for arg in expr.args]

        for overload in fn_def.overloads:
            if len(overload.params) != len(arg_types):
                continue
            if all(self._builtin_type_matches(arg_t, param.type)
                   for arg_t, param in zip(arg_types, overload.params)):
                return overload.returns

        # No matching overload found - build error message
        arg_type_strs = ", ".join(str(t) for t in arg_types)
        raise SemanticError(f"No matching overload for {fn_def.name}({arg_type_strs})")

    def _builtin_type_matches(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument type is compatible with a builtin parameter type."""
        if arg_type == param_type:
            return True
        # IntLiteral can match Int32 or BigInt
        if isinstance(arg_type, IntLiteralType):
            return isinstance(param_type, (Int32Type, BigIntType))
        return False

    def _check_method_args(self, expr: TpyMethodCall, method: builtin_modules.MethodDef,
                           obj_type: TpyType) -> TpyType:
        """Check method arguments against a resolved MethodDef and return the return type.

        Note: The method should be resolved (TypeParams substituted) before calling this.
        """
        if len(expr.args) != len(method.params):
            raise SemanticError(
                f"{expr.method}() takes {len(method.params)} argument(s), got {len(expr.args)}"
            )
        for i, (arg, param) in enumerate(zip(expr.args, method.params)):
            arg_type = self._analyze_expr(arg)
            # param.type is TpyType after resolve_method (not TypeParam)
            param_type: TpyType = param.type  # type: ignore
            expr.args[i] = self._coerce_expr(arg, arg_type, param_type, f"{param.name} argument",
                                             coercion_ctx="arg")
        # method.returns is TpyType after resolve_method (not TypeParam)
        return_type: TpyType = method.returns  # type: ignore
        return return_type

    def _analyze_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a method call."""
        obj_type = self._analyze_expr(expr.obj)

        # StaticList methods - use module lookup
        if isinstance(obj_type, StaticListType):
            # Try module lookup for defined methods
            type_params = builtin_modules.extract_type_params(obj_type)
            methods = builtin_modules.lookup_type_method(obj_type, expr.method)
            if methods:
                resolved = builtin_modules.resolve_method(methods[0], type_params)
                return self._check_method_args(expr, resolved, obj_type)

            # Hardcoded method: size (alias for __len__)
            if expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown StaticList method: '{expr.method}'")

        # Array methods - use module lookup
        if isinstance(obj_type, ArrayType):
            type_params = builtin_modules.extract_type_params(obj_type)
            methods = builtin_modules.lookup_type_method(obj_type, expr.method)
            if methods:
                resolved = builtin_modules.resolve_method(methods[0], type_params)
                return self._check_method_args(expr, resolved, obj_type)

            # Hardcoded method: size (alias for __len__)
            if expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown Array method: '{expr.method}'")

        # Span methods - use module lookup
        if isinstance(obj_type, SpanType):
            type_params = builtin_modules.extract_type_params(obj_type)
            methods = builtin_modules.lookup_type_method(obj_type, expr.method)
            if methods:
                resolved = builtin_modules.resolve_method(methods[0], type_params)
                return self._check_method_args(expr, resolved, obj_type)

            # Hardcoded method: size (alias for __len__)
            if expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown Span method: '{expr.method}'")

        # PendingListType and ListType methods
        if isinstance(obj_type, (PendingListType, ListType)):
            elem_type = obj_type.element_type

            # Mutation methods - mark literal as mutated if it's a pending type
            mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "__setitem__"}
            if expr.method in mutation_methods:
                self._mark_list_mutated(expr.obj)

            # Try module lookup first for any method
            type_params = builtin_modules.extract_type_params(obj_type)
            methods = builtin_modules.lookup_type_method(obj_type, expr.method)
            if methods:
                resolved = builtin_modules.resolve_method(methods[0], type_params)
                return self._check_method_args(expr, resolved, obj_type)

            # Hardcoded methods (extend, size)
            if expr.method == "extend":
                if len(expr.args) != 1:
                    raise SemanticError("extend() takes exactly 1 argument")
                arg_type = self._analyze_expr(expr.args[0])
                # Should be iterable of elem_type - for now accept list/array/span
                return VOID
            elif expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown list method: '{expr.method}'")

        # User-defined record methods
        if isinstance(obj_type, RecordType):
            record_info = self.registry.get_record(obj_type.name)
            if record_info and record_info.get_method(expr.method):
                method_info = record_info.get_method(expr.method)
                # Check argument count
                if len(expr.args) != len(method_info.params):
                    raise SemanticError(
                        f"Method '{expr.method}' expects {len(method_info.params)} arguments, "
                        f"got {len(expr.args)}"
                    )
                # Type-check arguments
                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, method_info.params)):
                    arg_type = self._analyze_expr(arg)
                    expr.args[i] = self._coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                     coercion_ctx="arg")
                return method_info.return_type

        raise SemanticError(f"Cannot call method '{expr.method}' on type {obj_type}")

    def _analyze_field_access(self, expr: TpyFieldAccess) -> TpyType:
        """Analyze a field access."""
        obj_type = self._analyze_expr(expr.obj)

        # Handle pointer types - dereference to get the pointee
        actual_type = obj_type
        if isinstance(obj_type, (PtrType, ConstPtrType)):
            actual_type = obj_type.pointee

        if isinstance(actual_type, RecordType):
            record = self.registry.get_record(actual_type.name)
            if not record:
                raise SemanticError(f"Unknown record type: '{actual_type.name}'")
            for fld in record.fields:
                if fld.name == expr.field:
                    return fld.type
            raise SemanticError(f"Record '{actual_type.name}' has no field '{expr.field}'")

        raise SemanticError(f"Cannot access field '{expr.field}' on type {obj_type}")

    def _analyze_array_literal(self, expr: TpyArrayLiteral) -> TpyType:
        """Analyze an array literal [expr, expr, ...]

        In function-local contexts, returns a PendingListType that will be
        resolved to Array or list based on usage (mutation, parameter passing).
        In global/module context, returns ListType directly.
        """
        if not expr.elements:
            raise SemanticError("Empty array literal requires explicit type annotation")

        # Analyze all elements first
        elem_types = [self._analyze_expr(e) for e in expr.elements]

        # Determine element type from first element
        first_type = elem_types[0]
        # Keep IntLiteralType so array can coerce to either Int32 or BigInt based on context

        # Check all elements are compatible
        for i, elem_type in enumerate(elem_types[1:], 2):
            # IntLiteralType elements are compatible with each other
            if isinstance(first_type, IntLiteralType) and isinstance(elem_type, IntLiteralType):
                continue
            # IntLiteral coerces to concrete integer types
            if isinstance(elem_type, IntLiteralType) and isinstance(first_type, (Int32Type, BigIntType)):
                continue
            if isinstance(first_type, IntLiteralType) and isinstance(elem_type, (Int32Type, BigIntType)):
                # First was literal, but later element is concrete - update first_type
                first_type = elem_type
                continue
            # Nested lists with IntLiteralType elements are compatible
            if (isinstance(first_type, ListType) and isinstance(elem_type, ListType) and
                isinstance(first_type.element_type, IntLiteralType) and
                isinstance(elem_type.element_type, IntLiteralType)):
                continue
            # PendingListTypes with compatible element types are compatible
            if (isinstance(first_type, PendingListType) and isinstance(elem_type, PendingListType) and
                first_type.size == elem_type.size):
                # IntLiteralType elements are compatible regardless of value
                if (isinstance(first_type.element_type, IntLiteralType) and
                    isinstance(elem_type.element_type, IntLiteralType)):
                    continue
                if first_type.element_type == elem_type.element_type:
                    continue
            if elem_type != first_type:
                raise SemanticError(
                    f"Array literal element {i} has type {elem_type}, expected {first_type}"
                )

        size = len(expr.elements)

        # Global context (no current function) -> ListType (std::vector)
        # Keep IntLiteralType to allow coercion to Int32 when annotation is present
        if self.current_function is None:
            return ListType(first_type)

        # Function-local context -> create PendingListType for deferred resolution
        literal_id = self.literal_counter
        self.literal_counter += 1

        info = ListLiteralInfo(
            literal_id=literal_id,
            expr=expr,
            element_type=first_type,
            size=size,
            is_global=self.is_top_level
        )
        self.list_literals[literal_id] = info
        self.pending_resolutions.append(literal_id)

        return PendingListType(first_type, size, literal_id)

    def _analyze_list_repeat(self, expr: TpyListRepeat) -> TpyType:
        """Analyze a list repetition: [element] * count"""
        elem_type = self._analyze_expr(expr.element)
        count_type = self._analyze_expr(expr.count)

        if not isinstance(count_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"List repetition count must be an integer type, got {count_type}")

        # Keep IntLiteralType so it can coerce to annotated type (list[Int32] or list[int])
        return ListType(elem_type)

    def _analyze_subscript(self, expr: TpySubscript) -> TpyType:
        """Analyze subscript indexing: obj[index]"""
        obj_type = self._analyze_expr(expr.obj)
        index_type = self._analyze_expr(expr.index)

        if not isinstance(index_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Subscript index must be an integer type, got {index_type}")

        if isinstance(obj_type, ArrayType):
            return obj_type.element_type
        elif isinstance(obj_type, SpanType):
            return obj_type.element_type
        elif isinstance(obj_type, StaticListType):
            return obj_type.element_type
        elif isinstance(obj_type, ListType):
            return obj_type.element_type
        elif isinstance(obj_type, PendingListType):
            return obj_type.element_type
        elif isinstance(obj_type, StrType):
            return CHAR
        else:
            raise SemanticError(f"Cannot index type {obj_type}")

    def _check_dangling_reference(self, expr: TpyExpr, return_type: TpyType, loc: SourceLocation | None) -> None:
        """Check if returning expr as a reference would be a dangling reference.

        Object types are returned by reference. Returning a local variable or
        newly constructed object would create a dangling reference.
        """
        # Only check object types (value types are returned by value)
        # Pointers are also value types (the pointer itself is copied)
        # OwnType returns by value (ownership transfer), so no dangling risk
        if return_type.is_value_type() or isinstance(return_type, (VoidType, PtrType, ConstPtrType, OwnType)):
            return

        # Check if the expression is safe to return as a reference
        if self._is_dangling_return(expr):
            raise self._error(
                f"Cannot return local or temporary as reference. "
                f"Object type '{return_type}' is returned by reference. "
                f"Use Own[{return_type}] to return by value.",
                expr
            )

    def _is_dangling_return(self, expr: TpyExpr) -> bool:
        """Check if returning this expression would create a dangling reference."""
        if isinstance(expr, TpyCoerce):
            return self._is_dangling_return(expr.expr)
        # Array literal - creates temporary
        if isinstance(expr, TpyArrayLiteral):
            return True

        # List repeat - creates temporary
        if isinstance(expr, TpyListRepeat):
            return True

        # Constructor call - creates temporary
        if isinstance(expr, TpyCall):
            # Generic container constructor (StaticList[T,N](), Array[T,N](), list[T]())
            if expr.call_type is not None:
                if isinstance(expr.call_type, (StaticListType, ArrayType, ListType)):
                    return True

            # Record constructor
            if expr.func in self.registry.records:
                return True

            # Function returning Own[T] creates a temporary (by-value return)
            func = self.registry.get_function(expr.func)
            if func and isinstance(func.return_type, OwnType):
                return True

            # Regular function call - assume it returns something safe
            # (the callee is responsible for not returning dangling refs)
            return False

        # Local variable (not a parameter or global) - would dangle after function returns
        if isinstance(expr, TpyName):
            # Check if it's a parameter (safe)
            if self.current_function:
                for pname, ptype in self.current_function.params:
                    if pname == expr.name:
                        return False  # Parameter - safe to return reference

            # Check if it's a global (safe - lives forever)
            if expr.name in self.global_scope.bindings:
                return False

            # Local variable - dangling
            return True

        # Field access - safe only if the object itself is safe
        if isinstance(expr, TpyFieldAccess):
            return self._is_dangling_return(expr.obj)

        # Subscript - safe only if the container itself is safe
        if isinstance(expr, TpySubscript):
            return self._is_dangling_return(expr.obj)

        # Method call - assume safe (callee's responsibility)
        if isinstance(expr, TpyMethodCall):
            return False

        # Unary/Binary ops - might create temporaries, be conservative
        if isinstance(expr, (TpyUnaryOp, TpyBinOp)):
            return True

        # Default: assume safe
        return False

    def _pending_list_matches_array(self, actual: PendingListType, expected: ArrayType) -> bool:
        """Check if a pending list literal can match an Array type (including nested arrays)."""
        if actual.size != expected.size:
            return False

        actual_elem = actual.element_type
        expected_elem = expected.element_type

        if isinstance(actual_elem, PendingListType) and isinstance(expected_elem, ArrayType):
            return self._pending_list_matches_array(actual_elem, expected_elem)

        if actual_elem == expected_elem:
            return True

        if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (Int32Type, BigIntType)):
            info = self.list_literals.get(actual.literal_id)
            if info:
                info.coerced_element_type = expected_elem
            return True

        return False

    def _check_type_compatible(self, actual: TpyType, expected: TpyType, context: str,
                                loc: SourceLocation | None = None,
                                source_expr: TpyExpr | None = None,
                                is_return: bool = False,
                                coercion_ctx: str | None = None) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Returns a Coercion if a conversion should be applied at codegen time.

        Args:
            source_expr: The expression being converted (for lvalue checks)
            is_return: True if this is a return statement (affects lifetime checks)
        """
        if actual == expected:
            return None

        # Allow T -> Own[T] coercion (ownership transfer for return values)
        if isinstance(expected, OwnType):
            return self._check_type_compatible(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self._check_type_compatible(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx)

        # IntLiteral can coerce to BigInt or stay unresolved
        if isinstance(actual, IntLiteralType):
            if isinstance(expected, (BigIntType, IntLiteralType)):
                return None

        # Allow Array element type coercion if sizes match
        if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
            if actual.size == expected.size:
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None

        # Allow list element type coercion
        if isinstance(actual, ListType) and isinstance(expected, ListType):
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return None

        # Allow ListType -> ArrayType for explicit annotations
        # (global array literals become ListType but can be assigned to Array variables)
        if isinstance(actual, ListType) and isinstance(expected, ArrayType):
            if actual.element_type == expected.element_type:
                return None
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return None

        # Allow PendingListType compatibility during first phase (before resolution)
        if isinstance(actual, PendingListType):
            # Compatible with list[T] if element types match
            if isinstance(expected, ListType):
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None
            # Compatible with Array[T, N] if element types and sizes match
            if isinstance(expected, ArrayType):
                if self._pending_list_matches_array(actual, expected):
                    return None

        ctx = coercion_ctx or context
        coercion = resolve_coercion(actual, expected, ctx)
        if coercion is None:
            raise SemanticError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

        if isinstance(actual, PendingListType) and isinstance(expected, SpanType):
            info = self.list_literals.get(actual.literal_id)
            if info:
                info.coerced_element_type = expected.element_type
                info.passed_to_span_param = True

        if coercion.check_range and not coercion.check_range(actual, expected):
            INT32_MIN = -(2**31)
            INT32_MAX = 2**31 - 1
            raise SemanticError(
                f"Integer literal {actual.value} is outside Int32 range "
                f"[{INT32_MIN}, {INT32_MAX}] in {context}",
                loc
            )

        if coercion.requires_mutable:
            if source_expr is None or not self._is_mutable_lvalue(source_expr):
                raise SemanticError(
                    f"Cannot take mutable pointer to read-only or temporary value in {context}; "
                    f"use ConstPtr for read-only access, or assign to a variable first",
                    loc
                )
        elif coercion.requires_lvalue:
            if source_expr is None or not self._is_lvalue(source_expr):
                raise SemanticError(
                    f"Cannot take address of temporary or rvalue in {context}; "
                    f"assign to a variable first",
                    loc
                )

        if coercion.forbid_return_local and is_return:
            if source_expr is not None and self._is_dangling_return(source_expr):
                raise SemanticError(
                    f"Cannot return local or temporary value; "
                    f"the returned pointer/reference would dangle",
                    loc
                )

        return coercion

    def _coerce_expr(self, expr: TpyExpr, actual: TpyType, expected: TpyType, context: str,
                     coercion_ctx: str, is_return: bool = False) -> TpyExpr:
        """Wrap expr in a coercion node if a conversion is needed."""
        coercion = self._check_type_compatible(actual, expected, context,
                                               getattr(expr, "loc", None),
                                               source_expr=expr,
                                               is_return=is_return,
                                               coercion_ctx=coercion_ctx)
        if coercion is None:
            return expr
        runtime_bigint = False
        if coercion.name == "int_literal_to_int32":
            runtime_bigint = self._is_runtime_bigint_expr(expr)
        coerced = TpyCoerce(
            expr=expr,
            actual_type=actual,
            expected_type=expected,
            coercion=coercion,
            context_kind=coercion_ctx,
            context_msg=context,
            runtime_bigint=runtime_bigint,
            loc=expr.loc
        )
        self.expr_types[id(coerced)] = expected
        return coerced

    def _is_runtime_bigint_expr(self, expr: TpyExpr) -> bool:
        """Check if an IntLiteralType expression could be BigInt at runtime."""
        if isinstance(expr, TpyCoerce):
            return self._is_runtime_bigint_expr(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return self._is_runtime_bigint_expr(expr.left) or self._is_runtime_bigint_expr(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self._is_runtime_bigint_expr(expr.operand)
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            return True
        return True

    def _is_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is an lvalue (can have its address taken)."""
        if isinstance(expr, TpyCoerce):
            return self._is_lvalue(expr.expr)
        # Named variables are lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on an lvalue is also an lvalue (e.g., obj.field)
        if isinstance(expr, TpyFieldAccess):
            return self._is_lvalue(expr.obj)
        # Subscript on an lvalue is also an lvalue (e.g., arr[i])
        if isinstance(expr, TpySubscript):
            return self._is_lvalue(expr.obj)
        # Everything else (calls, literals, operators) are rvalues
        return False

    def _is_mutable_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is a mutable lvalue (can get a mutable Ptr).

        This is like _is_lvalue but also rejects read-only sources like Span elements.
        """
        if isinstance(expr, TpyCoerce):
            return self._is_mutable_lvalue(expr.expr)
        # Named variables are mutable lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on a mutable lvalue is also mutable
        if isinstance(expr, TpyFieldAccess):
            return self._is_mutable_lvalue(expr.obj)
        # Subscript: check if the base is a read-only type (Span, str)
        if isinstance(expr, TpySubscript):
            obj_type = self.get_expr_type(expr.obj)
            if isinstance(obj_type, (SpanType, StrType)):
                return False  # Span and str elements are read-only
            return self._is_mutable_lvalue(expr.obj)
        return False

    def get_expr_type(self, expr: TpyExpr) -> Optional[TpyType]:
        """Get the cached type of an expression."""
        return self.expr_types.get(id(expr))

    def _mark_list_mutated(self, obj_expr: TpyExpr) -> None:
        """Mark a list literal as mutated if it can be traced to one."""
        if isinstance(obj_expr, TpyName):
            var_name = obj_expr.name
            if var_name in self.variable_to_literal:
                literal_id = self.variable_to_literal[var_name]
                if literal_id in self.list_literals:
                    self.list_literals[literal_id].is_mutated = True

    def _mark_list_param_context(self, arg_expr: TpyExpr, param_type: TpyType) -> None:
        """Track parameter context for list literal inference."""
        literal_id = None

        if isinstance(arg_expr, TpyCoerce):
            arg_expr = arg_expr.expr

        # Direct variable reference
        if isinstance(arg_expr, TpyName):
            var_name = arg_expr.name
            if var_name in self.variable_to_literal:
                literal_id = self.variable_to_literal[var_name]

        if literal_id is not None and literal_id in self.list_literals:
            info = self.list_literals[literal_id]
            if isinstance(param_type, ListType):
                info.passed_to_list_param = True
                info.coerced_element_type = param_type.element_type
            elif isinstance(param_type, SpanType):
                info.passed_to_span_param = True
                info.coerced_element_type = param_type.element_type

    def _resolve_pending_list_types(self) -> None:
        """Resolve all pending list types after function analysis.

        Resolution rules (in priority order):
        1. Explicit annotation → use it
        2. is_mutated → ListType
        3. passed_to_list_param → ListType
        4. Otherwise → ArrayType

        Element type resolution:
        - If passed to typed param (list[T] or Span[T]), use T
        - IntLiteralType defaults to Int32 for containers
        """
        for literal_id in self.pending_resolutions:
            if literal_id not in self.list_literals:
                continue

            info = self.list_literals[literal_id]

            # Resolve element type
            # Priority: coerced type from param > resolved inner PendingListType > default
            elem_type = info.element_type

            # If element type is a PendingListType, look up its resolved type
            if isinstance(elem_type, PendingListType):
                inner_info = self.list_literals.get(elem_type.literal_id)
                if inner_info and inner_info.resolved_type:
                    elem_type = inner_info.resolved_type

            if isinstance(elem_type, IntLiteralType):
                if info.coerced_element_type is not None:
                    # Use element type from typed parameter (list[T] or Span[T])
                    elem_type = info.coerced_element_type
                else:
                    # Default to BigInt (Python semantics)
                    elem_type = BIGINT

            # Determine resolved type
            if info.has_explicit_annotation and info.explicit_type:
                resolved = info.explicit_type
            elif info.is_mutated:
                resolved = ListType(elem_type)
            elif info.passed_to_list_param:
                resolved = ListType(elem_type)
            elif info.is_global:
                # Globals can be imported and mutated by other modules
                resolved = ListType(elem_type)
            else:
                # Default: Array (stack-allocated, no mutation detected)
                resolved = ArrayType(elem_type, info.size)

            info.resolved_type = resolved

            # Update expr_types for the literal expression
            self.expr_types[id(info.expr)] = resolved

            # Update scope binding if this literal was assigned to a variable
            if info.variable_name and self.current_scope:
                current_type = self.current_scope.lookup(info.variable_name)
                if isinstance(current_type, PendingListType):
                    self.current_scope.define(info.variable_name, resolved)

    def _reset_function_tracking(self) -> None:
        """Reset per-function tracking state between function analyses."""
        self.variable_to_literal.clear()
        self.pending_resolutions.clear()

    def _get_iterable_element_type(self, iterable_type: TpyType) -> TpyType:
        """Get the element type of an iterable for for-each loops."""
        if isinstance(iterable_type, (ListType, ArrayType, SpanType, StaticListType)):
            return iterable_type.element_type
        if isinstance(iterable_type, PendingListType):
            return iterable_type.element_type
        if isinstance(iterable_type, StrType):
            return CHAR
        raise SemanticError(f"Cannot iterate over type {iterable_type}")
