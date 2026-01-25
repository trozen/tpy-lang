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
    StaticListType, ArrayType, SpanType, ListType, StrType, CharType, BoolType, BigIntType,
    INT32, VOID, STR, CHAR, BOOL, BIGINT, FieldInfo, RecordInfo, FunctionInfo, TypeRegistry
)
from .parse import (
    TpyModule, TpyRecord, TpyFunction, TpyStmt, TpyExpr,
    TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn, TpyIf, TpyWhile, TpyFor, TpyBreak, TpyContinue,
    TpyIntLiteral, TpyStrLiteral, TpyBoolLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript
)


class SemanticError(Exception):
    """Error during semantic analysis."""
    pass


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
        self.loop_depth: int = 0  # Track nesting depth of loops

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
                    self.global_scope.define(stmt.name, typ)
                    self.current_scope = None

    def _analyze_record_methods(self, record: TpyRecord) -> None:
        """Analyze all methods of a record."""
        for method in record.methods:
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

            self.current_scope = None
            self.current_function = None

    def _analyze_function(self, func: TpyFunction) -> None:
        """Analyze a function body."""
        self.current_function = func
        self.current_scope = Scope(parent=self.global_scope)

        # Add parameters to scope
        for pname, ptype in func.params:
            self.current_scope.define(pname, ptype)

        # Analyze body
        for stmt in func.body:
            self._analyze_stmt(stmt)

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
                self._check_type_compatible(ret_type, expected, "return value")
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
            if stmt.type:
                self._check_type_compatible(init_type, stmt.type, f"variable '{stmt.name}'")
                var_type = stmt.type
            elif existing_type:
                # Reassignment: use existing type, check compatibility
                self._check_type_compatible(init_type, existing_type, f"reassignment to '{stmt.name}'")
                var_type = existing_type
            else:
                var_type = init_type
        elif stmt.type:
            var_type = stmt.type
        else:
            raise SemanticError(f"Variable '{stmt.name}' has no type annotation and no initializer")

        self.current_scope.define(stmt.name, var_type)

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

        self._check_type_compatible(value_type, target_type, "assignment")

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        target_type = self._analyze_expr(stmt.target)
        value_type = self._analyze_expr(stmt.value)
        # Both must be integer types for arithmetic augmented assignment
        if not isinstance(target_type, (Int32Type, BigIntType)):
            raise SemanticError(f"Augmented assignment target must be an integer type, got {target_type}")
        if not isinstance(value_type, (Int32Type, BigIntType)):
            raise SemanticError(f"Augmented assignment value must be an integer type, got {value_type}")

    def _analyze_expr(self, expr: TpyExpr) -> TpyType:
        """Analyze an expression and return its type."""
        if isinstance(expr, TpyIntLiteral):
            typ = BIGINT
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

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            # Allow comparisons between same integer types
            if isinstance(left_type, (Int32Type, BigIntType)) and isinstance(right_type, (Int32Type, BigIntType)):
                return BOOL
            return BOOL

        # Logical operators return Bool
        if expr.op in ("&&", "||"):
            return BOOL

        # Arithmetic operators
        if isinstance(left_type, Int32Type) and isinstance(right_type, Int32Type):
            return INT32

        # Check if operands are literals (literals can adapt to the other type)
        left_is_literal = isinstance(expr.left, TpyIntLiteral)
        right_is_literal = isinstance(expr.right, TpyIntLiteral)

        # Int32 + literal -> Int32 (literal adapts to Int32)
        if isinstance(left_type, Int32Type) and right_is_literal:
            return INT32
        if left_is_literal and isinstance(right_type, Int32Type):
            return INT32

        # BigInt arithmetic (both BigInt variables, or one BigInt variable + literal)
        if isinstance(left_type, BigIntType) and isinstance(right_type, BigIntType):
            # If both are literals, result is still BigInt (Python semantics)
            if not left_is_literal or not right_is_literal:
                return BIGINT
            # Two literals: return BigInt
            return BIGINT

        # Int32 + BigInt variable -> BigInt (variable wins)
        if isinstance(left_type, (Int32Type, BigIntType)) and isinstance(right_type, (Int32Type, BigIntType)):
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
        elif expr.op == "~":
            if isinstance(operand_type, Int32Type):
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
            if not isinstance(arg_type, (StaticListType, ArrayType, SpanType, ListType, StrType)):
                raise SemanticError(f"len() argument must be StaticList, Array, Span, list, or str, got {arg_type}")
            return INT32

        # Built-in chr()
        if expr.func == "chr":
            if len(expr.args) != 1:
                raise SemanticError("chr() takes exactly 1 argument")
            arg_type = self._analyze_expr(expr.args[0])
            if not isinstance(arg_type, (Int32Type, BigIntType)):
                raise SemanticError(f"chr() argument must be an integer type, got {arg_type}")
            return CHAR

        # Check if it's a type constructor
        if expr.func == "int":
            if len(expr.args) > 1:
                raise SemanticError("int() takes at most 1 argument")
            if expr.args:
                arg_type = self._analyze_expr(expr.args[0])
                if not isinstance(arg_type, (Int32Type, BigIntType)):
                    raise SemanticError(f"int() argument must be an integer type, got {arg_type}")
            return BIGINT

        if expr.func == "Int32":
            if len(expr.args) > 1:
                raise SemanticError("Int32() takes at most 1 argument")
            if expr.args:
                arg_type = self._analyze_expr(expr.args[0])
                if not isinstance(arg_type, Int32Type):
                    raise SemanticError(f"Int32() argument must be Int32, got {arg_type}")
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
                self._check_type_compatible(arg_type, ptype, f"argument '{pname}'")
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
        """Analyze an array literal [expr, expr, ...]"""
        if not expr.elements:
            raise SemanticError("Empty array literal requires explicit type annotation")

        # Check if all elements are integer literals - use Int32 for practical array usage
        all_int_literals = all(isinstance(e, TpyIntLiteral) for e in expr.elements)

        # Analyze first element to get the expected type
        first_type = self._analyze_expr(expr.elements[0])

        # For arrays of integer literals, use Int32 instead of BigInt
        # (arrays are typically used for fixed-size data with bounded integers)
        if all_int_literals and isinstance(first_type, BigIntType):
            first_type = INT32

        # Check all elements have the same type
        for i, elem in enumerate(expr.elements[1:], 2):
            elem_type = self._analyze_expr(elem)
            # If using Int32 for int literals, treat BigInt literals as Int32 for comparison
            if all_int_literals and isinstance(elem_type, BigIntType):
                elem_type = INT32
            if elem_type != first_type:
                raise SemanticError(
                    f"Array literal element {i} has type {elem_type}, expected {first_type}"
                )

        return ArrayType(first_type, len(expr.elements))

    def _analyze_list_repeat(self, expr: TpyListRepeat) -> TpyType:
        """Analyze a list repetition: [element] * count"""
        elem_type = self._analyze_expr(expr.element)
        count_type = self._analyze_expr(expr.count)

        if not isinstance(count_type, (Int32Type, BigIntType)):
            raise SemanticError(f"List repetition count must be an integer type, got {count_type}")

        return ListType(elem_type)

    def _analyze_subscript(self, expr: TpySubscript) -> TpyType:
        """Analyze subscript indexing: obj[index]"""
        obj_type = self._analyze_expr(expr.obj)
        index_type = self._analyze_expr(expr.index)

        if not isinstance(index_type, (Int32Type, BigIntType)):
            raise SemanticError(f"Subscript index must be an integer type, got {index_type}")

        if isinstance(obj_type, ArrayType):
            return obj_type.element_type
        elif isinstance(obj_type, SpanType):
            return obj_type.element_type
        elif isinstance(obj_type, StaticListType):
            return obj_type.element_type
        elif isinstance(obj_type, ListType):
            return obj_type.element_type
        elif isinstance(obj_type, StrType):
            return CHAR
        else:
            raise SemanticError(f"Cannot index type {obj_type}")

    def _check_type_compatible(self, actual: TpyType, expected: TpyType, context: str) -> None:
        """Check if actual type is compatible with expected type."""
        if actual == expected:
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

        # Allow ArrayType -> SpanType if element types match
        if isinstance(actual, ArrayType) and isinstance(expected, SpanType):
            if actual.element_type == expected.element_type:
                return
            # Allow Array[BigInt] -> Span[Int32] (literal arrays)
            if isinstance(actual.element_type, BigIntType) and isinstance(expected.element_type, Int32Type):
                return

        # Allow Array[BigInt] -> Array[Int32] if sizes match (literal arrays)
        if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
            if actual.size == expected.size:
                if isinstance(actual.element_type, BigIntType) and isinstance(expected.element_type, Int32Type):
                    return

        # Allow StaticListType -> SpanType if element types match
        if isinstance(actual, StaticListType) and isinstance(expected, SpanType):
            if actual.element_type == expected.element_type:
                return

        # Allow list[BigInt] -> list[Int32] (literal lists)
        if isinstance(actual, ListType) and isinstance(expected, ListType):
            if isinstance(actual.element_type, BigIntType) and isinstance(expected.element_type, Int32Type):
                return

        raise SemanticError(f"Type mismatch in {context}: expected {expected}, got {actual}")

    def get_expr_type(self, expr: TpyExpr) -> Optional[TpyType]:
        """Get the cached type of an expression."""
        return self.expr_types.get(id(expr))
