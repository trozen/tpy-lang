"""
TurboPython Parser.

Uses CPython's ast module to parse TurboPython source code.
Validates that only allowed constructs are used.
"""

from __future__ import annotations
import ast
from typing import Optional

from ..typesys import (
    TpyType, NamedType, PtrType, ConstPtrType, OwnType, ReadonlyType, TypeParamRef,
    OptionalType, VoidType, make_union,
    INT32, VOID, STR, CHAR, BOOL, FLOAT, BIGINT, SELF, FieldInfo, RecordInfo, TypeRegistry,
    MethodSignature, ProtocolInfo, TypeParamKind,
    INT8, INT16, INT64, UINT8, UINT16, UINT32, UINT64, ALL_FIXED_INTS,
)
from ..modules import lookup_generic_type, lookup_generic_type_in_module, lookup_protocol as lookup_builtin_protocol, BuiltinTypeDef
from .nodes import (
    ParseError, SourceLocation, ParseWarning, RecordLinkage, FunctionLinkage,
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall,
    TpyFieldAccess, TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce,
    TpyStmt, TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn,
    TpyAssert, TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue,
    TpyPassStmt, TpyGlobal, TpyRaiseStopIteration,
    RelativeImportKey, TpyImport, TpyFunction, TpyRecord, TpyProtocol, TpyModule,
)
from .imports import ImportProcessor, SPECIAL_MODULES, check_tpy_type_imported

# Map of fixed-int type names to their singleton instances
_FIXED_INT_MAP: dict[str, TpyType] = {str(t): t for t in ALL_FIXED_INTS}


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
    ast.Is: "is", ast.IsNot: "is not",
}

_UNARYOP_TO_STR: dict[type, str] = {
    ast.USub: "-", ast.Not: "!", ast.Invert: "~",
}


def _extract_subscript_slices(node: ast.Subscript) -> list[ast.expr]:
    """Extract individual type argument nodes from a subscript slice.

    Handles both single-arg (X[T]) and multi-arg (X[T, U]) forms.
    """
    if isinstance(node.slice, ast.Tuple):
        return node.slice.elts
    return [node.slice]


def _collect_bitor_arms(node: ast.BinOp) -> list[ast.expr]:
    """Flatten a left-recursive chain of A | B | C into [A, B, C]."""
    arms: list[ast.expr] = []
    if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.BitOr):
        arms.extend(_collect_bitor_arms(node.left))
    else:
        arms.append(node.left)
    arms.append(node.right)
    return arms


