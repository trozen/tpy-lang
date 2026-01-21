"""
TurboPython Parser

Uses CPython's ast module to parse TurboPython source code.
Validates that only allowed constructs are used.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
from typing import Optional, Union

from .typesys import (
    TpyType, Int32Type, VoidType, RecordType, PtrType, ConstPtrType,
    StaticListType, INT32, VOID, STR, FieldInfo, RecordInfo, FunctionInfo, TypeRegistry
)


class ParseError(Exception):
    """Error during parsing."""
    def __init__(self, message: str, node: Optional[ast.AST] = None):
        self.node = node
        loc = ""
        if node and hasattr(node, 'lineno'):
            loc = f" at line {node.lineno}"
        super().__init__(f"{message}{loc}")


# AST node types for TurboPython

@dataclass
class TpyExpr:
    """Base class for expressions."""
    pass


@dataclass
class TpyIntLiteral(TpyExpr):
    """Integer literal."""
    value: int


@dataclass
class TpyStrLiteral(TpyExpr):
    """String literal."""
    value: str


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
    call_type: Optional[TpyType] = None  # For generic instantiation like StaticList[T, N]()
    kwargs: dict[str, TpyExpr] = field(default_factory=dict)  # Keyword arguments (limited support)


@dataclass
class TpyMethodCall(TpyExpr):
    """Method call on an object."""
    obj: TpyExpr
    method: str
    args: list[TpyExpr]


@dataclass
class TpyFieldAccess(TpyExpr):
    """Field access on a value or pointer."""
    obj: TpyExpr
    field: str


@dataclass
class TpyStmt:
    """Base class for statements."""
    pass


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
class TpyFunction:
    """Function definition."""
    name: str
    params: list[tuple[str, TpyType]]
    return_type: TpyType
    body: list[TpyStmt]
    is_noalloc: bool = False
    is_method: bool = False


@dataclass
class TpyRecord:
    """Record (class) definition."""
    name: str
    fields: list[FieldInfo]
    methods: list[TpyFunction] = field(default_factory=list)

    @property
    def init_method(self) -> Optional[TpyFunction]:
        """Get __init__ method if present."""
        for m in self.methods:
            if m.name == "__init__":
                return m
        return None


@dataclass
class TpyModule:
    """Top-level module."""
    records: list[TpyRecord]
    functions: list[TpyFunction]
    top_level_stmts: list[TpyStmt] = field(default_factory=list)


class Parser:
    """Parser for TurboPython source code."""

    FORBIDDEN_CONSTRUCTS = {
        "list", "dict", "set", "tuple", "str",
        "try", "raise", "with", "async", "await",
        "lambda", "yield", "global", "nonlocal",
    }

    ALLOWED_IMPORTS = {"tpy"}

    def __init__(self):
        self.registry = TypeRegistry()

    def parse(self, source: str) -> TpyModule:
        """Parse TurboPython source code into a TpyModule."""
        tree = ast.parse(source)
        return self._parse_module(tree)

    def _parse_module(self, tree: ast.Module) -> TpyModule:
        """Parse a module."""
        records = []
        functions = []
        top_level_stmts = []

        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                self._check_import(node)
            elif isinstance(node, ast.ClassDef):
                record = self._parse_class(node)
                records.append(record)
                # Register the record type
                self.registry.register_record(RecordInfo(
                    name=record.name,
                    fields=record.fields,
                    has_init=record.init_method is not None
                ))
            elif isinstance(node, ast.FunctionDef):
                func = self._parse_function(node)
                functions.append(func)
            elif isinstance(node, ast.Expr):
                # Top-level expression (e.g., function call)
                top_level_stmts.append(TpyExprStmt(self._parse_expr(node.value)))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                # Top-level variable declaration
                top_level_stmts.append(self._parse_stmt(node))
            elif isinstance(node, ast.For):
                # Top-level for loop
                top_level_stmts.append(self._parse_stmt(node))
            else:
                raise ParseError(f"Unsupported top-level construct: {type(node).__name__}", node)

        return TpyModule(records=records, functions=functions, top_level_stmts=top_level_stmts)

    def _check_import(self, node: ast.ImportFrom) -> None:
        """Check that import is from allowed module."""
        if node.module not in self.ALLOWED_IMPORTS:
            raise ParseError(f"Import from '{node.module}' not allowed. Only 'from tpy import ...' is permitted.", node)

    def _parse_class(self, node: ast.ClassDef) -> TpyRecord:
        """Parse a class definition as a record."""
        if node.bases:
            raise ParseError(f"Inheritance not allowed in class '{node.name}'", node)
        if node.decorator_list:
            raise ParseError(f"Decorators not allowed on class '{node.name}'", node)

        fields = []
        methods = []

        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                # Field declaration: name: Type = default
                if not isinstance(item.target, ast.Name):
                    raise ParseError("Invalid field declaration", item)
                field_name = item.target.id
                field_type = self._parse_type_annotation(item.annotation)
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
                methods.append(self._parse_method(item, node.name))
            elif isinstance(item, ast.Pass):
                pass
            else:
                raise ParseError(f"Unsupported construct in class '{node.name}'", item)

        return TpyRecord(name=node.name, fields=fields, methods=methods)

    def _parse_method(self, node: ast.FunctionDef, class_name: str) -> TpyFunction:
        """Parse a method definition."""
        params = []
        for i, arg in enumerate(node.args.args):
            if i == 0:
                if arg.arg != "self":
                    raise ParseError(f"First parameter of method '{node.name}' must be 'self'", node)
                continue
            if arg.annotation is None:
                raise ParseError(f"Parameter '{arg.arg}' must have type annotation", node)
            param_type = self._parse_type_annotation(arg.annotation)
            params.append((arg.arg, param_type))

        # Get return type (default to Void for __init__)
        return_type = VOID
        if node.name != "__init__" and node.returns:
            return_type = self._parse_type_annotation(node.returns)

        body = [self._parse_stmt(stmt) for stmt in node.body]
        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_method=True
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
            is_noalloc=is_noalloc
        )

    def _parse_type_annotation(self, node: ast.expr) -> TpyType:
        """Parse a type annotation."""
        if isinstance(node, ast.Name):
            name = node.id
            if name == "Int32":
                return INT32
            elif name == "None":
                return VOID
            elif self.registry.is_known_type(name) or name[0].isupper():
                # Assume it's a record type (will be validated later)
                return RecordType(name)
            else:
                raise ParseError(f"Unknown type: {name}", node)

        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                container = node.value.id
                if container == "Ptr":
                    inner = self._parse_type_annotation(node.slice)
                    return PtrType(inner)
                elif container == "ConstPtr":
                    inner = self._parse_type_annotation(node.slice)
                    return ConstPtrType(inner)
                elif container == "StaticList":
                    if isinstance(node.slice, ast.Tuple):
                        if len(node.slice.elts) != 2:
                            raise ParseError("StaticList requires exactly 2 type parameters", node)
                        elem_type = self._parse_type_annotation(node.slice.elts[0])
                        cap_node = node.slice.elts[1]
                        if isinstance(cap_node, ast.Constant) and isinstance(cap_node.value, int):
                            capacity = cap_node.value
                        else:
                            raise ParseError("StaticList capacity must be an integer literal", node)
                        return StaticListType(elem_type, capacity)
                    else:
                        raise ParseError("StaticList requires [T, N] syntax", node)
                else:
                    raise ParseError(f"Unknown generic type: {container}", node)

        elif isinstance(node, ast.Constant) and node.value is None:
            return VOID

        raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

    def _parse_stmt(self, node: ast.stmt) -> TpyStmt:
        """Parse a statement."""
        if isinstance(node, ast.AnnAssign):
            # Annotated assignment: x: T = expr
            if not isinstance(node.target, ast.Name):
                raise ParseError("Invalid assignment target", node)
            var_type = self._parse_type_annotation(node.annotation)
            init_expr = self._parse_expr(node.value) if node.value else None
            return TpyVarDecl(node.target.id, var_type, init_expr)

        elif isinstance(node, ast.Assign):
            # Simple assignment: x = expr or x.field = expr
            if len(node.targets) != 1:
                raise ParseError("Multiple assignment targets not supported", node)
            target = self._parse_expr(node.targets[0])
            value = self._parse_expr(node.value)
            # Check if this is a variable declaration (unannotated)
            if isinstance(target, TpyName):
                return TpyVarDecl(target.name, None, value)
            return TpyAssign(target, value)

        elif isinstance(node, ast.Expr):
            return TpyExprStmt(self._parse_expr(node.value))

        elif isinstance(node, ast.Return):
            value = self._parse_expr(node.value) if node.value else None
            return TpyReturn(value)

        elif isinstance(node, ast.If):
            cond = self._parse_expr(node.test)
            then_body = [self._parse_stmt(s) for s in node.body]
            else_body = [self._parse_stmt(s) for s in node.orelse]
            return TpyIf(cond, then_body, else_body)

        elif isinstance(node, ast.While):
            cond = self._parse_expr(node.test)
            body = [self._parse_stmt(s) for s in node.body]
            return TpyWhile(cond, body)

        elif isinstance(node, ast.For):
            # Only support range-based for loops
            if not isinstance(node.target, ast.Name):
                raise ParseError("For loop target must be a simple variable", node)
            if not (isinstance(node.iter, ast.Call) and
                    isinstance(node.iter.func, ast.Name) and
                    node.iter.func.id == "range"):
                raise ParseError("For loops must use range()", node)
            var = node.target.id
            args = node.iter.args
            if len(args) == 1:
                start = TpyIntLiteral(0)
                end = self._parse_expr(args[0])
            elif len(args) == 2:
                start = self._parse_expr(args[0])
                end = self._parse_expr(args[1])
            else:
                raise ParseError("range() must have 1 or 2 arguments", node)
            body = [self._parse_stmt(s) for s in node.body]
            return TpyFor(var, start, end, body)

        elif isinstance(node, ast.Pass):
            return TpyExprStmt(TpyIntLiteral(0))  # No-op

        else:
            raise ParseError(f"Unsupported statement: {type(node).__name__}", node)

    def _parse_expr(self, node: ast.expr) -> TpyExpr:
        """Parse an expression."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return TpyIntLiteral(node.value)
            elif isinstance(node.value, str):
                return TpyStrLiteral(node.value)
            else:
                raise ParseError(f"Unsupported literal type: {type(node.value).__name__}", node)

        elif isinstance(node, ast.Name):
            if node.id in self.FORBIDDEN_CONSTRUCTS:
                raise ParseError(f"'{node.id}' is not allowed in TurboPython", node)
            return TpyName(node.id)

        elif isinstance(node, ast.BinOp):
            left = self._parse_expr(node.left)
            right = self._parse_expr(node.right)
            op = self._binop_to_str(node.op)
            return TpyBinOp(left, op, right)

        elif isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise ParseError("Chained comparisons not supported", node)
            left = self._parse_expr(node.left)
            right = self._parse_expr(node.comparators[0])
            op = self._cmpop_to_str(node.ops[0])
            return TpyBinOp(left, op, right)

        elif isinstance(node, ast.UnaryOp):
            operand = self._parse_expr(node.operand)
            op = self._unaryop_to_str(node.op)
            return TpyUnaryOp(op, operand)

        elif isinstance(node, ast.BoolOp):
            # Handle 'and' / 'or' - chain as binary ops
            op = "&&" if isinstance(node.op, ast.And) else "||"
            result = self._parse_expr(node.values[0])
            for val in node.values[1:]:
                result = TpyBinOp(result, op, self._parse_expr(val))
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
                return TpyCall(node.func.id, args, kwargs=kwargs)
            elif isinstance(node.func, ast.Attribute):
                obj = self._parse_expr(node.func.value)
                return TpyMethodCall(obj, node.func.attr, args)
            elif isinstance(node.func, ast.Subscript):
                # Generic type instantiation: StaticList[T, N]()
                call_type = self._parse_type_annotation(node.func)
                if isinstance(node.func.value, ast.Name):
                    return TpyCall(node.func.value.id, args, call_type)
                raise ParseError("Unsupported generic call target", node)
            else:
                raise ParseError("Unsupported call target", node)

        elif isinstance(node, ast.Attribute):
            obj = self._parse_expr(node.value)
            return TpyFieldAccess(obj, node.attr)

        else:
            raise ParseError(f"Unsupported expression: {type(node).__name__}", node)

    def _binop_to_str(self, op: ast.operator) -> str:
        """Convert binary operator to string."""
        ops = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
            ast.Div: "/", ast.Mod: "%", ast.FloorDiv: "/",
            ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
            ast.LShift: "<<", ast.RShift: ">>",
        }
        return ops.get(type(op), "?")

    def _cmpop_to_str(self, op: ast.cmpop) -> str:
        """Convert comparison operator to string."""
        ops = {
            ast.Eq: "==", ast.NotEq: "!=",
            ast.Lt: "<", ast.LtE: "<=",
            ast.Gt: ">", ast.GtE: ">=",
        }
        return ops.get(type(op), "?")

    def _unaryop_to_str(self, op: ast.unaryop) -> str:
        """Convert unary operator to string."""
        ops = {ast.USub: "-", ast.Not: "!", ast.Invert: "~"}
        return ops.get(type(op), "?")

    def _get_default_value(self, node: ast.expr) -> str:
        """Get string representation of a default value for C++."""
        if isinstance(node, ast.Constant):
            return str(node.value)
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
                return INT32
            elif isinstance(node.value, str):
                return STR
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            type_name = node.func.id
            if type_name == "Int32":
                return INT32
            # Check if it's a known record type
            record_info = self.registry.get_record(type_name)
            if record_info:
                return RecordType(type_name)
        return None
