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
    TpyType, Int32Type, VoidType, RecordType, PtrType, ConstPtrType,
    StaticListType, ArrayType, SpanType, ListType, PendingListType, ListLiteralInfo,
    StrType, CharType, BoolType, BigIntType, IntLiteralType,
    INT32, VOID, STR, CHAR, BOOL, BIGINT, FieldInfo, RecordInfo, FunctionInfo, TypeRegistry
)
from .parse import (
    SourceLocation,
    TpyModule, TpyRecord, TpyFunction, TpyStmt, TpyExpr,
    TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn, TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue,
    TpyIntLiteral, TpyStrLiteral, TpyBoolLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript
)


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
        """Register top-level variable declarations in global scope."""
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl):
                if stmt.type:
                    self.global_scope.define(stmt.name, stmt.type)
                elif stmt.init:
                    # Infer type from initializer
                    self.current_scope = self.global_scope
                    typ = self._analyze_expr(stmt.init)
                    # Resolve IntLiteralType to BigInt for variable declarations
                    if isinstance(typ, IntLiteralType):
                        typ = BIGINT
                    self.global_scope.define(stmt.name, typ)
                    self.current_scope = None

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
        """Analyze top-level statements (for generated main())."""
        self.current_scope = Scope(parent=self.global_scope)
        for stmt in stmts:
            self._analyze_stmt(stmt)
        self.current_scope = None

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
                self._check_type_compatible(ret_type, expected, "return value",
                                            getattr(stmt.value, 'loc', None))
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
        # Check if this is a reassignment (variable already exists in scope)
        existing_type = self.current_scope.lookup(stmt.name)

        if stmt.init:
            init_type = self._analyze_expr(stmt.init)

            # Track list literal to variable mapping for mutation detection
            if isinstance(init_type, PendingListType):
                literal_id = init_type.literal_id
                self.variable_to_literal[stmt.name] = literal_id
                info = self.list_literals[literal_id]
                info.variable_name = stmt.name

                # If explicit annotation is provided, record it
                if stmt.type:
                    info.has_explicit_annotation = True
                    info.explicit_type = stmt.type

            if stmt.type:
                self._check_type_compatible(init_type, stmt.type, f"variable '{stmt.name}'",
                                            getattr(stmt.init, 'loc', None))
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
                    self._check_type_compatible(init_type, existing_type, f"reassignment to '{stmt.name}'",
                                                getattr(stmt.init, 'loc', None))
                    var_type = existing_type
            else:
                # New variable: keep IntLiteralType for now, will be resolved based on usage
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

        self._check_type_compatible(value_type, target_type, "assignment")

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        target_type = self._analyze_expr(stmt.target)
        value_type = self._analyze_expr(stmt.value)
        # Both must be integer types for arithmetic augmented assignment
        if not isinstance(target_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Augmented assignment target must be an integer type, got {target_type}")
        if not isinstance(value_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Augmented assignment value must be an integer type, got {value_type}")

    def _analyze_expr(self, expr: TpyExpr) -> TpyType:
        """Analyze an expression and return its type."""
        if isinstance(expr, TpyIntLiteral):
            typ = IntLiteralType(expr.value)
        elif isinstance(expr, TpyStrLiteral):
            # Single-char string literals become Char type
            if len(expr.value) == 1:
                typ = CHAR
            else:
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

        # Arithmetic/bitwise operators - resolve integer types
        if is_int_type(left_type) and is_int_type(right_type):
            # IntLiteral coerces to the other operand's type
            # Int32 + IntLiteral -> Int32
            if isinstance(left_type, Int32Type) and isinstance(right_type, IntLiteralType):
                return INT32
            if isinstance(left_type, IntLiteralType) and isinstance(right_type, Int32Type):
                return INT32

            # BigInt + IntLiteral -> BigInt
            if isinstance(left_type, BigIntType) and isinstance(right_type, IntLiteralType):
                return BIGINT
            if isinstance(left_type, IntLiteralType) and isinstance(right_type, BigIntType):
                return BIGINT

            # IntLiteral + IntLiteral -> IntLiteral (stays unresolved until context determines type)
            if isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType):
                return IntLiteralType(0)  # Value not tracked for compound expressions

            # Int32 + Int32 -> Int32
            if isinstance(left_type, Int32Type) and isinstance(right_type, Int32Type):
                return INT32

            # BigInt + BigInt -> BigInt
            if isinstance(left_type, BigIntType) and isinstance(right_type, BigIntType):
                return BIGINT

            # Int32 + BigInt -> BigInt (wider type wins)
            return BIGINT

        raise SemanticError(f"Invalid operand types for '{expr.op}': {left_type} and {right_type}")

    def _analyze_unaryop(self, expr: TpyUnaryOp) -> TpyType:
        """Analyze a unary operation."""
        operand_type = self._analyze_expr(expr.operand)
        if expr.op == "-":
            if isinstance(operand_type, Int32Type):
                return INT32
            if isinstance(operand_type, BigIntType):
                return BIGINT
            if isinstance(operand_type, IntLiteralType):
                # -literal stays as IntLiteral (can still coerce)
                return IntLiteralType(-operand_type.value)
        elif expr.op == "~":
            if isinstance(operand_type, Int32Type):
                return INT32
            if isinstance(operand_type, IntLiteralType):
                # Bitwise not on literal - treat as Int32
                return INT32
        elif expr.op == "!":
            return BOOL
        raise SemanticError(f"Invalid operand type for unary '{expr.op}': {operand_type}")

    def _analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Generic type instantiation (e.g., StaticList[T, N]())
        if expr.call_type is not None:
            for arg in expr.args:
                self._analyze_expr(arg)
            return expr.call_type

        # Built-in print()
        if expr.func == "print":
            for arg in expr.args:
                self._analyze_expr(arg)
            return VOID

        # Built-in len()
        if expr.func == "len":
            if len(expr.args) != 1:
                raise SemanticError("len() takes exactly 1 argument")
            arg_type = self._analyze_expr(expr.args[0])
            if not isinstance(arg_type, (StaticListType, ArrayType, SpanType, ListType, PendingListType, StrType)):
                raise SemanticError(f"len() argument must be StaticList, Array, Span, list, or str, got {arg_type}")
            return INT32

        # Built-in chr()
        if expr.func == "chr":
            if len(expr.args) != 1:
                raise SemanticError("chr() takes exactly 1 argument")
            arg_type = self._analyze_expr(expr.args[0])
            if not isinstance(arg_type, (Int32Type, BigIntType, IntLiteralType)):
                raise SemanticError(f"chr() argument must be an integer type, got {arg_type}")
            return CHAR

        # Check if it's a type constructor
        if expr.func == "int":
            if len(expr.args) > 1:
                raise SemanticError("int() takes at most 1 argument")
            if expr.args:
                arg_type = self._analyze_expr(expr.args[0])
                if not isinstance(arg_type, (Int32Type, BigIntType, IntLiteralType)):
                    raise SemanticError(f"int() argument must be an integer type, got {arg_type}")
            return BIGINT

        if expr.func == "Int32":
            if len(expr.args) > 1:
                raise SemanticError("Int32() takes at most 1 argument")
            if expr.args:
                arg_type = self._analyze_expr(expr.args[0])
                if not isinstance(arg_type, (Int32Type, IntLiteralType, BigIntType)):
                    raise SemanticError(f"Int32() argument must be an integer type, got {arg_type}")
            return INT32

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
            for (pname, ptype), arg in zip(func.params, expr.args):
                arg_type = self._analyze_expr(arg)
                self._check_type_compatible(arg_type, ptype, f"argument '{pname}'",
                                            getattr(arg, 'loc', None))

                # Track parameter context for list inference
                if isinstance(arg_type, PendingListType):
                    self._mark_list_param_context(arg, ptype)

            return func.return_type

        raise SemanticError(f"Unknown function or type: '{expr.func}'")

    def _analyze_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a method call."""
        obj_type = self._analyze_expr(expr.obj)

        # StaticList methods
        if isinstance(obj_type, StaticListType):
            elem_type = obj_type.element_type
            if expr.method == "append":
                if len(expr.args) != 1:
                    raise SemanticError("append() takes exactly 1 argument")
                arg_type = self._analyze_expr(expr.args[0])
                self._check_type_compatible(arg_type, elem_type, "append argument")
                return VOID
            elif expr.method == "push_empty":
                if expr.args:
                    raise SemanticError("push_empty() takes no arguments")
                return PtrType(elem_type)
            elif expr.method == "get":
                if len(expr.args) != 1:
                    raise SemanticError("get() takes exactly 1 argument")
                self._analyze_expr(expr.args[0])
                return elem_type  # Returns T& in C++, value semantics for primitives
            elif expr.method == "get_mut":
                if len(expr.args) != 1:
                    raise SemanticError("get_mut() takes exactly 1 argument")
                self._analyze_expr(expr.args[0])
                return PtrType(elem_type)
            elif expr.method == "set":
                if len(expr.args) != 2:
                    raise SemanticError("set() takes exactly 2 arguments")
                self._analyze_expr(expr.args[0])  # index
                arg_type = self._analyze_expr(expr.args[1])  # value
                self._check_type_compatible(arg_type, elem_type, "set value")
                return VOID
            elif expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown StaticList method: '{expr.method}'")

        # Array methods
        if isinstance(obj_type, ArrayType):
            elem_type = obj_type.element_type
            if expr.method == "get":
                if len(expr.args) != 1:
                    raise SemanticError("get() takes exactly 1 argument")
                self._analyze_expr(expr.args[0])
                return elem_type
            elif expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown Array method: '{expr.method}'")

        # Span methods
        if isinstance(obj_type, SpanType):
            elem_type = obj_type.element_type
            if expr.method == "get":
                if len(expr.args) != 1:
                    raise SemanticError("get() takes exactly 1 argument")
                self._analyze_expr(expr.args[0])
                return elem_type
            elif expr.method == "size":
                if expr.args:
                    raise SemanticError("size() takes no arguments")
                return INT32
            else:
                raise SemanticError(f"Unknown Span method: '{expr.method}'")

        # PendingListType and ListType methods
        if isinstance(obj_type, (PendingListType, ListType)):
            elem_type = obj_type.element_type

            # Mutation methods - mark literal as mutated if it's a pending type
            mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend"}
            if expr.method in mutation_methods:
                self._mark_list_mutated(expr.obj)

            if expr.method == "append":
                if len(expr.args) != 1:
                    raise SemanticError("append() takes exactly 1 argument")
                arg_type = self._analyze_expr(expr.args[0])
                self._check_type_compatible(arg_type, elem_type, "append argument")
                return VOID
            elif expr.method == "pop":
                if expr.args:
                    raise SemanticError("pop() takes no arguments")
                return elem_type
            elif expr.method == "insert":
                if len(expr.args) != 2:
                    raise SemanticError("insert() takes exactly 2 arguments")
                self._analyze_expr(expr.args[0])  # index
                arg_type = self._analyze_expr(expr.args[1])  # value
                self._check_type_compatible(arg_type, elem_type, "insert value")
                return VOID
            elif expr.method == "remove":
                if len(expr.args) != 1:
                    raise SemanticError("remove() takes exactly 1 argument")
                arg_type = self._analyze_expr(expr.args[0])
                self._check_type_compatible(arg_type, elem_type, "remove argument")
                return VOID
            elif expr.method == "clear":
                if expr.args:
                    raise SemanticError("clear() takes no arguments")
                return VOID
            elif expr.method == "extend":
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
                    self._check_type_compatible(arg_type, ptype, f"argument '{pname}'")
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
            if elem_type != first_type:
                raise SemanticError(
                    f"Array literal element {i} has type {elem_type}, expected {first_type}"
                )

        size = len(expr.elements)

        # Global context (no current function) -> ListType (std::vector)
        if self.current_function is None:
            # Resolve IntLiteralType to Int32 for container elements
            elem_type = first_type
            if isinstance(elem_type, IntLiteralType):
                elem_type = INT32
            return ListType(elem_type)

        # Function-local context -> create PendingListType for deferred resolution
        literal_id = self.literal_counter
        self.literal_counter += 1

        info = ListLiteralInfo(
            literal_id=literal_id,
            expr=expr,
            element_type=first_type,
            size=size,
            is_global=False
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

        # Resolve IntLiteral element type to Int32 for list elements
        if isinstance(elem_type, IntLiteralType):
            elem_type = INT32

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

    def _check_type_compatible(self, actual: TpyType, expected: TpyType, context: str,
                                loc: SourceLocation | None = None) -> None:
        """Check if actual type is compatible with expected type."""
        if actual == expected:
            return

        # IntLiteral can coerce to any integer type
        if isinstance(actual, IntLiteralType):
            if isinstance(expected, (BigIntType, IntLiteralType)):
                return
            if isinstance(expected, Int32Type):
                # Check if literal value fits in Int32 range
                INT32_MIN = -(2**31)
                INT32_MAX = 2**31 - 1
                if actual.value < INT32_MIN or actual.value > INT32_MAX:
                    raise SemanticError(
                        f"Integer literal {actual.value} is outside Int32 range "
                        f"[{INT32_MIN}, {INT32_MAX}] in {context}",
                        loc
                    )
                return

        # Allow Int32 literal coercion
        if isinstance(actual, Int32Type) and isinstance(expected, Int32Type):
            return

        # Allow BigInt compatibility
        if isinstance(actual, BigIntType) and isinstance(expected, BigIntType):
            return

        # Allow Int32 -> BigInt implicit conversion (safe upcast)
        if isinstance(actual, Int32Type) and isinstance(expected, BigIntType):
            return

        # Allow BigInt -> Int32 implicit conversion (truncation - safe for literals)
        if isinstance(actual, BigIntType) and isinstance(expected, Int32Type):
            return

        # Allow same record types
        if isinstance(actual, RecordType) and isinstance(expected, RecordType):
            if actual.name == expected.name:
                return

        # Allow compatible pointer types
        if isinstance(actual, PtrType) and isinstance(expected, PtrType):
            if actual.pointee == expected.pointee:
                return

        if isinstance(actual, ConstPtrType) and isinstance(expected, ConstPtrType):
            if actual.pointee == expected.pointee:
                return

        # Allow ArrayType -> SpanType if element types match or are coercible
        if isinstance(actual, ArrayType) and isinstance(expected, SpanType):
            if actual.element_type == expected.element_type:
                return
            # IntLiteral element type coerces to expected element type
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return

        # Allow Array element type coercion if sizes match
        if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
            if actual.size == expected.size:
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return

        # Allow StaticListType -> SpanType if element types match
        if isinstance(actual, StaticListType) and isinstance(expected, SpanType):
            if actual.element_type == expected.element_type:
                return

        # Allow ListType -> SpanType if element types match or are coercible
        if isinstance(actual, ListType) and isinstance(expected, SpanType):
            if actual.element_type == expected.element_type:
                return
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return

        # Allow list element type coercion
        if isinstance(actual, ListType) and isinstance(expected, ListType):
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return

        # Allow ListType -> ArrayType for explicit annotations
        # (global array literals become ListType but can be assigned to Array variables)
        if isinstance(actual, ListType) and isinstance(expected, ArrayType):
            if actual.element_type == expected.element_type:
                return
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return

        # Allow PendingListType compatibility during first phase (before resolution)
        if isinstance(actual, PendingListType):
            # Compatible with list[T] if element types match
            if isinstance(expected, ListType):
                if actual.element_type == expected.element_type:
                    return
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return
            # Compatible with Span[T] if element types match
            if isinstance(expected, SpanType):
                if actual.element_type == expected.element_type:
                    return
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return
            # Compatible with Array[T, N] if element types and sizes match
            if isinstance(expected, ArrayType):
                if actual.size == expected.size:
                    if actual.element_type == expected.element_type:
                        return
                    if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                        return

        raise SemanticError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

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

        # Direct variable reference
        if isinstance(arg_expr, TpyName):
            var_name = arg_expr.name
            if var_name in self.variable_to_literal:
                literal_id = self.variable_to_literal[var_name]

        if literal_id is not None and literal_id in self.list_literals:
            info = self.list_literals[literal_id]
            if isinstance(param_type, ListType):
                info.passed_to_list_param = True
            elif isinstance(param_type, SpanType):
                info.passed_to_span_param = True

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

            # Resolve element type: IntLiteralType -> Int32 for containers
            elem_type = info.element_type
            if isinstance(elem_type, IntLiteralType):
                elem_type = INT32

            # Determine resolved type
            if info.has_explicit_annotation and info.explicit_type:
                resolved = info.explicit_type
            elif info.is_mutated:
                resolved = ListType(elem_type)
            elif info.passed_to_list_param:
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