class Parser:
    """Parser for TurboPython source code."""

    FORBIDDEN_CONSTRUCTS = {
        "dict", "set", "tuple",
        "try", "with", "async", "await",
        "lambda", "yield", "nonlocal",
    }

    def __init__(self):
        self.registry = TypeRegistry()
        self.source_lines: list[str] = []
        self._type_param_scope: dict[str, TypeParamKind] | None = None
        self._warnings: list[ParseWarning] = []
        self._imports = ImportProcessor(self._warn)

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
        self._warnings = []
        self._imports = ImportProcessor(self._warn)
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
        bare_module_imports: set[str] = set()
        seen_non_import = False

        for node in tree.body:
            is_import = isinstance(node, (ast.Import, ast.ImportFrom))

            # Check for late imports (imports after non-import code)
            if is_import and seen_non_import:
                self._warn("Import statement should be at the top of the file", node)

            if isinstance(node, ast.ImportFrom):
                self._imports.process_import_from(node, imports, user_module_imports, top_level_stmts, module_aliases)
            elif isinstance(node, ast.Import):
                self._imports.process_import(node, imports, user_module_imports, top_level_stmts, module_aliases, bare_module_imports)
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

        return TpyModule(records=records, functions=functions, protocols=protocols, top_level_stmts=top_level_stmts, source_lines=self.source_lines, imports=imports, user_module_imports=user_module_imports, module_aliases=module_aliases, bare_module_imports=bare_module_imports, parse_warnings=self._warnings)

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

        # Parse record decorators (@native, @native_c, @nocopy)
        linkage = RecordLinkage.DEFAULT
        native_name: str | None = None
        is_nocopy = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id in self._RECORD_LINKAGE_DECORATORS:
                new_linkage = self._RECORD_LINKAGE_DECORATORS[dec.id]
                if linkage != RecordLinkage.DEFAULT:
                    raise ParseError(
                        f"Class '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id in self._RECORD_LINKAGE_DECORATORS:
                new_linkage = self._RECORD_LINKAGE_DECORATORS[dec.func.id]
                if linkage != RecordLinkage.DEFAULT:
                    raise ParseError(
                        f"Class '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
                if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    native_name = dec.args[0].value
                else:
                    raise ParseError(f"@{dec.func.id}() requires a single string argument", dec)
            elif isinstance(dec, ast.Name) and dec.id == "nocopy":
                is_nocopy = True
            elif isinstance(dec, ast.Name):
                raise ParseError(f"Unknown decorator '{dec.id}' on class '{node.name}'", dec)
            else:
                raise ParseError(f"Unsupported decorator on class '{node.name}'", dec)

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
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and item.value.value is ...:
                pass  # Ellipsis for opaque native types
            else:
                raise ParseError(f"Unsupported construct in class '{node.name}'", item)

        # Validate method constraints based on class linkage
        for method in methods:
            if linkage != RecordLinkage.DEFAULT:
                # Native class: methods must be stubs
                if not method.is_stub:
                    raise ParseError(
                        f"Methods on @{linkage.value} classes must have '...' body (stub declaration)", node)
            else:
                # Non-native class: stubs and @native decorators are not allowed
                if method.is_stub:
                    raise ParseError(
                        f"Method '{method.name}' cannot have '...' body on a regular class "
                        f"(only allowed on @native/@native_c classes)", node)
                if method.native_name is not None:
                    raise ParseError(
                        f"@native(\"...\") decorator on method '{method.name}' is only allowed "
                        f"on @native/@native_c classes", node)

        # Restore the scope
        self._type_param_scope = old_scope
        return TpyRecord(name=node.name, fields=fields, methods=methods, type_params=type_params, type_param_kinds=type_param_kinds, type_param_bounds=type_param_bounds, bases=bases, linkage=linkage, native_name=native_name, is_nocopy=is_nocopy, loc=self._loc(node))

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
                # Parse @readonly decorator
                is_readonly = False
                readonly_opt_out = False
                for dec in item.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "readonly":
                        is_readonly = True
                    elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "readonly":
                        if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, bool):
                            if dec.args[0].value:
                                is_readonly = True
                            else:
                                readonly_opt_out = True
                        else:
                            raise ParseError("@readonly() requires a single bool argument (True or False)", dec)
                    else:
                        dec_name = dec.id if isinstance(dec, ast.Name) else type(dec).__name__
                        raise ParseError(f"Unknown decorator '{dec_name}' on protocol method '{item.name}'", dec)

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
                    return_type=return_type,
                    is_readonly=is_readonly,
                    readonly_opt_out=readonly_opt_out,
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

    # Decorator names that set method linkage (for method renaming on native classes)
    _METHOD_LINKAGE_DECORATORS: dict[str, FunctionLinkage] = {
        "native": FunctionLinkage.NATIVE,
        "native_c": FunctionLinkage.NATIVE_C,
    }

    def _parse_method(self, node: ast.FunctionDef, class_name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> TpyFunction:
        """Parse a method definition."""
        # Check decorators (@staticmethod, @readonly, @native("cpp_name"))
        is_staticmethod = False
        is_readonly = False
        readonly_opt_out = False
        method_linkage = FunctionLinkage.DEFAULT
        native_name: str | None = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "staticmethod":
                is_staticmethod = True
            elif isinstance(dec, ast.Name) and dec.id == "readonly":
                is_readonly = True
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "readonly":
                if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, bool):
                    if dec.args[0].value:
                        is_readonly = True
                    else:
                        readonly_opt_out = True
                else:
                    raise ParseError("@readonly() requires a single bool argument (True or False)", dec)
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id in self._METHOD_LINKAGE_DECORATORS:
                method_linkage = self._METHOD_LINKAGE_DECORATORS[dec.func.id]
                if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    native_name = dec.args[0].value
                else:
                    raise ParseError(f"@{dec.func.id}() on method requires a single string argument", dec)
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

        is_stub_body = self._is_stub_body(node.body)
        is_stub = is_stub_body
        if is_stub:
            body = []
        else:
            body = [self._parse_stmt(stmt) for stmt in node.body]

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_method=True,
            is_staticmethod=is_staticmethod,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            is_stub=is_stub,
            linkage=method_linkage,
            native_name=native_name,
            loc=self._loc(node)
        )

    # Decorator names that set record linkage
    _RECORD_LINKAGE_DECORATORS: dict[str, RecordLinkage] = {
        "native": RecordLinkage.NATIVE,
        "native_c": RecordLinkage.NATIVE_C,
    }

    # Decorator names that set function linkage
    _LINKAGE_DECORATORS: dict[str, FunctionLinkage] = {
        "native": FunctionLinkage.NATIVE,
        "native_c": FunctionLinkage.NATIVE_C,
        "extern_c": FunctionLinkage.EXTERN_C,
    }

    def _parse_function(self, node: ast.FunctionDef) -> TpyFunction:
        """Parse a function definition."""
        is_noalloc = False
        is_readonly = False
        readonly_opt_out = False
        linkage = FunctionLinkage.DEFAULT
        native_name: str | None = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "noalloc":
                is_noalloc = True
            elif isinstance(dec, ast.Name) and dec.id == "readonly":
                is_readonly = True
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "readonly":
                if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, bool):
                    if dec.args[0].value:
                        is_readonly = True
                    else:
                        readonly_opt_out = True
                else:
                    raise ParseError("@readonly() requires a single bool argument (True or False)", dec)
            elif isinstance(dec, ast.Name) and dec.id in self._LINKAGE_DECORATORS:
                new_linkage = self._LINKAGE_DECORATORS[dec.id]
                if linkage != FunctionLinkage.DEFAULT:
                    raise ParseError(
                        f"Function '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id in self._LINKAGE_DECORATORS:
                new_linkage = self._LINKAGE_DECORATORS[dec.func.id]
                if linkage != FunctionLinkage.DEFAULT:
                    raise ParseError(
                        f"Function '{node.name}' cannot have both @{linkage.value} and @{new_linkage.value}", node)
                linkage = new_linkage
                if len(dec.args) == 1 and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    native_name = dec.args[0].value
                else:
                    raise ParseError(f"@{dec.func.id}() requires a single string argument", dec)
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

        # Validate body vs linkage
        is_stub_body = self._is_stub_body(node.body)
        is_stub = False

        if linkage in (FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C):
            if not is_stub_body:
                raise ParseError(
                    f"@{linkage.value} function '{node.name}' must have `...` body (it declares an external symbol)",
                    node)
            is_stub = True
            body = []
        elif linkage == FunctionLinkage.EXTERN_C:
            if is_stub_body:
                raise ParseError(
                    f"@extern_c function '{node.name}' must have a body (it exports a TPy function)",
                    node)
            body = [self._parse_stmt(stmt) for stmt in node.body]
        else:
            body = [self._parse_stmt(stmt) for stmt in node.body]

        # Restore the scope
        self._type_param_scope = old_scope

        return TpyFunction(
            name=node.name,
            params=params,
            return_type=return_type,
            body=body,
            is_noalloc=is_noalloc,
            is_readonly=is_readonly,
            readonly_opt_out=readonly_opt_out,
            linkage=linkage,
            native_name=native_name,
            is_stub=is_stub,
            type_params=type_params,
            type_param_bounds=type_param_bounds,
            loc=self._loc(node)
        )

    def _is_stub_body(self, body: list[ast.stmt]) -> bool:
        """Check if a function body is a stub (only `...`).

        Only Ellipsis marks a declaration-only stub. `pass` is a valid
        no-op body that should still generate a definition.
        """
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
                return True
        return False

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
            resolved_name = self._imports.tpy_import_aliases.get(name, name)
            # Check if tpy type was explicitly imported
            check_tpy_type_imported(name, resolved_name, node, self._imports.tpy_star_import, self._imports.tpy_import_aliases)
            if resolved_name == "Self":
                return SELF
            elif (fixed_int := _FIXED_INT_MAP.get(resolved_name)) is not None:
                return fixed_int
            elif resolved_name == "int":
                return BIGINT
            elif resolved_name == "float":
                return FLOAT
            elif resolved_name == "bool":
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
                resolved_container = self._imports.tpy_import_aliases.get(container, container)
                # Check if tpy type was explicitly imported
                check_tpy_type_imported(container, resolved_container, node, self._imports.tpy_star_import, self._imports.tpy_import_aliases)
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
                elif resolved_container == "readonly":
                    inner = self._parse_type_annotation(node.slice, type_param_scope)
                    return ReadonlyType(inner)

                # Generic protocols (e.g., Sequence[Int32])
                if protocol_def := lookup_builtin_protocol(resolved_container):
                    if protocol_def.type_params:
                        type_args = self._parse_protocol_type_args(node, resolved_container, protocol_def.type_params, type_param_scope)
                        return NamedType(resolved_container, type_args, is_protocol=True)

                # Module-defined generic types (list, Array, Span, etc.)
                if lookup := lookup_generic_type(resolved_container):
                    return self._parse_generic_type(node, resolved_container, lookup.type_def, type_param_scope)

                # Generic types from explicitly imported builtin submodules (tpy.mem, etc.)
                if import_source := self._imports.get_import_source(container):
                    source_module, original_name = import_source
                    if lookup := lookup_generic_type_in_module(original_name, source_module):
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

        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            arms = _collect_bitor_arms(node)
            parsed = [self._parse_type_annotation(arm, type_param_scope) for arm in arms]

            # Handle readonly normalization:
            # readonly[A] | readonly[B] -> readonly[A | B]
            # readonly[A] | B -> error (mixed readonly)
            readonly_count = sum(1 for t in parsed if isinstance(t, ReadonlyType))
            non_none_count = sum(1 for t in parsed if not isinstance(t, VoidType))
            if readonly_count > 0 and readonly_count < non_none_count:
                raise ParseError("Cannot mix readonly and non-readonly types in a union", node)
            if readonly_count > 0:
                unwrapped = [
                    t.wrapped if isinstance(t, ReadonlyType) else t
                    for t in parsed
                ]
                return ReadonlyType(make_union(*unwrapped))

            return make_union(*parsed)

        raise ParseError(f"Cannot parse type annotation: {ast.dump(node)}", node)

    def _parse_protocol_type_args(self, node: ast.Subscript, name: str,
                                    type_params: list[str], type_param_scope: dict[str, TypeParamKind] | None = None) -> tuple[TpyType, ...]:
        """Parse type arguments for a generic protocol like Sequence[Int32]."""
        expected_count = len(type_params)
        slices = _extract_subscript_slices(node)

        if len(slices) != expected_count:
            raise ParseError(f"{name} requires exactly {expected_count} type parameters", node)

        type_args = tuple(self._parse_type_annotation(s, type_param_scope) for s in slices)
        return type_args

    def _parse_record_type_args(self, node: ast.Subscript, name: str, type_param_scope: dict[str, TypeParamKind] | None = None) -> tuple[TpyType | int, ...]:
        """Parse type arguments for a user-defined generic record like Stack[Int32] or Matrix[Int32, 8].

        For records with integer type parameters, integer literals are allowed in type argument positions.
        The validation of which positions accept integers is done in sema (since the record info
        may not be registered yet during parsing).
        """
        slices = _extract_subscript_slices(node)

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
        slices = _extract_subscript_slices(node)

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
        slices = _extract_subscript_slices(node)

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

        elif isinstance(node, ast.Assert):
            cond = self._parse_expr(node.test)
            msg = self._parse_expr(node.msg) if node.msg else None
            return TpyAssert(cond, msg, loc=loc)

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
            iterable = self._parse_expr(node.iter)
            return TpyForEach(var, iterable, body, loc=loc)

        elif isinstance(node, ast.Pass):
            return TpyPassStmt(loc=loc)

        elif isinstance(node, ast.Break):
            return TpyBreak(loc=loc)

        elif isinstance(node, ast.Continue):
            return TpyContinue(loc=loc)

        elif isinstance(node, ast.Global):
            return TpyGlobal(node.names, loc=loc)

        elif isinstance(node, ast.Raise):
            return self._parse_raise(node, loc)

        else:
            raise ParseError(f"Unsupported statement: {type(node).__name__}", node)

    def _parse_raise(self, node: ast.Raise, loc: SourceLocation | None) -> TpyStmt:
        """Parse a raise statement. Only `raise StopIteration` is supported."""
        exc = node.exc
        if exc is None:
            raise ParseError("'raise' requires an exception; only 'raise StopIteration' is supported", node)
        # raise StopIteration
        if isinstance(exc, ast.Name) and exc.id == "StopIteration":
            return TpyRaiseStopIteration(loc=loc)
        # raise StopIteration()
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "StopIteration":
            if exc.args or exc.keywords:
                raise ParseError("'raise StopIteration' does not accept arguments", node)
            return TpyRaiseStopIteration(loc=loc)
        raise ParseError("Only 'raise StopIteration' is supported", node)

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
            elif node.value is None:
                return TpyNoneLiteral(loc=loc)
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
                elif isinstance(node.func.value, ast.Attribute):
                    # module.func[T](args) -- method call with explicit type args
                    obj = self._parse_expr(node.func.value.value)
                    method = node.func.value.attr
                    type_args = ()
                    type_args_parse_error = None
                    try:
                        type_args = self._parse_type_args_from_subscript(node.func)
                    except ParseError as e:
                        type_args_parse_error = e.message
                    return TpyMethodCall(obj, method, args, type_args=type_args,
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
                if name == "Own":
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
                # Module-defined generic types
                from tpyc.modules import lookup_generic_type as _lookup_generic_type
                if _lookup_generic_type(name) is not None:
                    raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
                # Generic types from imported builtin submodules (tpy.mem, etc.)
                if import_src := self._imports.get_import_source(name):
                    if lookup_generic_type_in_module(import_src[1], import_src[0]) is not None:
                        raise ParseError(f"Generic type '{name}' cannot be used as a value", node)
            obj = self._parse_expr(node.value)
            index = self._parse_expr(node.slice)
            return TpySubscript(obj=obj, index=index, loc=loc)

        else:
            raise ParseError(f"Unsupported expression: {type(node).__name__}", node)

    def _binop_to_str(self, op: ast.operator) -> str:
        """Convert binary operator to string."""
        result = _BINOP_TO_STR.get(type(op))
        if result is None:
            raise ParseError(f"Unsupported binary operator: {type(op).__name__}")
        return result

    def _cmpop_to_str(self, op: ast.cmpop) -> str:
        """Convert comparison operator to string."""
        result = _CMPOP_TO_STR.get(type(op))
        if result is None:
            raise ParseError(f"Unsupported comparison operator: {type(op).__name__}")
        return result

    def _unaryop_to_str(self, op: ast.unaryop) -> str:
        """Convert unary operator to string."""
        result = _UNARYOP_TO_STR.get(type(op))
        if result is None:
            raise ParseError(f"Unsupported unary operator: {type(op).__name__}")
        return result

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
            if node.func.id in _FIXED_INT_MAP:
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
            if (fixed_int := _FIXED_INT_MAP.get(type_name)) is not None:
                return fixed_int
            if type_name == "int":
                return BIGINT
            if type_name == "float":
                return FLOAT
            # Check if it's a known record type
            record_info = self.registry.get_record(type_name)
            if record_info:
                return NamedType(type_name)
        return None
